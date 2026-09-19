"""Company pages for adopting catalogue departments into branches.

Thin adapters. Every authorization decision and every write happens in
``organization.adoption_services``, so a future API or background job enforces
the same rules without re-implementing them.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import redirect
from base_template.tables import paginate, render
from django.views.decorators.http import require_http_methods

from common.choices import ActiveStatus
from common.tenant import use_company
from organization.adoption_forms import (
    AdoptionStatusForm,
    CopyAdoptionsForm,
    DepartmentAdoptionForm,
    DesignationForm,
)
from organization.adoption_services import (
    adopt_department,
    copy_adoptions_between_branches,
    create_designation,
    get_adoption_for_edit,
    get_designation_for_edit,
    set_adoption_status,
    set_designation_status,
    update_adoption,
    update_designation,
    visible_adoptions,
    visible_designations,
)
from organization.services import (
    require_company_membership,
    require_structure_manager,
    visible_branches,
)
from organization.views import _company_or_redirect


def _employee_choices(company_id):
    """Employees who could head a department, for the optional head field."""
    from employees.models import Employee

    with use_company(company_id):
        return Employee.objects.order_by("first_name", "last_name")


@login_required
@require_http_methods(["GET"])
def adoption_list(request):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail

    membership = require_company_membership(request.user, company_id)

    with use_company(company_id):
        queryset = visible_adoptions(membership).annotate(
            title_count=Count("designations", distinct=True),
            employee_count=Count("assignments", distinct=True),
        )
        total = queryset.count()

        query = request.GET.get("q", "").strip()[:200]
        if query:
            queryset = queryset.filter(
                Q(name__icontains=query)
                | Q(code__icontains=query)
                | Q(branch__name__icontains=query)
            )

        status = request.GET.get("status", "").strip()
        if status in dict(ActiveStatus.choices):
            queryset = queryset.filter(status=status)

        filtered_total = queryset.count()
        page = paginate(request, queryset.order_by("branch__name", "name"),
            search=("branch__name", "name", "code", "head__first_name", "head__last_name", "status"),
            order=("branch__name", "name", "title_count", ("head__first_name", "head__last_name"), "status", "employee_count", None))

        return render(request, "organization/adoption_list.html", {
            "page": page,
            "query": query,
            "status": status,
            "statuses": ActiveStatus.choices,
            "total": total,
            "filtered_total": filtered_total,
            "can_manage": membership.role in ("owner", "company_admin"),
        })


@login_required
@require_http_methods(["GET", "POST"])
def adoption_create(request):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail

    membership = require_structure_manager(request.user, company_id)

    with use_company(company_id):
        branches = visible_branches(membership).filter(status=ActiveStatus.ACTIVE)
        employees = _employee_choices(company_id)

        if request.method == "POST":
            form = DepartmentAdoptionForm(
                request.POST, branches=branches, employees=employees
            )
            if form.is_valid():
                try:
                    adoption = adopt_department(
                        actor=request.user,
                        company_id=company_id,
                        values=form.cleaned_data,
                    )
                except ValidationError as exc:
                    _apply_errors(form, exc)
                else:
                    messages.success(
                        request,
                        f"{adoption.name} added to {adoption.branch.name}.",
                    )
                    return redirect("organization:adoption_list")
        else:
            form = DepartmentAdoptionForm(
                branches=branches,
                employees=employees,
                initial={"status": ActiveStatus.ACTIVE},
            )

        return render(request, "organization/adoption_form.html", {
            "form": form,
            "title": "Add a department",
            "submit_label": "Add department",
            "explanation": (
                "Choose a department and the branch that uses it, then assign "
                "the designations that branch needs. Any designation may go "
                "under any of your departments -- that pairing is yours to "
                "make. The department name is shared across the platform, so "
                "it stays consistent everywhere."
            ),
            "adoption": None,
        })


@login_required
@require_http_methods(["GET", "POST"])
def adoption_edit(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail

    membership, adoption = get_adoption_for_edit(
        actor=request.user, company_id=company_id, adoption_id=pk
    )

    with use_company(company_id):
        branches = visible_branches(membership)
        employees = _employee_choices(company_id)

        if request.method == "POST":
            form = DepartmentAdoptionForm(
                request.POST, instance=adoption, branches=branches, employees=employees
            )
            if form.is_valid():
                try:
                    update_adoption(
                        actor=request.user,
                        company_id=company_id,
                        adoption_id=adoption.pk,
                        values=form.cleaned_data,
                    )
                except ValidationError as exc:
                    _apply_errors(form, exc)
                else:
                    messages.success(request, "Department updated.")
                    return redirect("organization:adoption_list")
        else:
            form = DepartmentAdoptionForm(
                instance=adoption, branches=branches, employees=employees
            )

        return render(request, "organization/adoption_form.html", {
            "form": form,
            "title": f"Edit {adoption.name}",
            "submit_label": "Save department",
            "explanation": (
                "Edit the department here. Add or remove its designations (job "
                "titles) below; a title employees currently hold cannot be "
                "removed until they are moved."
            ),
            "adoption": adoption,
        })


@login_required
@require_http_methods(["GET", "POST"])
def adoption_status(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail

    _membership, adoption = get_adoption_for_edit(
        actor=request.user, company_id=company_id, adoption_id=pk
    )

    if request.method == "POST":
        form = AdoptionStatusForm(request.POST)
        if form.is_valid():
            try:
                set_adoption_status(
                    actor=request.user,
                    company_id=company_id,
                    adoption_id=adoption.pk,
                    status=form.cleaned_data["status"],
                )
            except ValidationError as exc:
                _apply_errors(form, exc)
            else:
                messages.success(request, "Status updated.")
                return redirect("organization:adoption_list")
    else:
        form = AdoptionStatusForm(initial={"status": adoption.status})

    return render(request, "organization/adoption_status_form.html", {
        "form": form,
        "title": f"Change status of {adoption.name}",
        "submit_label": "Save status",
        "adoption": adoption,
    })


def _apply_errors(form, exc):
    """Map a service ValidationError back onto the offending form field."""
    if hasattr(exc, "message_dict"):
        for field, errors in exc.message_dict.items():
            form.add_error(field if field in form.fields else None, errors)
    else:
        form.add_error(None, exc)


@login_required
@require_http_methods(["GET", "POST"])
def adoption_copy(request):
    """Copy one branch's departments and designations into another branch."""
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail

    membership = require_structure_manager(request.user, company_id)

    with use_company(company_id):
        branches = visible_branches(membership).filter(status=ActiveStatus.ACTIVE)

        if request.method == "POST":
            form = CopyAdoptionsForm(request.POST, branches=branches)
            if form.is_valid():
                try:
                    created, skipped = copy_adoptions_between_branches(
                        actor=request.user,
                        company_id=company_id,
                        source_branch=form.cleaned_data["source_branch"],
                        target_branch=form.cleaned_data["target_branch"],
                    )
                except ValidationError as exc:
                    _apply_errors(form, exc)
                else:
                    if created:
                        messages.success(
                            request,
                            f"Copied {len(created)} department"
                            f"{'' if len(created) == 1 else 's'} into "
                            f"{form.cleaned_data['target_branch'].name}. "
                            "Appoint a head for each one when you are ready — "
                            "heads are not copied.",
                        )
                    if skipped:
                        messages.info(
                            request,
                            f"Already present, so left alone: {', '.join(skipped)}.",
                        )
                    if not created and not skipped:
                        messages.info(
                            request,
                            "That branch has no active departments to copy.",
                        )
                    return redirect("organization:adoption_list")
        else:
            form = CopyAdoptionsForm(branches=branches)

        return render(request, "organization/adoption_copy_form.html", {
            "form": form,
            "title": "Copy departments to another branch",
            "submit_label": "Copy departments",
        })


