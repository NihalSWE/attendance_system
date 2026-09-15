"""Turn a day's scans into labelled sessions, breaks and minutes.

The rule is DEVICE_ATTENDANCE_POLICY.md processing step 7, including "When a
day closes, and the check-out" (Ajay, 2026-09-14).

**Labels.** After repeat filtering the day's scans alternate IN, OUT across the
whole stream — one stream, every device, never restarted per device or per
upload batch, and never taken from the terminal's own IN/OUT keys.

**Open or closed.** A day is open until it closes: the employee's next shift
start, or 24 hours after this shift started, whichever comes first. While it is
open a trailing OUT is a **break-out**, not a check-out — somebody out at lunch
at 13:00 is on a break, not gone — and a trailing IN means still in. Only when
the day closes does a trailing OUT become the **check-out**; a day that closes
on an IN takes the shift's scheduled end as its check-out and goes to review.

**Minutes are measured against the shift, not against the scans.**

    regular    in-office time between the scheduled start and the scheduled
               end, plus the paid part of the break
    overtime   in-office time after the scheduled end
    (neither)  time before the scheduled start — arriving early is normal, and
               is not overtime and not paid

so ``total`` (check-in to check-out) is what a person recognises as their day,
while ``regular`` is what payroll pays for. The real check-in time is always
kept and shown, even when none of it counts.
"""

import datetime
from dataclasses import dataclass, field


@dataclass
class Scan:
    """One kept scan, after repeat filtering."""

    at: datetime.datetime
    punch_event_id: int | None = None
    direction: str = "in"
    label: str = "check_in"
    note: str = ""


@dataclass
class Session:
    """One in-office stretch. ``ended_at`` is None while it is still running."""

    started_at: datetime.datetime
    ended_at: datetime.datetime | None = None
    minutes: int = 0
    regular_minutes: int = 0
    overtime_minutes: int = 0
    in_index: int | None = None
    out_index: int | None = None
    is_overtime: bool = False
    needs_review: bool = False

    @property
    def is_complete(self):
        return self.ended_at is not None


@dataclass
class Day:
    """Everything the pairing decided about one employee-day."""

    kept: list = field(default_factory=list)
    dropped: list = field(default_factory=list)
    sessions: list = field(default_factory=list)
    first_in_at: datetime.datetime | None = None
    last_out_at: datetime.datetime | None = None
    total_minutes: int = 0
    in_office_minutes: int = 0
    outside_minutes: int = 0
    break_count: int = 0
    worked_minutes: int = 0
    overtime_minutes: int = 0
    late_minutes: int = 0
    early_out_minutes: int = 0
    is_closed: bool = True
    has_check_out: bool = False
    check_out_by_rule: bool = False
    open_overtime: bool = False
    review_reason: str = ""

    @property
    def needs_review(self):
        return bool(self.review_reason)

    @property
    def is_still_in(self):
        """Open, and the last scan was an IN — the person is in the building."""
        return not self.is_closed and bool(self.kept) and self.kept[-1].direction == "in"


