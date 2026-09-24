"""Company salary pages: generate a month's salary, read payslips, and the
company's salary settings."""

import datetime
from decimal import Decimal

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, DecimalField, Exists, IntegerField, OuterRef, Q, Sum
from django.db.models.fields.json import KT
from django.db.models.functions import Cast, Coalesce
from django.shortcuts import redirect
from base_template.tables import paginate, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from access_control.branch_access import ALL_BRANCHES, branches_for_any
from access_control.page_access import may_open
from attendance.models import AttendanceRecord
from attendance.services import month_bounds
from attendance.views import month_context, read_month
from common.forms import StyledFormMixin, apply_service_errors
from common.tenant import use_company
from organization.services import (
    STRUCTURE_ROLES,
    require_structure_manager,
)
from organization.models import Branch
from organization.views import _company_or_redirect
from payroll import overtime, penalties, policy
from payroll.forms import (
    GeneralSettingsForm,
    OvertimeDecisionForm,
    PenaltyRuleForm,
    SalaryRulesForm,
    StopPenaltyRuleForm,
)
from payroll.models import (
    AttendancePenaltyRule,
    OvertimeDecision,
    PayrollAdjustment,
    PayrollPeriod,
    PayrollPolicyVersion,
    PayrollRecord,
    PayrollRun,
    PenaltyAssessment,
)
from payroll.services import (
    add_adjustment,
    approval_blocker,
    attendance_changed_since,
    approve_payroll,
    record_branch_id,
    remove_adjustment,
    generate_payroll,
    reopen_payroll,
    return_payroll,
    submit_payroll,
    salary_branches,
    salary_month_branches,
    summarise,
    waive_penalty,
)


@login_required
@require_http_methods(["GET"])
def payroll_home(request):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    membership, view_branches, prepare_branches, company_wide = _salary_scope(request.user, company_id)
    year, month = read_month(request.GET)
    first, last = month_bounds(year, month)

    with use_company(company_id):
        period = PayrollPeriod.objects.filter(start_date=first, end_date=last).first()
        run = (
            PayrollRun.objects.filter(payroll_period=period).order_by("-pk").first()
            if period else None
        )
        shown = run.records.all() if run else PayrollRecord.objects.none()
        if view_branches is not ALL_BRANCHES:
            # A12 part 6: the payslips of people placed in their branches.
            shown = shown.filter(employee_assignment_at_period_end__branch_id__in=view_branches)
        totals = shown.aggregate(employees=Count("pk"), gross=Sum("gross_earnings"),
                                 deductions=Sum("total_deductions"), net=Sum("net_pay"))
        shown_ids = list(shown.values_list("employee_id", flat=True))
        records = paginate(request,
            shown.select_related("employee")
            .annotate(table_rate=Cast(KT("calculation_snapshot__base_rate"), DecimalField(max_digits=18, decimal_places=2)),
                **{f"table_{key}": Coalesce(Cast(KT(f"calculation_snapshot__counts__{key}"), IntegerField()), 0)
                   for key in ("present", "half_day", "absent", "unpaid_leave")})
            .order_by("employee__first_name", "employee__last_name"),
            search=("employee__first_name", "employee__last_name", "calculation_snapshot__pay_basis"),
            order=(("employee__first_name", "employee__last_name"), "calculation_snapshot__pay_basis", "table_rate",
                   "table_present", "table_half_day", "table_absent",
                   "table_unpaid_leave", "gross_earnings", "total_deductions", "net_pay", None))

    if company_wide:
        decides_overtime = membership.role in overtime.OVERTIME_ROLES
        overtime_branches = ALL_BRANCHES
    else:
        overtime_branches = branches_for_any(
            request.user, company_id, "overtime.view", "overtime.decide")
        decides_overtime = bool(overtime_branches)
    return render(request, "payroll/payroll_home.html", {
        **month_context(year, month),
        "run": run,
        "records": records,
        "totals": totals,
        "company_wide": company_wide,
        "can_manage": membership.role in STRUCTURE_ROLES,
        # Owner/admin generate the whole month; a branch its own people.
        "can_generate": bool(prepare_branches),
        "decides_overtime": decides_overtime,
        "overtime_waiting": (
            overtime.undecided_count(company_id, first, last, overtime_branches)
            if decides_overtime else 0
        ),
        # Approval (A11): whoever submitted a waiting month can take it back.
        "is_submitter": bool(run and run.submitted_by_id == request.user.pk),
        # May this viewer approve the waiting month, and if not, why - said on
        # the page, not discovered after a click.
        "approval_blocked": (
            approval_blocker(request.user, company_id, run)
            if run and run.status == PayrollRun.Status.SUBMITTED else None
        ),
        # A day fixed since the payslips were built: approving would finalise
        # figures the attendance no longer matches, so approve refuses - said
        # here first, while it can still be regenerated.
        "attendance_since_run": (
            attendance_changed_since(company_id, run)
            if run and run.status != PayrollRun.Status.POSTED else 0
        ),
        # Decisions made since the draft was generated are not in it yet.
        "overtime_since_run": (
            overtime.decided_after(company_id, run, None if company_wide else shown_ids)
            if run and run.status == PayrollRun.Status.DRAFT else 0
        ),
    })


