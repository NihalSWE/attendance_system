"""Devices, part 2a: what the devices sent - messages, punches, the users on a
device, saved fingerprints and faces, its settings, and the command queue
(docs/api/00-PLAN.md phase 6; the guide: docs/api/60-devices-data-flow.md).

Read-only. Messages and punches are append-only evidence: nothing here
changes them. Behind the panel's device rule, like every device endpoint.
"""

from django.db.models import Q
from rest_framework.response import Response

from api.core.docs import PATH, QUERY, Param, endpoint
from api.core.errors import ApiError
from api.core.permissions import PanelRule
from api.v1.devices import serializers as s
from api.v1.devices.views import DEVICE_PARAM, READ_ERRORS, ROLES, DeviceView, _device
from devices.models import DeviceMessage, PunchEvent
from devices.services import mapping as mapping_service
from devices.services import templates
from devices.services.commands import (
    WRITABLE_OPTIONS,
    job_progress,
    pending_summary,
    recent_results,
    waiting_count,
)
from devices.services.device_roster import build_roster

AREA = "device_flow"
UNRESOLVED = (PunchEvent.AuthorizationStatus.UNKNOWN_EMPLOYEE,
              PunchEvent.AuthorizationStatus.EXPIRED_ENROLLMENT,
              PunchEvent.AuthorizationStatus.POLICY_UNRESOLVED)
DEVICE_REF = {"id": "6f1c9a2b-7e8d-4c3b-9a1f-2d4e5b6c7d8e", "name": "Main gate"}
MESSAGE_EXAMPLE = {"id": "1b2c3d4e-5f60-4718-293a-4b5c6d7e8f90", "device": DEVICE_REF,
                   "message_type": "punch_batch", "received_at": "2026-10-05T09:02:11+06:00",
                   "record_count": 1, "processing_status": "parsed", "processing_error": ""}
PUNCH_EXAMPLE = {"id": 5012, "device": DEVICE_REF, "device_user_id": "41",
                 "employee": {"id": 41, "name": "Rahim Uddin"},
                 "punched_at": "2026-10-05T09:02:08+06:00",
                 "punched_at_utc": "2026-10-05T03:02:08Z",
                 "received_at": "2026-10-05T09:02:11+06:00", "verification_method": "face",
                 "authorization_status": "authorized", "dedupe_status": "unique",
                 "processing_status": "allocated"}


def _device_ref(device):
    return {"id": str(device.public_id), "name": device.name}


def _device_filter(request, rows, field="device"):
    raw = request.query_params.get("device_id")
    if raw:
        rows = rows.filter(**{f"{field}__public_id": _device(raw).public_id})
    return rows


def _message_out(message):
    return {"id": str(message.public_id), "device": _device_ref(message.device),
            "message_type": message.message_type, "received_at": message.received_at,
            "record_count": message.record_count,
            "processing_status": message.processing_status,
            "processing_error": message.processing_error}


def _punch_out(punch):
    return {"id": punch.pk, "device": _device_ref(punch.device),
            "device_user_id": punch.device_user_id,
            "employee": {"id": punch.employee_id, "name": punch.employee.full_name}
            if punch.employee_id else None,
            "punched_at": punch.punched_at_device, "punched_at_utc": punch.punched_at_utc,
            "received_at": punch.received_at, "verification_method": punch.verification_method,
            "authorization_status": punch.authorization_status,
            "dedupe_status": punch.dedupe_status, "processing_status": punch.processing_status}


class FlowView(DeviceView):
    permission_classes = [PanelRule]
    panel_page = "devices:device_list"

    # A queryset paged first, then turned into rows (messages and punches are many).
    def paginator_page(self, request, rows):
        from api.core.pagination import StandardPagination

        self._paginator = StandardPagination()
        return self._paginator.paginate_queryset(rows, request, view=self)

    def page_response(self, rows, serializer):
        return self._paginator.get_paginated_response(serializer(rows, many=True).data)


# --- messages ---------------------------------------------------------------------------

