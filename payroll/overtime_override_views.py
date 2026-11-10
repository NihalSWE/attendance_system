"""Salary by month/week: replace one person's cycle overtime totals."""

import calendar
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import urlencode

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from common.forms import StyledFormMixin, apply_service_errors
from organization.views import _company_or_redirect
from payroll import overtime_overrides, periods
from payroll.models import PayrollRun
from payroll.policy import rules_for
from payroll.services import _overtime_lines, period_records
from payroll.views import overtime_summary


class CycleOvertimeForm(StyledFormMixin, forms.Form):
    hours = forms.DecimalField(label="Payable overtime hours for this cycle", min_value=0,
                               max_digits=7, decimal_places=2)
    day_off_hours = forms.DecimalField(label="Payable holiday / weekly-off hours", min_value=0,
                                       max_digits=7, decimal_places=2)
    reason = forms.CharField(label="Reason", max_length=255, widget=forms.Textarea(attrs={"rows": 3}))


@login_required
@require_http_methods(["GET", "POST"])
def cycle_overtime(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    record = overtime_overrides.record_for(request.user, company_id, pk)
    run, period = record.payroll_run, record.payroll_run.payroll_period
    query = {"year": period.end_date.year, "month": period.end_date.month}
    if run.pay_group_id:
        query.update(group=run.pay_group_id, week=period.start_date.isoformat())
    back = f"{reverse('payroll:payroll_home')}?{urlencode(query)}"
    current = overtime_overrides.active(company_id, period, run.pay_group, [record.employee_id]).get(record.employee_id)
    rules = rules_for(company_id, period.start_date)
    found, _ = period_records(company_id, period.start_date, period.end_date, run.pay_group,
                              employee_ids=[record.employee_id])
    days = next((days for employee, days in found.items() if employee.pk == record.employee_id), [])
    snapshot = record.calculation_snapshot
    basis, rate = snapshot["pay_basis"], Decimal(snapshot["base_rate"])
    count = (calendar.monthrange(period.end_date.year, period.end_date.month)[1] if run.pay_group_id
             else periods.full_days(company_id, period.end_date.year, period.end_date.month))
    automatic, _ = _overtime_lines(basis, rate, rules, days, count)
    # One hour of each category shows exactly which rates manual totals use.
    rate_days = [day for day in days if day.attendance_status != "inactive"]
    rates, _ = _overtime_lines(basis, rate, rules, rate_days, count,
                               SimpleNamespace(hours=Decimal(1), day_off_hours=Decimal(1))) if rate_days else ([], [])
    quantities = {line[1]: line[3] for line in automatic}
    form = CycleOvertimeForm(request.POST if request.method == "POST" else None, initial={
        "hours": current.hours if current else quantities.get("OVERTIME", 0),
        "day_off_hours": current.day_off_hours if current else quantities.get("DAY_OFF_WORK", 0),
        "reason": "",
    })
    if request.method == "POST":
        reverting = request.POST.get("action") == "revert"
        if reverting or form.is_valid():
            try:
                values = {"reason": request.POST.get("reason", "")} if reverting else form.cleaned_data
                overtime_overrides.save(actor=request.user, company_id=company_id, record_id=pk,
                                         revert=reverting, **values)
            except ValidationError as exc:
                apply_service_errors(form, exc)
            else:
                messages.success(request, "Overtime now follows attendance. Regenerate this salary cycle to update the pay."
                                 if reverting else "Cycle overtime hours saved. Regenerate this salary cycle to update the pay.")
                return redirect(back)
    return render(request, "payroll/cycle_overtime.html", {
        "record": record, "period": period, "form": form, "current": current,
        "history": overtime_overrides.history(company_id, record), "back_url": back,
        "automatic": automatic, "rates": rates, "rules_summary": overtime_summary(rules),
        "editable": run.status == PayrollRun.Status.DRAFT,
    })
