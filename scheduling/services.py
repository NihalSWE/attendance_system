"""Company-scoped writes for the working calendar: shifts, attendance settings,
weekly off days and holidays.

Same contract as ``organization.services``:

1. **Membership** - the actor holds an active CompanyMembership here.
2. **Action permission** - their role may manage company structure
   (owner / company administrator).
3. **Row scope** - a branch-restricted member may only touch rules for their
   own branches, re-checked against submitted ids.

Every write is validated through ``create_validated`` / ``full_clean`` so the
model rules and database constraints surface as field errors, and shares one
transaction with its audit row.

Attendance and payroll read these rows, so nothing here is ever deleted: a
shift is deactivated, a weekly off is ended on a date, a holiday is cancelled.
A past month can then always be recalculated against the calendar that was in
force at the time.
"""

import datetime
import zoneinfo

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from auditlog.services import record_company_event
from common.choices import ActiveStatus
from common.services import create_validated
from common.tenant import use_company
from organization.services import assert_branch_in_scope, require_structure_manager
from scheduling.models import (
    CompanyAttendanceSettings,
    DepartmentShift,
    EmployeeShiftAssignment,
    Holiday,
    Shift,
    WeeklyOffRule,
)

SHIFT_FIELDS = (
    "code",
    "name",
    "start_time",
    "end_time",
    "spans_next_day",
    "grace_in_minutes",
    "grace_out_minutes",
    "minimum_full_day_minutes",
    "minimum_half_day_minutes",
    "default_break_minutes",
    "break_is_paid",
    "overtime_after_minutes",
)
EMPLOYEE_SHIFT_FIELDS = ("employee", "shift", "first_day", "last_day", "reason")
SETTINGS_FIELDS = ("shift_mode", "company_shift", "missing_punch_policy")
DEPARTMENT_SHIFT_FIELDS = ("department", "shift", "effective_from")
WEEKLY_OFF_FIELDS = ("branch", "weekdays", "is_paid", "effective_from")
# What a single stored rule records, for its audit snapshot.
WEEKLY_OFF_RULE_FIELDS = ("branch", "weekday", "is_paid", "effective_from")
HOLIDAY_FIELDS = ("branch", "holiday_date", "name", "description", "is_paid")
# The year calendar: many dates at once, each with its own name.
HOLIDAY_BATCH_FIELDS = ("branch", "is_paid", "days")
MAX_HOLIDAYS_PER_BATCH = 366


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _writable(values, allowed):
    """Whitelist. A crafted POST must not reach a column the form omits."""
    unsupported = set(values) - set(allowed)
    if unsupported:
        raise ValidationError(
            f"Unsupported field: {', '.join(sorted(unsupported))}"
        )
    return dict(values)


def _plain(value):
    """JSON-safe audit value."""
    if isinstance(value, (datetime.date, datetime.time, datetime.datetime)):
        return value.isoformat()
    if hasattr(value, "pk"):
        return value.pk
    return value


def _snapshot(obj, fields):
    return {field: _plain(getattr(obj, field)) for field in fields}


def _check_branch(membership, values):
    branch = values.get("branch")
    if branch is not None:
        assert_branch_in_scope(membership, branch)


def scheduled_minutes_between(start_time, end_time, spans_next_day):
    """Length of a shift in minutes, crossing midnight when it spans the next day."""
    start = start_time.hour * 60 + start_time.minute
    end = end_time.hour * 60 + end_time.minute
    if spans_next_day:
        end += 24 * 60
    return end - start


def spans_next_day(start_time, end_time):
    """A shift whose end is earlier than its start ends the next day (22:00 → 06:00)."""
    return end_time < start_time


