"""Editing an employee: personal details, placement, and salary.

Placement and salary are dated history that attendance and payroll read, so
they are changed through the existing ``employees.services`` functions:

- a change **from a later date** closes the current row and opens a new one
  (``transfer_employee`` / ``revise_compensation``), keeping the history;
- a change **from the same date the current row started** is a correction of
  a mistake, so the current row is fixed in place rather than turned into a
  zero-length piece of history.

Every write: the employee must belong to the company, validation through
``full_clean``, and an audit row in the same transaction. Who may write
(A12 part 4): details and placement — the owner/company admin, or anyone with
``employees.edit`` in the employee's current branch (a placement can only move
to a branch where they have it too); salary — the owner/company admin, or
anyone with ``salary.prepare`` in that branch.
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from auditlog.services import record_company_event
from common.services import create_validated
from common.tenant import use_company
from employees.models import Employee, EmployeeAssignment, EmployeeCompensation
from employees.services import revise_compensation, transfer_employee
from access_control.branch_access import COMPANY_WIDE_ROLES, can
from organization.services import (
    assert_branch_in_scope,
    require_company_membership,
    require_structure_manager,
)

DETAIL_FIELDS = ("first_name", "last_name", "work_email", "phone", "joining_date")


def _open_row(model, employee):
    return (
        model.objects.filter(employee=employee, effective_to__isnull=True)
        .exclude(status__in=["cancelled", "draft"])
        .order_by("-effective_from")
        .first()
    )


def is_company_wide(membership):
    return membership.role in COMPANY_WIDE_ROLES


def get_employee_for_edit(*, actor, company_id, employee_id, code=None):
    """The employee, their current placement and salary — if ``actor`` may.

    ``code=None``: owner/company admin only, as before A12 (the employee page
    and End employment still use this). With a branch permission code, anyone
    holding it in the employee's current branch may, too — and the head of the
    department they are placed in, for the codes a head holds.
    """
    if code is None:
        membership = require_structure_manager(actor, company_id)
    else:
        membership = require_company_membership(actor, company_id)
    with use_company(company_id):
        employee = Employee.objects.filter(pk=employee_id).first()
        if employee is None:
            raise PermissionDenied("Employee not found in this company.")
        assignment = _open_row(EmployeeAssignment, employee)
        compensation = _open_row(EmployeeCompensation, employee)
    if code is not None and not is_company_wide(membership):
        reachable = assignment is not None and can(
            actor, company_id, code, assignment.branch_id, assignment.department_id
        )
        if not reachable:
            raise PermissionDenied(
                "This employee is not in a branch or department you look after."
            )
    return membership, employee, assignment, compensation


def card_permissions(actor, company_id, membership, assignment):
    """Which Edit employee cards ``actor`` may use for this employee (A12 part 4)."""
    if is_company_wide(membership):
        return {"edit": True, "logins": True, "role": True, "salary": True,
                "salary_view": True, "shift": True, "company": True}
    branch = assignment.branch_id if assignment else None
    return {
        "edit": branch is not None and can(actor, company_id, "employees.edit", branch),
        "logins": branch is not None and can(actor, company_id, "employees.logins", branch),
        # Pay follows "prepare salary" in that branch (a branch manager has it).
        # Making someone a branch manager and own shifts stay with the company
        # (shifts are the company's Shifts area).
        "role": False,
        "salary": branch is not None and can(actor, company_id, "salary.prepare", branch),
        "salary_view": branch is not None and can(actor, company_id, "salary.view", branch),
        "shift": False,
        "company": False,
    }


@transaction.atomic
def update_employee_details(*, actor, company_id, employee_id, values):
    membership, employee, _, _ = get_employee_for_edit(
        actor=actor, company_id=company_id, employee_id=employee_id, code="employees.edit"
    )
    unsupported = set(values) - set(DETAIL_FIELDS)
    if unsupported:
        raise ValidationError(f"Unsupported field: {', '.join(sorted(unsupported))}")
    with use_company(company_id):
        before = {f: str(getattr(employee, f) or "") for f in DETAIL_FIELDS}
        for field, value in values.items():
            setattr(employee, field, value)
        employee.updated_by = actor
        employee.full_clean()
        employee.save()
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="employee.details_updated", obj=employee, before=before,
            after={f: str(getattr(employee, f) or "") for f in DETAIL_FIELDS},
        )
    return employee


@transaction.atomic
def change_placement(*, actor, company_id, employee_id, values):
    """New branch / department / designation / code from a date."""
    membership, employee, current, _ = get_employee_for_edit(
        actor=actor, company_id=company_id, employee_id=employee_id, code="employees.edit"
    )
    if current is None:
        raise ValidationError("This employee has no current placement to change.")
    if not is_company_wide(membership) and not can(
        actor, company_id, "employees.edit", values["branch"].pk
    ):
        raise PermissionDenied("You can only place people in branches you look after.")
    starts = values["effective_at"]
    with use_company(company_id):
        assert_branch_in_scope(membership, values["branch"])
        before = {
            "branch_id": current.branch_id, "department_id": current.department_id,
            "designation_id": current.designation_id, "employee_code": current.employee_code,
            "effective_from": current.effective_from.isoformat(),
        }
        if starts < current.effective_from:
            raise ValidationError({
                "placement_from": (
                    f"The current placement started on {current.effective_from:%d %b %Y}; "
                    "a change cannot start before it."
                )
            })
        correction = starts == current.effective_from
        if correction:
            # A correction: fix the current row instead of adding history.
            current.branch = values["branch"]
            current.department = values["department"]
            current.designation = values["designation"]
            current.employee_code = values["employee_code"]
            current.change_reason = values.get("reason", "") or current.change_reason
            current.updated_by = actor
            current.full_clean()
            current.save()
            assignment = current
        else:
            assignment = transfer_employee(
                employee=employee, effective_at=starts,
                branch=values["branch"], department=values["department"],
                designation=values["designation"],
                employee_code=values["employee_code"],
                reason=values.get("reason", ""), actor=actor,
            )
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="employee.placement_changed", obj=employee, before=before,
            after={
                "branch_id": assignment.branch_id, "department_id": assignment.department_id,
                "designation_id": assignment.designation_id,
                "employee_code": assignment.employee_code,
                "effective_from": assignment.effective_from.isoformat(),
                "correction": correction,
            },
        )
    return assignment


@transaction.atomic
def change_salary(*, actor, company_id, employee_id, values):
    """New pay basis / rate from a date — or the first salary, if there is none.

    An employee created from a device's user list (devices/services/user_sync.py)
    has a placement but no salary; refusing here left no way to give them one
    (Ajay, 2026-09-14). They get their first salary instead.
    """
    # Pay: the owner/company admin, or whoever may prepare salary in the
    # employee's branch (A12 part 4; a branch manager has it).
    membership, employee, _, current = get_employee_for_edit(
        actor=actor, company_id=company_id, employee_id=employee_id, code="salary.prepare"
    )
    if current is None:
        return _set_first_salary(membership, employee, values, actor)
    starts = values["effective_at"]
    with use_company(company_id):
        before = {
            "pay_basis": current.pay_basis, "base_rate": str(current.base_rate),
            "effective_from": current.effective_from.isoformat(),
        }
        if starts < current.effective_from:
            raise ValidationError({
                "salary_from": (
                    f"The current salary started on {current.effective_from:%d %b %Y}; "
                    "a change cannot start before it."
                )
            })
        if starts == current.effective_from:
            current.pay_basis = values["pay_basis"]
            current.base_rate = values["base_rate"]
            current.reason = values.get("reason", "") or current.reason
            current.updated_by = actor
            current.full_clean()
            current.save()
            compensation = current
        else:
            compensation = revise_compensation(
                employee=employee, effective_at=starts,
                pay_basis=values["pay_basis"], base_rate=values["base_rate"],
                reason=values.get("reason", ""), actor=actor,
            )
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="employee.salary_changed", obj=employee, before=before,
            after={
                "pay_basis": compensation.pay_basis, "base_rate": str(compensation.base_rate),
                "effective_from": compensation.effective_from.isoformat(),
            },
        )
    return compensation


def first_placement(employee):
    """When the employee was first placed. Call inside the company's context."""
    return (
        EmployeeAssignment.objects.filter(employee=employee)
        .exclude(status__in=["cancelled", "draft"])
        .order_by("effective_from")
        .first()
    )


