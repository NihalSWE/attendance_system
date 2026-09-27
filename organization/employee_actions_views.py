"""The profile's actions (Ajay, 2026-09-27; the mapping is in
``organization.employee_actions``). Each is a modal: saved, back to the
profile; refused, the profile again with that modal open and its reasons."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from attendance import correction_services
from common.forms import apply_service_errors
from common.tenant import use_company
from employees.models import Employee
from leaves import services as leave_services
from organization import employee_actions as actions
from organization import employee_detail_services
from organization import employee_profile_info as info
from organization.employee_detail_views import _may_record_leave, _profile
from organization.views import _company_or_redirect


def _back(pk, tab="profile"):
    return redirect(reverse("organization:employee_detail", args=[pk]) + f"#{tab}")


@login_required
@require_POST
def employee_leave(request, pk):
    """Apply for leave: Record leave for this person, from their profile."""
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    _m, employee, assignment, _c = employee_detail_services.get_employee_for_edit(
        actor=request.user, company_id=company_id, employee_id=pk, code="employees.view")
    if not _may_record_leave(request.user, company_id, assignment):
        raise PermissionDenied("Recording leave for them is not yours to do.")
    form = actions.leave_form(company_id, employee, request.POST, request.FILES)
    if form.is_valid():
        data = {k: v for k, v in form.cleaned_data.items() if k != "document"}
        try:
            leave = leave_services.record_leave(actor=request.user, company_id=company_id,
                                                values=data,
                                                document=form.cleaned_data.get("document"))
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            with use_company(company_id):
                units = leave.segments.first().requested_units.normalize()
            messages.success(request, f"Leave recorded: {units} working day(s).")
            return _back(pk, "leave")
    return _profile(request, company_id, pk, bound={"leave": form}, open_dialog="leave-dialog")


@login_required
@require_POST
def employee_late(request, pk):
    """Apply for late approval: approve a late arrival on one of their days."""
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    _m, employee, _a, _c = employee_detail_services.get_employee_for_edit(
        actor=request.user, company_id=company_id, employee_id=pk, code="employees.view")
    today = employee_detail_services.company_today(_m.company)
    form = actions.LateForm(request.POST, days=actions.late_days(company_id, employee, today),
                            auto_id="late_%s")
    if form.is_valid():
        try:
            correction_services.excuse_late(
                actor=request.user, company_id=company_id, employee_id=pk,
                work_date=form.cleaned_data["work_date"], reason=form.cleaned_data["reason"])
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, f"Late arrival on {form.cleaned_data['work_date']:%d %b} "
                                      "approved. The day counts no late minutes.")
            return _back(pk, "attendance")
    return _profile(request, company_id, pk, bound={"late": form}, open_dialog="late-dialog")


@login_required
@require_POST
def employee_active(request, pk):
    """Make inactive (Suspended), or active again."""
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    active = request.POST.get("active") == "1"
    form = actions.InactiveForm(request.POST, auto_id="inactive_%s")
    if active or form.is_valid():
        try:
            employee = actions.set_active(
                actor=request.user, company_id=company_id, employee_id=pk, active=active,
                reason="" if active else form.cleaned_data["reason"])
        except ValidationError as exc:
            if active:
                messages.error(request, " ".join(exc.messages))
                return _back(pk)
            apply_service_errors(form, exc)
        else:
            messages.success(request, f"{employee.full_name} is "
                                      f"{'active again' if active else 'inactive (suspended)'}.")
            return _back(pk)
    return _profile(request, company_id, pk, bound={"inactive": form},
                    open_dialog="inactive-dialog")


@login_required
@require_POST
def employee_overtime(request, pk):
    """Disallow overtime from a day, or allow it again."""
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    allow = request.POST.get("allow") == "1"
    form = actions.OvertimeForm(request.POST, auto_id="overtime_%s")
    if allow or form.is_valid():
        membership, _e, _a, _c = employee_detail_services.get_employee_for_edit(
            actor=request.user, company_id=company_id, employee_id=pk, code="employees.view")
        try:
            employee = actions.set_overtime(
                actor=request.user, company_id=company_id, employee_id=pk,
                from_day=None if allow else form.cleaned_data["from_day"],
                today=employee_detail_services.company_today(membership.company))
        except ValidationError as exc:
            if allow:
                messages.error(request, " ".join(exc.messages))
                return _back(pk)
            apply_service_errors(form, exc)
        else:
            messages.success(request, f"Overtime allowed for {employee.full_name} again."
                             if allow else
                             f"No overtime for {employee.full_name} from "
                             f"{employee.no_overtime_from:%d %b %Y}.")
            return _back(pk)
    return _profile(request, company_id, pk, bound={"overtime": form},
                    open_dialog="overtime-dialog")


@login_required
@require_POST
def employee_reports(request, pk):
    """Set as line manager: the people who report to them."""
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    _m, manager, _a, _c = employee_detail_services.get_employee_for_edit(
        actor=request.user, company_id=company_id, employee_id=pk, code="employees.edit")
    with use_company(company_id):
        form = actions.ReportsForm(
            request.POST, choices=info.line_manager_choices(request.user, company_id, manager),
            auto_id="reports_%s")
        valid = form.is_valid()
    if valid:
        try:
            actions.set_reports(actor=request.user, company_id=company_id, manager_id=pk,
                                people=form.cleaned_data["people"])
        except (ValidationError, PermissionDenied) as exc:
            form.add_error("people", " ".join(getattr(exc, "messages", [str(exc)])))
        else:
            messages.success(request, f"{manager.full_name} is now line manager of "
                                      f"{len(form.cleaned_data['people'])} people.")
            return _back(pk)
    return _profile(request, company_id, pk, bound={"reports": form},
                    open_dialog="reports-dialog")
