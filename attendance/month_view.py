"""One employee's month, shaped for the calendar.

Kept apart from the view because two screens need exactly this: the admin
calendar, and the employee panel's "My attendance" (A7). Both hand in an
employee and a month and get the same weeks back, so the two can never drift
into showing a person different figures about the same day.

Everything here is read-only. The day's status and minutes were decided by
``attendance.services.calculate_attendance``; this only arranges them.
"""

import calendar as month_calendar
import datetime
import zoneinfo

from attendance.models import AttendanceRecord

# Monday first, as the spec asks and as the rest of the project's calendars do.
WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

#: status -> the token family the square is tinted with. Only the five status
#: families that already exist; no new colours (WARM_PAPER_INK_SPEC).
STATUS_TONE = {
    AttendanceRecord.AttendanceStatus.PRESENT: "success",
    AttendanceRecord.AttendanceStatus.HALF_DAY: "warning",
    AttendanceRecord.AttendanceStatus.INCOMPLETE: "warning",
    AttendanceRecord.AttendanceStatus.ABSENT: "danger",
    AttendanceRecord.AttendanceStatus.LEAVE: "info",
    AttendanceRecord.AttendanceStatus.HOLIDAY: "info",
    AttendanceRecord.AttendanceStatus.WEEKLY_OFF: "neutral",
}

STATUS_LABEL = {
    AttendanceRecord.AttendanceStatus.PRESENT: "Present",
    AttendanceRecord.AttendanceStatus.HALF_DAY: "Half day",
    AttendanceRecord.AttendanceStatus.INCOMPLETE: "Incomplete",
    AttendanceRecord.AttendanceStatus.ABSENT: "Absent",
    AttendanceRecord.AttendanceStatus.LEAVE: "Leave",
    AttendanceRecord.AttendanceStatus.HOLIDAY: "Holiday",
    AttendanceRecord.AttendanceStatus.WEEKLY_OFF: "Weekly off",
}

ALLOCATION_LABEL = {
    "check_in": "Check-in",
    "break_out": "Break-out",
    "break_in": "Break-in",
    "check_out": "Check-out",
    "ignored": "Ignored",
}


def zone(name):
    try:
        return zoneinfo.ZoneInfo(name or "UTC")
    except zoneinfo.ZoneInfoNotFoundError:
        return zoneinfo.ZoneInfo("UTC")


def hours_and_minutes(minutes):
    """480 -> "8h 0m". Read at a glance on a small square."""
    minutes = int(minutes or 0)
    return f"{minutes // 60}h {minutes % 60}m"


class Day:
    """One square. Empty when the month has no record for that date."""

    def __init__(self, date, record=None, *, tz, today):
        self.date = date
        self.record = record
        self.tz = tz
        self.is_today = date == today
        self.is_future = date > today

    # -- what the square shows --------------------------------------------

    @property
    def number(self):
        return self.date.day

    @property
    def has_record(self):
        return self.record is not None

    @property
    def status(self):
        return self.record.attendance_status if self.record else ""

    @property
    def tone(self):
        """success / warning / danger / info / neutral, or "" for an empty day.

        Late is not a status of its own — it is a present day with late
        minutes — but it reads as its own thing on a calendar, so it takes the
        warning tint while keeping the Present badge alongside a Late one.
        """
        if not self.record:
            return ""
        if self.is_late:
            return "warning"
        return STATUS_TONE.get(self.record.attendance_status, "neutral")

    @property
    def status_label(self):
        if not self.record:
            return ""
        base = STATUS_LABEL.get(self.record.attendance_status, "")
        if self.record.attendance_status == AttendanceRecord.AttendanceStatus.LEAVE:
            paid = (self.record.payable_fraction or 0) > 0
            return f"{base} ({'paid' if paid else 'unpaid'})"
        return base

    @property
    def is_late(self):
        return bool(self.record and self.record.late_minutes)

    @property
    def note(self):
        """The holiday's name, or the leave's — whatever the day is called."""
        return self.record.note if self.record else ""

    @property
    def check_in(self):
        return self._clock(self.record.first_in_at if self.record else None)

    @property
    def check_out(self):
        return self._clock(self.record.last_out_at if self.record else None)

    @property
    def has_times(self):
        return bool(self.check_in)

    @property
    def in_office(self):
        return hours_and_minutes(self.record.worked_minutes) if self.record else ""

    @property
    def break_count(self):
        return self.record.break_count if self.record else 0

    @property
    def break_label(self):
        count = self.break_count
        if not count:
            return ""
        return f"{count} break" if count == 1 else f"{count} breaks"

    def _clock(self, moment):
        return moment.astimezone(self.tz).strftime("%H:%M") if moment else ""


