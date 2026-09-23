"""Allowances and recurring deductions: the catalogue, and who gets what.

Two steps, on purpose. The company names a component once - House rent,
Transport, Provident fund - and then gives it to people. A component on its
own pays nobody, and an employee's row is **dated**: a raise is a new row, so
a payslip already paid keeps the amount it was paid with (the same shape as
``EmployeeCompensation`` for basic pay).

Who may do what: the catalogue is the company's, like salary settings, so
owner/company admin only. Giving one to an employee, or ending it, follows
that employee's branch - whoever may prepare salary there, which is also who
may add a bonus line to their payslip.
"""

import datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from auditlog.services import record_company_event
from common.choices import ActiveStatus
from common.services import create_validated
from common.tenant import use_company
from organization.services import require_structure_manager
from payroll.models import EmployeeSalaryComponent, SalaryComponent
from payroll.services import salary_branches

Method = SalaryComponent.Method


def components(company_id, *, active_only=False):
    """The company's catalogue. Call inside its tenant context."""
    rows = SalaryComponent.objects.all()
    return rows.filter(status=ActiveStatus.ACTIVE) if active_only else rows


def _clean(values):
    """One component's amount/percentage, matched to how it is calculated."""
    values = dict(values)
    if values.get("method") == Method.PERCENT_OF_BASIC:
        values["default_amount"] = None
        if values.get("default_percent") in (None, ""):
            raise ValidationError({"default_percent": "Give the percentage of basic."})
    else:
        values["default_percent"] = None
        if values.get("default_amount") in (None, ""):
            raise ValidationError({"default_amount": "Give the amount."})
    return values


@transaction.atomic
def create_component(*, actor, company_id, values):
    membership = require_structure_manager(actor, company_id)
    values = _clean(values)
    with use_company(company_id):
        if SalaryComponent.objects.filter(code=values["code"]).exists():
            raise ValidationError({"code": f"{values['code']} is already used."})
        component = create_validated(
            SalaryComponent, company=membership.company,
            created_by=actor, updated_by=actor, **values,
        )
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="salary_component.created", obj=component, after=_snapshot(component),
        )
    return component


@transaction.atomic
def update_component(*, actor, company_id, component_id, values):
    """Edit the catalogue entry. Amounts already given to people do not move:
    those are their own dated rows."""
    membership = require_structure_manager(actor, company_id)
    values = _clean(values)
    with use_company(company_id):
        component = SalaryComponent.objects.filter(pk=component_id).first()
        if component is None:
            raise PermissionDenied("Component not found in this company.")
        if SalaryComponent.objects.filter(code=values["code"]).exclude(pk=component.pk).exists():
            raise ValidationError({"code": f"{values['code']} is already used."})
        before = _snapshot(component)
        for field, value in values.items():
            setattr(component, field, value)
        component.updated_by = actor
        component.full_clean()
        component.save()
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="salary_component.updated", obj=component,
            before=before, after=_snapshot(component),
        )
    return component


@transaction.atomic
def set_component_status(*, actor, company_id, component_id, status):
    """Stop offering a component, or offer it again. Never deleted: payslips
    already paid refer to it. An inactive one stops counting on the next
    generation, and cannot be given to anybody new."""
    membership = require_structure_manager(actor, company_id)
    if status not in dict(ActiveStatus.choices):
        raise ValidationError({"status": "Unknown status."})
    with use_company(company_id):
        component = SalaryComponent.objects.filter(pk=component_id).first()
        if component is None:
            raise PermissionDenied("Component not found in this company.")
        before = _snapshot(component)
        component.status = status
        component.updated_by = actor
        component.full_clean()
        component.save()
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="salary_component.status_changed", obj=component,
            before=before, after=_snapshot(component),
        )
    return component


def _snapshot(component):
    return {"code": component.code, "name": component.name, "kind": component.kind,
            "method": component.method,
            "default_amount": str(component.default_amount or ""),
            "default_percent": str(component.default_percent or ""),
            "status": component.status}


