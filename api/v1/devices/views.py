"""Devices, part 1: setting them up (docs/api/00-PLAN.md phase 5; the guide:
docs/api/50-devices-setup.md).

Registering, changing and retiring a device; what to type on the terminal;
whether it is calling in, and a connection test; the departments it serves;
who is which user number on it (enrollments); and which devices count for
attendance. The panel's Devices pages, through the same rule
(``devices.services.panel_access``: the owner or an unrestricted company
administrator), forms and services (``devices.services.device_admin``).
"""

import datetime

from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.response import Response

from api.core.docs import PATH, QUERY, Param, endpoint
from api.core.errors import ApiError
from api.core.forms import refuse, service_errors
from api.core.network import client_ip
from api.core.permissions import PanelRule
from api.core.views import ApiView
from api.v1.devices import serializers as s
from common.forms import company_timezone
from devices.forms import (
    BiometricDeviceForm,
    DeviceDepartmentForm,
    DeviceEnrollmentForm,
    RecheckPunchesForm,
    serial_taken_message,
)
from devices.models import BiometricDevice, DeviceDepartment, DeviceEnrollment, DeviceModel
from devices.services import (
    attendance_rules,
    connection,
    device_admin,
    panel_access,
    setup_instructions,
)
from devices.services.commands import pending_summary, queue_command

AREA = "devices"
ROLES = ["Company owner or administrator (not limited to some branches)"]
READ_ERRORS = ["not_authenticated", "invalid_token", "token_expired", "session_ended",
               "signature_required", "invalid_signature", "two_step_setup_required",
               "scope_missing", "permission_denied", "rate_limited", "server_error"]
WRITE_ERRORS = READ_ERRORS + ["validation_error", "unknown_field"]
DEVICE_PARAM = Param("device_id", PATH, "string (uuid)", "The device id.", required=True,
                     example="6f1c9a2b-7e8d-4c3b-9a1f-2d4e5b6c7d8e")
CONNECTION_EXAMPLE = {"key": "connected", "label": "Connected", "tone": "success",
                      "detail": "Checked in 8 seconds ago",
                      "last_seen_at": "2026-10-05T10:15:02+06:00"}
DEVICE_EXAMPLE = {
    "id": "6f1c9a2b-7e8d-4c3b-9a1f-2d4e5b6c7d8e", "name": "Main gate",
    "serial_number": "CQZ7232460045", "branch": {"id": 3, "name": "Chattogram"},
    "device_model": {"id": 2, "name": "SenseFace 2A", "vendor": "ZKTeco"},
    "external_device_id": "", "timezone": "Asia/Dhaka", "status": "active",
    "installed_at": "2026-01-10T10:00:00+06:00", "push_interval_seconds": 10,
    "error_delay_seconds": 30, "realtime": True, "has_comm_key": False,
    "connection": CONNECTION_EXAMPLE}


# --- helpers ------------------------------------------------------------------------

def _ip(request):
    return client_ip(request)


def _moment(value, field):
    """``(date, time)`` text for the panel's date-and-time box (company time)."""
    value = (value or "").strip()
    if not value:
        return "", ""
    day, _, moment = value.partition("T")
    try:
        datetime.date.fromisoformat(day)
        if moment:
            datetime.time.fromisoformat(moment[:5])
    except ValueError:
        refuse({field: ["Use YYYY-MM-DD, or YYYY-MM-DDTHH:MM."]})
    return day, moment[:5]


def _stored_moment(instant):
    if instant is None:
        return "", ""
    local = instant.astimezone(company_timezone())
    return local.date().isoformat(), local.strftime("%H:%M")


def _put_moment(data, name, parts):
    data[f"{name}_0"], data[f"{name}_1"] = parts


def _device_out(device, now=None):
    settings = device.settings or {}
    model = device.device_model
    return {
        "id": str(device.public_id), "name": device.name, "serial_number": device.serial_number,
        "branch": {"id": device.branch_id, "name": device.branch.name},
        "device_model": {"id": model.pk, "name": model.name, "vendor": model.vendor.name}
        if model else None,
        "external_device_id": device.external_device_id or "", "timezone": device.timezone,
        "status": device.status, "installed_at": device.installed_at,
        "push_interval_seconds": settings.get("push_interval_seconds", 10),
        "error_delay_seconds": settings.get("error_delay_seconds", 30),
        "realtime": settings.get("realtime", True),
        "has_comm_key": bool(device.authentication_secret_hash),
        "connection": connection.connection_of(device, now).as_dict(),
    }


def _device(device_id):
    device = (BiometricDevice.objects.select_related("branch", "device_model__vendor")
              .filter(public_id=device_id).first())
    if device is None:
        raise ApiError("not_found", "No such device in this company.")
    return device


