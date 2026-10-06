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

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from attendance import corrections as correction_input, day_window, pairing
from attendance.models import (
    AttendanceDue,
    AttendanceRecord,
    AttendanceSession,
    PunchAllocation,
    ReviewStatus,
)
from auditlog.services import record_company_event
from common.tenant import use_company
from devices.models import PunchEvent
from employees import inactive as inactive_periods
from employees.models import Employee, EmployeeAssignment
from leaves.models import LeaveDay
from organization.services import require_company_membership
from scheduling.calendar import HOLIDAY, WEEKLY_OFF, WorkCalendar
from scheduling.models import CompanyAttendanceSettings

logger = logging.getLogger(__name__)

ONE, HALF, NONE = Decimal("1"), Decimal("0.5"), Decimal("0")

# Unusual days (plan step N11). A missed scan does not always leave a day
# without a check-out: somebody who scans out for tea and walks back in behind a
# colleague ends the day on that tea-break OUT, and the day simply looks short.
# These send such a finished working day to Days to review. They change nothing
# the day counts or pays; somebody looks, then adds the missing scan or accepts
# the day as it is.
EARLY_CHECK_OUT = "checked out long before the shift end"
LONG_OUTSIDE = "long time outside during the shift"
#: Checked out at least this long before the shift end.
EARLY_CHECK_OUT_REVIEW_MINUTES = 120
#: Outside during the shift for this much longer than the shift's break.
LONG_OUTSIDE_REVIEW_MINUTES = 60
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
        "in_office_minutes": day.in_office_minutes,
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


