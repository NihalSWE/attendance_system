"""The shape of a leave day (Phase E1, 2026-09-27; docs/LEAVE_FULL_DESIGN.md §1):
a full day, a morning or afternoon half, or some hours - and paid, unpaid or
partly paid. One place decides what each shape covers, counts and pays, so
recording, requesting, approving and changing leave agree.

A half day with no part is what every half day recorded before this was: it
covers the whole shift and excuses both a late arrival and an early leaving
(attendance keeps reading it exactly so).
"""

import datetime
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError

from leaves.models import LeaveRequestSegment, PayType

Duration = LeaveRequestSegment.DurationType
MORNING, AFTERNOON = "morning", "afternoon"
PARTS = ((MORNING, "Morning (first half)"), (AFTERNOON, "Afternoon (second half)"))
HUNDRED = Decimal("100")
CENT = Decimal("0.01")


@dataclass(frozen=True)
class Shape:
    duration: str = Duration.FULL_DAY
    part: str = ""
    start_time: datetime.time | None = None
    end_time: datetime.time | None = None

    @property
    def is_full(self):
        return self.duration == Duration.FULL_DAY

    @property
    def is_half(self):
        return self.duration == Duration.HALF_DAY

    @property
    def is_hourly(self):
        return self.duration == Duration.HOURLY

    def _hours(self, shift, on, tz):
        start = datetime.datetime.combine(on, self.start_time, tzinfo=tz)
        end = datetime.datetime.combine(on, self.end_time, tzinfo=tz)
        if shift.spans_next_day and self.start_time < shift.start_time:
            # A night shift's hours after midnight are on the next date.
            start += datetime.timedelta(days=1)
        if end <= start:
            end += datetime.timedelta(days=1)
        return start, end

    def covered(self, shift_start, shift_end, shift, on, tz):
        """The stretch of the shift the leave covers."""
        if self.is_hourly:
            return self._hours(shift, on, tz)
        if self.is_half and self.part:
            middle = shift_start + (shift_end - shift_start) / 2
            return (shift_start, middle) if self.part == MORNING else (middle, shift_end)
        return shift_start, shift_end

    def minutes(self, shift, covered=None):
        if self.is_hourly:
            start, end = covered
            return int((end - start).total_seconds() // 60)
        return shift.scheduled_minutes // 2 if self.is_half else shift.scheduled_minutes

    def units(self, shift, minutes):
        """What a day of this shape takes from the allowance: 1, 0.5 or a part."""
        if self.is_full:
            return Decimal("1")
        if self.is_half:
            return Decimal("0.5")
        return (Decimal(minutes) / Decimal(shift.scheduled_minutes or 1)).quantize(
            CENT, ROUND_HALF_UP)

    def describe(self):
        if self.is_half:
            return {MORNING: "Morning half", AFTERNOON: "Afternoon half"}.get(self.part, "Half day")
        if self.is_hourly:
            return f"{self.start_time:%H:%M}–{self.end_time:%H:%M}"
        return "Full day"


def shape_from(values):
    """The shape the form asked for, checked on its own. Checking it against
    the day's shift is ``fit``."""
    duration = values.get("duration") or Duration.FULL_DAY
    if duration not in Duration.values:
        raise ValidationError({"duration": "Choose a full day, a half day or some hours."})
    single = values.get("start_date") == values.get("end_date")
    if duration == Duration.HALF_DAY:
        if not single:
            raise ValidationError({"end_date": "A half day is for one date. Choose the same "
                                               "first and last day."})
        part = values.get("half_day_part") or ""
        if part not in ("", MORNING, AFTERNOON):
            raise ValidationError({"half_day_part": "Choose the morning or the afternoon."})
        return Shape(duration, part=part)
    if duration == Duration.HOURLY:
        if not single:
            raise ValidationError({"end_date": "Hours are taken on one date. Choose the same "
                                               "first and last day."})
        start, end = values.get("start_time"), values.get("end_time")
        if start is None or end is None:
            raise ValidationError({"start_time": "Give the time the leave starts and ends."})
        if start == end:
            raise ValidationError({"end_time": "The leave has to end after it starts."})
        return Shape(duration, start_time=start, end_time=end)
    return Shape(Duration.FULL_DAY)


def shape_of(segment):
    return Shape(segment.duration_type or Duration.FULL_DAY, part=segment.half_day_part or "",
                 start_time=segment.start_time, end_time=segment.end_time)


def fit(shape, days):
    """The hours must lie inside that day's shift and be less than all of it.
    ``days`` as ``plan_leave_days`` gives them. Returns ``{date: (covered_start,
    covered_end, minutes, units)}``."""
    fitted = {}
    for on, assignment, shift_start, shift_end, shift in days:
        tz = shift_start.tzinfo
        covered = shape.covered(shift_start, shift_end, shift, on, tz)
        minutes = shape.minutes(shift, covered)
        if shape.is_hourly:
            if covered[0] < shift_start or covered[1] > shift_end:
                raise ValidationError({"start_time": (
                    f"The hours must fall inside the shift on {on:%d %b} "
                    f"({shift.start_time:%H:%M}–{shift.end_time:%H:%M}).")})
            if minutes >= shift.scheduled_minutes:
                raise ValidationError({"end_time": "That is the whole shift. Take a full day."})
            if minutes < 15:
                raise ValidationError({"end_time": "Take at least 15 minutes."})
        fitted[on] = (covered[0], covered[1], minutes, shape.units(shift, minutes))
    return fitted


def pay_percentage(pay_type, percentage=None):
    """The share of pay kept, 0-100, for a pay choice. Partial needs 1-99."""
    if pay_type == PayType.PAID:
        return HUNDRED
    if pay_type == PayType.UNPAID:
        return Decimal("0")
    if pay_type == PayType.PARTIAL:
        try:
            value = Decimal(str(percentage)).quantize(CENT)
        except (ArithmeticError, TypeError, ValueError):
            value = None
        if value is None or not Decimal("1") <= value <= Decimal("99"):
            raise ValidationError({"pay_percentage": "Give the share of pay kept, from 1 to 99 %."})
        return value
    raise ValidationError({"pay_type": "Choose paid, unpaid or partly paid."})


def pay_label(pay_type, percentage):
    if pay_type == PayType.PARTIAL:
        return f"Part paid ({Decimal(percentage).normalize():f}%)"
    return {PayType.PAID: "Paid", PayType.UNPAID: "Unpaid"}.get(pay_type, pay_type)
