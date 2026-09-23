"""Email one finalised payslip to the person it belongs to.

Off until a mail server is set in .env (``MAIL_CONFIGURED``): with nothing
configured the button is not offered and the service refuses, rather than
reporting success for a message nobody receives.

Only a **finalised** payslip is sent - a draft can still change, and it is not
the employee's to see yet - and only to the address on their own record.
"""

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import EmailMessage
from django.utils import timezone

from auditlog.services import record_company_event
from common.tenant import use_company
from organization.services import require_company_membership
from payroll.models import PayrollRun
from payroll.payslip_export import build_payslip_pdf


def mail_is_configured():
    return bool(getattr(settings, "MAIL_CONFIGURED", False))


def address_for(employee):
    """Where a payslip would go: their work address, else their personal one."""
    return (employee.work_email or employee.personal_email or "").strip()


def why_not(record, employee):
    """Why this payslip cannot be emailed right now, or None.

    Said on the page beside the button, not discovered after a click.
    """
    if not mail_is_configured():
        return ("Email is not set up on this server yet, so payslips cannot be "
                "sent. Add the company's mail account to the .env file.")
    if record.payroll_run.status != PayrollRun.Status.POSTED:
        return "Only a finalised payslip can be emailed; this month is not finalised yet."
    if not address_for(employee):
        return f"{employee.full_name} has no email address on their record."
    return None


def email_payslip(*, actor, company_id, context, sent_by=""):
    """Send the payslip in ``context`` to its employee. Returns the address."""
    record = context["record"]
    employee = record.employee
    period = context["period"]
    membership = require_company_membership(actor, company_id)
    refusal = why_not(record, employee)
    if refusal:
        raise ValidationError(refusal)

    company = membership.company
    content, name = build_payslip_pdf(context, company=company, by=sent_by)
    address = address_for(employee)
    message = EmailMessage(
        subject=f"Payslip — {period.name} — {company.name}",
        body=(
            f"Dear {employee.full_name},\n\n"
            f"Your payslip for {period.name} is attached.\n\n"
            f"Net pay: {record.net_pay:,.2f} {record.currency or company.currency}\n\n"
            "You can also see it under My payslips when you sign in.\n\n"
            f"{company.name}"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[address],
    )
    message.attach(name, content, "application/pdf")
    message.send()

    with use_company(company_id):
        record_company_event(
            actor=actor, membership=membership, company=company,
            action="payslip.emailed", obj=record,
            after={"to": address, "employee": employee.pk, "period": period.name,
                   "sent_at": timezone.now().isoformat()},
        )
    return address
