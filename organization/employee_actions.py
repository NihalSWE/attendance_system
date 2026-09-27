"""The profile's actions (Ajay, 2026-09-27), those that were not already a
page of their own. Each opens a modal on the profile.

Ajay's twelve, mapped:

- Apply for Leave - Record leave, for this person (``leaves.services``).
- Apply for Late Approval - a late arrival approved by whoever may fix that
  day's attendance: an ``excuse_late`` correction (``attendance.correction_services``).
- Manual Entry - Add missing attendance (a missed-scan request), as before.
- Set as Admin - not done: a company has exactly one owner or administrator
  (``uniq_current_company_administrator``). The modal says who it is.
- Set as HR Manager - their login's access becomes HR (the owner or admin only).
- Set as Line Manager - choose the people who report to them (their line
  manager, ``EmployeeAssignment.manager``). Branch manager access stays under
  Login → Change access.
- Disallow Overtime - from a day on, no overtime approved or paid for them
  (``Employee.no_overtime_from``); Allow again clears it.
- Exclude From Attendance Report - Remove from reports, as before.
- Make Employee Status Inactive - Suspended, and back to Active. It marks them
  only: attendance and salary are counted as before; a suspended person cannot
  request leave or report a missed scan (what Suspended already did).
- Resign Employee / Delete Employee - End employment (history kept), as before.
- Sync Employee - send them to their branch's devices again.
"""

import datetime

from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from access_control.branch_access import can
from attendance.services import _is_locked, locked_ranges, recalculate
from auditlog.services import record_company_event
from common.forms import StyledFormMixin, date_widget
from common.tenant import use_company
from employees.models import Employee
from organization import employee_profile_info as info
from organization.employee_edit_services import get_employee_for_edit, is_company_wide

Status = Employee.EmploymentStatus
WORKING = (Status.ACTIVE, Status.PROBATION)
ENDED = (Status.RESIGNED, Status.TERMINATED, Status.RETIRED)


# --------------------------------------------------------------------------
# Make inactive / active
# --------------------------------------------------------------------------


class InactiveForm(StyledFormMixin, forms.Form):
    reason = forms.CharField(label="Why", max_length=255,
                             help_text="Recorded with the change.")


@transaction.atomic
def set_active(*, actor, company_id, employee_id, active, reason=""):
    membership, employee, _a, _c = get_employee_for_edit(
        actor=actor, company_id=company_id, employee_id=employee_id, code="employees.edit")
    if employee.user_id is not None and employee.user_id == actor.pk:
        raise PermissionDenied("You cannot change your own status.")
    if employee.employment_status in ENDED:
        raise ValidationError("They have left. Their status stays as it is.")
    before = employee.employment_status
    if active:
        if before != Status.SUSPENDED:
            raise ValidationError("They are not inactive.")
        employee.employment_status = Status.ACTIVE
    else:
        reason = (reason or "").strip()
        if not reason:
            raise ValidationError({"reason": "Say why, so the next person reading this knows."})
        if before not in WORKING:
            raise ValidationError("They are already inactive.")
        employee.employment_status = Status.SUSPENDED
    with use_company(company_id):
        employee.updated_by = actor
        employee.save(update_fields=["employment_status", "updated_by", "updated_at"])
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="employee.status_changed", obj=employee,
            before={"employment_status": before},
            after={"employment_status": employee.employment_status, "reason": reason},
        )
    return employee


# --------------------------------------------------------------------------
# Disallow / allow overtime
# --------------------------------------------------------------------------


class OvertimeForm(StyledFormMixin, forms.Form):
    from_day = forms.DateField(label="From", widget=date_widget("Choose a date"),
                               help_text="No overtime is approved or paid for them from this day. "
                                         "Days before it keep what they had.")


def may_set_overtime(actor, company_id, membership, assignment):
    """Whoever decides overtime in their branch (it is pay)."""
    if is_company_wide(membership):
        return True
    return assignment is not None and can(actor, company_id, "overtime.decide",
                                          assignment.branch_id)


