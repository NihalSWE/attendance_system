"""Organisation → Email settings: the company's own mail account.

A company enters the SMTP details its mail provider gives it - Gmail, Outlook,
SendGrid, Mailgun, Brevo, Amazon SES and the rest all give them; for an
API-key provider the key is the password - and emails such as payslips then go
out through that account, from the company's own address.

With no account saved, or with it switched off, the server's mail account in
.env is used exactly as before (``settings.MAIL_CONFIGURED``). With neither,
nothing is sent and the pages say so.

The password is encrypted with a key derived from ``SECRET_KEY`` and never
shown again; leaving the field empty keeps it. Only the owner and company
administrator may change these settings, and every change is audited without
the password.

The mail server is one the company chose, so it is reached only on the usual
mail ports and never at a private or local address: a company cannot point it
at this server's own network.
"""

import base64
import hashlib
import ipaddress
import socket
import smtplib
from email.utils import formataddr

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage
from django.db import transaction
from django.utils import timezone

from auditlog.services import record_company_event
from common.forms import StyledFormMixin
from common.tenant import use_company
from organization.models import CompanyMailSettings
from organization.services import require_structure_manager

#: The ports mail providers use. Anything else is refused.
MAIL_PORTS = (25, 465, 587, 2525)

#: How long to wait for a mail server before giving up, in seconds.
TIMEOUT = 20

#: What the page suggests for the common providers (all SMTP, port 587,
#: STARTTLS). For an API-key provider the username is fixed and the key is
#: the password.
PROVIDERS = (
    ("Gmail / Google Workspace", "smtp.gmail.com", "Your Gmail address",
     "An App password (Google account → Security → App passwords)"),
    ("Outlook / Microsoft 365", "smtp.office365.com", "Your Outlook address", "Your password"),
    ("SendGrid", "smtp.sendgrid.net", "apikey (exactly that word)", "Your SendGrid API key"),
    ("Mailgun", "smtp.mailgun.org", "The SMTP login Mailgun shows", "Its SMTP password"),
    ("Brevo", "smtp-relay.brevo.com", "Your Brevo login", "An SMTP key"),
    ("Amazon SES", "email-smtp.<region>.amazonaws.com", "SMTP username", "SMTP password"),
)


class MailSettingsError(ValidationError):
    """The company's mail account is missing, off, unreadable or unreachable."""


# --- the password, encrypted ------------------------------------------------


