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
secrets are kept encrypted; on the page (owner and company admin only) they
sit in their boxes as dots and the eye shows them. Every change is audited.
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
import time
import uuid
import zoneinfo
from dataclasses import dataclass
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import urlsplit

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from auditlog.services import record_company_event
from common.forms import StyledFormMixin, date_widget
from common.tenant import use_company
from organization.services import require_structure_manager
from webhooks.models import (WebhookDayState, WebhookDebugEntry, WebhookEvent,
                             WebhookSettings)

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


def _readable(token):
    try:
        return decrypt(token)
    except WebhookError:
        return None


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
        fields = ("url", "is_active", "send_breaks", "ping_url", "employee_key", "mode", "batch",
                  "send_from")
        labels = {
            "url": "Your system's webhook address",
            "is_active": "Send attendance to this address",
            "send_breaks": "Also send the scans in between (breaks)",
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
            "send_breaks": "Off: only the check-in (first scan) and the check-out (first scan "
                           "out after the shift ends) are sent - what the IGL ERP wants. On: "
                           "every scan in between - out for a break, back in - is sent too, as "
                           "its own break_out / break_in event. It never closes the day in "
                           "your system: only the check-out does.",
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
        # The saved keys stay in their boxes as dots; the eye shows them
        # (Nihal, 2026-10-01). Only the owner and company admin open this page.
        for name, token in (("secret", getattr(self.instance, "secret_encrypted", "")),
                            ("signing_secret",
                             getattr(self.instance, "signing_secret_encrypted", ""))):
            self.fields[name].widget.render_value = True
            if token and not self.is_bound and name not in self.initial:
                try:
                    self.initial[name] = decrypt(token)
                except WebhookError:
                    pass       # unreadable: the box stays empty, a new one replaces it
        self.fields["secret"].help_text = (
            "Saved. Click the eye to see it. To change it, type or create a new one and Save."
            if has_secret else
            "Your developer gives you this - or press Create a secret key and give it to them. "
            "Your system uses it to know the attendance really comes from us. "
            "(IGL ERP: ATTENDANCE_WEBHOOK_SECRET.)")
        self.fields["signing_secret"].help_text = (
            "Saved. Click the eye to see it; empty it and tick below to stop signing."
            if has_signing_secret else
            "Only if your developer gives you one. (IGL ERP: ATTENDANCE_WEBHOOK_SIGNING_SECRET.)")
        if not has_signing_secret:
            del self.fields["clear_signing_secret"]

    def main_fields(self):
        return [self[name] for name in self.fields
                if name not in ADVANCED + ("is_active", "send_breaks")]

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
        # The boxes post the saved keys back: only a different one is a change.
        if existing is not None and secret == _readable(existing.secret_encrypted):
            secret = ""
        if existing is not None and signing == _readable(existing.signing_secret_encrypted):
            signing = ""
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
            "is_active": row.is_active, "send_breaks": row.send_breaks,
            "signed": bool(row.signing_secret_encrypted)}


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
        status, text, trace = _traced_call(url, method="GET", body=b"",
                                           headers=_headers(row, b""))
    except WebhookError as exc:
        result, trace = diagnose(url=url, error=exc), getattr(exc, "trace", {})
    else:
        result = diagnose(url=url, status=status, text=text)
    summary = f"{result.title}. {result.detail} {result.fix}".strip()
    debug_note(row, "test_connection", result.ok, summary, trace)
    with transaction.atomic(), use_company(company_id):
        row.last_tested_at, row.last_test_ok = timezone.now(), result.ok
        row.last_test_message = summary[:500]
        row.save(update_fields=["last_tested_at", "last_test_ok", "last_test_message",
                                "updated_at"])
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action="webhook_settings.tested", obj=row,
                             after={"url": url, "ok": result.ok, "result": summary[:500]})
    return result


