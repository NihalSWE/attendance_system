"""Where each employee is right now, from today's scans.

The Employees page shows a badge per person. It is derived on read from the
same pairing the attendance record uses, so the badge and the calendar can
never tell different stories about the same day. Nothing is written.

    in_office    the last scan was an IN — they are in the building
    on_break     the last scan was an OUT and the day is still open
    left         the day has closed and they checked out
    not_in_yet   no scans, and they are not late yet
    absent       no scans, and the shift start plus grace has passed
    on_leave     approved leave for today
    off_today    a weekly off or a holiday

"absent" here is a live reading, not the stored one. The record does not call
a day absent until it closes (DEVICE_ATTENDANCE_POLICY.md step 7); the badge
says so as soon as somebody is late enough to be missing, which is the
question the page is being asked.
"""

import datetime
from collections import defaultdict

from django.db.models import Q

from attendance import day_window, pairing
from common.tenant import use_company
from devices.models import PunchEvent
from employees.models import EmployeeAssignment
from leaves.models import LeaveDay
from scheduling.calendar import HOLIDAY, WEEKLY_OFF, WorkCalendar
from scheduling.models import CompanyAttendanceSettings

LIVE_LEAVE = (
    LeaveDay.Status.RESERVED,
    LeaveDay.Status.APPROVED,
    LeaveDay.Status.CONSUMED,
)

#: key -> (label, token family). Only the five families that already exist.
BADGES = {
    "in_office": ("In office", "success"),
    "on_break": ("On break", "warning"),
    "left": ("Left", "neutral"),
    "not_in_yet": ("Not in yet", "neutral"),
    "absent": ("Absent", "danger"),
    "on_leave": ("On leave", "info"),
    "off_today": ("Off today", "neutral"),
    "no_shift": ("No shift", "neutral"),
}


class Status:
    """One employee's badge, ready for a template or for JSON."""

    def __init__(self, key, *, since=None, detail=""):
        self.key = key
        self.label, self.tone = BADGES.get(key, BADGES["not_in_yet"])
        self.since = since
        self.detail = detail

    @property
    def since_text(self):
        return self.since.strftime("%H:%M") if self.since else ""

    def as_dict(self):
        return {
            "key": self.key,
            "label": self.label,
            "tone": self.tone,
            "since": self.since_text,
            "detail": self.detail,
        }

    def __repr__(self):  # pragma: no cover - debugging only
        return f"<Status {self.key}>"


def _zone(name):
    import zoneinfo

    try:
        return zoneinfo.ZoneInfo(name or "UTC")
    except zoneinfo.ZoneInfoNotFoundError:
        return zoneinfo.ZoneInfo("UTC")


def _assignment_on(assignments, at):
    for assignment in assignments:
        if assignment.effective_from <= at and (
            assignment.effective_to is None or at < assignment.effective_to
        ):
            return assignment
    return None


