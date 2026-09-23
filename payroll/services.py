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

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from access_control.branch_access import ALL_BRANCHES, branches_for_any, can
from attendance.models import AttendanceRecord
from attendance.services import calculate_attendance, month_bounds, recalculate
from auditlog.services import record_company_event
from common.tenant import use_company
from employees.models import EmployeeAssignment, EmployeeCompensation
from organization.services import (
    STRUCTURE_ROLES,
    require_company_membership,
    require_structure_manager,
)
from scheduling.calendar import WorkCalendar
from payroll.models import (
    PayrollAdjustment,
    PayrollLine,
    PenaltyAssessment,
    PenaltyAssessmentAttendance,
    PayrollPeriod,
    PayrollPolicyVersion,
    PayrollRecord,
    PayrollRun,
    PayrollSettings,
)
from payroll.penalties import PayValue, assess, rules_in_force
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


def _monthly_segments(employee, start, end):
    """(rate, first day, last day) for each monthly salary in force between two dates.

    A11 part 4, kept simple: None unless the salary changed inside the range and
    every salary in it is monthly. Daily/hourly changes keep the month-end rate.
    ``revise_compensation`` closes the old row at the instant the new one starts.
    Call inside the company's tenant context.
    """
    begin = timezone.make_aware(datetime.datetime.combine(start, datetime.time.min))
    finish = timezone.make_aware(datetime.datetime.combine(end, datetime.time.max))
    compensations = list(
        EmployeeCompensation.objects.filter(employee=employee, effective_from__lte=finish)
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=begin))
        .exclude(status__in=["cancelled", "draft"])
        .order_by("effective_from")
    )
    if len(compensations) < 2 or any(
        c.pay_basis != EmployeeCompensation.PayBasis.MONTHLY for c in compensations
    ):
        return None
    segments, next_free = [], start
    for compensation in compensations:
        first = max(next_free, timezone.localtime(compensation.effective_from).date())
        last = end
        if compensation.effective_to is not None:
            ends = timezone.localtime(compensation.effective_to) - datetime.timedelta(microseconds=1)
            last = min(end, ends.date())
        if last >= first:
            segments.append((compensation.base_rate, first, last))
            next_free = last + datetime.timedelta(days=1)
    return segments if len(segments) > 1 else None


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
        leave_day = getattr(record, "leave_day", None)
        if status != Status.LEAVE and leave_day is not None and leave_day.approved_pay_type == "paid":
            # A paid half-day leave on a day the employee also worked.
            paid_leave_minutes += leave_day.leave_minutes
        counts["late_minutes"] += record.late_minutes
        counts["overtime_minutes"] += getattr(record, "approved_overtime_minutes", 0)
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
    # Leave days, and days worked with a half-day leave (unpaid half = 0.5).
    unpaid_leave = sum(
        (ONE - r.payable_fraction for r in records
         if r.attendance_status == Status.LEAVE or getattr(r, "leave_day", None) is not None),
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
            leave_day = getattr(record, "leave_day", None)
            # The half on leave is not "short": it is leave, paid or deducted above.
            expected = max(0, expected_minutes(record) - (leave_day.leave_minutes if leave_day else 0))
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


def shift_hours(record):
    """Paid hours of the day's shift, for turning a day's pay into an hour's."""
    minutes = expected_minutes(record) or getattr(getattr(record, "shift", None), "scheduled_minutes", 0)
    return Decimal(minutes or 480) / Decimal("60")


def _overtime_lines(pay_basis, rate, rules, records, days_in_month):
    """Earning lines for approved overtime, and the per-day evidence.

    Each day's approved minutes pass the minimum and rounding, then pay
    hours × hourly rate × multiplier: the day-off multiplier on a holiday or
    weekly off, the overtime one otherwise. The hourly rate is the employee's
    own for hourly staff; a day's pay ÷ the shift's hours for daily and
    monthly staff (a monthly day is worked out as the absence deduction is).
    """
    if not rules.pays_overtime:
        return [], []
    per_day = None
    if pay_basis == EmployeeCompensation.PayBasis.MONTHLY:
        per_day = _per_day(rules, rate, records, days_in_month) or rate / rules.divisor
    groups = {}
    details = []
    for record in records:
        approved = getattr(record, "approved_overtime_minutes", 0)
        paid = rules.payable_overtime(approved)
        if not paid:
            continue
        if pay_basis == EmployeeCompensation.PayBasis.HOURLY:
            hourly = rate
        elif pay_basis == EmployeeCompensation.PayBasis.DAILY:
            hourly = rate / shift_hours(record)
        else:
            hourly = per_day / shift_hours(record)
        day_off = record.attendance_status in (Status.HOLIDAY, Status.WEEKLY_OFF)
        multiplier = rules.day_off_multiplier if day_off else rules.overtime_multiplier
        amount = hourly * multiplier * Decimal(paid) / Decimal("60")
        group = groups.setdefault(day_off, {"days": 0, "minutes": 0, "amount": Decimal("0")})
        group["days"] += 1
        group["minutes"] += paid
        group["amount"] += amount
        details.append({
            "date": record.work_date.isoformat(),
            "approved_minutes": approved,
            "paid_minutes": paid,
            "hourly_rate": str(money(hourly)),
            "multiplier": plain(multiplier),
            "day_off": day_off,
            "amount": str(money(amount)),
        })
    lines = []
    for day_off, code, label, multiplier in (
        (False, "OVERTIME", "Overtime", rules.overtime_multiplier),
        (True, "DAY_OFF_WORK", "Work on holidays / weekly offs", rules.day_off_multiplier),
    ):
        group = groups.get(day_off)
        if not group:
            continue
        hours = Decimal(group["minutes"]) / Decimal("60")
        days = group["days"]
        lines.append((
            "earning", code,
            f"{label} ({days} day{'s' if days != 1 else ''}, {plain(multiplier)}× the hourly rate)",
            money(hours), group["amount"] / hours, money(group["amount"]),
        ))
    return lines, details


def calculate_pay(pay_basis, rate, records, rules=None, days_in_month=30,
                  penalty_rules=(), waived=frozenset(), employee_key="", adjustments=(),
                  employed_days=None, basic_segments=None):
    """Lines and totals for one employee. Pure: no database writes.

    ``penalty_rules`` add one deduction line per penalty found; the
    penalties themselves come back in ``result["penalties"]`` as
    ``(line index, Occurrence)`` so the caller can store them. Approved
    overtime (``AttendanceRecord.approved_overtime_minutes``) adds earning
    lines; the per-day working comes back in ``result["overtime"]``.
    """
    rules = rules or STANDARD_RULES
    counts = summarise(records)
    rate = Decimal(rate)
    lines = []
    paid_off = [
        r for r in records
        if r.attendance_status in (Status.HOLIDAY, Status.WEEKLY_OFF) and r.payable_fraction > 0
    ]

    if pay_basis == EmployeeCompensation.PayBasis.MONTHLY:
        if basic_segments:
            # The monthly salary changed inside the month (A11 part 4): one Basic
            # line per rate for the calendar days it was in force.
            for segment_rate, segment_first, segment_last in basic_segments:
                days = (segment_last - segment_first).days + 1
                share = Decimal(days) / Decimal(days_in_month)
                segment_rate = Decimal(segment_rate)
                lines.append((
                    "earning", "BASIC",
                    f"Basic salary {segment_rate:,.2f} ({segment_first:%d %b}–{segment_last:%d %b}, "
                    f"{days} of {days_in_month} days)",
                    share, segment_rate, money(segment_rate * share),
                ))
        elif employed_days is not None and employed_days < days_in_month:
            # Joined or left inside the month (A11 part 3): pay the employed
            # calendar days only.
            share = Decimal(employed_days) / Decimal(days_in_month)
            lines.append((
                "earning", "BASIC",
                f"Basic salary ({employed_days} of {days_in_month} days employed)",
                share, rate, money(rate * share),
            ))
        else:
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

    overtime_lines, overtime = _overtime_lines(pay_basis, rate, rules, records, days_in_month)
    lines.extend(overtime_lines)

    # One-time bonus and deduction lines added on the draft (A11 part 2).
    adjustment_lines = []
    for adjustment in adjustments:
        kind = "earning" if adjustment.adjustment_type == "earning" else "deduction"
        adjustment_lines.append((len(lines), adjustment.pk))
        source = getattr(adjustment, "source_payroll_period", None)
        lines.append((
            kind, "CORRECTION" if source else ("BONUS" if kind == "earning" else "DEDUCTION"),
            f"{adjustment.reason} (correction for {source.name})" if source
            else adjustment.reason,
            ONE, adjustment.amount, money(adjustment.amount),
        ))

    gross = sum((amount for kind, *_, amount in lines if kind == "earning"), Decimal("0"))

    penalties = []
    if penalty_rules:
        per_day = _per_day(rules, rate, records, days_in_month) if pay_basis == "monthly" else None
        if pay_basis == "monthly" and not per_day:
            # A penalty needs a day's value even when absence is not prorated.
            per_day = rate / rules.divisor
        found = assess(
            penalty_rules, records, PayValue(pay_basis, rate, per_day, expected_minutes),
            gross=gross, max_percent=rules.max_penalty_percent,
            waived=waived, employee_key=employee_key,
        )
        for occurrence in found:
            penalties.append((len(lines), occurrence))
            lines.append((
                "deduction", "PENALTY",
                f"{occurrence.rule.name} ({occurrence.describe_days()})",
                Decimal(len(occurrence.days)), occurrence.amount / len(occurrence.days),
                occurrence.amount,
            ))

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
        "penalties": penalties,
        "overtime": overtime,
        "adjustments": adjustment_lines,
    }