class TestEventForm(StyledFormMixin, forms.Form):
    """A check-in and check-out typed in by hand, sent as real attendance - to
    try the whole way to the ERP before a device is connected (2026-10-01)."""

    employee_code = forms.CharField(
        label="Employee ID", max_length=64,
        help_text="An employee of this company. Your system must know the same ID.")
    work_date = forms.DateField(label="Day", widget=date_widget("Choose a day"))
    check_in = forms.TimeField(label="Check-in", input_formats=["%H:%M", "%H:%M:%S"],
                               widget=forms.TextInput(attrs={
                                   "placeholder": "HH:MM", "maxlength": 8,
                                   "autocomplete": "off", "data-timepicker": ""}))
    check_out = forms.TimeField(label="Check-out (optional)", required=False,
                                input_formats=["%H:%M", "%H:%M:%S"],
                                widget=forms.TextInput(attrs={
                                    "placeholder": "HH:MM", "maxlength": 8,
                                    "autocomplete": "off", "data-timepicker": ""}))

    def clean(self):
        data = super().clean()
        if data.get("check_in") and data.get("check_out") and data["check_out"] <= data["check_in"]:
            self.add_error("check_out", "The check-out must be after the check-in.")
        return data


def send_test_event(*, actor, company_id, employee_code, work_date, check_in, check_out=None):
    """Send one check-in (and check-out) typed in by hand, now, in the same
    format as the real ones; keep it in What was sent; return a TestResult
    with what the receiver did with it."""
    from employees.models import EmployeeAssignment
    from tenants.models import Company

    membership = require_structure_manager(actor, company_id)
    row = settings_for(company_id)
    if row is None or not row.url or not row.secret_encrypted:
        return TestResult(False, "Nothing saved yet",
                          fix="Save the address and the secret key first.")
    code = (employee_code or "").strip()
    assignment = (EmployeeAssignment.all_objects.select_related("employee")
                  .filter(company_id=company_id, employee_code=code)
                  .exclude(status="cancelled").order_by("-effective_from").first())
    if assignment is None:
        return TestResult(False, f"No employee has Employee ID {code}",
                          fix="Use the Employee ID of one of this company's employees.")
    company = Company.objects.get(pk=company_id)
    zone = company_zone(company)
    first = datetime.datetime.combine(work_date, check_in, tzinfo=zone)
    last = datetime.datetime.combine(work_date, check_out, tzinfo=zone) if check_out else None
    now = timezone.now()
    with use_company(company_id):
        event = WebhookEvent(company_id=company_id, employee=assignment.employee,
                             work_date=work_date, kind=WebhookEvent.Kind.TEST, payload={},
                             next_attempt_at=now, status=Status.SKIPPED)
        payload = {
            "event": "check_out" if last else "check_in",
            "event_id": str(event.event_id),
            row.employee_key: code,
            "employee_name": assignment.employee.full_name,
            "work_date": work_date.isoformat(),
            "check_in": first.strftime(TIME_FORMAT),
        }
        if last is not None:
            payload["check_out"] = last.strftime(TIME_FORMAT)
        payload.update({"timezone": str(zone), "company": company.code, "test": True})
        event.payload = payload
        body = json.dumps({"events": [payload]} if row.batch else payload,
                          separators=(",", ":")).encode()
        try:
            status, text, trace = _traced_call(row.url, method="POST", body=body,
                                               headers=_headers(row, body))
        except WebhookError as exc:
            result, trace = diagnose(url=row.url, error=exc), getattr(exc, "trace", {})
            status = None
        else:
            result = _test_answer(row.url, status, text, code, row.employee_key)
        debug_note(row, "test_event", result.ok,
                   f"{result.title}. {result.detail} {result.fix}".strip(), trace)
        event.attempts, event.last_attempt_at, event.last_status_code = 1, now, status
        event.status = Status.SENT if result.ok else Status.SKIPPED
        event.sent_at = now if result.ok else None
        event.last_message = f"{result.title}. {result.detail}"[:500]
        event.save()
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action="webhook.test_event_sent", obj=event,
                             after={"payload": payload, "ok": result.ok,
                                    "result": event.last_message})
    return result