def _cipher():
    from cryptography.fernet import Fernet

    # A key of its own, derived from SECRET_KEY: nothing new to set up, and
    # not the biometric template key (a separate decision).
    digest = hashlib.sha256(b"company-mail-settings:" + settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(password):
    return _cipher().encrypt(password.encode()).decode("ascii") if password else ""


def decrypt(token):
    from cryptography.fernet import InvalidToken

    if not token:
        return ""
    try:
        return _cipher().decrypt(token.encode("ascii")).decode()
    except InvalidToken as exc:
        raise MailSettingsError(
            "The saved mail password can no longer be read (the server's secret key "
            "changed). Enter the password again under Organisation → Email settings."
        ) from exc


# --- reading ----------------------------------------------------------------


def settings_for(company_id):
    """The company's saved mail account, or None."""
    with use_company(company_id):
        return CompanyMailSettings.objects.filter(company_id=company_id).first()


def sender(company_id):
    """``(how, from_email, from_name)``: how this company's email goes out.

    ``how`` is "company" (its own account), "server" (the .env account) or
    None (nothing can be sent).
    """
    own = settings_for(company_id)
    if own is not None and own.is_active:
        return "company", own.from_email, own.from_name
    if getattr(settings, "MAIL_CONFIGURED", False):
        return "server", settings.DEFAULT_FROM_EMAIL, ""
    return None, "", ""


def not_set_up_reason():
    return ("Email is not set up on this server yet, so nothing can be sent. The owner "
            "or company admin sets the company's mail account under Organisation → "
            "Email settings.")


def _check_host(host, port):
    """Refuse a mail server on an odd port or at a private/local address."""
    if port not in MAIL_PORTS:
        raise MailSettingsError(
            f"Port {port} is not a mail port. Use one of {', '.join(map(str, MAIL_PORTS))} "
            "(your provider says which; usually 587).")
    try:
        found = {info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
    except (socket.gaierror, UnicodeError) as exc:
        raise MailSettingsError(f"The mail server {host} could not be found. Check the "
                                "server address.") from exc
    for address in found:
        ip = ipaddress.ip_address(address.split("%")[0])
        if not ip.is_global:
            raise MailSettingsError(
                f"The mail server {host} points at a private or local address, which is "
                "not allowed. Use your provider's public mail server.")


def connection_for(company_id):
    """A mail connection for this company, or None for the server's default.

    Raises MailSettingsError when the company's own account cannot be used.
    """
    own = settings_for(company_id)
    if own is None or not own.is_active:
        return None
    return _smtp(own.host, own.port, own.security, own.username, decrypt(own.password_encrypted))


def _smtp(host, port, security, username, password):
    from django.core.mail.backends.smtp import EmailBackend

    _check_host(host, port)
    # alias: a connection made here, not from settings.MAILERS - Django 6 then
    # uses exactly these values rather than the server's own mail settings.
    return EmailBackend(
        alias="company-mail", host=host, port=port, username=username or None,
        password=password or None, timeout=TIMEOUT,
        use_tls=security == CompanyMailSettings.Security.STARTTLS,
        use_ssl=security == CompanyMailSettings.Security.SSL,
    )


def from_header(address, name):
    """``"Name" <address>``, safely quoted; the address alone with no name."""
    return formataddr((name, address)) if name else address


def send(message, connection):
    """Send ``message`` and turn a mail server's refusal into a plain reason."""
    try:
        sent = message.send() if connection is None else connection.send_messages([message])
    except smtplib.SMTPAuthenticationError as exc:
        raise MailSettingsError(
            "The mail server refused the username or password. Check them under "
            "Organisation → Email settings (Gmail needs an App password).") from exc
    except smtplib.SMTPSenderRefused as exc:
        raise MailSettingsError(
            f"The mail server would not send from {message.from_email}. Use a sender "
            "address your provider has verified.") from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise MailSettingsError(
            f"The mail server refused the address {', '.join(message.to)}.") from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise MailSettingsError(
            f"The email could not be sent ({exc.__class__.__name__}: {exc})."[:400]
        ) from exc
    if not sent:
        raise MailSettingsError("The mail server did not accept the email.")


# --- the form and saving -------------------------------------------------------


class MailSettingsForm(StyledFormMixin, forms.ModelForm):
    password = forms.CharField(
        label="Password or API key", required=False, strip=False,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
    )

    class Meta:
        model = CompanyMailSettings
        fields = ("from_email", "from_name", "host", "port", "security", "username", "is_active")
        labels = {
            "from_email": "Sender email",
            "from_name": "Sender name",
            "host": "Mail server (SMTP)",
            "port": "Port",
            "security": "Security",
            "username": "Username",
            "is_active": "Send the company's email through this account",
        }
        help_texts = {
            "from_email": "The address emails come from. Your provider must allow it.",
            "from_name": "What people see as the sender, e.g. the company name.",
            "host": "From your provider, e.g. smtp.gmail.com or smtp.sendgrid.net.",
            "port": "Usually 587. Allowed: 25, 465, 587, 2525.",
            "username": "For SendGrid it is the word apikey.",
            "is_active": "Untick to go back to the server's own mail account.",
        }

    def __init__(self, *args, has_password=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["password"].help_text = (
            "Saved and hidden. Leave empty to keep it; type a new one to replace it."
            if has_password else "For API-key providers, paste the API key here.")

    def clean_host(self):
        host = self.cleaned_data["host"].strip().lower()
        if not host or " " in host or "/" in host or ":" in host:
            raise ValidationError("Enter just the server name, e.g. smtp.gmail.com.")
        return host


@transaction.atomic
def save_settings(*, actor, company_id, form):
    """Save the company's mail account. Owner and company admin only."""
    membership = require_structure_manager(actor, company_id)
    with use_company(company_id):
        existing = CompanyMailSettings.objects.filter(company_id=company_id).first()
        before = _snapshot(existing)
        row = form.save(commit=False)
        row.company = membership.company
        password = form.cleaned_data.get("password") or ""
        if password:
            row.password_encrypted = encrypt(password)
        elif existing is not None:
            row.password_encrypted = existing.password_encrypted
        if row.pk is None:
            row.created_by = actor
        row.updated_by = actor
        # A change of server or login makes the last test say nothing about now.
        if existing is not None and (
            password or (existing.host, existing.port, existing.security, existing.username)
            != (row.host, row.port, row.security, row.username)
        ):
            row.last_tested_at, row.last_test_ok, row.last_test_message = None, None, ""
        row.full_clean()
        row.save()
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="mail_settings.saved", obj=row,
            before=before, after={**_snapshot(row), "password_changed": bool(password)},
        )
    return row


def _snapshot(row):
    if row is None:
        return {}
    return {"host": row.host, "port": row.port, "security": row.security,
            "username": row.username, "from_email": row.from_email,
            "from_name": row.from_name, "is_active": row.is_active}


def send_test(*, actor, company_id, to):
    """Send a short test email through the company's account; remember the result.

    Not one transaction: a failed test is saved (and audited) before it is
    reported, so the page can say what went wrong last time."""
    membership = require_structure_manager(actor, company_id)
    own = settings_for(company_id)
    if own is None:
        raise MailSettingsError("Save the mail account first, then send a test.")
    to = (to or "").strip()
    try:
        forms.EmailField().clean(to)
    except ValidationError as exc:
        raise MailSettingsError("Enter the address to send the test to.") from exc
    company = membership.company
    message = EmailMessage(
        subject=f"Test email from {company.name}",
        body=(f"This is a test from {company.name}'s attendance system.\n\n"
              "If you can read this, the company's email settings work."),
        from_email=from_header(own.from_email, own.from_name or company.name),
        to=[to],
    )
    ok, reason = True, f"Sent to {to}."
    try:
        send(message, _smtp(own.host, own.port, own.security, own.username,
                            decrypt(own.password_encrypted)))
    except MailSettingsError as exc:
        ok, reason = False, " ".join(exc.messages)
    with transaction.atomic(), use_company(company_id):
        own.last_tested_at, own.last_test_ok = timezone.now(), ok
        own.last_test_message = reason[:500]
        own.save(update_fields=["last_tested_at", "last_test_ok", "last_test_message",
                                "updated_at"])
        record_company_event(
            actor=actor, membership=membership, company=company,
            action="mail_settings.tested", obj=own, after={"to": to, "ok": ok, "result": reason},
        )
    if not ok:
        raise MailSettingsError(reason)
    return to
