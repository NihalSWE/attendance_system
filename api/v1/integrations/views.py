"""Integrations: the ERP webhook - its settings, tests, the events sent and the
guide for the ERP's developer - and the company's audit log
(docs/api/00-PLAN.md phase 12; the guide: docs/api/120-integrations.md).

The panel's Organisation -> ERP webhook page, through ``webhooks.services``:
the owner or company administrator only. Two things are stricter than the
page, because an API answer travels further than a screen: the secret keys
are never shown back (a new one is shown once, when it is made), and where
the attendance goes - the address and the keys - is changed only by a person,
never by an API key.
"""

import datetime

from django.http import HttpResponse
from django.utils.text import slugify
from rest_framework.response import Response

from api.core.docs import QUERY, Param, endpoint
from api.core.errors import ApiError
from api.core.forms import checked, refuse, service_errors
from api.core.pagination import StandardPagination
from api.core.permissions import PanelRule
from api.core.views import ApiView
from api.v1.integrations import serializers as s
from organization.services import require_structure_manager
from webhooks import services
from webhooks.models import WebhookEvent

AREA = "integrations"
ADMINS = ["Company owner or administrator"]
PEOPLE_ONLY = ["Company owner or administrator, logged in (not an API key)"]
ERRORS = ["not_authenticated", "invalid_token", "token_expired", "session_ended",
          "signature_required", "invalid_signature", "two_step_setup_required", "scope_missing",
          "permission_denied", "rate_limited", "server_error"]
WRITE_ERRORS = ERRORS + ["validation_error", "unknown_field"]
FIELDS = ("url", "is_active", "send_breaks", "ping_url", "employee_key", "mode", "batch",
          "send_from")


class WebhookView(ApiView):
    permission_classes = [PanelRule]
    company_required = True
    panel_page = "webhooks:settings"
    read_scope = write_scope = "webhook:manage"
    throttle_scope = "write"

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        require_structure_manager(request.user, request.company_id)


def _people_only(request):
    if getattr(request, "api_key", None) is not None:
        raise ApiError("permission_denied", "Where the attendance goes is changed by a person, "
                                            "logged in - not by an API key.")


def _webhook_out(company_id):
    row = services.settings_for(company_id)
    counts = services.counts(company_id)
    if row is None:
        return {"configured": False, "url": "", "is_active": False, "send_breaks": False,
                "ping_url": "", "employee_key": "au_user_id", "mode": "arrive_leave",
                "batch": True, "send_from": None, "has_secret": False,
                "has_signing_secret": False, "last_tested_at": None, "last_test_ok": None,
                "last_test_message": "", "last_sent_at": None, "counts": counts}
    return {"configured": True, **{name: getattr(row, name) for name in FIELDS},
            "has_secret": bool(row.secret_encrypted),
            "has_signing_secret": bool(row.signing_secret_encrypted),
            "last_tested_at": row.last_tested_at, "last_test_ok": row.last_test_ok,
            "last_test_message": row.last_test_message, "last_sent_at": row.last_sent_at,
            "counts": counts}


def _save(request, changes):
    """Save through the panel's form: what is stored, with ``changes`` over it."""
    row = services.settings_for(request.company_id)
    current = _webhook_out(request.company_id)
    values = {name: current[name] for name in FIELDS}
    values.update({k: v for k, v in changes.items() if k in FIELDS})
    data = {}
    for name, value in values.items():
        if isinstance(value, bool):
            if value:
                data[name] = "on"
        elif value is not None:
            data[name] = value.isoformat() if isinstance(value, datetime.date) else value
    for name in ("secret", "signing_secret"):
        if changes.get(name):
            data[name] = changes[name]
    if changes.get("clear_signing_secret"):
        data["clear_signing_secret"] = "on"
    has_signing = bool(row and row.signing_secret_encrypted)
    if changes.get("clear_signing_secret") and not has_signing:
        refuse({"clear_signing_secret": ["No signing secret is saved."]})
    form = checked(services.WebhookSettingsForm, data, instance=row,
                   has_secret=bool(row and row.secret_encrypted),
                   has_signing_secret=has_signing)
    with service_errors():
        services.save_settings(actor=request.user, company_id=request.company_id, form=form)


