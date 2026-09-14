"""Calculate daily attendance for a month from punches, leave and the calendar.

Thin slice of the 2026-09-12 salary fast-track. For every employee and every
day of the month (up to today) one ``AttendanceRecord`` is written:

- a day on approved leave            -> leave, payable by its pay percentage
- a holiday / weekly off             -> holiday / weekly off, payable if paid
- a working day with IN and OUT      -> present / half day / absent by the
                                        shift's full-day and half-day minutes
- a working day with only one punch  -> "incomplete" (counted as present, and
                                        flagged) or absent, per the company's
                                        missing-punch setting
- a working day with no punch        -> absent

Only authorized, non-duplicate punches count, taken inside the shift's
attendance window. Recalculating a month replaces its records, so a cancelled
leave or a new holiday is picked up by simply calculating again.

Each day's scans are paired by ``attendance.pairing`` into labelled
allocations (check-in / break-out / break-in / check-out) and in-office
sessions, per DEVICE_ATTENDANCE_POLICY.md step 7.

A day owns the scans between its own opening and its close — the next shift
start, or 24 hours after this shift started, whichever comes first
(``attendance.day_window``). Until it closes it is **open**: a trailing OUT is
a break, not a check-out, and its figures are provisional. When it closes, a
trailing OUT becomes the check-out; a day that closes on an IN takes the
shift's scheduled end and goes to review.

Minutes are measured against the shift, not the scans: regular time runs from
the scheduled start to the scheduled end, time after the end is overtime, and
time before the start is neither — arriving early is normal, and is not paid.

``payable_fraction`` is what payroll reads: 1, 0.5 or 0. ``worked_minutes`` is
regular in-office time (plus a paid break) and never includes overtime.

Nobody presses Calculate. ``recalculate()`` is the entry point: ingestion calls
it for the days a punch batch touches, and the screens call it for days whose
close has passed. A day inside a posted payroll run is never touched.
"""

import calendar as month_calendar
import logging
import datetime
import zoneinfo
from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from attendance import day_window, pairing
from attendance.models import (
    AttendanceRecord,
    AttendanceSession,
    PunchAllocation,
    ReviewStatus,
)
from auditlog.services import record_company_event
from common.tenant import use_company
from devices.models import PunchEvent
from employees.models import Employee, EmployeeAssignment
from leaves.models import LeaveDay
from organization.services import require_structure_manager
from scheduling.calendar import HOLIDAY, WEEKLY_OFF, WorkCalendar
from scheduling.models import CompanyAttendanceSettings

logger = logging.getLogger(__name__)

ONE, HALF, NONE = Decimal("1"), Decimal("0.5"), Decimal("0")
LIVE_LEAVE = (LeaveDay.Status.RESERVED, LeaveDay.Status.APPROVED, LeaveDay.Status.CONSUMED)


def month_bounds(year, month):
    first = datetime.date(year, month, 1)
    last = datetime.date(year, month, month_calendar.monthrange(year, month)[1])
    return first, last


def _zone(name):
    try:
        return zoneinfo.ZoneInfo(name or "UTC")
    except zoneinfo.ZoneInfoNotFoundError:
        return zoneinfo.ZoneInfo("UTC")


def _window(shift, on, tz):
    start = datetime.datetime.combine(on, shift.start_time, tzinfo=tz)
    end_day = on + datetime.timedelta(days=1) if shift.spans_next_day else on
    end = datetime.datetime.combine(end_day, shift.end_time, tzinfo=tz)
    return start, end


def _assignment_on(assignments, at):
    for assignment in assignments:
        if assignment.effective_from <= at and (
            assignment.effective_to is None or at < assignment.effective_to
        ):
            return assignment
    return None


def _measurements(day):
    """The minute columns, straight off the paired day."""
    return {
        "first_in_at": day.first_in_at,
        "last_out_at": day.last_out_at,
        "worked_minutes": day.worked_minutes,
        "total_minutes": day.total_minutes,
        "break_minutes": day.outside_minutes,
        "outside_minutes": day.outside_minutes,
        "break_count": day.break_count,
        "calculated_overtime_minutes": day.overtime_minutes,
        "late_minutes": day.late_minutes,
        "early_out_minutes": day.early_out_minutes,
        "check_out_by_rule": day.check_out_by_rule,
        "review_status": (
            ReviewStatus.NEEDS_REVIEW if day.needs_review else ReviewStatus.CLEAN
        ),
        "review_reason": day.review_reason,
        "note": "",
    }


