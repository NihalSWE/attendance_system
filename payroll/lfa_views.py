"""LFA pages (2026-09-28): the company's settings, the claims to decide, one
claim, and the employee's own LFA page. Rules in ``payroll.lfa``."""

import mimetypes

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from access_control.branch_access import ALL_BRANCHES
from base_template.tables import paginate, render
from common.choices import ActiveStatus
from common.forms import apply_service_errors
from common.tenant import use_company
from employees.models import Employee
from leaves.models import LeaveType
from organization.access_services import people
from organization.services import require_structure_manager
from organization.views import _company_or_redirect
from payroll import lfa
from payroll.lfa_forms import (
    LfaCancelForm,
    LfaClaimForm,
    LfaDecisionForm,
    LfaPaidForm,
    LfaSettingsForm,
)
from payroll.models import LfaClaim

STATUS_TABS = (("pending", "Waiting"), ("approved", "Approved"), ("paid", "Paid"),
               ("closed", "Rejected or cancelled"), ("all", "All"))
CLOSED = ("rejected", "withdrawn", "cancelled")


def _scope(request, company_id):
    scope = lfa.decide_scope(request.user, company_id)
    if not scope:
        raise PermissionDenied("LFA claims are for the owner, the company administrator or "
                               "whoever prepares salary in a branch.")
    return scope


def _claims(scope):
    claims = LfaClaim.objects.select_related(
        "employee", "payroll_adjustment__target_payroll_period", "leave_request")
    if scope is ALL_BRANCHES:
        return claims
    return claims.filter(employee__in=people(scope))


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------


@login_required
@require_http_methods(["GET", "POST"])
def lfa_settings(request):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    require_structure_manager(request.user, company_id)
    settings = lfa.settings_for(company_id)
    with use_company(company_id):
        types = LeaveType.objects.filter(status=ActiveStatus.ACTIVE).order_by("name")
        form = LfaSettingsForm(request.POST or None, leave_types=types,
                               initial=LfaSettingsForm.initial_from(settings))
        if request.method == "POST" and form.is_valid():
            data = dict(form.cleaned_data)
            chosen = data.pop("leave_types")
            try:
                lfa.save_settings(actor=request.user, company_id=company_id, values=data,
                                  leave_types=chosen)
            except ValidationError as exc:
                apply_service_errors(form, exc)
            else:
                messages.success(request, "LFA is on. Employees can claim it." if data["enabled"]
                                 else "LFA settings saved. It is off: nobody can claim it.")
                return redirect("payroll:lfa_settings")
        return render(request, "payroll/lfa_settings.html", {"form": form, "settings": settings})


# --------------------------------------------------------------------------
# Claims
# --------------------------------------------------------------------------


@login_required
@require_http_methods(["GET"])
def lfa_claims(request):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    scope = _scope(request, company_id)
    show = request.GET.get("show", "pending")
    if show not in dict(STATUS_TABS):
        show = "pending"
    with use_company(company_id):
        claims = _claims(scope)
        lfa.sync_paid(company_id, list(claims.filter(status="approved",
                                                     payroll_adjustment__isnull=False)))
        counts = {key: (claims.filter(status__in=CLOSED) if key == "closed"
                        else claims if key == "all" else claims.filter(status=key)).count()
                  for key, _label in STATUS_TABS}
        shown = (claims.filter(status__in=CLOSED) if show == "closed"
                 else claims if show == "all" else claims.filter(status=show))
        page = paginate(request, shown.order_by("-created_at"),
                        search=("employee__first_name", "employee__last_name", "status", "note"),
                        order=(("employee__first_name", "employee__last_name"), "cycle_start",
                               "approved_amount", "payment", "status", "created_at", None))
    return render(request, "payroll/lfa_claims.html", {
        "claims": page, "show": show,
        "tabs": [(key, label, counts[key]) for key, label in STATUS_TABS],
        "settings": lfa.settings_for(company_id),
    })


