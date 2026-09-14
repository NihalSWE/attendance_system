"""What kind of day a date is, and which shift applies, for one company.

Leave and attendance both need the same answers — is this a working day, a
weekly off, or a holiday, and which shift is the person measured against — so
they live in one place. ``WorkCalendar`` loads the
rules for a date range once and then answers per day without further queries,
which matters when a month is calculated for every employee.

Weekly off rules are matched by their dates, not by status: a stopped rule has
an ``effective_to`` and still applies to the days before it, so recalculating
an old month gives the answer that was true then.
"""

import datetime
import zoneinfo
from dataclasses import dataclass

from django.db.models import Q

from common.tenant import use_company
from scheduling.models import (
    CompanyAttendanceSettings,
    DepartmentShift,
    EmployeeShiftAssignment,
    Holiday,
    WeeklyOffRule,
)
from tenants.models import Company


def _zone(name):
    try:
        return zoneinfo.ZoneInfo(name or "UTC")
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return datetime.timezone.utc

WORKING = "working"
WEEKLY_OFF = "weekly_off"
HOLIDAY = "holiday"


@dataclass(frozen=True)
class DayInfo:
    kind: str
    is_paid: bool = True
    label: str = ""


class WorkCalendar:
    """The calendar of one company between two dates, inclusive."""

    def __init__(self, company_id, start, end):
        self.company_id = company_id
        with use_company(company_id):
            self.rules = list(
                WeeklyOffRule.objects.filter(effective_from__lte=end).filter(
                    Q(effective_to__isnull=True) | Q(effective_to__gt=start)
                )
            )
            self.holidays = list(
                Holiday.objects.filter(
                    status=Holiday.Status.ACTIVE,
                    holiday_date__gte=start,
                    holiday_date__lte=end,
                )
            )
            settings = (
                CompanyAttendanceSettings.objects.select_related("company_shift").first()
            )
            # Matched by dates, like weekly offs: a replaced department shift
            # still applies to the days before its replacement started.
            self.department_shifts = list(
                DepartmentShift.objects.select_related("shift")
                .filter(is_default=True, effective_from__lte=end)
                .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=start))
            )
        self.settings = settings
        # The company shift: used by everyone in single-shift mode, and by any
        # department without its own shift in department mode.
        self.shift = settings.company_shift if settings else None
        self.by_department = (
            settings is not None
            and settings.shift_mode == CompanyAttendanceSettings.ShiftMode.DEPARTMENT_SHIFTS
        )

        # Employee-level shifts (overrides and temporary shifts), as local
        # dates in company time: an assignment starts at the company's
        # midnight of its first day. Cancelled ones never applied.
        with use_company(company_id):
            tz = _zone(Company.objects.filter(pk=company_id).values_list("timezone", flat=True).first())
            from_instant = datetime.datetime.combine(start, datetime.time.min, tzinfo=tz)
            to_instant = datetime.datetime.combine(
                end + datetime.timedelta(days=1), datetime.time.min, tzinfo=tz
            )
            self.employee_shifts = [
                (
                    a.employee_id,
                    a.effective_from.astimezone(tz).date(),
                    a.effective_to.astimezone(tz).date() if a.effective_to else None,
                    a,
                )
                for a in EmployeeShiftAssignment.objects.select_related("shift")
                .exclude(status=EmployeeShiftAssignment.Status.CANCELLED)
                .filter(effective_from__lt=to_instant)
                .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=from_instant))
            ]

    @property
    def has_any_shift(self):
        return (
            self.shift is not None
            or (self.by_department and bool(self.department_shifts))
            or bool(self.employee_shifts)
        )

    def employee_shift(self, employee_id, on):
        """The employee's own shift assignment in force on a date, or None."""
        for owner, first, until, assignment in self.employee_shifts:
            if owner == employee_id and first <= on and (until is None or on < until):
                return assignment
        return None

    def shift_for(self, department_id, on, employee_id=None):
        """The shift a person works on a date.

        An employee's own shift (an override or a temporary shift) wins. Else,
        in department mode, the department's shift in force that day, else the
        company shift; in single-shift mode, always the company shift. Callers
        that know the employee must pass ``employee_id`` or an override is
        missed.
        """
        if employee_id is not None:
            assignment = self.employee_shift(employee_id, on)
            if assignment is not None:
                return assignment.shift
        if self.by_department:
            for link in self.department_shifts:
                if (
                    link.department_id == department_id
                    and link.effective_from <= on
                    and (link.effective_to is None or on < link.effective_to)
                ):
                    return link.shift
        return self.shift

    def day(self, branch_id, on):
        """Classify one date. A holiday wins over a weekly off on the same day."""
        # Days off are always paid (A5c), including legacy rows whose retained
        # is_paid column is False. Daily/hourly pay still follows salary rules.
        for holiday in self.holidays:
            if holiday.holiday_date == on and holiday.branch_id in (None, branch_id):
                return DayInfo(HOLIDAY, True, holiday.name)
        for rule in self.rules:
            if (
                rule.weekday == on.weekday()
                and rule.branch_id in (None, branch_id)
                and rule.effective_from <= on
                and (rule.effective_to is None or on < rule.effective_to)
            ):
                return DayInfo(WEEKLY_OFF, True, rule.get_weekday_display())
        return DayInfo(WORKING)
