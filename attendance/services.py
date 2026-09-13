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
sessions, per DEVICE_ATTENDANCE_POLICY.md step 7. That is where the day's
minutes come from, so ``worked_minutes`` is in-office time plus any paid
break — not simply the span from the first scan to the last.

``payable_fraction`` is what payroll reads: 1, 0.5 or 0.
"""

import calendar as month_calendar
import datetime
import zoneinfo
from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from attendance import pairing
from attendance.models import AttendanceRecord, AttendanceSession, PunchAllocation
from auditlog.services import record_company_event
from common.tenant import use_company
from devices.models import PunchEvent
from employees.models import Employee, EmployeeAssignment
from leaves.models import LeaveDay
from organization.services import require_structure_manager
from scheduling.calendar import HOLIDAY, WEEKLY_OFF, WorkCalendar
from scheduling.models import CompanyAttendanceSettings

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


def _classify_working_day(shift, settings, day, scheduled_start):
    """Turn a paired day into the record's status and minute fields.

    ``day`` is an ``attendance.pairing.Day``: the labelling and the minutes are
    already decided, so this only maps them onto the record and applies the
    company's missing-punch policy.
    """
    blank = {
        "first_in_at": None, "last_out_at": None, "worked_minutes": 0,
        "total_minutes": 0, "break_minutes": 0, "outside_minutes": 0,
        "break_count": 0, "late_minutes": 0, "note": "",
    }
    if not day.kept:
        return (
            AttendanceRecord.AttendanceStatus.ABSENT,
            AttendanceRecord.PunchStatus.NO_PUNCH,
            NONE,
            blank,
        )

    late = max(
        0,
        int((day.first_in_at - scheduled_start).total_seconds() // 60)
        - shift.grace_in_minutes,
    )
    minutes = {
        "first_in_at": day.first_in_at,
        "last_out_at": day.last_out_at,
        "worked_minutes": day.worked_minutes,
        "total_minutes": day.total_minutes,
        "break_minutes": day.outside_minutes,
        "outside_minutes": day.outside_minutes,
        "break_count": day.break_count,
        "late_minutes": late,
        "note": "",
    }

    if not day.has_check_out:
        auto_absent = (
            settings.missing_punch_policy
            == CompanyAttendanceSettings.MissingPunchPolicy.AUTO_ABSENT
        )
        # No check-out, so the day has no end: the minutes measured so far are
        # kept as evidence but nothing is paid on a guess.
        minutes["note"] = (
            "No check-out; counted as absent by company setting."
            if auto_absent
            else "No check-out; counted as present until reviewed."
        )
        return (
            AttendanceRecord.AttendanceStatus.ABSENT if auto_absent
            else AttendanceRecord.AttendanceStatus.INCOMPLETE,
            AttendanceRecord.PunchStatus.MISSING_OUT,
            NONE if auto_absent else ONE,
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
    if day.break_count:
        minutes["note"] = (
            f"{day.break_count} break{'s' if day.break_count > 1 else ''}, "
            f"{day.outside_minutes} min outside."
        )
    return status, AttendanceRecord.PunchStatus.COMPLETE, fraction, minutes


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


@transaction.atomic
def calculate_attendance(*, actor, company_id, year, month):
    """(Re)calculate every employee's attendance for one month."""
    membership = require_structure_manager(actor, company_id)
    first, last = month_bounds(year, month)
    today = timezone.localdate()
    if first > today:
        raise ValidationError("That month has not started yet.")
    last = min(last, today)

    calendar = WorkCalendar(company_id, first, last)
    if not calendar.has_any_shift:
        raise ValidationError(
            "Set up shifts under Shifts first: a shift for each department, or a "
            "company shift. Attendance is measured against the shift."
        )

    with use_company(company_id):
        settings = CompanyAttendanceSettings.objects.get()
        company_tz = _zone(membership.company.timezone)
        range_start = datetime.datetime.combine(first, datetime.time.min, tzinfo=company_tz)
        range_end = datetime.datetime.combine(last + datetime.timedelta(days=2), datetime.time.min, tzinfo=company_tz)

        assignments_by_employee = defaultdict(list)
        for assignment in (
            EmployeeAssignment.objects.select_related("branch")
            .exclude(status=EmployeeAssignment.Status.CANCELLED)
            .filter(effective_from__lt=range_end)
            .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=range_start))
            .order_by("-effective_from")
        ):
            assignments_by_employee[assignment.employee_id].append(assignment)
        employees = {
            e.pk: e for e in Employee.objects.filter(pk__in=assignments_by_employee.keys())
        }

        # The punch id travels with the instant so each allocation can point
        # back at the evidence it came from.
        punches_by_employee = defaultdict(list)
        for employee_id, at, punch_id in (
            PunchEvent.objects.filter(
                employee_id__in=employees.keys(),
                authorization_status=PunchEvent.AuthorizationStatus.AUTHORIZED,
                punched_at_utc__gte=range_start - datetime.timedelta(days=1),
                punched_at_utc__lt=range_end,
            )
            .exclude(dedupe_status=PunchEvent.DedupeStatus.CONFIRMED_DUPLICATE)
            .values_list("employee_id", "punched_at_utc", "pk")
        ):
            punches_by_employee[employee_id].append((at, punch_id))

        leave_by_key = {
            (day.employee_id, day.work_date): day
            for day in LeaveDay.objects.filter(
                employee_id__in=employees.keys(), work_date__gte=first,
                work_date__lte=last, status__in=LIVE_LEAVE,
            )
        }

        before = timezone.now()
        written = defaultdict(int)
        kept_ids = []
        for employee_id, employee in employees.items():
            assignments = assignments_by_employee[employee_id]
            punches = sorted(punches_by_employee[employee_id])
            paired_days = {}
            day = first
            while day <= last:
                if (employee.joining_date and day < employee.joining_date) or (
                    employee.leaving_date and day > employee.leaving_date
                ):
                    day += datetime.timedelta(days=1)
                    continue
                # Midday decides which placement applies; the shift then
                # comes from that placement's department.
                probe = datetime.datetime.combine(day, datetime.time(12), tzinfo=company_tz)
                assignment = _assignment_on(assignments, probe)
                if assignment is None:
                    day += datetime.timedelta(days=1)
                    continue
                shift = calendar.shift_for(assignment.department_id, day)
                if shift is None:
                    # Department mode with neither a department nor a company
                    # shift: nothing to measure against, so no record.
                    written["no_shift"] += 1
                    day += datetime.timedelta(days=1)
                    continue
                tz = _zone(assignment.branch.timezone or membership.company.timezone)
                scheduled_start, scheduled_end = _window(shift, day, tz)
                window_start = scheduled_start - datetime.timedelta(minutes=settings.attendance_window_before_minutes)
                window_end = scheduled_end + datetime.timedelta(minutes=settings.attendance_window_after_minutes)
                day_punches = [p for p in punches if window_start <= p[0] <= window_end]

                values = {
                    "employee_assignment": assignment,
                    "branch": assignment.branch,
                    "shift": shift,
                    "scheduled_start_at": scheduled_start,
                    "scheduled_end_at": scheduled_end,
                    "first_in_at": None, "last_out_at": None,
                    "worked_minutes": 0, "total_minutes": 0,
                    "break_minutes": 0, "outside_minutes": 0,
                    "break_count": 0, "late_minutes": 0,
                    "leave_day": None, "note": "",
                    "calculated_at": before,
                }
                leave = leave_by_key.get((employee_id, day))
                info = calendar.day(assignment.branch_id, day)
                if leave is not None:
                    values.update(
                        attendance_status=AttendanceRecord.AttendanceStatus.LEAVE,
                        punch_status=AttendanceRecord.PunchStatus.NO_PUNCH,
                        payable_fraction=leave.approved_pay_percentage / Decimal("100"),
                        leave_day=leave,
                        note=f"{leave.approved_pay_type.title()} leave",
                    )
                elif info.kind in (HOLIDAY, WEEKLY_OFF):
                    values.update(
                        attendance_status=(
                            AttendanceRecord.AttendanceStatus.HOLIDAY if info.kind == HOLIDAY
                            else AttendanceRecord.AttendanceStatus.WEEKLY_OFF
                        ),
                        punch_status=AttendanceRecord.PunchStatus.NO_PUNCH,
                        payable_fraction=ONE if info.is_paid else NONE,
                        note=info.label,
                    )
                else:
                    paired = pairing.build_day(
                        day_punches,
                        window_seconds=settings.duplicate_punch_window_seconds,
                        break_minutes=shift.default_break_minutes,
                        break_is_paid=shift.break_is_paid,
                    )
                    status, punch_status, fraction, minutes = _classify_working_day(
                        shift, settings, paired, scheduled_start
                    )
                    values.update(
                        attendance_status=status, punch_status=punch_status,
                        payable_fraction=fraction, **minutes,
                    )
                    paired_days[day] = paired
                record, _ = AttendanceRecord.objects.update_or_create(
                    company=membership.company, employee=employee, work_date=day,
                    defaults=values,
                )
                kept_ids.append(record.pk)
                _write_pairing(record, paired_days.pop(day, None))
                written[values["attendance_status"]] += 1
                day += datetime.timedelta(days=1)

        # Days that no longer apply (before joining, no placement) are removed
        # so a recalculation leaves exactly the current answer.
        stale = AttendanceRecord.objects.filter(
            work_date__gte=first, work_date__lte=month_bounds(year, month)[1]
        ).exclude(pk__in=kept_ids)
        AttendanceSession.objects.filter(attendance_record__in=stale).delete()
        PunchAllocation.objects.filter(attendance_record__in=stale).delete()
        stale.delete()

        summary = {"month": f"{year}-{month:02d}", "employees": len(employees), **written}
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="attendance.calculated", obj=membership.company, after=summary,
        )
    return summary
