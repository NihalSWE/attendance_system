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

    decides_overtime = membership.role in overtime.OVERTIME_ROLES
    return render(request, "payroll/payroll_home.html", {
        **month_context(year, month),
        "run": run,
        "records": records,
        "can_manage": membership.role in STRUCTURE_ROLES,
        "decides_overtime": decides_overtime,
        "overtime_waiting": (
            overtime.undecided_count(company_id, first, last) if decides_overtime else 0
        ),
        # Decisions made since the draft was generated are not in it yet.
        "overtime_since_run": (
            overtime.decided_after(company_id, first, last, run.calculation_finished_at)
            if run and run.status == PayrollRun.Status.DRAFT else 0
        ),
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
            "rounded down to whole hours" if rules.overtime_step == 60
            else f"rounded down to {rules.overtime_step}-minute blocks"
        )
    return " · ".join(parts)


OVERTIME_TABS = (
    ("waiting", "Waiting"),
    ("approved", "Approved"),
    ("rejected", "Rejected"),
    ("all", "All"),
)


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
    show = request.GET.get("show", "waiting")
    if show not in dict(OVERTIME_TABS):
        show = "waiting"
    page = overtime.overtime_month(actor=request.user, company_id=company_id, year=year, month=month)
    rows = page["rows"] if show == "all" else [row for row in page["rows"] if row.state == show]
    return render(request, "payroll/overtime_list.html", {
        **month_context(year, month),
        **page,
        "shown": rows,
        "show": show,
        "tabs": [
            (key, label, len(page["rows"]) if key == "all" else page["counts"].get(key, 0))
            for key, label in OVERTIME_TABS
        ],
        "summary": overtime_summary(page["rules"]),
        "can_manage": page["membership"].role in STRUCTURE_ROLES,
        "company_tz": page["membership"].company.timezone or "UTC",
    })


def _overtime_list_url(day, show="waiting"):
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
        overtime.undo_overtime_decision(actor=request.user, company_id=company_id, record_id=pk)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("payroll:overtime_decide", pk)
    with use_company(company_id):
        day = AttendanceRecord.objects.filter(pk=pk).values_list("work_date", flat=True).first()
    day = day or timezone.localdate()
    message = "Decision undone; the overtime is waiting again."
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
