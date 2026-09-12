"""Company attendance page: calculate a month and review it (read-only)."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from attendance.models import AttendanceRecord
from attendance.services import calculate_attendance, month_bounds
from common.tenant import use_company
from employees.models import Employee
from organization.services import STRUCTURE_ROLES, require_company_membership
from organization.views import _company_or_redirect

MONTHS = [
    (1, "January"), (2, "February"), (3, "March"), (4, "April"), (5, "May"),
    (6, "June"), (7, "July"), (8, "August"), (9, "September"), (10, "October"),
    (11, "November"), (12, "December"),
]


def read_month(source):
    """Year and month from a GET/POST dict, defaulting to the current month."""
    today = timezone.localdate()
    raw_year, raw_month = str(source.get("year", "")), str(source.get("month", ""))
    year = int(raw_year) if raw_year.isdigit() and 2000 <= int(raw_year) <= 2100 else today.year
    month = int(raw_month) if raw_month.isdigit() and 1 <= int(raw_month) <= 12 else today.month
    return year, month


def month_context(year, month):
    today = timezone.localdate()
    return {
        "year": year,
        "month": month,
        "month_name": dict(MONTHS)[month],
        "months": MONTHS,
        "years": list(range(today.year + 1, today.year - 5, -1)),
    }


@login_required
@require_http_methods(["GET"])
def attendance_list(request):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    membership = require_company_membership(request.user, company_id)
    year, month = read_month(request.GET)
    first, last = month_bounds(year, month)
    employee_id = request.GET.get("employee", "").strip()
    status = request.GET.get("status", "").strip()

    with use_company(company_id):
        queryset = AttendanceRecord.objects.select_related("employee", "branch").filter(
            work_date__gte=first, work_date__lte=last
        )
        total = queryset.count()
        if employee_id.isdigit():
            queryset = queryset.filter(employee_id=int(employee_id))
        if status in dict(AttendanceRecord.AttendanceStatus.choices):
            queryset = queryset.filter(attendance_status=status)
        filtered_total = queryset.count()
        page = Paginator(
            queryset.order_by("work_date", "employee__first_name"), 50
        ).get_page(request.GET.get("page"))
        employees = Employee.objects.order_by("first_name", "last_name")

    return render(request, "attendance/attendance_list.html", {
        **month_context(year, month),
        "page": page,
        "total": total,
        "filtered_total": filtered_total,
        "employees": employees,
        "employee_id": employee_id,
        "status": status,
        "statuses": AttendanceRecord.AttendanceStatus.choices,
        "can_manage": membership.role in STRUCTURE_ROLES,
        # Punch times are stored in UTC; people read them in company time.
        "company_tz": membership.company.timezone or "UTC",
    })


@login_required
@require_http_methods(["POST"])
def attendance_calculate(request):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    year, month = read_month(request.POST)
    try:
        summary = calculate_attendance(
            actor=request.user, company_id=company_id, year=year, month=month
        )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(
            request,
            f"Attendance calculated for {dict(MONTHS)[month]} {year}: "
            f"{summary['employees']} employee(s).",
        )
    return redirect(f"{reverse('attendance:attendance_list')}?month={month}&year={year}")