def _device_form_data(device, values):
    """What Register / Edit device posts: the stored values with the changes."""
    form = BiometricDeviceForm(instance=device)
    data = {}
    for name, field in form.fields.items():
        if name in ("installed_at", "comm_key"):
            continue
        value = form.initial.get(name, field.initial)
        if hasattr(value, "pk"):
            value = value.pk
        data[name] = "" if value is None else value
    _put_moment(data, "installed_at", _stored_moment(device.installed_at) if device else ("", ""))
    names = {"branch_id": "branch", "device_model_id": "device_model"}
    for key, value in values.items():
        if key == "installed_at":
            _put_moment(data, "installed_at", _moment(value, "installed_at"))
        else:
            data[names.get(key, key)] = "" if value is None else value
    return data


DEVICE_NAMES = {"branch": "branch_id", "device_model": "device_model_id"}


def _check(form):
    if not form.is_valid():
        refuse({name: [str(m) for m in messages] for name, messages in form.errors.items()},
               DEVICE_NAMES)
    return form


class DeviceView(ApiView):
    """Shared: every device endpoint is the owner's or an unrestricted
    administrator's, as on the panel."""

    permission_classes = [PanelRule]
    company_required = True
    panel_page = "devices:device_list"
    read_scope, write_scope = "devices:read", "devices:write"
    throttle_scope = "write"

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        panel_access.assert_may_manage_devices(request.user, request.company_id)


# --- devices ---------------------------------------------------------------------------

class DeviceListView(DeviceView):
    permission_classes = [PanelRule]

    @endpoint(
        id="devices-list", area=AREA, title="Devices",
        summary="The company's attendance devices and whether each is calling in.",
        what_it_does=["Lists the devices with their connection."],
        description="Filter by status or branch_id, or search q by name or serial number.",
        roles=ROLES, scopes=["devices:read"], paginated=True,
        params=[Param("q", QUERY, "string", "Search by name or serial number.", example="gate"),
                Param("status", QUERY, "string", "pending, active, offline, suspended or "
                                                 "retired.", example="active"),
                Param("branch_id", QUERY, "integer", "Only this branch.", example=3)],
        response=s.DeviceSerializer,
        response_example={"count": 1, "next": None, "previous": None, "results": [DEVICE_EXAMPLE]},
        errors=READ_ERRORS,
    )
    def get(self, request):
        rows = BiometricDevice.objects.select_related("branch", "device_model__vendor")
        query = (request.query_params.get("q") or "").strip()[:200]
        if query:
            rows = rows.filter(Q(name__icontains=query) | Q(serial_number__icontains=query)
                               | Q(external_device_id__icontains=query))
        if request.query_params.get("status") in dict(BiometricDevice.Status.choices):
            rows = rows.filter(status=request.query_params["status"])
        branch = request.query_params.get("branch_id", "")
        if branch.isdigit():
            rows = rows.filter(branch_id=int(branch))
        now = timezone.now()
        out = [_device_out(device, now) for device in rows.order_by("name")]
        return self.paginated(request, out, s.DeviceSerializer)

    @endpoint(
        id="devices-register", area=AREA, title="Register a device",
        summary="A new terminal for the company.",
        what_it_does=["Registers it; the answer then says what to type on the terminal "
                      "(GET …/{device_id}/setup)."],
        description=("The serial number is matched to the device's calls exactly as printed; one "
                     "device sends to one company only (a serial registered elsewhere is "
                     "refused). Leave comm_key out for a ZKTeco device: it cannot send one."),
        roles=ROLES, scopes=["devices:write"], response_status=201,
        request=s.DeviceInputSerializer, response=s.RegisteredSerializer,
        request_example={"branch_id": 3, "name": "Main gate", "device_model_id": 2,
                         "serial_number": "CQZ7232460045", "timezone": "Asia/Dhaka"},
        response_example={**DEVICE_EXAMPLE, "status": "pending", "comm_key": None},
        errors=WRITE_ERRORS,
    )
    def post(self, request):
        data = s.DeviceInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = dict(data.validated_data)
        values.pop("remove_comm_key", None)
        form_input = _device_form_data(None, {k: v for k, v in values.items()
                                              if k != "comm_key"})
        form_input["comm_key"] = values.get("comm_key", "")
        form = _check(BiometricDeviceForm(data=form_input))
        with service_errors(DEVICE_NAMES):
            device = device_admin.register_device(actor=request.user,
                                                  company_id=request.company_id, form=form,
                                                  ip=_ip(request))
        out = _device_out(_device(device.public_id))
        return Response(s.RegisteredSerializer({**out, "comm_key": form.issued_comm_key or None})
                        .data, status=201)


