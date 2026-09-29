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

Since 2026-09-26 (Ajay):

- **The case is named**: a missing check-in, check-out, both (two times), or a
  whole missing day (the day's shift start and end, worked out when it is
  entered, so the approver sees the exact times).
- **Someone else can enter it** for the person - HR, their branch manager,
  their department head: anyone who may see that day's attendance
  (``enter_for``). It waits like any request. Whoever entered it does not
  approve it, unless they are the owner or company admin (nobody is above).
- **Approved means looked at**: when the rebuilt day still asks for a look for
  a reason a person can accept (left early, long outside, the rule's
  check-out), approving also accepts it (``accept_review``). That acceptance
  is kept through every rebuild, so the day does not come back in Days to
  review when salary is generated.
"""

import datetime
import zoneinfo

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from attendance import access, correction_services
from attendance.models import MissedScanRequest
from attendance.services import _is_locked, locked_ranges
from auditlog.services import record_company_event
from common.tenant import use_company
from employees.models import Employee
from organization.services import STRUCTURE_ROLES as COMPANY_ADMINS
from organization.services import require_company_membership

Status = MissedScanRequest.Status
Kind = MissedScanRequest.Kind
#: The cases that add two scans: a check-in and a check-out.
TWO_SCANS = (Kind.BOTH, Kind.WHOLE_DAY)

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
            "kind": request.kind,
            "work_date": request.work_date.isoformat(),
            "scan_at": request.scan_at.isoformat(),
            "scan_out_at": request.scan_out_at.isoformat() if request.scan_out_at else "",
            "status": request.status,
            "reason": request.reason,
            "decision_note": request.decision_note,
            "correction_id": request.correction_id,
        },
    )


def attendance_day(*, actor, membership, company_id, employee, kind, at, at_out):
    """The day a missing scan belongs to, when the form did not ask for it.

    Only a whole missing day asks for the day (Nihal, 2026-09-29: three
    calendars for one check-in and check-out). Otherwise it is the date of
    the scan - the check-in when both are missing - or, for a scan after
    midnight on a night shift, the day before: whichever day it counts on,
    tried the way approving would. When neither takes it, the scan's own
    date, and ``_create`` says why it does not fit. None without a time.
    """
    if at is None:
        return None
    try:
        zone = zoneinfo.ZoneInfo(membership.company.timezone or "UTC")
    except zoneinfo.ZoneInfoNotFoundError:
        zone = zoneinfo.ZoneInfo("UTC")
    day = timezone.localtime(at, zone).date()
    scans = [at] + ([at_out] if kind == Kind.BOTH and at_out is not None else [])
    for candidate in (day, day - datetime.timedelta(days=1)):
        try:
            correction_services.check_scans_fit(
                actor=actor, membership=membership, company_id=company_id,
                employee_id=employee.pk, work_date=candidate, scans=scans,
            )
        except (ValidationError, PermissionDenied):
            continue
        return candidate
    return day


def submit(*, actor, company_id, work_date, at, reason, kind=Kind.SCAN, at_out=None):
    """The employee's own request, from their login. ``work_date`` None: the
    day the scan counts on (``attendance_day``)."""
    membership = require_company_membership(actor, company_id)
    if actor.is_superuser:
        raise PermissionDenied("Use your employee login to report a missed scan.")
    employee = my_employee(actor, company_id)
    if employee is None or employee.employment_status not in WORKING:
        raise PermissionDenied("An active employee record linked to your login is required.")
    derived = work_date is None and kind != Kind.WHOLE_DAY
    if derived:
        work_date = attendance_day(actor=actor, membership=membership, company_id=company_id,
                                   employee=employee, kind=kind, at=at, at_out=at_out)
    return _create(actor=actor, membership=membership, company_id=company_id,
                   employee=employee, work_date=work_date, kind=kind, at=at, at_out=at_out,
                   reason=reason, who="You", derived=derived)


def enter_for(*, actor, company_id, employee_id, work_date, kind, at, at_out, reason):
    """Someone else enters the missing attendance for this person: HR, their
    branch manager or department head - whoever may see that day's attendance.
    It waits for approval like the person's own request."""
    membership, scope = access.view_scope(actor, company_id)
    with use_company(company_id):
        employee = Employee.objects.filter(pk=employee_id).first()
    if employee is None:
        raise PermissionDenied("Employee not found in this company.")
    if employee.user_id is not None and employee.user_id == actor.pk:
        raise PermissionDenied("For your own attendance, use Report a missed scan.")
    if employee.employment_status not in WORKING:
        raise ValidationError(f"{employee.full_name} no longer works here.")
    derived = work_date is None and kind != Kind.WHOLE_DAY
    if derived:
        work_date = attendance_day(actor=actor, membership=membership, company_id=company_id,
                                   employee=employee, kind=kind, at=at, at_out=at_out)
    if work_date is not None:
        branch_id, department_id = access.day_place(company_id, employee.pk, work_date)
        if not access.in_scope(branch_id, department_id, scope):
            raise PermissionDenied("That day is in a branch whose attendance you do not see.")
    return _create(actor=actor, membership=membership, company_id=company_id,
                   employee=employee, work_date=work_date, kind=kind, at=at, at_out=at_out,
                   reason=reason, who="They", derived=derived)


