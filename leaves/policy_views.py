"""Leave policies and balances pages (Phase E, 2026-09-27)."""

import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count, Max, Min
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from base_template.tables import Sortable, paginate, paginate_rows, render
from common.choices import ActiveStatus
from common.forms import apply_service_errors
from common.tenant import use_company
from leaves import policies, policy_admin
from leaves.forms import LeavePolicyForm, PolicyRuleForm, PolicyVersionForm
from leaves.models import LeavePolicy, LeavePolicyVersion
from leaves.views import _form_page
from organization.views import _company_or_redirect


def _manager(request):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return None, bail
    policy_admin.require_policy_manager(request.user, company_id)
    return company_id, None


@login_required
@require_http_methods(["GET"])
def policy_list(request):
    company_id, bail = _manager(request)
    if bail:
        return bail
    today = timezone.localdate()
    with use_company(company_id):
        rows = paginate(
            request,
            LeavePolicy.objects.annotate(
                table_versions=Count("versions", distinct=True),
                table_first=Min("versions__effective_from"),
                table_latest=Max("versions__effective_from"),
                table_people=Count("employees__employee", distinct=True),
            ).order_by("-is_default", "status", "name"),
            search=("code", "name", "description", "status"),
            order=("code", "name", "is_default", "table_versions", "table_people", "status", None))
    return render(request, "leaves/policy_list.html", {"policies": rows, "today": today})


@login_required
@require_http_methods(["GET", "POST"])
def policy_create(request):
    company_id, bail = _manager(request)
    if bail:
        return bail
    with use_company(company_id):
        return _form_page(
            request, form=LeavePolicyForm(request.POST or None), title="Add leave policy",
            submit_label="Add policy", redirect_to="leaves:leave_policy_list",
            success="Leave policy added. Give it its rules: New version.",
            explanation=("A policy gives each leave type its days per year, how they are "
                         "earned, what carries forward, and whether half days and hours are "
                         "allowed. Its rules are set in versions, each from a date."),
            action=lambda data: policy_admin.save_policy(
                actor=request.user, company_id=company_id, values=data),
        )


@login_required
@require_http_methods(["GET", "POST"])
def policy_edit(request, pk):
    company_id, bail = _manager(request)
    if bail:
        return bail
    with use_company(company_id):
        policy = get_object_or_404(LeavePolicy, pk=pk)
        form = LeavePolicyForm(request.POST or None, initial={
            "code": policy.code, "name": policy.name, "description": policy.description,
            "is_default": policy.is_default})
        if request.method == "POST" and form.is_valid():
            try:
                policy_admin.save_policy(actor=request.user, company_id=company_id,
                                         policy_id=policy.pk, values=form.cleaned_data)
            except ValidationError as exc:
                apply_service_errors(form, exc)
            else:
                messages.success(request, "Leave policy saved.")
                return redirect("leaves:leave_policy_detail", pk=policy.pk)
        return render(request, "leaves/form.html", {
            "form": form, "title": f"Edit {policy.name}", "submit_label": "Save policy",
            "explanation": "Making it the default gives it, from today, to everyone without "
                           "a policy of their own. What was given before stays."})


@login_required
@require_POST
def policy_status(request, pk):
    company_id, bail = _manager(request)
    if bail:
        return bail
    status = request.POST.get("status", "")
    try:
        policy_admin.set_policy_status(actor=request.user, company_id=company_id,
                                       policy_id=pk, status=status)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, "Leave policy turned on." if status == ActiveStatus.ACTIVE
                         else "Leave policy turned off. Nobody new can be given it.")
    return redirect("leaves:leave_policy_detail", pk=pk)


@login_required
@require_http_methods(["GET"])
def policy_detail(request, pk):
    company_id, bail = _manager(request)
    if bail:
        return bail
    today = timezone.localdate()
    with use_company(company_id):
        policy = get_object_or_404(LeavePolicy, pk=pk)
        versions = list(policy.versions.prefetch_related("rules__leave_type")
                        .order_by("-effective_from"))
        current = next((v for v in versions if v.effective_from <= today), None)
        people = policy.employees.filter(effective_to__isnull=True).count()
    return render(request, "leaves/policy_detail.html", {
        "policy": policy, "versions": versions, "current": current, "today": today,
        "people": people})


def _rule_forms(data, leave_types, initial):
    forms = []
    for leave_type in leave_types:
        start = initial.get(leave_type.pk)
        values = {"include": start is not None, **(start or {})}
        forms.append((leave_type, PolicyRuleForm(data, prefix=f"t{leave_type.pk}",
                                                 initial=values)))
    return forms


def _chosen_rules(rule_forms):
    rules = {}
    for leave_type, form in rule_forms:
        if form.cleaned_data.get("include"):
            rules[leave_type] = {name: form.cleaned_data.get(name)
                                 for name in policy_admin.RULE_FIELDS}
    return rules