def _test_answer(url, status, text, code, key):
    """A TestResult for the receiver's answer to a test event: what it did
    with it (created, updated, unchanged, skipped) when it says."""
    if not 200 <= status < 300:
        return diagnose(url=url, status=status, text=text)
    [result] = _results(text, 1)
    outcome = str((result or {}).get("result", "")).lower()
    said = str((result or {}).get("message") or "")
    if outcome == "skipped":
        return TestResult(False, "Your system received it but skipped it",
                          f"It said: {said}" if said else "",
                          f"Usually your system does not know {key} {code}. Check the same "
                          "Employee ID exists there, or read its message above.", url)
    if outcome == "failed":
        return TestResult(False, "Your system received it but could not save it",
                          f"It said: {said}" if said else "",
                          "Send it again; if it keeps failing, your developer can see why in "
                          "its logs.", url)
    words = {"created": "a new attendance row was created",
             "updated": "the attendance row was updated",
             "unchanged": "nothing needed changing (already there, or the day is complete)"}
    done = words.get(outcome, "it was accepted")
    return TestResult(True, "Received", f"Your system answered {status}: {done}."
                      + (f" It said: {said}" if said else ""), "", url)


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


def _between_scans(record_ids):
    """``{record id: [(instant, kind), ...]}``: each day's counted scans
    between its check-in and its check-out - out for a break, back in - in
    time order."""
    from attendance.models import PunchAllocation

    found = {}
    for record_id, at, label in PunchAllocation.all_objects.filter(
        attendance_record_id__in=record_ids, is_included=True,
        label__in=(PunchAllocation.Label.BREAK_OUT, PunchAllocation.Label.BREAK_IN),
    ).order_by("event_at").values_list("attendance_record_id", "event_at", "label"):
        found.setdefault(record_id, []).append(
            (at, WebhookEvent.Kind.BREAK_OUT if label == PunchAllocation.Label.BREAK_OUT
             else WebhookEvent.Kind.BREAK_IN))
    return found


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
        # Arrive and leave: an open day has a check-out only once they scanned
        # out after the shift's end - not a break, their check-out
        # (attendance.pairing.label_scans, 2026-10-03). Every scan: the latest.
        out = (record.last_out_at if mode == WebhookSettings.Mode.ARRIVE_AND_LEAVE
               else latest_out)
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
        row = settings_for(company_id)
        if row is None:
            return 0
        if not row.is_active:
            debug_note(row, "not_queued", None,
                       f"Attendance changed on {len(record_ids)} day(s), but the webhook is "
                       "switched off, so nothing was queued.",
                       {"attendance_days": len(record_ids)})
            return 0
        return _note(company_id, row, record_ids)
    except Exception:  # noqa: BLE001 - attendance must not fail on the webhook
        logger.exception("Webhook: days of company %s not queued", company_id)
        return 0