def _store_penalty(company, employee, period, occurrence, currency):
    """Save one penalty and the attendance days behind it."""
    rule = occurrence.rule
    assessment = PenaltyAssessment.objects.create(
        company=company, employee=employee, penalty_rule=rule, payroll_period=period,
        period_start=occurrence.first, period_end=occurrence.last,
        occurrence_identity=f"{employee.pk}:{occurrence.identity}",
        occurrence_count=len(occurrence.days),
        deduction_minutes=occurrence.minutes,
        deduction_day_fraction=occurrence.day_fraction,
        deduction_amount=occurrence.amount,
        currency=currency,
        calculation_details={
            "rule": f"{rule.code} v{rule.version}",
            "method": rule.deduction_method,
            "values": [str(value) for _, value in occurrence.days],
            **occurrence.details,
        },
        calculated_at=timezone.now(),
    )
    PenaltyAssessmentAttendance.objects.bulk_create([
        PenaltyAssessmentAttendance(
            company=company, penalty_assessment=assessment, attendance_record=record,
            sequence_number=number, qualifying_value=Decimal(value),
            reason_snapshot={
                "status": record.attendance_status, "late_minutes": record.late_minutes,
                "worked_minutes": record.worked_minutes,
            },
        )
        for number, (record, value) in enumerate(occurrence.days, start=1)
    ])
    return assessment


