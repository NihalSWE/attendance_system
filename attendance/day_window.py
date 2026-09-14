"""Which scans belong to which day, and when a day stops accepting them.

DEVICE_ATTENDANCE_POLICY.md step 7, "When a day closes":

    A day's scans run from the attendance window before its shift start until
    the day closes: the employee's next shift start, or 24 hours after this
    shift started, whichever comes first.

That one rule replaces the old fixed window after the shift end, and it has to
do three jobs at once:

*Two shifts in a day.* The next shift start ends the previous day, so a second
shift that begins the same afternoon takes its own scans.

*Night shifts.* A shift running 22:00–06:00 closes at the next shift's start,
which is the following evening — so the 06:00 check-out still belongs to the
night it began.

*Weekly offs and holidays.* Somebody who comes in on their day off is working
that day, not extending the day before. The 24-hour cap does most of this, and
the off day owns whatever is left of the gap, so no scan falls between two days
and disappears.

The windows are built as a contiguous chain: one day's close is the next day's
opening. Nothing can land in a gap, and nothing can land in two days at once.
"""

import datetime

DAY = datetime.timedelta(days=1)


class DayWindow:
    """One employee-day: when it opens, when it closes, and its shift."""

    def __init__(self, date, *, shift, scheduled_start, scheduled_end,
                 opens_at, closes_at):
        self.date = date
        self.shift = shift
        self.scheduled_start = scheduled_start
        self.scheduled_end = scheduled_end
        self.opens_at = opens_at
        self.closes_at = closes_at

    @property
    def has_shift(self):
        return self.shift is not None

    def is_closed(self, now):
        return now >= self.closes_at

    def contains(self, moment):
        return self.opens_at <= moment < self.closes_at

    def __repr__(self):  # pragma: no cover - debugging only
        return f"<DayWindow {self.date} {self.opens_at:%H:%M}–{self.closes_at:%H:%M}>"


def _combine(day, time, tz):
    return datetime.datetime.combine(day, time, tzinfo=tz)


def shift_bounds(shift, day, tz):
    """The shift's scheduled start and end as instants."""
    start = _combine(day, shift.start_time, tz)
    end_day = day + DAY if shift.spans_next_day else day
    return start, _combine(end_day, shift.end_time, tz)


def build_windows(*, days, shift_for, tz, window_before_minutes=0):
    """A contiguous window per day, in order.

    ``shift_for`` is called with a date and returns that day's shift or None,
    so the caller stays in charge of department shifts, dated replacements and
    whatever A5 adds. ``days`` must be consecutive dates.

    Returns ``{date: DayWindow}``. The last day's close is an estimate — there
    is no day after it to ask — which is why callers build one day beyond the
    range they actually need.
    """
    before = datetime.timedelta(minutes=max(0, int(window_before_minutes or 0)))
    shifts = {day: shift_for(day) for day in days}

    # Pass one: where each day would like to begin.
    anchors = {}
    for day in days:
        shift = shifts[day]
        if shift is not None:
            anchors[day] = shift_bounds(shift, day, tz)[0] - before
        else:
            anchors[day] = None  # settled in pass two

    # Pass two: an off day has no shift to anchor to, so it takes whatever the
    # previous day gives up and runs to the next day that does have one. That
    # is what stops a scan on a day off falling into no day at all.
    windows = {}
    for index, day in enumerate(days):
        shift = shifts[day]
        later = days[index + 1:]
        next_anchored = next(
            (anchors[d] for d in later if anchors[d] is not None), None
        )

        if shift is None:
            opens_at = _previous_close(windows, days, index) or _combine(
                day, datetime.time.min, tz
            )
            closes_at = next_anchored or (opens_at + DAY)
            if closes_at <= opens_at:
                closes_at = opens_at + DAY
            windows[day] = DayWindow(
                day, shift=None, scheduled_start=None, scheduled_end=None,
                opens_at=opens_at, closes_at=closes_at,
            )
            continue

        scheduled_start, scheduled_end = shift_bounds(shift, day, tz)
        opens_at = anchors[day]
        # The rule: the next shift start, or 24 hours after this one started.
        cap = scheduled_start + DAY
        closes_at = min(next_anchored, cap) if next_anchored else cap
        if closes_at <= opens_at:
            closes_at = opens_at + DAY
        windows[day] = DayWindow(
            day, shift=shift,
            scheduled_start=scheduled_start, scheduled_end=scheduled_end,
            opens_at=opens_at, closes_at=closes_at,
        )
    return windows


def _previous_close(windows, days, index):
    if index == 0:
        return None
    earlier = windows.get(days[index - 1])
    return earlier.closes_at if earlier is not None else None