def may_approve_own(actor, company_id):
    """The owner or company admin may approve what they entered (``decide``)."""
    membership = require_company_membership(actor, company_id)
    return membership.role in COMPANY_ADMINS


@transaction.atomic
def enter_and_approve(*, actor, company_id, employee_id, work_date, kind, at, at_out, reason):
    """Enter it and approve it in one step - the owner or company admin only.
    Either both happen or neither does."""
    if not may_approve_own(actor, company_id):
        raise PermissionDenied("Someone else who may fix attendance approves what you enter.")
    request = enter_for(actor=actor, company_id=company_id, employee_id=employee_id,
                        work_date=work_date, kind=kind, at=at, at_out=at_out, reason=reason)
    return decide(actor=actor, company_id=company_id, request_id=request.pk, approve=True,
                  note="Entered and approved in one step")


def _times(company_id, employee, work_date, kind, at, at_out):
    """``(check-in or the one scan, check-out or None)`` the request adds."""
    if kind not in Kind.values:
        raise ValidationError({"kind": "Choose what is missing."})
    if kind == Kind.WHOLE_DAY:
        from attendance.models import AttendanceRecord
        from attendance.services import refresh

        refresh(company_id, employee_ids=[employee.pk], start=work_date, end=work_date)
        with use_company(company_id):
            record = AttendanceRecord.objects.filter(
                employee=employee, work_date=work_date).first()
        if record is None or not (record.scheduled_start_at and record.scheduled_end_at):
            raise ValidationError({"kind": (
                "That day has no shift, so a whole day cannot be worked out. Choose "
                '"Missing check-in and check-out" and give the times.')})
        return record.scheduled_start_at, record.scheduled_end_at
    errors = {}
    if at is None:
        errors["at"] = "Choose the date and time of the scan."
    if kind == Kind.BOTH:
        if at_out is None:
            errors["at_out"] = "Choose when they checked out."
        elif at is not None and at_out <= at:
            errors["at_out"] = "The check-out must be after the check-in."
    if errors:
        raise ValidationError(errors)
    return at, (at_out if kind == Kind.BOTH else None)


def _create(*, actor, membership, company_id, employee, work_date, kind, at, at_out, reason, who,
            derived=False):
    # A day worked out from the scan (``attendance_day``) has no box of its
    # own on the form: what is wrong with it is said at the scan's time.
    day_field = "at" if derived else "work_date"
    reason = (reason or "").strip()
    errors = {}
    if not reason:
        errors["reason"] = "Say what happened, so whoever approves it knows."
    if work_date is None:
        errors[day_field] = ("Choose the date and time of the scan." if derived
                             else "Choose the day.")
    if errors:
        raise ValidationError(errors)
    first, second = _times(company_id, employee, work_date, kind, at, at_out)
    now = timezone.now()
    if max(first, second or first) > now:
        raise ValidationError({"at": "A scan cannot be in the future."})
    if _is_locked(work_date, locked_ranges(company_id)):
        raise ValidationError({
            day_field: "Salary for that month is finalised, so the day can no longer change."
        })
    branch_id = access.day_branch(company_id, employee.pk, work_date)
    if branch_id is None:
        raise ValidationError({day_field: f"{who} were not placed in a branch on that day."})

    with use_company(company_id):
        if MissedScanRequest.objects.filter(
            employee=employee, scan_at=first, status=Status.PENDING,
        ).exists():
            raise ValidationError({"at": "This scan is already reported. It is waiting."})

    try:
        correction_services.check_scans_fit(
            actor=actor, membership=membership, company_id=company_id,
            employee_id=employee.pk, work_date=work_date,
            scans=[first] + ([second] if second else []),
        )
    except ValidationError as exc:
        words = [EMPLOYEE_WORDS.get(message, message) for message in exc.messages]
        raise ValidationError({"at": words}) from exc

    with transaction.atomic(), use_company(company_id):
        request = MissedScanRequest.objects.create(
            company=membership.company, employee=employee, work_date=work_date,
            kind=kind, scan_at=first, scan_out_at=second, reason=reason,
            branch_id=branch_id, submitted_at=now, created_by=actor, updated_by=actor,
        )
        _audit(actor, membership, request, "attendance.missed_scan_requested")
    return request