def _salary_scope(user, company_id):
    """``(membership, view_branches, prepare_branches, company_wide)`` (A12 part 6).

    The owner/admin and the payroll manager see and generate the whole month.
    A branch manager or a person given access sees the payslips of people
    placed in their branches, and generates those. HR sees no pay at all
    (Ajay, 2026-09-23); ``salary_month_branches`` says who else is let in.
    """
    membership, view = salary_month_branches(user, company_id)
    if not view:
        raise PermissionDenied("Salary needs owner, company administrator or payroll manager "
                               "access, or salary access in a branch.")
    _, prepare = salary_branches(user, company_id, "salary.prepare")
    return membership, view, prepare, view is ALL_BRANCHES


@login_required
@require_http_methods(["POST"])
def payroll_generate(request):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    year, month = read_month(request.POST)
    _, prepare = salary_branches(request.user, company_id, "salary.prepare")
    if not prepare:
        raise PermissionDenied("Generating salary needs owner or company administrator access, "
                               "or access to prepare salary in a branch.")
    branch_ids = None if prepare is ALL_BRANCHES else prepare
    try:
        run = generate_payroll(
            actor=request.user, company_id=company_id, year=year, month=month,
            branch_ids=branch_ids,
        )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        skipped = run.skipped_now
        if branch_ids is None:
            message = (
                f"Salary generated for {run.totals_snapshot['employees']} employee(s). "
                "It is a draft and can be regenerated at any time."
            )
        else:
            # A branch's own people; other branches' payslips were not touched.
            with use_company(company_id):
                mine = run.records.filter(
                    employee_assignment_at_period_end__branch_id__in=branch_ids).count()
                names = sorted(Branch.objects.filter(pk__in=branch_ids).values_list("name", flat=True))
            message = (
                f"Salary generated for {mine} employee(s) in {', '.join(names)}. "
                "It is a draft; the owner or company administrator finalises the month."
            )
        if skipped:
            message += f" Skipped, no salary set: {', '.join(skipped)}."
        messages.success(request, message)
    return redirect(f"{reverse('payroll:payroll_home')}?month={month}&year={year}")


class ReopenForm(StyledFormMixin, forms.Form):
    reason = forms.CharField(
        label="Why is it being undone?", widget=forms.Textarea,
        help_text="Recorded in the audit trail. Employees stop seeing these payslips until it is finalised again.",
    )


class ReturnForm(StyledFormMixin, forms.Form):
    reason = forms.CharField(
        label="What needs changing?", widget=forms.Textarea,
        help_text="Shown on the Salary page until it is submitted again, and kept in the audit trail.",
    )


class AdjustmentForm(StyledFormMixin, forms.Form):
    adjustment_type = forms.ChoiceField(
        label="Type", choices=PayrollAdjustment.AdjustmentType.choices
    )
    amount = forms.DecimalField(
        label="Amount", min_value=Decimal("0.01"), max_digits=14, decimal_places=2
    )
    reason = forms.CharField(
        label="Reason", max_length=255,
        help_text="Shown on the payslip, for example Eid bonus or Advance recovery.",
    )


#: The four things that move a month between Draft, Waiting for approval and
#: Finalised: (service, form, page title, button label, button tone, done message).
RUN_ACTIONS = {
    "submit": (submit_payroll, None, "Submit salary for approval", "Submit for approval", "primary",
               "Salary submitted. An owner or company administrator approves it; until then "
               "it cannot be regenerated."),
    "approve": (approve_payroll, None, "Approve salary", "Approve and finalise", "primary",
                "Salary approved and finalised. Employees can now see their payslips; the "
                "month's attendance and overtime are locked."),
    "return": (return_payroll, ReturnForm, "Send salary back", "Send back", "danger",
               "Salary sent back. It is a draft again: fix it, generate it, then submit it."),
    "reopen": (reopen_payroll, ReopenForm, "Undo finalise", "Undo finalise", "danger",
               "Salary is a draft again. Fix what was wrong, generate it, then submit it for approval."),
}