class MessageListView(FlowView):
    permission_classes = [PanelRule]
    panel_page = "devices:message_list"

    @endpoint(
        id="device-messages", area=AREA, title="Device messages",
        summary="Everything the devices sent, newest first.",
        what_it_does=["Lists the messages: scans, heartbeats, user lists, command answers."],
        description=("Every call a device makes is kept as it arrived. A failed message is "
                     "where to look when scans seem missing. Filter by device_id, status or "
                     "type."),
        roles=ROLES, scopes=["devices:read"], paginated=True,
        params=[Param("device_id", QUERY, "string (uuid)", "Only this device.",
                      example=DEVICE_REF["id"]),
                Param("status", QUERY, "string", "received, parsing, parsed, partially_failed "
                                                 "or failed.", example="failed"),
                Param("type", QUERY, "string", "punch_batch, heartbeat, enrollment_result, "
                                               "command_result, device_info or unknown.",
                      example="punch_batch")],
        response=s.DeviceMessageSerializer,
        response_example={"count": 1, "next": None, "previous": None,
                          "results": [MESSAGE_EXAMPLE]},
        errors=READ_ERRORS + ["not_found"],
    )
    def get(self, request):
        rows = _device_filter(request, DeviceMessage.objects.select_related("device"))
        if request.query_params.get("status") in dict(DeviceMessage.ProcessingStatus.choices):
            rows = rows.filter(processing_status=request.query_params["status"])
        if request.query_params.get("type") in dict(DeviceMessage.MessageType.choices):
            rows = rows.filter(message_type=request.query_params["type"])
        page = self.paginator_page(request, rows.order_by("-received_at"))
        return self.page_response([_message_out(m) for m in page], s.DeviceMessageSerializer)


class MessageDetailView(FlowView):
    permission_classes = [PanelRule]
    panel_page = "devices:message_detail"

    @endpoint(
        id="device-messages-get", area=AREA, title="One device message",
        summary="One message as it arrived, and the punches it became.",
        what_it_does=["Answers the message, with its raw text for a person."],
        description="The raw text can hold fingerprint and face templates, so an API key gets "
                    "null there.",
        roles=ROLES, scopes=["devices:read"],
        params=[Param("message_id", PATH, "string (uuid)", "The message id.", required=True,
                      example=MESSAGE_EXAMPLE["id"])],
        response=s.DeviceMessageDetailSerializer,
        response_example={**MESSAGE_EXAMPLE, "content_type": "text/plain",
                          "source_ip": "103.110.25.4",
                          "raw_payload_text": "41\t2026-10-05 09:02:08\t0\t15\t\t0\t0",
                          "punch_ids": [5012]},
        errors=READ_ERRORS + ["not_found"],
    )
    def get(self, request, message_id):
        message = DeviceMessage.objects.select_related("device").filter(
            public_id=message_id).first()
        if message is None:
            raise ApiError("not_found", "No such message in this company.")
        person = getattr(request, "api_key", None) is None
        return Response(s.DeviceMessageDetailSerializer({
            **_message_out(message), "content_type": message.content_type,
            "source_ip": message.source_ip,
            "raw_payload_text": message.raw_payload_text if person else None,
            "punch_ids": list(PunchEvent.objects.filter(device_message=message)
                              .order_by("source_record_index").values_list("pk", flat=True)),
        }).data)


# --- punches ---------------------------------------------------------------------------

class PunchListView(FlowView):
    permission_classes = [PanelRule]
    panel_page = "devices:punch_list"

    @endpoint(
        id="punches", area=AREA, title="Punches",
        summary="Every scan, newest arrival first, and whether it counts.",
        what_it_does=["Lists the punches with whose they are and why they count or not."],
        description=("A punch that does not count says why in authorization_status. Filter by "
                     "device_id, authorization, dedupe, or search q by user number or name."),
        roles=ROLES, scopes=["devices:read"], paginated=True,
        params=[Param("device_id", QUERY, "string (uuid)", "Only this device.",
                      example=DEVICE_REF["id"]),
                Param("authorization", QUERY, "string", "e.g. authorized or unknown_employee.",
                      example="unknown_employee"),
                Param("dedupe", QUERY, "string", "unique, probable_duplicate or "
                                                 "confirmed_duplicate.", example="unique"),
                Param("q", QUERY, "string", "Search by user number or name.", example="41")],
        response=s.PunchSerializer,
        response_example={"count": 1, "next": None, "previous": None, "results": [PUNCH_EXAMPLE]},
        errors=READ_ERRORS + ["not_found"],
    )
    def get(self, request):
        rows = _device_filter(request, PunchEvent.objects.select_related("device", "employee"))
        if request.query_params.get("authorization") in dict(
                PunchEvent.AuthorizationStatus.choices):
            rows = rows.filter(authorization_status=request.query_params["authorization"])
        if request.query_params.get("dedupe") in dict(PunchEvent.DedupeStatus.choices):
            rows = rows.filter(dedupe_status=request.query_params["dedupe"])
        query = (request.query_params.get("q") or "").strip()[:100]
        if query:
            rows = rows.filter(Q(device_user_id__icontains=query)
                               | Q(employee__first_name__icontains=query)
                               | Q(employee__last_name__icontains=query))
        page = self.paginator_page(request, rows.order_by("-received_at", "-id"))
        return self.page_response([_punch_out(p) for p in page], s.PunchSerializer)


