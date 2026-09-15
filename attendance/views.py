"""Company attendance pages, live — and the one place a day is fixed by hand.

There is no Calculate button. ``attendance.services.refresh`` brings the days
being read up to date first — a day whose close has passed, or one never
written because its shift had not finished — so the page shows the finished
answer rather than whatever was stored last time somebody looked.
"""

import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from attendance import correction_services, live_status, month_view
from attendance.forms import (
    AcceptReviewForm,
    AddScanForm,
    ChangeStatusForm,
    WithdrawForm,
)
from attendance.models import AttendanceCorrection, AttendanceRecord
from attendance.services import _is_locked, locked_ranges, month_bounds, refresh
from base_template.tables import paginate, render
from common.forms import apply_service_errors
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
            # Only offer a fix for somebody who is this company's employee: the
            # id comes from the URL.
            return render(request, "attendance/includes/day_panel.html", {
                "on": on, "detail": None,
                "may_correct": (
                    correction_services.may_correct(request.user, company_id)
                    and Employee.objects.filter(pk=employee_id).exists()
                ),
                "employee_id": employee_id,
            })
        detail = month_view.build_day_detail(
            record=record, company_timezone=company_tz
        )
        detail["employee"] = record.employee
    return render(request, "attendance/includes/day_panel.html", {
        "on": on, "detail": detail,
        "may_correct": correction_services.may_correct(request.user, company_id),
        "employee_id": employee_id,
    })


# --------------------------------------------------------------------------
# Fixing a day, and the days waiting for review (plan step N5)
# --------------------------------------------------------------------------

FIX_ACTIONS = {
    "add_scan": AddScanForm,
    "change_status": ChangeStatusForm,
    "accept_review": AcceptReviewForm,
}


def _fix_url(employee_id, day):
    return reverse("attendance:attendance_day_fix", args=[employee_id, day.isoformat()])


def _parse_day(on):
    import datetime as _dt

    try:
        return _dt.date.fromisoformat(str(on))
    except ValueError:
        return None


@login_required
@require_http_methods(["GET", "POST"])
def attendance_day_fix(request, employee_id, on):
    """One employee-day: what it is now, and the three ways to fix it."""
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    membership = correction_services.require_corrector(request.user, company_id)
    day = _parse_day(on)
    if day is None:
        messages.error(request, "That is not a date.")
        return redirect("attendance:attendance_review")

    with use_company(company_id):
        employee = Employee.objects.filter(pk=employee_id).first()
    if employee is None:
        raise PermissionDenied("Employee not found in this company.")

    forms = {
        "add_scan": AddScanForm(day=day, prefix="scan"),
        "change_status": ChangeStatusForm(prefix="status"),
        "accept_review": AcceptReviewForm(prefix="accept"),
    }
    if request.method == "POST":
        action = request.POST.get("action", "")
        form_class = FIX_ACTIONS.get(action)
        if form_class is None:
            messages.error(request, "Choose what to fix.")
            return redirect(_fix_url(employee_id, day))
        prefix = {"add_scan": "scan", "change_status": "status", "accept_review": "accept"}[action]
        kwargs = {"day": day} if action == "add_scan" else {}
        form = form_class(request.POST, prefix=prefix, **kwargs)
        forms[action] = form
        if form.is_valid():
            data = form.cleaned_data
            common = {
                "actor": request.user, "company_id": company_id,
                "employee_id": employee_id, "work_date": day, "reason": data["reason"],
            }
            try:
                if action == "add_scan":
                    correction_services.add_scan(at=data["at"], **common)
                    done = "Scan added. The day has been worked out again with it."
                elif action == "change_status":
                    correction_services.change_status(status=data["status"], **common)
                    done = "Status changed. The day now counts as marked."
                else:
                    correction_services.accept_review(**common)
                    done = "Accepted. The day no longer needs a review."
            except ValidationError as exc:
                apply_service_errors(form, exc)
            else:
                messages.success(request, done)
                return redirect(_fix_url(employee_id, day))

    refresh(company_id, employee_ids=[employee_id], start=day, end=day)
    company_tz = membership.company.timezone or "UTC"
    with use_company(company_id):
        record = (
            AttendanceRecord.objects.select_related("shift", "employee")
            .filter(employee_id=employee_id, work_date=day).first()
        )
        detail = (
            month_view.build_day_detail(record=record, company_timezone=company_tz)
            if record is not None else None
        )
    corrections = correction_services.corrections_for_day(company_id, employee_id, day)
    locked = _is_locked(day, locked_ranges(company_id))
    can_change_status = (
        record is not None and not record.is_open and not record.leave_day_id
        and record.attendance_status in correction_services.CORRECTABLE_DAY_STATUSES
    )
    return render(request, "attendance/day_fix.html", {
        "employee": employee,
        "day": day,
        "record": record,
        "detail": detail,
        "forms": forms,
        "corrections": corrections,
        "withdraw_form": WithdrawForm(),
        "company_tz": company_tz,
        "locked": locked,
        "can_change_status": can_change_status,
        "is_rule_check_out": record is not None and record.review_status == "needs_review"
        and correction_services.is_rule_check_out(record),
        "is_open_overtime": record is not None and record.review_status == "needs_review"
        and correction_services.is_open_overtime(record),
        "calendar_url": (
            reverse("attendance:attendance_calendar")
            + f"?employee={employee_id}&year={day.year}&month={day.month}"
        ),
    })


@require_POST
@login_required
def attendance_correction_withdraw(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    correction_services.require_corrector(request.user, company_id)
    with use_company(company_id):
        correction = AttendanceCorrection.objects.filter(pk=pk).first()
    if correction is None:
        raise PermissionDenied("Correction not found in this company.")
    form = WithdrawForm(request.POST)
    form.is_valid()
    try:
        correction_services.withdraw(
            actor=request.user, company_id=company_id, correction_id=pk,
            note=form.cleaned_data.get("note", ""),
        )
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    else:
        messages.success(request, "Withdrawn. The day has been worked out again without it.")
    return redirect(_fix_url(correction.employee_id, correction.work_date))


@login_required
@require_http_methods(["GET"])
def attendance_review(request):
    """Days that closed without a clear answer and need a person."""
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    membership = correction_services.require_corrector(request.user, company_id)
    # Bring this month and last up to date first: a day that has closed since
    # anybody last looked is exactly the kind that lands here.
    today = timezone.now().astimezone(month_view.zone(membership.company.timezone)).date()
    last_month_start = (today.replace(day=1) - datetime.timedelta(days=1)).replace(day=1)
    refresh(company_id, start=last_month_start, end=today)
    rows = correction_services.review_queue(company_id)
    paginator = Paginator(rows, 25)
    page = paginator.get_page(request.GET.get("page"))
    return render(request, "attendance/review_list.html", {
        "page": page,
        "total": paginator.count,
        "rule_check_out": correction_services.RULE_CHECK_OUT,
        "open_overtime": correction_services.OPEN_OVERTIME,
        "company_tz": membership.company.timezone or "UTC",
    })
