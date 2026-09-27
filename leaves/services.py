"""Leave writes: leave types, and recording approved leave.

Thin slice of the 2026-09-12 salary fast-track. A company administrator
records leave that is already approved — full days, paid or unpaid — and the
service expands it into one ``LeaveDay`` per working day, which attendance and
payroll read. Weekly offs and holidays inside the range are skipped: a Thursday
to Saturday unpaid leave with Friday off costs two days, not three.

Same contract as the other company services: membership, owner/company-admin
role, whitelisted fields, validation through ``full_clean``, and one
transaction with the audit row. Nothing is deleted — a mistaken leave is
cancelled, and its days stop counting when attendance is recalculated.
"""

import datetime
import zoneinfo
from collections import Counter
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from accounts.models import CompanyMembership
from auditlog.services import record_company_event
from common.choices import ActiveStatus
from common.services import create_validated
from common.tenant import use_company
from employees.models import Employee, EmployeeAssignment
from leaves import documents
from leaves.models import LeaveDay, LeaveRequest, LeaveRequestSegment, LeaveType, PayType
from organization.services import (
    STRUCTURE_ROLES,
    require_company_membership,
    require_structure_manager,
)
from scheduling.calendar import WORKING, WorkCalendar

LEAVE_TYPE_FIELDS = ("code", "name", "days_per_year", "requires_attachment_by_default",
                     "description")
RECORD_FIELDS = ("employee", "leave_type", "start_date", "end_date", "duration", "pay_type", "reason")

# A leave longer than this is almost certainly a typo in the year.
MAX_LEAVE_DAYS = 366

# HR records and cancels leave; leave types stay with owners and administrators.
LEAVE_RECORDER_ROLES = (*STRUCTURE_ROLES, CompanyMembership.Role.HR)

# Offered to every new company and addable later from Leave types. Whether a
# given leave is paid is still decided when it is recorded or approved.
DEFAULT_LEAVE_TYPES = (
    ("CL", "Casual leave", "Short personal leave, for example a family matter."),
    ("SL", "Sick leave", "Illness or medical treatment."),
    ("EL", "Earned leave", "Annual leave earned through service."),
    ("ML", "Maternity leave", "Leave before and after childbirth."),
)


def _plain(value):
    # Audit rows are JSON: keep a decimal allowance as text.
    return str(value) if isinstance(value, Decimal) else value


def _days_text(value):
    return f"{value:.1f}".rstrip("0").rstrip(".")


LIVE_LEAVE_DAYS = (LeaveDay.Status.RESERVED, LeaveDay.Status.APPROVED, LeaveDay.Status.CONSUMED)


def allowance_left(employee, leave_type, year):
    """Days per year minus approved leave of this type in that year; None if unlimited.

    Call inside the company's tenant context.
    """
    if leave_type.days_per_year is None:
        return None
    used = LeaveDay.objects.filter(
        employee=employee, request_segment__leave_type=leave_type,
        work_date__year=year, status__in=LIVE_LEAVE_DAYS,
    ).aggregate(total=Sum("balance_units"))["total"] or Decimal("0")
    return leave_type.days_per_year - used


def check_allowance(employee, leave_type, days, half=False):
    """Refuse leave that would take the employee over the type's yearly allowance."""
    if leave_type.days_per_year is None:
        return
    wanted = Counter()
    for on, *_ in days:
        wanted[on.year] += Decimal("0.5") if half else Decimal("1")
    for year, units in sorted(wanted.items()):
        left = allowance_left(employee, leave_type, year)
        if units > left:
            raise ValidationError({"leave_type": (
                f"{leave_type.name}: {_days_text(max(left, Decimal('0')))} of "
                f"{_days_text(leave_type.days_per_year)} days left in {year}; "
                f"this leave needs {_days_text(units)}."
            )})


