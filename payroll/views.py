"""Company salary pages: generate a month's salary, read payslips, and the
company's salary settings."""

import datetime
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from attendance.models import AttendanceRecord
from attendance.services import month_bounds
from attendance.views import month_context, read_month
from common.forms import apply_service_errors
from common.tenant import use_company
from organization.services import (
    STRUCTURE_ROLES,
    require_company_membership,
    require_structure_manager,
)
from organization.views import _company_or_redirect
from payroll import penalties, policy
from payroll.forms import (
    GeneralSettingsForm,
    PenaltyRuleForm,
    SalaryRulesForm,
    StopPenaltyRuleForm,
)
from payroll.models import (
    AttendancePenaltyRule,
    PayrollPeriod,
    PayrollPolicyVersion,
    PayrollRecord,
    PayrollRun,
    PenaltyAssessment,
)
from payroll.services import generate_payroll, summarise, waive_penalty


@login_required
@require_http_methods(["GET"])
def payroll_home(request):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    membership = require_company_membership(request.user, company_id)
    year, month = read_month(request.GET)
    first, last = month_bounds(year, month)

    with use_company(company_id):
        period = PayrollPeriod.objects.filter(start_date=first, end_date=last).first()
        run = (
            PayrollRun.objects.filter(payroll_period=period).order_by("-pk").first()
            if period else None
        )
        records = (
            list(
                run.records.select_related("employee")
                .prefetch_related("lines")
                .order_by("employee__first_name", "employee__last_name")
            )
            if run else []
        )

    return render(request, "payroll/payroll_home.html", {
        **month_context(year, month),
        "run": run,
        "records": records,
        "can_manage": membership.role in STRUCTURE_ROLES,
    })


@login_required
@require_http_methods(["POST"])
def payroll_generate(request):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    year, month = read_month(request.POST)
    try:
        run = generate_payroll(
            actor=request.user, company_id=company_id, year=year, month=month
        )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        skipped = run.totals_snapshot.get("skipped_without_salary") or []
        message = (
            f"Salary generated for {run.totals_snapshot['employees']} employee(s). "
            "It is a draft and can be regenerated at any time."
        )
        if skipped:
            message += f" Skipped, no salary set: {', '.join(skipped)}."
        messages.success(request, message)
    return redirect(f"{reverse('payroll:payroll_home')}?month={month}&year={year}")


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
        "penalty_rules": _penalty_rows(company_id, today, page["currency"]),
    })


def _penalty_rows(company_id, today, currency):
    """Penalty rules in use now or saved for a later month, as table rows."""
    Rule = AttendancePenaltyRule
    with use_company(company_id):
        versions = list(
            Rule.objects.filter(status=Rule.Status.ACTIVE)
            .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=today))
            .order_by("name", "effective_from")
        )
    newest = {}
    for rule in versions:
        newest[rule.code] = rule  # ordered by start: the last one is the newest
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
            "can_change": newest[rule.code] is rule,
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
        ("One day of a monthly salary", per_day),
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
    ]


@login_required
@require_http_methods(["GET"])
def payslip(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    require_structure_manager(request.user, company_id)
    with use_company(company_id):
        record = (
            PayrollRecord.objects.select_related(
                "employee", "payroll_run__payroll_period",
                "employee_assignment_at_period_end__department__department",
                "employee_assignment_at_period_end__designation__designation",
                "employee_assignment_at_period_end__branch",
            )
            .filter(pk=pk).first()
        )
        if record is None:
            raise PermissionDenied("Payslip not found in this company.")
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
    return render(request, "payroll/payslip.html", {
        "record": record,
        "period": period,
        "assignment": record.employee_assignment_at_period_end,
        "earnings": [line for line in lines if line.line_type == "earning"],
        "deductions": [line for line in lines if line.line_type == "deduction"],
        "counts": summarise(days),
        "days": days,
        "penalties": penalty_list,
        "can_waive": record.payroll_run.status == PayrollRun.Status.DRAFT,
    })
