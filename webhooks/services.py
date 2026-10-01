"""The ERP webhook: what is sent, when, and how (see ``webhooks.models``).

When: attendance days are worked out in one place
(``attendance.services.recalculate``), and it hands every day it wrote to
``note_days``. A day is queued once for its check-in and once more when it
has its check-out - "finished" by default (the shift's window has closed), or
after every scan in the "every scan" mode. A check-out the system only
assumed (the shift's end, nobody scanned out) is never sent: the day waits
until a real one is added. A later change to what was sent goes as "update".

How: the queue (``WebhookEvent``) is sent straight after the scan is saved, in
the background, and anything not received is tried again - 1 minute, then 2,
5, 15, 30 minutes, 1, 3, 6 and 12 hours later - each time a device checks in
(at most once a minute per company) and by ``manage.py send_webhooks``. After
that it is given up and the page offers to send it again.

Safety: only ``https://`` addresses, never one that resolves to a private or
local network, no redirects followed, 10 seconds at most per request. The
secrets are encrypted and never shown again; every change is audited.
"""

import base64
import datetime
import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import ssl
import threading
import uuid
import zoneinfo
from dataclasses import dataclass
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import urlsplit

from django import forms
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.utils import timezone

from auditlog.services import record_company_event
from common.forms import StyledFormMixin, date_widget
from common.tenant import use_company
from organization.services import require_structure_manager
from webhooks.models import WebhookDayState, WebhookEvent, WebhookSettings

logger = logging.getLogger(__name__)

TIMEOUT = 10
#: The receiver's limit in the IGL ERP; more go in the next request.
MAX_EVENTS = 500
#: Minutes to wait before each retry; after the last, given up.
BACKOFF_MINUTES = (1, 2, 5, 15, 30, 60, 180, 360, 720)
USER_AGENT = "AttendanceSystem-Webhook/1.0"
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
Status = WebhookEvent.Status


class WebhookError(ValidationError):
    """The receiver cannot be used or did not answer; the message says why."""


# --- the secrets, encrypted -------------------------------------------------