WEBHOOK_EXAMPLE = {"configured": True, "url": "https://erp.example.com/api/webhook/attendance",
                   "is_active": True, "send_breaks": False, "ping_url": "",
                   "employee_key": "au_user_id", "mode": "arrive_leave", "batch": True,
                   "send_from": "2026-10-01", "has_secret": True, "has_signing_secret": False,
                   "last_tested_at": "2026-10-05T10:00:00+06:00", "last_test_ok": True,
                   "last_test_message": "Connected.", "last_sent_at": None,
                   "counts": {"pending": 0, "sent": 120, "failed": 0, "skipped": 0,
                              "gave_up": 0}}


class WebhookSettingsView(WebhookView):
    permission_classes = [PanelRule]

    @endpoint(
        id="webhook", area=AREA, title="ERP webhook",
        summary="Where attendance is sent as it happens, whether it is on, and how it went.",
        what_it_does=["Answers the settings, the last test, and the events by status."],
        description="The secret keys are never shown: has_secret says one is saved.",
        roles=ADMINS, scopes=["webhook:manage"], response=s.WebhookSerializer,
        response_example=WEBHOOK_EXAMPLE, errors=ERRORS,
    )
    def get(self, request):
        return Response(s.WebhookSerializer(_webhook_out(request.company_id)).data)

    @endpoint(
        id="webhook-change", area=AREA, title="Change the ERP webhook",
        summary="The address, the secret key, on or off, and how events are sent.",
        what_it_does=["Saves the fields sent through the panel's own checks; recorded in the "
                      "audit log (without the keys)."],
        description=("The address must be https and not a private network. Switching it on "
                     "needs a secret key. A new address or key clears the last test: test "
                     "again. A person only - not an API key."),
        roles=PEOPLE_ONLY, scopes=["webhook:manage"], request=s.WebhookInputSerializer,
        response=s.WebhookSerializer,
        request_example={"url": "https://erp.example.com/api/webhook/attendance",
                         "secret": "the key the ERP developer gave you", "is_active": True},
        response_example=WEBHOOK_EXAMPLE, errors=WRITE_ERRORS,
    )
    def patch(self, request):
        _people_only(request)
        data = s.WebhookInputSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        _save(request, dict(data.validated_data))
        return Response(s.WebhookSerializer(_webhook_out(request.company_id)).data)


class WebhookSecretView(WebhookView):
    permission_classes = [PanelRule]

    @endpoint(
        id="webhook-secret", area=AREA, title="Make a new secret key",
        summary="A new random secret key, saved and shown this once.",
        what_it_does=["Makes and saves a new key; the old one stops working at once."],
        description="Give it to the ERP's developer (IGL ERP: ATTENDANCE_WEBHOOK_SECRET). It "
                    "is not shown again. A person only.",
        roles=PEOPLE_ONLY, scopes=["webhook:manage"], response=s.NewSecretSerializer,
        response_example={"secret": "wh_3kF9…"}, errors=WRITE_ERRORS,
    )
    def post(self, request):
        _people_only(request)
        if services.settings_for(request.company_id) is None:
            refuse({"url": ["Save the address first (PATCH /webhook)."]})
        secret = services.new_secret()
        _save(request, {"secret": secret})
        return Response(s.NewSecretSerializer({"secret": secret}).data)


class WebhookTestView(WebhookView):
    permission_classes = [PanelRule]

    @endpoint(
        id="webhook-test", area=AREA, title="Test the connection",
        summary="Call the ERP's test address with the real headers, and say what happened.",
        what_it_does=["Calls it, remembers the result, and explains a failure with what to "
                      "do about it."],
        description="Nothing is sent as attendance.",
        roles=ADMINS, scopes=["webhook:manage"], response=s.WebhookTestSerializer,
        response_example={"ok": True, "title": "Connected", "detail": "", "fix": "",
                          "url": "https://erp.example.com/api/webhook/attendance/ping"},
        errors=ERRORS,
    )
    def post(self, request):
        result = services.test_connection(actor=request.user, company_id=request.company_id)
        return Response(s.WebhookTestSerializer(result.as_dict()).data)


