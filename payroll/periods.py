"""Which dates a salary month covers (2026-10-24).

A salary month keeps its name, "October 2026" (year, month), everywhere in
payroll. What it covers comes from the company's ``SalaryCycle``: it ends on
the day before the start day in its own month (start day 26: 25 Oct), and it
starts the day after the previous salary month ended. So:

- start day 1 (the default): the calendar month, as before;
- start day 26: "October 2026" is 26 Sep - 25 Oct;
- changing from 1 to 26 for October 2026: September is 1-30 Sep, October is
  1-25 Oct (a short changeover month), November is 26 Oct - 25 Nov.

No day is in two salary months and none is in no salary month.

The month's salary is divided by its "full days": the days it covers, except
in a changeover month, which is divided by the days of its calendar month (the
senior's choice, docs/payroll-periods/00-PLAN.md): 1-25 Oct pays 25/31.
"""

import calendar
import datetime

from common.tenant import use_company


def _cycles(company_id):
    from payroll.models import SalaryCycle

    with use_company(company_id):
        return list(SalaryCycle.objects.order_by("first_month")
                    .values_list("first_month", "start_day"))


def start_day_of(company_id, year, month, cycles=None):
    """The start day in force for salary month ``year``-``month``."""
    cycles = _cycles(company_id) if cycles is None else cycles
    target = datetime.date(year, month, 1)
    day = 1
    for first_month, start in cycles:
        if first_month <= target:
            day = start
    return day


def _end(company_id, year, month, cycles):
    day = start_day_of(company_id, year, month, cycles)
    if day == 1:
        return datetime.date(year, month, calendar.monthrange(year, month)[1])
    return datetime.date(year, month, day - 1)


def _previous(year, month):
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _next(year, month):
    return (year + 1, 1) if month == 12 else (year, month + 1)


def bounds(company_id, year, month, cycles=None):
    """``(first, last)`` dates of salary month ``year``-``month``."""
    cycles = _cycles(company_id) if cycles is None else cycles
    last = _end(company_id, year, month, cycles)
    first = _end(company_id, *_previous(year, month), cycles) + datetime.timedelta(days=1)
    return first, last


def is_changeover(company_id, year, month, cycles=None):
    """True when the start day changes with this salary month (a short month)."""
    cycles = _cycles(company_id) if cycles is None else cycles
    return (start_day_of(company_id, year, month, cycles)
            != start_day_of(company_id, *_previous(year, month), cycles))


def full_days(company_id, year, month, cycles=None):
    """What a monthly salary is divided by for this salary month (see above)."""
    cycles = _cycles(company_id) if cycles is None else cycles
    first, last = bounds(company_id, year, month, cycles)
    if is_changeover(company_id, year, month, cycles):
        return calendar.monthrange(last.year, last.month)[1]
    return (last - first).days + 1


def month_of(company_id, day, cycles=None):
    """``(year, month)`` of the salary month that covers ``day``."""
    cycles = _cycles(company_id) if cycles is None else cycles
    year, month = day.year, day.month
    if day > _end(company_id, year, month, cycles):
        year, month = _next(year, month)
    return year, month


def name(year, month):
    return datetime.date(year, month, 1).strftime("%B %Y")


def set_start_day(*, actor, company_id, start_day, year, month):
    """Salary months start on ``start_day`` from salary month ``year``-``month``.

    Owner or company administrator. Refused when that month, or a later one,
    already has a salary (draft, waiting or finalised) or a bonus, deduction or
    LFA line: its dates would change under it. So a change always starts from a
    month nobody has generated yet, and no generated month ever moves.
    """
    from django.core.exceptions import ValidationError
    from django.db import transaction

    from auditlog.services import record_company_event
    from organization.services import require_structure_manager
    from payroll.models import PayrollAdjustment, PayrollRun, SalaryCycle

    membership = require_structure_manager(actor, company_id)
    try:
        day = int(start_day)
    except (TypeError, ValueError):
        raise ValidationError({"start_day": "Give a day from 1 to 28."}) from None
    if not 1 <= day <= 28:
        raise ValidationError({"start_day": "Give a day from 1 to 28 (29-31 would break "
                                            "February)."})
    first_month = datetime.date(year, month, 1)
    cycles = _cycles(company_id)
    if day == start_day_of(company_id, year, month, cycles):
        raise ValidationError({"start_day": f"{name(year, month)} already starts on day {day}."})
    with transaction.atomic(), use_company(company_id):
        # Only months from the chosen one on change; none may have a salary.
        # A weekly pay group's weeks do not follow the salary month.
        run = (PayrollRun.objects.filter(payroll_period__end_date__gte=first_month,
                                         pay_group__isnull=True)
               .select_related("payroll_period").order_by("payroll_period__end_date").first())
        line = (PayrollAdjustment.objects.filter(target_payroll_period__end_date__gte=first_month)
                .select_related("target_payroll_period")
                .order_by("target_payroll_period__end_date").first())
        taken = run.payroll_period if run else (line.target_payroll_period if line else None)
        if taken is not None:
            raise ValidationError({"first_month": (
                f"{taken.name} already has a salary or a bonus, deduction or LFA line, so its "
                "dates cannot change. Choose a month after the last one that has any.")})
        before = [[when.isoformat(), start] for when, start in cycles]
        SalaryCycle.objects.update_or_create(
            first_month=first_month,
            defaults={"start_day": day, "company": membership.company,
                      "created_by": actor, "updated_by": actor})
        after = _cycles(company_id)
        first, last = bounds(company_id, year, month, after)
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="payroll.salary_month_changed", obj=membership.company,
            before={"cycles": before},
            after={"cycles": [[when.isoformat(), start] for when, start in after],
                   "from": name(year, month), "start_day": day,
                   "first": first.isoformat(), "last": last.isoformat()})
    return first, last