def is_half_day(values):
    """Full day unless Half day is chosen. A half day is one date and counts as 0.5.

    Kept simple on purpose: no morning/afternoon choice and no hourly leave.
    Attendance counts a half-day leave day in full if the employee came in.
    """
    duration = values.get("duration") or LeaveRequestSegment.DurationType.FULL_DAY
    if duration not in (LeaveRequestSegment.DurationType.FULL_DAY,
                        LeaveRequestSegment.DurationType.HALF_DAY):
        raise ValidationError({"duration": "Choose a full day or a half day."})
    half = duration == LeaveRequestSegment.DurationType.HALF_DAY
    if half and values.get("start_date") != values.get("end_date"):
        raise ValidationError({"end_date": "A half day is for one date. Choose the same first and last day."})
    return half


def recorder_branches(actor, company_id):
    """``(membership, branches)`` where ``actor`` may record and cancel leave.

    HR, owner and company admin: every branch, as before. A branch manager, or
    someone given "Record and cancel leave" (A12 part 5): their branches only.
    """
    from access_control.branch_access import branches_for

    membership = require_company_membership(actor, company_id)
    branches = branches_for(actor, company_id, "leave.record")
    if not branches:
        raise PermissionDenied(
            "Recording leave requires HR, owner or company administrator access, "
            "or access to record leave in a branch."
        )
    return membership, branches


def require_leave_recorder(actor, company_id):
    return recorder_branches(actor, company_id)[0]


def _refuse_outside(branches, days, employee):
    """Every leave day must fall in a placement in one of ``branches``."""
    for on, assignment, *_ in days:
        if assignment.branch_id not in branches:
            raise ValidationError({"start_date": (
                f"On {on:%d %b %Y} {employee.full_name} is placed in "
                f"{assignment.branch.name}, which is outside your branches."
            )})


def _refuse_own_leave(actor, employee):
    if employee.user_id and employee.user_id == actor.pk:
        raise PermissionDenied(
            "You cannot record or cancel your own leave. Ask another administrator or HR."
        )


def _refuse_finalised_month(company_id, start, end):
    # Imported here: attendance.services reads leave days.
    from attendance.services import locked_ranges

    if any(start <= last and end >= first for first, last in locked_ranges(company_id)):
        raise ValidationError({"start_date": "These dates include a finalised salary month."})


def _writable(values, allowed):
    unsupported = set(values) - set(allowed)
    if unsupported:
        raise ValidationError(f"Unsupported field: {', '.join(sorted(unsupported))}")
    return dict(values)


# --------------------------------------------------------------------------
# Leave types
# --------------------------------------------------------------------------

@transaction.atomic
def create_leave_type(*, actor, company_id, values):
    membership = require_structure_manager(actor, company_id)
    values = _writable(values, LEAVE_TYPE_FIELDS)
    with use_company(company_id):
        leave_type = create_validated(
            LeaveType, company=membership.company,
            created_by=actor, updated_by=actor, **values,
        )
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="leave_type.created", obj=leave_type,
            after={field: _plain(getattr(leave_type, field)) for field in LEAVE_TYPE_FIELDS},
        )
    return leave_type


def get_leave_type_for_edit(*, actor, company_id, leave_type_id):
    membership = require_structure_manager(actor, company_id)
    with use_company(company_id):
        leave_type = LeaveType.objects.filter(pk=leave_type_id).first()
    if leave_type is None:
        raise PermissionDenied("Leave type not found in this company.")
    return membership, leave_type


@transaction.atomic
def update_leave_type(*, actor, company_id, leave_type_id, values):
    membership, leave_type = get_leave_type_for_edit(
        actor=actor, company_id=company_id, leave_type_id=leave_type_id
    )
    values = _writable(values, LEAVE_TYPE_FIELDS)
    with use_company(company_id):
        before = {field: _plain(getattr(leave_type, field)) for field in LEAVE_TYPE_FIELDS}
        for field, value in values.items():
            setattr(leave_type, field, value)
        leave_type.updated_by = actor
        leave_type.full_clean()
        leave_type.save()
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="leave_type.updated", obj=leave_type, before=before,
            after={field: _plain(getattr(leave_type, field)) for field in LEAVE_TYPE_FIELDS},
        )
    return leave_type


