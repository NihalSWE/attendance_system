"""Hiring an employee into a branch, department and job title.

A thin adapter. The domain write is ``employees.services.hire_employee``,
which already creates the employee, their assignment and their compensation in
one transaction and is covered by its own tests. Nothing is reimplemented
here; this view authorizes the actor, narrows the choices, and translates the
database's own constraint failures into readable field errors.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from common.choices import ActiveStatus
from common.tenant import use_company
from employees.services import hire_employee
from organization.hire_forms import HireEmployeeForm
from organization.models import Branch, CompanyDepartment, CompanyDesignation
from organization.services import require_structure_manager, visible_branches
from organization.views import _company_or_redirect


@login_required
@require_http_methods(["GET", "POST"])
def hire(request):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail

    membership = require_structure_manager(request.user, company_id)

    with use_company(company_id):
        from employees.models import Employee

        branches = visible_branches(membership).filter(status=ActiveStatus.ACTIVE)
        employees = Employee.objects.order_by("first_name", "last_name")

        if request.method == "POST":
            form = HireEmployeeForm(
                request.POST,
                company=membership.company,
                branches=branches,
                employees=employees,
            )
            if form.is_valid():
                data = form.cleaned_data
                try:
                    result = hire_employee(
                        company=membership.company,
                        first_name=data["first_name"],
                        last_name=data["last_name"],
                        employee_code=data["employee_code"],
                        branch=data["branch"],
                        department=data["department"],
                        designation=data["designation"],
                        manager=data.get("manager"),
                        effective_from=data["effective_from"],
                        pay_basis=data["pay_basis"],
                        base_rate=data["base_rate"],
                        joining_date=data["effective_from"],
                        created_by=request.user,
                    )
                except (ValidationError, IntegrityError) as exc:
                    _apply_errors(form, exc)
                else:
                    employee = result["employee"]
                    messages.success(
                        request,
                        f"{employee.full_name} hired as "
                        f"{data['designation'].name} in {data['department'].name}.",
                    )
                    return redirect("employee_list")
        else:
            form = HireEmployeeForm(
                company=membership.company,
                branches=branches,
                employees=employees,
                initial={"pay_basis": "monthly"},
            )

        return render(request, "organization/hire_form.html", {
            "form": form,
            "title": "Hire an employee",
            "submit_label": "Hire employee",
            "has_departments": CompanyDepartment.objects.filter(
                status=ActiveStatus.ACTIVE
            ).exists(),
        })


@login_required
@require_http_methods(["GET"])
def branch_departments(request):
    """Departments a branch has adopted, for the dependent select."""
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    require_structure_manager(request.user, company_id)

    branch = request.GET.get("branch", "").strip()
    if not branch.isdigit():
        return JsonResponse({"results": []})

    with use_company(company_id):
        rows = (
            CompanyDepartment.objects.filter(
                branch_id=int(branch), status=ActiveStatus.ACTIVE
            )
            .select_related("department")
            .order_by("department__name")
        )
        return JsonResponse({
            "results": [{"id": row.pk, "text": row.department.name} for row in rows]
        })


@login_required
@require_http_methods(["GET"])
def department_job_titles(request):
    """Job titles adopted into one company department, for the dependent select."""
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    require_structure_manager(request.user, company_id)

    department = request.GET.get("department", "").strip()
    if not department.isdigit():
        return JsonResponse({"results": []})

    with use_company(company_id):
        rows = (
            CompanyDesignation.objects.filter(
                company_department_id=int(department), status=ActiveStatus.ACTIVE
            )
            .select_related("designation")
            .order_by("designation__name")
        )
        return JsonResponse({
            "results": [{"id": row.pk, "text": row.designation.name} for row in rows]
        })


# The database guards employee-code reuse with an exclusion constraint, so the
# failure arrives named after the constraint rather than in business language.
# Django validates constraints inside full_clean(), which means it can surface
# as either a ValidationError or an IntegrityError depending on the race.
CONSTRAINT_MESSAGES = {
    "excl_employee_code_overlap_per_company": (
        "employee_code",
        "That employee code is already held by someone whose placement has not "
        "ended. Codes can be reused once the previous placement closes — use a "
        "different code, or end the earlier one first.",
    ),
    "excl_assignment_overlap_per_employee": (
        "employee_code",
        "This person already has an overlapping placement. End the current one "
        "before starting another.",
    ),
}


def _apply_errors(form, exc):
    """Map a service failure onto the field the reader needs to change."""
    text = str(getattr(exc, "message_dict", "")) or str(exc)
    for name, (field, message) in CONSTRAINT_MESSAGES.items():
        if name in text:
            form.add_error(field, message)
            return

    if hasattr(exc, "message_dict"):
        for field, errors in exc.message_dict.items():
            form.add_error(field if field in form.fields else None, errors)
    else:
        form.add_error(None, exc)