def _apply_shift_values(shift, values):
    """Set shift fields and derive "ends the next day" and the length from the times.

    Both are computed rather than typed: asking for what start and end already
    determine is an invitation to get it wrong. A value passed for
    ``spans_next_day`` is ignored in favour of the times.
    """
    for field, value in values.items():
        setattr(shift, field, value)
    if shift.start_time and shift.end_time:
        if shift.start_time == shift.end_time:
            raise ValidationError({"end_time": "The shift cannot start and end at the same time."})
        shift.spans_next_day = spans_next_day(shift.start_time, shift.end_time)
        shift.scheduled_minutes = scheduled_minutes_between(
            shift.start_time, shift.end_time, shift.spans_next_day
        )
    # "The break is paid" is hidden while there is no break; a box left ticked
    # before the break was cleared must not survive as a stray setting.
    if not shift.default_break_minutes:
        shift.break_is_paid = False
    errors = {}
    if shift.scheduled_minutes and shift.minimum_full_day_minutes > shift.scheduled_minutes:
        errors["minimum_full_day_minutes"] = (
            f"A full day cannot need more than the shift's "
            f"{shift.scheduled_minutes} scheduled minutes."
        )
    if shift.minimum_half_day_minutes > shift.minimum_full_day_minutes:
        errors["minimum_half_day_minutes"] = (
            "A half day cannot need more minutes than a full day."
        )
    if shift.scheduled_minutes and shift.grace_out_minutes >= shift.scheduled_minutes:
        errors["grace_out_minutes"] = "Leaving early grace must be shorter than the shift."
    if shift.scheduled_minutes and shift.grace_in_minutes >= shift.scheduled_minutes:
        errors["grace_in_minutes"] = "Late grace must be shorter than the shift."
    if shift.overtime_after_minutes > 24 * 60:
        errors["overtime_after_minutes"] = "Use at most 1440 minutes (24 hours)."
    if errors:
        raise ValidationError(errors)


# --------------------------------------------------------------------------
# Attendance settings
# --------------------------------------------------------------------------

def get_attendance_settings(company_id):
    """The company's single attendance-settings row. Onboarding creates it."""
    with use_company(company_id):
        return CompanyAttendanceSettings.objects.select_related("company_shift").first()


@transaction.atomic
def update_attendance_settings(*, actor, company_id, values):
    """Choose how shifts are assigned, the company shift, and the missing-punch rule.

    - One shift for the company: everyone is measured against the company shift.
    - Shifts per department: each employee is measured against their
      department's shift; the company shift, if set, covers any department
      without one.
    """
    membership = require_structure_manager(actor, company_id)
    values = _writable(values, SETTINGS_FIELDS)
    with use_company(company_id):
        settings = CompanyAttendanceSettings.objects.select_for_update().get()
        before = _snapshot(settings, SETTINGS_FIELDS)
        shift = values.get("company_shift", settings.company_shift)
        mode = values.get("shift_mode", settings.shift_mode)
        if shift is not None and shift.status != ActiveStatus.ACTIVE:
            raise ValidationError({"company_shift": "Choose an active shift."})
        if mode == CompanyAttendanceSettings.ShiftMode.COMPANY_SINGLE_SHIFT and shift is None:
            raise ValidationError({
                "company_shift": "One shift for the company needs that shift chosen."
            })
        for field, value in values.items():
            setattr(settings, field, value)
        settings.settings_version += 1
        settings.updated_by = actor
        settings.full_clean()
        settings.save()
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="attendance_settings.updated", obj=settings,
            before=before, after=_snapshot(settings, SETTINGS_FIELDS),
        )
    return settings


def current_department_shifts(company_id, on):
    """{company_department_id: DepartmentShift} in force on a date."""
    with use_company(company_id):
        return {
            link.department_id: link
            for link in DepartmentShift.objects.select_related("shift")
            .filter(is_default=True, effective_from__lte=on)
            .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=on))
        }