@transaction.atomic
def set_leave_type_status(*, actor, company_id, leave_type_id, status):
    membership, leave_type = get_leave_type_for_edit(
        actor=actor, company_id=company_id, leave_type_id=leave_type_id
    )
    if status not in dict(ActiveStatus.choices):
        raise ValidationError({"status": "Unknown status."})
    with use_company(company_id):
        before = {"status": leave_type.status}
        leave_type.status = status
        leave_type.updated_by = actor
        leave_type.full_clean()
        leave_type.save()
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="leave_type.status_changed", obj=leave_type,
            before=before, after={"status": status},
        )
    return leave_type


def create_default_leave_types(company, *, actor=None):
    """Add the standard leave types a company lacks. Existing codes are kept as they are."""
    with use_company(company.pk):
        existing = set(LeaveType.objects.values_list("code", flat=True))
        return [
            create_validated(
                LeaveType, company=company, code=code, name=name, description=description,
                created_by=actor, updated_by=actor,
            )
            for code, name, description in DEFAULT_LEAVE_TYPES
            if code not in existing
        ]


@transaction.atomic
def add_default_leave_types(*, actor, company_id):
    membership = require_structure_manager(actor, company_id)
    created = create_default_leave_types(membership.company, actor=actor)
    with use_company(company_id):
        for leave_type in created:
            record_company_event(
                actor=actor, membership=membership, company=membership.company,
                action="leave_type.created", obj=leave_type,
                after={field: _plain(getattr(leave_type, field)) for field in LEAVE_TYPE_FIELDS},
            )
    return created


# --------------------------------------------------------------------------
# Recording approved leave
# --------------------------------------------------------------------------

def _dates(start, end):
    day = start
    while day <= end:
        yield day
        day += datetime.timedelta(days=1)


def _tz(assignment):
    name = assignment.branch.timezone or assignment.company.timezone or "UTC"
    try:
        return zoneinfo.ZoneInfo(name)
    except zoneinfo.ZoneInfoNotFoundError:
        return zoneinfo.ZoneInfo("UTC")


def _covered_interval(shift, on, tz):
    """The shift's working window on a date, as aware datetimes."""
    start = datetime.datetime.combine(on, shift.start_time, tzinfo=tz)
    end_day = on + datetime.timedelta(days=1) if shift.spans_next_day else on
    end = datetime.datetime.combine(end_day, shift.end_time, tzinfo=tz)
    return start, end


def _assignment_on(employee, at):
    """The placement in force at an instant, or None."""
    return (
        EmployeeAssignment.objects.select_related("branch", "company", "department")
        .filter(employee=employee, effective_from__lte=at)
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=at))
        .exclude(status=EmployeeAssignment.Status.CANCELLED)
        .order_by("-effective_from")
        .first()
    )


