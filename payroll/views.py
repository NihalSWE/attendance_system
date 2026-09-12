"""Company salary pages: generate a month's salary and read payslips."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from attendance.models import AttendanceRecord
from attendance.services import month_bounds
from attendance.views import month_context, read_month
from common.tenant import use_company
from organization.services import (
    STRUCTURE_ROLES,
    require_company_membership,
    require_structure_manager,
)
from organization.views import _company_or_redirect
from payroll.models import PayrollPeriod, PayrollRecord, PayrollRun
from payroll.services import generate_payroll, summarise


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
    return render(request, "payroll/payslip.html", {
        "record": record,
        "period": period,
        "assignment": record.employee_assignment_at_period_end,
        "earnings": [line for line in lines if line.line_type == "earning"],
        "deductions": [line for line in lines if line.line_type == "deduction"],
        "counts": summarise(days),
        "days": days,
    })
