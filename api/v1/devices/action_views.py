"""Devices, part 2b: telling the devices - commands, users, loading a device,
fingerprints and faces, settings, the server address, and linking people to
device users (docs/api/60-devices-data-flow.md).

Nothing here reaches a terminal at once: every change waits as a command
until the device next calls in and collects it. Each endpoint runs the panel's
own service (``devices.services.mapping``, ``commands``, ``load_jobs``,
``templates``, ``server_address``), which checks who may do it again.
"""

from rest_framework.response import Response

from api.core.docs import PATH, Param, endpoint
from api.core.errors import ApiError
from api.core.forms import refuse
from api.core.network import client_ip
from api.core.permissions import PanelRule
from api.core.views import ApiView
from api.v1.devices import serializers as s
from api.v1.devices.flow_views import CommandsView
from api.v1.devices.views import DEVICE_PARAM, ROLES, WRITE_ERRORS, DeviceView, _device
from devices.models import BiometricDevice, DeviceEnrollment
from devices.services import commands, device_admin, protocol, server_address, templates
from devices.services import mapping
from devices.services.commands import COMMAND_LABELS, queue_command
from devices.services.device_roster import build_roster
from employees.models import Employee
from organization.models import Branch

AREA = "device_flow"
PIN_PARAM = Param("pin", PATH, "string", "The user number on the device.", required=True,
                  example="41")
ONE_ERRORS = WRITE_ERRORS + ["not_found"]
MAPPERS = ["Whoever may map people of that branch: the owner, company administrator, or "
           "anyone given Create and edit employees there (a branch manager in theirs)"]


def _ref(row):
    return {"id": row.pk, "name": getattr(row, "full_name", None) or row.name}


def _device_ref(device):
    return {"id": str(device.public_id), "name": device.name}


def _audit(request, action, obj, after):
    device_admin.audit(actor=request.user, company_id=request.company_id, action=action, obj=obj,
                       after=after, ip=client_ip(request))


def _pins(request, data, device):
    """The user numbers asked for, or every user still on the device."""
    if data.get("all") or not data.get("pins"):
        return [row["pin"] for row in build_roster(device)
                if not row.get("removed_from_device") and not row.get("only_in_scans")]
    return list(data["pins"])


def _picked(data):
    return None if data.get("all") or not data.get("pins") else list(data["pins"])


def _mapping_errors(fn):
    try:
        return fn()
    except mapping.MappingError as exc:
        raise ApiError("validation_error", str(exc)) from exc


def _queued(entry, label, already):
    if entry is None:
        return {"queued": False, "command_id": None,
                "detail": f"“{label}” is already waiting for this device."}
    return {"queued": True, "command_id": entry["id"],
            "detail": f"“{label}” queued. The device collects it on its next check-in (usually "
                      "within a minute) and answers; follow it in …/commands."}


# --- commands -------------------------------------------------------------------------

class CommandView(CommandsView):
    """GET (the queue, from part a) and POST (ask the device) on one address."""

    permission_classes = [PanelRule]
    panel_page = {"GET": "devices:device_list", "POST": "devices:device_command"}

    @endpoint(
        id="device-commands-send", area=AREA, title="Ask a device for its data",
        summary="Send your users, your fingerprints and faces, your settings, or your scans.",
        what_it_does=["Queues the request; the device answers on its next check-in."],
        description=("Only these read-only requests, and only those the device's protocol "
                     "has. Asking twice while one waits does nothing. The answer comes back as "
                     "a message; then GET …/users, …/options or the punches show it."),
        roles=ROLES, scopes=["devices:write"], params=[DEVICE_PARAM], response_status=201,
        request=s.CommandInputSerializer, response=s.QueuedSerializer,
        request_example={"command": "query_users"},
        response_example={"queued": True, "command_id": 812,
                          "detail": "“Refresh user list” queued. The device collects it on its "
                                    "next check-in (usually within a minute) and answers."},
        errors=ONE_ERRORS,
    )
    def post(self, request, device_id):
        device = _device(device_id)
        data = s.CommandInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        key = data.validated_data["command"]
        label = COMMAND_LABELS[key]
        if not protocol.supports(device, key):
            refuse({"command": [f"This device does not take “{label}”."]})
        entry = queue_command(device=device, command_key=key, requested_by=request.user)
        return Response(s.QueuedSerializer(_queued(entry, label, entry is None)).data,
                        status=201 if entry else 200)


