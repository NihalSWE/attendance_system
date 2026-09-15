"""An employee's history, and ending their employment (plan step N6)."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from attendance.models import AttendanceRecord
from common.forms import apply_service_errors
from organization import employee_detail_services as services
from organization.employee_detail_forms import EndEmploymentForm
from organization.views import _company_or_redirect

#: The month summary, in the order a person reads it.
MONTH_ROWS = (
    (AttendanceRecord.AttendanceStatus.PRESENT, "Present"),
    (AttendanceRecord.AttendanceStatus.HALF_DAY, "Half day"),
    (AttendanceRecord.AttendanceStatus.ABSENT, "Absent"),
    (AttendanceRecord.AttendanceStatus.INCOMPLETE, "No check-out"),
    (AttendanceRecord.AttendanceStatus.LEAVE, "Leave"),
    (AttendanceRecord.AttendanceStatus.WEEKLY_OFF, "Weekly off"),
    (AttendanceRecord.AttendanceStatus.HOLIDAY, "Holiday"),
)


@login_required
@require_http_methods(["GET"])
def employee_detail(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    page = services.employee_history(actor=request.user, company_id=company_id, employee_id=pk)
    counts = page["month_counts"]
    today = page["today"]
    return render(request, "organization/employee_detail.html", {
        **page,
        "month_rows": [(label, counts.get(status, 0)) for status, label in MONTH_ROWS],
        "calendar_url": (
            reverse("attendance:attendance_calendar")
            + f"?employee={pk}&year={today.year}&month={today.month}"
        ),
    })


@login_required
@require_http_methods(["GET", "POST"])
def employee_end(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    page = services.employee_history(actor=request.user, company_id=company_id, employee_id=pk)
    employee = page["employee"]
    if page["is_ended"]:
        messages.info(request, f"{employee.full_name} has already left.")
        return redirect("organization:employee_detail", pk=employee.pk)

    form = EndEmploymentForm(
        request.POST or None,
        initial={"last_day": page["today"], "disable_login": True, "end_device_enrollments": True},
    )
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            employee, summary = services.end_employment(
                actor=request.user, company_id=company_id, employee_id=employee.pk,
                last_day=data["last_day"], status=data["status"], reason=data["reason"],
                disable_login=data["disable_login"],
                end_device_enrollments=data["end_device_enrollments"],
            )
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            parts = [f"{employee.full_name}'s employment ended on {data['last_day']:%d %b %Y}."]
            if summary["enrollments_ended"]:
                parts.append(
                    f"{summary['enrollments_ended']} device enrollment"
                    f"{'s' if summary['enrollments_ended'] != 1 else ''} ended."
                )
            if summary["login_disabled"]:
                parts.append("Their login is disabled.")
            messages.success(request, " ".join(parts))
            return redirect("organization:employee_detail", pk=employee.pk)

    active_devices = [d for d in page["devices"] if d.is_current]
    return render(request, "organization/employee_end.html", {
        **page,
        "form": form,
        "active_devices": active_devices,
        "has_active_login": bool(page["login"] and page["login"].status == "active"),
    })