def statuses_for(company_id, *, employee_ids=None, now=None):
    """``{employee_id: Status}`` for today. Read-only.

    One pass for the whole page: the punches, placements, leave and calendar
    are fetched once for everybody rather than per row, because this runs on
    every list render and again every minute after that.
    """
    from django.utils import timezone
    from tenants.models import Company

    now = now or timezone.now()
    company = Company.objects.get(pk=company_id)
    tz = _zone(company.timezone)
    today = now.astimezone(tz).date()
    yesterday = today - datetime.timedelta(days=1)

    calendar = WorkCalendar(company_id, yesterday - datetime.timedelta(days=1),
                            today + datetime.timedelta(days=2))
    statuses = {}

    with use_company(company_id):
        settings = CompanyAttendanceSettings.objects.first()
        window_before = getattr(settings, "attendance_window_before_minutes", 0)
        repeat_window = getattr(settings, "duplicate_punch_window_seconds", 0)

        span_start = datetime.datetime.combine(
            yesterday - datetime.timedelta(days=1), datetime.time.min, tzinfo=tz
        )
        span_end = datetime.datetime.combine(
            today + datetime.timedelta(days=2), datetime.time.min, tzinfo=tz
        )

        assignments_by_employee = defaultdict(list)
        rows = (
            EmployeeAssignment.objects.select_related("branch")
            .exclude(status=EmployeeAssignment.Status.CANCELLED)
            .filter(effective_from__lt=span_end)
            .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=span_start))
        )
        if employee_ids is not None:
            rows = rows.filter(employee_id__in=list(employee_ids))
        for assignment in rows.order_by("-effective_from"):
            assignments_by_employee[assignment.employee_id].append(assignment)

        if not assignments_by_employee:
            return statuses

        punches_by_employee = defaultdict(list)
        for employee_id, at, punch_id in (
            PunchEvent.objects.filter(
                employee_id__in=assignments_by_employee.keys(),
                authorization_status=PunchEvent.AuthorizationStatus.AUTHORIZED,
                punched_at_utc__gte=span_start,
                punched_at_utc__lt=span_end,
            )
            .exclude(dedupe_status=PunchEvent.DedupeStatus.CONFIRMED_DUPLICATE)
            .values_list("employee_id", "punched_at_utc", "pk")
        ):
            punches_by_employee[employee_id].append((at, punch_id))

        on_leave = set(
            LeaveDay.objects.filter(
                employee_id__in=assignments_by_employee.keys(),
                work_date=today, status__in=LIVE_LEAVE,
                # A half-day leave still expects the employee for the other half.
                balance_units__gte=1,
            ).values_list("employee_id", flat=True)
        )

        span_days = [
            yesterday - datetime.timedelta(days=1),
            yesterday,
            today,
            today + datetime.timedelta(days=1),
        ]

        for employee_id, assignments in assignments_by_employee.items():
            statuses[employee_id] = _status_for(
                employee_id=employee_id,
                assignments=assignments,
                punches=sorted(punches_by_employee.get(employee_id, [])),
                is_on_leave=employee_id in on_leave,
                calendar=calendar, span_days=span_days, today=today,
                tz=tz, now=now, window_before=window_before,
                repeat_window=repeat_window,
            )
    return statuses


def _status_for(*, employee_id, assignments, punches, is_on_leave, calendar,
                span_days, today, tz, now, window_before, repeat_window):
    if is_on_leave:
        return Status("on_leave")

    probe = datetime.datetime.combine(today, datetime.time(12), tzinfo=tz)
    assignment = _assignment_on(assignments, probe)
    if assignment is None:
        return Status("not_in_yet")

    info = calendar.day(assignment.branch_id, today)
    is_day_off = info.kind in (HOLIDAY, WEEKLY_OFF)
    # Held rather than returned straight away: somebody who came in on their
    # day off is in the building, and the page should say so rather than
    # "off today". This is only used when there are no scans.
    off = Status("off_today", detail=info.label) if is_day_off else None

    def shift_on(on):
        placement = _assignment_on(
            assignments,
            datetime.datetime.combine(on, datetime.time(12), tzinfo=tz),
        )
        if placement is None:
            return None
        return calendar.shift_for(
            placement.department_id, on, employee_id=employee_id
        )

    shift = shift_on(today)
    if shift is None and not is_day_off:
        return Status("no_shift")

    windows = day_window.build_windows(
        days=span_days, shift_for=shift_on, tz=tz,
        window_before_minutes=window_before,
    )
    window = windows[today]
    today_punches = [p for p in punches if window.contains(p[0])]

    if not today_punches:
        if off is not None:
            return off
        # Late enough to be missing? The badge answers "are they here", so it
        # says absent as soon as the grace has run out, without waiting for
        # the day to close the way the stored record does.
        grace = datetime.timedelta(minutes=getattr(shift, "grace_in_minutes", 0) or 0)
        if window.scheduled_start and now > window.scheduled_start + grace:
            return Status("absent")
        return Status("not_in_yet")

    day = pairing.build_day(
        today_punches,
        window_seconds=repeat_window,
        scheduled_start=window.scheduled_start,
        scheduled_end=window.scheduled_end,
        is_closed=window.is_closed(now),
    )
    last = day.kept[-1]
    since = last.at.astimezone(tz)

    if last.direction == "in":
        return Status("in_office", since=since)

    # Out, and the shift is over: they have gone home. Out, and the shift is
    # still running: they are on a break. The stored record keeps the day open
    # until it closes, which is the right rule for deciding a check-out — but
    # a badge answering "where are they now" should not say "on break" all
    # evening because somebody left at six.
    after_the_shift = (
        window.scheduled_end is not None and now >= window.scheduled_end
    )
    if after_the_shift or window.is_closed(now):
        return Status("left", since=since)
    return Status("on_break", since=since)