class AskUserView(DeviceView):
    permission_classes = [PanelRule]
    panel_page = "devices:device_user_query"

    @endpoint(
        id="device-users-ask", area=AREA, title="Ask a device about one user",
        summary="The device sends that one user back - to check what it holds.",
        what_it_does=["Queues the question; the answer updates GET …/users."],
        description="Useful after sending someone, to see what the terminal really keeps.",
        roles=ROLES, scopes=["devices:write"], params=[DEVICE_PARAM, PIN_PARAM],
        response_status=201, response=s.QueuedSerializer,
        response_example={"queued": True, "command_id": 813,
                          "detail": "Asked the device for user 41."},
        errors=ONE_ERRORS,
    )
    def post(self, request, device_id, pin):
        device = _device(device_id)
        entry, error = commands.queue_user_query(device=device, device_user_id=pin,
                                                 requested_by=request.user)
        if error:
            raise ApiError("validation_error", error)
        return Response(s.QueuedSerializer({
            "queued": True, "command_id": entry["id"],
            "detail": f"Asked {device.name} for user {pin}. It answers on its next check-in."})
            .data, status=201)


# --- users on the device ----------------------------------------------------------------

class DeviceUserView(DeviceView):
    permission_classes = [PanelRule]
    panel_page = {"POST": "devices:device_user_push", "DELETE": "devices:device_user_delete"}

    @endpoint(
        id="device-users-send", area=AREA, title="Send a linked user to the device",
        summary="Put the employee linked to this user number on the terminal again.",
        what_it_does=["Queues their record - name, card and role - and the fingerprint and "
                      "face saved for them."],
        description="The number must be linked to an employee on this device. Without a saved "
                    "fingerprint or face they go with ID and name only: enrol them at the "
                    "terminal.",
        roles=ROLES, scopes=["devices:write"], params=[DEVICE_PARAM, PIN_PARAM],
        response_status=201, response=s.QueuedSerializer,
        response_example={"queued": True, "command_id": None,
                          "detail": "Queued Rahim Uddin as user 41."},
        errors=ONE_ERRORS,
    )
    def post(self, request, device_id, pin):
        device = _device(device_id)
        enrollment = (DeviceEnrollment.objects.select_related("employee")
                      .filter(device=device, device_user_id=pin)
                      .exclude(enrollment_status=DeviceEnrollment.EnrollmentStatus.REMOVED)
                      .order_by("-effective_from").first())
        if enrollment is None:
            raise ApiError("not_found", f"User {pin} is not linked to anyone on this device.")
        result = mapping.resend_identity(actor=request.user, employee=enrollment.employee,
                                         only_device=device)
        if result.failed:
            raise ApiError("validation_error", result.failed[0][2])
        _audit(request, "device.user_pushed", enrollment,
               {"device_user_id": enrollment.device_user_id})
        return Response(s.QueuedSerializer({
            "queued": True, "command_id": None,
            "detail": f"Queued {enrollment.employee.full_name} as user {pin}. The device takes it "
                      "on its next check-in."}).data, status=201)

    @endpoint(
        id="device-users-remove-one", area=AREA, title="Remove a user from the device",
        summary="Take one user number off the terminal.",
        what_it_does=["Queues the removal. The fingerprint and face on that terminal are "
                      "destroyed; the copy saved here stays, so they can be sent back."],
        description="The device's last super admin is kept. Punch history is untouched.",
        roles=ROLES, scopes=["devices:write"], params=[DEVICE_PARAM, PIN_PARAM],
        response=s.RemovedSerializer,
        response_example={"removed": ["41"], "kept": []}, errors=ONE_ERRORS,
    )
    def delete(self, request, device_id, pin):
        device = _device(device_id)
        result = _mapping_errors(lambda: mapping.remove_users(actor=request.user, device=device,
                                                              pins=[pin]))
        if result.removed:
            _audit(request, "device.user_deleted", device, {"device_user_id": pin})
        return Response(s.RemovedSerializer({
            "removed": result.removed,
            "kept": [{"pin": p, "reason": r} for p, r in result.skipped]}).data)