def entered_by_someone_else(request):
    """True when the request was entered for the person, not by them."""
    return request.created_by_id is not None and request.created_by_id != request.employee.user_id


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
    from django.db.models import Q

    from access_control.branch_access import Scope
    from organization.access_services import people

    membership, where = access.fix_scope(actor, company_id)
    # Built here, read by the caller inside the same company.
    with use_company(company_id):
        queryset = MissedScanRequest.objects.exclude(employee__user_id=actor.pk)
        if not where.is_all:
            # Branches keep matching the branch the request was filed in. A head
            # reaches it through the people placed in their department now.
            matches = Q(branch_id__in=where.branches)
            if where.departments:
                headed = people(Scope(set(), where.departments))
                matches |= Q(employee_id__in=list(headed.values_list("pk", flat=True)))
            queryset = queryset.filter(matches)
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
        if (entered_by_someone_else(request) and request.created_by_id == actor.pk
                and membership.role not in COMPANY_ADMINS):
            # Entered and approved by one person is no check at all. The owner
            # or company admin may: there is nobody above them to ask.
            raise PermissionDenied(
                "You entered this, so someone else who may fix attendance approves it.")
        # The day's branch now, as for any fix (the placement may have moved).
        correction_services.require_corrector(
            actor, company_id, request.employee_id, request.work_date
        )
        if request.status != Status.PENDING:
            raise ValidationError("This request has already been decided or withdrawn.")
        before = {"status": request.status}
        now = timezone.now()
        if approve:
            said = (f"Entered for {request.employee.full_name} by "
                    f"{request.created_by.get_username() if request.created_by else 'someone'}"
                    if entered_by_someone_else(request)
                    else f"Reported by {request.employee.full_name}")
            why = (f"{said} ({request.get_kind_display().lower()}): {request.reason}"
                   + (f" — approved: {note}" if note else ""))
            correction = correction_services.add_scan(
                actor=actor, company_id=company_id, employee_id=request.employee_id,
                work_date=request.work_date, at=request.scan_at, reason=why,
            )
            if request.scan_out_at:
                request.correction_out = correction_services.add_scan(
                    actor=actor, company_id=company_id, employee_id=request.employee_id,
                    work_date=request.work_date, at=request.scan_out_at, reason=why,
                )
            request.status = Status.APPROVED
            request.correction = correction
            _accept_what_was_approved(actor, company_id, request, why)
        else:
            if not note:
                raise ValidationError({"note": "Say why it is rejected, so they know."})
            request.status = Status.REJECTED
        request.decided_by = actor
        request.decided_at = now
        request.decision_note = note
        request.updated_by = actor
        request.save(update_fields=[
            "status", "correction", "correction_out", "decided_by", "decided_at",
            "decision_note", "updated_by", "updated_at",
        ])
        _audit(
            actor, membership, request,
            "attendance.missed_scan_approved" if approve else "attendance.missed_scan_rejected",
            before=before,
        )
    return request


def _accept_what_was_approved(actor, company_id, request, why):
    """The approver has just looked at this day and said these times are right.
    If the rebuilt day still asks for a look for a reason a person can accept,
    accept it now - so it stays out of Days to review through every rebuild,
    salary generation included. An open overtime session is not accepted
    here: that is decided on the Overtime page."""
    from attendance.models import AttendanceRecord, ReviewStatus

    with use_company(company_id):
        record = AttendanceRecord.objects.filter(
            employee_id=request.employee_id, work_date=request.work_date).first()
    if (record is not None and record.review_status == ReviewStatus.NEEDS_REVIEW
            and record.review_reason in correction_services.ACCEPTABLE_REASONS):
        correction_services.accept_review(
            actor=actor, company_id=company_id, employee_id=request.employee_id,
            work_date=request.work_date, reason=f"Approved with the missing attendance. {why}",
        )
