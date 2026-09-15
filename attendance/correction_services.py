"""Fixing a day by hand, and the list of days that need a look (plan step N5).

Every action here follows the same shape:

1. Check the person may do it — owner, company administrator or HR, the same
   people who decide overtime.
2. Refuse a day inside a finalised salary month. Its salary has been paid.
3. Write the correction, recalculate that day, and check the result is what
   was asked for. A scan that would land in a different day's window, or be
   swallowed as a repeat of a real scan, is refused rather than silently lost.
4. Record the day before and after, on the correction and in the audit log.

Nothing edits an AttendanceRecord directly. The record is recalculated from
the punches plus the corrections in force (attendance.corrections), which is
what makes a fix survive the next punch.
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.models import CompanyMembership
from attendance.models import AttendanceCorrection, AttendanceRecord, ReviewStatus
from attendance.services import _is_locked, locked_ranges, recalculate
from auditlog.services import record_company_event
from common.tenant import use_company
from employees.models import Employee
from organization.services import STRUCTURE_ROLES, require_company_membership

Type = AttendanceCorrection.CorrectionType
Status = AttendanceCorrection.Status
DayStatus = AttendanceRecord.AttendanceStatus

#: Who may fix attendance. Matches payroll.overtime.OVERTIME_ROLES: "the admin
#: or HR" (Ajay, 2026-09-14).
CORRECTION_ROLES = (*STRUCTURE_ROLES, CompanyMembership.Role.HR)

#: The rule's check-out is something a person can confirm. An open overtime
#: session is decided on the Overtime page instead (A9).
RULE_CHECK_OUT = "check-out by rule, no scan"
OPEN_OVERTIME = "overtime session with no check-out"

#: The days a status can be set on: working days. Leave, holidays and weekly
#: offs have their own pages.
CORRECTABLE_DAY_STATUSES = (
    DayStatus.PRESENT, DayStatus.HALF_DAY, DayStatus.ABSENT, DayStatus.INCOMPLETE,
)


def require_corrector(actor, company_id):
    membership = require_company_membership(actor, company_id)
    if membership.role not in CORRECTION_ROLES:
        raise PermissionDenied(
            "Fixing attendance needs owner, company administrator or HR access."
        )
    return membership


def may_correct(actor, company_id):
    try:
        require_corrector(actor, company_id)
    except PermissionDenied:
        return False
    return True


def snapshot(record):
    """The parts of a day a person would recognise, for before/after."""
    if record is None:
        return {"exists": False}
    return {
        "exists": True,
        "attendance_status": record.attendance_status,
        "payable_fraction": str(record.payable_fraction),
        "first_in_at": record.first_in_at.isoformat() if record.first_in_at else None,
        "last_out_at": record.last_out_at.isoformat() if record.last_out_at else None,
        "worked_minutes": record.worked_minutes,
        "check_out_by_rule": record.check_out_by_rule,
        "review_status": record.review_status,
        "review_reason": record.review_reason,
    }


def _record(employee_id, work_date):
    return AttendanceRecord.objects.filter(
        employee_id=employee_id, work_date=work_date
    ).first()


def _refuse_if_locked(company_id, work_date):
    if _is_locked(work_date, locked_ranges(company_id)):
        raise ValidationError(
            f"{work_date:%d %b %Y} is in a finalised salary month, so it can no "
            "longer be changed."
        )


def _rebuild(company_id, employee_id, work_date):
    recalculate(
        company_id, employee_ids=[employee_id], start=work_date, end=work_date
    )
    return _record(employee_id, work_date)


def _finish(*, actor, membership, correction, before, record, action):
    correction.before_snapshot = before
    correction.after_snapshot = snapshot(record)
    correction.save(update_fields=["before_snapshot", "after_snapshot", "updated_at"])
    record_company_event(
        actor=actor, membership=membership, company=membership.company,
        action=action, obj=correction,
        before={"day": before},
        after={
            "day": correction.after_snapshot,
            "correction_type": correction.correction_type,
            "status": correction.status,
            "reason": correction.reason,
            "proposed_event_at": (
                correction.proposed_event_at.isoformat()
                if correction.proposed_event_at else None
            ),
            "proposed_status": correction.proposed_status,
        },
    )
    return correction


def _clean_reason(reason):
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "Say why, so the next person reading this knows."})
    return reason


def _new(*, actor, membership, employee_id, work_date, correction_type, reason, **fields):
    # The employee id comes from the URL. The tenant-scoped manager answers
    # only for this company, so another company's employee is simply not found.
    if not Employee.objects.filter(pk=employee_id).exists():
        raise PermissionDenied("Employee not found in this company.")
    now = timezone.now()
    return AttendanceCorrection.objects.create(
        company=membership.company, employee_id=employee_id, work_date=work_date,
        correction_type=correction_type, reason=reason,
        approved_by=actor, approved_at=now, created_by=actor, updated_by=actor,
        **fields,
    )


# --------------------------------------------------------------------------
# The three fixes
# --------------------------------------------------------------------------


def add_scan(*, actor, company_id, employee_id, work_date, at, reason):
    """A scan the device never got, on this day."""
    membership = require_corrector(actor, company_id)
    reason = _clean_reason(reason)
    if at is None:
        raise ValidationError({"at": "Choose the date and time of the scan."})
    if at > timezone.now():
        raise ValidationError({"at": "A scan cannot be in the future."})
    _refuse_if_locked(company_id, work_date)

    with transaction.atomic(), use_company(company_id):
        before = snapshot(_rebuild(company_id, employee_id, work_date))
        correction = _new(
            actor=actor, membership=membership, employee_id=employee_id,
            work_date=work_date, correction_type=Type.ADD_SCAN, reason=reason,
            proposed_event_at=at,
        )
        record = _rebuild(company_id, employee_id, work_date)
        allocation = (
            record.allocations.filter(attendance_correction=correction).first()
            if record is not None else None
        )
        if allocation is None:
            # Rolls the correction back with the transaction.
            raise ValidationError({
                "at": "That time belongs to a different day's attendance. Open "
                      "that day and add it there."
            })
        if not allocation.is_included:
            raise ValidationError({
                "at": "There is already a scan within seconds of that time, so "
                      "this one would be ignored as a repeat."
            })
        return _finish(
            actor=actor, membership=membership, correction=correction,
            before=before, record=record, action="attendance.scan_added",
        )


def change_status(*, actor, company_id, employee_id, work_date, status, reason):
    """Say what the day was: present, half day or absent."""
    membership = require_corrector(actor, company_id)
    reason = _clean_reason(reason)
    if status not in AttendanceCorrection.SETTABLE_STATUSES:
        raise ValidationError({"status": "Choose present, half day or absent."})
    _refuse_if_locked(company_id, work_date)

    with transaction.atomic(), use_company(company_id):
        # Judged on the day as it stands now: it may have closed, or never
        # been written, since anybody last looked.
        record = _rebuild(company_id, employee_id, work_date)
        if record is None or record.is_open:
            raise ValidationError(
                "This day has not finished yet, so there is no status to change."
            )
        if record.leave_day_id:
            # Leave decides these days, including a half-day leave somebody
            # came in on (which reads "present"). _write_day applies a status
            # correction only to an ordinary working day, so accepting one
            # here would store a fix that silently does nothing.
            raise ValidationError(
                "This day has leave recorded. Change or cancel the leave on the "
                "Leave page."
            )
        if record.attendance_status not in CORRECTABLE_DAY_STATUSES:
            raise ValidationError(
                f"This day is {record.get_attendance_status_display().lower()}. "
                "Change leave, holidays and weekly offs on their own pages."
            )
        before = snapshot(record)
        # One status change in force per day: the newest replaces the last.
        AttendanceCorrection.objects.select_for_update().filter(
            employee_id=employee_id, work_date=work_date,
            correction_type=Type.CHANGE_STATUS, status=Status.APPLIED,
        ).update(status=Status.SUPERSEDED, updated_by=actor, updated_at=timezone.now())
        correction = _new(
            actor=actor, membership=membership, employee_id=employee_id,
            work_date=work_date, correction_type=Type.CHANGE_STATUS, reason=reason,
            proposed_status=status,
        )
        record = _rebuild(company_id, employee_id, work_date)
        return _finish(
            actor=actor, membership=membership, correction=correction,
            before=before, record=record, action="attendance.status_changed",
        )


def accept_review(*, actor, company_id, employee_id, work_date, reason):
    """The check-out the rule set is right; stop asking."""
    membership = require_corrector(actor, company_id)
    reason = _clean_reason(reason)
    _refuse_if_locked(company_id, work_date)

    with transaction.atomic(), use_company(company_id):
        record = _rebuild(company_id, employee_id, work_date)
        if record is None or record.review_status != ReviewStatus.NEEDS_REVIEW:
            raise ValidationError("This day does not need a review.")
        if record.review_reason != RULE_CHECK_OUT:
            raise ValidationError(
                "An open overtime session is decided on the Overtime page."
            )
        before = snapshot(record)
        correction = _new(
            actor=actor, membership=membership, employee_id=employee_id,
            work_date=work_date, correction_type=Type.ACCEPT_REVIEW, reason=reason,
            accepted_review_reason=record.review_reason,
        )
        record = _rebuild(company_id, employee_id, work_date)
        return _finish(
            actor=actor, membership=membership, correction=correction,
            before=before, record=record, action="attendance.review_accepted",
        )


def withdraw(*, actor, company_id, correction_id, note):
    """Take a correction back. The day is rebuilt without it."""
    membership = require_corrector(actor, company_id)
    note = (note or "").strip()
    with transaction.atomic(), use_company(company_id):
        correction = (
            AttendanceCorrection.objects.select_for_update()
            .filter(pk=correction_id).first()
        )
        if correction is None:
            raise PermissionDenied("Correction not found in this company.")
        if correction.status != Status.APPLIED:
            raise ValidationError("Only a correction in force can be withdrawn.")
        _refuse_if_locked(company_id, correction.work_date)
        before = snapshot(_record(correction.employee_id, correction.work_date))
        correction.status = Status.WITHDRAWN
        correction.withdrawn_by = actor
        correction.withdrawn_at = timezone.now()
        correction.decision_note = note
        correction.updated_by = actor
        correction.save(update_fields=[
            "status", "withdrawn_by", "withdrawn_at", "decision_note",
            "updated_by", "updated_at",
        ])
        record = _rebuild(company_id, correction.employee_id, correction.work_date)
        # The correction's own before/after stay as they were — they record what
        # it did. The withdrawal's effect is kept beside them.
        after = snapshot(record)
        correction.after_snapshot = {
            **correction.after_snapshot,
            "withdrawal": {"before": before, "after": after},
        }
        correction.save(update_fields=["after_snapshot", "updated_at"])
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="attendance.correction_withdrawn", obj=correction,
            before={"day": before, "status": Status.APPLIED},
            after={"day": after, "status": Status.WITHDRAWN, "note": note},
        )
        return correction


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def corrections_for_day(company_id, employee_id, work_date):
    with use_company(company_id):
        return list(
            AttendanceCorrection.objects.select_related("approved_by", "withdrawn_by")
            .filter(employee_id=employee_id, work_date=work_date)
            .order_by("-created_at", "-pk")
        )


def review_queue(company_id):
    """Closed days waiting for a person, oldest first.

    Two reasons put a day here (DEVICE_ATTENDANCE_POLICY.md step 7): the day
    closed on an IN and was checked out at the shift end by rule, or somebody
    came back after the shift and never scanned out. A day inside a finalised
    salary month is not listed — nothing can change it now.
    """
    locked = locked_ranges(company_id)
    with use_company(company_id):
        rows = list(
            AttendanceRecord.objects.select_related(
                "employee", "shift", "employee_assignment__department__department",
            )
            .filter(review_status=ReviewStatus.NEEDS_REVIEW, is_open=False)
            .order_by("work_date", "employee__first_name", "pk")
        )
    return [row for row in rows if not _is_locked(row.work_date, locked)]


def is_rule_check_out(record):
    return record.review_reason == RULE_CHECK_OUT


def is_open_overtime(record):
    return record.review_reason == OPEN_OVERTIME