class SerialCheckView(DeviceView):
    permission_classes = [PanelRule]

    @endpoint(
        id="devices-serial-check", area=AREA, title="Is a serial number free?",
        summary="Before registering: is this serial already used?",
        what_it_does=["Says whether the serial is free, in the words saving would use."],
        description="Ask it as the serial is typed. device_id leaves that device itself out "
                    "(when changing its serial).",
        roles=ROLES, scopes=["devices:read"],
        params=[Param("serial", QUERY, "string", "The serial number.", required=True,
                      example="CQZ7232460045"),
                Param("device_id", QUERY, "string (uuid)", "The device being changed, if any.",
                      example="6f1c9a2b-7e8d-4c3b-9a1f-2d4e5b6c7d8e")],
        response=s.SerialCheckSerializer,
        response_example={"ok": True, "message": "This serial number is free."},
        errors=READ_ERRORS,
    )
    def get(self, request):
        from devices.services.ingestion import serial_owner_elsewhere

        serial = (request.query_params.get("serial") or "").strip()
        if not serial:
            return Response({"ok": True, "message": ""})
        mine = BiometricDevice.objects.filter(serial_number__iexact=serial)
        device = request.query_params.get("device_id") or ""
        try:
            import uuid

            mine = mine.exclude(public_id=uuid.UUID(device)) if device else mine
        except ValueError:
            pass
        same = mine.first()
        if same is not None:
            return Response({"ok": False, "message": (
                f"This serial number is already used by {same.name} in this company.")})
        owner = serial_owner_elsewhere(serial, request.company_id)
        if owner:
            return Response({"ok": False, "message": serial_taken_message(owner)})
        return Response({"ok": True, "message": "This serial number is free."})


class DeviceDetailView(DeviceView):
    permission_classes = [PanelRule]

    @endpoint(
        id="devices-get", area=AREA, title="One device",
        summary="One device's settings and connection.",
        what_it_does=["Answers the device."],
        description="Another company's device answers not_found.",
        roles=ROLES, scopes=["devices:read"], params=[DEVICE_PARAM],
        response=s.DeviceSerializer, response_example=DEVICE_EXAMPLE,
        errors=READ_ERRORS + ["not_found"],
    )
    def get(self, request, device_id):
        return Response(s.DeviceSerializer(_device_out(_device(device_id))).data)

    @endpoint(
        id="devices-change", area=AREA, title="Change a device",
        summary="A new name, branch, model, serial, status or push settings.",
        what_it_does=["Changes only the fields sent; records it in the audit log."],
        description=("The server address the device sends to is changed with its own endpoint "
                     "(it is checked with the device first). A key, once set, is kept unless "
                     "a new one is sent or remove_comm_key is true."),
        roles=ROLES, scopes=["devices:write"], params=[DEVICE_PARAM],
        request=s.DeviceInputSerializer, response=s.RegisteredSerializer,
        request_example={"name": "Main gate (north)", "push_interval_seconds": 15},
        response_example={**DEVICE_EXAMPLE, "name": "Main gate (north)",
                          "push_interval_seconds": 15, "comm_key": None},
        errors=WRITE_ERRORS + ["not_found"],
    )
    def patch(self, request, device_id):
        device = _device(device_id)
        data = s.DeviceInputSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        values = dict(data.validated_data)
        key, remove = values.pop("comm_key", ""), values.pop("remove_comm_key", False)
        form_input = _device_form_data(device, values)
        form_input["comm_key"] = key
        if remove:
            form_input["remove_comm_key"] = "on"
        form = _check(BiometricDeviceForm(data=form_input, instance=device))
        with service_errors(DEVICE_NAMES):
            device_admin.update_device(actor=request.user, company_id=request.company_id,
                                       device=device, form=form, ip=_ip(request))
        out = _device_out(_device(device_id))
        return Response(s.RegisteredSerializer({**out, "comm_key": form.issued_comm_key or None})
                        .data)


class RetireView(DeviceView):
    permission_classes = [PanelRule]
    panel_page = "devices:device_retire"

    @endpoint(
        id="devices-retire", area=AREA, title="Retire a device",
        summary="It can no longer send data; its history stays.",
        what_it_does=["Retires it; records it in the audit log."],
        description="Nothing is deleted: its punches and messages are kept.",
        roles=ROLES, scopes=["devices:write"], params=[DEVICE_PARAM],
        response=s.DeviceSerializer, response_example={**DEVICE_EXAMPLE, "status": "retired"},
        errors=WRITE_ERRORS + ["not_found"],
    )
    def post(self, request, device_id):
        device = _device(device_id)
        with service_errors():
            device_admin.retire_device(actor=request.user, company_id=request.company_id,
                                       device=device, ip=_ip(request))
        return Response(s.DeviceSerializer(_device_out(_device(device_id))).data)


