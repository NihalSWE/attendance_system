"""My profile: an employee's own details, from their own login (Nihal,
2026-09-27).

What they may change about themselves: their personal and contact details,
address and emergency contact, their photo, and their education history.
What the company records - name, Employee ID, work email, placement, joining
and confirmation dates, pay - they see and do not change; that stays with
whoever manages employees. Every change is audited as made by them.

Nothing here opens anyone else's record: the employee is always the one whose
record is linked to the signed-in login.
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from auditlog.services import record_company_event
from common.tenant import use_company
from employees.models import Employee, EmployeeAssignment, EmployeeEducation
from organization import employee_profile as profile
from organization import employee_records as records
from organization.services import require_company_membership

#: What they change themselves. Not the name (the terminals show it), work
#: email, joining or confirmation date: those are the company's record.
SELF_FIELDS = (
    "preferred_name", "phone", "personal_email", "date_of_birth", "gender", "blood_group",
    "marital_status", "national_id", "passport_number", "address",
    "emergency_contact_name", "emergency_contact_phone", "emergency_contact_relation",
)


class MyDetailsForm(profile.PersonalForm):
    class Meta(profile.PersonalForm.Meta):
        fields = SELF_FIELDS
        labels = {**profile.PersonalForm.Meta.labels, "phone": "Phone"}


def mine(actor, company_id):
    """``(membership, employee)`` for the signed-in login's own record."""
    membership = require_company_membership(actor, company_id)
    with use_company(company_id):
        employee = Employee.objects.filter(user=actor).first()
    if employee is None:
        raise PermissionDenied("This login is not linked to an employee record.")
    return membership, employee


def placement(company_id, employee):
    with use_company(company_id):
        return (EmployeeAssignment.objects.select_related("branch", "department", "designation",
                                                          "manager")
                .filter(employee=employee, effective_to__isnull=True)
                .exclude(status__in=["cancelled", "draft"]).first())


@transaction.atomic
def save_details(*, actor, company_id, form):
    membership, employee = mine(actor, company_id)
    with use_company(company_id):
        employee = Employee.objects.select_for_update().get(pk=employee.pk)
        before = {f: str(getattr(employee, f) or "") for f in SELF_FIELDS}
        for field in SELF_FIELDS:
            setattr(employee, field, form.cleaned_data.get(field) or (
                None if field == "date_of_birth" else ""))
        employee.updated_by = actor
        employee.full_clean()
        employee.save()
        after = {f: str(getattr(employee, f) or "") for f in SELF_FIELDS}
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="employee.personal_updated", obj=employee,
            before={f: v for f, v in before.items() if v != after[f]},
            after={**{f: v for f, v in after.items() if v != before[f]}, "by": "themselves"},
        )
    return employee


def save_photo(*, actor, company_id, upload=None, remove=False):
    membership, employee = mine(actor, company_id)
    return profile.store_photo(actor=actor, membership=membership, company_id=company_id,
                               employee=employee, upload=upload, remove=remove)


def save_education(*, actor, company_id, values, row_id=None):
    membership, employee = mine(actor, company_id)
    return records.write_education(actor=actor, membership=membership, company_id=company_id,
                                   employee=employee, values=values, row_id=row_id)


def remove_education(*, actor, company_id, row_id):
    membership, employee = mine(actor, company_id)
    return records.delete_education(actor=actor, membership=membership, company_id=company_id,
                                    employee=employee, row_id=row_id)


def my_education(company_id, employee, row_id):
    with use_company(company_id):
        row = EmployeeEducation.objects.filter(pk=row_id, employee=employee).first()
    if row is None:
        raise PermissionDenied("That qualification is not yours.")
    return row