def _classify_working_day(shift, settings, day):
    """Turn a paired day into the record's status and minute fields.

    ``day`` is an ``attendance.pairing.Day``: the labelling, the minutes, the
    lateness and the review reason are already decided there. This maps them
    onto the record and applies the company's missing-punch policy.
    """
    minutes = _measurements(day)

    if not day.kept:
        # No scans. "Not in yet" until the shift ends; only then absent. The
        # caller does not write a record at all before that, so reaching here
        # means the day is over.
        return (
            AttendanceRecord.AttendanceStatus.ABSENT,
            AttendanceRecord.PunchStatus.NO_PUNCH,
            NONE,
            minutes,
        )

    if not day.is_closed:
        # Still running. Nothing is decided, and nothing is paid on a guess.
        minutes["note"] = "In progress."
        return (
            AttendanceRecord.AttendanceStatus.INCOMPLETE,
            AttendanceRecord.PunchStatus.MISSING_OUT,
            NONE,
            minutes,
        )

    auto_absent = (
        settings.missing_punch_policy
        == CompanyAttendanceSettings.MissingPunchPolicy.AUTO_ABSENT
    )
    if day.check_out_by_rule and auto_absent:
        # The company chose that a day with no check-out is simply absent.
        minutes["note"] = "No check-out; counted as absent by company setting."
        return (
            AttendanceRecord.AttendanceStatus.ABSENT,
            AttendanceRecord.PunchStatus.MISSING_OUT,
            NONE,
            minutes,
        )

    worked = day.worked_minutes
    full = shift.minimum_full_day_minutes
    half = shift.minimum_half_day_minutes
    if not full or worked >= full:
        status, fraction = AttendanceRecord.AttendanceStatus.PRESENT, ONE
    elif half and worked >= half:
        status, fraction = AttendanceRecord.AttendanceStatus.HALF_DAY, HALF
    else:
        status, fraction = AttendanceRecord.AttendanceStatus.ABSENT, NONE

    punch_status = (
        AttendanceRecord.PunchStatus.MISSING_OUT
        if day.check_out_by_rule
        else AttendanceRecord.PunchStatus.COMPLETE
    )
    notes = []
    if day.check_out_by_rule:
        notes.append("Checked out at the shift end by rule; no scan.")
    if day.open_overtime:
        notes.append("Overtime session left open; pays nothing until approved.")
    if day.break_count:
        notes.append(
            f"{day.break_count} break{'s' if day.break_count > 1 else ''}, "
            f"{day.outside_minutes} min outside."
        )
    minutes["note"] = " ".join(notes)[:255]
    return status, punch_status, fraction, minutes


def _write_pairing(record, paired):
    """Replace this record's allocations and sessions with the new pairing.

    Rewritten rather than merged: a recalculation must leave exactly the
    current answer, and these rows are a derived layer over PunchEvent, which
    is never touched.
    """
    AttendanceSession.objects.filter(attendance_record=record).delete()
    PunchAllocation.objects.filter(attendance_record=record).delete()
    if paired is None or not paired.kept:
        return

    allocations = []
    for index, scan in enumerate(paired.kept, start=1):
        allocations.append(
            PunchAllocation(
                company_id=record.company_id,
                attendance_record=record,
                punch_event_id=scan.punch_event_id,
                sequence_number=index,
                event_at=scan.at,
                interpreted_direction=scan.direction,
                label=scan.label,
                is_included=True,
                interpretation_note=scan.note,
            )
        )
    for index, (at, punch_id) in enumerate(paired.dropped, start=len(allocations) + 1):
        allocations.append(
            PunchAllocation(
                company_id=record.company_id,
                attendance_record=record,
                punch_event_id=punch_id,
                sequence_number=index,
                event_at=at,
                interpreted_direction=PunchAllocation.Direction.IGNORED,
                label=PunchAllocation.Label.IGNORED,
                is_included=False,
                exclusion_reason=PunchAllocation.ExclusionReason.DUPLICATE,
                interpretation_note="Repeat scan inside the duplicate window.",
            )
        )
    PunchAllocation.objects.bulk_create(allocations)

    by_sequence = {a.sequence_number: a for a in allocations}
    sessions = []
    for index, session in enumerate(paired.sessions, start=1):
        sessions.append(
            AttendanceSession(
                company_id=record.company_id,
                attendance_record=record,
                sequence_number=index,
                in_allocation=by_sequence.get((session.in_index or 0) + 1),
                out_allocation=(
                    by_sequence.get(session.out_index + 1)
                    if session.out_index is not None
                    else None
                ),
                started_at=session.started_at,
                ended_at=session.ended_at,
                worked_minutes=session.minutes,
                status=(
                    AttendanceSession.Status.COMPLETE
                    if session.is_complete
                    else AttendanceSession.Status.MISSING_OUT
                ),
            )
        )
    AttendanceSession.objects.bulk_create(sessions)