@transaction.atomic
def set_department_shift(*, actor, company_id, values):
    """Give a department a shift from a date. Everyone in it works that shift.

    The department's previous shift is closed the day the new one starts, not
    deleted, so a past month is still measured against the shift it was
    actually worked on. Setting it again for the same start date replaces it.
    """
    membership = require_structure_manager(actor, company_id)
    values = _writable(values, DEPARTMENT_SHIFT_FIELDS)
    department = values["department"]
    shift = values["shift"]
    starts = values["effective_from"]
    with use_company(company_id):
        assert_branch_in_scope(membership, department.branch)
        if shift.status != ActiveStatus.ACTIVE:
            raise ValidationError({"shift": "Choose an active shift."})
        links = DepartmentShift.objects.select_for_update().filter(
            department=department, is_default=True
        )
        later = links.filter(effective_from__gt=starts).order_by("effective_from").first()
        if later is not None:
            raise ValidationError({
                "effective_from": (
                    f"{department.name} already changes shift on "
                    f"{later.effective_from:%d %b %Y}. Pick a date after that."
                )
            })
        current = links.filter(effective_from__lte=starts).filter(
            Q(effective_to__isnull=True) | Q(effective_to__gt=starts)
        ).first()
        before = (
            {"shift_id": current.shift_id, "effective_from": current.effective_from.isoformat()}
            if current else {}
        )
        if current is not None and current.effective_from == starts:
            current.shift = shift
            current.updated_by = actor
            current.full_clean()
            current.save()
            link = current
        else:
            if current is not None:
                if current.shift_id == shift.pk:
                    raise ValidationError({
                        "shift": f"{department.name} is already on {shift.name}."
                    })
                current.effective_to = starts
                current.status = DepartmentShift.Status.ENDED
                current.updated_by = actor
                current.full_clean()
                current.save()
            link = create_validated(
                DepartmentShift,
                company=membership.company,
                department=department,
                shift=shift,
                is_default=True,
                effective_from=starts,
                status=DepartmentShift.Status.ACTIVE,
                created_by=actor,
                updated_by=actor,
            )
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="department_shift.set", obj=link, before=before,
            after={"department_id": department.pk, "shift_id": shift.pk,
                   "effective_from": starts.isoformat()},
        )
    return link


# --------------------------------------------------------------------------
# One employee's own shift
# --------------------------------------------------------------------------

def _company_zone(company):
    try:
        return zoneinfo.ZoneInfo(company.timezone or "UTC")
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return datetime.timezone.utc


def _midnight(tz, on):
    """An employee shift starts and ends at the company's midnight."""
    return datetime.datetime.combine(on, datetime.time.min, tzinfo=tz)


def _employee_snapshot(assignment, tz):
    return {
        "employee_id": assignment.employee_id,
        "shift_id": assignment.shift_id,
        "type": assignment.assignment_type,
        "first_day": assignment.effective_from.astimezone(tz).date().isoformat(),
        "until": (
            assignment.effective_to.astimezone(tz).date().isoformat()
            if assignment.effective_to else None
        ),
        "status": assignment.status,
    }


def _employee_in_scope(membership, employee):
    """The employee belongs to this company and to a branch the actor manages."""
    from employees.models import Employee, EmployeeAssignment

    if not Employee.objects.filter(pk=employee.pk).exists():
        raise PermissionDenied("Employee not found in this company.")
    placement = (
        EmployeeAssignment.objects.select_related("branch")
        .filter(employee=employee, effective_to__isnull=True)
        .exclude(status__in=["cancelled", "draft"])
        .first()
    )
    if placement is not None:
        assert_branch_in_scope(membership, placement.branch)