def _version_page(request, company_id, policy, version=None):
    today = timezone.localdate()
    leave_types = list(policy_admin.active_leave_types())
    if version is not None:
        initial = {rule.leave_type_id: {name: getattr(rule, name)
                                        for name in policy_admin.RULE_FIELDS}
                   for rule in version.rules.all()}
        start = {"effective_from": version.effective_from, "note": version.note}
    else:
        initial = policy_admin.rules_initial(policy)
        first = not policy.versions.exists()
        start = {"effective_from": datetime.date(today.year, 1, 1) if first
                 else policy_admin.next_year_start(today)}
    data = request.POST if request.method == "POST" else None
    form = PolicyVersionForm(data, initial=start)
    rule_forms = _rule_forms(data, leave_types, initial)
    if request.method == "POST" and form.is_valid() and all(f.is_valid() for _, f in rule_forms):
        try:
            if version is None:
                policy_admin.add_version(
                    actor=request.user, company_id=company_id, policy_id=policy.pk,
                    effective_from=form.cleaned_data["effective_from"],
                    note=form.cleaned_data["note"], rules=_chosen_rules(rule_forms))
            else:
                policy_admin.edit_version(
                    actor=request.user, company_id=company_id, version_id=version.pk,
                    effective_from=form.cleaned_data["effective_from"],
                    note=form.cleaned_data["note"], rules=_chosen_rules(rule_forms))
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, "Version saved. Balances follow it from its date.")
            return redirect("leaves:leave_policy_detail", pk=policy.pk)
    return render(request, "leaves/policy_version_form.html", {
        "policy": policy, "version": version, "form": form, "rule_forms": rule_forms,
        "first": version is None and not policy.versions.exists(), "today": today})


@login_required
@require_http_methods(["GET", "POST"])
def version_create(request, pk):
    company_id, bail = _manager(request)
    if bail:
        return bail
    with use_company(company_id):
        policy = get_object_or_404(LeavePolicy, pk=pk)
        return _version_page(request, company_id, policy)


@login_required
@require_http_methods(["GET", "POST"])
def version_edit(request, pk):
    company_id, bail = _manager(request)
    if bail:
        return bail
    with use_company(company_id):
        version = get_object_or_404(LeavePolicyVersion.objects.select_related("policy"), pk=pk)
        if version.effective_from <= timezone.localdate():
            messages.error(request, "This version has started, so it stays as it is. Add a "
                                    "new version from a later date instead.")
            return redirect("leaves:leave_policy_detail", pk=version.policy_id)
        return _version_page(request, company_id, version.policy, version)


@login_required
@require_POST
def version_delete(request, pk):
    company_id, bail = _manager(request)
    if bail:
        return bail
    with use_company(company_id):
        version = get_object_or_404(LeavePolicyVersion, pk=pk)
        policy_id = version.policy_id
    try:
        policy_admin.delete_version(actor=request.user, company_id=company_id, version_id=pk)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, "Version removed.")
    return redirect("leaves:leave_policy_detail", pk=policy_id)


# --------------------------------------------------------------------------
# Leave balances: everyone the viewer sees leave for
# --------------------------------------------------------------------------


def _allowances(employees, year):
    """``policies.overview`` for a company with no leave policy, for many
    people at once: each leave type's days per year, less what was taken."""
    from django.db.models import Sum

    from leaves.models import LeaveDay, LeaveType

    types = list(LeaveType.objects.filter(status=ActiveStatus.ACTIVE,
                                          days_per_year__isnull=False).order_by("name"))
    used = {}
    for employee_id, type_id, total in (
            LeaveDay.objects.filter(employee__in=employees, work_date__year=year,
                                    status__in=policies.LIVE,
                                    request_segment__leave_type__in=types)
            .values_list("employee_id", "request_segment__leave_type_id")
            .annotate(total=Sum("balance_units"))):
        used[(employee_id, type_id)] = total
    for employee in employees:
        rows = []
        for leave_type in types:
            taken = used.get((employee.pk, leave_type.pk), policies.ZERO)
            rows.append({"leave_type": leave_type, "by_policy": False, "policy": None,
                         "given": leave_type.days_per_year, "taken": taken,
                         "left": leave_type.days_per_year - taken})
        yield employee, rows


@login_required
@require_http_methods(["GET"])
def balance_list(request):
    """Each person's balance of each leave type in a year."""
    from leaves.views import _list_scope
    from organization.access_services import people

    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    _company_wide, scope, _record = _list_scope(request.user, company_id)
    today = timezone.localdate()
    raw = request.GET.get("year", "").strip()
    year = int(raw) if raw.isdigit() and 2000 <= int(raw) <= today.year + 1 else today.year
    rows = []
    with use_company(company_id):
        employees = list(people(scope).exclude(
            employment_status__in=["resigned", "terminated", "retired"])
            .order_by("first_name", "last_name"))
        if LeavePolicy.objects.exists():
            overviews = ((employee, policies.overview(employee, year, today))
                         for employee in employees)
        else:
            # No policy in the company: each type's days per year, in one query.
            overviews = _allowances(employees, year)
        for employee, found in overviews:
            for row in found:
                rows.append([
                    employee.table_code or "", employee.full_name, employee.table_branch or "",
                    row["leave_type"].name,
                    (row["policy"].name if row["policy"] else "Policy") if row["by_policy"]
                    else "Days per year",
                    Sortable(f"{row['given'].normalize():f}", row["given"]),
                    Sortable(f"{row['taken'].normalize():f}", row["taken"]),
                    Sortable(f"{row['left'].normalize():f}", row["left"]),
                    employee.pk,
                ])
    page = paginate_rows(request, rows, columns=8)
    return render(request, "leaves/balance_list.html", {
        "page": page, "year": year, "years": range(today.year + 1, today.year - 4, -1)})