def set_overtime(*, actor, company_id, employee_id, from_day, today=None):
    """Disallow overtime from ``from_day``, or allow it again (None). Their
    days from then are worked out again (not in a finalised month)."""
    membership, employee, assignment, _c = get_employee_for_edit(
        actor=actor, company_id=company_id, employee_id=employee_id, code="employees.edit")
    if not may_set_overtime(actor, company_id, membership, assignment):
        raise PermissionDenied("Overtime is allowed or not by whoever decides it in their branch.")
    locked = locked_ranges(company_id)
    if from_day is not None and _is_locked(from_day, locked):
        raise ValidationError({"from_day": f"{from_day:%d %b %Y} is in a finalised salary month. "
                                           "Choose a later day."})
    before = employee.no_overtime_from
    if from_day is None and before is None:
        raise ValidationError("Overtime is already allowed for them.")
    with transaction.atomic(), use_company(company_id):
        employee.no_overtime_from = from_day
        employee.updated_by = actor
        employee.save(update_fields=["no_overtime_from", "updated_by", "updated_at"])
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="employee.overtime_changed", obj=employee,
            before={"no_overtime_from": before.isoformat() if before else None},
            after={"no_overtime_from": from_day.isoformat() if from_day else None},
        )
    today = today or datetime.date.today()
    start = min(day for day in (before, from_day) if day is not None)
    if start <= today:
        # Days in a finalised month are skipped by the recalculation itself.
        recalculate(company_id, employee_ids=[employee.pk], start=start, end=today)
    return employee


# --------------------------------------------------------------------------
# Set as line manager: who reports to them
# --------------------------------------------------------------------------


class ReportsForm(StyledFormMixin, forms.Form):
    people = forms.ModelMultipleChoiceField(
        queryset=Employee.all_objects.none(), label="Who reports to them",
        help_text="They become these people's line manager. It does not change who "
                  "approves their leave.")

    def __init__(self, *args, choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        if choices is not None:
            self.fields["people"].queryset = choices


def set_reports(*, actor, company_id, manager_id, people):
    """Make ``manager_id`` the line manager of each of ``people``."""
    _m, manager, _a, _c = get_employee_for_edit(
        actor=actor, company_id=company_id, employee_id=manager_id, code="employees.view")
    if manager.employment_status not in WORKING:
        raise ValidationError("Only someone working here can be a line manager.")
    with transaction.atomic():
        for person in people:
            info.set_line_manager(actor=actor, company_id=company_id, employee_id=person.pk,
                                  manager=manager)
    return manager


def reports_to(company_id, employee):
    """The people whose current placement names them as line manager."""
    from employees.models import EmployeeAssignment

    with use_company(company_id):
        return list(Employee.objects.filter(
            pk__in=EmployeeAssignment.objects.filter(
                manager=employee, effective_to__isnull=True)
            .exclude(status__in=["cancelled", "draft"]).values("employee_id"))
            .order_by("first_name", "last_name"))


# --------------------------------------------------------------------------
# Apply for late approval
# --------------------------------------------------------------------------

#: How far back a late day can be approved from the profile.
LATE_DAYS = 62


class LateForm(StyledFormMixin, forms.Form):
    work_date = forms.ChoiceField(label="Late on", choices=())
    reason = forms.CharField(label="Why it is approved", max_length=255)

    def __init__(self, *args, days=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["work_date"].choices = [
            (record.work_date.isoformat(),
             f"{record.work_date:%a %d %b %Y} · {record.late_minutes} minutes late")
            for record in days]

    def clean_work_date(self):
        return datetime.date.fromisoformat(self.cleaned_data["work_date"])


def leave_form(company_id, employee, data=None, files=None):
    """Record leave, for this one person (their field hidden)."""
    from common.choices import ActiveStatus
    from leaves.forms import RecordLeaveForm
    from leaves.models import LeaveType

    with use_company(company_id):
        form = RecordLeaveForm(
            data, files,
            employees=Employee.objects.filter(pk=employee.pk),
            leave_types=LeaveType.objects.filter(status=ActiveStatus.ACTIVE).order_by("name"),
            initial={"employee": employee.pk, "pay_type": "paid", "duration": "full_day"},
            auto_id="leave_%s",
        )
    form.fields["employee"].widget = forms.HiddenInput()
    return form


def late_days(company_id, employee, today):
    from attendance.correction_services import late_days as late

    return late(company_id, employee.pk, today - datetime.timedelta(days=LATE_DAYS), today)