def _note(company_id, row, record_ids):
    """Queue what changed on these days.

    Called after every attendance rebuild - and every screen that shows
    attendance rebuilds today first, for everybody - so it is asked far more
    often than anything changes (2026-10-03: four times a minute with three
    people scanning). It therefore compares first and does the work only for
    the days that changed; the rest cost one comparison each.
    """
    from attendance.models import AttendanceRecord
    from tenants.models import Company

    records = AttendanceRecord.all_objects.filter(
        pk__in=list(record_ids), company_id=company_id,
    ).exclude(attendance_status=AttendanceRecord.AttendanceStatus.INACTIVE).exclude(
        first_in_at__isnull=True)
    if row.send_from:
        if debugging(row):
            before = records.filter(work_date__lt=row.send_from).count()
            if before:
                debug_note(row, "not_queued", None,
                           f"{before} day(s) before {row.send_from:%d %b %Y} (Send from) are "
                           "not sent.", {"send_from": row.send_from.isoformat()})
        records = records.filter(work_date__gte=row.send_from)
    records = list(records.select_related("employee", "employee_assignment"))
    if not records:
        return 0
    states = {
        (s.employee_id, s.work_date): s
        for s in WebhookDayState.all_objects.filter(
            company_id=company_id, employee_id__in={r.employee_id for r in records},
            work_date__in={r.work_date for r in records})
    }
    latest = (_latest_outs([r.pk for r in records if r.is_open])
              if row.mode == WebhookSettings.Mode.EVERY_SCAN else {})
    between = _between_scans([r.pk for r in records]) if row.send_breaks else {}
    zone = None
    changed = []
    for record in records:
        code = _code(record)
        who = f"{record.employee.full_name} on {record.work_date:%d %b %Y}"
        if not code:
            debug_note(row, "not_queued", None,
                       f"{who}: not sent - they have no Employee ID.", {})
            continue
        check_in, check_out = _wanted(record, row.mode, latest.get(record.pk))
        state = states.get((record.employee_id, record.work_date))
        sent = (state.check_in, state.check_out) if state else (None, None)
        done = set(state.breaks_sent) if state else set()
        breaks = [(at, label) for at, label in between.get(record.pk, ())
                  if at.isoformat() not in done]
        same = (check_in, check_out) == sent or (sent[1] is not None and check_out is None)
        if same and not breaks:
            # Nothing new (a check-out once sent is not taken back). Said
            # once per change in what they did, not at every rebuild.
            if debugging(row):
                zone = zone or company_zone(record.employee.company)
                debug_note(row, "not_queued", None,
                           _nothing_new(who, code, row, record, sent, zone), {})
            continue
        changed.append((record, code, who, check_in, check_out, state, sent, same, breaks))
    if not changed:
        return 0

    company = Company.objects.get(pk=company_id)
    zone = company_zone(company)
    ips = _device_ips([record.pk for record, *_ in changed])
    now = timezone.now()
    queued = 0
    with transaction.atomic():
        for record, code, who, check_in, check_out, state, sent, same, breaks in changed:
            main = None
            if not same:
                if sent == (None, None):
                    kind = (WebhookEvent.Kind.CHECK_OUT if check_out
                            else WebhookEvent.Kind.CHECK_IN)
                elif sent[0] == check_in and sent[1] is None:
                    kind = WebhookEvent.Kind.CHECK_OUT
                else:
                    kind = WebhookEvent.Kind.UPDATE
                main = WebhookEvent(company_id=company_id, employee_id=record.employee_id,
                                    work_date=record.work_date, kind=kind, payload={},
                                    next_attempt_at=now)
                ip = ips.get((record.pk, check_out)) or ips.get((record.pk, check_in)) or ""
                main.payload = _payload(main, row, company, zone, record, code, check_in,
                                        check_out, ip)
            # In the order they happened: the day's first notice (its check-in)
            # before the breaks, a check-out after them.
            first = main is not None and (sent == (None, None)
                                          or main.kind == WebhookEvent.Kind.CHECK_IN)
            events = [main] if first else []
            for at, label in breaks:
                event = WebhookEvent(company_id=company_id, employee_id=record.employee_id,
                                     work_date=record.work_date, kind=label, payload={},
                                     next_attempt_at=now)
                event.payload = _break_payload(event, row, company, zone, record, code, at,
                                               ips.get((record.pk, at), ""))
                events.append(event)
            if main is not None and not first:
                events.append(main)
            for event in events:
                event.save()
                debug_note(row, "queued", None,
                           f"{who} ({code}): {event.get_kind_display().lower()} queued; it is "
                           "sent next.", {"payload": event.payload})
            if state is None:
                state = WebhookDayState(company_id=company_id, employee_id=record.employee_id,
                                        work_date=record.work_date)
            if not same:
                state.check_in, state.check_out = check_in, check_out
            state.breaks_sent = sorted(set(state.breaks_sent or [])
                                       | {at.isoformat() for at, _ in breaks})
            state.save()
            queued += len(events)
    if queued:
        transaction.on_commit(lambda: send_soon(company_id))
    return queued


def _nothing_new(who, code, row, record, sent, zone):
    """Why a day sent nothing this time, in words that change only when what
    they did changes - so a debug message appears once per change."""
    def at(moment):
        return moment.astimezone(zone).strftime("%H:%M:%S") if moment else ""

    told = f"your system has the check-in {at(sent[0])}" if sent[0] else "nothing sent yet"
    if sent[1]:
        told += f" and the check-out {at(sent[1])}"
    reason = ""
    if (record.is_open and not sent[1] and row.mode == WebhookSettings.Mode.ARRIVE_AND_LEAVE
            and record.scheduled_end_at):
        reason = (f" Their check-out goes when they scan out after the shift ends "
                  f"({at(record.scheduled_end_at)[:5]}); a scan out before that is a break.")
    elif sent[1] and record.is_open and not record.last_out_at:
        reason = " They scanned back in after it; the next scan out is sent as the new check-out."
    return f"{who} ({code}): nothing new - {told}.{reason}"


