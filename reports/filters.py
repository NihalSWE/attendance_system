"""A report's filters: its period (a day, a week, a month or a range) and the
branch, department and employee to narrow it to.

One reader for every report, so each one reads its period and filters the same
way - and what the page shows is exactly what its download holds.
"""

import calendar
import datetime
from dataclasses import dataclass, field

from django import forms
from django.utils import timezone

from attendance.forms import _range_input
from common.forms import StyledFormMixin, date_widget

DAY, WEEK, MONTH, RANGE = "day", "week", "month", "range"

#: Saturday: the week most of our companies keep (Friday the weekly off).
WEEK_STARTS_ON = calendar.SATURDAY

MONTHS = [(number, calendar.month_name[number]) for number in range(1, 13)]


class DatesForm(StyledFormMixin, forms.Form):
    """The date inputs, for the project's calendar pickers."""

    on = forms.DateField(required=False, label="Date", widget=date_widget("Date"))
    week_start = forms.DateField(required=False, label="Week starting",
                                 widget=date_widget("Week starting"))
    date_from = forms.DateField(required=False, label="From",
                                widget=_range_input("report-range", "From"))
    date_to = forms.DateField(required=False, label="To",
                              widget=_range_input("report-range", "To"))


@dataclass
class Filters:
    period: str
    first: datetime.date
    last: datetime.date
    label: str
    dates: DatesForm
    month: int = 0
    year: int = 0
    branch: str = ""
    department: str = ""
    employee: str = ""
    extra: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)

    @property
    def days(self):
        count = (self.last - self.first).days + 1
        return [self.first + datetime.timedelta(days=n) for n in range(count)]

    def slug(self):
        """The period as it goes in a download's file name."""
        if self.first == self.last:
            return self.first.isoformat()
        if self.period == MONTH:
            return f"{self.year}-{self.month:02d}"
        return f"{self.first.isoformat()}-to-{self.last.isoformat()}"


def _week_start(day):
    return day - datetime.timedelta(days=(day.weekday() - WEEK_STARTS_ON) % 7)


def _int(value, default, low, high):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if low <= number <= high else default


def read(source, period, *, max_days=92, extras=()):
    """``Filters`` from the query string, for a report of ``period``.

    Anything missing or unreadable falls back to a sensible default (today,
    this week, this month, this month so far) rather than an error; a range
    too long or back to front is said on the page and shortened.
    """
    today = timezone.localdate()
    dates = DatesForm(source)
    values = dates.cleaned_data if dates.is_valid() else {}
    errors = [str(e) for errors in dates.errors.values() for e in errors]
    month = year = 0

    if period == DAY:
        first = last = values.get("on") or today
        label = f"{first:%A, %d %B %Y}"
    elif period == WEEK:
        first = _week_start(values.get("week_start") or today)
        last = first + datetime.timedelta(days=6)
        label = f"Week of {first:%d %b} – {last:%d %b %Y}"
    elif period == MONTH:
        year = _int(source.get("year"), today.year, 2000, today.year + 1)
        month = _int(source.get("month"), today.month, 1, 12)
        first = datetime.date(year, month, 1)
        last = datetime.date(year, month, calendar.monthrange(year, month)[1])
        label = f"{calendar.month_name[month]} {year}"
    else:
        first = values.get("date_from") or today.replace(day=1)
        last = values.get("date_to") or today
        if last < first:
            first, last = last, first
            errors.append("The dates were the wrong way round, so they have been swapped.")
        if (last - first).days + 1 > max_days:
            last = first + datetime.timedelta(days=max_days - 1)
            errors.append(f"A report covers at most {max_days} days, so it stops on "
                          f"{last:%d %b %Y}. Download it in parts for longer.")
        label = (f"{first:%d %b %Y}" if first == last
                 else f"{first:%d %b %Y} – {last:%d %b %Y}")

    # Shown filled with the dates actually reported, picked or defaulted.
    shown = DatesForm(initial={"on": first, "week_start": first,
                               "date_from": first, "date_to": last})
    return Filters(
        period=period, first=first, last=last, label=label, dates=shown,
        month=month, year=year,
        branch=_digits(source.get("branch")), department=_digits(source.get("department")),
        employee=_digits(source.get("employee")),
        extra={name: (source.get(name) or "").strip() for name in extras},
        errors=errors,
    )


def neighbours(f):
    """``(earlier, later)``: the query values for the period before and after."""
    if f.period == DAY:
        step = datetime.timedelta(days=1)
        return {"on": f.first - step}, {"on": f.first + step}
    if f.period == WEEK:
        step = datetime.timedelta(days=7)
        return {"week_start": f.first - step}, {"week_start": f.first + step}
    if f.period == MONTH:
        before = f.first - datetime.timedelta(days=1)
        after = f.last + datetime.timedelta(days=1)
        return ({"month": before.month, "year": before.year},
                {"month": after.month, "year": after.year})
    return None, None


def _digits(value):
    value = (value or "").strip()
    return value if value.isdigit() else ""