def _outside_during_shift(day, scheduled_start, scheduled_end):
    """Minutes between sessions that fall inside the scheduled shift."""
    if scheduled_start is None or scheduled_end is None:
        return day.outside_minutes
    total = 0
    for earlier, later in zip(day.sessions, day.sessions[1:]):
        if earlier.ended_at is None:
            continue
        if day.has_check_out and day.last_out_at and earlier.ended_at >= day.last_out_at:
            continue              # back in after the check-out: not a break
        start = max(earlier.ended_at, scheduled_start)
        end = min(later.started_at, scheduled_end)
        if end > start:
            total += int((end - start).total_seconds() // 60)
    return total


def unusual_reason(shift, day, scheduled_start=None, scheduled_end=None):
    """Why a finished, otherwise clear working day should get a look, or ""."""
    if not day.is_closed or not day.kept or day.needs_review:
        return ""
    if day.early_out_minutes >= EARLY_CHECK_OUT_REVIEW_MINUTES:
        return EARLY_CHECK_OUT
    allowance = getattr(shift, "default_break_minutes", 0) or 0
    outside = _outside_during_shift(day, scheduled_start, scheduled_end)
    if outside > allowance + LONG_OUTSIDE_REVIEW_MINUTES:
        return LONG_OUTSIDE
    return ""


def _classify_working_day(shift, settings, day, *, scheduled_start=None,
                          scheduled_end=None, flag_unusual=True):
    """Turn a paired day into the record's status and minute fields.

    ``day`` is an ``attendance.pairing.Day``: the labelling, the minutes, the
    lateness and the review reason are already decided there. This maps them
    onto the record and applies the company's missing-punch policy.

    ``flag_unusual`` sends a short day or a long time outside to review
    (N11). Off for a half-day leave, where leaving early is the point.
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

    checked_out = (
        # Checked out though the day has not closed yet (that waits for the
        # next shift, or 24 hours): judged now, and judged again if they scan
        # once more (Nihal, 2026-10-03: checked out 12:51 on a shift ending
        # 12:50, still "In progress" at 13:21). An open day only has a
        # check-out once the shift's end has passed: a scan out after it, or
        # - with "first and last scan" - the latest scan when it passed.
        not day.is_closed and day.has_check_out and not day.check_out_by_rule
    )
    if not day.is_closed and not checked_out:
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
    if checked_out:
        notes.append("Checked out; this can still change until the day closes.")
    minutes["note"] = " ".join(notes)[:255]
    if flag_unusual:
        reason = unusual_reason(shift, day, scheduled_start, scheduled_end)
        if reason:
            minutes["review_status"] = ReviewStatus.NEEDS_REVIEW
            minutes["review_reason"] = reason
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
        punch_id, correction_id = correction_input.source_ids(scan.punch_event_id)
        allocations.append(
            PunchAllocation(
                company_id=record.company_id,
                attendance_record=record,
                punch_event_id=punch_id,
                attendance_correction_id=correction_id,
                sequence_number=index,
                event_at=scan.at,
                interpreted_direction=scan.direction,
                label=scan.label,
                is_included=True,
                interpretation_note=scan.note,
            )
        )
    for index, (at, source) in enumerate(paired.dropped, start=len(allocations) + 1):
        punch_id, correction_id = correction_input.source_ids(source)
        allocations.append(
            PunchAllocation(
                company_id=record.company_id,
                attendance_record=record,
                punch_event_id=punch_id,
                attendance_correction_id=correction_id,
                sequence_number=index,
                event_at=at,
                interpreted_direction=PunchAllocation.Direction.IGNORED,
                label=PunchAllocation.Label.IGNORED,
                is_included=False,
                exclusion_reason=PunchAllocation.ExclusionReason.DUPLICATE,
                interpretation_note="Scanned again only seconds after the scan before.",
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
    # When each day built here next changes with time alone (AttendanceDue).
    due_rows = []
    lookback_rebuilt = []

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
            for day in LeaveDay.objects.select_related("request_segment").filter(
                employee_id__in=employees.keys(), work_date__gte=start,
                work_date__lte=end, status__in=LIVE_LEAVE,
            )
        }
        overtime_by_key = _overtime_decisions(employees.keys(), lookback, end)
        # Inactive periods (2026-09-29): those days are Inactive, not worked.
        inactive_by_employee = inactive_periods.periods(company_id, employees.keys(),
                                                        lookback, end)
        # What people fixed by hand (plan step N5). Read in on every pass, so a
        # correction survives the next punch rather than being overwritten.
        fixes = correction_input.in_force(
            employees.keys(), since=range_start, until=range_end,
            start=lookback, end=end,
        )

        for employee_id, employee in employees.items():
            assignments = assignments_by_employee[employee_id]
            punches = sorted(
                punches_by_employee[employee_id] + fixes.scans.get(employee_id, []),
                key=correction_input.stream_order,
            )

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
            if first_day == lookback:
                lookback_rebuilt.append(employee_id)
            day = first_day
            while day <= end:
                window = windows[day]
                outcome = _write_day(
                    day=day, employee=employee, assignments=assignments,
                    window=window, punches=punches, settings=settings,
                    calendar=calendar, leave_by_key=leave_by_key,
                    company=company, company_tz=company_tz, now=now,
                    locked=_is_locked(day, posted),
                    overtime=overtime_by_key.get((employee_id, day)),
                    status_fix=fixes.statuses.get((employee_id, day)),
                    accepted_reason=fixes.accepted.get((employee_id, day)),
                    late_excused=(fixes.excused or {}).get((employee_id, day)),
                    inactive=inactive_periods.on(
                        inactive_by_employee.get(employee_id, ()), day),
                )
                if outcome is None:
                    written["skipped"] += 1
                elif outcome == "locked":
                    written["skipped_locked"] += 1
                elif outcome == "pending":
                    written["skipped"] += 1
                    due_rows.append(AttendanceDue(company_id=company_id,
                                                  employee_id=employee_id, work_date=day,
                                                  due_at=window.closes_at))
                else:
                    touched_ids.append(outcome.pk)
                    written[outcome.attendance_status] += 1
                    if outcome.is_open:
                        due_rows.append(AttendanceDue(
                            company_id=company_id, employee_id=employee_id, work_date=day,
                            due_at=_next_change(window, now)))
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

        # What these days wait for now replaces what they waited for before: a
        # day built final has nothing left to wait for.
        dues = AttendanceDue.objects.filter(company_id=company_id)
        rebuilt = Q(work_date__gte=start, work_date__lte=end)
        if lookback_rebuilt:
            rebuilt |= Q(work_date=lookback, employee_id__in=lookback_rebuilt)
        dues = dues.filter(rebuilt)
        if employee_ids is not None:
            dues = dues.filter(employee_id__in=list(employee_ids))
        dues.delete()
        AttendanceDue.objects.bulk_create(due_rows, ignore_conflicts=True)

    written["days"] = len(touched_ids)
    # The ERP webhook (2026-10-01): check-ins and finished days go to the
    # company's own system, when it has one set up. It never fails this.
    from webhooks.services import note_days

    note_days(company_id, touched_ids)
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
               overtime=None, status_fix=None, accepted_reason=None, late_excused=None,
               inactive=None):
    """One employee-day. Returns the record, "locked", None for no record, or
    "pending" - no record *yet*: the day has not closed and nobody has come in,
    so it becomes absent (or leave) when it closes.

    ``overtime`` is the approved minutes of a decision on this day (payroll's
    A9), or None when nobody has decided it yet. ``status_fix`` and
    ``accepted_reason`` are corrections a person made to this day (N5);
    ``late_excused`` a late arrival somebody approved (2026-09-27);
    ``inactive`` the inactive period covering the day (2026-09-29), which
    wins over everything: nothing worked, nothing paid, not absent.
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

    # No shift, no record — on every kind of day, not just a working one. A
    # record's scheduled start and end cannot be blank, and an employee whose
    # department has no shift has nothing to measure a holiday or a leave day
    # against either. Checked before the day's kind for that reason.
    if window.shift is None:
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

    if inactive is not None:
        # Their scans that day were blocked (devices' EMPLOYEE_INACTIVE), and a
        # scan added by hand does not count either: the day is not theirs.
        values.update(
            attendance_status=AttendanceRecord.AttendanceStatus.INACTIVE,
            punch_status=AttendanceRecord.PunchStatus.NO_PUNCH,
            payable_fraction=NONE, is_open=False,
            note=f"Inactive: {inactive.reason}"[:255],
        )
        paired = None
    elif leave is not None and leave.balance_units < ONE:
        # Part of the day on leave: a half day (A10) or some hours (Phase E).
        # Came in: the day counts, less the unpaid share of the leave part.
        # No scans: only the paid share of the leave part counts. With the
        # paid share p and the leave's part of the day f (docs/LEAVE_FULL_DESIGN.md):
        # payable 1 - f(1-p) or f*p - for a paid or unpaid half day exactly
        # what it always was (1 / 0.5, and 0.5 / 0).
        share = leave.approved_pay_percentage / Decimal("100")
        fraction = leave.balance_units
        note = f"{_part_label(leave)} leave ({_pay_word(leave)})"
        if day_punches:
            paired = _pair(day_punches, window, settings, is_closed, now)
            status, punch_status, _, minutes = _classify_working_day(
                window.shift, settings, paired, flag_unusual=False,
            )
            values.update(punch_status=punch_status, **minutes)
            late, early = _excused(leave, window, minutes)
            values.update(
                attendance_status=(
                    AttendanceRecord.AttendanceStatus.PRESENT if is_closed else status
                ),
                payable_fraction=(ONE - fraction * (ONE - share)) if is_closed else NONE,
                late_minutes=late, early_out_minutes=early,
                leave_day=leave, note=note, is_open=not is_closed,
            )
        elif not is_closed:
            return "pending"
        else:
            values.update(
                attendance_status=AttendanceRecord.AttendanceStatus.LEAVE,
                punch_status=AttendanceRecord.PunchStatus.NO_PUNCH,
                payable_fraction=fraction * share,
                leave_day=leave, is_open=False,
                note=f"{note}; did not come in for the rest of the day",
            )
            paired = None
    elif leave is not None:
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
        paired = _pair(day_punches, window, settings, is_closed, now)
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
        if not day_punches and not is_closed:
            # "Not in yet": the shift has not finished, so nobody is absent.
            return "pending"
        paired = _pair(day_punches, window, settings, is_closed, now)
        status, punch_status, fraction, minutes = _classify_working_day(
            window.shift, settings, paired,
            scheduled_start=window.scheduled_start, scheduled_end=window.scheduled_end,
        )
        values.update(
            attendance_status=status, punch_status=punch_status,
            payable_fraction=fraction, **minutes,
        )
        values["is_open"] = not is_closed
        if status_fix is not None and is_closed:
            # A person said what this day was. The scans and minutes stay as
            # measured; the status and what it pays follow the correction.
            values.update(_status_from_fix(status_fix))
        if late_excused is not None and values.get("late_minutes"):
            # Their late arrival was approved: no late minutes, so no late
            # penalty and no Late entry. The scans stay as they were.
            values["late_minutes"] = 0
            values["note"] = f"Late approved: {late_excused.reason}"[:255]

    if paired is not None:
        # Overtime's approval rule lives with overtime (payroll, plan step A9):
        # a decision if there is one, otherwise automatic when scanned out.
        from payroll.overtime import approved_minutes

        values["approved_overtime_minutes"] = approved_minutes(
            paired, day_off=info.kind in (HOLIDAY, WEEKLY_OFF), decided=overtime,
        )
        if employee.no_overtime_from is not None and day >= employee.no_overtime_from:
            # Overtime is not allowed for them from that day (2026-09-27).
            values["approved_overtime_minutes"] = 0
        if overtime is not None and paired.open_overtime:
            # The open overtime session was what needed a look, and it has had one.
            values["review_status"] = ReviewStatus.REVIEWED

    if (
        accepted_reason
        and values["review_status"] == ReviewStatus.NEEDS_REVIEW
        and values["review_reason"] == accepted_reason
    ):
        # Somebody looked at exactly this and said it is right. A different
        # reason turning up later is a new question, so it is not covered.
        values["review_status"] = ReviewStatus.REVIEWED

    record, _ = AttendanceRecord.objects.update_or_create(
        company=company, employee=employee, work_date=day, defaults=values,
    )
    _write_pairing(record, paired)
    return record


def _part_label(leave):
    segment = leave.request_segment
    if segment.duration_type == "hourly" and segment.start_time and segment.end_time:
        return f"{segment.start_time:%H:%M}-{segment.end_time:%H:%M}"
    return {"morning": "Morning half-day", "afternoon": "Afternoon half-day"}.get(
        segment.half_day_part, "Half-day")


def _pay_word(leave):
    if leave.approved_pay_type == "partial":
        return f"{leave.approved_pay_percentage.normalize():f}% paid"
    return leave.approved_pay_type


def _excused(leave, window, minutes):
    """The late and early-out minutes left on a day with part of it on leave.

    Leave at the start of the shift (a morning half, hours from its start)
    excuses lateness up to its length; at the end (an afternoon half, hours to
    its end), leaving early. Hours in the middle excuse neither. A half day
    with no part - every one recorded before Phase E - excuses both, as it
    always did."""
    segment = leave.request_segment
    at_start = leave.covered_start_at <= window.scheduled_start if window.scheduled_start else False
    at_end = leave.covered_end_at >= window.scheduled_end if window.scheduled_end else False
    if segment.duration_type == "half_day" and not segment.half_day_part:
        return 0, 0
    late, early = minutes.get("late_minutes", 0), minutes.get("early_out_minutes", 0)
    if at_start:
        late = max(0, late - leave.leave_minutes)
    if at_end:
        early = max(0, early - leave.leave_minutes)
    return late, early


def _status_from_fix(fix):
    """The record fields a "change status" correction sets."""
    status = fix.proposed_status
    fraction = {"present": ONE, "half_day": HALF}.get(status, NONE)
    label = dict(AttendanceRecord.AttendanceStatus.choices).get(status, status)
    return {
        "attendance_status": status,
        "payable_fraction": fraction,
        "review_status": ReviewStatus.REVIEWED,
        "note": f"Marked {label.lower()} by hand: {fix.reason}"[:255],
    }


def _pair(day_punches, window, settings, is_closed, now=None):
    shift = window.shift
    first_last = (getattr(settings, "punch_pairing_strategy", "")
                  == CompanyAttendanceSettings.PairingStrategy.FIRST_LAST)
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
        first_last=first_last,
        shift_over=bool(now and window.scheduled_end and now >= window.scheduled_end),
    )