class DeviceModelListView(DeviceView):
    permission_classes = [PanelRule]

    @endpoint(
        id="device-models", area=AREA, title="Device models",
        summary="The models a device can be registered as.",
        what_it_does=["Lists the active models, by vendor."],
        description="Send its id as device_model_id.",
        roles=ROLES, scopes=["devices:read"], paginated=True,
        response=s.DeviceModelSerializer,
        response_example={"count": 1, "next": None, "previous": None,
                          "results": [{"id": 2, "name": "SenseFace 2A", "vendor": "ZKTeco"}]},
        errors=READ_ERRORS,
    )
    def get(self, request):
        rows = [{"id": m.pk, "name": m.name, "vendor": m.vendor.name}
                for m in DeviceModel.objects.filter(is_active=True).select_related("vendor")
                .order_by("vendor__name", "name")]
        return self.paginated(request, rows, s.DeviceModelSerializer)


# --- setup and connection -------------------------------------------------------------------

class SetupView(DeviceView):
    permission_classes = [PanelRule]

    @endpoint(
        id="devices-setup", area=AREA, title="What to type on the terminal",
        summary="The server address, port and the rest, for the device's own menu.",
        what_it_does=["Answers each setting to enter on the terminal, in order."],
        description="host_unreachable is true when this server is reached at an address a "
                    "device cannot use (such as localhost): use the server's real address.",
        roles=ROLES, scopes=["devices:read"], params=[DEVICE_PARAM],
        response=s.SetupSerializer,
        response_example={"lines": [
            {"setting": "Server address / ADMS server", "value": "attendance.example.com",
             "note": "Domain or IP only — the device adds the path itself."},
            {"setting": "Server port", "value": "443", "note": "443 for HTTPS, 80 for plain HTTP."}],
            "endpoint_url": "https://attendance.example.com/iclock/cdata",
            "host_unreachable": False},
        errors=READ_ERRORS + ["not_found"],
    )
    def get(self, request, device_id):
        device = _device(device_id)
        _scheme, host, _port = setup_instructions.server_address(request)
        lines = setup_instructions.build(request, device)
        return Response(s.SetupSerializer({
            "lines": [{"setting": line.device_menu_label, "value": line.value,
                       "note": line.note} for line in lines],
            "endpoint_url": setup_instructions.full_endpoint(request),
            "host_unreachable": setup_instructions.is_unreachable_host(host)}).data)


class ConnectionsView(DeviceView):
    permission_classes = [PanelRule]

    @endpoint(
        id="devices-connections", area=AREA, title="Connections",
        summary="Is each device calling in - for a live status board.",
        what_it_does=["Answers every device's connection, by id."],
        description="Ask it every 30 seconds or so to keep a board up to date.",
        roles=ROLES, scopes=["devices:read"], paginated=True,
        response=s.DeviceConnectionSerializer,
        response_example={"count": 1, "next": None, "previous": None, "results": [{
            "device": {"id": "6f1c9a2b-7e8d-4c3b-9a1f-2d4e5b6c7d8e", "name": "Main gate"},
            "connection": CONNECTION_EXAMPLE}]},
        errors=READ_ERRORS,
    )
    def get(self, request):
        now = timezone.now()
        rows = [{"device": {"id": str(device.public_id), "name": device.name},
                 "connection": connection.connection_of(device, now).as_dict()}
                for device in BiometricDevice.objects.order_by("name")]
        return self.paginated(request, rows, s.DeviceConnectionSerializer)