class RemoveUsersView(DeviceView):
    permission_classes = [PanelRule]
    panel_page = "devices:device_users_remove"

    @endpoint(
        id="device-users-remove", area=AREA, title="Remove users from the device",
        summary="Take the user numbers given - or everyone - off the terminal.",
        what_it_does=["Queues the removals; the device takes a few per check-in."],
        description="The last super admin is kept whatever is asked. Fingerprints and faces "
                    "saved here stay, so they can be sent back.",
        roles=ROLES, scopes=["devices:write"], params=[DEVICE_PARAM],
        request=s.PinsInputSerializer, response=s.RemovedSerializer,
        request_example={"pins": ["77", "78"]},
        response_example={"removed": ["77", "78"], "kept": []}, errors=ONE_ERRORS,
    )
    def post(self, request, device_id):
        device = _device(device_id)
        data = s.PinsInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        pins = _pins(request, data.validated_data, device)
        if not pins:
            refuse({"pins": ["Give at least one user number."]})
        result = _mapping_errors(lambda: mapping.remove_users(actor=request.user, device=device,
                                                              pins=pins))
        return Response(s.RemovedSerializer({
            "removed": result.removed,
            "kept": [{"pin": p, "reason": r} for p, r in result.skipped]}).data)


class CopyUsersView(DeviceView):
    permission_classes = [PanelRule]
    panel_page = "devices:device_users_transfer"

    @endpoint(
        id="device-users-copy", area=AREA, title="Copy users to another device",
        summary="The users given - or everyone - onto another device of the same model.",
        what_it_does=["Queues each user with their saved fingerprints and faces on the other "
                      "device, and links the employees there too."],
        description="Only between devices whose fingerprint and face formats match (the same "
                    "model). The target takes a few per check-in.",
        roles=ROLES, scopes=["devices:write"], params=[DEVICE_PARAM],
        request=s.CopyInputSerializer, response=s.CopiedSerializer,
        request_example={"target_device_id": "7a2d9b3c-8f9e-4d4c-ab2f-3e5f6c7d8e9f",
                         "all": True},
        response_example={"sent": ["41", "42"], "fingerprints": 4, "faces": 2,
                          "employees_linked": 2, "failed": []},
        errors=ONE_ERRORS,
    )
    def post(self, request, device_id):
        source = _device(device_id)
        data = s.CopyInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        target = BiometricDevice.objects.filter(
            public_id=data.validated_data["target_device_id"]).first()
        if target is None:
            refuse({"target_device_id": ["No such device in this company."]})
        pins = _pins(request, data.validated_data, source)
        if not pins:
            refuse({"pins": ["Give at least one user number."]})
        result = _mapping_errors(lambda: mapping.transfer_users(
            actor=request.user, source=source, target=target, pins=pins))
        return Response(s.CopiedSerializer({
            "sent": result.sent, "fingerprints": result.fingerprints, "faces": result.faces,
            "employees_linked": len(result.mapped),
            "failed": [{"pin": p, "reason": r} for p, r in result.failed]}).data)


class LinkByIdView(DeviceView):
    permission_classes = [PanelRule]
    panel_page = "devices:device_map_automatically"

    @endpoint(
        id="device-users-link", area=AREA, title="Link users by Employee ID",
        summary="Device user numbers that are an employee's Employee ID become linked to them.",
        what_it_does=["Links each one whose number is an Employee ID of the device's branch.",
                      "Earlier scans of those numbers are re-checked."],
        description="The users given, or every unlinked user. GET …/users says, for each, why "
                    "it is or is not ready.",
        roles=ROLES, scopes=["devices:write"], params=[DEVICE_PARAM],
        request=s.PinsInputSerializer, response=s.LinkResultSerializer,
        request_example={"all": True},
        response_example={"linked": [{"pin": "41", "employee": {"id": 41, "name": "Rahim Uddin"}}],
                          "left": [{"pin": "77", "reason": "no employee has this Employee ID"}],
                          "rechecked": 3},
        errors=ONE_ERRORS,
    )
    def post(self, request, device_id):
        device = _device(device_id)
        data = s.PinsInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        result = mapping.map_automatically(actor=request.user, device=device,
                                           pins=_picked(data.validated_data))
        rechecked = sum(o.rechecked or 0 for o in result.mapped)
        return Response(s.LinkResultSerializer({
            "linked": [{"pin": o.enrollment.device_user_id, "employee": _ref(o.employee)}
                       for o in result.mapped],
            "left": [{"pin": p, "reason": r} for p, _e, r in result.skipped]
            + [{"pin": "", "reason": f"{e.full_name}: {r}"} for e, _d, r in result.failed],
            "rechecked": rechecked}).data)


