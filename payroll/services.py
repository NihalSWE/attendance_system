"""Monthly salary, from attendance and the company's salary rules.

Generating a month first recalculates its attendance, then writes one draft
``PayrollRecord`` per employee with readable lines. How each line is worked
out comes from the company's salary rules for that month (``payroll.policy``);
a company that never changed them gets the standard rules agreed on
2026-09-12:

- **Monthly**: base − (absent + unpaid-leave days, half days counting 0.5)
  × base ÷ 30. Weekly offs, holidays and paid leave are not deducted; an
  unpaid holiday or weekly off is.
- **Daily**: rate × payable working days (present 1, half day 0.5, paid leave
  1). Weekly offs and holidays are not paid.
- **Hourly**: rate × hours worked, plus paid leave hours.

The rate is the compensation in force at the end of the month (proration for a
mid-month change is on the plan, step A11). Runs stay **draft** and can be
regenerated at any time — finalising is not built yet.
"""

import datetime
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from attendance.models import AttendanceRecord
from attendance.services import calculate_attendance, month_bounds
from auditlog.services import record_company_event
from common.tenant import use_company
from employees.models import EmployeeCompensation
from organization.services import require_structure_manager
from payroll.models import (
    PayrollLine,
    PayrollPeriod,
    PayrollPolicyVersion,
    PayrollRecord,
    PayrollRun,
    PayrollSettings,
)
from payroll.policy import STANDARD_RULES, plain, rules_for

CENT = Decimal("0.01")
ONE = Decimal("1")
Status = AttendanceRecord.AttendanceStatus
WORKING_STATUSES = (Status.PRESENT, Status.HALF_DAY, Status.LEAVE, Status.INCOMPLETE, Status.ABSENT)
Method = PayrollPolicyVersion.MonthlyProration
Absence = PayrollPolicyVersion.AbsenceDeduction


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def _compensation_at(employee, at):
    return (
        EmployeeCompensation.objects.filter(employee=employee, effective_from__lte=at)
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=at))
        .exclude(status__in=["cancelled", "draft"])
        .order_by("-effective_from")
        .first()
    )


def summarise(records):
    """Day counts for one employee's month, as shown on the payslip."""
    counts = Counter()
    worked_minutes = 0
    paid_leave_minutes = 0
    for record in records:
        worked_minutes += record.worked_minutes
        status = record.attendance_status
        if status == Status.LEAVE:
            if record.payable_fraction > 0:
                counts["paid_leave"] += 1
                paid_leave_minutes += record.leave_day.leave_minutes if record.leave_day else 0
            else:
                counts["unpaid_leave"] += 1
        elif status in (Status.HOLIDAY, Status.WEEKLY_OFF):
            counts[status] += 1
            if record.payable_fraction == 0:
                counts["unpaid_off"] += 1
        else:
            counts[status] += 1
        counts["late_minutes"] += record.late_minutes
    counts["worked_minutes"] = worked_minutes
    counts["paid_leave_minutes"] = paid_leave_minutes
    return counts


def expected_minutes(record):
    """Minutes of work the day's shift expects: its length less an unpaid break."""
    shift = getattr(record, "shift", None)
    if shift is None:
        return 0
    minutes = shift.scheduled_minutes
    if not shift.break_is_paid:
        minutes -= shift.default_break_minutes
    return max(minutes, 0)


def _percent(fraction):
    return f"{plain(fraction * 100)}%"


def _per_day(rules, rate, records, days_in_month):
    """One day's pay for a monthly salary, by the company's chosen method."""
    if rules.per_day_method == Method.FIXED_DIVISOR:
        return rate / rules.divisor
    if rules.per_day_method == Method.CALENDAR_DAYS:
        return rate / Decimal(days_in_month)
    if rules.per_day_method == Method.SCHEDULED_WORKDAYS:
        workdays = sum(1 for r in records if r.attendance_status in WORKING_STATUSES)
        return rate / Decimal(workdays) if workdays else Decimal("0")
    return Decimal("0")