def _designation_form_querysets(membership, company_id):
    with use_company(company_id):
        departments = (
            visible_adoptions(membership)
            .filter(status=ActiveStatus.ACTIVE)
            .order_by("branch__name", "name")
        )
        designations = visible_designations(membership).order_by("name")
    return departments, designations


@login_required
@require_http_methods(["GET"])
def designation_list(request):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    membership = require_company_membership(request.user, company_id)
    with use_company(company_id):
        queryset = visible_designations(membership).annotate(
            employee_count=Count("assignments", distinct=True),
        )
        status = request.GET.get("status", "").strip()
        if status in dict(ActiveStatus.choices):
            queryset = queryset.filter(status=status)
        page = paginate(
            request,
            queryset.order_by("department__branch__name", "department__name", "name"),
            search=("name", "code", "department__name", "department__branch__name", "status"),
            order=("name", "code", "department__name", "department__branch__name",
                   "status", "employee_count", None),
        )
        return render(request, "organization/designation_list.html", {
            "page": page,
            "status": status,
            "statuses": ActiveStatus.choices,
            "can_manage": membership.role in ("owner", "company_admin"),
        })


@login_required
@require_http_methods(["GET", "POST"])
def designation_create(request):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    membership = require_structure_manager(request.user, company_id)
    departments, designations = _designation_form_querysets(membership, company_id)
    if request.method == "POST":
        form = DesignationForm(request.POST, departments=departments, designations=designations)
        if form.is_valid():
            try:
                designation = create_designation(
                    actor=request.user, company_id=company_id, values=form.cleaned_data
                )
            except ValidationError as exc:
                _apply_errors(form, exc)
            else:
                messages.success(request, f"{designation.name} added.")
                return redirect("organization:designation_list")
    else:
        form = DesignationForm(
            departments=departments, designations=designations,
            initial={"status": ActiveStatus.ACTIVE},
        )
    return render(request, "organization/designation_form.html", {
        "form": form, "title": "Add a designation", "submit_label": "Add designation",
    })


@login_required
@require_http_methods(["GET", "POST"])
def designation_edit(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    membership, designation = get_designation_for_edit(
        actor=request.user, company_id=company_id, designation_id=pk
    )
    departments, designations = _designation_form_querysets(membership, company_id)
    if request.method == "POST":
        form = DesignationForm(
            request.POST, instance=designation, departments=departments, designations=designations
        )
        if form.is_valid():
            try:
                update_designation(
                    actor=request.user, company_id=company_id,
                    designation_id=designation.pk, values=form.cleaned_data,
                )
            except ValidationError as exc:
                _apply_errors(form, exc)
            else:
                messages.success(request, "Designation updated.")
                return redirect("organization:designation_list")
    else:
        form = DesignationForm(
            instance=designation, departments=departments, designations=designations
        )
    return render(request, "organization/designation_form.html", {
        "form": form, "title": f"Edit {designation.name}", "submit_label": "Save designation",
    })


@login_required
@require_http_methods(["POST"])
def designation_status(request, pk):
    """Activate or deactivate one designation."""
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    try:
        set_designation_status(
            actor=request.user, company_id=company_id, designation_id=pk,
            status=request.POST.get("status", ""),
        )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, "Designation updated.")
    return redirect("organization:designation_list")
