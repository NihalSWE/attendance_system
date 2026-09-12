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
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from auditlog.services import record_company_event
from common.choices import ActiveStatus
from common.services import create_validated
from common.tenant import use_company
from employees.models import Employee, EmployeeAssignment
from leaves.models import LeaveDay, LeaveRequest, LeaveRequestSegment, LeaveType, PayType
from organization.services import require_structure_manager
from scheduling.calendar import WORKING, WorkCalendar

LEAVE_TYPE_FIELDS = ("code", "name", "description")
RECORD_FIELDS = ("employee", "leave_type", "start_date", "end_date", "pay_type", "reason")

# A leave longer than this is almost certainly a typo in the year.
MAX_LEAVE_DAYS = 366


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
            after={field: getattr(leave_type, field) for field in LEAVE_TYPE_FIELDS},
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
        before = {field: getattr(leave_type, field) for field in LEAVE_TYPE_FIELDS}
        for field, value in values.items():
            setattr(leave_type, field, value)
        leave_type.updated_by = actor
        leave_type.full_clean()
        leave_type.save()
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="leave_type.updated", obj=leave_type, before=before,
            after={field: getattr(leave_type, field) for field in LEAVE_TYPE_FIELDS},
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
        EmployeeAssignment.objects.select_related("branch", "company", "department__department")
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
            shift = calendar.shift_for(assignment.department_id, on)
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
def record_leave(*, actor, company_id, values):
    """Record approved, full-day leave for one employee."""
    membership = require_structure_manager(actor, company_id)
    values = _writable(values, RECORD_FIELDS)
    employee = values["employee"]
    leave_type = values["leave_type"]
    pay_type = values["pay_type"]
    if pay_type not in PayType.values:
        raise ValidationError({"pay_type": "Choose paid or unpaid."})

    with use_company(company_id):
        if employee.company_id != membership.company.pk:
            raise PermissionDenied("That employee is not in this company.")
        if leave_type.company_id != membership.company.pk or leave_type.status != ActiveStatus.ACTIVE:
            raise ValidationError({"leave_type": "Choose an active leave type."})

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

        now = timezone.now()
        first_assignment = days[0][1]
        percentage = Decimal("100") if pay_type == PayType.PAID else Decimal("0")
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
        segment = create_validated(
            LeaveRequestSegment,
            company=membership.company,
            leave_request=request,
            leave_type=leave_type,
            duration_type=LeaveRequestSegment.DurationType.FULL_DAY,
            start_date=values["start_date"],
            end_date=values["end_date"],
            timezone=str(_tz(first_assignment)),
            requested_units=Decimal(len(days)),
            requested_minutes=sum(shift.scheduled_minutes for *_, shift in days),
            requested_pay_type=pay_type,
            requested_pay_percentage=percentage,
        )
        for on, assignment, covered_start, covered_end, shift in days:
            create_validated(
                LeaveDay,
                company=membership.company,
                request_segment=segment,
                employee=employee,
                employee_assignment=assignment,
                work_date=on,
                covered_start_at=covered_start,
                covered_end_at=covered_end,
                scheduled_minutes_snapshot=shift.scheduled_minutes,
                leave_minutes=shift.scheduled_minutes,
                balance_units=Decimal("1"),
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
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="leave.recorded", obj=request,
            after={
                "employee_id": employee.pk,
                "leave_type": leave_type.code,
                "start_date": values["start_date"].isoformat(),
                "end_date": values["end_date"].isoformat(),
                "pay_type": pay_type,
                "working_days": len(days),
            },
        )
    return request


def get_leave_for_edit(*, actor, company_id, request_id):
    membership = require_structure_manager(actor, company_id)
    with use_company(company_id):
        request = (
            LeaveRequest.objects.select_related("employee")
            .filter(pk=request_id).first()
        )
    if request is None:
        raise PermissionDenied("Leave not found in this company.")
    return membership, request


@transaction.atomic
def cancel_leave(*, actor, company_id, request_id, reason=""):
    """Cancel a recorded leave. Its days stop counting; nothing is deleted."""
    membership, request = get_leave_for_edit(
        actor=actor, company_id=company_id, request_id=request_id
    )
    if request.status == LeaveRequest.Status.CANCELLED:
        raise ValidationError("This leave is already cancelled.")
    with use_company(company_id):
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
    return request