def _monthly_deductions(rules, per_day, records):
    """Deduction lines for a monthly salary. Leave and unpaid days off are
    always deducted; absence follows the company's absence method."""
    lines = []

    def add(code, label, quantity, amount=None):
        if quantity:
            value = per_day * quantity if amount is None else amount
            lines.append(("deduction", code, label, quantity, per_day, money(value)))

    absent = Decimal(sum(1 for r in records if r.attendance_status == Status.ABSENT))
    half_days = [r for r in records if r.attendance_status == Status.HALF_DAY]
    incomplete = Decimal(sum(1 for r in records if r.attendance_status == Status.INCOMPLETE))
    unpaid_leave = sum(
        (ONE - r.payable_fraction for r in records if r.attendance_status == Status.LEAVE),
        Decimal("0"),
    )
    unpaid_off = Decimal(sum(
        1 for r in records
        if r.attendance_status in (Status.HOLIDAY, Status.WEEKLY_OFF) and r.payable_fraction == 0
    ))

    if rules.absence_method == Absence.DAY_FRACTION:
        add("ABSENT", "Absent days", absent)
        add(
            "HALF_DAY",
            f"Half days ({len(half_days)}, {_percent(ONE - rules.half_day_pay)} unpaid each)",
            Decimal(len(half_days)) * (ONE - rules.half_day_pay),
        )
        add("INCOMPLETE", "Incomplete days (no check-out)", incomplete * (ONE - rules.incomplete_pay))
    elif rules.absence_method == Absence.SCHEDULED_MINUTES:
        add("ABSENT", "Absent days", absent)
        short_minutes, short_amount = 0, Decimal("0")
        for record in records:
            if record.attendance_status not in (Status.PRESENT, Status.HALF_DAY):
                continue
            expected = expected_minutes(record)
            short = max(0, expected - record.worked_minutes)
            if expected and short:
                short_minutes += short
                short_amount += per_day * Decimal(short) / Decimal(expected)
        if short_minutes:
            lines.append((
                "deduction", "SHORT_MINUTES", "Minutes short of the shift",
                Decimal(short_minutes), short_amount / Decimal(short_minutes), money(short_amount),
            ))
        add("INCOMPLETE", "Incomplete days (no check-out)", incomplete * (ONE - rules.incomplete_pay))
    # Absence.RULE_ONLY: absence is left to penalty rules.

    add("UNPAID_LEAVE", "Unpaid leave days", unpaid_leave)
    add("UNPAID_OFF", "Unpaid holidays / weekly offs", unpaid_off)
    return lines


def _day_pay(record, rules):
    """How much of a day a daily-rate employee is paid for."""
    status = record.attendance_status
    if status == Status.HALF_DAY:
        return rules.half_day_pay
    if status == Status.INCOMPLETE:
        return rules.incomplete_pay
    if status in (Status.PRESENT, Status.LEAVE):
        return record.payable_fraction
    return Decimal("0")


def calculate_pay(pay_basis, rate, records, rules=None, days_in_month=30):
    """Lines and totals for one employee. Pure: no database writes."""
    rules = rules or STANDARD_RULES
    counts = summarise(records)
    rate = Decimal(rate)
    lines = []
    paid_off = [
        r for r in records
        if r.attendance_status in (Status.HOLIDAY, Status.WEEKLY_OFF) and r.payable_fraction > 0
    ]

    if pay_basis == EmployeeCompensation.PayBasis.MONTHLY:
        lines.append(("earning", "BASIC", "Basic salary", ONE, rate, money(rate)))
        per_day = _per_day(rules, rate, records, days_in_month)
        if per_day:
            lines.extend(_monthly_deductions(rules, per_day, records))
    elif pay_basis == EmployeeCompensation.PayBasis.DAILY:
        payable = sum((_day_pay(r, rules) for r in records), Decimal("0"))
        lines.append(("earning", "DAYS", "Payable days worked", payable, rate, money(payable * rate)))
        if rules.daily_paid_days_off and paid_off:
            days_off = Decimal(len(paid_off))
            lines.append((
                "earning", "PAID_OFF", "Paid holidays and weekly offs",
                days_off, rate, money(days_off * rate),
            ))
    else:
        hours = Decimal(counts["worked_minutes"]) / Decimal("60")
        lines.append(("earning", "HOURS", "Hours worked", money(hours), rate, money(hours * rate)))
        incomplete_minutes = sum(
            (Decimal(expected_minutes(r)) * rules.incomplete_pay
             for r in records if r.attendance_status == Status.INCOMPLETE),
            Decimal("0"),
        )
        if incomplete_minutes:
            incomplete_hours = incomplete_minutes / Decimal("60")
            lines.append((
                "earning", "INCOMPLETE", "Incomplete days (shift hours, no check-out)",
                money(incomplete_hours), rate, money(incomplete_hours * rate),
            ))
        leave_hours = Decimal(counts["paid_leave_minutes"]) / Decimal("60")
        if leave_hours:
            lines.append(("earning", "PAID_LEAVE", "Paid leave hours", money(leave_hours), rate, money(leave_hours * rate)))
        if rules.hourly_paid_days_off and paid_off:
            off_hours = Decimal(sum(expected_minutes(r) for r in paid_off)) / Decimal("60")
            if off_hours:
                lines.append((
                    "earning", "PAID_OFF", "Paid holidays and weekly offs (shift hours)",
                    money(off_hours), rate, money(off_hours * rate),
                ))

    gross = sum((amount for kind, *_, amount in lines if kind == "earning"), Decimal("0"))
    deductions = sum((amount for kind, *_, amount in lines if kind == "deduction"), Decimal("0"))
    if not rules.allow_negative:
        # A month of absences cannot produce a negative salary.
        deductions = min(deductions, gross)
    net = money(gross - deductions)
    rounded = rules.round_net(net)
    if rounded != net:
        difference = rounded - net
        kind = "earning" if difference > 0 else "deduction"
        lines.append((kind, "ROUNDING", "Rounding", ONE, abs(difference), abs(difference)))
        if difference > 0:
            gross += difference
        else:
            deductions -= difference
    return {
        "lines": lines,
        "gross": money(gross),
        "deductions": money(deductions),
        "net": money(gross - deductions),
        "counts": dict(counts),
        "rules": rules.describe(),
    }