class ReplaceLinksView(DeviceView):
    permission_classes = [PanelRule]
    panel_page = "devices:device_replace_links"

    @endpoint(
        id="device-users-replace-links", area=AREA, title="Replace old links",
        summary="Someone linked here under an old number gets the number the device uses.",
        what_it_does=["Ends the old link and links the new number from the first day it "
                      "scanned; earlier scans are re-checked."],
        description="For users whose reason is other_number in GET …/users.",
        roles=ROLES, scopes=["devices:write"], params=[DEVICE_PARAM],
        request=s.PinsInputSerializer, response=s.LinkResultSerializer,
        request_example={"pins": ["1041"]},
        response_example={"linked": [{"pin": "1041", "employee": {"id": 41,
                                                                  "name": "Rahim Uddin"}}],
                          "left": [], "rechecked": 5},
        errors=ONE_ERRORS,
    )
    def post(self, request, device_id):
        device = _device(device_id)
        data = s.PinsInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        result = mapping.replace_old_links(actor=request.user, device=device,
                                           pins=_picked(data.validated_data))
        return Response(s.LinkResultSerializer({
            "linked": [{"pin": new, "employee": _ref(employee)}
                       for employee, _old, new, _start in result.replaced],
            "left": [{"pin": p, "reason": r} for p, r in result.skipped],
            "rechecked": result.rechecked}).data)


class ImportUsersView(DeviceView):
    permission_classes = [PanelRule]
    panel_page = "devices:device_users_import"

    @endpoint(
        id="device-users-import", area=AREA, title="Add device users as employees",
        summary="Users nobody is linked to become employees, linked under their number.",
        what_it_does=["Links users whose number is an existing Employee ID.",
                      "Adds the others as employees (Employee ID = their number) in the "
                      "branch's Unassigned department, without pay."],
        description="The users given, or everyone. Then give them a department and pay.",
        roles=ROLES, scopes=["devices:write"], params=[DEVICE_PARAM],
        request=s.PinsInputSerializer, response=s.DeviceImportResultSerializer,
        request_example={"all": True},
        response_example={"created": [{"id": 90, "name": "STRANGER"}], "linked": [],
                          "left": []},
        errors=ONE_ERRORS,
    )
    def post(self, request, device_id):
        device = _device(device_id)
        data = s.PinsInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        result = mapping.import_users(actor=request.user, device=device,
                                      pins=_picked(data.validated_data))
        return Response(s.DeviceImportResultSerializer({
            "created": [_ref(e) for e in result.created],
            "linked": [_ref(o.employee if hasattr(o, "employee") else o) for o in result.mapped],
            "left": [{"pin": p, "reason": r} for p, r in result.skipped
                     if r != "already linked"]}).data)


class LoadView(DeviceView):
    permission_classes = [PanelRule]
    panel_page = "devices:device_load"

    @endpoint(
        id="device-load", area=AREA, title="Load employees onto a device",
        summary="A new or replaced device gets every active employee of its branch.",
        what_it_does=["Starts a background job: each employee goes with Employee ID, name, "
                      "card, and the fingerprint and face kept for them.",
                      "Follow it with GET …/jobs."],
        description="Asking again while one runs does not send anybody twice.",
        roles=ROLES, scopes=["devices:write"], params=[DEVICE_PARAM], response_status=201,
        response=s.LoadStartedSerializer,
        response_example={"started": True, "total": 250,
                          "detail": "Loading 250 employees; follow it with GET …/jobs."},
        errors=ONE_ERRORS,
    )
    def post(self, request, device_id):
        from devices.services import load_jobs

        device = _device(device_id)
        job, started = _mapping_errors(lambda: load_jobs.start_load(actor=request.user,
                                                                    device=device))
        detail = (f"Loading {job.total} employee(s) onto {device.name}; follow it with GET …/jobs."
                  if started else
                  f"Already loading: {job.done_count + job.failed_count} of {job.total} prepared.")
        if started and not job.total:
            detail = f"{device.branch.name} has no active employees to load."
        return Response(s.LoadStartedSerializer({"started": started, "total": job.total,
                                                 "detail": detail}).data,
                        status=201 if started else 200)