def _break_payload(event, row, company, zone, record, code, at, ip):
    """A scan in between: no check_in or check_out field, so it can never
    open or close the day in the receiver - only its own time."""
    payload = {
        "event": event.kind,
        "event_id": str(event.event_id),
        row.employee_key: code,
        "employee_name": record.employee.full_name,
        "work_date": record.work_date.isoformat(),
        "punch_time": at.astimezone(zone).strftime(TIME_FORMAT),
    }
    if ip:
        payload["device_ip"] = ip
    payload["timezone"] = str(zone)
    payload["company"] = company.code
    return payload


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


# --- debug messages (Nihal, 2026-10-03) ----------------------------------------------
#
# A button on the page turns them on for 15 minutes: everything the webhook
# does meanwhile - each event queued (or why not), each send with the exact
# request and the receiver's whole answer, each test, success or not - is kept
# and shown on the page as a message and as JSON. When the 15 minutes end (or
# Stop), they are deleted. The secret key never appears in them.

DEBUG_MINUTES = 15
DEBUG_KEEP = 300                 # entries shown at most
SECRET_HEADERS = ("X-Webhook-Secret", "Authorization")
HIDDEN = "(secret key, hidden)"
DEBUG_WHAT = {
    "queued": "Queued", "send": "Sent to your system", "test_connection": "Test connection",
    "test_event": "Send a test", "not_queued": "Not queued", "debug": "Debug",
}


def debugging(row):
    return bool(row is not None and row.debug_until and row.debug_until > timezone.now())


def _end_debug_if_over(row):
    """The 15 minutes are over: the messages go, and the switch goes off."""
    if row is not None and row.debug_until and row.debug_until <= timezone.now():
        WebhookDebugEntry.all_objects.filter(company_id=row.company_id).delete()
        WebhookSettings.all_objects.filter(pk=row.pk).update(debug_until=None)
        row.debug_until = None


def start_debug(*, actor, company_id):
    membership = require_structure_manager(actor, company_id)
    row = settings_for(company_id)
    if row is None:
        raise WebhookError("Save the webhook first.")
    with transaction.atomic():
        WebhookDebugEntry.all_objects.filter(company_id=company_id).delete()
        row.debug_until = timezone.now() + datetime.timedelta(minutes=DEBUG_MINUTES)
        WebhookSettings.all_objects.filter(pk=row.pk).update(debug_until=row.debug_until)
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action="webhook.debug_started", obj=row,
                             after={"until": row.debug_until.isoformat()})
    debug_note(row, "debug", None,
               f"Debug messages on for {DEBUG_MINUTES} minutes. "
               + ("The webhook is switched on: scans are queued and sent as they happen."
                  if row.is_active else
                  "The webhook is switched OFF: nothing is queued or sent until it is "
                  "switched on (the tests still work)."),
               {"url": row.url, "test_url": ping_url(row), "switched_on": row.is_active,
                "mode": row.mode, "one_request_for_several": row.batch,
                "scans_in_between": row.send_breaks,
                "employee_key": row.employee_key,
                "send_from": row.send_from.isoformat() if row.send_from else None})
    return row


def stop_debug(*, actor, company_id):
    membership = require_structure_manager(actor, company_id)
    row = settings_for(company_id)
    if row is None:
        return
    with transaction.atomic():
        WebhookDebugEntry.all_objects.filter(company_id=company_id).delete()
        WebhookSettings.all_objects.filter(pk=row.pk).update(debug_until=None)
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action="webhook.debug_stopped", obj=row, after={})


def debug_note(row, what, ok, message, detail=None):
    """Keep one debug message, when they are on. Never stands in the way of
    the webhook itself."""
    try:
        if not debugging(row):
            return
        if ok is None and WebhookDebugEntry.all_objects.filter(
                company_id=row.company_id, what=what, message=message[:2000]).exists():
            return        # said already in this window: once per change, not per rebuild
        WebhookDebugEntry.all_objects.create(company_id=row.company_id, what=what, ok=ok,
                                             message=message[:2000], detail=detail or {})
    except Exception:  # noqa: BLE001 - a debug message is never worth a failed send
        logger.exception("Webhook: debug message not kept")