def _run_action(request, action):
    """Confirmation page for Submit / Approve / Send back / Undo finalise.

    Submit: whoever may prepare salary (the owner/administrator, or a branch
    manager with salary access). Approve and Undo finalise: the owner or
    company administrator. Send back: the owner/administrator, or whoever
    submitted it. The services check all of this again.
    """
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    if action in ("approve", "reopen"):
        require_structure_manager(request.user, company_id)
    elif action == "submit" and not salary_branches(request.user, company_id, "salary.prepare")[1]:
        raise PermissionDenied("Submitting salary needs access to prepare it.")
    service, form_class, title, submit_label, tone, done = RUN_ACTIONS[action]
    year, month = read_month(request.POST if request.method == "POST" else request.GET)
    back = f"{reverse('payroll:payroll_home')}?month={month}&year={year}"
    form = form_class(request.POST or None) if form_class else None
    if request.method == "POST" and (form is None or form.is_valid()):
        extra = {"reason": form.cleaned_data["reason"]} if form else {}
        try:
            service(actor=request.user, company_id=company_id, year=year, month=month, **extra)
        except ValidationError as exc:
            if form is None:
                messages.error(request, " ".join(exc.messages))
                return redirect(back)
            apply_service_errors(form, exc)
        else:
            messages.success(request, done)
            return redirect(back)
    first, last = month_bounds(year, month)
    with use_company(company_id):
        run = PayrollRun.objects.select_related("submitted_by").filter(
            payroll_period__start_date=first, payroll_period__end_date=last
        ).order_by("-pk").first()
    return render(request, "payroll/run_action.html", {
        **month_context(year, month),
        "run": run, "form": form, "action": action, "back": back,
        "title": title, "submit_label": submit_label, "tone": tone,
        # Said on the page rather than only after the click.
        "blocked": approval_blocker(request.user, company_id, run) if action == "approve" else None,
    })


@login_required
@require_http_methods(["GET", "POST"])
def payroll_submit(request):
    return _run_action(request, "submit")


@login_required
@require_http_methods(["GET", "POST"])
def payroll_finalise(request):
    """Approve a submitted month, which finalises it (the URL keeps its old name)."""
    return _run_action(request, "approve")


@login_required
@require_http_methods(["GET", "POST"])
def payroll_return(request):
    return _run_action(request, "return")


@login_required
@require_http_methods(["GET", "POST"])
def payroll_reopen(request):
    return _run_action(request, "reopen")


@login_required
@require_http_methods(["GET", "POST"])
def payslip_email(request, pk):
    """Email one finalised payslip: a compose page (From, To, Subject, Message,
    the PDF attached), then send it through the company's mail account
    (payroll/payslip_email.py).

    A POST without the compose fields sends to the employee's own address with
    the standard wording, as the button did before the compose page."""
    from payroll.payslip_email import PayslipEmailForm, compose_initial, email_payslip, why_not
    from payroll.payslip_export import payslip_filename
    from organization import mail_settings

    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    membership, prepare = salary_branches(request.user, company_id, "salary.prepare")
    if not prepare:
        raise PermissionDenied("Emailing a payslip needs access to prepare salary.")
    with use_company(company_id):
        record = payslip_records().filter(pk=pk).first()
        if record is None or record_branch_id(record) not in prepare:
            raise PermissionDenied("Payslip not found in your branches.")
        context = payslip_context(record)
    company = membership.company
    payslip_url = reverse("payroll:payslip", args=[pk])
    composed = request.method == "POST" and "to" in request.POST
    form = PayslipEmailForm(request.POST if composed else None,
                            initial=compose_initial(context, company))

    if request.method == "POST" and (not composed or form.is_valid()):
        fields = form.cleaned_data if composed else {}
        try:
            with use_company(company_id):
                address = email_payslip(actor=request.user, company_id=company_id,
                                        context=context, sent_by=request.user.get_username(),
                                        **fields)
        except ValidationError as exc:
            if not composed:
                messages.error(request, " ".join(exc.messages))
                return redirect(payslip_url)
            form.add_error(None, " ".join(exc.messages))
        else:
            messages.success(request, f"Payslip emailed to {address}.")
            return redirect(payslip_url)

    how, from_email, _name = mail_settings.sender(company_id)
    return render(request, "payroll/payslip_email.html", {
        "form": form,
        "record": record,
        "period": context["period"],
        "blocked": why_not(record, record.employee),
        "how": how,
        "from_email": from_email,
        "attachment": payslip_filename(record, context["period"]),
        "payslip_url": payslip_url,
        "can_set_up_mail": membership.role in STRUCTURE_ROLES,
    })


