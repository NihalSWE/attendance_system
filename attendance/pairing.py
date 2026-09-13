"""Turn a day's scans into labelled sessions, breaks and minutes.

The rule is DEVICE_ATTENDANCE_POLICY.md processing step 7, and it is decided
here in the software:

    After repeat filtering, the day's scans alternate IN, OUT, IN, OUT across
    the whole stream. The first IN is check-in; the last OUT is check-out;
    every OUT followed by another IN is a break-out, and that IN is the
    matching break-in.

Two consequences worth stating, because both are easy to get wrong:

*The stream is one stream.* Alternation runs across every device the employee
used, in time order. It is never restarted per device or per upload batch —
somebody who checks in at the front door and out at the back has one session,
not two broken ones.

*The device's own keys are ignored.* A terminal may have IN/OUT or break
buttons; ``PunchEvent.reported_direction`` records what it claimed and nothing
reads it here. Alternation is the only source of a label.

Minutes fall out of the same pairing, so they cannot disagree with each other:

    total     check-in to check-out, breaks included
    in-office the sum of the IN -> OUT sessions
    outside   the sum of the break-out -> break-in gaps
    worked    in-office, plus the paid part of the break when the shift has one

``total == in-office + outside`` always holds for a day that has a check-out.
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
    """One in-office stretch. ``ended_at`` is None when the day is unfinished."""

    started_at: datetime.datetime
    ended_at: datetime.datetime | None = None
    minutes: int = 0
    in_index: int | None = None
    out_index: int | None = None

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
    has_check_out: bool = False


def _minutes(start, end):
    return max(0, int((end - start).total_seconds() // 60))


def drop_repeats(moments, window_seconds):
    """Split scans into the ones that count and the repeats to ignore.

    Somebody scanning twice because the first beep was missed, or a terminal
    re-reporting, must not read as an instant break. The window is the
    company's ``duplicate_punch_window_seconds``.

    The window runs from the *previous scan*, kept or dropped, not from the
    last one we kept. A terminal that re-reports every few seconds would
    otherwise slip a scan through as soon as one gap edged past the window,
    and that scan reads as a break-out — a stutter becomes a phantom break.
    Chaining cannot swallow a real scan in practice: it would take an unbroken
    run of scans closer together than the window all the way from check-in.
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


def label_scans(kept):
    """Alternate IN/OUT across the whole stream and name each scan.

    The first scan of the day is an IN, and they alternate from there. A day
    with an odd number of scans ends on an IN, which means there is no
    check-out — the caller decides what that means under the company's
    missing-punch policy.
    """
    scans = []
    for index, (at, punch_id) in enumerate(kept):
        direction = "in" if index % 2 == 0 else "out"
        scans.append(Scan(at=at, punch_event_id=punch_id, direction=direction))

    ins = [s for s in scans if s.direction == "in"]
    outs = [s for s in scans if s.direction == "out"]
    for scan in ins:
        scan.label = "break_in"
    for scan in outs:
        scan.label = "break_out"
    if ins:
        ins[0].label = "check_in"
    if outs and len(outs) == len(ins):
        # A closed day: the final OUT is the check-out rather than a break.
        outs[-1].label = "check_out"
    elif outs and len(outs) < len(ins):
        # Ends on an IN, so every OUT so far was a break-out and the day has
        # no check-out.
        pass
    return scans


def build_day(moments, *, window_seconds=0, break_minutes=0, break_is_paid=False):
    """Pair one employee-day. ``moments`` is datetimes or (datetime, punch id).

    ``break_minutes`` / ``break_is_paid`` come from the shift. A paid break
    credits back the time spent outside, capped at the shift's allowance, so
    somebody who takes the lunch they are entitled to is not short-paid, and
    somebody who takes an hour when thirty minutes are paid loses only the
    extra thirty.
    """
    kept, dropped = drop_repeats(moments, window_seconds)
    scans = label_scans(kept)
    day = Day(kept=scans, dropped=dropped)
    if not scans:
        return day

    day.first_in_at = scans[0].at
    day.has_check_out = any(s.label == "check_out" for s in scans)

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

    day.in_office_minutes = sum(s.minutes for s in day.sessions)

    # Breaks: the gaps between one session ending and the next beginning.
    for earlier, later in zip(day.sessions, day.sessions[1:]):
        if earlier.ended_at is None:
            continue
        day.outside_minutes += _minutes(earlier.ended_at, later.started_at)
        day.break_count += 1

    if day.has_check_out:
        day.last_out_at = scans[-1].at
        day.total_minutes = _minutes(day.first_in_at, day.last_out_at)

    paid_break = min(day.outside_minutes, int(break_minutes or 0)) if break_is_paid else 0
    day.worked_minutes = day.in_office_minutes + paid_break
    return day