class WebhookTestEventView(WebhookView):
    permission_classes = [PanelRule]

    @endpoint(
        id="webhook-test-event", area=AREA, title="Send a test attendance",
        summary="A check-in (and check-out) typed in, sent now in the real format.",
        what_it_does=["Sends it, keeps it among the events, and says what the ERP did with "
                      "it."],
        description="To try the whole way to the ERP before a device is connected. The ERP "
                    "must know the same Employee ID.",
        roles=ADMINS, scopes=["webhook:manage"], request=s.TestEventInputSerializer,
        response=s.WebhookTestSerializer,
        request_example={"employee_code": "E-0041", "work_date": "2026-10-05",
                         "check_in": "09:02", "check_out": "18:05"},
        response_example={"ok": True, "title": "Received", "detail": "", "fix": "",
                          "url": "https://erp.example.com/api/webhook/attendance"},
        errors=WRITE_ERRORS,
    )
    def post(self, request):
        data = s.TestEventInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        form = checked(services.TestEventForm, {
            "test-employee_code": values["employee_code"],
            "test-work_date": values["work_date"].isoformat(),
            "test-check_in": values["check_in"],
            "test-check_out": values.get("check_out", "")}, prefix="test")
        cleaned = form.cleaned_data
        with service_errors():
            result = services.send_test_event(
                actor=request.user, company_id=request.company_id,
                employee_code=cleaned["employee_code"], work_date=cleaned["work_date"],
                check_in=cleaned["check_in"], check_out=cleaned["check_out"])
        return Response(s.WebhookTestSerializer(result.as_dict()).data)


def _event_out(event):
    return {"id": str(event.event_id),
            "employee": {"id": event.employee_id, "name": event.employee.full_name},
            "work_date": event.work_date, "kind": event.kind, "status": event.status,
            "attempts": event.attempts, "next_attempt_at": event.next_attempt_at,
            "last_attempt_at": event.last_attempt_at,
            "last_status_code": event.last_status_code, "last_message": event.last_message,
            "sent_at": event.sent_at, "created_at": event.created_at, "payload": event.payload}


class WebhookEventsView(WebhookView):
    permission_classes = [PanelRule]

    @endpoint(
        id="webhook-events", area=AREA, title="What was sent",
        summary="Every event: when, whose, what, and whether the ERP received it.",
        what_it_does=["Lists the events, newest first."],
        description="A failed event is tried again by itself, waiting longer each time.",
        roles=ADMINS, scopes=["webhook:manage"], paginated=True,
        params=[Param("status", QUERY, "string", "pending, sent, failed, skipped or gave_up.",
                      example="failed")],
        response=s.WebhookEventSerializer,
        response_example={"count": 1, "next": None, "previous": None, "results": [{
            "id": "0b6c…", "employee": {"id": 41, "name": "Rahim Uddin"},
            "work_date": "2026-10-05", "kind": "check_in", "status": "sent", "attempts": 1,
            "next_attempt_at": None, "last_attempt_at": "2026-10-05T09:03:00+06:00",
            "last_status_code": 200, "last_message": "Received", "sent_at":
                "2026-10-05T09:03:00+06:00", "created_at": "2026-10-05T09:02:10+06:00",
            "payload": {"au_user_id": "E-0041", "date": "2026-10-05",
                        "check_in": "2026-10-05 09:02:08"}}]},
        errors=ERRORS,
    )
    def get(self, request):
        rows = WebhookEvent.objects.select_related("employee").order_by("-created_at", "-pk")
        status = request.query_params.get("status", "")
        if status in WebhookEvent.Status.values:
            rows = rows.filter(status=status)
        paginator = StandardPagination()
        chunk = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(
            s.WebhookEventSerializer([_event_out(e) for e in chunk], many=True).data)