def plan_leave_days(*, company_id, employee, start_date, end_date):
    """Work out which dates become leave days, without writing anything.

    Returns ``(days, skipped)``: ``days`` is a list of
    ``(date, assignment, covered_start, covered_end, shift)`` for working days;
    ``skipped`` lists ``(date, reason)`` for weekly offs and holidays. Raises
    ValidationError for anything that makes the leave impossible to record.
    """
    if end_date < start_date:
        raise ValidationError({"end_date": "The last day cannot be before the first."})
    if (end_date - start_date).days + 1 > MAX_LEAVE_DAYS:
        raise ValidationError({"end_date": f"A leave cannot be longer than {MAX_LEAVE_DAYS} days."})
    if employee.joining_date and start_date < employee.joining_date:
        raise ValidationError({
            "start_date": f"{employee.full_name} joined on {employee.joining_date:%d %b %Y}."
        })
    if employee.leaving_date and end_date > employee.leaving_date:
        raise ValidationError({
            "end_date": f"{employee.full_name} left on {employee.leaving_date:%d %b %Y}."
        })

    calendar = WorkCalendar(company_id, start_date, end_date)
    if not calendar.has_any_shift:
        raise ValidationError(
            "Set up shifts under Shifts first. Leave is measured against the "
            "working day, so it needs a shift."
        )

    try:
        company_tz = zoneinfo.ZoneInfo(employee.company.timezone or "UTC")
    except zoneinfo.ZoneInfoNotFoundError:
        company_tz = zoneinfo.ZoneInfo("UTC")

    days, skipped = [], []
    with use_company(company_id):
        for on in _dates(start_date, end_date):
            # Midday in the company's timezone decides the placement; the shift
            # comes from its department, the working window from its branch.
            probe = datetime.datetime.combine(on, datetime.time(12), tzinfo=company_tz)
            assignment = _assignment_on(employee, probe)
            if assignment is None:
                raise ValidationError({
                    "start_date": (
                        f"{employee.full_name} has no placement on {on:%d %b %Y}. "
                        "Leave can only fall inside their employment."
                    )
                })
            shift = calendar.shift_for(assignment.department_id, on, employee_id=employee.pk)
            if shift is None:
                raise ValidationError({
                    "start_date": (
                        f"{assignment.department.name} has no shift on {on:%d %b %Y}. "
                        "Set one under Shifts."
                    )
                })
            tz = _tz(assignment)
            covered_start, covered_end = _covered_interval(shift, on, tz)
            day = calendar.day(assignment.branch_id, on)
            if day.kind != WORKING:
                skipped.append((on, day.label or day.kind))
                continue
            days.append((on, assignment, covered_start, covered_end, shift))
    return days, skipped


@transaction.atomic
def record_leave(*, actor, company_id, values, document=None):
    """Record approved leave for one employee, with its document if any."""
    membership, branches = recorder_branches(actor, company_id)
    return _write_leave(actor=actor, membership=membership, branches=branches,
                        company_id=company_id, values=values, document=document)


def _write_leave(*, actor, membership, branches, company_id, values, request=None,
                 document=None):
    """Record approved leave - a new request, or (``request``) the new dates of
    an amended one, whose old days are already cancelled."""
    values = _writable(values, RECORD_FIELDS)
    employee = values["employee"]
    leave_type = values["leave_type"]
    pay_type = values["pay_type"]
    if pay_type not in PayType.values:
        raise ValidationError({"pay_type": "Choose paid or unpaid."})
    half = is_half_day(values)

    with use_company(company_id):
        if employee.company_id != membership.company.pk:
            raise PermissionDenied("That employee is not in this company.")
        _refuse_own_leave(actor, employee)
        if leave_type.company_id != membership.company.pk or leave_type.status != ActiveStatus.ACTIVE:
            raise ValidationError({"leave_type": "Choose an active leave type."})
        documents.require_if_needed(leave_type, document, request)
        _refuse_finalised_month(company_id, values["start_date"], values["end_date"])

        days, skipped = plan_leave_days(
            company_id=company_id, employee=employee,
            start_date=values["start_date"], end_date=values["end_date"],
        )
        if not days:
            raise ValidationError({
                "end_date": (
                    "Every day in that range is a weekly off or holiday, so there "
                    "is no working day to take as leave."
                )
            })
        _refuse_outside(branches, days, employee)

        clashes = list(
            LeaveDay.objects.filter(
                employee=employee,
                work_date__in=[on for on, *_ in days],
                status__in=[LeaveDay.Status.RESERVED, LeaveDay.Status.APPROVED,
                            LeaveDay.Status.CONSUMED],
            ).order_by("work_date").values_list("work_date", flat=True)
        )
        if clashes:
            raise ValidationError({
                "start_date": (
                    f"{employee.full_name} is already on leave on "
                    + ", ".join(f"{d:%d %b}" for d in clashes) + "."
                )
            })

        check_allowance(employee, leave_type, days, half)

        now = timezone.now()
        first_assignment = days[0][1]
        percentage = Decimal("100") if pay_type == PayType.PAID else Decimal("0")
        if request is not None:
            request = _amend_request(actor=actor, membership=membership, request=request,
                                     values=values, days=days, skipped=skipped, half=half,
                                     leave_type=leave_type, pay_type=pay_type,
                                     percentage=percentage, first_assignment=first_assignment)
            documents.store(request, document)
            return request
        request = create_validated(
            LeaveRequest,
            company=membership.company,
            employee=employee,
            submission_assignment=first_assignment,
            reason=values.get("reason", ""),
            status=LeaveRequest.Status.APPROVED,
            submitted_at=now,
            submitted_by=actor,
            decided_at=now,
            decision_snapshot={
                "recorded_as_approved_by": actor.pk,
                "pay_type": pay_type,
                "skipped": [[on.isoformat(), reason] for on, reason in skipped],
            },
            created_by=actor,
            updated_by=actor,
        )
        segment = _new_segment(membership=membership, request=request, leave_type=leave_type,
                               values=values, days=days, half=half, pay_type=pay_type,
                               percentage=percentage, first_assignment=first_assignment)
        write_approved_days(company=membership.company, employee=employee,
                            segment=segment, days=days, pay_type=pay_type)
        documents.store(request, document)
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="leave.recorded", obj=request,
            after={
                "employee_id": employee.pk,
                "leave_type": leave_type.code,
                "start_date": values["start_date"].isoformat(),
                "end_date": values["end_date"].isoformat(),
                "pay_type": pay_type,
                "half_day": half,
                "working_days": len(days),
                "document": request.attachment_name,
            },
        )
    _refresh_attendance(company_id, employee.pk, [on for on, *_ in days])
    return request