def locked_ranges(company_id):
    """The date ranges of every posted payroll run.

    A day inside one never changes again: salary has been paid against it.
    Read through the unscoped manager because this also runs from the
    ingestion path, where there is no ambient tenant.
    """
    from payroll.models import PayrollRun

    return list(
        PayrollRun.all_objects.filter(
            company_id=company_id, status=PayrollRun.Status.POSTED
        ).values_list(
            "payroll_period__start_date", "payroll_period__end_date"
        )
    )


def _is_locked(day, ranges):
    return any(start <= day <= end for start, end in ranges)


def recalculate(company_id, *, employee_ids=None, start, end, now=None):
    """Rebuild attendance for a range of days. The one way in.

    Called by ingestion for the days a punch batch touched, by the screens for
    days whose close has passed, and by scheduling/leave when the calendar
    moves. Nobody presses Calculate.

    ``employee_ids`` narrows it to the people actually affected; None means
    everybody with a placement in the range. Days inside a posted payroll run
    are skipped, not rewritten.

    Returns a summary dict. Safe to call with no shifts configured: it simply
    writes nothing rather than raising, because ingestion must never fail on
    a company that has not finished setting itself up.
    """
    from tenants.models import Company

    now = now or timezone.now()
    company = Company.objects.get(pk=company_id)
    company_tz = _zone(company.timezone)
    today = now.astimezone(company_tz).date()
    end = min(end, today)
    if start > end:
        return {"days": 0, "skipped_locked": 0}

    # One day either side: the window builder needs the day after the range to
    # know when the last day closes, and a night shift's scans can arrive
    # before its own window opens.
    calendar = WorkCalendar(company_id, start - datetime.timedelta(days=2),
                            end + datetime.timedelta(days=2))
    if not calendar.has_any_shift:
        return {"days": 0, "skipped_locked": 0, "no_shift": True}

    posted = locked_ranges(company_id)
    written = defaultdict(int)
    touched_ids = []

    with use_company(company_id):
        settings = CompanyAttendanceSettings.objects.get()
        lookback = start - datetime.timedelta(days=1)
        span_days = [
            lookback + datetime.timedelta(days=offset)
            for offset in range((end - lookback).days + 3)
        ]
        range_start = datetime.datetime.combine(
            start - datetime.timedelta(days=1), datetime.time.min, tzinfo=company_tz
        )
        range_end = datetime.datetime.combine(
            end + datetime.timedelta(days=2), datetime.time.min, tzinfo=company_tz
        )

        assignments_by_employee = defaultdict(list)
        assignment_filter = (
            EmployeeAssignment.objects.select_related("branch")
            .exclude(status=EmployeeAssignment.Status.CANCELLED)
            .filter(effective_from__lt=range_end)
            .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=range_start))
        )
        if employee_ids is not None:
            assignment_filter = assignment_filter.filter(
                employee_id__in=list(employee_ids)
            )
        for assignment in assignment_filter.order_by("-effective_from"):
            assignments_by_employee[assignment.employee_id].append(assignment)
        employees = {
            e.pk: e
            for e in Employee.objects.filter(pk__in=assignments_by_employee.keys())
        }

        punches_by_employee = defaultdict(list)
        for employee_id, at, punch_id in (
            PunchEvent.objects.filter(
                employee_id__in=employees.keys(),
                authorization_status=PunchEvent.AuthorizationStatus.AUTHORIZED,
                punched_at_utc__gte=range_start,
                punched_at_utc__lt=range_end,
            )
            .exclude(dedupe_status=PunchEvent.DedupeStatus.CONFIRMED_DUPLICATE)
            .values_list("employee_id", "punched_at_utc", "pk")
        ):
            punches_by_employee[employee_id].append((at, punch_id))

        leave_by_key = {
            (day.employee_id, day.work_date): day
            for day in LeaveDay.objects.filter(
                employee_id__in=employees.keys(), work_date__gte=start,
                work_date__lte=end, status__in=LIVE_LEAVE,
            )
        }
        overtime_by_key = _overtime_decisions(employees.keys(), lookback, end)

        for employee_id, employee in employees.items():
            assignments = assignments_by_employee[employee_id]
            punches = sorted(punches_by_employee[employee_id])

            # One employee's windows: the shift can differ per day because it
            # follows the placement's department — unless this employee has a
            # shift of their own, which wins. Without employee_id that
            # override is silently ignored.
            def shift_on(on, _assignments=assignments, _employee_id=employee_id):
                probe = datetime.datetime.combine(
                    on, datetime.time(12), tzinfo=company_tz
                )
                placement = _assignment_on(_assignments, probe)
                if placement is None:
                    return None
                return calendar.shift_for(
                    placement.department_id, on, employee_id=_employee_id
                )

            windows = day_window.build_windows(
                days=span_days, shift_for=shift_on, tz=company_tz,
                window_before_minutes=settings.attendance_window_before_minutes,
            )

            # A night shift that began the day before ``start`` owns scans
            # made on ``start`` morning, so that day is written too — but only
            # when it really holds one. Widening unconditionally would invent
            # an absent day every time a punch arrived.
            earlier = windows[lookback]
            first_day = (
                lookback
                if any(earlier.contains(p[0]) for p in punches)
                else start
            )
            day = first_day
            while day <= end:
                outcome = _write_day(
                    day=day, employee=employee, assignments=assignments,
                    window=windows[day], punches=punches, settings=settings,
                    calendar=calendar, leave_by_key=leave_by_key,
                    company=company, company_tz=company_tz, now=now,
                    locked=_is_locked(day, posted),
                    overtime=overtime_by_key.get((employee_id, day)),
                )
                if outcome is None:
                    written["skipped"] += 1
                elif outcome == "locked":
                    written["skipped_locked"] += 1
                else:
                    touched_ids.append(outcome.pk)
                    written[outcome.attendance_status] += 1
                day += datetime.timedelta(days=1)

        # Days that no longer apply (before joining, no placement, no shift)
        # are removed so a recalculation leaves exactly the current answer.
        stale = AttendanceRecord.objects.filter(
            work_date__gte=start, work_date__lte=end
        ).exclude(pk__in=touched_ids)

        if employee_ids is not None:
            stale = stale.filter(employee_id__in=list(employee_ids))
        for locked_start, locked_end in posted:
            stale = stale.exclude(
                work_date__gte=locked_start, work_date__lte=locked_end
            )
        AttendanceSession.objects.filter(attendance_record__in=stale).delete()
        PunchAllocation.objects.filter(attendance_record__in=stale).delete()
        stale.delete()

    written["days"] = len(touched_ids)
    return dict(written)


