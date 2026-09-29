"""Reading inactive periods (Nihal, 2026-09-29): who is inactive on which day.

One place, because punches (devices.services.authorization), the attendance
day (attendance.services) and salary (payroll.services) must agree on it.
Setting and ending a period is ``organization.employee_inactive``.
"""

import datetime
from collections import defaultdict

from django.db.models import Q

from employees.models import EmployeeInactivePeriod

ACTIVE = EmployeeInactivePeriod.Status.ACTIVE


def periods(company_id, employee_ids, start, end):
    """``{employee_id: [period, ...]}`` of periods in force touching start..end."""
    found = defaultdict(list)
    for period in (
        EmployeeInactivePeriod.all_objects.filter(
            company_id=company_id, employee_id__in=list(employee_ids), status=ACTIVE,
            start_date__lte=end,
        ).filter(Q(end_date__isnull=True) | Q(end_date__gte=start)).order_by("start_date")
    ):
        found[period.employee_id].append(period)
    return found


def covering(company_id, employee_id, day):
    """The period in force for this employee on ``day``, or None."""
    return (
        EmployeeInactivePeriod.all_objects.filter(
            company_id=company_id, employee_id=employee_id, status=ACTIVE, start_date__lte=day,
        ).filter(Q(end_date__isnull=True) | Q(end_date__gte=day)).order_by("start_date").first()
    )


def on(found, day):
    """The period among ``found`` (one employee's) covering ``day``, or None."""
    return next((period for period in found if period.covers(day)), None)


def days(found, start, end):
    """The days from start to end (inclusive) that ``found`` covers."""
    covered = set()
    for period in found:
        first = max(start, period.start_date)
        last = min(end, period.end_date or end)
        day = first
        while day <= last:
            covered.add(day)
            day += datetime.timedelta(days=1)
    return covered