@transaction.atomic
def waive_penalty(*, actor, company_id, assessment_id):
    """Waive one proposed penalty, then regenerate that month's draft salary.

    The waiver is kept across regenerations: the same occurrence is not
    charged again while the draft is rebuilt.
    """
    membership = require_structure_manager(actor, company_id)
    with use_company(company_id):
        assessment = (
            # Lock only the penalty row: FOR UPDATE cannot reach the nullable
            # side of the outer join to its period.
            PenaltyAssessment.objects.select_for_update(of=("self",)).select_related("payroll_period")
            .filter(pk=assessment_id).first()
        )
        if assessment is None:
            raise PermissionDenied("Penalty not found in this company.")
        if assessment.status != PenaltyAssessment.Status.PROPOSED:
            raise ValidationError("Only a penalty on a draft salary can be waived.")
        assessment.status = PenaltyAssessment.Status.WAIVED
        assessment.approved_by = actor
        assessment.approved_at = timezone.now()
        assessment.save(update_fields=["status", "approved_by", "approved_at"])
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="penalty.waived", obj=assessment,
            before={"status": "proposed"},
            after={"status": "waived", "amount": str(assessment.deduction_amount)},
        )
        period = assessment.payroll_period
        employee_id = assessment.employee_id
    run = generate_payroll(
        actor=actor, company_id=company_id,
        year=period.start_date.year, month=period.start_date.month,
    )
    with use_company(company_id):
        return run.records.filter(employee_id=employee_id).first()


@transaction.atomic
def unwaive_penalty(*, actor, company_id, assessment_id):
    """Undo a waiver, then regenerate that month's draft salary.

    For a waiver given by mistake: the penalty goes back to proposed, so the
    next generation charges it again like any other. Only while the month is
    still a draft - a finalised month is put right with a correction instead.
    """
    membership = require_structure_manager(actor, company_id)
    with use_company(company_id):
        assessment = (
            PenaltyAssessment.objects.select_for_update(of=("self",)).select_related("payroll_period")
            .filter(pk=assessment_id).first()
        )
        if assessment is None:
            raise PermissionDenied("Penalty not found in this company.")
        if assessment.status != PenaltyAssessment.Status.WAIVED:
            raise ValidationError("That penalty is not waived.")
        period = assessment.payroll_period
        if period is not None and PayrollRun.objects.filter(
            payroll_period=period,
            status__in=(PayrollRun.Status.POSTED, PayrollRun.Status.SUBMITTED),
        ).exists():
            raise ValidationError(
                "That month's salary is no longer a draft, so the waiver cannot be "
                "undone. Put the month right with a correction instead."
            )
        assessment.status = PenaltyAssessment.Status.PROPOSED
        assessment.approved_by = None
        assessment.approved_at = None
        assessment.save(update_fields=["status", "approved_by", "approved_at"])
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="penalty.waiver_undone", obj=assessment,
            before={"status": "waived"},
            after={"status": "proposed", "amount": str(assessment.deduction_amount)},
        )
        employee_id = assessment.employee_id
    if period is None:
        return None
    run = generate_payroll(
        actor=actor, company_id=company_id,
        year=period.start_date.year, month=period.start_date.month,
    )
    with use_company(company_id):
        return run.records.filter(employee_id=employee_id).first()


def salary_branches(actor, company_id, *codes):
    """``(membership, branches)`` for the salary pages (A12 part 6).

    Owner and company admin: every branch, as before. Anyone else: the
    branches where they hold one of ``codes`` (a branch manager holds them
    all in their own branches). Finalising and salary settings are not
    branch permissions and stay with the owner and company admin.
    """
    membership = require_company_membership(actor, company_id)
    if membership.role in STRUCTURE_ROLES:
        return membership, ALL_BRANCHES
    return membership, branches_for_any(actor, company_id, *codes)