@transaction.atomic
def generate_payroll(*, actor, company_id, year, month):
    """Recalculate the month's attendance, then (re)build its draft salary run."""
    membership = require_structure_manager(actor, company_id)
    calculate_attendance(actor=actor, company_id=company_id, year=year, month=month)
    first, last = month_bounds(year, month)
    period_end = timezone.make_aware(
        datetime.datetime.combine(last, datetime.time.max)
    )

    rules = rules_for(company_id, first)

    with use_company(company_id):
        settings = PayrollSettings.objects.first()
        default_currency = settings.currency if settings else membership.company.currency
        period, _ = PayrollPeriod.objects.get_or_create(
            company=membership.company, start_date=first, end_date=last,
            defaults={"name": first.strftime("%B %Y"), "created_by": actor, "updated_by": actor},
        )
        if PayrollRun.objects.filter(payroll_period=period, status=PayrollRun.Status.POSTED).exists():
            raise ValidationError("This month's salary is finalised and cannot be regenerated.")

        run = PayrollRun.objects.filter(payroll_period=period, status=PayrollRun.Status.DRAFT).first()
        if run is None:
            run = PayrollRun.objects.create(
                company=membership.company, payroll_period=period,
                created_by=actor, updated_by=actor,
            )
        else:
            # Regenerating a draft replaces it entirely.
            PayrollLine.objects.filter(payroll_record__payroll_run=run).delete()
            run.records.all().delete()

        records_by_employee = {}
        for record in (
            AttendanceRecord.objects.select_related(
                "employee", "employee_assignment", "leave_day", "shift"
            )
            .filter(work_date__gte=first, work_date__lte=last)
            .order_by("employee_id", "work_date")
        ):
            records_by_employee.setdefault(record.employee, []).append(record)

        totals = Counter()
        skipped = []
        for employee, records in records_by_employee.items():
            compensation = _compensation_at(employee, period_end) or _compensation_at(
                employee, timezone.make_aware(datetime.datetime.combine(records[-1].work_date, datetime.time.max))
            )
            if compensation is None:
                skipped.append(employee.full_name)
                continue
            result = calculate_pay(
                compensation.pay_basis, compensation.base_rate, records,
                rules=rules, days_in_month=last.day,
            )
            payroll_record = PayrollRecord.objects.create(
                company=membership.company, payroll_run=run, employee=employee,
                employee_assignment_at_period_end=records[-1].employee_assignment,
                currency=compensation.currency or default_currency,
                gross_earnings=result["gross"], total_deductions=result["deductions"],
                net_pay=result["net"],
                calculation_snapshot={
                    "pay_basis": compensation.pay_basis,
                    "base_rate": str(compensation.base_rate),
                    "compensation_id": compensation.pk,
                    "counts": result["counts"],
                    "rules": result["rules"],
                },
            )
            PayrollLine.objects.bulk_create([
                PayrollLine(
                    company=membership.company, payroll_record=payroll_record,
                    line_type=kind, code=code, description=label,
                    quantity=money(quantity), rate=rate, amount=amount, sequence=index,
                )
                for index, (kind, code, label, quantity, rate, amount) in enumerate(result["lines"])
            ])
            totals["employees"] += 1
            totals["gross"] += result["gross"]
            totals["deductions"] += result["deductions"]
            totals["net"] += result["net"]

        run.generated_by = actor
        run.policy_version = rules.version
        run.calculation_finished_at = timezone.now()
        run.totals_snapshot = {
            "employees": totals["employees"],
            "gross": str(money(totals["gross"])),
            "deductions": str(money(totals["deductions"])),
            "net": str(money(totals["net"])),
            "skipped_without_salary": skipped,
            "rules": rules.describe(),
        }
        run.updated_by = actor
        run.save()
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="payroll.generated", obj=run, after=run.totals_snapshot,
        )
    return run
