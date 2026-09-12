"""Root-only screens for the platform department and designation lists.

HTTP adapters only: every mutation delegates to
``organization.catalogue_services``, which re-checks authorization and
re-validates the values. A form here is a convenience, never the boundary.

These live under ``/platform/`` beside the company screens because the rows
belong to the platform operator, not to any tenant. Nothing in this module
opens a tenant context — root must stay off the tenant query path
(docs/DATABASE_SCHEMA.md §14).
"""

from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from common.choices import ActiveStatus
from organization import catalogue_services as services
from organization.forms import (
    CatalogueDepartmentForm,
    CatalogueDesignationForm,
    CatalogueStatusForm,
)
from organization.models import Department, Designation


def platform_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        services.require_platform_owner(request.user)
        return view(request, *args, **kwargs)

    return wrapped


def _form_page(request, *, form, title, submit_label, action, redirect_to,
               explanation="", usage=None, usage_label="",
               usage_shows_department=False):
    """Shared create/edit rendering.

    A ValidationError from the service is mapped back onto the offending
    field, so a uniqueness collision reads as a field error under the input
    rather than a 500 page.
    """
    if request.method == "POST" and form.is_valid():
        try:
            action(form.cleaned_data)
        except ValidationError as exc:
            if hasattr(exc, "message_dict"):
                for field, errors in exc.message_dict.items():
                    form.add_error(field if field in form.fields else None, errors)
            else:
                form.add_error(None, exc)
        except IntegrityError:
            # The database constraint is the real guard; a racing duplicate
            # lands here rather than in clean().
            form.add_error(
                None,
                "That code or name is already used by another entry.",
            )
        else:
            messages.success(request, "Changes saved.")
            return redirect(redirect_to)

    return render(request, "organization/platform/catalogue_form.html", {
        "form": form,
        "title": title,
        "submit_label": submit_label,
        "explanation": explanation,
        "cancel_url": redirect_to,
        "usage": usage,
        "usage_label": usage_label,
        # Only the designation screens need it: a company may use one
        # designation under several of its departments.
        "usage_shows_department": usage_shows_department,
    })


# --- departments ---------------------------------------------------------


@platform_required
@require_http_methods(["GET"])
def department_list(request):
    queryset = services.department_queryset()
    total = queryset.count()

    query = request.GET.get("q", "").strip()[:200]
    if query:
        queryset = queryset.filter(Q(name__icontains=query) | Q(code__icontains=query))

    status = request.GET.get("status", "").strip()
    if status in dict(ActiveStatus.choices):
        queryset = queryset.filter(status=status)

    page = Paginator(queryset.order_by("name"), 25).get_page(request.GET.get("page"))
    return render(request, "organization/platform/department_list.html", {
        "page": page,
        "query": query,
        "status": status,
        "statuses": ActiveStatus.choices,
        "total": total,
        "filtered_total": queryset.count(),
    })


@platform_required
@require_http_methods(["GET", "POST"])
def department_create(request):
    return _form_page(
        request,
        form=CatalogueDepartmentForm(request.POST or None),
        title="Add department",
        submit_label="Add department",
        explanation=(
            "This is the platform-wide list. Every company chooses from these "
            "names, so keep them canonical — a company adds a department to one "
            "of its branches rather than creating its own spelling."
        ),
        redirect_to="catalogue:department_list",
        action=lambda data: services.create_department(actor=request.user, values=data),
    )


@platform_required
@require_http_methods(["GET", "POST"])
def department_edit(request, pk):
    department = get_object_or_404(Department, pk=pk)
    usage = services.department_usage(department)
    return _form_page(
        request,
        form=CatalogueDepartmentForm(request.POST or None, instance=department),
        title=f"Edit {department.name}",
        submit_label="Save department",
        explanation=(
            "Renaming changes what every company sees, because a company "
            "reads the name through to this row rather than copying it."
        ),
        redirect_to="catalogue:department_list",
        usage=usage,
        usage_label="Companies using this department",
        action=lambda data: services.update_department(
            actor=request.user, department_id=department.pk, values=data
        ),
    )


@platform_required
@require_http_methods(["GET", "POST"])
def department_status(request, pk):
    department = get_object_or_404(Department, pk=pk)
    return _form_page(
        request,
        form=CatalogueStatusForm(
            request.POST or None, initial={"status": department.status}
        ),
        title=f"Change status of {department.name}",
        submit_label="Save status",
        explanation=(
            "Entries are never deleted. Deactivating hides this "
            "department from being added; companies already using it keep "
            "working, and their history stays readable."
        ),
        redirect_to="catalogue:department_list",
        usage=services.department_usage(department),
        usage_label="Companies using this department",
        action=lambda data: services.set_department_status(
            actor=request.user, department_id=department.pk, status=data["status"]
        ),
    )


# --- designations --------------------------------------------------------


@platform_required
@require_http_methods(["GET"])
def designation_list(request):
    queryset = services.designation_queryset()
    total = queryset.count()

    query = request.GET.get("q", "").strip()[:200]
    if query:
        queryset = queryset.filter(
            Q(name__icontains=query) | Q(code__icontains=query)
        )

    status = request.GET.get("status", "").strip()
    if status in dict(ActiveStatus.choices):
        queryset = queryset.filter(status=status)

    page = Paginator(queryset.order_by("name"), 25).get_page(request.GET.get("page"))
    return render(request, "organization/platform/designation_list.html", {
        "page": page,
        "query": query,
        "status": status,
        "statuses": ActiveStatus.choices,
        "total": total,
        "filtered_total": queryset.count(),
    })


@platform_required
@require_http_methods(["GET", "POST"])
def designation_create(request):
    return _form_page(
        request,
        form=CatalogueDesignationForm(request.POST or None),
        title="Add designation",
        submit_label="Add designation",
        explanation=(
            "One flat list for the whole platform. Create \"Manager\" once; each "
            "company then decides which of its own departments use it, so one "
            "company can place it under Sales and another under Production."
        ),
        redirect_to="catalogue:designation_list",
        action=lambda data: services.create_designation(actor=request.user, values=data),
    )


@platform_required
@require_http_methods(["GET", "POST"])
def designation_edit(request, pk):
    designation = get_object_or_404(Designation, pk=pk)
    return _form_page(
        request,
        form=CatalogueDesignationForm(request.POST or None, instance=designation),
        title=f"Edit {designation.name}",
        submit_label="Save designation",
        explanation=(
            "Renaming changes what every company sees, because a company "
            "assignment reads the name through to this row. Which departments "
            "use it is each company's own choice and is unaffected."
        ),
        redirect_to="catalogue:designation_list",
        usage=services.designation_usage(designation),
        usage_label="Companies using this designation",
        usage_shows_department=True,
        action=lambda data: services.update_designation(
            actor=request.user, designation_id=designation.pk, values=data
        ),
    )


@platform_required
@require_http_methods(["GET", "POST"])
def designation_status(request, pk):
    designation = get_object_or_404(Designation, pk=pk)
    return _form_page(
        request,
        form=CatalogueStatusForm(
            request.POST or None, initial={"status": designation.status}
        ),
        title=f"Change status of {designation.name}",
        submit_label="Save status",
        explanation=(
            "Entries are never deleted. Deactivating hides this designation "
            "from new assignments; employees who already hold it keep it."
        ),
        redirect_to="catalogue:designation_list",
        usage=services.designation_usage(designation),
        usage_label="Companies using this designation",
        usage_shows_department=True,
        action=lambda data: services.set_designation_status(
            actor=request.user, designation_id=designation.pk, status=data["status"]
        ),
    )