class SaveTemplatesView(DeviceView):
    permission_classes = [PanelRule]
    panel_page = "devices:device_templates_save"

    @endpoint(
        id="device-templates-save", area=AREA, title="Save fingerprints and faces",
        summary="Keep, encrypted, the fingerprints and faces this device has sent.",
        what_it_does=["Saves what already arrived, and asks the device to send them again so "
                      "anything newer follows."],
        description="Saved templates let a person be put back on a device, or copied to another "
                    "of the same model. They are never returned by the API.",
        roles=ROLES, scopes=["devices:write"], params=[DEVICE_PARAM],
        response=s.TemplatesSavedSerializer,
        response_example={"added": 12, "updated": 0, "unchanged": 230, "asked_device": True},
        errors=ONE_ERRORS,
    )
    def post(self, request, device_id):
        device = _device(device_id)
        try:
            added, updated, unchanged = templates.save_from_messages(device)
        except templates.TemplateKeyMissing as exc:
            raise ApiError("validation_error", str(exc)) from exc
        key = "query_biodata" if protocol.supports(device, "query_biodata") else "query_users"
        queued = queue_command(device=device, command_key=key, requested_by=request.user)
        _audit(request, "device.templates_saved", device,
               {"added": added, "updated": updated, "unchanged": unchanged,
                "asked_device": bool(queued)})
        return Response(s.TemplatesSavedSerializer({
            "added": added, "updated": updated, "unchanged": unchanged,
            "asked_device": bool(queued)}).data)


# --- settings and the server address ------------------------------------------------------

class SetOptionView(DeviceView):
    permission_classes = [PanelRule]
    panel_page = "devices:device_set_option"

    @endpoint(
        id="device-options-set", area=AREA, title="Change a device setting",
        summary="One changeable setting, applied on the device's next check-in.",
        what_it_does=["Checks the value, queues the change and records it in the audit log."],
        description="Only the settings GET …/options lists as writable, within their range.",
        roles=ROLES, scopes=["devices:write"], params=[DEVICE_PARAM], response_status=201,
        request=s.OptionInputSerializer, response=s.QueuedSerializer,
        request_example={"key": "push_interval_seconds", "value": "15"},
        response_example={"queued": True, "command_id": 814,
                          "detail": "Queued “SET OPTION Delay=15”."},
        errors=ONE_ERRORS,
    )
    def post(self, request, device_id):
        device = _device(device_id)
        data = s.OptionInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        before = dict(device.settings or {})
        entry, error = commands.queue_set_option(
            device=device, option_key=data.validated_data["key"],
            value=data.validated_data["value"], requested_by=request.user)
        if error:
            raise ApiError("validation_error", error)
        device.refresh_from_db()
        device_admin.audit(actor=request.user, company_id=request.company_id,
                           action="device.option_queued", obj=device,
                           before={"settings": before},
                           after={"settings": device.settings, "command": entry["body"]},
                           ip=client_ip(request))
        return Response(s.QueuedSerializer({
            "queued": True, "command_id": entry["id"],
            "detail": f"Queued “{entry['body']}”. The device applies it on its next check-in."})
            .data, status=201)


def _address(device):
    return server_address.status_payload(
        device, server_address.refresh(server_address.latest_change(device)))


class ServerAddressView(DeviceView):
    permission_classes = [PanelRule]
    panel_page = {"GET": "devices:device_server_address_status", "POST": "devices:device_edit"}

    @endpoint(
        id="device-address-get", area=AREA, title="The device's server address",
        summary="The address the device sends to, and how a change is going.",
        what_it_does=["Answers the saved address and the latest change's progress.",
                      "Reading it is also what notices a change that timed out."],
        description="Ask every few seconds while active is true. When the device went quiet, "
                    "recovery says what to type on the terminal to bring it back.",
        roles=ROLES, scopes=["devices:read"], params=[DEVICE_PARAM],
        response=s.AddressSerializer,
        response_example={"active": False, "status": "", "message": "",
                          "saved_address": "https://attendance.example.com"},
        errors=["not_authenticated", "invalid_token", "token_expired", "session_ended",
                "signature_required", "invalid_signature", "two_step_setup_required",
                "scope_missing", "permission_denied", "rate_limited", "server_error",
                "not_found"],
    )
    def get(self, request, device_id):
        return Response(s.AddressSerializer(_address(_device(device_id))).data)

    @endpoint(
        id="device-address-change", area=AREA, title="Change the device's server address",
        summary="Point the device at another address - checked before the device is told.",
        what_it_does=[
            "First checks that the new address answers like this server; if not, nothing is "
            "sent to the device.",
            "Then queues the change; the saved address changes only once the device has called "
            "in at the new one.",
        ],
        description=("A device pointed at an address it cannot reach is out of reach until "
                     "someone types the old one back in at the terminal - hence the check. "
                     "Refused for a device that has never connected (set it on the terminal), "
                     "a retired one, or while another change runs."),
        roles=ROLES, scopes=["devices:write"], params=[DEVICE_PARAM], response_status=201,
        request=s.AddressInputSerializer, response=s.AddressSerializer,
        request_example={"address": "https://attendance.example.com"},
        response_example={"active": True, "status": "queued",
                          "message": "Waiting for the device to collect the change.",
                          "saved_address": "http://192.168.1.20:8000",
                          "new_address": "https://attendance.example.com"},
        errors=ONE_ERRORS,
    )
    def post(self, request, device_id):
        device = _device(device_id)
        data = s.AddressInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            target = server_address.parse_address(data.validated_data["address"])
        except server_address.ServerAddressError as exc:
            refuse({"address": [str(exc)]})
        saved = server_address.current_address(device)
        if saved is not None and saved.matches(target):
            refuse({"address": ["The device already sends to this address."]})
        try:
            attempt = server_address.request_change(device=device, actor=request.user,
                                                    raw_address=target.text)
        except server_address.ServerAddressError as exc:
            raise ApiError("validation_error", str(exc)) from exc
        device_admin.audit(actor=request.user, company_id=request.company_id,
                           action="device.server_address.requested", obj=attempt,
                           before={"address": server_address.previous_address_text(attempt)},
                           after={"address": target.text, "status": attempt.status,
                                  "reason": attempt.failure_reason},
                           ip=client_ip(request))
        return Response(s.AddressSerializer(_address(device)).data, status=201)