class ConnectionTestView(DeviceView):
    permission_classes = [PanelRule]
    panel_page = "devices:device_connection_test"

    @endpoint(
        id="devices-test-start", area=AREA, title="Start a connection test",
        summary="Is the terminal calling in, and does it take commands?",
        what_it_does=["Starts a test from now and sends a harmless command to the device.",
                      "Then ask GET …/test-connection with since and command_id."],
        description="A retired device is not tested.",
        roles=ROLES, scopes=["devices:write"], params=[DEVICE_PARAM], response_status=201,
        response=s.TestStartedSerializer,
        response_example={"started_at": "2026-10-05T10:15:00+06:00", "command_id": 812},
        errors=WRITE_ERRORS + ["not_found"],
    )
    def post(self, request, device_id):
        device = _device(device_id)
        if device.status == BiometricDevice.Status.RETIRED:
            raise ApiError("validation_error", "A retired device is not tested.")
        started = timezone.now()
        entry = queue_command(device=device, command_key=connection.TEST_COMMAND,
                              requested_by=request.user)
        if entry is None:
            entry = next((e for e in pending_summary(device)
                          if e.get("key") == connection.TEST_COMMAND), None)
        if entry is not None:
            device_admin.audit(actor=request.user, company_id=request.company_id,
                               action="device.command_queued", obj=device,
                               after={"command": entry["body"], "purpose": "connection test"},
                               ip=_ip(request))
        return Response(s.TestStartedSerializer({
            "started_at": started, "command_id": entry["id"] if entry else None}).data,
            status=201)

    @endpoint(
        id="devices-test-status", area=AREA, title="How the connection test went",
        summary="Has the device called in since the test started?",
        what_it_does=["Answers whether it checked in, and how far the test command got.",
                      "Once it has waited long enough without a check-in, the advice says "
                      "what to check."],
        description="Ask every few seconds until checked_in or gave_up.",
        roles=ROLES, scopes=["devices:read"],
        params=[DEVICE_PARAM,
                Param("since", QUERY, "string (date-time)", "started_at from the start.",
                      required=True, example="2026-10-05T10:15:00+06:00"),
                Param("command_id", QUERY, "integer", "command_id from the start.", example=812)],
        response=s.TestSerializer,
        response_example={"connection": CONNECTION_EXAMPLE,
                          "started_at": "2026-10-05T10:15:00+06:00", "checked_in": True,
                          "checked_in_at": "2026-10-05T10:15:08+06:00", "waited_seconds": 9,
                          "gave_up": False, "command": {"id": 812, "stage": "answered"},
                          "advice": []},
        errors=READ_ERRORS + ["not_found", "validation_error"],
    )
    def get(self, request, device_id):
        device = _device(device_id)
        started = parse_datetime(request.query_params.get("since") or "")
        if started is None:
            refuse({"since": ["The test's started_at, e.g. 2026-10-05T10:15:00+06:00."]})
        if timezone.is_naive(started):
            started = timezone.make_aware(started, datetime.timezone.utc)
        command = request.query_params.get("command_id", "")
        result = connection.test_status(device, since=started,
                                        command_id=int(command) if command.isdigit() else None)
        advice = []
        if result.gave_up:
            advice = [{"title": item["title"], "text": item["text"],
                       "values": [list(pair) for pair in item["values"]]}
                      for item in connection.advice(device,
                                                    setup_instructions.build(request, device))]
        return Response(s.TestSerializer(result.as_dict(advice)).data)


# --- departments it serves ----------------------------------------------------------------

def _link_out(link):
    return {"id": link.pk, "department": {"id": link.department_id, "name": link.department.name},
            "effective_from": link.effective_from, "effective_to": link.effective_to,
            "status": link.status}


LINK_EXAMPLE = {"id": 4, "department": {"id": 8, "name": "Software"},
                "effective_from": "2026-01-01T00:00:00+06:00", "effective_to": None,
                "status": "active"}


class DepartmentLinksView(DeviceView):
    permission_classes = [PanelRule]
    panel_page = {"GET": "devices:device_list", "POST": "devices:device_department_add"}

    @endpoint(
        id="devices-departments", area=AREA, title="Departments a device serves",
        summary="For department-devices mode: which departments' punches it counts.",
        what_it_does=["Lists the device's department mappings, newest first."],
        description="Only matters when punches count on department devices.",
        roles=ROLES, scopes=["devices:read"], params=[DEVICE_PARAM], paginated=True,
        response=s.DepartmentLinkSerializer,
        response_example={"count": 1, "next": None, "previous": None, "results": [LINK_EXAMPLE]},
        errors=READ_ERRORS + ["not_found"],
    )
    def get(self, request, device_id):
        device = _device(device_id)
        rows = [_link_out(link) for link in DeviceDepartment.objects.filter(device=device)
                .select_related("department").order_by("-effective_from")]
        return self.paginated(request, rows, s.DepartmentLinkSerializer)

    @endpoint(
        id="devices-departments-add", area=AREA, title="Map a device to a department",
        summary="The device serves a department of its branch, from a moment.",
        what_it_does=["Adds the mapping; records it in the audit log."],
        description="It may not overlap a mapping of the same department that is not ended.",
        roles=ROLES, scopes=["devices:write"], params=[DEVICE_PARAM], response_status=201,
        request=s.DepartmentLinkInputSerializer, response=s.DepartmentLinkSerializer,
        request_example={"department_id": 8, "effective_from": "2026-01-01"},
        response_example=LINK_EXAMPLE, errors=WRITE_ERRORS + ["not_found"],
    )
    def post(self, request, device_id):
        device = _device(device_id)
        data = s.DepartmentLinkInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        form_input = {"department": values["department_id"]}
        _put_moment(form_input, "effective_from", _moment(values["effective_from"],
                                                          "effective_from"))
        _put_moment(form_input, "effective_to", _moment(values.get("effective_to", ""),
                                                        "effective_to"))
        names = {"department": "department_id"}
        form = DeviceDepartmentForm(data=form_input, device=device)
        if not form.is_valid():
            refuse(form.errors, names)
        with service_errors(names):
            link = device_admin.add_department_link(actor=request.user,
                                                    company_id=request.company_id,
                                                    device=device, form=form, ip=_ip(request))
        return Response(s.DepartmentLinkSerializer(_link_out(link)).data, status=201)