def _set_first_salary(membership, employee, values, actor):
    """The first salary of an employee who has none. From a date not before
    their first placement: there is no attendance to pay before it."""
    starts = values["effective_at"]
    with use_company(membership.company_id):
        placed = first_placement(employee)
        if placed is not None and starts < placed.effective_from:
            raise ValidationError({
                "salary_from": (
                    f"They were placed on {placed.effective_from:%d %b %Y}; "
                    "the salary cannot start before that."
                )
            })
        if EmployeeCompensation.objects.filter(employee=employee).exclude(
            status__in=["cancelled", "draft"]
        ).exists():
            # Only ended rows: employment was ended, which is not this card's to undo.
            raise ValidationError("This employee's salary has ended; there is no current salary to change.")
        compensation = create_validated(
            EmployeeCompensation,
            company=membership.company,
            employee=employee,
            pay_basis=values["pay_basis"],
            base_rate=values["base_rate"],
            currency=membership.company.currency,
            effective_from=starts,
            reason=values.get("reason", "") or "First salary",
            created_by=actor,
        )
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="employee.salary_set", obj=employee, before={"salary": None},
            after={
                "pay_basis": compensation.pay_basis, "base_rate": str(compensation.base_rate),
                "effective_from": compensation.effective_from.isoformat(),
            },
        )
    return compensation
