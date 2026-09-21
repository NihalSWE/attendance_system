"""Creating an employee and placing them in a branch, department and designation.

A thin adapter. The domain write is ``employees.services.create_employee``,
which already creates the employee, their assignment and their compensation in
one transaction and is covered by its own tests. Nothing is reimplemented
here; this view authorizes the actor, narrows the choices, and translates the
database's own constraint failures into readable field errors.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from common.choices import ActiveStatus
from common.tenant import use_company
from employees.services import create_employee
from organization.employee_forms import EmployeeCreateForm
from organization.models import Branch, Department, Designation
from access_control.branch_access import ALL_BRANCHES, branches_for, can
from organization.access_services import people
from organization.services import require_company_membership, visible_branches
from organization.views import _company_or_redirect


def _creator(user, company_id):
    """The actor's membership and where they may add people (A12 part 4).

    Owner/company admin: every branch (as before). Otherwise the branches where
    they hold ``employees.edit``; nowhere means no access.
    """
    membership = require_company_membership(user, company_id)
    branch_ids = branches_for(user, company_id, "employees.edit")
    if not branch_ids:
        raise PermissionDenied("Adding employees requires owner, company administrator or branch access.")
    return membership, branch_ids


@login_required
@require_http_methods(["GET", "POST"])
def employee_create(request):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail

    membership, branch_ids = _creator(request.user, company_id)

    with use_company(company_id):
        from employees.models import Employee

        branches = visible_branches(membership).filter(status=ActiveStatus.ACTIVE)
        employees = Employee.objects.order_by("first_name", "last_name")
        if branch_ids is not ALL_BRANCHES:
            # A12 part 4: only the branches where they may add people, and a
            # reporting manager from those branches.
            branches = branches.filter(pk__in=branch_ids)
            employees = people(branch_ids).order_by("first_name", "last_name")

        if request.method == "POST":
            form = EmployeeCreateForm(
                request.POST,
                company=membership.company,
                branches=branches,
                employees=employees,
            )
            if form.is_valid():
                data = form.cleaned_data
                if branch_ids is not ALL_BRANCHES and data["branch"].pk not in branch_ids:
                    raise PermissionDenied("You can only add people to branches you look after.")
                if not can(request.user, company_id, "salary.prepare", data["branch"].pk):
                    # A new employee comes with their pay, which follows
                    # "prepare salary" in that branch (a branch manager has it).
                    raise PermissionDenied(
                        "Adding someone sets their pay, which needs salary access for that branch."
                    )
                try:
                    result = create_employee(
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
                        f"{employee.full_name} created as "
                        f"{data['designation'].name} in {data['department'].name}.",
                    )
                    return redirect("employee_list")
        else:
            form = EmployeeCreateForm(
                company=membership.company,
                branches=branches,
                employees=employees,
                initial={"pay_basis": "monthly"},
            )

        return render(request, "organization/employee_form.html", {
            "form": form,
            "title": "Create employee",
            "submit_label": "Create employee",
            "has_departments": Department.objects.filter(
                status=ActiveStatus.ACTIVE
            ).exists(),
        })


@login_required
@require_http_methods(["GET"])
def branch_departments(request):
    """Departments added to a branch, for the dependent select."""
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    _, branch_ids = _creator(request.user, company_id)

    branch = request.GET.get("branch", "").strip()
    if not branch.isdigit() or int(branch) not in branch_ids:
        return JsonResponse({"results": []})

    with use_company(company_id):
        rows = (
            Department.objects.filter(
                branch_id=int(branch), status=ActiveStatus.ACTIVE
            )
            .order_by("name")
        )
        return JsonResponse({
            "results": [{"id": row.pk, "text": row.name} for row in rows]
        })


@login_required
@require_http_methods(["GET"])
def department_designations(request):
    """Designations assigned to one company department, for the dependent select."""
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    _, branch_ids = _creator(request.user, company_id)

    department = request.GET.get("department", "").strip()
    if not department.isdigit():
        return JsonResponse({"results": []})

    with use_company(company_id):
        branch_of = Department.objects.filter(pk=int(department)).values_list(
            "branch_id", flat=True
        ).first()
        if branch_of is None or branch_of not in branch_ids:
            return JsonResponse({"results": []})
        rows = (
            Designation.objects.filter(
                department_id=int(department), status=ActiveStatus.ACTIVE
            )
            .order_by("name")
        )
        return JsonResponse({
            "results": [{"id": row.pk, "text": row.name} for row in rows]
        })


# The database guards employee-code reuse with an exclusion constraint, so the
# failure arrives named after the constraint rather than in business language.
# Django validates constraints inside full_clean(), which means it can surface
# as either a ValidationError or an IntegrityError depending on the race.
CONSTRAINT_MESSAGES = {
    "excl_employee_code_overlap_per_company": (
        "employee_code",
        "That EMP-ID is already held by someone whose placement has not "
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
