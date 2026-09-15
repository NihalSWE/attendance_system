"""Organisation → Access (A12 part 2): the list of people, and one person's access."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from access_control import branch_access as access
from base_template.tables import paginate, render
from common.tenant import use_company
from organization import access_services as services
from organization import employee_login
from organization.employee_edit_forms import GiveLoginForm
from organization.employee_edit_views import _login_errors
from organization.views import _company_or_redirect


@login_required
@require_http_methods(["GET"])
def access_list(request):
    """People in the branches where you may give access, with what they hold."""
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    branches = services.grant_branches(request.user, company_id)
    branch_id = request.GET.get("branch", "").strip()
    with use_company(company_id):
        choices = services.branch_choices(company_id, branches)
        queryset = services.people(branches)
        if branch_id.isdigit() and any(b.pk == int(branch_id) for b in choices):
            queryset = queryset.filter(table_branch_id=int(branch_id))
        page = paginate(
            request,
            queryset.order_by("first_name", "last_name"),
            search=("first_name", "last_name", "table_code", "table_branch"),
            order=("table_code", ("first_name", "last_name"), "table_branch", None, None, None),
        )
        rows = list(page.object_list)
        grants = {employee.pk: services.granted(employee) for employee in rows}
    names = {b.pk: b.name for b in choices}
    for employee in rows:
        membership = employee_login.login_for(company_id, employee)
        employee.login_label = (
            services.LOGIN_LABELS.get(membership.role, membership.get_role_display())
            if membership else ""
        )
        employee.login_disabled = bool(membership and membership.status != "active")
        employee.role_note = services.role_note(company_id, membership)
        held = grants[employee.pk]
        employee.access_summary = [
            f"{access.LABELS[code]} — "
            + ", ".join(sorted(names.get(b, "another branch") for b in branch_ids))
            for code, branch_ids in sorted(held.items())
        ]
    return render(request, "organization/access_list.html", {
        "page": page,
        "rows": rows,
        "branches": choices,
        "branch_id": branch_id,
    })


@login_required
@require_http_methods(["GET", "POST"])
def access_person(request, employee_id):
    """Tick the permissions one person has in each branch; give them a login."""
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    page = services.person(request.user, company_id, employee_id)
    section = request.POST.get("section", "") if request.method == "POST" else ""
    here = reverse("organization:access_person", args=[employee_id])

    login_form = GiveLoginForm(
        request.POST if section == "login" else None
    ) if page["can_give_login"] else None
    if login_form is not None:
        # Branch managers create Employee logins only (making someone a branch
        # manager stays with the owner and company admin).
        login_form.fields.pop("login_role", None)
        login_form.fields.pop("login_branches", None)

    if section == "access":
        reason = (request.POST.get("reason") or "").strip()[:255]
        try:
            added, removed = services.save_person(
                request.user, company_id, employee_id, services.parse_ticked(request.POST), reason
            )
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            if added or removed:
                messages.success(
                    request,
                    f"Access saved for {page['employee'].full_name}: "
                    f"{len(added)} given, {len(removed)} removed.",
                )
            else:
                messages.info(request, "Nothing changed.")
            return redirect(here)
    elif section == "login" and login_form is not None and login_form.is_valid():
        data = login_form.cleaned_data
        try:
            employee_login.give_login(
                actor=request.user, company_id=company_id, employee_id=employee_id,
                values={
                    "email": data["login_email"], "password": data["login_password"],
                    "password_confirm": data["login_password_confirm"],
                    "role": "employee", "branches": [],
                },
            )
        except ValidationError as exc:
            _login_errors(login_form, exc)
        else:
            messages.success(
                request,
                f"{page['employee'].full_name} can now sign in with {data['login_email']}.",
            )
            return redirect(here)

    return render(request, "organization/access_person.html", {
        **page,
        "login_form": login_form,
        "back_url": reverse("organization:access"),
    })
