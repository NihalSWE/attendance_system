"""Edit employee: details, placement and salary, each saved on its own."""

from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from common.choices import ActiveStatus
from common.forms import apply_service_errors
from common.tenant import use_company
from organization import employee_edit_services as services
from organization.employee_edit_forms import EmployeeDetailsForm, PlacementForm, SalaryForm
from organization.services import visible_branches
from organization.views import _company_or_redirect
from scheduling import services as schedule
from scheduling.calendar import WorkCalendar
from scheduling.forms import EmployeeShiftForm, EndEmployeeShiftForm
from scheduling.models import Shift


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
        actor=request.user, company_id=company_id, employee_id=pk
    )
    company = membership.company
    section = request.POST.get("section", "") if request.method == "POST" else ""
    today = timezone.localdate()

    with use_company(company_id):
        branches = visible_branches(membership).filter(status=ActiveStatus.ACTIVE)

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
        salary = SalaryForm(
            request.POST if section == "salary" else None,
            company=company,
            initial={
                "pay_basis": compensation.pay_basis if compensation else "monthly",
                "base_rate": compensation.base_rate if compensation else None,
                "salary_from": today,
            },
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
                    message = "Salary saved. Regenerate the month's salary to apply it."
            except (ValidationError, IntegrityError) as exc:
                if isinstance(exc, IntegrityError):
                    form.add_error(None, "That employee code is already in use for those dates.")
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
        })