@transaction.atomic
def set_employee_shift(*, actor, company_id, values):
    """Give one employee their own shift from a day, optionally until a day.

    No last day: an **override**, in force until changed. With a last day: a
    **temporary** shift; afterwards the employee is back on what they had —
    the override it interrupted, or else their department's or the company's
    shift. Either way it wins over the department and company shift.

    One employee shift at a time, like department shifts: a new one closes the
    one in force on its first day, one starting that same day is replaced, and
    a change already saved for a later day refuses an earlier one.
    """
    membership = require_structure_manager(actor, company_id)
    values = _writable(values, EMPLOYEE_SHIFT_FIELDS)
    employee, shift = values["employee"], values["shift"]
    first_day, last_day = values["first_day"], values.get("last_day")
    if last_day is not None and last_day < first_day:
        raise ValidationError({"last_day": "The last day cannot be before the first day."})
    tz = _company_zone(membership.company)
    starts = _midnight(tz, first_day)
    ends = _midnight(tz, last_day + datetime.timedelta(days=1)) if last_day else None

    with use_company(company_id):
        _employee_in_scope(membership, employee)
        if shift.status != ActiveStatus.ACTIVE:
            raise ValidationError({"shift": "Choose an active shift."})
        existing = (
            EmployeeShiftAssignment.objects.select_for_update()
            .filter(employee=employee)
            .exclude(status=EmployeeShiftAssignment.Status.CANCELLED)
        )
        later = existing.filter(effective_from__gt=starts).order_by("effective_from").first()
        if later is not None:
            raise ValidationError({
                "first_day": (
                    f"{employee.full_name} already has a shift change from "
                    f"{later.effective_from.astimezone(tz):%d %b %Y}. Pick that day or a later one."
                )
            })
        current = existing.filter(effective_from__lte=starts).filter(
            Q(effective_to__isnull=True) | Q(effective_to__gt=starts)
        ).first()
        before = _employee_snapshot(current, tz) if current else {}
        resume = None
        if current is not None:
            # A temporary shift in the middle of a longer one: the longer one
            # carries on afterwards.
            if ends is not None and (current.effective_to is None or current.effective_to > ends):
                resume = (current.shift, current.assignment_type, current.effective_to, current.reason)
            if current.effective_from == starts:
                current.status = EmployeeShiftAssignment.Status.CANCELLED
            else:
                current.effective_to = starts
                current.status = EmployeeShiftAssignment.Status.ENDED
            current.updated_by = actor
            current.full_clean()
            current.save()

        assignment = create_validated(
            EmployeeShiftAssignment,
            company=membership.company,
            employee=employee,
            shift=shift,
            effective_from=starts,
            effective_to=ends,
            assignment_type=(
                EmployeeShiftAssignment.AssignmentType.TEMPORARY if ends
                else EmployeeShiftAssignment.AssignmentType.EMPLOYEE_OVERRIDE
            ),
            reason=(values.get("reason") or "").strip(),
            assigned_by=actor,
            status=EmployeeShiftAssignment.Status.ACTIVE,
            created_by=actor,
            updated_by=actor,
        )
        if resume is not None:
            resumed_shift, resumed_type, resumed_to, resumed_reason = resume
            create_validated(
                EmployeeShiftAssignment,
                company=membership.company,
                employee=employee,
                shift=resumed_shift,
                effective_from=ends,
                effective_to=resumed_to,
                assignment_type=resumed_type,
                reason=resumed_reason,
                assigned_by=actor,
                status=EmployeeShiftAssignment.Status.ACTIVE,
                created_by=actor,
                updated_by=actor,
            )
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="employee_shift.set", obj=assignment,
            before=before, after={**_employee_snapshot(assignment, tz), "resumes_after": resume is not None},
        )
    return assignment


def get_employee_shift_for_edit(*, actor, company_id, assignment_id):
    membership = require_structure_manager(actor, company_id)
    with use_company(company_id):
        assignment = (
            EmployeeShiftAssignment.objects.select_related("employee", "shift")
            .filter(pk=assignment_id).first()
        )
        if assignment is None:
            raise PermissionDenied("Employee shift not found in this company.")
        _employee_in_scope(membership, assignment.employee)
    return membership, assignment


@transaction.atomic
def end_employee_shift(*, actor, company_id, assignment_id, last_day):
    """The employee's own shift stops after ``last_day``; from the next day they
    are back on their department's or the company's shift. Ending it before it
    started cancels it."""
    membership, assignment = get_employee_shift_for_edit(
        actor=actor, company_id=company_id, assignment_id=assignment_id
    )
    if assignment.status == EmployeeShiftAssignment.Status.CANCELLED:
        raise ValidationError({"last_day": "This shift was cancelled."})
    tz = _company_zone(membership.company)
    stops = _midnight(tz, last_day + datetime.timedelta(days=1))
    if assignment.effective_to is not None and stops >= assignment.effective_to:
        raise ValidationError({
            "last_day": (
                "It already ends on "
                f"{(assignment.effective_to.astimezone(tz) - datetime.timedelta(days=1)):%d %b %Y}."
            )
        })
    with use_company(company_id):
        before = _employee_snapshot(assignment, tz)
        if stops <= assignment.effective_from:
            assignment.status = EmployeeShiftAssignment.Status.CANCELLED
        else:
            assignment.effective_to = stops
            assignment.status = EmployeeShiftAssignment.Status.ENDED
        assignment.updated_by = actor
        assignment.full_clean()
        assignment.save()
        # A shift that was due to resume after this one is dropped too:
        # ending means "back to the department's shift".
        EmployeeShiftAssignment.objects.filter(
            employee=assignment.employee, effective_from__gte=stops,
        ).exclude(status=EmployeeShiftAssignment.Status.CANCELLED).update(
            status=EmployeeShiftAssignment.Status.CANCELLED, updated_by=actor,
        )
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="employee_shift.ended", obj=assignment,
            before=before, after=_employee_snapshot(assignment, tz),
        )
    return assignment