def _new_segment(*, membership, request, leave_type, values, days, half, pay_type,
                 percentage, first_assignment):
    return create_validated(
        LeaveRequestSegment,
        company=membership.company,
        leave_request=request,
        leave_type=leave_type,
        duration_type=(LeaveRequestSegment.DurationType.HALF_DAY if half
                       else LeaveRequestSegment.DurationType.FULL_DAY),
        start_date=values["start_date"],
        end_date=values["end_date"],
        timezone=str(_tz(first_assignment)),
        requested_units=Decimal("0.5") if half else Decimal(len(days)),
        requested_minutes=(days[0][4].scheduled_minutes // 2 if half
                           else sum(shift.scheduled_minutes for *_, shift in days)),
        requested_pay_type=pay_type,
        requested_pay_percentage=percentage,
        sequence_number=(request.segments.count() + 1),
    )


def _amend_request(*, actor, membership, request, values, days, skipped, half, leave_type,
                   pay_type, percentage, first_assignment):
    segment = _new_segment(membership=membership, request=request, leave_type=leave_type,
                           values=values, days=days, half=half, pay_type=pay_type,
                           percentage=percentage, first_assignment=first_assignment)
    write_approved_days(company=membership.company, employee=request.employee,
                        segment=segment, days=days, pay_type=pay_type)
    request.status = LeaveRequest.Status.APPROVED
    request.reason = values.get("reason", "") or request.reason
    request.decision_snapshot = {
        **request.decision_snapshot, "pay_type": pay_type,
        "skipped": [[on.isoformat(), reason] for on, reason in skipped],
    }
    request.updated_by = actor
    request.full_clean()
    request.save()
    return request


def _refresh_attendance(company_id, employee_id, dates):
    """Work the days out again now, not at the next refresh: a leave recorded,
    changed or cancelled shows on the Daily list and the calendar at once."""
    from attendance.services import recalculate

    dates = sorted(set(dates))
    if dates:
        recalculate(company_id, employee_ids=[employee_id], start=dates[0], end=dates[-1])