class CancelAddressView(DeviceView):
    permission_classes = [PanelRule]
    panel_page = "devices:device_server_address_cancel"

    @endpoint(
        id="device-address-cancel", area=AREA, title="Cancel a server address change",
        summary="Abandon a change that has not reached the device yet.",
        what_it_does=["Cancels it while the new address is still being checked."],
        description="Once queued the device may already have taken it, so it can no longer be "
                    "cancelled.",
        roles=ROLES, scopes=["devices:write"], params=[DEVICE_PARAM],
        response=s.AddressSerializer,
        response_example={"active": False, "status": "cancelled", "message": "Cancelled.",
                          "saved_address": "http://192.168.1.20:8000"},
        errors=ONE_ERRORS,
    )
    def post(self, request, device_id):
        device = _device(device_id)
        attempt = server_address.latest_change(device)
        if attempt is None:
            raise ApiError("validation_error", "There is no server address change to cancel.")
        try:
            server_address.cancel(attempt=attempt, actor=request.user)
        except server_address.ServerAddressError as exc:
            raise ApiError("validation_error", str(exc)) from exc
        device_admin.audit(actor=request.user, company_id=request.company_id,
                           action="device.server_address.cancelled", obj=attempt,
                           after={"status": attempt.status}, ip=client_ip(request))
        return Response(s.AddressSerializer(_address(device)).data)


# --- people onto devices (branch managers too) ------------------------------------------------

def _mapped_out(outcome):
    return {"employee": _ref(outcome.employee), "device": _device_ref(outcome.device),
            "user_number": outcome.enrollment.device_user_id,
            "sent_to_device": outcome.uploaded, "with_fingerprint": bool(outcome.fingerprint),
            "with_face": bool(outcome.face), "note": outcome.note,
            "rechecked": outcome.rechecked}


def _failed_out(employee, device, reason):
    return {"employee": _ref(employee), "device": _device_ref(device) if device else None,
            "reason": reason}


class PeopleView(ApiView):
    """The Employees list's Map, Bulk map and Send to devices: open to whoever
    may edit people of that branch, checked by the mapping service."""

    permission_classes = [PanelRule]
    company_required = True
    read_scope, write_scope = "devices:read", "devices:write"
    throttle_scope = "write"


