"""Edit employee: details, placement and salary, each saved on its own."""

from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from common.choices import ActiveStatus
from common.forms import apply_service_errors
from common.tenant import use_company
from organization import employee_edit_services as services
from organization import employee_login
from organization.employee_edit_forms import (
    EmployeeDetailsForm,
    GiveLoginForm,
    LoginPasswordForm,
    LoginRoleForm,
    PlacementForm,
    SalaryForm,
)
from access_control.branch_access import scope_queryset
from organization.services import visible_branches
from organization.views import _company_or_redirect
from scheduling import services as schedule
from scheduling.calendar import WorkCalendar
from scheduling.forms import EmployeeShiftForm, EndEmployeeShiftForm
from scheduling.models import Shift


# The login service names fields as it stores them; the page's forms prefix them.
_LOGIN_FIELDS = {
    "email": "login_email", "password": "login_password",
    "password_confirm": "login_password_confirm", "role": "login_role",
    "branches": "login_branches",
}


def _login_errors(form, exc):
    if not hasattr(exc, "error_dict"):
        for message in exc.messages:
            form.add_error(None, message)
        return
    for field, errors in exc.error_dict.items():
        target = _LOGIN_FIELDS.get(field, field)
        if target == "login_password" and "login_new_password" in form.fields:
            target = "login_new_password"
        if target == "login_password_confirm" and "login_new_password_confirm" in form.fields:
            target = "login_new_password_confirm"
        for error in errors:
            form.add_error(target if target in form.fields else None, error)


def _local_date(instant, company):
    if instant is None:
        return None
    return instant.astimezone(ZoneInfo(company.timezone or "UTC")).date()


