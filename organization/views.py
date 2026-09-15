"""Company organization pages: branches (departments/designations follow).

Views are thin adapters. Every authorization decision and every write happens in
organization.services, so a future API or job enforces the same rules.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.shortcuts import redirect
from base_template.tables import paginate, render
from django.views.decorators.http import require_POST

from common.choices import ActiveStatus
from common.tenant import use_company
from organization.adoption_services import provision_new_branch
from organization.forms import BranchForm, BranchStatusForm
from organization.models import Branch
from organization.services import (
    create_branch,
    get_branch_for_edit,
    require_company_membership,
    require_structure_manager,
    set_branch_status,
    update_branch,
    visible_branches,
)


def _company_or_redirect(request):
    """Root has no company context; company users need an active membership."""
    if request.user.is_superuser:
        return None, redirect("platform:company_list")
    if not request.company_id:
        return None, render(request, "base_template/no_company.html")
    return request.company_id, None


@login_required
def branch_list(request):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail

    membership = require_company_membership(request.user, company_id)

    with use_company(company_id):
        queryset = (
            visible_branches(membership)
            # Departments hang off the branch through the adoption row now;
            # the old `departments` accessor belonged to the tenant-owned
            # Department that the root catalogue replaced.
            .annotate(department_count=Count("company_departments", distinct=True))
            .order_by("-is_default", "name")
        )

        search = request.GET.get("q", "").strip()
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search)
                | Q(code__icontains=search)
                | Q(city__icontains=search)
            )
        status = request.GET.get("status", "").strip()
        if status in dict(ActiveStatus.choices):
            queryset = queryset.filter(status=status)

        page = paginate(request, queryset, search=("code", "name", "city", "status"),
            order=("code", "name", "city", "is_default", "status", "department_count", None))
        paginator, per_page = page.paginator, page.paginator.per_page
        branches = list(page.object_list)

    can_manage = membership.role in ("owner", "company_admin")
    return render(request, "organization/branch_list.html", {
        "branches": branches,
        "page": page,
        "paginator": paginator,
        "search": search,
        "status": status,
        "per_page": per_page,
        "statuses": ActiveStatus.choices,
        "can_manage": can_manage,
        "restricted_scope": membership.allowed_branches.exists(),
    })


@login_required
def branch_create(request):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail

    membership = require_structure_manager(request.user, company_id)

    if request.method == "POST":
        form = BranchForm(request.POST, company=membership.company)
        if form.is_valid():
            try:
                branch = create_branch(
                    actor=request.user,
                    company_id=company_id,
                    values=form.cleaned_data,
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                # Every branch offers the same departments and designations, so a
                # new one is given the company's existing set rather than
                # starting empty and being filled in by hand. The rows are the
                # branch's own — each still carries its own head, status and
                # device rules — so diverging later is a normal edit.
                copied, _skipped = provision_new_branch(
                    actor=request.user, company_id=company_id, branch=branch
                )
                if copied:
                    messages.success(
                        request,
                        f"Branch “{branch.name}” created with "
                        f"{len(copied)} department{'' if len(copied) == 1 else 's'} "
                        "copied from your existing branches. Appoint a head for "
                        "each when you are ready.",
                    )
                else:
                    messages.success(request, f"Branch “{branch.name}” created.")
                return redirect("organization:branch_list")
    else:
        # A company's first branch is created during onboarding, so a new one
        # defaults to non-default and active.
        form = BranchForm(
            company=membership.company,
            initial={"status": ActiveStatus.ACTIVE,
                     "timezone": membership.company.timezone},
        )

    return render(request, "organization/branch_form.html", {
        "form": form,
        "mode": "create",
        "title": "Add branch",
    })


@login_required
def branch_edit(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail

    membership, branch = get_branch_for_edit(
        actor=request.user, company_id=company_id, branch_id=pk
    )

    if request.method == "POST":
        form = BranchForm(request.POST, instance=branch, company=membership.company)
        if form.is_valid():
            try:
                update_branch(
                    actor=request.user,
                    company_id=company_id,
                    branch_id=branch.pk,
                    values=form.cleaned_data,
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, f"Branch “{branch.name}” updated.")
                return redirect("organization:branch_list")
    else:
        form = BranchForm(instance=branch, company=membership.company)

    return render(request, "organization/branch_form.html", {
        "form": form,
        "branch": branch,
        "mode": "edit",
        "title": f"Edit {branch.name}",
        "status_form": BranchStatusForm(initial={"status": branch.status}),
    })


@require_POST
@login_required
def branch_status(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail

    form = BranchStatusForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose a valid status.")
        return redirect("organization:branch_edit", pk=pk)

    try:
        branch = set_branch_status(
            actor=request.user,
            company_id=company_id,
            branch_id=pk,
            status=form.cleaned_data["status"],
            reason=form.cleaned_data.get("reason", ""),
        )
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("organization:branch_edit", pk=pk)

    messages.success(
        request, f"Branch “{branch.name}” is now {branch.get_status_display().lower()}."
    )
    return redirect("organization:branch_list")
