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

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from auditlog.services import record_company_event
from common.choices import ActiveStatus
from common.services import create_validated
from common.tenant import use_company
from organization.services import assert_branch_in_scope, require_structure_manager
from scheduling.models import CompanyAttendanceSettings, Holiday, Shift, WeeklyOffRule

SHIFT_FIELDS = (
    "code",
    "name",
    "start_time",
    "end_time",
    "spans_next_day",
    "grace_in_minutes",
    "minimum_full_day_minutes",
    "minimum_half_day_minutes",
)
SETTINGS_FIELDS = ("company_shift", "missing_punch_policy")
WEEKLY_OFF_FIELDS = ("branch", "weekday", "is_paid", "effective_from")
HOLIDAY_FIELDS = ("branch", "holiday_date", "name", "description", "is_paid")


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


def _apply_shift_values(shift, values):
    """Set shift fields and derive the scheduled length from start and end.

    Scheduled minutes are computed rather than typed: asking for a number that
    start and end already determine is an invitation to get it wrong.
    """
    for field, value in values.items():
        setattr(shift, field, value)
    if shift.start_time and shift.end_time:
        shift.scheduled_minutes = scheduled_minutes_between(
            shift.start_time, shift.end_time, shift.spans_next_day
        )
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
    """Choose the company shift and what a missing punch means.

    Saving a company shift puts the company in single-shift mode: every
    employee is measured against that one shift. Per-department shifts are
    not offered yet, so single-shift is the only mode this screen sets.
    """
    membership = require_structure_manager(actor, company_id)
    values = _writable(values, SETTINGS_FIELDS)
    with use_company(company_id):
        settings = CompanyAttendanceSettings.objects.select_for_update().get()
        before = _snapshot(settings, SETTINGS_FIELDS + ("shift_mode",))
        shift = values.get("company_shift")
        if shift is not None and shift.status != ActiveStatus.ACTIVE:
            raise ValidationError(
                {"company_shift": "Choose an active shift."}
            )
        for field, value in values.items():
            setattr(settings, field, value)
        settings.shift_mode = CompanyAttendanceSettings.ShiftMode.COMPANY_SINGLE_SHIFT
        settings.settings_version += 1
        settings.updated_by = actor
        settings.full_clean()
        settings.save()
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="attendance_settings.updated", obj=settings,
            before=before, after=_snapshot(settings, SETTINGS_FIELDS + ("shift_mode",)),
        )
    return settings


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
def add_weekly_off(*, actor, company_id, values):
    """Add a recurring weekly off day, company-wide or for one branch."""
    membership = require_structure_manager(actor, company_id)
    values = _writable(values, WEEKLY_OFF_FIELDS)
    with use_company(company_id):
        _check_branch(membership, values)
        clash = WeeklyOffRule.objects.filter(
            weekday=values.get("weekday"),
            branch=values.get("branch"),
            status=WeeklyOffRule.Status.ACTIVE,
        ).first()
        if clash is not None:
            raise ValidationError({
                "weekday": (
                    f"{clash.get_weekday_display()} is already a weekly off day "
                    f"{'for this branch' if clash.branch_id else 'company-wide'}."
                )
            })
        rule = create_validated(
            WeeklyOffRule,
            company=membership.company,
            created_by=actor,
            updated_by=actor,
            **values,
        )
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="weekly_off.added", obj=rule,
            after=_snapshot(rule, WEEKLY_OFF_FIELDS),
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
