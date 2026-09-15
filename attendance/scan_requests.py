"""An employee reports a missed scan; someone who may fix attendance decides (plan step N11).

The device cannot tell a forgotten scan from a person who was never there, so
the employee says so: "I came back in at 11:15", "I left at 19:00". Nothing
changes until it is approved.

- **Asking** needs an active employee record on the login. The scan is tried
  against the day straight away (``correction_services.check_scan_fits``), so
  "that time belongs to another day" or "you already scanned then" is said
  when it is asked, not days later by the approver.
- **Deciding** needs ``attendance.fix`` in the day's branch — a branch manager
  for their own branches, HR and the company everywhere (A12 part 7). Nobody
  decides their own request.
- **Approving** adds the scan through ``correction_services.add_scan``, the
  same fix HR makes on Fix a day, and links the correction. The day is rebuilt
  with it; it can be withdrawn on Fix a day like any other correction.
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from access_control.branch_access import ALL_BRANCHES
from attendance import access, correction_services
from attendance.models import MissedScanRequest
from attendance.services import _is_locked, locked_ranges
from auditlog.services import record_company_event
from common.tenant import use_company
from employees.models import Employee
from organization.services import require_company_membership

Status = MissedScanRequest.Status

#: The correction errors, as the employee reads them.
EMPLOYEE_WORDS = {
    correction_services.OTHER_DAY: (
        "That time is not part of this day's attendance. Check the date: a scan "
        "after midnight on a night shift belongs to the day the shift started."
    ),
    correction_services.REPEAT: (
        "You already have a scan within seconds of that time, so it was not missed."
    ),
}

WORKING = (Employee.EmploymentStatus.ACTIVE, Employee.EmploymentStatus.PROBATION)


def my_employee(actor, company_id):
    """The employee record on this login, or None."""
    with use_company(company_id):
        return Employee.objects.filter(user=actor).first()


def _audit(actor, membership, request, action, before=None):
    record_company_event(
        actor=actor, membership=membership, company=membership.company,
        action=action, obj=request, before=before or {},
        after={
            "employee_id": request.employee_id,
            "work_date": request.work_date.isoformat(),
            "scan_at": request.scan_at.isoformat(),
            "status": request.status,
            "reason": request.reason,
            "decision_note": request.decision_note,
            "correction_id": request.correction_id,
        },
    )


def submit(*, actor, company_id, work_date, at, reason):
    membership = require_company_membership(actor, company_id)
    if actor.is_superuser:
        raise PermissionDenied("Use your employee login to report a missed scan.")
    employee = my_employee(actor, company_id)
    if employee is None or employee.employment_status not in WORKING:
        raise PermissionDenied("An active employee record linked to your login is required.")
    reason = (reason or "").strip()
    errors = {}
    if not reason:
        errors["reason"] = "Say what happened, so whoever approves it knows."
    if work_date is None:
        errors["work_date"] = "Choose the day."
    if at is None:
        errors["at"] = "Choose the date and time you scanned."
    if errors:
        raise ValidationError(errors)
    now = timezone.now()
    if at > now:
        raise ValidationError({"at": "A scan cannot be in the future."})
    if _is_locked(work_date, locked_ranges(company_id)):
        raise ValidationError({
            "work_date": "Salary for that month is finalised, so the day can no longer change."
        })
    branch_id = access.day_branch(company_id, employee.pk, work_date)
    if branch_id is None:
        raise ValidationError({"work_date": "You were not placed in a branch on that day."})

    with use_company(company_id):
        if MissedScanRequest.objects.filter(
            employee=employee, scan_at=at, status=Status.PENDING,
        ).exists():
            raise ValidationError({"at": "You have already reported this scan. It is waiting."})

    try:
        correction_services.check_scan_fits(
            actor=actor, membership=membership, company_id=company_id,
            employee_id=employee.pk, work_date=work_date, at=at,
        )
    except ValidationError as exc:
        words = [EMPLOYEE_WORDS.get(message, message) for message in exc.messages]
        raise ValidationError({"at": words}) from exc

    with transaction.atomic(), use_company(company_id):
        request = MissedScanRequest.objects.create(
            company=membership.company, employee=employee, work_date=work_date,
            scan_at=at, reason=reason, branch_id=branch_id, submitted_at=now,
            created_by=actor, updated_by=actor,
        )
        _audit(actor, membership, request, "attendance.missed_scan_requested")
    return request


def withdraw(*, actor, company_id, request_id):
    membership = require_company_membership(actor, company_id)
    with transaction.atomic(), use_company(company_id):
        request = (
            MissedScanRequest.objects.select_for_update()
            .filter(pk=request_id, employee__user=actor).first()
        )
        if request is None:
            raise PermissionDenied("Request not found.")
        if request.status != Status.PENDING:
            raise ValidationError("Only a request that is still waiting can be withdrawn.")
        request.status = Status.WITHDRAWN
        request.updated_by = actor
        request.save(update_fields=["status", "updated_by", "updated_at"])
        _audit(actor, membership, request, "attendance.missed_scan_withdrawn",
               before={"status": Status.PENDING})
    return request


def reviewable(actor, company_id):
    """``(membership, queryset)``: requests in the branches where ``actor`` may fix attendance.

    Never their own. Refuses somebody who may fix attendance nowhere.
    """
    membership, branches = access.fix_branches(actor, company_id)
    # Built here, read by the caller inside the same company.
    with use_company(company_id):
        queryset = MissedScanRequest.objects.exclude(employee__user_id=actor.pk)
    if branches is not ALL_BRANCHES:
        queryset = queryset.filter(branch_id__in=branches)
    return membership, queryset


def waiting_count(actor, company_id):
    """How many requests wait for ``actor``; 0 for someone who cannot decide any."""
    try:
        _membership, queryset = reviewable(actor, company_id)
    except PermissionDenied:
        return 0
    with use_company(company_id):
        return queryset.filter(status=Status.PENDING).count()


def decide(*, actor, company_id, request_id, approve, note=""):
    membership = require_company_membership(actor, company_id)
    note = (note or "").strip()
    with transaction.atomic(), use_company(company_id):
        request = (
            MissedScanRequest.objects.select_for_update().select_related("employee")
            .filter(pk=request_id).first()
        )
        if request is None:
            raise PermissionDenied("Request not found in this company.")
        if request.employee.user_id is not None and request.employee.user_id == actor.pk:
            raise PermissionDenied("You cannot decide your own request.")
        # The day's branch now, as for any fix (the placement may have moved).
        correction_services.require_corrector(
            actor, company_id, request.employee_id, request.work_date
        )
        if request.status != Status.PENDING:
            raise ValidationError("This request has already been decided or withdrawn.")
        before = {"status": request.status}
        now = timezone.now()
        if approve:
            correction = correction_services.add_scan(
                actor=actor, company_id=company_id, employee_id=request.employee_id,
                work_date=request.work_date, at=request.scan_at,
                reason=f"Reported by {request.employee.full_name}: {request.reason}"
                       + (f" — approved: {note}" if note else ""),
            )
            request.status = Status.APPROVED
            request.correction = correction
        else:
            if not note:
                raise ValidationError({"note": "Say why it is rejected, so they know."})
            request.status = Status.REJECTED
        request.decided_by = actor
        request.decided_at = now
        request.decision_note = note
        request.updated_by = actor
        request.save(update_fields=[
            "status", "correction", "decided_by", "decided_at", "decision_note",
            "updated_by", "updated_at",
        ])
        _audit(
            actor, membership, request,
            "attendance.missed_scan_approved" if approve else "attendance.missed_scan_rejected",
            before=before,
        )
    return request