def _next_change(window, now):
    """When an open day next changes with time alone: its shift's end (a last
    scan out becomes the check-out), then its close (the day becomes final)."""
    if window.scheduled_end is not None and now < window.scheduled_end:
        return window.scheduled_end
    return window.closes_at


def _spans(days):
    """Sorted dates -> ``[(first, last)]`` runs of consecutive days."""
    spans = []
    for day in days:
        if spans and day == spans[-1][1] + datetime.timedelta(days=1):
            spans[-1][1] = day
        else:
            spans.append([day, day])
    return [tuple(span) for span in spans]


def _add(summary, result):
    for key, value in result.items():
        if isinstance(value, int) and not isinstance(value, bool):
            summary[key] += value


def refresh(company_id, *, employee_ids=None, start, end, now=None):
    """Bring a range up to date before it is read - only what is due.

    What is saved is the answer: every change rewrites the days it touches the
    moment it happens (a scan, a fix, leave, a schedule). What time alone
    changes - a shift ending, a day closing - is waited for one employee-day
    at a time (``AttendanceDue``). So a reader:

    - reads settled history (nothing open, before today, stored) as it is;
    - builds, once, a day never built for everybody (a new day; a new person);
    - rebuilds only the employee-days whose next change has come
      (``settle_due``) - not everybody's, however many people there are.

    (2026-10-06: it was a rebuild of every day of the month so far, for
    everyone, on every load; then a rebuild of today and yesterday for
    everyone every few minutes.)
    """
    from attendance.models import AttendanceDayBuild

    now = now or timezone.now()
    tz = _zone(_company_timezone(company_id))
    today = now.astimezone(tz).date()
    with use_company(company_id):
        stored = AttendanceRecord.objects.filter(
            work_date__gte=start, work_date__lte=end
        )
        if employee_ids is not None:
            stored = stored.filter(employee_id__in=list(employee_ids))
        open_days = stored.filter(is_open=True).exists()
        anything_stored = stored.exists()
    # Settled history is left alone, which is what makes opening an old month
    # cost one query. A range holding nothing at all is not settled history —
    # it has simply never been built, and skipping it was how a month that
    # nobody had calculated stayed empty for ever.
    if anything_stored and not open_days and end < today:
        return {"days": 0, "unchanged": True}
    last = min(end, today)
    built = set(AttendanceDayBuild.objects.filter(
        company_id=company_id, work_date__gte=start, work_date__lte=last,
    ).values_list("work_date", flat=True))
    never = []
    day = start
    while day <= last:
        if day not in built:
            never.append(day)
        day += datetime.timedelta(days=1)

    summary = defaultdict(int)
    no_shift = False
    for first, final in _spans(never):
        result = recalculate(company_id, employee_ids=employee_ids, start=first, end=final,
                             now=now)
        no_shift = no_shift or bool(result.get("no_shift"))
        _add(summary, result)
    if never and employee_ids is None and not no_shift:
        # Built for everybody: from now on only its due employee-days are.
        # (A no-shift company built nothing; its first shift builds the days.)
        AttendanceDayBuild.objects.bulk_create(
            [AttendanceDayBuild(company_id=company_id, work_date=day, built_at=now)
             for day in never],
            update_conflicts=True, unique_fields=["company", "work_date"],
            update_fields=["built_at"])
    _add(summary, settle_due(company_id, employee_ids=employee_ids, start=start, end=last,
                             now=now))
    if not summary.get("days") and not never:
        return {"days": 0, "unchanged": True}
    return dict(summary)