@login_required
@require_http_methods(["GET", "POST"])
def component_list(request):
    """The company's allowances and recurring deductions, and adding one."""
    from payroll import component_services
    from payroll.forms import SalaryComponentForm

    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    require_structure_manager(request.user, company_id)
    with use_company(company_id):
        form = SalaryComponentForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            try:
                component = component_services.create_component(
                    actor=request.user, company_id=company_id, values=form.cleaned_data)
            except ValidationError as exc:
                apply_service_errors(form, exc)
            else:
                messages.success(
                    request,
                    f"{component.name} added. Give it to people on their Edit employee page.")
                return redirect("payroll:component_list")
        return render(request, "payroll/components.html", {
            "form": form,
            "components": list(component_services.components(company_id)),
        })


@login_required
@require_http_methods(["GET", "POST"])
def component_edit(request, pk):
    from payroll import component_services
    from payroll.forms import SalaryComponentForm

    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    require_structure_manager(request.user, company_id)
    with use_company(company_id):
        component = component_services.components(company_id).filter(pk=pk).first()
        if component is None:
            raise PermissionDenied("Component not found in this company.")
        form = SalaryComponentForm(request.POST or None, instance=component)
        if request.method == "POST" and form.is_valid():
            try:
                component_services.update_component(
                    actor=request.user, company_id=company_id, component_id=pk,
                    values=form.cleaned_data)
            except ValidationError as exc:
                apply_service_errors(form, exc)
            else:
                messages.success(request, "Saved. It applies from the next time salary is generated.")
                return redirect("payroll:component_list")
        return render(request, "payroll/component_form.html", {
            "form": form, "component": component,
        })


@login_required
@require_http_methods(["POST"])
def component_status(request, pk):
    from payroll import component_services

    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    status = "inactive" if request.POST.get("status") == "inactive" else "active"
    try:
        component = component_services.set_component_status(
            actor=request.user, company_id=company_id, component_id=pk, status=status)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, (
            f"{component.name} is no longer offered; it stops counting the next time salary "
            "is generated." if status == "inactive" else f"{component.name} is offered again."))
    return redirect("payroll:component_list")


@login_required
@require_http_methods(["GET", "POST"])
def salary_settings(request):
    """Company salary settings: dated calculation rules, plus currency and pay day.

    Two forms on one page, told apart by the posted ``section``.
    """
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    today = timezone.localdate()
    page = policy.salary_settings_page(actor=request.user, company_id=company_id, today=today)
    settings = page["settings"]

    section = request.POST.get("section") if request.method == "POST" else None
    # Start the form from the newest saved rules: if a change is already saved
    # for a later month, a new change can only start from that month on.
    latest = next((v for v in page["versions"] if v.status == "active"), None)
    if latest and latest.effective_from > today:
        start_rules, start_month = policy.SalaryRules.from_version(latest), latest.effective_from
    else:
        start_rules, start_month = page["rules"], today
    rules_form = SalaryRulesForm(
        request.POST if section == "rules" else None,
        initial=SalaryRulesForm.initial_from(start_rules, start_month),
    )
    general_form = GeneralSettingsForm(
        request.POST if section == "general" else None,
        initial={
            "currency": page["currency"],
            "default_pay_day": settings.default_pay_day if settings else None,
        },
    )

    if section == "rules" and rules_form.is_valid():
        values = rules_form.service_values()
        try:
            version = policy.change_salary_rules(
                actor=request.user, company_id=company_id, values=values
            )
        except ValidationError as exc:
            apply_service_errors(rules_form, exc)
        else:
            messages.success(
                request,
                f"Salary rules saved from {version.effective_from:%B %Y}. "
                "Regenerate a month's salary to apply them to it.",
            )
            return redirect("payroll:salary_settings")
    elif section == "general" and general_form.is_valid():
        try:
            policy.update_general_settings(
                actor=request.user, company_id=company_id, values=general_form.cleaned_data
            )
        except ValidationError as exc:
            apply_service_errors(general_form, exc)
        else:
            messages.success(request, "Salary settings saved.")
            return redirect("payroll:salary_settings")

    for version in page["versions"]:
        # effective_to is the first day of the next version; show the last month.
        version.last_day = (
            version.effective_to - datetime.timedelta(days=1) if version.effective_to else None
        )
        if version.status != PayrollPolicyVersion.Status.ACTIVE:
            version.state = ("Replaced", "neutral")
        elif version.effective_from > today:
            version.state = ("Upcoming", "info")
        elif version.effective_to and version.effective_to <= today:
            version.state = ("Ended", "neutral")
        else:
            version.state = ("In use", "success")
    return render(request, "payroll/salary_settings.html", {
        **page,
        "summary": rules_summary(page["rules"]),
        "rules_form": rules_form,
        "general_form": general_form,
        "penalty_rules": _penalty_rows(company_id, today, page["currency"], request=request),
    })