class MapEmployeeView(PeopleView):
    permission_classes = [PanelRule]
    panel_page = "devices:employee_map"

    @endpoint(
        id="employees-map", area=AREA, title="Map an employee onto a device",
        summary="Link one employee to a device of their branch, under their Employee ID.",
        what_it_does=["Links them from the day given (today unless given) and sends them to "
                      "the device with their saved fingerprint and face.",
                      "Earlier scans of their number are re-checked."],
        description="The device must be of their branch.",
        roles=MAPPERS, scopes=["devices:write"],
        params=[Param("employee_id", PATH, "integer", "The employee id.", required=True,
                      example=41)],
        response_status=201, request=s.MapInputSerializer, response=s.MappedSerializer,
        request_example={"device_id": "6f1c9a2b-7e8d-4c3b-9a1f-2d4e5b6c7d8e"},
        response_example={"employee": {"id": 41, "name": "Rahim Uddin"},
                          "device": {"id": "6f1c9a2b-7e8d-4c3b-9a1f-2d4e5b6c7d8e",
                                     "name": "Main gate"},
                          "user_number": "41", "sent_to_device": True, "with_fingerprint": True,
                          "with_face": True, "note": "", "rechecked": 0},
        errors=ONE_ERRORS,
    )
    def post(self, request, employee_id):
        employee = Employee.objects.filter(pk=employee_id).first()
        if employee is None:
            raise ApiError("not_found", "No such employee in this company.")
        data = s.MapInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        device = BiometricDevice.objects.filter(public_id=values["device_id"]).first()
        if device is None:
            refuse({"device_id": ["No such device in this company."]})
        outcome = _mapping_errors(lambda: mapping.map_one(
            actor=request.user, device=device, employee=employee,
            start_day=values.get("start_day"), attendance_enabled=values["attendance_enabled"],
            assigned=values["assigned"]))
        return Response(s.MappedSerializer(_mapped_out(outcome)).data, status=201)


class BulkMapView(PeopleView):
    permission_classes = [PanelRule]
    panel_page = "devices:employee_bulk_map"

    @endpoint(
        id="employees-bulk-map", area=AREA, title="Map a whole branch onto its devices",
        summary="Every active employee of a branch, onto one of its devices or all of them.",
        what_it_does=["Links each one under their Employee ID and sends them to the device(s).",
                      "Those already linked are left as they are."],
        description="A large branch takes the devices a few minutes to collect.",
        roles=MAPPERS, scopes=["devices:write"], response=s.BulkMappedSerializer,
        request=s.BulkMapInputSerializer, request_example={"branch_id": 3},
        response_example={"mapped": [], "already": 120, "failed": [], "rechecked": 0},
        errors=WRITE_ERRORS,
    )
    def post(self, request):
        data = s.BulkMapInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        branch = Branch.objects.filter(pk=values["branch_id"]).first()
        if branch is None:
            refuse({"branch_id": ["No such branch in this company."]})
        device = None
        if values.get("device_id"):
            device = BiometricDevice.objects.filter(public_id=values["device_id"],
                                                    branch=branch).first()
            if device is None:
                refuse({"device_id": ["No such device in that branch."]})
        result = _mapping_errors(lambda: mapping.map_branch(
            actor=request.user, branch=branch, device=device, start_day=values.get("start_day"),
            attendance_enabled=values["attendance_enabled"], assigned=values["assigned"]))
        return Response(s.BulkMappedSerializer({
            "mapped": [_mapped_out(o) for o in result.mapped], "already": len(result.skipped),
            "failed": [_failed_out(e, d, r) for e, d, r in result.failed],
            "rechecked": result.rechecked}).data)


class SendEmployeesView(PeopleView):
    permission_classes = [PanelRule]
    panel_page = "devices:employees_send"

    @endpoint(
        id="employees-send", area=AREA, title="Send employees to their devices",
        summary="The employees given - or everyone - onto their branch's devices again.",
        what_it_does=["Sends each to the devices of their branch, with what is saved for them."],
        description="Anyone without a saved fingerprint or face goes with ID and name only: "
                    "enrol them at the terminal and the device reports it back.",
        roles=MAPPERS, scopes=["devices:write"], request=s.SendInputSerializer,
        response=s.SentSerializer, request_example={"employee_ids": [41, 42]},
        response_example={"sent": [{"employee": {"id": 41, "name": "Rahim Uddin"},
                                    "device": {"id": "6f1c9a2b-7e8d-4c3b-9a1f-2d4e5b6c7d8e",
                                               "name": "Main gate"}}], "failed": []},
        errors=WRITE_ERRORS,
    )
    def post(self, request):
        data = s.SendInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        if values["all"]:
            employees = list(Employee.objects.exclude(
                employment_status__in=["resigned", "terminated"]))
        else:
            employees = list(Employee.objects.filter(pk__in=values.get("employee_ids") or []))
        if not employees:
            refuse({"employee_ids": ["Give at least one employee."]})
        result = mapping.send_employees(actor=request.user, employees=employees)
        return Response(s.SentSerializer({
            "sent": [{"employee": _ref(e), "device": _device_ref(d)} for e, d in result.sent],
            "failed": [_failed_out(e, d, r) for e, d, r in result.failed]}).data)