def _cipher():
    from cryptography.fernet import Fernet

    digest = hashlib.sha256(b"company-webhook-secret:" + settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(secret):
    return _cipher().encrypt(secret.encode()).decode("ascii") if secret else ""


def decrypt(token):
    from cryptography.fernet import InvalidToken

    if not token:
        return ""
    try:
        return _cipher().decrypt(token.encode("ascii")).decode()
    except InvalidToken as exc:
        raise WebhookError("The saved secret can no longer be read (the server's secret key "
                           "changed). Enter it again under Organisation → ERP webhook.") from exc


def new_secret():
    """A secret key for the company to give its system: 64 random hex characters."""
    import secrets

    return secrets.token_hex(32)


# --- reading ----------------------------------------------------------------


def settings_for(company_id):
    return WebhookSettings.all_objects.filter(company_id=company_id).first()


def active_settings(company_id):
    found = settings_for(company_id)
    return found if found is not None and found.is_active else None


def company_zone(company):
    try:
        return zoneinfo.ZoneInfo(company.timezone or "UTC")
    except zoneinfo.ZoneInfoNotFoundError:
        return zoneinfo.ZoneInfo("UTC")


def ping_url(row):
    return row.ping_url or row.url.rstrip("/") + "/ping"


def counts(company_id):
    found = dict.fromkeys(Status.values, 0)
    for status in (WebhookEvent.all_objects.filter(company_id=company_id)
                   .values_list("status", flat=True)):
        found[status] += 1
    return found


# --- the form and saving ------------------------------------------------------


#: The form's fields most companies never touch, behind "Advanced" (Nihal,
#: 2026-10-01: only what is needed on the form). Their defaults suit the IGL ERP.
ADVANCED = ("signing_secret", "clear_signing_secret", "employee_key", "mode", "batch",
            "send_from", "ping_url")


class WebhookSettingsForm(StyledFormMixin, forms.ModelForm):
    secret = forms.CharField(
        label="Secret key", required=False, strip=True,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
    )
    signing_secret = forms.CharField(
        label="Signing secret", required=False, strip=True,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
    )
    clear_signing_secret = forms.BooleanField(
        label="Stop signing requests", required=False,
        help_text="Removes the saved signing secret.")

    class Meta:
        model = WebhookSettings
        fields = ("url", "is_active", "ping_url", "employee_key", "mode", "batch", "send_from")
        labels = {
            "url": "Your system's webhook address",
            "is_active": "Send attendance to this address",
            "ping_url": "Test address",
            "employee_key": "Employee field name",
            "mode": "When to send",
            "batch": "Send several in one request",
            "send_from": "Send days from",
        }
        help_texts = {
            "url": "Your developer gives you this. It starts with https://, for example "
                   "https://erp.example.com/api/webhook/attendance",
            "is_active": "Untick to stop sending. Nothing is lost: what is waiting goes when "
                         "you tick it again.",
            "ping_url": "Only if your developer gives you one. Empty: the address above "
                        "followed by /ping.",
            "employee_key": "Only if your developer asks. The IGL ERP uses au_user_id.",
            "mode": "Keep the first unless your developer asks for every scan.",
            "batch": "Sends up to 500 at a time. Untick only if your developer asks.",
            "send_from": "Earlier days are never sent. Empty: from the day you switch it on.",
        }
        widgets = {"send_from": date_widget("From the day it is switched on")}

    def __init__(self, *args, has_secret=False, has_signing_secret=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.has_secret = has_secret
        self.fields["secret"].help_text = (
            "Saved and hidden. Leave empty to keep it, or type a new one to replace it."
            if has_secret else
            "Your developer gives you this too. Your system uses it to know the attendance "
            "really comes from us. (IGL ERP: ATTENDANCE_WEBHOOK_SECRET.)")
        self.fields["signing_secret"].help_text = (
            "Saved and hidden. Leave empty to keep it."
            if has_signing_secret else
            "Only if your developer gives you one. (IGL ERP: ATTENDANCE_WEBHOOK_SIGNING_SECRET.)")
        if not has_signing_secret:
            del self.fields["clear_signing_secret"]

    def main_fields(self):
        return [self[name] for name in self.fields if name not in ADVANCED + ("is_active",)]

    def advanced_fields(self):
        return [self[name] for name in ADVANCED
                if name in self.fields and name not in ("batch", "clear_signing_secret")]

    def advanced_has_errors(self):
        return any(self[name].errors for name in ADVANCED if name in self.fields)

    def clean_url(self):
        return check_address(self.cleaned_data["url"], check_network=False)

    def clean_ping_url(self):
        value = self.cleaned_data.get("ping_url") or ""
        return check_address(value, check_network=False) if value else ""

    def clean_employee_key(self):
        value = (self.cleaned_data.get("employee_key") or "").strip()
        if not value.replace("_", "").isalnum():
            raise ValidationError("Letters, digits and _ only, e.g. au_user_id.")
        return value

    def clean(self):
        data = super().clean()
        if data.get("is_active") and not (data.get("secret") or self.has_secret):
            self.add_error("secret", "Enter the secret key before switching it on.")
        return data


@transaction.atomic
def save_settings(*, actor, company_id, form):
    """Save the company's webhook. Owner and company admin only."""
    membership = require_structure_manager(actor, company_id)
    company = membership.company
    with use_company(company_id):
        existing = settings_for(company_id)
        before = _snapshot(existing)
        row = form.save(commit=False)
        row.company = company
        secret = form.cleaned_data.get("secret") or ""
        signing = form.cleaned_data.get("signing_secret") or ""
        row.secret_encrypted = encrypt(secret) if secret else (
            existing.secret_encrypted if existing else "")
        if form.cleaned_data.get("clear_signing_secret"):
            row.signing_secret_encrypted = ""
        else:
            row.signing_secret_encrypted = encrypt(signing) if signing else (
                existing.signing_secret_encrypted if existing else "")
        if row.is_active and row.send_from is None:
            row.send_from = timezone.now().astimezone(company_zone(company)).date()
        if existing is not None and (secret or row.url != existing.url
                                     or row.ping_url != existing.ping_url):
            row.last_tested_at, row.last_test_ok, row.last_test_message = None, None, ""
        if row.pk is None:
            row.created_by = actor
        row.updated_by = actor
        row.full_clean()
        row.save()
        record_company_event(
            actor=actor, membership=membership, company=company,
            action="webhook_settings.saved", obj=row, before=before,
            after={**_snapshot(row), "secret_changed": bool(secret),
                   "signing_secret_changed": bool(signing)
                   or bool(form.cleaned_data.get("clear_signing_secret"))},
        )
    return row


def _snapshot(row):
    if row is None:
        return {}
    return {"url": row.url, "ping_url": row.ping_url, "auth": row.auth,
            "employee_key": row.employee_key, "mode": row.mode, "batch": row.batch,
            "send_from": row.send_from.isoformat() if row.send_from else "",
            "is_active": row.is_active, "signed": bool(row.signing_secret_encrypted)}


# --- the address ----------------------------------------------------------------


def check_address(url, *, check_network=True):
    """An https address on the public internet, or WebhookError saying why
    (its ``code`` names the problem for ``diagnose``)."""
    url = (url or "").strip()
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise WebhookError("Use an https:// address. A plain http:// one can be redirected, "
                           "and the attendance would be lost on the way.", code="https")
    if parts.username or parts.password:
        raise WebhookError("Put the secret in the Secret key box, not in the address.",
                           code="https")
    if check_network:
        port = parts.port or 443
        try:
            found = {info[4][0] for info in socket.getaddrinfo(parts.hostname, port,
                                                                type=socket.SOCK_STREAM)}
        except (socket.gaierror, UnicodeError) as exc:
            raise WebhookError(f"{parts.hostname} could not be found.",
                               code="not_found") from exc
        for address in found:
            if not ipaddress.ip_address(address.split("%")[0]).is_global:
                raise WebhookError(f"{parts.hostname} points at a private or local network "
                                   "address, which is not allowed.", code="private")
    return url


class _NoRedirect(urlrequest.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urlerror.HTTPError(req.full_url, code,
                                 f"Redirected to {newurl} - use that address instead.",
                                 headers, fp)


_OPENER = urlrequest.build_opener(_NoRedirect)


def _headers(row, body):
    """The secret both ways - X-Webhook-Secret and Authorization: Bearer - so a
    receiver reading either finds it (2026-10-01: nothing to choose on the form)."""
    secret = decrypt(row.secret_encrypted)
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "User-Agent": USER_AGENT, "X-Webhook-Request-Id": str(uuid.uuid4()),
               "X-Webhook-Secret": secret, "Authorization": f"Bearer {secret}"}
    signing = decrypt(row.signing_secret_encrypted)
    if signing:
        digest = hmac.new(signing.encode(), body, hashlib.sha256).hexdigest()
        headers["X-Webhook-Signature"] = f"sha256={digest}"
    return headers


def _call(url, *, method, body, headers):
    """``(status, text)`` of one request; WebhookError (with a ``code``) for no
    answer at all."""
    check_address(url)
    req = urlrequest.Request(url, data=body if method == "POST" else None, method=method,
                             headers=headers)
    try:
        with _OPENER.open(req, timeout=TIMEOUT) as answer:
            return answer.status, answer.read(65536).decode("utf-8", "replace")
    except urlerror.HTTPError as exc:
        text = ""
        try:
            text = exc.read(4096).decode("utf-8", "replace") if exc.fp else ""
        except Exception:  # noqa: BLE001 - the status says enough
            pass
        return exc.code, text or str(exc.reason)
    except (urlerror.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(reason):
            code = "timeout"
        elif isinstance(reason, ssl.SSLError):
            code = "certificate"
        elif isinstance(reason, ConnectionRefusedError):
            code = "refused"
        elif isinstance(reason, socket.gaierror):
            code = "not_found"
        else:
            code = "network"
        raise WebhookError(f"No answer from the address ({reason}).", code=code) from exc


@dataclass
class TestResult:
    """What Test connection found, said for the person who pressed it."""

    ok: bool
    title: str
    detail: str = ""
    fix: str = ""
    url: str = ""

    def as_dict(self):
        return {"ok": self.ok, "title": self.title, "detail": self.detail, "fix": self.fix,
                "url": self.url}


#: (title, what to do) for each way a test can fail before any answer.
NO_ANSWER = {
    "https": ("The address must start with https://",
              "Ask your developer for the https:// address of the webhook and paste it."),
    "not_found": ("The server name could not be found",
                  "Check the address for a typing mistake. The part after https:// must be a "
                  "web address that works from the internet."),
    "private": ("That address is inside a private network",
                "Use the address your system has on the internet, not one that only works "
                "inside your office."),
    "timeout": ("No answer within 10 seconds",
                "Make sure your system is running and can be reached from the internet - a "
                "firewall may be blocking it."),
    "refused": ("The connection was refused",
                "Your system is not accepting connections at that address. Check that it is "
                "running and that the address is right."),
    "certificate": ("The address's security certificate is not valid",
                    "Its https certificate is missing, expired or made for another name. Your "
                    "developer or hosting company can fix it."),
    "network": ("Could not connect",
                "Check the address and that your system is online."),
}


def diagnose(*, url, status=None, text="", error=None):
    """A TestResult for an answer (``status``/``text``) or no answer (``error``)."""
    host = urlsplit(url).hostname or url
    if error is not None:
        title, fix = NO_ANSWER.get(getattr(error, "code", ""), NO_ANSWER["network"])
        return TestResult(False, title, " ".join(error.messages), fix, url)
    said = " ".join((text or "").split())[:300]
    detail = f"Your system answered {status}" + (f": {said}" if said else ".")
    if 200 <= status < 300:
        return TestResult(True, "Connected",
                          f"{host} answered {status}. The address and the secret key both "
                          "work, so attendance can be sent.", "", url)
    if status in (301, 302, 303, 307, 308):
        return TestResult(False, "The address redirects somewhere else", detail,
                          "Use the address it redirects to. Often it is https:// instead of "
                          "http://, or a / at the end.", url)
    found = {
        401: ("Wrong secret key",
              "The secret key does not match the one your system expects. Copy it again from "
              "your developer (IGL ERP: ATTENDANCE_WEBHOOK_SECRET). If a signing secret is set "
              "under Advanced, check it too."),
        403: ("This server is not allowed in",
              "Your system accepts only some IP addresses. Ask your developer to allow this "
              "server's address (IGL ERP: ATTENDANCE_WEBHOOK_IPS)."),
        404: ("Nothing found at that address",
              f"Check the address. If it is right, your system has no test address at {url} - "
              "ask your developer to add one (see the guide), or enter it under Advanced."),
        405: ("That address does not answer a test",
              "Your system needs a test address that answers GET. Enter it under Advanced, or "
              "ask your developer to add one (see the guide)."),
        429: ("Too many requests", "Wait a minute, then test again."),
        503: ("Your system is not ready",
              "It has no secret key set up yet (IGL ERP: ATTENDANCE_WEBHOOK_SECRET in its .env)."),
    }.get(status)
    if found is None and status >= 500:
        found = ("Your system had an error",
                 "The problem is on your system's side; your developer can see it in its logs.")
    if found is None:
        found = ("Unexpected answer", "Send this message to your developer.")
    return TestResult(False, found[0], detail, found[1], url)


def test_connection(*, actor, company_id):
    """Call the saved webhook's test address with the real headers, remember
    the result on the settings and return it as a TestResult."""
    membership = require_structure_manager(actor, company_id)
    row = settings_for(company_id)
    if row is None or not row.url:
        return TestResult(False, "Nothing saved yet",
                          fix="Enter the address and the secret key, then press Save and test.")
    if not row.secret_encrypted:
        return TestResult(False, "No secret key saved", url=row.url,
                          fix="Enter the secret key your developer gave you, then Save and test.")
    url = ping_url(row)
    try:
        status, text = _call(url, method="GET", body=b"", headers=_headers(row, b""))
    except WebhookError as exc:
        result = diagnose(url=url, error=exc)
    else:
        result = diagnose(url=url, status=status, text=text)
    summary = f"{result.title}. {result.detail} {result.fix}".strip()
    with transaction.atomic(), use_company(company_id):
        row.last_tested_at, row.last_test_ok = timezone.now(), result.ok
        row.last_test_message = summary[:500]
        row.save(update_fields=["last_tested_at", "last_test_ok", "last_test_message",
                                "updated_at"])
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action="webhook_settings.tested", obj=row,
                             after={"url": url, "ok": result.ok, "result": summary[:500]})
    return result


def send_test(*, actor, company_id):
    """``test_connection`` that raises WebhookError when it did not pass."""
    result = test_connection(actor=actor, company_id=company_id)
    if not result.ok:
        raise WebhookError(f"{result.title}. {result.fix}")
    return f"{result.title}. {result.detail}"


def _explain(status):
    return {
        401: "the secret was refused - check it and how it is sent.",
        403: "this server is not allowed - your system may allow only some IP addresses.",
        404: "nothing at that address - check it.",
        405: "that address does not take this request - check the health check address.",
        422: "your system could not use the data.",
        429: "too many requests - it will be tried again.",
        503: "your system has no secret set up yet.",
    }.get(status, "")


# --- what is sent -----------------------------------------------------------------


def _code(record):
    assignment = record.employee_assignment
    return (assignment.employee_code if assignment else "") or ""


def _device_ips(record_ids):
    """``{(record id, instant): device ip}`` for the counted scans of these days."""
    from attendance.models import PunchAllocation

    return {
        (record_id, at): ip
        for record_id, at, ip in PunchAllocation.all_objects.filter(
            attendance_record_id__in=record_ids, is_included=True,
            punch_event__isnull=False,
        ).values_list("attendance_record_id", "event_at",
                      "punch_event__device_message__source_ip")
        if ip
    }


def _latest_outs(record_ids):
    """``{record id: instant}``: each day's latest counted scan out so far - a
    day still running keeps no check-out of its own yet."""
    from django.db.models import Max

    from attendance.models import PunchAllocation

    return dict(
        PunchAllocation.all_objects.filter(
            attendance_record_id__in=record_ids, is_included=True,
            interpreted_direction=PunchAllocation.Direction.OUT,
        ).values("attendance_record_id").annotate(latest=Max("event_at"))
        .values_list("attendance_record_id", "latest")
    )


def _wanted(record, mode, latest_out=None):
    """``(check-in, check-out)`` the receiver should have for this day now."""
    if record.first_in_at is None:
        return None, None
    if record.is_open:
        if mode == WebhookSettings.Mode.ARRIVE_AND_LEAVE:
            return record.first_in_at, None
        out = latest_out
    else:
        out = None if record.check_out_by_rule else record.last_out_at
    if out is None or out <= record.first_in_at:
        return record.first_in_at, None
    return record.first_in_at, out


def note_days(company_id, record_ids):
    """Queue what changed on these attendance days. Called by ``recalculate``
    with every day it wrote; does nothing for a company without an active
    webhook. Never fails the attendance: a problem is logged."""
    if not record_ids:
        return 0
    try:
        row = active_settings(company_id)
        if row is None:
            return 0
        return _note(company_id, row, record_ids)
    except Exception:  # noqa: BLE001 - attendance must not fail on the webhook
        logger.exception("Webhook: days of company %s not queued", company_id)
        return 0


def _note(company_id, row, record_ids):
    from attendance.models import AttendanceRecord
    from tenants.models import Company

    company = Company.objects.get(pk=company_id)
    zone = company_zone(company)
    records = list(
        AttendanceRecord.all_objects.select_related("employee", "employee_assignment")
        .filter(pk__in=list(record_ids), company_id=company_id)
        .exclude(attendance_status=AttendanceRecord.AttendanceStatus.INACTIVE)
        .exclude(first_in_at__isnull=True)
    )
    if row.send_from:
        records = [r for r in records if r.work_date >= row.send_from]
    if not records:
        return 0
    states = {
        (s.employee_id, s.work_date): s
        for s in WebhookDayState.all_objects.filter(
            company_id=company_id, employee_id__in={r.employee_id for r in records},
            work_date__in={r.work_date for r in records})
    }
    ips = _device_ips([r.pk for r in records])
    latest = (_latest_outs([r.pk for r in records if r.is_open])
              if row.mode == WebhookSettings.Mode.EVERY_SCAN else {})
    now = timezone.now()
    queued = 0
    with transaction.atomic():
        for record in records:
            code = _code(record)
            if not code:
                continue
            check_in, check_out = _wanted(record, row.mode, latest.get(record.pk))
            state = states.get((record.employee_id, record.work_date))
            sent = (state.check_in, state.check_out) if state else (None, None)
            if (check_in, check_out) == sent:
                continue
            if sent[1] is not None and check_out is None:
                continue      # a check-out once sent is not taken back
            if sent == (None, None):
                kind = WebhookEvent.Kind.CHECK_OUT if check_out else WebhookEvent.Kind.CHECK_IN
            elif sent[0] == check_in and sent[1] is None:
                kind = WebhookEvent.Kind.CHECK_OUT
            else:
                kind = WebhookEvent.Kind.UPDATE
            event = WebhookEvent(company_id=company_id, employee_id=record.employee_id,
                                 work_date=record.work_date, kind=kind, payload={},
                                 next_attempt_at=now)
            ip = ips.get((record.pk, check_out)) or ips.get((record.pk, check_in)) or ""
            event.payload = _payload(event, row, company, zone, record, code, check_in,
                                     check_out, ip)
            event.save()
            if state is None:
                state = WebhookDayState(company_id=company_id, employee_id=record.employee_id,
                                        work_date=record.work_date)
            state.check_in, state.check_out = check_in, check_out
            state.save()
            queued += 1
    if queued:
        transaction.on_commit(lambda: send_soon(company_id))
    return queued


def _payload(event, row, company, zone, record, code, check_in, check_out, ip):
    payload = {
        "event": event.kind,
        "event_id": str(event.event_id),
        row.employee_key: code,
        "employee_name": record.employee.full_name,
        "work_date": record.work_date.isoformat(),
        "check_in": check_in.astimezone(zone).strftime(TIME_FORMAT),
    }
    if check_out is not None:
        payload["check_out"] = check_out.astimezone(zone).strftime(TIME_FORMAT)
    if ip:
        payload["device_ip"] = ip
    payload["timezone"] = str(zone)
    payload["company"] = company.code
    return payload


# --- sending -----------------------------------------------------------------------


def deliver_due(company_id, *, limit=MAX_EVENTS):
    """Send what is due for this company. Returns how many were received."""
    row = active_settings(company_id)
    if row is None:
        return 0
    received = 0
    with transaction.atomic():
        events = list(
            WebhookEvent.all_objects.select_for_update(skip_locked=True)
            .filter(company_id=company_id, status__in=(Status.PENDING, Status.FAILED),
                    next_attempt_at__lte=timezone.now())
            .order_by("work_date", "created_at", "pk")[:limit]
        )
        groups = [events] if row.batch else [[event] for event in events]
        for group in groups:
            if group:
                received += _send(row, group)
    return received


def _send(row, group):
    payloads = [event.payload for event in group]
    body = json.dumps({"events": payloads} if row.batch else payloads[0],
                      separators=(",", ":")).encode()
    now = timezone.now()
    try:
        status, text = _call(row.url, method="POST", body=body, headers=_headers(row, body))
    except WebhookError as exc:
        _retry(group, now, None, " ".join(exc.messages))
        return 0
    if not 200 <= status < 300:
        _retry(group, now, status, f"Answered {status}: {_explain(status)} {text[:200]}".strip())
        return 0
    results = _results(text, len(group))
    received = 0
    for event, result in zip(group, results):
        outcome = str((result or {}).get("result", "")).lower()
        message = str((result or {}).get("message") or "")[:500]
        event.attempts += 1
        event.last_attempt_at, event.last_status_code = now, status
        if outcome == "failed":
            _schedule(event, now, message or "The receiver failed on this event.")
        elif outcome == "skipped":
            event.status, event.last_message = Status.SKIPPED, message or "Skipped by the receiver."
        else:
            event.status, event.sent_at = Status.SENT, now
            event.last_message = message or (outcome.capitalize() if outcome else "Received.")
            received += 1
        event.save(update_fields=["status", "attempts", "last_attempt_at", "last_status_code",
                                  "last_message", "sent_at", "next_attempt_at", "updated_at"])
    if received:
        WebhookSettings.all_objects.filter(pk=row.pk).update(last_sent_at=now)
    return received


def _results(text, size):
    """The receiver's per-event results when it gives them in order, else Nones."""
    try:
        data = json.loads(text)
    except ValueError:
        return [None] * size
    results = data.get("results") if isinstance(data, dict) else None
    if isinstance(results, list) and len(results) == size:
        return [item if isinstance(item, dict) else None for item in results]
    return [None] * size


def _schedule(event, now, message):
    step = event.attempts - 1
    if step >= len(BACKOFF_MINUTES):
        event.status = Status.GAVE_UP
    else:
        event.status = Status.FAILED
        event.next_attempt_at = now + datetime.timedelta(minutes=BACKOFF_MINUTES[step])
    event.last_message = message[:500]


def _retry(group, now, status, message):
    for event in group:
        event.attempts += 1
        event.last_attempt_at, event.last_status_code = now, status
        _schedule(event, now, message)
        event.save(update_fields=["status", "attempts", "last_attempt_at", "last_status_code",
                                  "last_message", "next_attempt_at", "updated_at"])
    logger.warning("Webhook: %d event(s) not received: %s", len(group), message)


def send_again(*, actor, company_id):
    """Put what failed or was given up back in the queue, to go now."""
    membership = require_structure_manager(actor, company_id)
    with transaction.atomic():
        again = WebhookEvent.all_objects.filter(
            company_id=company_id, status__in=(Status.FAILED, Status.GAVE_UP),
        ).update(status=Status.PENDING, attempts=0, next_attempt_at=timezone.now())
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action="webhook.send_again", obj=membership.company,
                             after={"events": again})
    if again:
        transaction.on_commit(lambda: send_soon(company_id))
    return again


# --- when it runs ---------------------------------------------------------------------


def send_soon(company_id):
    """Send in the background, so a device or a page never waits for the ERP."""
    if not getattr(settings, "WEBHOOK_SEND_IN_BACKGROUND", True):
        return

    def run():
        try:
            deliver_due(company_id)
        except Exception:  # noqa: BLE001 - the queue keeps it for the next try
            logger.exception("Webhook: sending for company %s failed", company_id)
        finally:
            connection.close()

    threading.Thread(target=run, name=f"webhook-{company_id}", daemon=True).start()


def on_device_poll(company_id):
    """A device checked in: at most once a minute per company, finish the days
    whose shift has ended (their check-outs go) and send what is due."""
    if not getattr(settings, "WEBHOOK_SEND_IN_BACKGROUND", True):
        return
    if active_settings(company_id) is None:
        return
    if not cache.add(f"webhook-poll:{company_id}", True, 60):
        return

    def run():
        try:
            from attendance.services import refresh
            from tenants.models import Company

            company = Company.objects.get(pk=company_id)
            today = timezone.now().astimezone(company_zone(company)).date()
            refresh(company_id, start=today - datetime.timedelta(days=1), end=today)
            deliver_due(company_id)
        except Exception:  # noqa: BLE001 - the next poll tries again
            logger.exception("Webhook: poll for company %s failed", company_id)
        finally:
            connection.close()

    threading.Thread(target=run, name=f"webhook-poll-{company_id}", daemon=True).start()