def _penalty_rows(company_id, today, currency, *, request=None):
    """Penalty rules in use now or saved for a later month, as table rows."""
    Rule = AttendancePenaltyRule
    with use_company(company_id):
        later = Rule.objects.filter(code=OuterRef("code"), status=Rule.Status.ACTIVE,
                                    effective_from__gt=OuterRef("effective_from"))
        versions = Rule.objects.filter(status=Rule.Status.ACTIVE).filter(
            Q(effective_to__isnull=True) | Q(effective_to__gt=today)
        ).annotate(table_has_later=Exists(later)).order_by("name", "effective_from")
        if request is not None:
            versions = paginate(request, versions, search=("name", "code", "metric", "deduction_method"),
                order=("name", "metric", "deduction_method", "effective_from", None))
        versions = list(versions)
    rows = []
    for rule in versions:
        if rule.effective_from > today:
            state = ("Upcoming", "info")
        else:
            state = ("In use", "success")
        rows.append({
            "rule": rule,
            "when": penalties.describe_when(rule),
            "deducts": penalties.describe_deduction(rule, currency),
            "state": state,
            "last_day": rule.effective_to - datetime.timedelta(days=1) if rule.effective_to else None,
            "can_change": not rule.table_has_later,
        })
    return rows


def _penalty_form_page(request, *, form, title, submit_label, rule=None):
    return render(request, "payroll/penalty_rule_form.html", {
        "form": form, "title": title, "submit_label": submit_label, "rule": rule,
    })


@login_required
@require_http_methods(["GET", "POST"])
def penalty_rule_create(request):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    require_structure_manager(request.user, company_id)
    form = PenaltyRuleForm(request.POST or None, initial={
        "operator": "gte", "occurrence_mode": "single_day", "required_occurrences": 1,
        "deduction_method": "day_fraction",
    })
    if request.method == "POST" and form.is_valid():
        try:
            rule = penalties.create_penalty_rule(
                actor=request.user, company_id=company_id, values=form.service_values()
            )
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(
                request, f"Penalty rule “{rule.name}” added from {rule.effective_from:%B %Y}."
            )
            return redirect(f"{reverse('payroll:salary_settings')}#penalty-rules")
    return _penalty_form_page(request, form=form, title="Add penalty rule", submit_label="Add rule")