@login_required
@require_http_methods(["GET", "POST"])
def lfa_claim_new(request):
    """Enter a claim for someone (e.g. without a login)."""
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    scope = _scope(request, company_id)
    settings = lfa.settings_for(company_id)
    today = timezone.localdate()
    with use_company(company_id):
        employees = (Employee.objects.filter(employment_status__in=["active", "probation"])
                     if scope is ALL_BRANCHES else people(scope).filter(
                         employment_status__in=["active", "probation"])
                     ).exclude(user=request.user).order_by("first_name", "last_name")
        raw = request.POST.get("employee") or request.GET.get("employee", "")
        employee = employees.filter(pk=raw).first() if str(raw).isdigit() else None
        found = lfa.eligibility(employee, settings, today) if employee else None
        form = LfaClaimForm(request.POST or None, request.FILES or None, employees=employees,
                            leave_requests=found.leave_requests if found else (),
                            needs_leave=settings.requires_leave,
                            needs_document=settings.requires_document,
                            initial={"employee": employee.pk if employee else None})
        if request.method == "POST" and form.is_valid():
            try:
                claim = lfa.submit(actor=request.user, company_id=company_id,
                                   employee=form.cleaned_data["employee"],
                                   values={"leave_request": form.cleaned_data.get("leave_request"),
                                           "note": form.cleaned_data.get("note")},
                                   document=form.cleaned_data.get("document"), today=today)
            except ValidationError as exc:
                apply_service_errors(form, exc)
            else:
                messages.success(request, f"Claim entered for {claim.employee.full_name}. "
                                          "Decide it now or later.")
                return redirect("payroll:lfa_claim", pk=claim.pk)
        return render(request, "payroll/lfa_new.html", {
            "form": form, "employee": employee, "found": found, "settings": settings})


def _claim_page(request, company_id, claim, *, decision=None, cancel_form=None, paid_form=None,
                open_dialog=""):
    settings_now = lfa.settings_for(company_id)
    may_decide = lfa._deciders(request.user, company_id, claim.employee)[1] and \
        claim.employee.user_id != request.user.pk
    with_salary = claim.payment == "with_salary"
    return render(request, "payroll/lfa_claim.html", {
        "claim": claim, "may_decide": may_decide, "settings": settings_now,
        "decision": decision or LfaDecisionForm(
            with_salary=with_salary,
            initial={"amount": claim.calculated_amount,
                     "pay_month": timezone.localdate().replace(day=1).isoformat()}),
        "cancel_form": cancel_form or LfaCancelForm(auto_id="cancel_%s"),
        "paid_form": paid_form or LfaPaidForm(auto_id="paid_%s",
                                              initial={"paid_on": timezone.localdate()}),
        "open_dialog": open_dialog,
    })


def _claim(request, company_id, pk):
    scope = _scope(request, company_id)
    with use_company(company_id):
        claim = get_object_or_404(_claims(scope), pk=pk)
        lfa.sync_paid(company_id, [claim])
    return claim


@login_required
@require_http_methods(["GET", "POST"])
def lfa_claim(request, pk):
    """One claim, and its decision."""
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    claim = _claim(request, company_id, pk)
    decision = None
    if request.method == "POST":
        decision = LfaDecisionForm(request.POST, with_salary=claim.payment == "with_salary")
        if decision.is_valid():
            data = decision.cleaned_data
            try:
                lfa.decide(actor=request.user, company_id=company_id, claim_id=claim.pk,
                           approve=data["decision"] == "approve", amount=data.get("amount"),
                           pay_month=data.get("pay_month"), note=data.get("note"))
            except ValidationError as exc:
                apply_service_errors(decision, exc)
            else:
                messages.success(request, "LFA claim approved." if data["decision"] == "approve"
                                 else "LFA claim rejected.")
                return redirect("payroll:lfa_claim", pk=claim.pk)
    with use_company(company_id):
        return _claim_page(request, company_id, claim, decision=decision)