# --- other makes of device ---------------------------------------------------------------------

MAX_SCANS = 500


class IngestPunchesView(ApiView):
    """Scans from a device that is not a ZKTeco push terminal, sent by a
    machine with an API key. Stored and judged by the same pipeline as the
    terminals' own (``devices.services.ingestion.ingest``)."""

    permission_classes = [PanelRule]
    company_required = True
    read_scope, write_scope = None, "punches:write"
    throttle_scope = "write"

    @endpoint(
        id="ingest-punches", area=AREA, title="Push scans from another device",
        summary="For a device that is not a ZKTeco terminal: send its scans here.",
        what_it_does=[
            "Stores the batch as a message, exactly as received.",
            "Makes a punch of each scan and judges it like a terminal's own: whose it is, "
            "whether it counts; then rebuilds those days of attendance.",
        ],
        description=("For machines: an API key with the punches:write scope, signed like any "
                     "request. Register the device first (any model) and enroll the people on it, "
                     "so user numbers are linked. Times are the device's own local time; its "
                     "time zone is the one registered. Send the same batch_id again after a lost "
                     "answer: nothing is stored twice."),
        roles=["A machine with an API key holding punches:write"], scopes=["punches:write"],
        response_status=201, request=s.IngestInputSerializer, response=s.IngestedSerializer,
        request_example={"device_id": "6f1c9a2b-7e8d-4c3b-9a1f-2d4e5b6c7d8e",
                         "batch_id": "gate-2026-10-05-0915",
                         "scans": [{"user_number": "41", "time": "2026-10-05 09:02:08",
                                    "method": "card"}]},
        response_example={"replay": False, "message_id": "1b2c3d4e-5f60-4718-293a-4b5c6d7e8f90",
                          "punches": 1, "detail": "Stored 1 scan."},
        errors=WRITE_ERRORS + ["not_found", "payload_too_large"],
    )
    def post(self, request):
        import datetime
        import json

        from devices.adapters.base import ParsedMessage, ParsedPunch
        from devices.models import DeviceMessage
        from devices.services.ingestion import INGESTING_STATUSES, ingest

        if getattr(request, "api_key", None) is None:
            raise ApiError("permission_denied", "This endpoint is for machines: use an API key "
                                                "with the punches:write scope.")
        data = s.IngestInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        if len(values["scans"]) > MAX_SCANS:
            refuse({"scans": [f"At most {MAX_SCANS} scans per call."]})
        device = BiometricDevice.objects.filter(public_id=values["device_id"]).first()
        if device is None:
            raise ApiError("not_found", "No such device in this company.")
        if device.status not in INGESTING_STATUSES:
            refuse({"device_id": [f"This device is {device.status}: it may not send scans."]})
        punches, problems = [], {}
        for index, scan in enumerate(values["scans"]):
            raw = scan["time"].replace("T", " ")
            try:
                moment = datetime.datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                problems[f"scans[{index}].time"] = ["Use YYYY-MM-DD HH:MM:SS."]
                continue
            punches.append(ParsedPunch(
                source_record_index=index, device_user_id=scan["user_number"].strip(),
                punched_at_device_raw=raw, punched_at_device=moment,
                verification_method=scan["method"],
                raw_record={"user_number": scan["user_number"], "time": scan["time"],
                            "method": scan["method"]}))
        if problems:
            refuse(problems)
        body = json.dumps({"batch_id": values["batch_id"], "scans": [
            {k: v for k, v in scan.items()} for scan in values["scans"]]})
        parsed = ParsedMessage(
            message_type=DeviceMessage.MessageType.PUNCH_BATCH, punches=punches,
            idempotency_key=f"api:{request.api_key.public_id}:{values['batch_id']}",
            record_count=len(punches), payload_json={"source": "api", "batch_id":
                                                     values["batch_id"]})
        result = ingest(device=device, parsed=parsed, raw_body=body,
                        source_ip=client_ip(request), headers={}, content_type="application/json")
        count = result.extraction.accepted_count if not result.is_replay else 0
        return Response(s.IngestedSerializer({
            "replay": result.is_replay, "message_id": str(result.message.public_id),
            "punches": count,
            "detail": "This batch was already received; nothing was stored again."
            if result.is_replay else f"Stored {count} scan{'s' if count != 1 else ''}."}).data,
            status=200 if result.is_replay else 201)
