"""Email one finalised payslip - composed like an email, sent through the
company's own mail account.

"Email to employee" opens a compose page: From (the company's sender - its
name can be changed), To (the employee's address on file, or any address
typed), a Subject and a Message already written and free to change, and the
payslip PDF attached. Nihal, 2026-09-24.

The mail goes out through the company's account from Organisation → Email
settings (``organization.mail_settings``); with none saved, through the
server's account in .env as before; with neither, nothing is sent and the page
says why, in words on the page - not in a tooltip.

Only a **finalised** payslip is sent: a draft can still change.
"""

from django import forms
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage
from django.utils import timezone

from auditlog.services import record_company_event
from common.forms import StyledFormMixin
from common.tenant import use_company
from organization import mail_settings as mail
from organization.services import require_company_membership
from payroll.models import PayrollRun
from payroll.payslip_export import build_payslip_pdf


def mail_is_configured(company_id):
    """Can this company send email at all - its own account, or the server's?"""
    return mail.sender(company_id)[0] is not None


def address_for(employee):
    """The address on their record: their work address, else their personal one."""
    return (employee.work_email or employee.personal_email or "").strip()


def why_not(record, employee):
    """Why this payslip cannot be emailed right now, or None. Said on the page."""
    if not mail_is_configured(record.company_id):
        return mail.not_set_up_reason()
    if record.payroll_run.status != PayrollRun.Status.POSTED:
        return "Only a finalised payslip can be emailed; this month is not finalised yet."
    return None


def default_subject(context, company):
    return f"Payslip — {context['period'].name} — {company.name}"


def default_body(context, company):
    record = context["record"]
    return (
        f"Dear {record.employee.full_name},\n\n"
        f"Your payslip for {context['period'].name} is attached.\n\n"
        f"Net pay: {record.net_pay:,.2f} {record.currency or company.currency}\n\n"
        "You can also see it under My payslips when you sign in.\n\n"
        f"{company.name}"
    )


def _one_line(value, label):
    if "\n" in value or "\r" in value:
        raise ValidationError(f"{label} must be on one line.")
    return value


class PayslipEmailForm(StyledFormMixin, forms.Form):
    from_name = forms.CharField(
        label="From (name)", max_length=120, required=False,
        help_text="The name the employee sees as the sender.")
    to = forms.EmailField(label="To", help_text="Any address - the employee's own is filled in.")
    subject = forms.CharField(label="Subject", max_length=200)
    body = forms.CharField(label="Message", max_length=5000,
                           widget=forms.Textarea(attrs={"rows": 10}))

    def clean_from_name(self):
        return _one_line(self.cleaned_data["from_name"].strip(), "The sender name")

    def clean_subject(self):
        return _one_line(self.cleaned_data["subject"].strip(), "The subject")


def compose_initial(context, company):
    """What the compose page starts with."""
    _how, _address, name = mail.sender(company.pk)
    return {
        "from_name": name or company.name,
        "to": address_for(context["record"].employee),
        "subject": default_subject(context, company),
        "body": default_body(context, company),
    }


def email_payslip(*, actor, company_id, context, sent_by="", to=None, subject=None,
                  body=None, from_name=None):
    """Send the payslip in ``context``, the PDF attached. Returns the address.

    ``to``, ``subject``, ``body`` and ``from_name`` come from the compose page;
    left out, the employee's own address and the standard wording are used.
    """
    record = context["record"]
    employee = record.employee
    period = context["period"]
    membership = require_company_membership(actor, company_id)
    refusal = why_not(record, employee)
    if refusal:
        raise ValidationError(refusal)

    company = membership.company
    to = (address_for(employee) if to is None else to).strip()
    if not to:
        raise ValidationError(
            f"{employee.full_name} has no email address on their record. "
            "Type the address to send it to.")
    forms.EmailField().clean(to)
    how, from_email, saved_name = mail.sender(company_id)
    name = (saved_name or company.name) if from_name is None else from_name
    subject = subject or default_subject(context, company)
    content, filename = build_payslip_pdf(context, company=company, by=sent_by)
    message = EmailMessage(
        subject=subject,
        body=body or default_body(context, company),
        from_email=mail.from_header(from_email, name),
        to=[to],
    )
    message.attach(filename, content, "application/pdf")
    mail.send(message, mail.connection_for(company_id) if how == "company" else None)

    with use_company(company_id):
        record_company_event(
            actor=actor, membership=membership, company=company,
            action="payslip.emailed", obj=record,
            after={"to": to, "employee": employee.pk, "period": period.name,
                   "subject": subject, "from": message.from_email, "through": how,
                   "sent_at": timezone.now().isoformat()},
        )
    return to