class DepartmentLinkEndView(DeviceView):
    permission_classes = [PanelRule]
    panel_page = "devices:device_department_end"

    @endpoint(
        id="devices-departments-end", area=AREA, title="End a department mapping",
        summary="The device no longer serves that department, from now.",
        what_it_does=["Ends the mapping now; it stays listed as ended."],
        description="Ending is not deleting: punches before now keep it.",
        roles=ROLES, scopes=["devices:write"],
        params=[Param("link_id", PATH, "integer", "The mapping id.", required=True, example=4)],
        response=s.DepartmentLinkSerializer,
        response_example={**LINK_EXAMPLE, "status": "ended",
                          "effective_to": "2026-10-05T10:15:00+06:00"},
        errors=WRITE_ERRORS + ["not_found"],
    )
    def post(self, request, link_id):
        link = DeviceDepartment.objects.select_related("department").filter(pk=link_id).first()
        if link is None:
            raise ApiError("not_found", "No such department mapping in this company.")
        with service_errors():
            device_admin.end_department_link(actor=request.user, company_id=request.company_id,
                                             link=link, ip=_ip(request))
        return Response(s.DepartmentLinkSerializer(_link_out(link)).data)


# --- enrollments ---------------------------------------------------------------------------

ENROLLMENT_EXAMPLE = {"id": 7, "device": {"id": "6f1c9a2b-7e8d-4c3b-9a1f-2d4e5b6c7d8e",
                                          "name": "Main gate"},
                      "employee": {"id": 41, "name": "Rahim Uddin"}, "device_user_id": "41",
                      "card_number": "", "device_privilege": "normal_user",
                      "attendance_enabled": True, "assigned_device_authorized": True,
                      "effective_from": "2026-01-01T00:00:00+06:00", "effective_to": None}
ENROLLMENT_NAMES = {"device": "device_id", "employee": "employee_id"}


def _enrollment_out(row):
    return {"id": row.pk, "device": {"id": str(row.device.public_id), "name": row.device.name},
            "employee": {"id": row.employee_id, "name": row.employee.full_name},
            "device_user_id": row.device_user_id, "card_number": row.card_number,
            "device_privilege": row.device_privilege, "attendance_enabled": row.attendance_enabled,
            "assigned_device_authorized": row.assigned_device_authorized,
            "effective_from": row.effective_from, "effective_to": row.effective_to}


def _enrollment_form_data(row, values):
    data = {}
    if row is not None:
        data = {"device": row.device_id, "employee": row.employee_id,
                "device_user_id": row.device_user_id, "card_number": row.card_number,
                "device_privilege": row.device_privilege}
        if row.attendance_enabled:
            data["attendance_enabled"] = "on"
        if row.assigned_device_authorized:
            data["assigned_device_authorized"] = "on"
        _put_moment(data, "effective_from", _stored_moment(row.effective_from))
        _put_moment(data, "effective_to", _stored_moment(row.effective_to))
    else:
        data = {"device_privilege": DeviceEnrollment.Privilege.NORMAL_USER,
                "attendance_enabled": "on", "assigned_device_authorized": "on"}
        _put_moment(data, "effective_from", _stored_moment(timezone.now()))
        _put_moment(data, "effective_to", ("", ""))
    for key, value in values.items():
        if key == "device_id":
            device = BiometricDevice.objects.filter(public_id=value).first()
            data["device"] = device.pk if device else ""
        elif key in ("effective_from", "effective_to"):
            _put_moment(data, key, _moment(value, key))
        elif key in ("attendance_enabled", "assigned_device_authorized"):
            if value:
                data[key] = "on"
            else:
                data.pop(key, None)
        else:
            data[{"employee_id": "employee"}.get(key, key)] = value
    return data