class WebhookSendNowView(WebhookView):
    permission_classes = [PanelRule]

    @endpoint(
        id="webhook-send-now", area=AREA, title="Send now",
        summary="Send what is waiting now, instead of on the next round.",
        what_it_does=["Sends the waiting events and says how many were received."],
        description="The webhook must be switched on.",
        roles=ADMINS, scopes=["webhook:manage"], response=s.WebhookSentSerializer,
        response_example={"count": 3, "detail": "Sent: 3 event(s) received."},
        errors=WRITE_ERRORS,
    )
    def post(self, request):
        if services.active_settings(request.company_id) is None:
            refuse({"is_active": ["Switch the webhook on first."]})
        received = services.deliver_due(request.company_id)
        return Response(s.WebhookSentSerializer({
            "count": received,
            "detail": f"Sent: {received} event(s) received." if received
            else "Nothing was received - see GET /webhook/events for why."}).data)


class WebhookSendAgainView(WebhookView):
    permission_classes = [PanelRule]

    @endpoint(
        id="webhook-send-again", area=AREA, title="Send again",
        summary="Put what failed or was given up back in the queue, to go now.",
        what_it_does=["Queues them again; recorded in the audit log."],
        description="After the ERP has been put right.",
        roles=ADMINS, scopes=["webhook:manage"], response=s.WebhookSentSerializer,
        response_example={"count": 4, "detail": "4 event(s) will be sent again now."},
        errors=ERRORS,
    )
    def post(self, request):
        again = services.send_again(actor=request.user, company_id=request.company_id)
        return Response(s.WebhookSentSerializer({
            "count": again, "detail": f"{again} event(s) will be sent again now." if again
            else "Nothing is waiting to be sent again."}).data)


class WebhookDebugView(WebhookView):
    permission_classes = [PanelRule]
    panel_page = {"GET": "webhooks:debug", "POST": "webhooks:settings"}

    @endpoint(
        id="webhook-debug", area=AREA, title="Debug messages",
        summary="Every call to the ERP and its answer, while debugging is on.",
        what_it_does=["Answers whether it is on, the time left, and the messages, newest "
                      "first."],
        description="Ask every few seconds while it is on. The secret keys are hidden.",
        roles=ADMINS, scopes=["webhook:manage"], response=s.DebugSerializer,
        response_example={"active": True, "seconds_left": 812, "entries": [
            {"id": 1, "at": "2026-10-05 10:01:00", "what": "Test connection", "ok": True,
             "message": "Connected.", "detail": None}]},
        errors=ERRORS,
    )
    def get(self, request):
        return Response(s.DebugSerializer(services.debug_state(request.company_id)).data)

    @endpoint(
        id="webhook-debug-switch", area=AREA, title="Switch debug messages on or off",
        summary="Keep every call and answer for 15 minutes - or stop now.",
        what_it_does=["Starts (clearing the old messages) or stops; recorded in the audit "
                      "log."],
        description="It switches itself off after 15 minutes.",
        roles=ADMINS, scopes=["webhook:manage"], request=s.DebugInputSerializer,
        response=s.DebugSerializer, request_example={"on": True},
        response_example={"active": True, "seconds_left": 900, "entries": []},
        errors=WRITE_ERRORS,
    )
    def post(self, request):
        data = s.DebugInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        with service_errors():
            if data.validated_data["on"]:
                services.start_debug(actor=request.user, company_id=request.company_id)
            else:
                services.stop_debug(actor=request.user, company_id=request.company_id)
        return Response(s.DebugSerializer(services.debug_state(request.company_id)).data)