@login_required
@require_http_methods(["GET", "POST"])
def penalty_rule_change(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    _, rule = penalties.get_rule_for_edit(actor=request.user, company_id=company_id, rule_id=pk)
    form = PenaltyRuleForm(
        request.POST or None,
        initial=PenaltyRuleForm.initial_from(rule, timezone.localdate()),
    )
    if request.method == "POST" and form.is_valid():
        try:
            new = penalties.change_penalty_rule(
                actor=request.user, company_id=company_id, rule_id=rule.pk,
                values=form.service_values(),
            )
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(
                request, f"“{new.name}” changed from {new.effective_from:%B %Y}. Earlier months keep the old rule."
            )
            return redirect(f"{reverse('payroll:salary_settings')}#penalty-rules")
    return _penalty_form_page(
        request, form=form, title=f"Change {rule.name}", submit_label="Save the change", rule=rule,
    )


@login_required
@require_http_methods(["GET", "POST"])
def penalty_rule_stop(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    _, rule = penalties.get_rule_for_edit(actor=request.user, company_id=company_id, rule_id=pk)
    form = StopPenaltyRuleForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            penalties.stop_penalty_rule(
                actor=request.user, company_id=company_id, rule_id=rule.pk,
                stops_from=form.stops_from(),
            )
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(
                request, f"“{rule.name}” no longer applies from {form.stops_from():%B %Y}."
            )
            return redirect(f"{reverse('payroll:salary_settings')}#penalty-rules")
    return render(request, "payroll/penalty_rule_stop.html", {"form": form, "rule": rule})


@login_required
@require_http_methods(["POST"])
def penalty_waive(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    try:
        record = waive_penalty(actor=request.user, company_id=company_id, assessment_id=pk)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("payroll:payroll_home")
    messages.success(request, "Penalty waived. The month's salary was regenerated without it.")
    if record is None:
        return redirect("payroll:payroll_home")
    return redirect("payroll:payslip", record.pk)


@login_required
@require_http_methods(["POST"])
def penalty_unwaive(request, pk):
    """Undo a waiver given by mistake, while the month is still a draft."""
    from payroll.services import unwaive_penalty

    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    try:
        record = unwaive_penalty(actor=request.user, company_id=company_id, assessment_id=pk)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("payroll:payroll_home")
    messages.success(
        request, "Waiver undone. The month's salary was regenerated with the penalty.")
    if record is None:
        return redirect("payroll:payroll_home")
    return redirect("payroll:payslip", record.pk)


@login_required
@require_http_methods(["POST"])
def payslip_adjustment_add(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    form = AdjustmentForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose Bonus or Deduction, and enter an amount above zero and a reason.")
        return redirect("payroll:payslip", pk)
    try:
        record = add_adjustment(actor=request.user, company_id=company_id, record_id=pk,
                                **form.cleaned_data)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("payroll:payslip", pk)
    messages.success(request, "Line added. The month's salary was regenerated with it.")
    return redirect("payroll:payslip", record.pk) if record else redirect("payroll:payroll_home")


@login_required
@require_http_methods(["POST"])
def payslip_correction_add(request, pk):
    """Put right a finalised month, in the first month still open."""
    from payroll.services import correct_finalised_month

    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    form = AdjustmentForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose Bonus or Deduction, and enter an amount above zero and a reason.")
        return redirect("payroll:payslip", pk)
    try:
        adjustment = correct_finalised_month(
            actor=request.user, company_id=company_id, record_id=pk, **form.cleaned_data)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, (
            f"Correction saved. It is paid with {adjustment.target_payroll_period.name}'s "
            f"salary; {adjustment.source_payroll_period.name} stays as it was paid."
        ))
    return redirect("payroll:payslip", pk)


@login_required
@require_http_methods(["POST"])
def payslip_adjustment_remove(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    try:
        record = remove_adjustment(actor=request.user, company_id=company_id, adjustment_id=pk)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("payroll:payroll_home")
    messages.success(request, "Line removed. The month's salary was regenerated without it.")
    return redirect("payroll:payslip", record.pk) if record else redirect("payroll:payroll_home")


def rules_summary(rules):
    """The rules in force, as plain sentences for the settings page."""
    Version = PayrollPolicyVersion
    if rules.per_day_method == Version.MonthlyProration.FIXED_DIVISOR:
        per_day = f"Monthly salary ÷ {policy.plain(rules.divisor)} days"
    else:
        per_day = Version.MonthlyProration(rules.per_day_method).label
    incomplete = {
        Decimal("1"): "Paid in full until reviewed",
        Decimal("0.5"): "Paid as a half day",
        Decimal("0"): "Not paid",
    }[rules.incomplete_pay]
    step = policy.plain(rules.rounding_increment)
    if rules.rounding_increment == Decimal("0.01"):
        rounding = "Exact amount, not rounded"
    elif rules.rounding_mode == Version.RoundingMode.UP:
        rounding = f"Rounded up to a multiple of {step}"
    elif rules.rounding_mode == Version.RoundingMode.DOWN:
        rounding = f"Rounded down to a multiple of {step}"
    else:
        rounding = f"Rounded to the nearest {step}"
    return [
        ("One day's pay for absence deductions", per_day),
        ("Absence", Version.AbsenceDeduction(rules.absence_method).label),
        ("A half day pays", f"{policy.plain(rules.half_day_pay * 100)}% of a day"),
        ("A day without a check-out", incomplete),
        (
            "Paid holidays and weekly offs",
            "Monthly staff: not deducted. "
            f"Daily staff: {'paid' if rules.daily_paid_days_off else 'not paid'}. "
            f"Hourly staff: {'paid their shift hours' if rules.hourly_paid_days_off else 'not paid'}.",
        ),
        (
            "Net salary",
            f"{rounding} · {'can go below zero' if rules.allow_negative else 'never below zero'}",
        ),
        (
            "Penalties",
            f"At most {policy.plain(rules.max_penalty_percent)}% of a month's pay, all rules together"
            if rules.max_penalty_percent is not None else "No monthly limit",
        ),
        ("Overtime", overtime_summary(rules)),
    ]


def overtime_summary(rules):
    if not rules.pays_overtime:
        return "Not paid"
    parts = [
        f"Approved overtime pays {policy.plain(rules.overtime_multiplier)}× the hourly rate",
        f"work on a day off {policy.plain(rules.day_off_multiplier)}×",
    ]
    if rules.overtime_minimum:
        parts.append(f"under {rules.overtime_minimum} minutes a day pays none")
    if rules.overtime_step:
        parts.append(
            "paid in whole hours" if rules.overtime_step == 60
            else f"paid in {rules.overtime_step}-minute blocks"
        )
    return " · ".join(parts)


OVERTIME_TABS = (
    ("all", "All"),
    ("waiting", "Waiting for you"),
    ("approved", "Approved"),
    ("rejected", "Rejected"),
    ("too_short", "Too short to pay"),
)
OVERTIME_FILTERS = {
    "waiting": (overtime.WAITING,),
    "approved": overtime.APPROVED_STATES,
    "rejected": (OvertimeDecision.Status.REJECTED,),
    "too_short": (overtime.TOO_SHORT,),
}


def _draft_run_exists(company_id, day):
    first, last = month_bounds(day.year, day.month)
    with use_company(company_id):
        return PayrollRun.objects.filter(
            payroll_period__start_date=first, payroll_period__end_date=last,
            status=PayrollRun.Status.DRAFT,
        ).exists()


@login_required
@require_http_methods(["GET"])
def overtime_list(request):
    """A month's overtime, waiting for a decision or already decided."""
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    year, month = read_month(request.GET)
    show = request.GET.get("show", "all")
    if show not in dict(OVERTIME_TABS):
        show = "all"
    from payroll.table_views import overtime_table
    page = overtime_table(request, company_id=company_id, year=year, month=month,
                          states=OVERTIME_FILTERS.get(show))
    counts = {key: sum(page["counts"].get(state, 0) for state in states)
              for key, states in OVERTIME_FILTERS.items()}
    counts["all"] = sum(page["counts"].values())
    rows = page["rows"]
    return render(request, "payroll/overtime_list.html", {
        **month_context(year, month),
        **page,
        "shown": rows,
        "show": show,
        "tabs": [(key, label, counts[key]) for key, label in OVERTIME_TABS],
        "summary": overtime_summary(page["rules"]),
        "can_manage": page["membership"].role in STRUCTURE_ROLES,
        "company_tz": page["membership"].company.timezone or "UTC",
        "salary_link": page["company_wide"] or may_open(request.user, company_id, "payroll:payroll_home"),
    })


def _calendar_link(request, company_id, company_wide):
    """Link to the attendance calendar (Nihal's) once this viewer may open it."""
    return company_wide or may_open(request.user, company_id, "attendance:attendance_calendar")


def _overtime_list_url(day, show="all"):
    return f"{reverse('payroll:overtime_list')}?month={day.month}&year={day.year}&show={show}"


@login_required
@require_http_methods(["GET", "POST"])
def overtime_decide(request, pk):
    """Approve or reject one day's overtime."""
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    page = overtime.overtime_day(actor=request.user, company_id=company_id, record_id=pk)
    record, claim, decision = page["record"], page["claim"], page["decision"]
    initial = {"minutes": claim.minutes or None}
    if decision is not None:
        initial["note"] = decision.note
        if decision.status == OvertimeDecision.Status.APPROVED and claim.open_from is None:
            initial["minutes"] = decision.approved_minutes
    form = OvertimeDecisionForm(request.POST or None, claim=claim, initial=initial)
    if request.method == "POST" and not page["may_decide"]:
        # A12 part 5: viewing a branch's overtime is not deciding it.
        raise PermissionDenied("You may view this overtime, not decide it.")
    if request.method == "POST" and form.is_valid():
        approve = request.POST.get("decision") == "approve"
        try:
            decision = overtime.decide_overtime(
                actor=request.user, company_id=company_id, record_id=record.pk,
                approve=approve,
                minutes=form.cleaned_data.get("minutes"),
                check_out=form.cleaned_data.get("check_out"),
                note=form.cleaned_data.get("note", ""),
            )
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            name, day = record.employee.full_name, record.work_date
            if approve:
                minutes = decision.approved_minutes
                message = f"Approved {minutes // 60}h {minutes % 60}m of overtime for {name} on {day:%d %b}."
            else:
                message = f"Overtime for {name} on {day:%d %b} rejected; it will not be paid."
            if _draft_run_exists(company_id, day):
                message += f" Generate {day:%B} salary again to include it."
            messages.success(request, message)
            return redirect(_overtime_list_url(day))
    return render(request, "payroll/overtime_decide.html", {
        **page,
        "calendar_link": _calendar_link(request, company_id, page["company_wide"]),
        "form": form,
        "back_url": _overtime_list_url(record.work_date),
        "company_tz": page["membership"].company.timezone or "UTC",
        "day_off_rate": policy.plain(page["rules"].day_off_multiplier),
    })


@login_required
@require_http_methods(["POST"])
def overtime_undo(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    try:
        state = overtime.undo_overtime_decision(
            actor=request.user, company_id=company_id, record_id=pk
        )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("payroll:overtime_decide", pk)
    with use_company(company_id):
        day = AttendanceRecord.objects.filter(pk=pk).values_list("work_date", flat=True).first()
    day = day or timezone.localdate()
    message = (
        "Decision undone; the overtime is waiting for a decision again."
        if state == overtime.WAITING
        else "Decision undone; the overtime is approved automatically again."
    )
    if _draft_run_exists(company_id, day):
        message += f" Generate {day:%B} salary again to include the change."
    messages.success(request, message)
    return redirect(_overtime_list_url(day))


@login_required
@require_http_methods(["GET"])
def payslip(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    # Owner/admin: any payslip, as before. A12 part 6: someone with salary
    # access in the payslip's branch sees it; preparing adds Bonus/Deduction.
    membership, view = salary_branches(request.user, company_id, "salary.view", "salary.prepare")
    _, prepare = salary_branches(request.user, company_id, "salary.prepare")
    if not view:
        raise PermissionDenied("Payslips need owner or company administrator access, or salary access in a branch.")
    with use_company(company_id):
        record = payslip_records().filter(pk=pk).first()
        if record is None or record_branch_id(record) not in view:
            raise PermissionDenied("Payslip not found in your branches.")
        company_wide = view is ALL_BRANCHES
        context = payslip_context(record)
        context["adjustments"] = list(PayrollAdjustment.objects.filter(
            employee=record.employee, target_payroll_period=context["period"],
            status=PayrollAdjustment.Status.ACTIVE,
        ))
        # A finalised month is put right in a later one (A11): what was paid
        # stays paid, and the correction is a line on the next open month.
        from payroll.services import corrections_for

        finalised = record.payroll_run.status == PayrollRun.Status.POSTED
        context["can_correct"] = finalised and (
            prepare is ALL_BRANCHES or record_branch_id(record) in prepare)
        context["corrections"] = list(corrections_for(company_id, record)) if finalised else []
        context["correction_form"] = AdjustmentForm()
        draft = record.payroll_run.status == PayrollRun.Status.DRAFT
        context["can_adjust"] = draft and (
            prepare is ALL_BRANCHES or record_branch_id(record) in prepare)
        status = record.payroll_run.status
        context["adjust_note"] = (
            "This salary is waiting for approval. Send it back on Salary by month to change these lines."
            if status == PayrollRun.Status.SUBMITTED else
            "This salary is finalised. Undo finalise on Salary by month to change these lines."
            if status == PayrollRun.Status.POSTED else
            "Adding lines needs access to prepare salary."
        )
        # Waiving a penalty stays with the owner and company admin - not the
        # payroll manager, who also sees every branch.
        context["can_waive"] = context["can_waive"] and membership.role in STRUCTURE_ROLES
        context["calendar_link"] = _calendar_link(request, company_id, company_wide)
        context["adjustment_form"] = AdjustmentForm()
        # Emailing it: the owner/admin, or whoever may prepare salary in this
        # payslip's branch - the same people who may change its lines.
        from payroll.payslip_email import why_not

        context["can_email"] = prepare is ALL_BRANCHES or record_branch_id(record) in prepare
        context["email_blocked"] = why_not(record, record.employee)
        # Who can fix "email is not set up": Organisation → Email settings.
        context["can_manage_mail"] = membership.role in STRUCTURE_ROLES
        if request.GET.get("format") == "pdf":
            # The payslip's own view, so the download obeys the same rules.
            from payroll.payslip_export import export_payslip

            return export_payslip(request, context)
    return render(request, "payroll/payslip.html", context)


def payslip_records():
    """Payslips with everything the page reads. Call inside the company's context."""
    return PayrollRecord.objects.select_related(
        "employee", "payroll_run__payroll_period",
        "employee_assignment_at_period_end__department",
        "employee_assignment_at_period_end__designation",
        "employee_assignment_at_period_end__branch",
    )


def payslip_context(record, *, for_employee=False):
    """What payroll/payslip.html shows. Call inside the company's context.

    Shared by the company's payslip page and the employee's own (A7);
    ``for_employee`` hides what is the company's to act on (Waive) and tells
    the template it is the employee's page.
    """
    period = record.payroll_run.payroll_period
    days = list(
        AttendanceRecord.objects.select_related("leave_day").filter(
            employee=record.employee,
            work_date__gte=period.start_date, work_date__lte=period.end_date,
        ).order_by("work_date")
    )
    lines = list(record.lines.all())
    penalty_list = list(
        PenaltyAssessment.objects.select_related("penalty_rule", "approved_by")
        .prefetch_related("days__attendance_record")
        .filter(employee=record.employee, payroll_period=period)
        .exclude(status=PenaltyAssessment.Status.REVERSED)
        .order_by("period_start", "pk")
    )
    return {
        "record": record,
        "period": period,
        "assignment": record.employee_assignment_at_period_end,
        "earnings": [line for line in lines if line.line_type == "earning"],
        "deductions": [line for line in lines if line.line_type == "deduction"],
        "counts": summarise(days),
        "days": days,
        "penalties": penalty_list,
        "can_waive": (
            not for_employee and record.payroll_run.status == PayrollRun.Status.DRAFT
        ),
        "for_employee": for_employee,
    }