def live_days(request):
    """The request's days that still count as leave. Call in the company."""
    return list(LeaveDay.objects.filter(
        request_segment__leave_request=request, status__in=LIVE_LEAVE_DAYS,
    ).order_by("work_date"))


def _changeable(actor, company_id, request_id):
    """An approved (or partly cancelled) leave ``actor`` may change."""
    membership, request = get_leave_for_edit(
        actor=actor, company_id=company_id, request_id=request_id)
    if request.status not in (LeaveRequest.Status.APPROVED,
                              LeaveRequest.Status.PARTIALLY_CANCELLED):
        raise ValidationError("Only approved leave can be changed or cancelled.")
    _refuse_own_leave(actor, request.employee)
    return membership, request


@transaction.atomic
def cancel_days(*, actor, company_id, request_id, work_dates, reason=""):
    """Cancel some days of an approved leave (someone came back early): those
    days stop counting, the rest stay. All of them is the whole leave."""
    membership, request = _changeable(actor, company_id, request_id)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "Say why, so the history is clear."})
    with use_company(company_id):
        live = {day.work_date: day for day in live_days(request)}
        chosen = sorted(set(work_dates))
        if not chosen:
            raise ValidationError({"work_dates": "Choose the days to cancel."})
        unknown = [d for d in chosen if d not in live]
        if unknown:
            raise ValidationError({"work_dates": "Those days are not part of this leave: "
                                   + ", ".join(f"{d:%d %b}" for d in unknown)})
        if len(chosen) == len(live):
            return cancel_leave(actor=actor, company_id=company_id, request_id=request_id,
                                reason=reason)
        for day in chosen:
            _refuse_finalised_month(company_id, day, day)
        LeaveDay.objects.filter(pk__in=[live[d].pk for d in chosen]).update(
            status=LeaveDay.Status.CANCELLED)
        before = {"status": request.status}
        request.status = LeaveRequest.Status.PARTIALLY_CANCELLED
        request.decision_snapshot = {
            **request.decision_snapshot,
            "days_cancelled": request.decision_snapshot.get("days_cancelled", [])
            + [{"dates": [d.isoformat() for d in chosen], "by": actor.pk, "reason": reason}],
        }
        request.updated_by = actor
        request.full_clean()
        request.save()
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="leave.days_cancelled", obj=request, before=before,
            after={"status": request.status, "dates": [d.isoformat() for d in chosen],
                   "reason": reason, "days_left": len(live) - len(chosen)},
        )
    _refresh_attendance(company_id, request.employee_id, chosen)
    return request


@transaction.atomic
def amend_leave(*, actor, company_id, request_id, values, document=None):
    """Change an approved leave's type, dates, half or full day, or pay. The old
    days stop counting and the new ones are written, on the same leave, with
    every check recording makes - clashes, the allowance, finalised months."""
    membership, request = _changeable(actor, company_id, request_id)
    _membership, branches = recorder_branches(actor, company_id)
    with use_company(company_id):
        old = live_days(request)
        before = {
            "segments": [
                {"leave_type": seg.leave_type.code, "start_date": seg.start_date.isoformat(),
                 "end_date": seg.end_date.isoformat(), "pay_type": seg.requested_pay_type,
                 "duration": seg.duration_type}
                for seg in request.segments.select_related("leave_type")
                .exclude(status=LeaveRequestSegment.Status.CANCELLED)
            ],
            "days": [day.work_date.isoformat() for day in old],
        }
        for day in old:
            _refuse_finalised_month(company_id, day.work_date, day.work_date)
        LeaveDay.objects.filter(pk__in=[day.pk for day in old]).update(
            status=LeaveDay.Status.CANCELLED)
        request.segments.update(status=LeaveRequestSegment.Status.CANCELLED)
    values = {**values, "employee": request.employee}
    request = _write_leave(actor=actor, membership=membership, branches=branches,
                           company_id=company_id, values=values, request=request,
                           document=document)
    with use_company(company_id):
        new = live_days(request)
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="leave.amended", obj=request, before=before,
            after={"leave_type": values["leave_type"].code,
                   "start_date": values["start_date"].isoformat(),
                   "end_date": values["end_date"].isoformat(),
                   "pay_type": values["pay_type"], "reason": values.get("reason", ""),
                   "days": [day.work_date.isoformat() for day in new]},
        )
    _refresh_attendance(company_id, request.employee_id,
                        [day.work_date for day in old] + [day.work_date for day in new])
    return request