class WebhookGuideView(WebhookView):
    permission_classes = [PanelRule]
    panel_page = "webhooks:guide"

    @endpoint(
        id="webhook-guide", area=AREA, title="The guide for the ERP's developer",
        summary="How to receive the attendance: the address, the headers, every event, "
                "examples - for this company's settings.",
        what_it_does=["Answers the guide as Markdown or PDF."],
        description="file_type md (default) or pdf. The secret key itself is not in it.",
        roles=ADMINS, scopes=["webhook:manage"],
        params=[Param("file_type", QUERY, "string", "md (default) or pdf.", example="pdf")],
        errors=ERRORS + ["validation_error"],
    )
    def get(self, request):
        from tenants.models import Company
        from webhooks import guide

        fmt = request.query_params.get("file_type") or "md"
        if fmt not in ("md", "pdf"):
            refuse({"file_type": ["md or pdf."]})
        company = Company.objects.get(pk=request.company_id)
        row = services.settings_for(request.company_id)
        name = f"attendance-webhook-guide-{slugify(company.code or company.name)}"
        if fmt == "pdf":
            response = HttpResponse(guide.pdf(row, company), content_type="application/pdf")
        else:
            response = HttpResponse(guide.markdown(row, company),
                                    content_type="text/markdown; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{name}.{fmt}"'
        return response


# --- the audit log ------------------------------------------------------------------------

class AuditLogView(ApiView):
    permission_classes = [PanelRule]
    company_required = True
    panel_page = "dashboard"
    read_scope, write_scope = None, None
    throttle_scope = "write"

    @endpoint(
        id="audit-log", area=AREA, title="Audit log",
        summary="Who did what, when, and what changed - everything recorded in this company.",
        what_it_does=["Lists the company's audit records, newest first. Read only: a record "
                      "can never be changed or removed."],
        description=("For the owner or company administrator, logged in. Filter by action "
                     "(e.g. leave.approved, or leave. for all leave), by who (actor email), "
                     "by thing (object_type, object_id) or by dates."),
        roles=PEOPLE_ONLY, paginated=True,
        params=[Param("action", QUERY, "string", "An action, or its start (ending in a dot).",
                      example="leave."),
                Param("actor", QUERY, "string", "Who: their email.", example="hr@example.com"),
                Param("object_type", QUERY, "string", "e.g. leaves.leaverequest.",
                      example="leaves.leaverequest"),
                Param("object_id", QUERY, "string", "Its id.", example="31"),
                Param("from", QUERY, "string (date)", "From this day.", example="2026-10-01"),
                Param("to", QUERY, "string (date)", "To this day.", example="2026-10-31")],
        response=s.AuditEntrySerializer,
        response_example={"count": 1, "next": None, "previous": None, "results": [{
            "id": 9921, "at": "2026-10-05T11:00:00+06:00", "actor": "hr@example.com",
            "actor_type": "user", "action": "leave.approved",
            "object_type": "leaves.leaverequest", "object_id": "31",
            "object": "Rahim Uddin - Casual leave", "before": {"status": "pending"},
            "after": {"status": "approved"}, "ip_address": "203.0.113.7"}]},
        errors=ERRORS + ["validation_error"],
    )
    def get(self, request):
        from auditlog.models import AuditLog

        require_structure_manager(request.user, request.company_id)
        rows = AuditLog.objects.filter(company_id=request.company_id).select_related(
            "actor_user").order_by("-occurred_at", "-pk")
        q = request.query_params
        action = q.get("action", "").strip()
        if action:
            rows = rows.filter(action__startswith=action) if action.endswith(".") \
                else rows.filter(action=action)
        if q.get("actor"):
            rows = rows.filter(actor_user__email__iexact=q["actor"].strip())
        if q.get("object_type"):
            app, _, model = q["object_type"].partition(".")
            rows = rows.filter(object_app=app, object_model=model)
        if q.get("object_id"):
            rows = rows.filter(object_id=q["object_id"].strip())
        for name, lookup in (("from", "occurred_at__date__gte"), ("to", "occurred_at__date__lte")):
            if q.get(name):
                try:
                    rows = rows.filter(**{lookup: datetime.date.fromisoformat(q[name])})
                except ValueError:
                    refuse({name: ["Use YYYY-MM-DD."]})
        paginator = StandardPagination()
        chunk = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(s.AuditEntrySerializer([{
            "id": entry.pk, "at": entry.occurred_at,
            "actor": entry.actor_user.email if entry.actor_user_id else None,
            "actor_type": entry.actor_type, "action": entry.action,
            "object_type": f"{entry.object_app}.{entry.object_model}",
            "object_id": str(entry.object_id or entry.object_public_id or ""),
            "object": entry.object_display or "", "before": entry.before_data,
            "after": entry.after_data,
            "ip_address": str(entry.ip_address) if entry.ip_address else None,
        } for entry in chunk], many=True).data)