def employee_shift_history(company_id, employee, tz):
    """The employee's own shifts, newest first, with local first/last days."""
    with use_company(company_id):
        rows = list(
            EmployeeShiftAssignment.objects.select_related("shift", "assigned_by")
            .filter(employee=employee)
            .exclude(status=EmployeeShiftAssignment.Status.CANCELLED)
            .order_by("-effective_from")
        )
    for row in rows:
        row.first_day = row.effective_from.astimezone(tz).date()
        row.last_day = (
            (row.effective_to.astimezone(tz) - datetime.timedelta(days=1)).date()
            if row.effective_to else None
        )
    return rows


# --------------------------------------------------------------------------
# Shifts
# --------------------------------------------------------------------------

@transaction.atomic
def create_shift(*, actor, company_id, values):
    membership = require_structure_manager(actor, company_id)
    values = _writable(values, SHIFT_FIELDS)
    with use_company(company_id):
        shift = Shift(
            company=membership.company, created_by=actor, updated_by=actor
        )
        _apply_shift_values(shift, values)
        shift.full_clean()
        shift.save()
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="shift.created", obj=shift,
            after=_snapshot(shift, SHIFT_FIELDS + ("scheduled_minutes",)),
        )
    return shift


def get_shift_for_edit(*, actor, company_id, shift_id):
    membership = require_structure_manager(actor, company_id)
    with use_company(company_id):
        shift = Shift.objects.filter(pk=shift_id).first()
    if shift is None:
        raise PermissionDenied("Shift not found in this company.")
    return membership, shift


@transaction.atomic
def update_shift(*, actor, company_id, shift_id, values):
    membership, shift = get_shift_for_edit(
        actor=actor, company_id=company_id, shift_id=shift_id
    )
    values = _writable(values, SHIFT_FIELDS)
    with use_company(company_id):
        before = _snapshot(shift, SHIFT_FIELDS + ("scheduled_minutes",))
        _apply_shift_values(shift, values)
        shift.updated_by = actor
        shift.full_clean()
        shift.save()
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="shift.updated", obj=shift,
            before=before, after=_snapshot(shift, SHIFT_FIELDS + ("scheduled_minutes",)),
        )
    return shift


@transaction.atomic
def set_shift_status(*, actor, company_id, shift_id, status):
    membership, shift = get_shift_for_edit(
        actor=actor, company_id=company_id, shift_id=shift_id
    )
    if status not in dict(ActiveStatus.choices):
        raise ValidationError({"status": "Unknown status."})
    with use_company(company_id):
        if status == ActiveStatus.INACTIVE and CompanyAttendanceSettings.objects.filter(
            company_shift=shift
        ).exists():
            raise ValidationError({
                "status": (
                    "This is the company shift. Choose a different company shift "
                    "in attendance settings first, then deactivate this one."
                )
            })
        before = {"status": shift.status}
        shift.status = status
        shift.updated_by = actor
        shift.full_clean()
        shift.save()
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="shift.status_changed", obj=shift,
            before=before, after={"status": shift.status},
        )
    return shift


# --------------------------------------------------------------------------
# Weekly off days
# --------------------------------------------------------------------------

