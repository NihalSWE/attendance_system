"""Salary -> Pay groups (2026-10-25): who is paid weekly, on a page of its own.

Each group is a card with the people in it, always listed; every change
opens in a modal, and a refused one opens again with the reason (the
employee profile's pattern, organization/js/employee_profile.js). The start
days are chosen from a list (paygroups.start_days), so a date the service
would refuse cannot be picked.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from base_template.tables import paginate, render
from common.choices import ActiveStatus
from common.forms import apply_service_errors
from common.tenant import use_company
from employees.models import Employee, EmployeeAssignment
from organization.models import Department
from organization.services import require_structure_manager
from organization.views import _company_or_redirect
from payroll import paygroups
from payroll.forms import EndMemberForm, KeepMonthlyForm, PayGroupForm, PayGroupMemberForm
from payroll.models import PayGroup, PayGroupMember

LEFT = (Employee.EmploymentStatus.RESIGNED, Employee.EmploymentStatus.TERMINATED,
        Employee.EmploymentStatus.RETIRED)


@login_required
@require_http_methods(["GET", "POST"])
def pay_groups(request):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    require_structure_manager(request.user, company_id)
    today = timezone.localdate()
    with use_company(company_id):
        groups = list(PayGroup.objects.filter(status=ActiveStatus.ACTIVE).order_by("name"))
        rows = list(
            PayGroupMember.objects.filter(status=PayGroupMember.Status.ACTIVE)
            .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=today))
            .select_related("pay_group", "department", "employee")
            .order_by("department__name", "employee__first_name", "effective_from"))
        departments = Department.objects.order_by("name")
        employees = Employee.objects.exclude(employment_status__in=LEFT).order_by(
            "first_name", "last_name")
        # A person kept monthly is listed under the group their department is in.
        kept_ids = [row.employee_id for row in rows if row.pay_group_id is None]
        department_of = dict(
            EmployeeAssignment.objects.filter(employee_id__in=kept_ids, effective_to__isnull=True)
            .exclude(status=EmployeeAssignment.Status.CANCELLED)
            .values_list("employee_id", "department_id"))
    days = {group.pk: paygroups.start_days(group, company_id, today) for group in groups}
    group_of_department = {row.department_id: row.pay_group_id for row in rows if row.department_id}

    section = request.POST.get("section") if request.method == "POST" else None
    row_id = request.POST.get("row", "")
    bound, open_dialog = {}, ""
    if section:
        result = _save(request, company_id, section, row_id, groups, rows, days,
                       departments, employees)
        if result is True:
            return redirect("payroll:pay_groups")
        if result is not None:
            open_dialog, form = result
            bound[open_dialog] = form

    def owner_of(row):
        """The group whose weeks a row starts and ends on: its own, or (kept
        monthly) the group of the person's department."""
        return row.pay_group_id or group_of_department.get(department_of.get(row.employee_id))

    cards = []
    for group in groups:
        first, last = paygroups.week_of(group, today)
        cards.append({
            "group": group, "this_week": (first, last),
            "departments": sum(1 for r in rows if r.pay_group_id == group.pk and r.department_id),
            "people": sum(1 for r in rows if r.pay_group_id == group.pk and r.employee_id),
            "kept": sum(1 for r in rows if r.pay_group_id is None and owner_of(r) == group.pk),
            "add_dialog": f"add-{group.pk}-dialog", "keep_dialog": f"keep-{group.pk}-dialog",
            "edit_dialog": f"edit-{group.pk}-dialog", "end_dialog": f"end-{group.pk}-dialog",
            "again_dialog": f"again-{group.pk}-dialog",
            "add_form": bound.get(f"add-{group.pk}-dialog") or PayGroupMemberForm(
                departments=departments, employees=employees, days=days[group.pk],
                auto_id=f"add{group.pk}_%s"),
            "keep_form": bound.get(f"keep-{group.pk}-dialog") or KeepMonthlyForm(
                employees=employees, days=days[group.pk], auto_id=f"keep{group.pk}_%s"),
            "edit_form": bound.get(f"edit-{group.pk}-dialog") or PayGroupForm(
                initial={"name": group.name, "week_start": group.week_start},
                auto_id=f"edit{group.pk}_%s"),
            "end_form": bound.get(f"end-{group.pk}-dialog") or EndMemberForm(
                days=days[group.pk], auto_id=f"end{group.pk}_%s"),
            "again_form": bound.get(f"again-{group.pk}-dialog") or EndMemberForm(
                days=days[group.pk], label="Weekly again from", auto_id=f"again{group.pk}_%s"),
        })

    # Everyone in a group, as one server-side table (SL, pages, search, sort).
    shown_group = next((g for g in groups if str(g.pk) == request.GET.get("group", "")), None)
    with use_company(company_id):
        members = (PayGroupMember.objects.filter(status=PayGroupMember.Status.ACTIVE)
                   .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=today))
                   .select_related("pay_group", "department", "employee"))
        if shown_group is not None:
            ids = [r.pk for r in rows if owner_of(r) == shown_group.pk]
            members = members.filter(pk__in=ids)
        table = paginate(
            request, members.order_by("department__name", "employee__first_name"), name="members",
            search=("department__name", "employee__first_name", "employee__last_name",
                    "employee__assignments__employee_code", "pay_group__name"),
            order=(("department__name", "employee__first_name"), "pay_group__name", None,
                   "effective_from", "effective_to", None))
        table.object_list = list(table.object_list)
    names = {g.pk: g for g in groups}
    for row in table.object_list:
        row.owner = names.get(owner_of(row))
        row.dialog = (f"end-{row.owner.pk}-dialog" if row.pay_group_id else
                      f"again-{row.owner.pk}-dialog") if row.owner else ""
    return render(request, "payroll/pay_groups.html", {
        "cards": cards, "members": table, "groups": groups, "shown_group": shown_group,
        "group_form": bound.get("group-add-dialog") or PayGroupForm(auto_id="group_%s"),
        "open_dialog": open_dialog, "bound_row": row_id if open_dialog.startswith(("end-", "again-")) else "",
        "who_of_row": next((_who(r) for r in rows if str(r.pk) == row_id), ""),
    })