def _minutes(start, end):
    return max(0, int((end - start).total_seconds() // 60))


def _overlap(start, end, window_start, window_end):
    """Minutes of [start, end) inside [window_start, window_end)."""
    if start is None or end is None:
        return 0
    lower = max(start, window_start) if window_start else start
    upper = min(end, window_end) if window_end else end
    return _minutes(lower, upper) if upper > lower else 0


def drop_repeats(moments, window_seconds):
    """Split scans into the ones that count and the repeats to ignore.

    Somebody scanning twice because the first beep was missed, or a terminal
    re-reporting, must not read as an instant break. The window is the
    company's ``duplicate_punch_window_seconds``.

    The window runs from the *previous scan*, kept or dropped, not from the
    last one we kept. A terminal that re-reports every few seconds would
    otherwise slip a scan through as soon as one gap edged past the window,
    and that scan reads as a break-out — a stutter becomes a phantom break.
    """
    kept, dropped = [], []
    window = datetime.timedelta(seconds=max(0, int(window_seconds or 0)))
    previous = None
    for moment in sorted(moments, key=lambda m: m[0] if isinstance(m, tuple) else m):
        at, punch_id = moment if isinstance(moment, tuple) else (moment, None)
        if previous is not None and window and (at - previous) <= window:
            dropped.append((at, punch_id))
        else:
            kept.append((at, punch_id))
        previous = at
    return kept, dropped


def label_scans(kept, *, is_closed):
    """Alternate IN/OUT across the whole stream and name each scan.

    ``is_closed`` is what decides whether a trailing OUT is the check-out or
    just the latest break-out. Getting that wrong is not cosmetic: it turns
    somebody's lunch into the end of their working day.
    """
    scans = []
    for index, (at, punch_id) in enumerate(kept):
        scans.append(Scan(
            at=at,
            punch_event_id=punch_id,
            direction="in" if index % 2 == 0 else "out",
            label="check_in" if index % 2 == 0 else "break_out",
        ))
    if not scans:
        return scans

    for scan in scans[1:]:
        scan.label = "break_in" if scan.direction == "in" else "break_out"
    scans[0].label = "check_in"

    if is_closed and scans[-1].direction == "out":
        scans[-1].label = "check_out"
    return scans


def build_day(
    moments,
    *,
    window_seconds=0,
    break_minutes=0,
    break_is_paid=False,
    scheduled_start=None,
    scheduled_end=None,
    grace_in_minutes=0,
    grace_out_minutes=0,
    overtime_after_minutes=0,
    is_closed=True,
):
    """Pair one employee-day. ``moments`` is datetimes or (datetime, punch id).

    ``is_closed`` says whether the day's close time has passed; the caller
    works that out from the shift (see ``attendance.day_window``). Everything
    that separates a finished day from one in progress hangs off it.
    """
    kept, dropped = drop_repeats(moments, window_seconds)
    scans = label_scans(kept, is_closed=is_closed)
    day = Day(kept=scans, dropped=dropped, is_closed=is_closed)
    if not scans:
        return day

    day.first_in_at = scans[0].at

    # Sessions: each IN with the OUT that follows it, if there is one.
    index = 0
    while index < len(scans):
        opening = scans[index]
        closing = scans[index + 1] if index + 1 < len(scans) else None
        session = Session(started_at=opening.at, in_index=index)
        if closing is not None:
            session.ended_at = closing.at
            session.minutes = _minutes(opening.at, closing.at)
            session.out_index = index + 1
        day.sessions.append(session)
        index += 2

    # A day that closes on an IN gets the shift's end as its check-out, and
    # goes to review. It counts up to the shift end until somebody corrects it.
    trailing = day.sessions[-1] if day.sessions else None
    if (
        is_closed
        and trailing is not None
        and not trailing.is_complete
        # With no shift there is nothing to close the day against, so the
        # session simply stays open rather than being called overtime.
        and scheduled_end is not None
    ):
        if trailing.started_at < scheduled_end:
            trailing.ended_at = scheduled_end
            trailing.minutes = _minutes(trailing.started_at, scheduled_end)
            day.check_out_by_rule = True
            day.review_reason = "check-out by rule, no scan"
        else:
            # Scanned back in after the shift had ended and never scanned out.
            # That is an overtime claim with no end: it pays nothing until
            # somebody approves it.
            trailing.is_overtime = True
            trailing.needs_review = True
            day.open_overtime = True
            day.review_reason = "overtime session with no check-out"
            # "The regular day still closes as above": the OUT that ended the
            # shift is the check-out, even though somebody came back later and
            # never scanned out again. Without this the regular day would be
            # left open by an overtime claim that pays nothing anyway.
            for scan in reversed(scans[:-1]):
                if scan.direction == "out":
                    scan.label = "check_out"
                    break

    day.in_office_minutes = sum(s.minutes for s in day.sessions)

    # Breaks: the gaps between one session ending and the next beginning.
    for earlier, later in zip(day.sessions, day.sessions[1:]):
        if earlier.ended_at is None:
            continue
        day.outside_minutes += _minutes(earlier.ended_at, later.started_at)
        day.break_count += 1

    closing_scan = next((s for s in reversed(scans) if s.label == "check_out"), None)
    if closing_scan is not None:
        day.last_out_at = closing_scan.at
        day.has_check_out = True
    elif day.check_out_by_rule:
        day.last_out_at = scheduled_end
        day.has_check_out = True
    if day.has_check_out:
        day.total_minutes = _minutes(day.first_in_at, day.last_out_at)

    _account(
        day,
        scheduled_start=scheduled_start,
        scheduled_end=scheduled_end,
        break_minutes=break_minutes,
        break_is_paid=break_is_paid,
        grace_in_minutes=grace_in_minutes,
        grace_out_minutes=grace_out_minutes,
        overtime_after_minutes=overtime_after_minutes,
    )
    return day


def _account(day, *, scheduled_start, scheduled_end, break_minutes,
             break_is_paid, grace_in_minutes, grace_out_minutes=0,
             overtime_after_minutes=0):
    """Split in-office time into regular and overtime against the shift.

    ``overtime_after_minutes`` delays when overtime starts counting, as the
    shift form puts it: "Minutes after the shift's end before overtime counts
    — 0 = straight away." So with 30, the first half hour past the end counts
    as neither regular time nor overtime, the same way arriving early counts
    as neither, and overtime is measured from the shift's end plus 30.
    """
    overtime_from = scheduled_end
    if scheduled_end is not None and overtime_after_minutes:
        overtime_from = scheduled_end + datetime.timedelta(
            minutes=int(overtime_after_minutes)
        )

    regular = overtime = 0
    for session in day.sessions:
        if session.ended_at is None:
            continue
        session.regular_minutes = _overlap(
            session.started_at, session.ended_at, scheduled_start, scheduled_end
        )
        session.overtime_minutes = (
            _overlap(session.started_at, session.ended_at, overtime_from, None)
            if overtime_from
            else 0
        )
        if session.needs_review:
            # An unapproved, unclosed overtime claim counts nothing.
            session.regular_minutes = session.overtime_minutes = 0
        regular += session.regular_minutes
        overtime += session.overtime_minutes

    if scheduled_start is None:
        # No shift to measure against: everything in office is regular time.
        regular = day.in_office_minutes
        overtime = 0

    # Only a break taken during the shift can be paid back.
    paid_break = 0
    if break_is_paid:
        shift_break_minutes = sum(
            _overlap(earlier.ended_at, later.started_at, scheduled_start, scheduled_end)
            for earlier, later in zip(day.sessions, day.sessions[1:])
            if earlier.ended_at is not None
        )
        paid_break = min(shift_break_minutes, int(break_minutes or 0))
    day.worked_minutes = regular + paid_break
    day.overtime_minutes = overtime

    if scheduled_start is not None and day.first_in_at is not None:
        late = _minutes(scheduled_start, day.first_in_at)
        day.late_minutes = max(0, late - int(grace_in_minutes or 0))
    if (
        scheduled_end is not None
        and day.last_out_at is not None
        and not day.check_out_by_rule
    ):
        # Leaving inside the shift's out-grace is not leaving early.
        early = _minutes(day.last_out_at, scheduled_end)
        day.early_out_minutes = (
            early if early > int(grace_out_minutes or 0) else 0
        )
