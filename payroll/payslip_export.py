"""One payslip as a PDF (``?format=pdf`` on the payslip page).

Served by the payslip's own view - the company's page and the employee's own -
so a download can only hold a payslip that viewer may already open, and the
employee's own page keeps its rule of finalised months only.

Built through ``common.exports``: the same bundled font, page furniture and
audit line as every other download here, which is also why a Bangla name
prints properly.
"""

from django.utils import timezone

from common import exports
from common.tenant import use_company
from organization.services import require_company_membership
from payroll.services import money

#: The day counts worth printing, in the order a payslip reads them.
COUNTS = (
    ("present", "Present"),
    ("half_day", "Half days"),
    ("absent", "Absent"),
    ("paid_leave", "Paid leave"),
    ("unpaid_leave", "Unpaid leave"),
    ("holiday", "Holidays"),
    ("weekly_off", "Weekly offs"),
    ("incomplete", "Incomplete"),
)


def _amount(value, currency):
    return f"{money(value):,.2f} {currency}"


def build_payslip_pdf(context, *, company, by=""):
    """``(pdf bytes, filename)`` for one payslip. No permissions, no audit:
    the callers - the download and the email - do those."""
    record = context["record"]
    period = context["period"]
    assignment = context["assignment"]
    currency = record.currency or company.currency
    counts = context["counts"]

    details = [
        ["Employee", record.employee.full_name],
        ["Employee ID", assignment.employee_code if assignment else ""],
        ["Designation", assignment.designation.name if assignment and assignment.designation_id else ""],
        ["Department", assignment.department.name if assignment and assignment.department_id else ""],
        ["Branch", assignment.branch.name if assignment and assignment.branch_id else ""],
        ["Period", period.name],
        ["Pay basis", (record.calculation_snapshot or {}).get("pay_basis", "").capitalize()],
        ["Status", record.payroll_run.get_status_display()],
    ]
    money_columns = (2, 3, 4)
    headers = ["Code", "Description", "Quantity", "Rate", "Amount"]

    def lines_of(kind):
        return [[line.code, line.description,
                 line.quantity or "", line.rate or "",
                 _amount(line.amount, currency)]
                for line in context[kind]]

    attendance = [[label, counts.get(key, 0)] for key, label in COUNTS if counts.get(key)]
    if counts.get("late_minutes"):
        attendance.append(["Late minutes", counts["late_minutes"]])
    if counts.get("overtime_minutes"):
        attendance.append(["Approved overtime minutes", counts["overtime_minutes"]])

    penalties = [[p.penalty_rule.name if p.penalty_rule_id else "Penalty",
                  p.get_status_display(), _amount(p.amount, currency)]
                 for p in context.get("penalties", [])]

    sections = [
        ("", [], details, ()),
        ("Earnings", headers, lines_of("earnings"), money_columns),
        ("Deductions", headers, lines_of("deductions"), money_columns),
        ("Attendance", [], attendance, (1,)),
        ("Penalties", ["Rule", "Status", "Amount"], penalties, (2,)),
    ]
    summary = [
        ("Gross earnings", _amount(record.gross_earnings, currency)),
        ("Total deductions", _amount(record.total_deductions, currency)),
        ("Net pay", _amount(record.net_pay, currency)),
    ]
    now = timezone.localtime()
    note = (
        "This payslip is a draft and can still change."
        if record.payroll_run.status != "posted" else
        f"Finalised {timezone.localtime(record.payroll_run.posted_at):%d %b %Y}."
        if record.payroll_run.posted_at else ""
    )
    content = exports.document_pdf(
        title=f"{company.name} — Payslip",
        lines=[f"{record.employee.full_name} · {period.name}",
               f"Prepared {now:%d %b %Y %H:%M}" + (f" by {by}" if by else "")],
        sections=sections, summary=summary, note=note,
    )
    return content, payslip_filename(record, period)


def payslip_filename(record, period):
    """What a payslip PDF is called - the download and the email attachment."""
    return exports.filename("payslip", record.employee.full_name,
                            f"{period.start_date:%Y-%m}", exports.PDF)


def export_payslip(request, context):
    """The payslip in ``context`` (payroll.views.payslip_context) as a download."""
    record = context["record"]
    period = context["period"]
    membership = require_company_membership(request.user, request.company_id)
    content, name = build_payslip_pdf(context, company=membership.company,
                                      by=request.user.get_username())
    with use_company(membership.company.pk):
        exports.record(
            actor=request.user, membership=membership, page="payslip",
            fmt=exports.PDF, count=1,
            filters={"payslip": record.pk, "employee": record.employee_id,
                     "period": period.name},
        )
    return exports.response(content, exports.PDF, name)