def _may_prepare_for(actor, company_id, employee):
    """The membership, if ``actor`` may change this employee's pay; else refused."""
    from access_control.branch_access import ALL_BRANCHES
    from employees.models import EmployeeAssignment

    membership, prepare = salary_branches(actor, company_id, "salary.prepare")
    if not prepare:
        raise PermissionDenied("Allowances need access to prepare salary.")
    with use_company(company_id):
        placement = (
            EmployeeAssignment.objects.filter(employee=employee)
            .exclude(status="cancelled").order_by("-effective_from").first()
        )
    branch_id = placement.branch_id if placement else None
    if prepare is not ALL_BRANCHES and branch_id not in prepare:
        raise PermissionDenied("That employee is not in a branch you prepare salary for.")
    return membership


def employee_rows(company_id, employee):
    """One employee's allowances and deductions, newest first. In context."""
    return (
        EmployeeSalaryComponent.objects.select_related("component")
        .filter(employee=employee)
        .exclude(status=EmployeeSalaryComponent.Status.CANCELLED)
        .order_by("-effective_from", "component__name")
    )


@transaction.atomic
def give_component(*, actor, company_id, employee_id, values):
    """Give an employee an allowance or deduction from a date."""
    from employees.models import Employee

    with use_company(company_id):
        employee = Employee.objects.filter(pk=employee_id).first()
        if employee is None:
            raise PermissionDenied("Employee not found in this company.")
    membership = _may_prepare_for(actor, company_id, employee)
    component = values["component"]
    starts = values["effective_from"]
    with use_company(company_id):
        if component.status != ActiveStatus.ACTIVE:
            raise ValidationError({"component": f"{component.name} is not offered any more."})
        amount = values.get("amount")
        percent = values.get("percent")
        if component.method == Method.PERCENT_OF_BASIC:
            amount = None
            percent = percent if percent not in (None, "") else component.default_percent
            if percent in (None, ""):
                raise ValidationError({"percent": "Give the percentage of basic."})
        else:
            percent = None
            amount = amount if amount not in (None, "") else component.default_amount
            if amount in (None, ""):
                raise ValidationError({"amount": "Give the amount."})
        open_row = employee_rows(company_id, employee).filter(
            component=component, status=EmployeeSalaryComponent.Status.ACTIVE,
        ).filter(effective_to__isnull=True).first()
        if open_row is not None:
            if starts <= open_row.effective_from:
                raise ValidationError({
                    "effective_from": (
                        f"{component.name} already runs from "
                        f"{open_row.effective_from:%d %b %Y}; a change must start after it."
                    )
                })
            # A change: the old amount ends the day before the new one starts,
            # so a month split between them pays each for its own days.
            open_row.effective_to = starts - datetime.timedelta(days=1)
            open_row.updated_by = actor
            open_row.full_clean()
            open_row.save()
        row = create_validated(
            EmployeeSalaryComponent, company=membership.company, employee=employee,
            component=component, amount=amount, percent=percent,
            effective_from=starts, reason=values.get("reason", "") or "",
            created_by=actor, updated_by=actor,
        )
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="employee_component.given", obj=row,
            after={"employee": employee.pk, "component": component.code,
                   "amount": str(amount or ""), "percent": str(percent or ""),
                   "from": starts.isoformat()},
        )
    return row


@transaction.atomic
def end_component(*, actor, company_id, row_id, last_day):
    """Stop an employee's allowance after ``last_day``."""
    with use_company(company_id):
        row = (
            EmployeeSalaryComponent.objects.select_related("component", "employee")
            .filter(pk=row_id, status=EmployeeSalaryComponent.Status.ACTIVE).first()
        )
        if row is None:
            raise PermissionDenied("That allowance was not found.")
    membership = _may_prepare_for(actor, company_id, row.employee)
    if last_day is None:
        raise ValidationError({"last_day": "Choose the last day it applies."})
    if last_day < row.effective_from:
        raise ValidationError({
            "last_day": f"It starts on {row.effective_from:%d %b %Y}; it cannot end before that."
        })
    with use_company(company_id):
        before = {"effective_to": row.effective_to.isoformat() if row.effective_to else None}
        row.effective_to = last_day
        row.updated_by = actor
        row.full_clean()
        row.save()
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="employee_component.ended", obj=row, before=before,
            after={"employee": row.employee_id, "component": row.component.code,
                   "to": last_day.isoformat()},
        )
    return row