class EnrollmentListView(DeviceView):
    permission_classes = [PanelRule]
    panel_page = {"GET": "devices:enrollment_list", "POST": "devices:enrollment_create"}

    @endpoint(
        id="enrollments-list", area=AREA, title="Enrollments",
        summary="Who is which user number on which device.",
        what_it_does=["Lists the enrollments, newest first."],
        description="Filter by device_id, or search q by name, device or user number.",
        roles=ROLES, scopes=["devices:read"], paginated=True,
        params=[Param("device_id", QUERY, "string (uuid)", "Only this device.",
                      example="6f1c9a2b-7e8d-4c3b-9a1f-2d4e5b6c7d8e"),
                Param("q", QUERY, "string", "Search.", example="rahim")],
        response=s.EnrollmentSerializer,
        response_example={"count": 1, "next": None, "previous": None,
                          "results": [ENROLLMENT_EXAMPLE]},
        errors=READ_ERRORS,
    )
    def get(self, request):
        rows = DeviceEnrollment.objects.select_related("device", "employee")
        query = (request.query_params.get("q") or "").strip()[:200]
        if query:
            rows = rows.filter(Q(device_user_id__icontains=query)
                               | Q(employee__first_name__icontains=query)
                               | Q(employee__last_name__icontains=query)
                               | Q(device__name__icontains=query))
        device = request.query_params.get("device_id")
        if device:
            rows = rows.filter(device__public_id=_device(device).public_id)
        out = [_enrollment_out(row) for row in rows.order_by("-effective_from")[:5000]]
        return self.paginated(request, out, s.EnrollmentSerializer)

    @endpoint(
        id="enrollments-create", area=AREA, title="Enroll an employee on a device",
        summary="An employee is a user number on a device, from a moment.",
        what_it_does=["Adds the enrollment; records it in the audit log."],
        description=("Enrolling means the device recognises them; attendance_enabled and "
                     "assigned_device_authorized (both true unless sent false) decide whether "
                     "their punches count. A user number, or a person, may not have two "
                     "overlapping enrollments on one device."),
        roles=ROLES, scopes=["devices:write"], response_status=201,
        request=s.EnrollmentInputSerializer, response=s.EnrollmentSerializer,
        request_example={"device_id": "6f1c9a2b-7e8d-4c3b-9a1f-2d4e5b6c7d8e", "employee_id": 41,
                         "device_user_id": "41", "effective_from": "2026-01-01"},
        response_example=ENROLLMENT_EXAMPLE, errors=WRITE_ERRORS,
    )
    def post(self, request):
        data = s.EnrollmentInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        form = DeviceEnrollmentForm(data=_enrollment_form_data(None, dict(data.validated_data)))
        if not form.is_valid():
            refuse(form.errors, ENROLLMENT_NAMES)
        with service_errors(ENROLLMENT_NAMES):
            row = device_admin.create_enrollment(actor=request.user,
                                                 company_id=request.company_id, form=form,
                                                 ip=_ip(request))
        return Response(s.EnrollmentSerializer(_enrollment_out(row)).data, status=201)


class EnrollmentDetailView(DeviceView):
    permission_classes = [PanelRule]
    panel_page = {"GET": "devices:enrollment_list", "PATCH": "devices:enrollment_edit"}

    def _row(self, enrollment_id):
        row = DeviceEnrollment.objects.select_related("device", "employee").filter(
            pk=enrollment_id).first()
        if row is None:
            raise ApiError("not_found", "No such enrollment in this company.")
        return row

    @endpoint(
        id="enrollments-get", area=AREA, title="One enrollment",
        summary="One person's user number on one device.",
        what_it_does=["Answers the enrollment."],
        description="Another company's answers not_found.",
        roles=ROLES, scopes=["devices:read"],
        params=[Param("enrollment_id", PATH, "integer", "The enrollment id.", required=True,
                      example=7)],
        response=s.EnrollmentSerializer, response_example=ENROLLMENT_EXAMPLE,
        errors=READ_ERRORS + ["not_found"],
    )
    def get(self, request, enrollment_id):
        return Response(s.EnrollmentSerializer(_enrollment_out(self._row(enrollment_id))).data)

    @endpoint(
        id="enrollments-change", area=AREA, title="Change an enrollment",
        summary="New dates, user number, card, role or switches.",
        what_it_does=["Changes only the fields sent; records every changed field in the audit "
                      "log (past punches are judged by it).",
                      "A new card or role is sent to the terminals they are on."],
        description="End it by giving effective_to.",
        roles=ROLES, scopes=["devices:write"],
        params=[Param("enrollment_id", PATH, "integer", "The enrollment id.", required=True,
                      example=7)],
        request=s.EnrollmentInputSerializer, response=s.EnrollmentSerializer,
        request_example={"effective_to": "2026-12-31T18:00"},
        response_example={**ENROLLMENT_EXAMPLE, "effective_to": "2026-12-31T18:00:00+06:00"},
        errors=WRITE_ERRORS + ["not_found"],
    )
    def patch(self, request, enrollment_id):
        row = self._row(enrollment_id)
        data = s.EnrollmentInputSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        form = DeviceEnrollmentForm(data=_enrollment_form_data(row, dict(data.validated_data)),
                                    instance=row)
        if not form.is_valid():
            refuse(form.errors, ENROLLMENT_NAMES)
        with service_errors(ENROLLMENT_NAMES):
            row, _result = device_admin.update_enrollment(
                actor=request.user, company_id=request.company_id, enrollment=row, form=form,
                ip=_ip(request))
        return Response(s.EnrollmentSerializer(_enrollment_out(self._row(enrollment_id))).data)


# --- which devices count ----------------------------------------------------------------------