def settle_due(company_id, *, employee_ids=None, start=None, end=None, now=None):
    """Rebuild exactly the employee-days whose next change has come - a shift
    has ended, a day has closed. Cheap when nothing is due (one query), and
    proportional to what changed, never to how many people there are."""
    from attendance.models import AttendanceDue

    now = now or timezone.now()
    due = AttendanceDue.objects.filter(company_id=company_id, due_at__lte=now)
    if employee_ids is not None:
        due = due.filter(employee_id__in=list(employee_ids))
    if start is not None:
        due = due.filter(work_date__gte=start)
    if end is not None:
        due = due.filter(work_date__lte=end)
    by_employee = defaultdict(list)
    for employee_id, work_date in due.values_list("employee_id", "work_date"):
        by_employee[employee_id].append(work_date)
    if not by_employee:
        return {"days": 0}
    # Everybody whose due days form the same run is rebuilt in one pass: at a
    # shift's end that is one pass for all of that shift's people.
    people_by_span = defaultdict(list)
    for employee_id, days in by_employee.items():
        for span in _spans(sorted(set(days))):
            people_by_span[span].append(employee_id)
    summary = defaultdict(int)
    for (first, final), people in sorted(people_by_span.items()):
        _add(summary, recalculate(company_id, employee_ids=people, start=first, end=final,
                                  now=now))
    return dict(summary)