def _who(row):
    return row.department.name if row.department_id else row.employee.full_name


def _save(request, company_id, section, row_id, groups, rows, days, departments, employees):
    """Apply one modal's form: True when saved, else ``(dialog id, bound
    form)`` to open again with the reasons; None for an unknown request."""
    post = request.POST
    actor = request.user
    group = next((g for g in groups if str(g.pk) == row_id), None)
    if section == "group_add":
        form = PayGroupForm(post, auto_id="group_%s")
        dialog = "group-add-dialog"
        if form.is_valid():
            try:
                saved = paygroups.create_group(actor=actor, company_id=company_id, **form.cleaned_data)
            except ValidationError as exc:
                return _refused(form, exc, dialog)
            messages.success(request, (
                f"Pay group {saved.name} added: weeks start on {saved.get_week_start_display()}. "
                "Now press Add people on it."))
            return True
        return dialog, form
    if group is None and section in ("group_edit", "member_add", "keep_monthly"):
        return None
    if section == "group_edit":
        form = PayGroupForm(post, auto_id=f"edit{group.pk}_%s")
        dialog = f"edit-{group.pk}-dialog"
        if form.is_valid():
            try:
                saved = paygroups.update_group(actor=actor, company_id=company_id, group=group,
                                               **form.cleaned_data)
            except ValidationError as exc:
                return _refused(form, exc, dialog)
            messages.success(request, f"Saved: {saved.name}, weeks start on "
                                      f"{saved.get_week_start_display()}.")
            return True
        return dialog, form
    if section == "member_add":
        form = PayGroupMemberForm(post, departments=departments, employees=employees,
                                  days=days[group.pk], auto_id=f"add{group.pk}_%s")
        dialog = f"add-{group.pk}-dialog"
        if form.is_valid():
            data = form.cleaned_data
            try:
                paygroups.set_member(actor=actor, company_id=company_id, pay_group=group,
                                     effective_from=data["effective_from"],
                                     department=data["department"], employee=data["employee"])
            except ValidationError as exc:
                return _refused(form, exc, dialog)
            who = data["department"].name if data["department"] else data["employee"].full_name
            messages.success(request, f"{who}: paid weekly in {group.name} from "
                                      f"{data['effective_from']:%a %d %b %Y}.")
            return True
        return dialog, form
    if section == "keep_monthly":
        form = KeepMonthlyForm(post, employees=employees, days=days[group.pk],
                               auto_id=f"keep{group.pk}_%s")
        dialog = f"keep-{group.pk}-dialog"
        if form.is_valid():
            data = form.cleaned_data
            try:
                paygroups.set_member(actor=actor, company_id=company_id, pay_group=None,
                                     effective_from=data["effective_from"], employee=data["employee"])
            except ValidationError as exc:
                return _refused(form, exc, dialog)
            messages.success(request, f"{data['employee'].full_name}: kept on a monthly salary "
                                      f"from {data['effective_from']:%a %d %b %Y}.")
            return True
        return dialog, form
    if section == "member_end":
        row = next((r for r in rows if str(r.pk) == row_id), None)
        if row is None:
            return None
        owner = row.pay_group or _department_group(company_id, row, rows, groups)
        if owner is None:
            return None
        kind = "end" if row.pay_group_id else "again"
        form = EndMemberForm(post, days=days[owner.pk], label=_end_label(row),
                             auto_id=f"{kind}{owner.pk}_%s")
        dialog = f"{kind}-{owner.pk}-dialog"
        if form.is_valid():
            try:
                ended = paygroups.end_member(actor=actor, company_id=company_id, member=row,
                                             effective_to=form.cleaned_data["effective_to"])
            except ValidationError as exc:
                return _refused(form, exc, dialog)
            who = row.department.name if row.department else row.employee.full_name
            if ended.status == PayGroupMember.Status.CANCELLED:
                messages.success(request, f"{who}: removed, as if never saved.")
            else:
                messages.success(request, f"{who}: back to its usual pay from "
                                          f"{form.cleaned_data['effective_to']:%a %d %b %Y}.")
            return True
        return dialog, form
    return None


def _end_label(row):
    return "Monthly again from" if row.pay_group_id else "Weekly again from"


def _department_group(company_id, row, rows, groups):
    """The weekly group a kept-monthly person's department is in."""
    with use_company(company_id):
        department_id = (EmployeeAssignment.objects.filter(
            employee_id=row.employee_id, effective_to__isnull=True)
            .exclude(status=EmployeeAssignment.Status.CANCELLED)
            .values_list("department_id", flat=True).first())
    group_id = next((r.pay_group_id for r in rows if r.department_id == department_id), None)
    return next((g for g in groups if g.pk == group_id), None)


def _refused(form, exc, dialog):
    apply_service_errors(form, exc)
    return dialog, form