def record_branch_id(record):
    assignment = record.employee_assignment_at_period_end
    return assignment.branch_id if assignment else None


def _bring_branches_up_to_date(company_id, year, month, branch_ids):
    """``calculate_attendance`` for the people placed in some branches, for a
    branch's own salary: the same checks, without recalculating the company."""
    first, last = month_bounds(year, month)
    today = timezone.localdate()
    if first > today:
        raise ValidationError("That month has not started yet.")
    if not WorkCalendar(company_id, first, min(last, today)).has_any_shift:
        raise ValidationError(
            "Set up shifts under Shifts first: a shift for each department, or a "
            "company shift. Attendance is measured against the shift."
        )
    begin = timezone.make_aware(datetime.datetime.combine(first, datetime.time.min))
    finish = timezone.make_aware(datetime.datetime.combine(last, datetime.time.max))
    with use_company(company_id):
        employee_ids = set(
            EmployeeAssignment.objects.filter(branch_id__in=branch_ids, effective_from__lte=finish)
            .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=begin))
            .exclude(status="cancelled").values_list("employee_id", flat=True)
        )
    if employee_ids:
        recalculate(company_id, employee_ids=employee_ids, start=first, end=last)


@transaction.atomic
def generate_payroll(*, actor, company_id, year, month, branch_ids=None):
    """Recalculate the month's attendance, then (re)build its draft salary run.

    ``branch_ids`` (A12 part 6): a branch prepares its own salary. Only the
    people whose placement at the end of the month is in those branches are
    rebuilt; everybody else's draft payslip stays exactly as it was. The run
    stays one per month for the company, finalised once by the owner or admin.
    """
    if branch_ids is None:
        membership = require_structure_manager(actor, company_id)
        calculate_attendance(actor=actor, company_id=company_id, year=year, month=month)
    else:
        membership = require_company_membership(actor, company_id)
        branch_ids = {int(branch_id) for branch_id in branch_ids}
        if not branch_ids or not all(
            can(actor, company_id, "salary.prepare", branch_id) for branch_id in branch_ids
        ):
            raise PermissionDenied("You can generate salary only for branches where you may prepare it.")
        _bring_branches_up_to_date(company_id, year, month, branch_ids)
    first, last = month_bounds(year, month)
    period_end = timezone.make_aware(
        datetime.datetime.combine(last, datetime.time.max)
    )

    rules = rules_for(company_id, first)
    penalty_rules = rules_in_force(company_id, first)

    with use_company(company_id):
        settings = PayrollSettings.objects.first()
        default_currency = settings.currency if settings else membership.company.currency
        period, _ = PayrollPeriod.objects.get_or_create(
            company=membership.company, start_date=first, end_date=last,
            defaults={"name": first.strftime("%B %Y"), "created_by": actor, "updated_by": actor},
        )
        if PayrollRun.objects.filter(payroll_period=period, status=PayrollRun.Status.POSTED).exists():
            raise ValidationError("This month's salary is finalised and cannot be regenerated.")
        if PayrollRun.objects.filter(payroll_period=period, status=PayrollRun.Status.SUBMITTED).exists():
            raise ValidationError(
                "This month's salary is waiting for approval. Send it back before "
                "generating it again."
            )

        records_by_employee = {}
        for record in (
            AttendanceRecord.objects.select_related(
                "employee", "employee_assignment", "leave_day", "shift"
            )
            .filter(work_date__gte=first, work_date__lte=last)
            .order_by("employee_id", "work_date")
        ):
            records_by_employee.setdefault(record.employee, []).append(record)
        # Only days inside the employment count; a monthly salary is paid for
        # the employed calendar days of the month.
        for employee, records in list(records_by_employee.items()):
            start = max(first, employee.joining_date or first)
            end = min(last, employee.leaving_date or last)
            records_by_employee[employee] = [r for r in records if start <= r.work_date <= end]
        if branch_ids is not None:
            # A branch's own salary: the people placed there at the month's end.
            records_by_employee = {
                employee: records for employee, records in records_by_employee.items()
                if records and records[-1].employee_assignment.branch_id in branch_ids
            }
        rebuilt = {employee.pk for employee in records_by_employee}

        run = PayrollRun.objects.filter(payroll_period=period, status=PayrollRun.Status.DRAFT).first()
        previous_skipped = {}
        if run is None:
            run = PayrollRun.objects.create(
                company=membership.company, payroll_period=period,
                created_by=actor, updated_by=actor,
            )
        else:
            # Regenerating a draft replaces it: all of it, or a branch's people.
            replaced = run.records.all()
            if branch_ids is not None:
                replaced = replaced.filter(employee_id__in=rebuilt)
                previous_skipped = {
                    int(employee_id): name
                    for employee_id, name in (run.totals_snapshot.get("skipped") or {}).items()
                    if int(employee_id) not in rebuilt
                }
            PayrollLine.objects.filter(payroll_record__in=replaced).delete()
            replaced.delete()
        # Proposed penalties are recalculated; a waived one is kept and its
        # occurrence is not charged again.
        proposed = PenaltyAssessment.objects.filter(
            payroll_period=period, status=PenaltyAssessment.Status.PROPOSED
        )
        if branch_ids is not None:
            proposed = proposed.filter(employee_id__in=rebuilt)
        PenaltyAssessmentAttendance.objects.filter(penalty_assessment__in=proposed).delete()
        proposed.delete()
        waived = set(
            PenaltyAssessment.objects.filter(
                payroll_period=period, status=PenaltyAssessment.Status.WAIVED
            ).values_list("occurrence_identity", flat=True)
        )
        adjustments_by_employee = {}
        for adjustment in PayrollAdjustment.objects.select_related(
            "source_payroll_period"
        ).filter(
            target_payroll_period=period, status=PayrollAdjustment.Status.ACTIVE
        ).order_by("pk"):
            adjustments_by_employee.setdefault(adjustment.employee_id, []).append(adjustment)

        skipped = dict(previous_skipped)
        skipped_now = []
        for employee, records in records_by_employee.items():
            if not records:
                continue
            start = max(first, employee.joining_date or first)
            end = min(last, employee.leaving_date or last)
            employed_days = (end - start).days + 1
            compensation = _compensation_at(employee, period_end) or _compensation_at(
                employee, timezone.make_aware(datetime.datetime.combine(records[-1].work_date, datetime.time.max))
            )
            if compensation is None:
                skipped[employee.pk] = employee.full_name
                skipped_now.append(employee.full_name)
                continue
            basic_segments = (
                _monthly_segments(employee, start, end)
                if compensation.pay_basis == EmployeeCompensation.PayBasis.MONTHLY else None
            )
            result = calculate_pay(
                compensation.pay_basis, compensation.base_rate, records,
                rules=rules, days_in_month=last.day,
                penalty_rules=penalty_rules, waived=waived, employee_key=f"{employee.pk}:",
                adjustments=adjustments_by_employee.get(employee.pk, ()),
                employed_days=employed_days,
                basic_segments=basic_segments,
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
                    "employed_days": employed_days,
                    "basic_segments": [
                        [str(rate), first_day.isoformat(), last_day.isoformat()]
                        for rate, first_day, last_day in (basic_segments or [])
                    ],
                    "counts": result["counts"],
                    "rules": result["rules"],
                    "overtime": result["overtime"],
                },
            )
            currency = compensation.currency or default_currency
            assessments = {
                index: _store_penalty(
                    membership.company, employee, period, occurrence, currency
                )
                for index, occurrence in result["penalties"]
            }
            adjusted = dict(result["adjustments"])
            PayrollLine.objects.bulk_create([
                PayrollLine(
                    company=membership.company, payroll_record=payroll_record,
                    line_type=kind, code=code, description=label,
                    quantity=money(quantity), rate=rate, amount=amount, sequence=index,
                    penalty_assessment=assessments.get(index),
                    payroll_adjustment_id=adjusted.get(index),
                    is_manual=index in adjusted,
                    source_type=(
                        "penalty" if index in assessments
                        else "adjustment" if index in adjusted else ""
                    ),
                )
                for index, (kind, code, label, quantity, rate, amount) in enumerate(result["lines"])
            ])

        # Totals over the whole run: after a branch regenerates, the other
        # branches' payslips are still in it.
        totals = run.records.aggregate(
            employees=Count("pk"), gross=Sum("gross_earnings"),
            deductions=Sum("total_deductions"), net=Sum("net_pay"),
        )
        penalty_totals = PenaltyAssessment.objects.filter(
            payroll_period=period, status=PenaltyAssessment.Status.PROPOSED
        ).aggregate(count=Count("pk"), amount=Sum("deduction_amount"))
        run.generated_by = actor
        run.policy_version = rules.version
        run.calculation_finished_at = timezone.now()
        run.totals_snapshot = {
            "employees": totals["employees"],
            "gross": str(money(totals["gross"] or 0)),
            "deductions": str(money(totals["deductions"] or 0)),
            "net": str(money(totals["net"] or 0)),
            "skipped_without_salary": list(skipped.values()),
            "skipped": {str(employee_id): name for employee_id, name in skipped.items()},
            "rules": rules.describe(),
            "penalties": penalty_totals["count"],
            "penalty_amount": str(money(penalty_totals["amount"] or 0)),
            "penalty_rules": [f"{rule.code} v{rule.version}" for rule in penalty_rules],
        }
        run.updated_by = actor
        run.save()
        after = dict(run.totals_snapshot)
        if branch_ids is not None:
            after.update(branches=sorted(branch_ids), rebuilt=len(rebuilt))
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="payroll.generated", obj=run, after=after,
        )
    # Who this pass skipped (for the message); the snapshot keeps the month's list.
    run.skipped_now = skipped_now
    return run


