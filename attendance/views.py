"""Company attendance pages, read-only and live.

There is no Calculate button. ``attendance.services.refresh`` brings the days
being read up to date first — a day whose close has passed, or one never
written because its shift had not finished — so the page shows the finished
answer rather than whatever was stored last time somebody looked.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from attendance import live_status, month_view
from base_template.tables import paginate, render
from attendance.models import AttendanceRecord
from attendance.services import month_bounds, refresh
from common.tenant import use_company
from employees.models import Employee
from organization.models import Branch
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
    """Attendance → Daily list: one row per employee-day, paged in the database.

    Month, branch, employee and status are the page's own filters and narrow
    the set before the table sees it; the table's search, order and page are
    then applied to that set in SQL (base_template/tables.py), so its counts
    are the real counts, not what happens to be on screen.

    Branch-aware on purpose: every row carries ``branch``, which is the field
    ``access_control.branch_access.scope_queryset`` will narrow on once that
    step is wired in (A12).
    """
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    membership = require_company_membership(request.user, company_id)
    year, month = read_month(request.GET)
    first, last = month_bounds(year, month)
    branch_id = request.GET.get("branch", "").strip()
    employee_id = request.GET.get("employee", "").strip()
    status = request.GET.get("status", "").strip()

    refresh(company_id, start=first, end=last)

    with use_company(company_id):
        queryset = AttendanceRecord.objects.select_related(
            "employee", "branch", "employee_assignment",
        ).filter(work_date__gte=first, work_date__lte=last)
        month_total = queryset.count()
        if branch_id.isdigit():
            queryset = queryset.filter(branch_id=int(branch_id))
        if employee_id.isdigit():
            queryset = queryset.filter(employee_id=int(employee_id))
        if status in dict(AttendanceRecord.AttendanceStatus.choices):
            queryset = queryset.filter(attendance_status=status)
        page = paginate(
            request,
            queryset.order_by("work_date", "employee__first_name", "employee__last_name"),
            search=(
                "employee__first_name", "employee__last_name",
                "employee_assignment__employee_code", "branch__name",
                "attendance_status", "note",
            ),
            order=(
                "work_date",
                ("employee__first_name", "employee__last_name"),
                "branch__name",
                "attendance_status",
                "first_in_at",
                "last_out_at",
                "worked_minutes",
                "late_minutes",
                "payable_fraction",
                "note",
            ),
        )
        employees = Employee.objects.order_by("first_name", "last_name")
        branches = Branch.objects.order_by("name")

    return render(request, "attendance/attendance_list.html", {
        **month_context(year, month),
        "page": page,
        "month_total": month_total,
        "employees": employees,
        "branches": branches,
        "branch_id": branch_id,
        "employee_id": employee_id,
        "status": status,
        "statuses": AttendanceRecord.AttendanceStatus.choices,
        "can_manage": membership.role in STRUCTURE_ROLES,
        "filtered": bool(branch_id or employee_id or status),
        # Punch times are stored in UTC; people read them in company time.
        "company_tz": membership.company.timezone or "UTC",
    })


def _month_steps(year, month):
    """The previous and next month, for the arrows."""
    previous = (year - 1, 12) if month == 1 else (year, month - 1)
    following = (year + 1, 1) if month == 12 else (year, month + 1)
    return previous, following


@login_required
@require_http_methods(["GET"])
def attendance_calendar(request):
    """One employee's month as a planner-style calendar.

    The page picks the employee; the grid itself is an include so the employee
    panel (A7) can render exactly the same month for whoever is logged in.
    """
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    membership = require_company_membership(request.user, company_id)
    year, month = read_month(request.GET)
    company_tz = membership.company.timezone or "UTC"
    requested = request.GET.get("employee", "").strip()

    with use_company(company_id):
        employees = list(Employee.objects.order_by("first_name", "last_name"))
        employee = None
        if requested.isdigit():
            employee = next(
                (e for e in employees if e.pk == int(requested)), None
            )
        # Default to somebody rather than an empty screen: a calendar with no
        # employee chosen has nothing to say.
        if employee is None and employees:
            employee = employees[0]

    first, last = month_bounds(year, month)
    if employee is not None:
        # Live: bring this person's month up to date before drawing it.
        refresh(company_id, employee_ids=[employee.pk], start=first, end=last)

    with use_company(company_id):
        calendar = (
            month_view.build_month(
                employee=employee, year=year, month=month,
                company_timezone=company_tz,
                today=timezone.localdate(),
            )
            if employee is not None
            else None
        )
        has_any_record = AttendanceRecord.objects.exists()

    previous, following = _month_steps(year, month)
    return render(request, "attendance/attendance_calendar.html", {
        **month_context(year, month),
        "calendar": calendar,
        "employee": employee,
        "employees": employees,
        "employee_id": str(employee.pk) if employee else "",
        "has_any_record": has_any_record,
        "can_manage": membership.role in STRUCTURE_ROLES,
        "company_tz": company_tz,
        "previous_year": previous[0], "previous_month": previous[1],
        "next_year": following[0], "next_month": following[1],
    })


@login_required
@require_http_methods(["GET"])
def attendance_now(request):
    """Who is in the office right now, as JSON.

    Polled by the Employees page once a minute. Derived on read from today's
    scans — nothing is stored, so this can be called as often as it likes.
    """
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    require_company_membership(request.user, company_id)

    wanted = request.GET.get("employees", "").strip()
    employee_ids = [
        int(value) for value in wanted.split(",") if value.strip().isdigit()
    ] or None

    statuses = live_status.statuses_for(company_id, employee_ids=employee_ids)
    return JsonResponse({
        "employees": {
            str(employee_id): status.as_dict()
            for employee_id, status in statuses.items()
        },
    })


@login_required
@require_http_methods(["GET"])
def attendance_day(request, employee_id, on):
    """One day's history, rendered as the panel's contents.

    Returned as a fragment rather than JSON so the timeline is built by the
    template like every other list on the site, and the panel still reads
    correctly if the script does not load.
    """
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    membership = require_company_membership(request.user, company_id)
    company_tz = membership.company.timezone or "UTC"

    import datetime as _dt

    try:
        day = _dt.date.fromisoformat(str(on))
    except ValueError:
        day = None
    if day is not None:
        refresh(company_id, employee_ids=[employee_id], start=day, end=day)

    with use_company(company_id):
        record = (
            AttendanceRecord.objects.select_related("shift", "employee")
            .filter(employee_id=employee_id, work_date=on)
            .first()
        )
        if record is None:
            return render(request, "attendance/includes/day_panel.html", {
                "on": on, "detail": None,
            })
        detail = month_view.build_day_detail(
            record=record, company_timezone=company_tz
        )
        detail["employee"] = record.employee
    return render(request, "attendance/includes/day_panel.html", {
        "on": on, "detail": detail,
    })