def debug_state(company_id):
    """What the page shows: on or off, time left, and the messages, newest first."""
    row = settings_for(company_id)
    _end_debug_if_over(row)
    if not debugging(row):
        return {"active": False, "seconds_left": 0, "entries": []}
    zone = company_zone(row.company)
    rows = (WebhookDebugEntry.all_objects.filter(company_id=company_id)
            .order_by("-created_at", "-pk")[:DEBUG_KEEP])
    return {
        "active": True,
        "seconds_left": max(0, int((row.debug_until - timezone.now()).total_seconds())),
        "entries": [{
            "id": entry.pk,
            "at": entry.created_at.astimezone(zone).strftime(TIME_FORMAT),
            "what": DEBUG_WHAT.get(entry.what, entry.what),
            "ok": entry.ok,
            "message": entry.message,
            "detail": entry.detail,
        } for entry in rows],
    }


def _shown_headers(headers):
    return {k: (HIDDEN if k in SECRET_HEADERS else v) for k, v in headers.items()}


def _as_json(text):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def _traced_call(url, *, method, body, headers):
    """``_call``, plus a ``trace`` of the exact request and the whole answer
    for the debug messages: ``(status, text, trace)``. A WebhookError carries
    its trace too."""
    started = time.monotonic()
    request = {"method": method, "url": url, "headers": _shown_headers(headers),
               "body": _as_json(body.decode()) if body else None}
    try:
        status, text = _call(url, method=method, body=body, headers=headers)
    except WebhookError as exc:
        exc.trace = {"request": request,
                     "error": {"code": getattr(exc, "code", ""),
                               "message": " ".join(exc.messages)},
                     "took_ms": int((time.monotonic() - started) * 1000)}
        raise
    answer = _as_json(text)
    return status, text, {
        "request": request,
        "response": {"status": status, "meaning": _explain(status),
                     "body": answer if answer is not None else text[:20000]},
        "took_ms": int((time.monotonic() - started) * 1000),
    }


# --- sending -----------------------------------------------------------------------


def deliver_due(company_id, *, limit=MAX_EVENTS):
    """Send what is due for this company. Returns how many were received."""
    row = active_settings(company_id)
    if row is None:
        return 0
    _end_debug_if_over(row)
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
    who = ", ".join(f"{e.payload.get(row.employee_key, '?')} {e.kind}" for e in group[:10])
    try:
        status, text, trace = _traced_call(row.url, method="POST", body=body,
                                           headers=_headers(row, body))
    except WebhookError as exc:
        _retry(group, now, None, " ".join(exc.messages))
        debug_note(row, "send", False,
                   f"Not received ({who}): {' '.join(exc.messages)} It will be tried again.",
                   getattr(exc, "trace", {}))
        return 0
    if not 200 <= status < 300:
        _retry(group, now, status, f"Answered {status}: {_explain(status)} {text[:200]}".strip())
        debug_note(row, "send", False,
                   f"Not received ({who}): your system answered {status} - {_explain(status)} "
                   "It will be tried again.", trace)
        return 0
    results = _results(text, len(group))
    received = 0
    outcomes = []
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
        outcomes.append({"event_id": str(event.event_id),
                         "employee": event.payload.get(row.employee_key), "event": event.kind,
                         "result": outcome or "received", "status_here": event.get_status_display(),
                         "message": event.last_message})
    if received:
        WebhookSettings.all_objects.filter(pk=row.pk).update(last_sent_at=now)
    debug_note(row, "send", received == len(group),
               f"Sent {len(group)} event(s) ({who}): {received} received"
               + (f", {len(group) - received} not" if received < len(group) else "")
               + f". Your system answered {status}.", {**trace, "events": outcomes})
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
    row = active_settings(company_id)
    if row is None:
        return
    # Once a minute for the company, whichever server worker the device
    # reaches: the cache was each worker's own, so several workers each
    # rebuilt everybody's day every minute (2026-10-03).
    now = timezone.now()
    claimed = WebhookSettings.all_objects.filter(pk=row.pk).filter(
        Q(polled_at__isnull=True) | Q(polled_at__lte=now - datetime.timedelta(seconds=60))
    ).update(polled_at=now)
    if not claimed:
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
