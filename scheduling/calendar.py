"""What kind of day a date is, for one branch of one company.

Leave and attendance both need the same answer — is this a working day, a
weekly off, or a holiday — so it lives in one place. ``WorkCalendar`` loads the
rules for a date range once and then answers per day without further queries,
which matters when a month is calculated for every employee.

Weekly off rules are matched by their dates, not by status: a stopped rule has
an ``effective_to`` and still applies to the days before it, so recalculating
an old month gives the answer that was true then.
"""

from dataclasses import dataclass

from django.db.models import Q

from common.tenant import use_company
from scheduling.models import CompanyAttendanceSettings, Holiday, WeeklyOffRule

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
        self.shift = settings.company_shift if settings else None

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