class UnresolvedView(FlowView):
    permission_classes = [PanelRule]
    panel_page = "devices:unresolved_queue"

    @endpoint(
        id="punches-unresolved", area=AREA, title="Punches to sort out",
        summary="Punches a person must act on before they can count.",
        what_it_does=["Lists the unknown user numbers, scans outside an enrollment, undecided "
                      "rules and probable duplicates."],
        description="Usually fixed by linking the user number to an employee, then re-checking "
                    "the punches. GET …/unresolved/counts gives the totals by reason.",
        roles=ROLES, scopes=["devices:read"], paginated=True,
        params=[Param("reason", QUERY, "string", "unknown_employee, expired_enrollment, "
                                                 "policy_unresolved or probable_duplicate.",
                      example="unknown_employee")],
        response=s.PunchSerializer,
        response_example={"count": 1, "next": None, "previous": None, "results": [
            {**PUNCH_EXAMPLE, "employee": None, "authorization_status": "unknown_employee",
             "processing_status": "needs_review"}]},
        errors=READ_ERRORS,
    )
    def get(self, request):
        rows = PunchEvent.objects.select_related("device", "employee").filter(
            Q(authorization_status__in=UNRESOLVED)
            | Q(dedupe_status=PunchEvent.DedupeStatus.PROBABLE_DUPLICATE))
        reason = request.query_params.get("reason", "")
        if reason == "probable_duplicate":
            rows = rows.filter(dedupe_status=PunchEvent.DedupeStatus.PROBABLE_DUPLICATE)
        elif reason in UNRESOLVED:
            rows = rows.filter(authorization_status=reason)
        page = self.paginator_page(request, rows.order_by("-punched_at_utc"))
        return self.page_response([_punch_out(p) for p in page], s.PunchSerializer)


class UnresolvedCountsView(FlowView):
    permission_classes = [PanelRule]
    panel_page = "devices:unresolved_queue"

    @endpoint(
        id="punches-unresolved-counts", area=AREA, title="Punches to sort out: totals",
        summary="How many punches wait for a person, by reason.",
        what_it_does=["Counts them."],
        description="For a badge or a dashboard.",
        roles=ROLES, scopes=["devices:read"], response=s.UnresolvedCountsSerializer,
        response_example={"unknown_employee": 3, "expired_enrollment": 0,
                          "policy_unresolved": 0, "probable_duplicate": 1},
        errors=READ_ERRORS,
    )
    def get(self, request):
        auth = PunchEvent.AuthorizationStatus
        return Response(s.UnresolvedCountsSerializer({
            "unknown_employee": PunchEvent.objects.filter(
                authorization_status=auth.UNKNOWN_EMPLOYEE).count(),
            "expired_enrollment": PunchEvent.objects.filter(
                authorization_status=auth.EXPIRED_ENROLLMENT).count(),
            "policy_unresolved": PunchEvent.objects.filter(
                authorization_status=auth.POLICY_UNRESOLVED).count(),
            "probable_duplicate": PunchEvent.objects.filter(
                dedupe_status=PunchEvent.DedupeStatus.PROBABLE_DUPLICATE).count(),
        }).data)


class PunchDetailView(FlowView):
    permission_classes = [PanelRule]
    panel_page = "devices:punch_detail"

    @endpoint(
        id="punches-get", area=AREA, title="One punch",
        summary="One scan: the record the device sent and the facts it was judged on.",
        what_it_does=["Answers the punch with its raw record and its frozen judgement."],
        description="authorization_snapshot is never rewritten: it is why the punch was "
                    "decided as it was, at the time.",
        roles=ROLES, scopes=["devices:read"],
        params=[Param("punch_id", PATH, "integer", "The punch id.", required=True, example=5012)],
        response=s.PunchDetailSerializer,
        response_example={**PUNCH_EXAMPLE, "message_id": MESSAGE_EXAMPLE["id"],
                          "duplicate_of": None, "raw_record": {"pin": "41"},
                          "authorization_snapshot": {"scope": "assigned_devices"},
                          "processing_error": ""},
        errors=READ_ERRORS + ["not_found"],
    )
    def get(self, request, punch_id):
        punch = (PunchEvent.objects.select_related("device", "employee", "device_message")
                 .filter(pk=punch_id).first())
        if punch is None:
            raise ApiError("not_found", "No such punch in this company.")
        return Response(s.PunchDetailSerializer({
            **_punch_out(punch),
            "message_id": str(punch.device_message.public_id) if punch.device_message_id else None,
            "duplicate_of": punch.duplicate_of_id, "raw_record": punch.raw_record or {},
            "authorization_snapshot": punch.authorization_snapshot or {},
            "processing_error": punch.processing_error}).data)