@login_required
@require_http_methods(["GET", "POST"])
def employee_edit(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    membership, employee, assignment, compensation = services.get_employee_for_edit(
        actor=request.user, company_id=company_id, employee_id=pk, code="employees.edit"
    )
    company = membership.company
    section = request.POST.get("section", "") if request.method == "POST" else ""
    today = timezone.localdate()
    # A12 part 4: which cards this person may use for this employee.
    may = services.card_permissions(request.user, company_id, membership, assignment)
    needs = {"salary": "salary", "shift": "shift", "shift_end": "shift",
             "login_give": "logins", "login_password": "logins", "login_disable": "logins",
             "login_enable": "logins", "login_role": "role"}
    if section in needs and not may[needs[section]]:
        raise PermissionDenied("That part of this page is the company's to change.")

    with use_company(company_id):
        branches = visible_branches(membership).filter(status=ActiveStatus.ACTIVE)
        if not may["company"]:
            # A placement can only move to a branch where they may edit people.
            branches = scope_queryset(
                branches, request.user, company_id, "employees.edit", field="pk"
            )

        details = EmployeeDetailsForm(
            request.POST if section == "details" else None, instance=employee
        )
        placement = PlacementForm(
            request.POST if section == "placement" else None,
            company=company, branches=branches,
            initial={
                "branch": assignment.branch_id if assignment else None,
                "department": assignment.department_id if assignment else None,
                "designation": assignment.designation_id if assignment else None,
                "employee_code": assignment.employee_code if assignment else "",
                "placement_from": today,
            },
        )
        # No salary yet (e.g. created from a device's users): the first one
        # starts, by default, when they were placed, so no worked day is missed.
        placed = None if compensation else services.first_placement(employee)
        salary = SalaryForm(
            request.POST if section == "salary" else None,
            company=company,
            initial={
                "pay_basis": compensation.pay_basis if compensation else "monthly",
                "base_rate": compensation.base_rate if compensation else None,
                "salary_from": (
                    _local_date(placed.effective_from, company) if placed else today
                ),
            },
        )
        if compensation is None:
            salary.fields["salary_from"].help_text = (
                "The salary counts from this date. It starts, by default, when they were placed."
            )

        shifts = Shift.objects.filter(status=ActiveStatus.ACTIVE).order_by("name")
        shift_form = EmployeeShiftForm(
            request.POST if section == "shift" else None,
            shifts=shifts, initial={"first_day": today},
        )
        end_form = EndEmployeeShiftForm(
            request.POST if section == "shift_end" else None, initial={"last_day": today},
        )

        if section in ("shift", "shift_end"):
            chosen = shift_form if section == "shift" else end_form
            if chosen.is_valid():
                try:
                    if section == "shift":
                        schedule.set_employee_shift(
                            actor=request.user, company_id=company_id,
                            values={"employee": employee, **chosen.cleaned_data},
                        )
                        message = "Shift saved. It wins over the department's shift."
                    else:
                        schedule.end_employee_shift(
                            actor=request.user, company_id=company_id,
                            assignment_id=request.POST.get("assignment"),
                            last_day=chosen.cleaned_data["last_day"],
                        )
                        message = "Shift ended. The employee is back on the department's shift afterwards."
                except ValidationError as exc:
                    apply_service_errors(chosen, exc)
                else:
                    messages.success(request, message)
                    return redirect(f"{reverse('organization:employee_edit', args=[employee.pk])}#shift")

        # Login (A6).
        login = employee_login.login_for(company_id, employee)
        manager_branches = visible_branches(membership).filter(status=ActiveStatus.ACTIVE).order_by("name")
        give_login = GiveLoginForm(
            request.POST if section == "login_give" else None, branches=manager_branches,
            initial={"login_email": employee.work_email, "login_role": "employee"},
        )
        login_role = LoginRoleForm(
            request.POST if section == "login_role" else None, branches=manager_branches,
            initial={
                "login_role": login.role if login else "employee",
                "login_branches": [b.pk for b in login.allowed_branches.all()] if login else [],
            },
        )
        login_password = LoginPasswordForm(request.POST if section == "login_password" else None)

        if section in ("login_give", "login_role", "login_password", "login_disable", "login_enable"):
            chosen = {"login_give": give_login, "login_role": login_role,
                      "login_password": login_password}.get(section)
            if chosen is None or chosen.is_valid():
                try:
                    if section == "login_give":
                        employee_login.give_login(
                            actor=request.user, company_id=company_id,
                            employee_id=employee.pk, values=chosen.service_values(),
                        )
                        message = "Login created. Give them the email and password to sign in."
                    elif section == "login_role":
                        employee_login.change_login_role(
                            actor=request.user, company_id=company_id,
                            employee_id=employee.pk, values=chosen.role_values(),
                        )
                        message = "Access saved."
                    elif section == "login_password":
                        employee_login.reset_login_password(
                            actor=request.user, company_id=company_id,
                            employee_id=employee.pk, values=chosen.service_values(),
                        )
                        message = "New password set. Give it to them."
                    else:
                        employee_login.set_login_active(
                            actor=request.user, company_id=company_id,
                            employee_id=employee.pk, active=section == "login_enable",
                        )
                        message = ("Login enabled." if section == "login_enable"
                                   else "Login disabled. They can no longer sign in to this company.")
                except (ValidationError, PermissionDenied) as exc:
                    if chosen is None or isinstance(exc, PermissionDenied):
                        # No form to show it on: say it at the top of the page.
                        messages.error(request, " ".join(getattr(exc, "messages", [str(exc)])))
                        return redirect(f"{reverse('organization:employee_edit', args=[employee.pk])}#login")
                    _login_errors(chosen, exc)
                else:
                    messages.success(request, message)
                    return redirect(f"{reverse('organization:employee_edit', args=[employee.pk])}#login")

        form = {"details": details, "placement": placement, "salary": salary}.get(section)
        if form is not None and form.is_valid():
            try:
                if section == "details":
                    services.update_employee_details(
                        actor=request.user, company_id=company_id,
                        employee_id=employee.pk, values=dict(form.cleaned_data),
                    )
                    message = "Details saved."
                elif section == "placement":
                    services.change_placement(
                        actor=request.user, company_id=company_id,
                        employee_id=employee.pk, values=form.service_values(),
                    )
                    message = "Placement saved."
                else:
                    services.change_salary(
                        actor=request.user, company_id=company_id,
                        employee_id=employee.pk, values=form.service_values(),
                    )
                    message = (
                        "Salary saved. Regenerate the month's salary to apply it."
                        if compensation else
                        "Salary set. Regenerate the month's salary to include them."
                    )
            except (ValidationError, IntegrityError) as exc:
                if isinstance(exc, IntegrityError):
                    form.add_error(None, "That Employee ID is already in use for those dates.")
                else:
                    apply_service_errors(form, exc)
            else:
                messages.success(request, message)
                return redirect("organization:employee_edit", pk=employee.pk)

        tz = ZoneInfo(company.timezone or "UTC")
        own_shifts = schedule.employee_shift_history(company_id, employee, tz)
        calendar = WorkCalendar(company_id, today, today)
        own = calendar.employee_shift(employee.pk, today)
        department_id = assignment.department_id if assignment else None
        works = calendar.shift_for(department_id, today, employee_id=employee.pk)
        if own is not None:
            works_from = "their own shift"
        elif calendar.by_department and calendar.shift_for(department_id, today) != calendar.shift:
            works_from = "the department's shift"
        else:
            works_from = "the company shift"

        return render(request, "organization/employee_edit.html", {
            "employee": employee,
            "assignment": assignment,
            "compensation": compensation,
            "placement_since": _local_date(assignment.effective_from if assignment else None, company),
            "salary_since": _local_date(compensation.effective_from if compensation else None, company),
            "details": details,
            "placement": placement,
            "salary": salary,
            "shift_form": shift_form,
            "end_form": end_form,
            "works_shift": works,
            "works_from": works_from,
            "own_shifts": own_shifts,
            # The one "End" acts on: the own shift in force today, else the next one.
            "endable": next(
                (row for row in reversed(own_shifts)
                 if row.last_day is None or row.last_day >= today),
                None,
            ),
            "today": today,
            "may": may,
            "login": login,
            "login_role_label": employee_login.ROLE_LABELS.get(login.role, "") if login else "",
            "give_login": give_login,
            "login_role": login_role,
            "login_password": login_password,
        })