@transaction.atomic
def add_weekly_offs(*, actor, company_id, values):
    """Add one or more recurring weekly off days in a single step.

    One rule is stored per selected weekday, so each can later be stopped on
    its own date. All or nothing: if any selected day is already a weekly off
    for the same scope, none are added and the clash is named.
    """
    membership = require_structure_manager(actor, company_id)
    values = _writable(values, WEEKLY_OFF_FIELDS)
    values["is_paid"] = True
    weekdays = sorted(set(int(day) for day in (values.pop("weekdays", None) or [])))
    if not weekdays:
        raise ValidationError({"weekdays": "Select at least one day."})
    invalid = [day for day in weekdays if day not in WeeklyOffRule.Weekday.values]
    if invalid:
        raise ValidationError({"weekdays": "Unknown day selected."})

    with use_company(company_id):
        _lock_weekly_offs(company_id)
        _check_branch(membership, values)
        _refuse_weekly_off_overlap(
            branch=values.get("branch"), weekdays=weekdays,
            start=values.get("effective_from"), field="weekdays",
        )

        rules = []
        for day in weekdays:
            rule = create_validated(
                WeeklyOffRule,
                company=membership.company,
                created_by=actor,
                updated_by=actor,
                weekday=day,
                **values,
            )
            record_company_event(
                actor=actor, membership=membership, company=membership.company,
                action="weekly_off.added", obj=rule,
                after=_snapshot(rule, WEEKLY_OFF_RULE_FIELDS),
            )
            rules.append(rule)
    return rules


def _lock_weekly_offs(company_id):
    # Serialize even when there is no rule yet. All weekly-off writers take
    # this same lock before reading dates; the exclusion constraint remains.
    from tenants.models import Company

    Company.objects.select_for_update().get(pk=company_id)


def _refuse_weekly_off_overlap(*, branch, weekdays, start, field, end=None, exclude_pk=None):
    """Periods are [start, end), including the history of stopped rules."""
    if start is None:
        raise ValidationError({"effective_from": "Choose a start date."})
    clashes = WeeklyOffRule.objects.filter(branch=branch, weekday__in=weekdays).filter(
        Q(effective_to__isnull=True) | Q(effective_to__gt=start)
    )
    if end is not None:
        clashes = clashes.filter(effective_from__lt=end)
    if exclude_pk is not None:
        clashes = clashes.exclude(pk=exclude_pk)
    errors = []
    for rule in clashes.order_by("weekday", "effective_from"):
        scope = f"for {rule.branch.name}" if rule.branch_id else "company-wide"
        period = f"from {rule.effective_from:%d %b %Y}"
        if rule.effective_to:
            period += f" until {rule.effective_to:%d %b %Y} (excluding that date)"
        errors.append(
            f"{rule.get_weekday_display()} is already a weekly off {scope} {period}; "
            "change its start date instead, or choose a period that does not overlap."
        )
    if errors:
        raise ValidationError({field: errors})


def _recalculate_weekly_off_dates(company_id, rule, start, end):
    """Rebuild the changed range in monthly batches, bounded by employment.

    Calling the existing attendance service preserves finalised months and
    overtime decisions. Keep this inside the write transaction so a failure
    cannot save a new calendar with only part of its attendance updated.
    """
    from attendance.services import recalculate
    from employees.models import EmployeeAssignment

    tz = _company_zone(rule.company)
    end = min(end, timezone.now().astimezone(tz).date())
    placements = EmployeeAssignment.objects.exclude(status="cancelled").filter(
        effective_from__lt=_midnight(tz, end + datetime.timedelta(days=1)),
    ).filter(Q(effective_to__isnull=True) | Q(effective_to__gt=_midnight(tz, start)))
    if rule.branch_id:
        placements = placements.filter(branch_id=rule.branch_id)
    first = placements.order_by("effective_from").first()
    if first is None:
        return
    start = max(start, first.effective_from.astimezone(tz).date())
    employee_ids = list(placements.values_list("employee_id", flat=True).distinct())
    while start <= end:
        next_month = (start.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
        batch_end = min(end, next_month - datetime.timedelta(days=1))
        recalculate(company_id, employee_ids=employee_ids, start=start, end=batch_end)
        start = next_month


@transaction.atomic
def change_weekly_off_start(*, actor, company_id, rule_id, effective_from):
    """Correct a rule's start and rebuild the dates gained or removed."""
    require_structure_manager(actor, company_id)
    _lock_weekly_offs(company_id)
    membership, rule = get_weekly_off_for_edit(
        actor=actor, company_id=company_id, rule_id=rule_id
    )
    if effective_from is None:
        raise ValidationError({"effective_from": "Choose a start date."})
    if rule.effective_to is not None and effective_from >= rule.effective_to:
        raise ValidationError({
            "effective_from": f"The start must be before {rule.effective_to:%d %b %Y}, when it stops."
        })
    with use_company(company_id):
        _refuse_weekly_off_overlap(
            branch=rule.branch, weekdays=[rule.weekday], start=effective_from,
            end=rule.effective_to, exclude_pk=rule.pk, field="effective_from",
        )
        previous_start = rule.effective_from
        if previous_start == effective_from:
            return rule
        before = _snapshot(rule, WEEKLY_OFF_RULE_FIELDS)
        rule.effective_from = effective_from
        rule.is_paid = True
        rule.updated_by = actor
        rule.full_clean()
        rule.save()
        _recalculate_weekly_off_dates(
            company_id, rule, min(previous_start, effective_from),
            max(previous_start, effective_from) - datetime.timedelta(days=1),
        )
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="weekly_off.start_changed", obj=rule,
            before=before, after=_snapshot(rule, WEEKLY_OFF_RULE_FIELDS),
        )
    return rule