def forget_built(company_id, since):
    """Days from ``since`` must be built again for everybody - someone appeared
    whose days were never written (a new employee). Their next reader builds
    them."""
    from attendance.models import AttendanceDayBuild

    AttendanceDayBuild.objects.filter(company_id=company_id, work_date__gte=since).delete()


#: The background pass runs at most this often per company and server process.
BACKGROUND_EVERY_SECONDS = 30


def settle_recent(company_id, now=None):
    """The background pass (a device's check-in, or ``manage.py
    settle_attendance``): today and yesterday built for everybody if they never
    were, and every employee-day whose next change has come rebuilt."""
    now = now or timezone.now()
    today = now.astimezone(_zone(_company_timezone(company_id))).date()
    summary = defaultdict(int)
    _add(summary, refresh(company_id, start=today - datetime.timedelta(days=1), end=today,
                          now=now))
    # Older days still open (a night shift, a day nobody looked at): their
    # changes are due too.
    _add(summary, settle_due(company_id, now=now))
    return dict(summary)


def settle_soon(company_id):
    """A device checked in: bring today and yesterday up to date in the
    background, at most once a minute per server process - so a screen opened
    afterwards finds them already built and only reads."""
    from django.conf import settings
    from django.core.cache import cache
    from django.db import connection

    if not getattr(settings, "ATTENDANCE_SETTLE_IN_BACKGROUND", True):
        return
    if not cache.add(f"attendance-settle-{company_id}", 1, BACKGROUND_EVERY_SECONDS):
        return

    def run():
        try:
            settle_recent(company_id)
        except Exception:  # noqa: BLE001 - the next check-in tries again
            logger.exception("Attendance: background settling for company %s failed",
                             company_id)
        finally:
            connection.close()

    import threading

    threading.Thread(target=run, name=f"attendance-settle-{company_id}", daemon=True).start()


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

    So it is allowed to whoever may generate the whole month: the owner or
    company admin, or someone who prepares salary in every branch (the payroll
    manager). A branch's own salary uses ``_bring_branches_up_to_date`` instead.
    """
    from access_control.branch_access import ALL_BRANCHES, branches_for

    membership = require_company_membership(actor, company_id)
    if branches_for(actor, company_id, "salary.prepare") is not ALL_BRANCHES:
        raise PermissionDenied(
            "Calculating a whole month needs owner, company administrator or "
            "payroll manager access."
        )
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