def _overtime_decisions(employee_ids, start, end):
    """Approved overtime minutes by (employee, day), from payroll's decisions.

    The decision belongs to salary (plan step A9, payroll.overtime); the day
    only carries its result. Rewriting the day must not drop it, so it is
    read back here. A rejected day maps to 0.
    """
    from payroll.models import OvertimeDecision

    return {
        (employee_id, work_date): minutes
        for employee_id, work_date, minutes in OvertimeDecision.objects.filter(
            employee_id__in=list(employee_ids), work_date__gte=start, work_date__lte=end,
        ).values_list("employee_id", "work_date", "approved_minutes")
    }


def _write_day(*, day, employee, assignments, window, punches, settings,
               calendar, leave_by_key, company, company_tz, now, locked,
               overtime=None):
    """One employee-day. Returns the record, "locked", or None for no record.

    ``overtime`` is the approved minutes of a decision on this day (payroll's
    A9), or None when nobody has decided it yet.
    """
    if locked:
        return "locked"
    if (employee.joining_date and day < employee.joining_date) or (
        employee.leaving_date and day > employee.leaving_date
    ):
        return None

    probe = datetime.datetime.combine(day, datetime.time(12), tzinfo=company_tz)
    assignment = _assignment_on(assignments, probe)
    if assignment is None:
        return None

    leave = leave_by_key.get((employee.pk, day))
    info = calendar.day(assignment.branch_id, day)
    day_punches = [p for p in punches if window.contains(p[0])]
    is_closed = window.is_closed(now)

    values = {
        "employee_assignment": assignment,
        "branch": assignment.branch,
        "shift": window.shift,
        "scheduled_start_at": window.scheduled_start,
        "scheduled_end_at": window.scheduled_end,
        "first_in_at": None, "last_out_at": None,
        "worked_minutes": 0, "total_minutes": 0,
        "break_minutes": 0, "outside_minutes": 0, "break_count": 0,
        "calculated_overtime_minutes": 0, "approved_overtime_minutes": 0,
        "late_minutes": 0, "early_out_minutes": 0,
        "check_out_by_rule": False, "is_open": not is_closed,
        "review_status": ReviewStatus.CLEAN, "review_reason": "",
        "leave_day": None, "note": "",
        "calculated_at": now,
    }

    if leave is not None:
        values.update(
            attendance_status=AttendanceRecord.AttendanceStatus.LEAVE,
            punch_status=AttendanceRecord.PunchStatus.NO_PUNCH,
            payable_fraction=leave.approved_pay_percentage / Decimal("100"),
            leave_day=leave, is_open=False,
            note=f"{leave.approved_pay_type.title()} leave",
        )
        paired = None
    elif info.kind in (HOLIDAY, WEEKLY_OFF) and not day_punches:
        values.update(
            attendance_status=(
                AttendanceRecord.AttendanceStatus.HOLIDAY if info.kind == HOLIDAY
                else AttendanceRecord.AttendanceStatus.WEEKLY_OFF
            ),
            punch_status=AttendanceRecord.PunchStatus.NO_PUNCH,
            payable_fraction=ONE if info.is_paid else NONE,
            is_open=False, note=info.label,
        )
        paired = None
    elif info.kind in (HOLIDAY, WEEKLY_OFF):
        # Somebody came in on their day off. The day keeps its holiday status
        # and pay; the scans are recorded so the work is visible and A9 can
        # decide what it is worth.
        paired = _pair(day_punches, window, settings, is_closed)
        values.update(
            attendance_status=(
                AttendanceRecord.AttendanceStatus.HOLIDAY if info.kind == HOLIDAY
                else AttendanceRecord.AttendanceStatus.WEEKLY_OFF
            ),
            punch_status=AttendanceRecord.PunchStatus.COMPLETE,
            payable_fraction=ONE if info.is_paid else NONE,
            **_measurements(paired),
        )
        values["note"] = f"{info.label} — worked."
        values["worked_minutes"] = 0
        values["is_open"] = not is_closed
    else:
        if window.shift is None:
            return None
        if not day_punches and not is_closed:
            # "Not in yet": the shift has not finished, so nobody is absent.
            return None
        paired = _pair(day_punches, window, settings, is_closed)
        status, punch_status, fraction, minutes = _classify_working_day(
            window.shift, settings, paired
        )
        values.update(
            attendance_status=status, punch_status=punch_status,
            payable_fraction=fraction, **minutes,
        )
        values["is_open"] = not is_closed

    if overtime is not None and paired is not None:
        values["approved_overtime_minutes"] = overtime
        if paired.open_overtime:
            # The open overtime session was what needed a look, and it has had one.
            values["review_status"] = ReviewStatus.REVIEWED

    record, _ = AttendanceRecord.objects.update_or_create(
        company=company, employee=employee, work_date=day, defaults=values,
    )
    _write_pairing(record, paired)
    return record