def get_weekly_off_for_edit(*, actor, company_id, rule_id):
    membership = require_structure_manager(actor, company_id)
    with use_company(company_id):
        rule = WeeklyOffRule.objects.select_related("branch").filter(pk=rule_id).first()
        if rule is None:
            raise PermissionDenied("Weekly off day not found in this company.")
        if rule.branch_id:
            assert_branch_in_scope(membership, rule.branch)
    return membership, rule


@transaction.atomic
def end_weekly_off(*, actor, company_id, rule_id, effective_to):
    """Stop a weekly off from a date. Past months keep using it."""
    require_structure_manager(actor, company_id)
    _lock_weekly_offs(company_id)
    membership, rule = get_weekly_off_for_edit(
        actor=actor, company_id=company_id, rule_id=rule_id
    )
    if rule.status == WeeklyOffRule.Status.ENDED:
        raise ValidationError({"effective_to": "This weekly off day has already ended."})
    if effective_to is None or effective_to <= rule.effective_from:
        raise ValidationError({
            "effective_to": (
                f"The last day must be after it started on "
                f"{rule.effective_from:%d %b %Y}."
            )
        })
    with use_company(company_id):
        before = {"effective_to": None, "status": rule.status}
        rule.effective_to = effective_to
        rule.status = WeeklyOffRule.Status.ENDED
        rule.updated_by = actor
        rule.full_clean()
        rule.save()
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="weekly_off.ended", obj=rule,
            before=before,
            after={"effective_to": effective_to.isoformat(), "status": rule.status},
        )
    return rule


# --------------------------------------------------------------------------
# Holidays
# --------------------------------------------------------------------------

def _snapshot_values(holiday):
    return {"holiday_date": holiday.holiday_date, "branch": holiday.branch}


def _refuse_duplicate_holiday(values, exclude_pk=None):
    """One active holiday per date and scope; say so in words, not a constraint name."""
    clash = Holiday.objects.filter(
        holiday_date=values.get("holiday_date"),
        branch=values.get("branch"),
        status=Holiday.Status.ACTIVE,
    )
    if exclude_pk:
        clash = clash.exclude(pk=exclude_pk)
    clash = clash.first()
    if clash is not None:
        raise ValidationError({
            "holiday_date": (
                f"{clash.name} is already a holiday on this date"
                f"{' for this branch' if clash.branch_id else ''}."
            )
        })


@transaction.atomic
def create_holiday(*, actor, company_id, values):
    membership = require_structure_manager(actor, company_id)
    values = _writable(values, HOLIDAY_FIELDS)
    values["is_paid"] = True
    with use_company(company_id):
        _check_branch(membership, values)
        _refuse_duplicate_holiday(values)
        holiday = create_validated(
            Holiday,
            company=membership.company,
            created_by=actor,
            updated_by=actor,
            **values,
        )
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="holiday.created", obj=holiday,
            after=_snapshot(holiday, HOLIDAY_FIELDS),
        )
    return holiday


