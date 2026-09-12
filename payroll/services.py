"""Basic monthly salary, from attendance.

Thin slice of the 2026-09-12 salary fast-track. Generating a month first
recalculates its attendance, then writes one draft ``PayrollRecord`` per
employee with readable lines. The formula agreed on 2026-09-12:

- **Monthly**: base − (absent + unpaid-leave days, half days counting 0.5)
  × base ÷ 30. Weekly offs, holidays and paid leave are not deducted; an
  unpaid holiday or weekly off is.
- **Daily**: rate × payable working days (present 1, half day 0.5, paid leave
  1). Weekly offs and holidays are not paid.
- **Hourly**: rate × hours worked, plus paid leave hours.

The rate is the compensation in force at the end of the month (proration for a
mid-month change is on the skipped list). Runs stay **draft** and can be
regenerated at any time — finalising is not built today.
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
from payroll.models import PayrollLine, PayrollPeriod, PayrollRecord, PayrollRun

CENT = Decimal("0.01")
DAYS_IN_MONTH_BASIS = Decimal("30")
Status = AttendanceRecord.AttendanceStatus
WORKING_STATUSES = (Status.PRESENT, Status.HALF_DAY, Status.LEAVE, Status.INCOMPLETE, Status.ABSENT)


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


def calculate_pay(pay_basis, rate, records):
    """Lines and totals for one employee. Pure: no database writes."""
    counts = summarise(records)
    rate = Decimal(rate)
    lines = []

    if pay_basis == EmployeeCompensation.PayBasis.MONTHLY:
        per_day = rate / DAYS_IN_MONTH_BASIS
        lines.append(("earning", "BASIC", "Basic salary", Decimal("1"), rate, money(rate)))
        for code, label, days in (
            ("ABSENT", "Absent days", Decimal(counts[Status.ABSENT])),
            ("UNPAID_LEAVE", "Unpaid leave days", Decimal(counts["unpaid_leave"])),
            ("HALF_DAY", "Half days (0.5 each)", Decimal(counts[Status.HALF_DAY]) * Decimal("0.5")),
            ("UNPAID_OFF", "Unpaid holidays / weekly offs", Decimal(counts["unpaid_off"])),
        ):
            if days:
                lines.append(("deduction", code, label, days, per_day, money(days * per_day)))
    elif pay_basis == EmployeeCompensation.PayBasis.DAILY:
        payable = sum(
            (r.payable_fraction for r in records if r.attendance_status in WORKING_STATUSES),
            Decimal("0"),
        )
        lines.append(("earning", "DAYS", "Payable days worked", payable, rate, money(payable * rate)))
    else:
        hours = Decimal(counts["worked_minutes"]) / Decimal("60")
        lines.append(("earning", "HOURS", "Hours worked", money(hours), rate, money(hours * rate)))
        leave_hours = Decimal(counts["paid_leave_minutes"]) / Decimal("60")
        if leave_hours:
            lines.append(("earning", "PAID_LEAVE", "Paid leave hours", money(leave_hours), rate, money(leave_hours * rate)))

    gross = sum((amount for kind, *_, amount in lines if kind == "earning"), Decimal("0"))
    deductions = sum((amount for kind, *_, amount in lines if kind == "deduction"), Decimal("0"))
    # A month of absences cannot produce a negative salary.
    deductions = min(deductions, gross)
    return {
        "lines": lines,
        "gross": money(gross),
        "deductions": money(deductions),
        "net": money(gross - deductions),
        "counts": dict(counts),
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

    with use_company(company_id):
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
            AttendanceRecord.objects.select_related("employee", "employee_assignment", "leave_day")
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
            result = calculate_pay(compensation.pay_basis, compensation.base_rate, records)
            payroll_record = PayrollRecord.objects.create(
                company=membership.company, payroll_run=run, employee=employee,
                employee_assignment_at_period_end=records[-1].employee_assignment,
                currency=compensation.currency or "BDT",
                gross_earnings=result["gross"], total_deductions=result["deductions"],
                net_pay=result["net"],
                calculation_snapshot={
                    "pay_basis": compensation.pay_basis,
                    "base_rate": str(compensation.base_rate),
                    "compensation_id": compensation.pk,
                    "counts": result["counts"],
                    "formula": "fast-track 2026-09-12 (monthly: base/30 per deducted day)",
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
        run.calculation_finished_at = timezone.now()
        run.totals_snapshot = {
            "employees": totals["employees"],
            "gross": str(money(totals["gross"])),
            "deductions": str(money(totals["deductions"])),
            "net": str(money(totals["net"])),
            "skipped_without_salary": skipped,
        }
        run.updated_by = actor
        run.save()
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="payroll.generated", obj=run, after=run.totals_snapshot,
        )
    return run