def _pair(day_punches, window, settings, is_closed):
    shift = window.shift
    return pairing.build_day(
        day_punches,
        window_seconds=settings.duplicate_punch_window_seconds,
        break_minutes=getattr(shift, "default_break_minutes", 0),
        break_is_paid=getattr(shift, "break_is_paid", False),
        scheduled_start=window.scheduled_start,
        scheduled_end=window.scheduled_end,
        grace_in_minutes=getattr(shift, "grace_in_minutes", 0),
        grace_out_minutes=getattr(shift, "grace_out_minutes", 0),
        overtime_after_minutes=getattr(shift, "overtime_after_minutes", 0),
        is_closed=is_closed,
    )


def refresh(company_id, *, employee_ids=None, start, end, now=None):
    """Bring a range up to date before it is read.

    The screens call this: a day whose close has passed, or one never written
    because its shift had not finished, is rebuilt so the reader sees the
    finished answer rather than yesterday's provisional one. Days that are
    already closed and already stored are left alone, so opening a month of
    settled history costs one query.
    """
    now = now or timezone.now()
    with use_company(company_id):
        stored = AttendanceRecord.objects.filter(
            work_date__gte=start, work_date__lte=end
        )
        if employee_ids is not None:
            stored = stored.filter(employee_id__in=list(employee_ids))
        open_days = stored.filter(is_open=True).exists()
        anything_stored = stored.exists()
        # A day that was never written (nobody had scanned before its shift
        # ended) has no row to look at, so a range that reaches today always
        # gets one pass.
        reaches_today = end >= now.astimezone(
            _zone(_company_timezone(company_id))
        ).date()
    # Settled history is left alone, which is what makes opening an old month
    # cost one query. A range holding nothing at all is not settled history —
    # it has simply never been built, and skipping it was how a month that
    # nobody had calculated stayed empty for ever.
    if anything_stored and not open_days and not reaches_today:
        return {"days": 0, "unchanged": True}
    return recalculate(
        company_id, employee_ids=employee_ids, start=start, end=end, now=now
    )


