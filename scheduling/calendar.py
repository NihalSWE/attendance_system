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

from dataclasses import dataclass

from django.db.models import Q

from common.tenant import use_company
from scheduling.models import (
    CompanyAttendanceSettings,
    DepartmentShift,
    Holiday,
    WeeklyOffRule,
)

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

    @property
    def has_any_shift(self):
        return self.shift is not None or (self.by_department and bool(self.department_shifts))

    def shift_for(self, department_id, on):
        """The shift a person in this company department works on a date.

        Department mode: the department's shift in force that day, else the
        company shift. Single-shift mode: always the company shift. An
        employee-level override is not built yet.
        """
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
        for holiday in self.holidays:
            if holiday.holiday_date == on and holiday.branch_id in (None, branch_id):
                return DayInfo(HOLIDAY, holiday.is_paid, holiday.name)
        for rule in self.rules:
            if (
                rule.weekday == on.weekday()
                and rule.branch_id in (None, branch_id)
                and rule.effective_from <= on
                and (rule.effective_to is None or on < rule.effective_to)
            ):
                return DayInfo(WEEKLY_OFF, rule.is_paid, rule.get_weekday_display())
        return DayInfo(WORKING)