def leave_branch_ids(request):
    """Every branch a leave touches: where it was asked for, and each day's placement."""
    branch_ids = set(
        LeaveDay.objects.filter(request_segment__leave_request=request)
        .values_list("employee_assignment__branch_id", flat=True)
    )
    if request.submission_assignment_id:
        branch_ids.add(request.submission_assignment.branch_id)
    return branch_ids


def get_leave_for_edit(*, actor, company_id, request_id):
    membership, branches = recorder_branches(actor, company_id)
    with use_company(company_id):
        request = (
            LeaveRequest.objects.select_related("employee", "submission_assignment")
            .filter(pk=request_id).first()
        )
        if request is None:
            raise PermissionDenied("Leave not found in this company.")
        if not all(branch_id in branches for branch_id in leave_branch_ids(request)):
            raise PermissionDenied("This leave is outside your branches.")
    return membership, request


@transaction.atomic
def cancel_leave(*, actor, company_id, request_id, reason=""):
    """Cancel a recorded leave. Its days stop counting; nothing is deleted."""
    membership, request = get_leave_for_edit(
        actor=actor, company_id=company_id, request_id=request_id
    )
    if request.status == LeaveRequest.Status.CANCELLED:
        raise ValidationError("This leave is already cancelled.")
    # (A partly cancelled leave is cancelled whole here too.)
    _refuse_own_leave(actor, request.employee)
    with use_company(company_id):
        for start, end in request.segments.values_list("start_date", "end_date"):
            _refuse_finalised_month(company_id, start, end)
        LeaveDay.objects.filter(request_segment__leave_request=request).update(
            status=LeaveDay.Status.CANCELLED
        )
        request.segments.update(status=LeaveRequestSegment.Status.CANCELLED)
        before = {"status": request.status}
        request.status = LeaveRequest.Status.CANCELLED
        request.decision_snapshot = {
            **request.decision_snapshot, "cancelled_by": actor.pk,
            "cancel_reason": reason,
        }
        request.updated_by = actor
        request.full_clean()
        request.save()
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="leave.cancelled", obj=request,
            before=before, after={"status": request.status, "reason": reason},
        )
        dates = list(LeaveDay.objects.filter(request_segment__leave_request=request)
                     .values_list("work_date", flat=True))
    _refresh_attendance(company_id, request.employee_id, dates)
    return request


def write_approved_days(*, company, employee, segment, days, pay_type):
    """Shared day expansion for recorded leave and approved employee requests."""
    percentage = Decimal("100") if pay_type == PayType.PAID else Decimal("0")
    half = segment.duration_type == LeaveRequestSegment.DurationType.HALF_DAY
    for on, assignment, covered_start, covered_end, shift in days:
        create_validated(
            LeaveDay,
            company=company,
            request_segment=segment,
            employee=employee,
            employee_assignment=assignment,
            work_date=on,
            covered_start_at=covered_start,
            covered_end_at=covered_end,
            scheduled_minutes_snapshot=shift.scheduled_minutes,
            leave_minutes=shift.scheduled_minutes // 2 if half else shift.scheduled_minutes,
            balance_units=Decimal("0.5") if half else Decimal("1"),
            approved_pay_type=pay_type,
            approved_pay_percentage=percentage,
            shift_snapshot={
                "shift_id": shift.pk,
                "code": shift.code,
                "start": shift.start_time.isoformat(),
                "end": shift.end_time.isoformat(),
                "spans_next_day": shift.spans_next_day,
            },
            status=LeaveDay.Status.APPROVED,
        )