class RulesView(DeviceView):
    permission_classes = [PanelRule]
    panel_page = "devices:attendance_rules"

    @endpoint(
        id="devices-rules", area=AREA, title="Which devices count",
        summary="The company rule for which devices' punches count, and what does not count.",
        what_it_does=["Answers the rule, the branches and people with their own, and the "
                      "punches in a range (the last month unless given) that do not count."],
        description="Change the rule with PATCH; judge excluded punches again with POST "
                    "…/attendance-rules/recheck.",
        roles=ROLES, scopes=["devices:read"],
        params=[Param("start", QUERY, "string (date)", "First day of the range.",
                      example="2026-09-01"),
                Param("end", QUERY, "string (date)", "Last day of the range.",
                      example="2026-09-30")],
        response=s.RulesSerializer,
        response_example={"scope": "assigned_devices",
                          "scope_help": {"assigned_devices": "Only on devices each person is "
                                                             "authorised for."},
                          "branch_overrides": [], "employees_with_own_rule": 0,
                          "start": "2026-09-06", "end": "2026-10-05",
                          "excluded": [{"status": "unauthorized_device",
                                        "label": "Not their device",
                                        "what_to_do": "Authorise the device for them.",
                                        "count": 3}]},
        errors=READ_ERRORS + ["validation_error"],
    )
    def get(self, request):
        return Response(s.RulesSerializer(self._out(request)).data)

    def _out(self, request):
        today = timezone.now().astimezone(company_timezone()).date()
        start, end = today - datetime.timedelta(days=29), today
        if request.query_params.get("start") or request.query_params.get("end"):
            form = RecheckPunchesForm(data={"start": request.query_params.get("start", ""),
                                            "end": request.query_params.get("end", "")})
            if not form.is_valid():
                refuse(form.errors)
            start, end = form.cleaned_data["start"], form.cleaned_data["end"]
        with service_errors():
            scope = attendance_rules.company_scope(request.company_id)
        found = attendance_rules.overrides(request.company_id)
        return {
            "scope": scope, "scope_help": attendance_rules.SCOPE_HELP,
            "branch_overrides": [{"id": b.pk, "name": b.name} for b, _l in found["branches"]],
            "employees_with_own_rule": found["employee_count"], "start": start, "end": end,
            "excluded": [{"status": st, "label": label, "what_to_do": todo, "count": count}
                         for st, label, todo, count in attendance_rules.excluded_summary(
                             request.company_id, start=start, end=end)],
        }

    @endpoint(
        id="devices-rules-change", area=AREA, title="Change which devices count",
        summary="The company-wide rule for which devices' punches count.",
        what_it_does=["Changes the rule; punches already stored keep their decision until "
                      "re-checked."],
        description="Branches and people with their own rule keep it.",
        roles=ROLES, scopes=["devices:write"], request=s.ScopeInputSerializer,
        response=s.RulesSerializer, request_example={"scope": "branch_devices"},
        response_example={"scope": "branch_devices", "scope_help": {}, "branch_overrides": [],
                          "employees_with_own_rule": 0, "start": "2026-09-06",
                          "end": "2026-10-05", "excluded": []},
        errors=WRITE_ERRORS,
    )
    def patch(self, request):
        data = s.ScopeInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        with service_errors():
            attendance_rules.set_company_scope(actor=request.user, company_id=request.company_id,
                                               scope=data.validated_data["scope"])
        return Response(s.RulesSerializer(self._out(request)).data)


class RecheckView(DeviceView):
    permission_classes = [PanelRule]
    panel_page = "devices:attendance_recheck"

    @endpoint(
        id="devices-recheck", area=AREA, title="Re-check punches",
        summary="Judge the punches that did not count, in a range, under today's rules.",
        what_it_does=["Judges them again; the days of any that now count are rebuilt.",
                      "Punches in a finalised salary month are left alone."],
        description="At most a year at a time.",
        roles=ROLES, scopes=["devices:write"], request=s.RecheckInputSerializer,
        response=s.RecheckSerializer,
        request_example={"start": "2026-09-01", "end": "2026-09-30"},
        response_example={"checked": 12, "now_count": 9, "still_excluded": 3,
                          "skipped_locked": 0},
        errors=WRITE_ERRORS,
    )
    def post(self, request):
        data = s.RecheckInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        form = RecheckPunchesForm(data={"start": data.validated_data["start"].isoformat(),
                                        "end": data.validated_data["end"].isoformat()})
        if not form.is_valid():
            refuse(form.errors)
        with service_errors():
            result = attendance_rules.recheck_punches(
                actor=request.user, company_id=request.company_id,
                start=form.cleaned_data["start"], end=form.cleaned_data["end"])
        return Response(s.RecheckSerializer({
            "checked": result.checked, "now_count": result.now_count,
            "still_excluded": sum(result.still_excluded.values()),
            "skipped_locked": result.skipped_locked}).data)
