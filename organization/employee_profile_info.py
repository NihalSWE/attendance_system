"""What the profile's General information shows beyond the employee row, and
the one edit it adds - the line manager (Ajay, 2026-09-27).

Ajay's checklist for General information, mapped to what the system holds:
name, Employee ID, work email and phone (Employee); Workplace, Department,
Designation and Line manager (the current placement); Shift group (the shift
they work today - their own, their department's or the company's); Gender
(personal information); the third-party device person ID and RFID (the device
user number and card number on their current device enrolments); Joining and
End date; HR manager (the company's HR logins). There is no sub-department,
and "employment type" is the employment status (active, probation...) - no
field is invented for either.

The line manager is ``EmployeeAssignment.manager``: set when someone is
created, never editable until now. It records who they report to; it does
not change who approves their leave (that is their branch manager, the head
of their department, or whoever was given "Approve leave").
"""

from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction

from access_control.branch_access import branches_for
from accounts.models import CompanyMembership
from auditlog.services import record_company_event
from common.forms import StyledFormMixin
from common.tenant import use_company
from employees.models import Employee
from organization.access_services import people
from organization.employee_edit_services import get_employee_for_edit

#: Statuses of someone who can be a line manager: still working here.
WORKING = ("active", "probation")


def todays_shift(company_id, employee, assignment, today):
    """``(shift, where it comes from)`` for today, or ``(None, "")``."""
    from scheduling.calendar import WorkCalendar

    with use_company(company_id):
        calendar = WorkCalendar(company_id, today, today)
        department_id = assignment.department_id if assignment else None
        works = calendar.shift_for(department_id, today, employee_id=employee.pk)
        if calendar.employee_shift(employee.pk, today) is not None:
            source = "their own shift"
        elif calendar.by_department and calendar.shift_for(department_id, today) != calendar.shift:
            source = "their department's shift"
        else:
            source = "the company shift"
    return works, (source if works else "")


def hr_managers(company_id):
    """The company's HR logins, by the name of their employee record if any."""
    logins = list(CompanyMembership.all_objects.filter(
        company_id=company_id, role=CompanyMembership.Role.HR, status="active",
        ended_at__isnull=True, user__is_active=True,
    ).select_related("user").order_by("user__email"))
    with use_company(company_id):
        named = {e.user_id: e.full_name for e in
                 Employee.objects.filter(user_id__in=[m.user_id for m in logins])}
    return [named.get(m.user_id) or m.user.email for m in logins]


def general(company_id, employee, assignment, devices, today):
    """Everything General information shows that the page does not have yet."""
    shift, source = todays_shift(company_id, employee, assignment, today)
    current = [d.row for d in devices if d.is_current]
    return {
        "shift": shift,
        "shift_from": source,
        # The number each terminal knows them by ("person ID" on a third-party
        # device), and a card number where one was enrolled.
        "device_ids": sorted({row.device_user_id for row in current}),
        "cards": sorted({row.card_number for row in current if row.card_number}),
        "hr_managers": hr_managers(company_id),
    }


class LineManagerForm(StyledFormMixin, forms.Form):
    manager = forms.ModelChoiceField(
        queryset=Employee.all_objects.none(), required=False, label="Line manager",
        help_text="Who they report to. It does not change who approves their leave.",
    )

    def __init__(self, *args, choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        if choices is not None:
            self.fields["manager"].queryset = choices
        self.fields["manager"].empty_label = "No line manager"


def line_manager_choices(actor, company_id, employee):
    """The people ``actor`` may see who are still working here, not them.
    Call inside the company's context."""
    scope = branches_for(actor, company_id, "employees.view")
    return (people(scope).filter(employment_status__in=WORKING).exclude(pk=employee.pk)
            .order_by("first_name", "last_name"))


@transaction.atomic
def set_line_manager(*, actor, company_id, employee_id, manager):
    """Record who they report to, on their current placement. Not dated: it
    is who they report to now, and the placement history keeps the rest."""
    membership, employee, assignment, _pay = get_employee_for_edit(
        actor=actor, company_id=company_id, employee_id=employee_id, code="employees.edit")
    if assignment is None:
        raise ValidationError("They have no current placement to record a line manager on.")
    with use_company(company_id):
        if manager is not None:
            if manager.pk == employee.pk:
                raise ValidationError({"manager": "Someone cannot be their own line manager."})
            if not line_manager_choices(actor, company_id, employee).filter(pk=manager.pk).exists():
                raise ValidationError({"manager": "Choose someone working in a branch you see."})
        before = assignment.manager.full_name if assignment.manager_id else ""
        assignment.manager = manager
        assignment.updated_by = actor
        assignment.full_clean()
        assignment.save(update_fields=["manager", "updated_by", "updated_at"])
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="employee.line_manager_changed", obj=employee,
            before={"line_manager": before},
            after={"line_manager": manager.full_name if manager else ""},
        )
    return assignment