# --- one device's side ----------------------------------------------------------------------

USER_EXAMPLE = {"pin": "41", "name": "RAHIM", "privilege": "Normal user", "card_number": "",
                "has_password": False, "fingerprint_count": 2, "face_count": 1,
                "saved_fingerprints": 2, "saved_faces": 1, "only_in_scans": False,
                "removed_from_device": False, "employee": {"id": 41, "name": "Rahim Uddin"},
                "attendance_enabled": True, "assigned_device_authorized": True,
                "unlinked": None}


class DeviceUsersView(FlowView):
    permission_classes = [PanelRule]
    panel_page = "devices:device_users"

    @endpoint(
        id="device-users", area=AREA, title="Users on a device",
        summary="Who the device recognises, and whether their punches count here.",
        what_it_does=["Lists every user number the device reported (or scanned with), joined "
                      "to the employee it is linked to.",
                      "For someone not linked, says why and what to do."],
        description=("The list is what the device last reported - ask it to send it again with "
                     "POST …/users/refresh. Filter mapping by linked, not_linked or "
                     "not_linked:<reason>."),
        roles=ROLES, scopes=["devices:read"], paginated=True,
        params=[DEVICE_PARAM,
                Param("mapping", QUERY, "string", "linked, not_linked, or not_linked:<reason> "
                                                  "(ready, other_number, other_branch, "
                                                  "no_employee, not_digits).", example="not_linked"),
                Param("q", QUERY, "string", "Search by user number or name.", example="rahim")],
        response=s.DeviceUserSerializer,
        response_example={"count": 1, "next": None, "previous": None, "results": [USER_EXAMPLE]},
        errors=READ_ERRORS + ["not_found"],
    )
    def get(self, request, device_id):
        device = _device(device_id)
        roster = build_roster(device)
        saved = templates.saved_counts(device)
        mapping_service.why_not_linked(device, roster)
        mapping = request.query_params.get("mapping", "")
        query = (request.query_params.get("q") or "").strip().lower()
        out = []
        for row in roster:
            if mapping == "linked" and not row["is_mapped"]:
                continue
            if mapping == "not_linked" and row["is_mapped"]:
                continue
            if mapping.startswith("not_linked:") and (row.get("unlinked") or {}).get(
                    "code") != mapping.split(":", 1)[1]:
                continue
            employee = row["employee"]
            if query and query not in row["pin"].lower() and query not in row["name"].lower() \
                    and not (employee and query in employee.full_name.lower()):
                continue
            counts = saved.get(row["pin"], {})
            unlinked = row.get("unlinked")
            out.append({
                "pin": row["pin"], "name": row["name"], "privilege": row["privilege_label"],
                "card_number": row["card_number"], "has_password": row["has_password"],
                "fingerprint_count": row["fingerprint_count"], "face_count": row["face_count"],
                "saved_fingerprints": counts.get("fingerprint", 0),
                "saved_faces": counts.get("face", 0), "only_in_scans": row["only_in_scans"],
                "removed_from_device": row["removed_from_device"],
                "employee": {"id": employee.pk, "name": employee.full_name} if employee else None,
                "attendance_enabled": row["attendance_enabled"],
                "assigned_device_authorized": row["assigned_device_authorized"],
                "unlinked": {"code": unlinked["code"], "text": unlinked["text"]}
                if unlinked else None,
            })
        return self.paginated(request, out, s.DeviceUserSerializer)