@transaction.atomic
def add_holidays(*, actor, company_id, values):
    """Add many holidays in one step, from the year calendar.

    ``days`` is a list of ``(date, name)`` pairs; the branch applies to all
    of them and holidays are always paid. All or nothing: if any date
    is already a holiday for the same scope, none are added and every clash is
    named, so the administrator unselects them and saves again.
    """
    membership = require_structure_manager(actor, company_id)
    values = _writable(values, HOLIDAY_BATCH_FIELDS)
    days = [(day, (name or "").strip()) for day, name in (values.pop("days", None) or [])]
    if not days:
        raise ValidationError({"days": "Select at least one date on the calendar."})
    if len(days) > MAX_HOLIDAYS_PER_BATCH:
        raise ValidationError({"days": "Select at most one year of dates at a time."})
    dates = [day for day, _ in days]
    if len(set(dates)) != len(dates):
        raise ValidationError({"days": "A date is selected twice."})
    unnamed = [day for day, name in days if not name]
    if unnamed:
        raise ValidationError({
            "days": "Give every selected date a holiday name: "
            + ", ".join(f"{day:%d %b %Y}" for day in sorted(unnamed)) + "."
        })

    with use_company(company_id):
        _check_branch(membership, values)
        branch = values.get("branch")
        clashes = list(
            Holiday.objects.filter(
                holiday_date__in=dates, branch=branch, status=Holiday.Status.ACTIVE
            ).order_by("holiday_date")
        )
        if clashes:
            scope = "for this branch" if branch else "for all branches"
            listed = "; ".join(f"{h.holiday_date:%d %b %Y} ({h.name})" for h in clashes)
            raise ValidationError({
                "days": f"Already a holiday {scope}: {listed}. Unselect it and save again."
            })

        holidays = []
        for day, name in sorted(days):
            holiday = create_validated(
                Holiday,
                company=membership.company,
                created_by=actor,
                updated_by=actor,
                branch=branch,
                holiday_date=day,
                name=name,
                is_paid=True,
            )
            record_company_event(
                actor=actor, membership=membership, company=membership.company,
                action="holiday.created", obj=holiday,
                after={**_snapshot(holiday, HOLIDAY_FIELDS), "from": "year_calendar"},
            )
            holidays.append(holiday)
    return holidays


def get_holiday_for_edit(*, actor, company_id, holiday_id):
    membership = require_structure_manager(actor, company_id)
    with use_company(company_id):
        holiday = Holiday.objects.select_related("branch").filter(pk=holiday_id).first()
        if holiday is None:
            raise PermissionDenied("Holiday not found in this company.")
        if holiday.branch_id:
            assert_branch_in_scope(membership, holiday.branch)
    return membership, holiday


@transaction.atomic
def update_holiday(*, actor, company_id, holiday_id, values):
    membership, holiday = get_holiday_for_edit(
        actor=actor, company_id=company_id, holiday_id=holiday_id
    )
    if holiday.status == Holiday.Status.CANCELLED:
        raise ValidationError("A cancelled holiday cannot be edited.")
    values = _writable(values, HOLIDAY_FIELDS)
    values["is_paid"] = True
    with use_company(company_id):
        _check_branch(membership, values)
        _refuse_duplicate_holiday(
            {**_snapshot_values(holiday), **values}, exclude_pk=holiday.pk
        )
        before = _snapshot(holiday, HOLIDAY_FIELDS)
        for field, value in values.items():
            setattr(holiday, field, value)
        holiday.updated_by = actor
        holiday.full_clean()
        holiday.save()
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="holiday.updated", obj=holiday,
            before=before, after=_snapshot(holiday, HOLIDAY_FIELDS),
        )
    return holiday


@transaction.atomic
def cancel_holiday(*, actor, company_id, holiday_id):
    """Cancel rather than delete, so a recalculated month sees what happened."""
    membership, holiday = get_holiday_for_edit(
        actor=actor, company_id=company_id, holiday_id=holiday_id
    )
    if holiday.status == Holiday.Status.CANCELLED:
        raise ValidationError("This holiday is already cancelled.")
    with use_company(company_id):
        holiday.status = Holiday.Status.CANCELLED
        holiday.cancelled_at = timezone.now()
        holiday.cancelled_by = actor
        holiday.updated_by = actor
        holiday.full_clean()
        holiday.save()
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="holiday.cancelled", obj=holiday,
            before={"status": Holiday.Status.ACTIVE},
            after={"status": holiday.status},
        )
    return holiday