class Filler:
    """A square belonging to the previous or next month: shown, but empty."""

    is_filler = True
    has_record = False
    is_today = False
    is_future = False
    tone = ""

    def __init__(self, date):
        self.date = date
        self.number = date.day


def build_month(*, employee, year, month, company_timezone, today=None):
    """The weeks of one employee's month, plus a summary strip.

    Must be called inside the employee's tenant context.
    """
    today = today or datetime.date.today()
    tz = zone(company_timezone)
    first = datetime.date(year, month, 1)
    last = datetime.date(year, month, month_calendar.monthrange(year, month)[1])

    records = {
        record.work_date: record
        for record in AttendanceRecord.objects.filter(
            employee=employee, work_date__gte=first, work_date__lte=last
        ).select_related("shift", "leave_day")
    }

    days = [
        Day(first + datetime.timedelta(days=offset), records.get(
            first + datetime.timedelta(days=offset)
        ), tz=tz, today=today)
        for offset in range((last - first).days + 1)
    ]

    # Pad to whole Monday-first weeks with the neighbouring months' dates.
    leading = first.weekday()
    trailing = 6 - last.weekday()
    cells = (
        [Filler(first - datetime.timedelta(days=n)) for n in range(leading, 0, -1)]
        + days
        + [Filler(last + datetime.timedelta(days=n)) for n in range(1, trailing + 1)]
    )
    weeks = [cells[i:i + 7] for i in range(0, len(cells), 7)]

    return {
        "weeks": weeks,
        "days": days,
        "weekday_names": WEEKDAY_NAMES,
        "summary": summarise(days),
        "month_start": first,
        "month_end": last,
    }


def summarise(days):
    """The strip above the grid. Counted from the same days it sits over."""
    counts = {
        "present": 0, "late": 0, "absent": 0, "leave": 0,
        "holiday": 0, "weekly_off": 0, "incomplete": 0, "half_day": 0,
    }
    worked = 0
    S = AttendanceRecord.AttendanceStatus
    for day in days:
        record = day.record
        if record is None:
            continue
        worked += record.worked_minutes or 0
        if record.attendance_status == S.PRESENT:
            counts["present"] += 1
        elif record.attendance_status == S.HALF_DAY:
            counts["half_day"] += 1
        elif record.attendance_status == S.ABSENT:
            counts["absent"] += 1
        elif record.attendance_status == S.LEAVE:
            counts["leave"] += 1
        elif record.attendance_status == S.HOLIDAY:
            counts["holiday"] += 1
        elif record.attendance_status == S.WEEKLY_OFF:
            counts["weekly_off"] += 1
        elif record.attendance_status == S.INCOMPLETE:
            counts["incomplete"] += 1
        if record.late_minutes:
            counts["late"] += 1
    counts["in_office"] = hours_and_minutes(worked)
    return counts


def build_day_detail(*, record, company_timezone):
    """Everything the day panel shows: the scans, the totals, the shift.

    Must be called inside the record's tenant context.
    """
    tz = zone(company_timezone)
    scans = []
    for allocation in record.allocations.select_related(
        "punch_event__device"
    ).order_by("sequence_number"):
        device = getattr(allocation.punch_event, "device", None)
        if allocation.attendance_correction_id:
            # Added by hand (N5): there is no device, and saying so is the point.
            source = "Added by hand"
        else:
            source = device.name if device else ""
        scans.append({
            "time": allocation.event_at.astimezone(tz).strftime("%H:%M:%S"),
            "label": ALLOCATION_LABEL.get(allocation.label, allocation.label),
            "device": source,
            "is_manual": bool(allocation.attendance_correction_id),
            "is_included": allocation.is_included,
            "note": allocation.interpretation_note,
        })

    day = Day(record.work_date, record, tz=tz, today=datetime.date.today())
    shift = record.shift
    return {
        "date": record.work_date,
        "status_label": day.status_label,
        "tone": day.tone,
        "is_late": day.is_late,
        "late_minutes": record.late_minutes,
        "note": record.note,
        "scans": scans,
        "ignored_count": sum(1 for s in scans if not s["is_included"]),
        "check_in": day.check_in,
        "check_out": day.check_out,
        "total": hours_and_minutes(record.total_minutes),
        "in_office": hours_and_minutes(record.worked_minutes),
        "outside": hours_and_minutes(record.outside_minutes),
        "break_count": record.break_count,
        "shift_name": shift.name if shift else "",
        "shift_times": (
            f"{shift.start_time:%H:%M} – {shift.end_time:%H:%M}" if shift else ""
        ),
    }