def _run_for_month(company_id, year, month, status):
    first, last = month_bounds(year, month)
    return PayrollRun.objects.select_for_update(of=("self",)).select_related("payroll_period").filter(
        payroll_period__start_date=first, payroll_period__end_date=last, status=status,
    ).first()


def _is_approver(actor, company_id):
    """The company's owner/administrator - there is exactly one per company
    (accounts: uniq_current_company_administrator)."""
    from accounts.models import CompanyMembership

    return CompanyMembership.all_objects.filter(
        company_id=company_id, user=actor, status=CompanyMembership.Status.ACTIVE,
        role__in=STRUCTURE_ROLES,
    ).exists()


def approval_blocker(actor, company_id, run):
    """Why ``actor`` may not approve ``run`` right now, or None if they may.

    Whoever prepares salary submits it - a branch manager with salary access,
    and later the payroll manager - and the owner/administrator approves it:
    two people see the month before employees do. A company has exactly one
    owner/administrator, so when they prepared the month themselves they
    approve their own submission; there is nobody else to ask.
    """
    if run is None or run.status != PayrollRun.Status.SUBMITTED:
        return "This month's salary is not waiting for approval."
    if not _is_approver(actor, company_id):
        return "Only the owner or company administrator can approve salary."
    return None


@transaction.atomic
def submit_payroll(*, actor, company_id, year, month):
    """Send a month's draft salary for approval (A11: finalise with approval).

    Whoever may prepare salary submits: the owner/administrator, or a branch
    manager with salary access (the month is one run for the company, so a
    branch submits all of it; the approver sees every branch and can send it
    back). Nothing about the month can change while it waits - Generate and
    the bonus/deduction lines refuse - so the approver approves exactly what
    was submitted.
    """
    from payroll import overtime

    membership, prepare = salary_branches(actor, company_id, "salary.prepare")
    if not prepare:
        raise PermissionDenied("Submitting salary needs access to prepare it.")
    with use_company(company_id):
        run = _run_for_month(company_id, year, month, PayrollRun.Status.DRAFT)
        if run is None:
            if _run_for_month(company_id, year, month, PayrollRun.Status.SUBMITTED):
                raise ValidationError("This month's salary is already waiting for approval.")
            raise ValidationError("Generate this month's salary before submitting it.")
        if overtime.decided_after(company_id, run):
            raise ValidationError(
                "Overtime was decided after this salary was generated. "
                "Generate the month again, then submit it."
            )
        run.status = PayrollRun.Status.SUBMITTED
        run.submitted_by = actor
        run.submitted_at = timezone.now()
        run.returned_by = None
        run.returned_at = None
        run.return_reason = ""
        run.updated_by = actor
        run.save()
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="payroll.submitted", obj=run,
            before={"status": PayrollRun.Status.DRAFT},
            after={"status": PayrollRun.Status.SUBMITTED, "net": run.totals_snapshot.get("net")},
        )
    return run


