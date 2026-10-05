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

import datetime
from zoneinfo import ZoneInfo

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q

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
#: The ones the attendance terminals display, so a change has to reach them.
NAME_FIELDS = ("first_name", "last_name")


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


def placement_branches(actor, company_id, membership, may):
    """The branches a placement may move to: the active ones the actor sees -
    and, for anyone but the company, only where they may edit people. Call
    inside the company's context."""
    from access_control.branch_access import scope_queryset
    from common.choices import ActiveStatus
    from organization.services import visible_branches

    branches = visible_branches(membership).filter(status=ActiveStatus.ACTIVE)
    if not may["company"]:
        branches = scope_queryset(branches, actor, company_id, "employees.edit", field="pk")
    return branches


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
        # Changing pay shows the current pay, so it needs seeing it too.
        "salary": branch is not None and can(actor, company_id, "salary.prepare", branch)
        and can(actor, company_id, "salary.view", branch),
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
        # The terminals show the name they were given, and keep showing the
        # old one until they are told. Renaming someone here sends it to every
        # device they are on; the device keys them by number, so the record is
        # updated in place and their fingerprint and face are untouched.
        if any(before[f] != str(getattr(employee, f) or "") for f in NAME_FIELDS):
            from devices.services import mapping as device_mapping

            device_mapping.resend_identity(actor=actor, employee=employee)
    return employee


def _on_the_day(starts, began, company):
    """``began`` when ``starts`` falls earlier on the same local day; else ``starts``.

    This page works in whole days: "from 21 Sep" arrives as midnight on the
    21st. A placement or salary can begin later that day - a device import
    starts the placement at the moment it runs (devices/services/mapping.py,
    user_sync.py). Read literally, midnight is "before" 14:05, so a change
    dated the very day was refused, and so was the first salary on its own
    default date. A date on the day something began means that day.
    """
    if starts >= began:
        return starts
    zone = ZoneInfo(getattr(company, "timezone", None) or "UTC")
    if starts.astimezone(zone).date() == began.astimezone(zone).date():
        return began
    return starts


def _day(moment, company):
    """``moment`` as the company's own date, for messages. Rows are stored in
    UTC, so midnight on 3 Oct in Dhaka printed as "02 Oct" (2026-10-03)."""
    zone = ZoneInfo(getattr(company, "timezone", None) or "UTC")
    return f"{moment.astimezone(zone):%d %b %Y}"


def _override_from(model, employee, current, starts, company, field):
    """The latest save wins (Nihal, 2026-10-03): ``current`` will start on
    ``starts``, earlier than it does now, and whatever the history held from
    that date gives way to it.

    Rows starting on or after ``starts`` are cancelled - kept, for the audit
    trail, but no longer read by attendance or payroll - and a row running on
    that date ends there. Never reaches into a finalised payroll: settled pay
    is not rewritten. Call inside the company's context, before moving
    ``current``. Returns what changed, for the audit row.
    """
    from payroll.models import PayrollRecord, PayrollRun

    first_day = starts.astimezone(ZoneInfo(getattr(company, "timezone", None) or "UTC")).date()
    finalised = (
        PayrollRecord.objects.filter(
            employee=employee, payroll_run__status=PayrollRun.Status.POSTED,
            payroll_run__payroll_period__end_date__gte=first_day,
        ).select_related("payroll_run__payroll_period")
        .order_by("-payroll_run__payroll_period__end_date").first()
    )
    if finalised is not None:
        period = finalised.payroll_run.payroll_period
        raise ValidationError({field: (
            f"Salary up to {period.end_date:%d %b %Y} ({period.name}) is already finalised; "
            f"a change can start on {period.end_date + datetime.timedelta(days=1):%d %b %Y} "
            "at the earliest."
        )})
    others = (
        model.objects.filter(employee=employee).exclude(pk=current.pk)
        .exclude(status__in=["cancelled", "draft"])
    )
    cancelled, ended = [], []
    for row in others.filter(effective_from__gte=starts):
        row.status = "cancelled"
        row.save(update_fields=["status", "updated_at"])
        cancelled.append(row.pk)
    for row in others.filter(effective_from__lt=starts).filter(
        Q(effective_to__isnull=True) | Q(effective_to__gt=starts)
    ):
        row.effective_to = starts
        row.status = "ended"
        row.save(update_fields=["effective_to", "status", "updated_at"])
        ended.append(row.pk)
    return {"cancelled": cancelled, "ended_early": ended}


def _rebuild_attendance(company_id, employee, starts, company):
    """Attendance days from ``starts`` point at the placement that now covers
    them (a cancelled one no longer does). Days before the joining date are
    not counted anyway, and days in a finalised payroll are left alone."""
    from attendance.services import recalculate
    from organization.employee_detail_services import company_today

    zone = ZoneInfo(getattr(company, "timezone", None) or "UTC")
    first = starts.astimezone(zone).date()
    if employee.joining_date and employee.joining_date > first:
        first = employee.joining_date
    today = company_today(company)
    if first <= today:
        recalculate(company_id, employee_ids=[employee.pk], start=first, end=today)


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
    starts = _on_the_day(values["effective_at"], current.effective_from, membership.company)
    with use_company(company_id):
        assert_branch_in_scope(membership, values["branch"])
        before = {
            "branch_id": current.branch_id, "department_id": current.department_id,
            "designation_id": current.designation_id, "employee_code": current.employee_code,
            "effective_from": current.effective_from.isoformat(),
        }
        overridden = None
        if starts < current.effective_from:
            # Earlier than the current placement: the latest save wins, from
            # that date (someone added today who has worked there since 2023).
            overridden = _override_from(EmployeeAssignment, employee, current, starts,
                                        membership.company, "placement_from")
            current.effective_from = starts
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
                **({"overrode": overridden} if overridden else {}),
            },
        )
        if overridden is not None:
            _rebuild_attendance(company_id, employee, starts, membership.company)
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
    starts = _on_the_day(values["effective_at"], current.effective_from, membership.company)
    with use_company(company_id):
        before = {
            "pay_basis": current.pay_basis, "base_rate": str(current.base_rate),
            "effective_from": current.effective_from.isoformat(),
        }
        overridden = None
        if starts < current.effective_from:
            # As for the placement: the latest save wins from that date -
            # never before they were placed (no attendance to pay before it).
            placed = first_placement(employee)
            if placed is not None:
                # The day they were placed means from the placement.
                starts = _on_the_day(starts, placed.effective_from, membership.company)
            if placed is not None and starts < placed.effective_from:
                raise ValidationError({
                    "salary_from": (
                        f"They were placed on {_day(placed.effective_from, membership.company)}; "
                        "the salary cannot start before that."
                    )
                })
            starts = min(starts, current.effective_from)
            if starts < current.effective_from:
                overridden = _override_from(EmployeeCompensation, employee, current, starts,
                                            membership.company, "salary_from")
                current.effective_from = starts
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
                **({"overrode": overridden} if overridden else {}),
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
        if placed is not None:
            # The day they were placed means from the placement, never before it.
            starts = _on_the_day(starts, placed.effective_from, membership.company)
        if placed is not None and starts < placed.effective_from:
            raise ValidationError({
                "salary_from": (
                    f"They were placed on {_day(placed.effective_from, membership.company)}; "
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