def _company_timezone(company_id):
    from tenants.models import Company

    return (
        Company.objects.filter(pk=company_id)
        .values_list("timezone", flat=True)
        .first()
        or "UTC"
    )


def recalculate_for_punches(company_id, employee_days):
    """Rebuild exactly the employee-days a punch batch touched.

    ``employee_days`` is an iterable of (employee_id, date). A device coming
    back from a week offline sends a backlog, so this groups by employee and
    covers the span each one actually touched rather than recalculating a
    month for everybody.

    Never raises into the ingestion path: a punch is evidence and must be
    stored even if attendance cannot be worked out yet.
    """
    from collections import defaultdict as _defaultdict

    spans = _defaultdict(list)
    for employee_id, day in employee_days:
        if employee_id and day:
            spans[employee_id].append(day)
    if not spans:
        return {"days": 0}

    summary = {"days": 0}
    for employee_id, days in spans.items():
        try:
            result = recalculate(
                company_id, employee_ids=[employee_id],
                start=min(days), end=max(days),
            )
        except Exception:  # pragma: no cover - ingestion must not fail
            logger.exception(
                "Attendance recalculation failed for employee %s", employee_id
            )
            continue
        summary["days"] += result.get("days", 0)
    return summary


@transaction.atomic
def calculate_attendance(*, actor, company_id, year, month):
    """(Re)calculate one month. Kept for payroll generation.

    Attendance is live now — ``recalculate`` runs on its own when punches
    arrive and when days close — so nothing on screen calls this. Payroll
    still brings a month fully up to date before it generates.
    """
    membership = require_structure_manager(actor, company_id)
    first, last = month_bounds(year, month)
    today = timezone.localdate()
    if first > today:
        raise ValidationError("That month has not started yet.")

    calendar = WorkCalendar(company_id, first, min(last, today))
    if not calendar.has_any_shift:
        raise ValidationError(
            "Set up shifts under Shifts first: a shift for each department, or a "
            "company shift. Attendance is measured against the shift."
        )

    summary = recalculate(company_id, start=first, end=last)
    summary["month"] = f"{year}-{month:02d}"
    with use_company(company_id):
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="attendance.calculated", obj=membership.company, after=summary,
        )
    return summary