class TemplatesView(FlowView):
    permission_classes = [PanelRule]
    panel_page = "devices:device_users"

    @endpoint(
        id="device-templates", area=AREA, title="Saved fingerprints and faces",
        summary="What the server keeps of this device's fingerprints and faces.",
        what_it_does=["Answers how many are saved, and in which formats."],
        description=("Templates are kept encrypted, so a person can be put back on a device or "
                     "copied to another of the same model. Never shown themselves. Save what "
                     "the device sent with POST …/templates/save."),
        roles=ROLES, scopes=["devices:read"], params=[DEVICE_PARAM],
        response=s.TemplatesSerializer,
        response_example={"can_save": True, "problem": "", "people": 120, "fingerprints": 230,
                          "faces": 118, "other": 0, "last_saved": "2026-10-04T18:00:00+06:00",
                          "formats": [{"kind": "face", "type": "9", "version": "58.0",
                                       "format": "zk", "count": 118}]},
        errors=READ_ERRORS + ["not_found"],
    )
    def get(self, request, device_id):
        device = _device(device_id)
        problem = templates.key_problem()
        return Response(s.TemplatesSerializer({**templates.saved_summary(device),
                                               "can_save": not problem,
                                               "problem": problem}).data)


class OptionsView(FlowView):
    permission_classes = [PanelRule]
    panel_page = "devices:device_list"

    @endpoint(
        id="device-options", area=AREA, title="A device's settings",
        summary="What it reported about itself, and the settings that can be changed.",
        what_it_does=["Answers the changeable settings with their current values, and "
                      "everything the device last reported."],
        description="Ask it to report again with POST …/commands (query_options).",
        roles=ROLES, scopes=["devices:read"], params=[DEVICE_PARAM],
        response=s.OptionsSerializer,
        response_example={"writable": [{"key": "push_interval_seconds",
                                        "help": "How often the device contacts the server, in "
                                                "seconds.", "current": "10"}],
                          "reported": {"FirmVer": "ZAM70-NF24HA-Ver3.0.15"}},
        errors=READ_ERRORS + ["not_found"],
    )
    def get(self, request, device_id):
        device = _device(device_id)
        settings = device.settings or {}
        return Response(s.OptionsSerializer({
            "writable": [{"key": key, "help": spec[2], "current": str(settings.get(key, ""))}
                         for key, spec in WRITABLE_OPTIONS.items()],
            "reported": {k: v for k, v in settings.items() if k not in WRITABLE_OPTIONS},
        }).data)


class CommandsView(FlowView):
    permission_classes = [PanelRule]
    panel_page = "devices:device_list"

    @endpoint(
        id="device-commands", area=AREA, title="A device's command queue",
        summary="What waits for the device, and its last answers.",
        what_it_does=["Answers the commands waiting for it to collect, and the latest answers."],
        description=("A device cannot be called: it collects its commands when it next calls "
                     "in (about twice a second while busy). Return code 0 is done."),
        roles=ROLES, scopes=["devices:read"], params=[DEVICE_PARAM],
        response=s.CommandsSerializer,
        response_example={"waiting": 1, "pending": [{"id": 812, "key": "query_users",
                                                     "body": "DATA QUERY USERINFO"}],
                          "recent_results": [{"id": 811, "key": "query_options",
                                              "command": "INFO", "returned": "0", "ok": True,
                                              "at": "2026-10-05T10:15:00+06:00"}]},
        errors=READ_ERRORS + ["not_found"],
    )
    def get(self, request, device_id):
        device = _device(device_id)
        return Response(s.CommandsSerializer({
            "waiting": waiting_count(device),
            "pending": [{"id": row["id"], "key": row.get("key") or "", "body": row["body"]}
                        for row in pending_summary(device)],
            "recent_results": [{"id": row["id"], "key": row.get("key") or "",
                                "command": row["command"], "returned": row.get("return"),
                                "ok": row["ok"], "at": row["at"]}
                               for row in recent_results(device)],
        }).data)


class JobsView(FlowView):
    permission_classes = [PanelRule]
    panel_page = "devices:device_users"

    @endpoint(
        id="device-jobs", area=AREA, title="A device's work in progress",
        summary="How a run of commands, or loading employees onto it, is going.",
        what_it_does=["Answers the progress of its commands (the last hour) and of a load."],
        description="Ask it every few seconds while running is true.",
        roles=ROLES, scopes=["devices:read"], params=[DEVICE_PARAM],
        response=s.JobsSerializer,
        response_example={"commands": {"running": True, "waiting": 40, "sent": 10, "done": 50,
                                       "refused": 0, "total": 100, "percent": 50,
                                       "seconds_left": 10, "refused_users": []},
                          "load": None},
        errors=READ_ERRORS + ["not_found"],
    )
    def get(self, request, device_id):
        from devices.services import load_jobs

        device = _device(device_id)
        load_jobs.resume_if_stalled(device)
        return Response(s.JobsSerializer({"commands": job_progress(device),
                                          "load": load_jobs.progress(device)}).data)

