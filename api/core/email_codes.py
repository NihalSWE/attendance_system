"""Two-step codes by email - the backup way (docs/api/02-authentication.md).

The authenticator app is the main way. When it cannot be used - the phone is
lost, the app deleted, the account gone - a code by email gets the person in
at login, so nobody is locked out of their company. Nothing to set up: it goes
to the login's own email. Once in, they can move the app to a new phone.

A code is 6 digits, works once, for 10 minutes, and dies after 5 wrong tries.
Only its hash is kept, on the session that asked for it. One email a minute
per session.

It goes out through the company's own mail account (Organisation -> Email
settings) or the server's (.env), as payslips do. With neither, email codes
are not offered - except with DEBUG on, where the email is printed in the
server's console so the flow can be tried locally.
"""

import datetime
import secrets

from django.conf import settings
from django.core.mail import EmailMessage
from django.db.models import F
from django.utils import timezone

from accounts.services import get_active_memberships
from api.core.crypto import digest, same
from api.core.errors import ApiError
from api.models import ApiSession
from organization import mail_settings as mail

CODE_MINUTES = 10
RESEND_SECONDS = 60
MAX_TRIES = 5


def route(user):
    """``(how, company_id)``: "company" (that company's own mail account),
    "server" (.env), "console" (DEBUG only) or None (cannot be sent)."""
    for company_id in get_active_memberships(user).values_list("company_id", flat=True):
        if mail.sender(company_id)[0] == "company":
            return "company", company_id
    if getattr(settings, "MAIL_CONFIGURED", False):
        return "server", None
    if settings.DEBUG:
        return "console", None
    return None, None


def available(user):
    return route(user)[0] is not None


def masked(email):
    """t***@example.com - enough to recognise the address, not to learn it."""
    local, _, domain = (email or "").partition("@")
    return f"{local[:1]}***@{domain}"


def _hash(session, code):
    return digest(f"email-code:{session.public_id}:{code}")


def send(session):
    """Email a new code for this session. Answers where it went and for how long."""
    user = session.user
    now = timezone.now()
    if session.email_code_sent_at is not None:
        wait = RESEND_SECONDS - int((now - session.email_code_sent_at).total_seconds())
        if wait > 0:
            error = ApiError("rate_limited", f"A code was sent just now. Ask for a new one in "
                                             f"{wait} seconds.")
            error.retry_after = wait
            raise error
    how, company_id = route(user)
    if how is None:
        raise ApiError("email_not_available")
    code = f"{secrets.randbelow(10 ** 6):06d}"
    if how == "company":
        _, from_email, from_name = mail.sender(company_id)
    else:
        from_email, from_name = settings.DEFAULT_FROM_EMAIL or "webmaster@localhost", ""
    message = EmailMessage(
        subject=f"Your login code: {code}",
        body=(f"Your login code is {code}\n\n"
              f"It works once, for {CODE_MINUTES} minutes. Nobody from the company will ever "
              "ask you for it.\n\n"
              f"If you did not just try to log in as {user.email}, someone knows your "
              "password: change it now."),
        from_email=mail.from_header(from_email, from_name or "Attendance Management"),
        to=[user.email],
    )
    try:
        mail.send(message, mail.connection_for(company_id) if how == "company" else None)
    except mail.MailSettingsError as exc:
        raise ApiError("email_not_sent", exc.messages[0]) from exc
    values = {"email_code_hash": _hash(session, code), "email_code_sent_at": now,
              "email_code_expires_at": now + datetime.timedelta(minutes=CODE_MINUTES),
              "email_code_tries": 0}
    ApiSession.objects.filter(pk=session.pk).update(**values)
    for name, value in values.items():
        setattr(session, name, value)
    return {"email_sent_to": masked(user.email), "email_expires_in": CODE_MINUTES * 60}


def check(session, code):
    """True once for the right code. Commits its own bookkeeping (a wrong try
    counts even when the request then fails)."""
    fresh = ApiSession.objects.filter(pk=session.pk).values(
        "email_code_hash", "email_code_expires_at", "email_code_tries").first()
    if (not fresh or not fresh["email_code_hash"] or fresh["email_code_tries"] >= MAX_TRIES
            or fresh["email_code_expires_at"] is None
            or fresh["email_code_expires_at"] < timezone.now()):
        return False
    if not same(_hash(session, code), fresh["email_code_hash"]):
        ApiSession.objects.filter(pk=session.pk).update(email_code_tries=F("email_code_tries") + 1)
        return False
    # Used up - claimed by a conditional update, so two requests cannot both
    # use it. A new code may be asked for at once.
    return ApiSession.objects.filter(pk=session.pk, email_code_hash=fresh["email_code_hash"]).update(
        email_code_hash="", email_code_expires_at=None, email_code_sent_at=None) == 1