@transaction.atomic
def approve_payroll(*, actor, company_id, year, month):
    """Approve a submitted month: it is finalised.

    Employees then see their payslips, and the month's attendance and overtime
    stop changing (``attendance.services.locked_ranges`` reads the status).
    """
    from payroll import overtime

    membership = require_structure_manager(actor, company_id)
    with use_company(company_id):
        run = _run_for_month(company_id, year, month, PayrollRun.Status.SUBMITTED)
        refusal = approval_blocker(actor, company_id, run)
        if refusal:
            raise ValidationError(refusal)
        if overtime.decided_after(company_id, run):
            raise ValidationError(
                "Overtime was decided after this salary was generated. "
                "Send it back, generate the month again, then submit it."
            )
        run.status = PayrollRun.Status.POSTED
        run.posted_by = actor
        run.posted_at = timezone.now()
        run.updated_by = actor
        run.save()
        PenaltyAssessment.objects.filter(
            payroll_period=run.payroll_period, status=PenaltyAssessment.Status.PROPOSED
        ).update(status=PenaltyAssessment.Status.POSTED)
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="payroll.finalised", obj=run,
            before={"status": PayrollRun.Status.SUBMITTED},
            after={"status": PayrollRun.Status.POSTED, "net": run.totals_snapshot.get("net"),
                   "submitted_by": run.submitted_by_id, "approved_by": actor.pk},
        )
    return run