@login_required
@require_POST
def lfa_cancel(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    claim = _claim(request, company_id, pk)
    form = LfaCancelForm(request.POST, auto_id="cancel_%s")
    if form.is_valid():
        try:
            lfa.cancel(actor=request.user, company_id=company_id, claim_id=claim.pk,
                       note=form.cleaned_data["note"])
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, "LFA claim cancelled. Its payslip line is taken off.")
            return redirect("payroll:lfa_claim", pk=claim.pk)
    with use_company(company_id):
        return _claim_page(request, company_id, claim, cancel_form=form,
                           open_dialog="cancel-dialog")


@login_required
@require_POST
def lfa_paid(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    claim = _claim(request, company_id, pk)
    form = LfaPaidForm(request.POST, auto_id="paid_%s")
    if form.is_valid():
        try:
            lfa.mark_paid(actor=request.user, company_id=company_id, claim_id=claim.pk,
                          paid_on=form.cleaned_data["paid_on"],
                          reference=form.cleaned_data.get("reference"))
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, "Marked as paid.")
            return redirect("payroll:lfa_claim", pk=claim.pk)
    with use_company(company_id):
        return _claim_page(request, company_id, claim, paid_form=form, open_dialog="paid-dialog")


@login_required
@require_http_methods(["GET"])
def lfa_document(request, pk):
    """A claim's proof: for the employee and whoever decides their claims -
    never a public file address."""
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    with use_company(company_id):
        claim = get_object_or_404(LfaClaim.objects.select_related("employee"), pk=pk)
    mine = claim.employee.user_id == request.user.pk
    if not mine and not lfa._deciders(request.user, company_id, claim.employee)[1]:
        raise PermissionDenied("This document is not yours to open.")
    if not claim.document:
        raise Http404("No document.")
    kind = mimetypes.guess_type(claim.document.name)[0] or "application/octet-stream"
    reply = FileResponse(claim.document.open("rb"), content_type=kind,
                         filename=claim.document_name or None)
    reply["Cache-Control"] = "private, max-age=300"
    reply["X-Content-Type-Options"] = "nosniff"
    return reply


# --------------------------------------------------------------------------
# The employee's own LFA
# --------------------------------------------------------------------------


@login_required
@require_http_methods(["GET", "POST"])
def my_lfa(request):
    if not request.company_id:
        raise Http404("No company.")
    company_id = request.company_id
    settings = lfa.settings_for(company_id)
    today = timezone.localdate()
    with use_company(company_id):
        employee = Employee.objects.filter(user=request.user).first()
        if employee is None:
            raise PermissionDenied("This login is not linked to an employee record.")
        found = lfa.eligibility(employee, settings, today)
        form = LfaClaimForm(request.POST or None, request.FILES or None,
                            leave_requests=found.leave_requests,
                            needs_leave=settings.requires_leave,
                            needs_document=settings.requires_document, auto_id="claim_%s")
        if request.method == "POST" and form.is_valid():
            try:
                lfa.submit(actor=request.user, company_id=company_id,
                           values={"leave_request": form.cleaned_data.get("leave_request"),
                                   "note": form.cleaned_data.get("note")},
                           document=form.cleaned_data.get("document"), today=today)
            except ValidationError as exc:
                apply_service_errors(form, exc)
            else:
                messages.success(request, "Your LFA claim is sent for approval.")
                return redirect("me:lfa")
        claims = list(LfaClaim.objects.select_related(
            "payroll_adjustment__target_payroll_period").filter(employee=employee))
        lfa.sync_paid(company_id, claims)
    return render(request, "base_template/me/lfa.html", {
        "settings": settings, "found": found, "form": form, "claims": claims,
        "open_dialog": "claim-dialog" if request.method == "POST" else ""})


@login_required
@require_POST
def my_lfa_withdraw(request, pk):
    if not request.company_id:
        raise Http404("No company.")
    try:
        lfa.withdraw(actor=request.user, company_id=request.company_id, claim_id=pk)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, "Your claim is withdrawn.")
    return redirect(reverse("me:lfa"))
