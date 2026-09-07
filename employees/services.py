"""Employee lifecycle services: hire, transfer, revise compensation.

Every function here is atomic. The transfer and revision functions follow the
same "close then open" shape:

    1. lock the currently-open dated row (SELECT ... FOR UPDATE)
    2. close it by setting effective_to
    3. insert its successor starting at the same instant

Locking matters: without it, two concurrent transfers could both read the same
open row, both close it, and both insert a successor — producing overlapping
history. The exclusion constraints would reject the second insert, but locking
turns a confusing constraint error into an orderly wait.

The order matters too: the old row must be closed *before* the successor is
inserted, or the exclusion constraint sees two overlapping open-ended periods.
"""

from django.core.exceptions import ValidationError
from django.db import transaction

from common.services import create_validated
from common.tenant import use_company
from employees.models import Employee, EmployeeAssignment, EmployeeCompensation
from scheduling.models import EmployeeShiftAssignment


@transaction.atomic
def hire_employee(
    *,
    company,
    first_name,
    employee_code,
    branch,
    department,
    designation,
    effective_from,
    pay_basis,
    base_rate,
    last_name="",
    currency=None,
    user=None,
    manager=None,
    shift=None,
    joining_date=None,
    created_by=None,
):
    """Create an employee with their first assignment and compensation.

    All three rows are written together: an employee without compensation or an
    organization placement cannot enter payroll, so a partial hire is invalid.

    Not idempotent by key — re-running is instead *blocked* by the database:
    the exclusion constraint rejects a second assignment overlapping the same
    (company, employee_code) period. An explicit idempotency key can be added
    when hiring is exposed over an API.
    """
    with use_company(company):
        employee = create_validated(
            Employee,
            company=company,
            first_name=first_name,
            last_name=last_name,
            user=user,
            joining_date=joining_date,
            created_by=created_by,
        )
        assignment = create_validated(
            EmployeeAssignment,
            company=company,
            employee=employee,
            employee_code=employee_code,
            branch=branch,
            department=department,
            designation=designation,
            manager=manager,
            effective_from=effective_from,
            change_reason="Initial hire",
            created_by=created_by,
        )
        compensation = create_validated(
            EmployeeCompensation,
            company=company,
            employee=employee,
            pay_basis=pay_basis,
            base_rate=base_rate,
            currency=currency or company.currency,
            effective_from=effective_from,
            reason="Initial compensation",
            created_by=created_by,
        )
        shift_assignment = None
        if shift is not None:
            shift_assignment = create_validated(
                EmployeeShiftAssignment,
                company=company,
                employee=employee,
                shift=shift,
                effective_from=effective_from,
                assigned_by=created_by,
                created_by=created_by,
            )

    return {
        "employee": employee,
        "assignment": assignment,
        "compensation": compensation,
        "shift_assignment": shift_assignment,
    }


def _current_open_row(model, employee):
    """Lock and return the employee's currently open dated row, if any."""
    return (
        model.objects.select_for_update()
        .filter(employee=employee, effective_to__isnull=True)
        .exclude(status="cancelled")
        .order_by("-effective_from")
        .first()
    )


@transaction.atomic
def transfer_employee(
    *,
    employee,
    effective_at,
    branch=None,
    department=None,
    designation=None,
    employee_code=None,
    manager=None,
    reason="",
    actor=None,
):
    """Move an employee to a new placement from ``effective_at``.

    Unspecified attributes are carried forward from the current assignment, so a
    department-only change does not silently reset the branch or code. The old
    interval is preserved, never edited away — that is what lets payroll rebuild
    where the person sat on any past date.
    """
    company = employee.company
    with use_company(company):
        current = _current_open_row(EmployeeAssignment, employee)
        if current is None:
            raise ValidationError("Employee has no open assignment to transfer.")
        if effective_at <= current.effective_from:
            raise ValidationError(
                "Transfer date must be after the current assignment started."
            )

        current.effective_to = effective_at
        current.status = EmployeeAssignment.Status.ENDED
        current.updated_by = actor
        current.save()

        return create_validated(
            EmployeeAssignment,
            company=company,
            employee=employee,
            employee_code=employee_code or current.employee_code,
            branch=branch or current.branch,
            department=department or current.department,
            designation=designation or current.designation,
            manager=manager if manager is not None else current.manager,
            effective_from=effective_at,
            change_reason=reason,
            device_attendance_scope_override=current.device_attendance_scope_override,
            created_by=actor,
        )


@transaction.atomic
def terminate_employee(
    *,
    employee,
    effective_at,
    employment_status=Employee.EmploymentStatus.RESIGNED,
    reason="",
    actor=None,
):
    """End employment: close the open assignment and compensation intervals.

    Nothing is deleted. The person keeps their permanent identity and their whole
    history; only the open intervals are closed. Closing the assignment is also
    what frees their ``employee_code`` for reuse by someone else afterwards —
    the exclusion constraint allows a later, non-overlapping holder.
    """
    company = employee.company
    with use_company(company):
        assignment = _current_open_row(EmployeeAssignment, employee)
        if assignment is not None:
            if effective_at <= assignment.effective_from:
                raise ValidationError(
                    "Termination date must be after the assignment started."
                )
            assignment.effective_to = effective_at
            assignment.status = EmployeeAssignment.Status.ENDED
            assignment.change_reason = reason or assignment.change_reason
            assignment.updated_by = actor
            assignment.save()

        compensation = _current_open_row(EmployeeCompensation, employee)
        if compensation is not None:
            compensation.effective_to = effective_at
            compensation.status = EmployeeCompensation.Status.ENDED
            compensation.updated_by = actor
            compensation.save()

        employee.employment_status = employment_status
        employee.leaving_date = effective_at.date()
        employee.updated_by = actor
        employee.save()

    return employee


@transaction.atomic
def revise_compensation(
    *,
    employee,
    effective_at,
    base_rate=None,
    pay_basis=None,
    currency=None,
    reason="",
    actor=None,
):
    """Close the current pay record and open a new one from ``effective_at``.

    A mid-month raise leaves both rows intact so payroll can split the month at
    the change instead of applying one rate to the whole period.
    """
    company = employee.company
    with use_company(company):
        current = _current_open_row(EmployeeCompensation, employee)
        if current is None:
            raise ValidationError("Employee has no open compensation to revise.")
        if effective_at <= current.effective_from:
            raise ValidationError(
                "Revision date must be after the current compensation started."
            )

        current.effective_to = effective_at
        current.status = EmployeeCompensation.Status.ENDED
        current.updated_by = actor
        current.save()

        return create_validated(
            EmployeeCompensation,
            company=company,
            employee=employee,
            pay_basis=pay_basis or current.pay_basis,
            base_rate=base_rate if base_rate is not None else current.base_rate,
            currency=currency or current.currency,
            effective_from=effective_at,
            reason=reason,
            created_by=actor,
        )