@transaction.atomic
def return_payroll(*, actor, company_id, year, month, reason):
    """Send a submitted month back to Draft, with a reason.

    The owner/administrator sends it back to have it corrected; whoever
    submitted it can also take it back themselves on noticing a mistake.
    """
    membership = require_company_membership(actor, company_id)
    reason = str(reason or "").strip()
    with use_company(company_id):
        run = _run_for_month(company_id, year, month, PayrollRun.Status.SUBMITTED)
        if run is None:
            raise ValidationError("This month's salary is not waiting for approval.")
        if not (_is_approver(actor, company_id) or run.submitted_by_id == actor.pk):
            raise PermissionDenied(
                "Only the owner or company administrator, or whoever submitted it, "
                "can send it back."
            )
        if not reason:
            raise ValidationError({"reason": "Say what needs changing, so it can be fixed."})
        run.status = PayrollRun.Status.DRAFT
        run.returned_by = actor
        run.returned_at = timezone.now()
        run.return_reason = reason
        run.updated_by = actor
        run.save()
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="payroll.returned", obj=run,
            before={"status": PayrollRun.Status.SUBMITTED, "submitted_by": run.submitted_by_id},
            after={"status": PayrollRun.Status.DRAFT, "reason": reason},
        )
    return run


@transaction.atomic
def reopen_payroll(*, actor, company_id, year, month, reason):
    """Undo a finalise, for a mistake: the month becomes a draft again. Audited with the reason."""
    membership = require_structure_manager(actor, company_id)
    reason = str(reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "Give a reason for undoing the finalise."})
    with use_company(company_id):
        run = _run_for_month(company_id, year, month, PayrollRun.Status.POSTED)
        if run is None:
            raise ValidationError("This month's salary is not finalised.")
        before = {"status": run.status, "posted_by": run.posted_by_id,
                  "posted_at": run.posted_at.isoformat() if run.posted_at else None}
        run.status = PayrollRun.Status.DRAFT
        run.posted_by = None
        run.posted_at = None
        run.updated_by = actor
        run.save()
        PenaltyAssessment.objects.filter(
            payroll_period=run.payroll_period, status=PenaltyAssessment.Status.POSTED
        ).update(status=PenaltyAssessment.Status.PROPOSED)
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="payroll.reopened", obj=run, before=before,
            after={"status": PayrollRun.Status.DRAFT, "reason": reason},
        )
    return run


def _preparer(actor, company_id, record):
    """``(membership, branch_ids)``: who may change a draft payslip's lines.

    Owner and company admin: any payslip, and the whole month is regenerated
    as before. Someone who may prepare salary in the payslip's branch (A12
    part 6): that branch is regenerated.
    """
    membership, branches = salary_branches(actor, company_id, "salary.prepare")
    if branches is ALL_BRANCHES:
        return membership, None
    branch_id = record_branch_id(record) if record else None
    if branch_id is None or branch_id not in branches:
        raise PermissionDenied("You can change payslips only in branches where you may prepare salary.")
    return membership, {branch_id}


def _regenerated_record(actor, company_id, period, employee_id, branch_ids=None):
    run = generate_payroll(
        actor=actor, company_id=company_id,
        year=period.start_date.year, month=period.start_date.month, branch_ids=branch_ids,
    )
    with use_company(company_id):
        return run.records.filter(employee_id=employee_id).first()


@transaction.atomic
def add_adjustment(*, actor, company_id, record_id, adjustment_type, amount, reason):
    """Add a one-time bonus or deduction to a draft payslip, then regenerate the month."""
    if not salary_branches(actor, company_id, "salary.prepare")[1]:
        raise PermissionDenied("Adding bonus or deduction lines needs access to prepare salary.")
    if adjustment_type not in PayrollAdjustment.AdjustmentType.values:
        raise ValidationError({"adjustment_type": "Choose Bonus or Deduction."})
    try:
        amount = Decimal(str(amount))
    except ArithmeticError:
        amount = Decimal("0")
    if not amount > 0:
        raise ValidationError({"amount": "Enter an amount above zero."})
    reason = str(reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "Give a reason; it is shown on the payslip."})
    with use_company(company_id):
        record = PayrollRecord.objects.select_related(
            "payroll_run__payroll_period", "employee_assignment_at_period_end"
        ).filter(pk=record_id).first()
        if record is None:
            raise PermissionDenied("Payslip not found in this company.")
        membership, branch_ids = _preparer(actor, company_id, record)
        if record.payroll_run.status != PayrollRun.Status.DRAFT:
            raise ValidationError(
                "Bonus and deduction lines can only change while the salary is a draft."
            )
        period = record.payroll_run.payroll_period
        adjustment = PayrollAdjustment(
            company=membership.company, employee_id=record.employee_id,
            target_payroll_period=period, adjustment_type=adjustment_type,
            amount=amount, reason=reason, created_by=actor, updated_by=actor,
        )
        adjustment.full_clean()
        adjustment.save()
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="payroll.adjustment_added", obj=adjustment,
            after={"employee_id": record.employee_id, "type": adjustment_type,
                   "amount": str(amount), "reason": reason},
        )
        employee_id = record.employee_id
    return _regenerated_record(actor, company_id, period, employee_id, branch_ids)


def open_period_after(company_id, period, months=12):
    """The first month after ``period`` whose salary is not finalised.

    A correction is paid in the next month; if that one is finalised too it
    moves to the one after. The period row is made if the month has none yet,
    so a correction can be raised before anybody generates that month.
    """
    first = period.end_date + datetime.timedelta(days=1)
    with use_company(company_id):
        for _ in range(months):
            start, end = month_bounds(first.year, first.month)
            row, _made = PayrollPeriod.objects.get_or_create(
                company_id=company_id, start_date=start, end_date=end,
                defaults={"name": start.strftime("%B %Y")},
            )
            if not PayrollRun.objects.filter(
                payroll_period=row, status=PayrollRun.Status.POSTED
            ).exists():
                return row
            first = end + datetime.timedelta(days=1)
    raise ValidationError(
        "Every month after this one is finalised too. Undo a finalise to make "
        "room for the correction."
    )


@transaction.atomic
def correct_finalised_month(*, actor, company_id, record_id, adjustment_type, amount, reason):
    """Put right a month already finalised, in the first month still open.

    The finalised month is never touched - what was paid stays what was paid,
    and the employee's payslip for it does not change under them. The money
    is paid as a line on the next open month, saying which month it is for.
    """
    if adjustment_type not in PayrollAdjustment.AdjustmentType.values:
        raise ValidationError({"adjustment_type": "Choose Bonus or Deduction."})
    try:
        amount = Decimal(str(amount))
    except ArithmeticError:
        amount = Decimal("0")
    if not amount > 0:
        raise ValidationError({"amount": "Enter an amount above zero."})
    reason = str(reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "Say what is being put right; it is shown on the payslip."})
    with use_company(company_id):
        record = PayrollRecord.objects.select_related(
            "payroll_run__payroll_period", "employee_assignment_at_period_end", "employee"
        ).filter(pk=record_id).first()
        if record is None:
            raise PermissionDenied("Payslip not found in this company.")
        membership, branch_ids = _preparer(actor, company_id, record)
        if record.payroll_run.status != PayrollRun.Status.POSTED:
            raise ValidationError(
                "This month is not finalised. Add a bonus or deduction line to it instead."
            )
        source = record.payroll_run.payroll_period
    target = open_period_after(company_id, source)
    with use_company(company_id):
        adjustment = PayrollAdjustment(
            company=membership.company, employee_id=record.employee_id,
            target_payroll_period=target, source_payroll_period=source,
            adjustment_type=adjustment_type, amount=amount, reason=reason,
            created_by=actor, updated_by=actor,
        )
        adjustment.full_clean()
        adjustment.save()
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="payroll.correction_added", obj=adjustment,
            after={"employee_id": record.employee_id, "type": adjustment_type,
                   "amount": str(amount), "reason": reason,
                   "for_month": source.name, "paid_in": target.name},
        )
        # If that month is already a draft, show it there straight away.
        exists = PayrollRun.objects.filter(
            payroll_period=target, status=PayrollRun.Status.DRAFT).exists()
    if exists:
        _regenerated_record(actor, company_id, target, record.employee_id, branch_ids)
    return adjustment


def corrections_for(company_id, record):
    """Corrections raised from this finalised payslip. Call in the company."""
    return (
        PayrollAdjustment.objects.select_related("target_payroll_period")
        .filter(employee_id=record.employee_id,
                source_payroll_period=record.payroll_run.payroll_period)
        .exclude(status=PayrollAdjustment.Status.CANCELLED)
        .order_by("pk")
    )


@transaction.atomic
def remove_adjustment(*, actor, company_id, adjustment_id):
    """Remove a bonus or deduction from a draft month (kept as removed), then regenerate."""
    if not salary_branches(actor, company_id, "salary.prepare")[1]:
        raise PermissionDenied("Removing bonus or deduction lines needs access to prepare salary.")
    with use_company(company_id):
        adjustment = PayrollAdjustment.objects.select_for_update(of=("self",)).select_related(
            "target_payroll_period"
        ).filter(pk=adjustment_id, status=PayrollAdjustment.Status.ACTIVE).first()
        if adjustment is None:
            raise PermissionDenied("Line not found in this company.")
        period = adjustment.target_payroll_period
        # The payslip it sits on decides the branch.
        record = PayrollRecord.objects.select_related("employee_assignment_at_period_end").filter(
            payroll_run__payroll_period=period, employee_id=adjustment.employee_id,
        ).order_by("-pk").first()
        membership, branch_ids = _preparer(actor, company_id, record)
        if PayrollRun.objects.filter(
            payroll_period=period,
            status__in=(PayrollRun.Status.POSTED, PayrollRun.Status.SUBMITTED),
        ).exists():
            raise ValidationError(
                "Bonus and deduction lines can only change while the salary is a draft."
            )
        adjustment.status = PayrollAdjustment.Status.CANCELLED
        adjustment.updated_by = actor
        adjustment.save()
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="payroll.adjustment_removed", obj=adjustment,
            before={"status": "active"},
            after={"status": "cancelled", "amount": str(adjustment.amount), "reason": adjustment.reason},
        )
        employee_id = adjustment.employee_id
    return _regenerated_record(actor, company_id, period, employee_id, branch_ids)
