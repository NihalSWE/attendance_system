"""The employee panel: the pages an Employee or Branch manager login lands on.

My account, My attendance, My leave and My payslips (plan steps A6, A7).
Everyone signed in to a company may open these; they only ever show the
signed-in person's own record — the employee is always the one linked to the
login, never one named in the URL.
"""

import datetime
from collections import Counter
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.views import PasswordChangeView
from django.http import Http404
from django.db.models import Min
from django.shortcuts import redirect
from base_template.tables import paginate, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from accounts.models import CompanyMembership
from attendance import month_view
from attendance.live_status import statuses_for
from attendance.models import AttendanceRecord
from attendance.services import month_bounds, refresh
from attendance.views import _month_steps, month_context, read_month
from common.forms import StyledFormMixin
from common.tenant import use_company
from employees.models import Employee, EmployeeAssignment
from leaves.models import LeaveDay, LeaveRequest, LeaveType
from leaves.services import allowance_left
from organization.employee_login import ROLE_LABELS
from organization.models import Branch
from payroll.models import PayrollRun
from payroll.views import payslip_context, payslip_records
from scheduling.calendar import WorkCalendar

LEAVE_TAKEN = (LeaveDay.Status.RESERVED, LeaveDay.Status.APPROVED, LeaveDay.Status.CONSUMED)


def _membership(request):
    return (
        CompanyMembership.all_objects.select_related("company")
        .prefetch_related("allowed_branches")
        .filter(company_id=request.company_id, user=request.user,
                status=CompanyMembership.Status.ACTIVE)
        .first()
    )


def _my_employee(request):
    """The employee linked to this login in the current company, or None."""
    if not request.company_id:
        return None
    with use_company(request.company_id):
        return Employee.objects.filter(user=request.user).first()


def _no_employee(request, title):
    """A login without an employee record (a company administrator, say) has no own pages."""
    return render(request, "base_template/me/no_employee.html", {"title": title})


@login_required
def my_account(request):
    if request.user.is_superuser:
        return redirect("platform:company_list")
    if not request.company_id:
        return render(request, "base_template/no_company.html")
    membership = _membership(request)
    today = timezone.localdate()
    employee = placement = shift = None
    now_status = summary = None
    with use_company(request.company_id):
        employee = Employee.objects.filter(user=request.user).first()
        if employee is not None:
            placement = (
                EmployeeAssignment.objects.select_related(
                    "branch", "department", "designation"
                )
                .filter(employee=employee, effective_to__isnull=True)
                .exclude(status__in=["cancelled", "draft"])
                .first()
            )
            calendar = WorkCalendar(request.company_id, today, today)
            shift = calendar.shift_for(
                placement.department_id if placement else None, today, employee_id=employee.pk
            )
    if employee is not None:
        # Today, as the Employees page's "Now" badge sees it (N3), and this
        # month so far, as the calendar counts it (N2).
        now_status = statuses_for(request.company_id, employee_ids=[employee.pk]).get(employee.pk)
        first, last = month_bounds(today.year, today.month)
        refresh(request.company_id, employee_ids=[employee.pk], start=first, end=last)
        with use_company(request.company_id):
            summary = month_view.build_month(
                employee=employee, year=today.year, month=today.month,
                company_timezone=membership.company.timezone or "UTC", today=today,
            )["summary"]
    return render(request, "base_template/me/home.html", {
        "branch_card": _branch_card(request, membership),
        "now_status": now_status,
        "month_summary": summary,
        "month_name": f"{today:%B}",
        "membership": membership,
        "role_label": ROLE_LABELS.get(membership.role, membership.get_role_display()) if membership else "",
        "managed_branches": list(membership.allowed_branches.all()) if membership else [],
        "employee": employee,
        "placement": placement,
        "shift": shift,
        "joined": (
            membership.joined_at.astimezone(ZoneInfo(membership.company.timezone or "UTC"))
            if membership and membership.joined_at else None
        ),
    })


def _branch_card(request, membership):
    """"Your branches" on My account (A12 part 3), or None.

    For a branch manager or a person given access: which branches, and — for
    each permission they hold — a count limited to those branches. Nothing
    here widens access; it only reads within ``branches_for``.
    """
    from access_control.branch_access import ALL_BRANCHES, branches_for
    from access_control.page_access import held_codes, may_open
    from leaves.workflow import reviewable
    from organization.access_services import people

    if membership is None or membership.role not in ("employee", "manager"):
        return None
    codes = held_codes(request.user, request.company_id)
    if not codes:
        return None
    branch_ids = set()
    for code in codes:
        found = branches_for(request.user, request.company_id, code)
        if found is not ALL_BRANCHES:
            branch_ids |= found
    card = {"items": []}
    with use_company(request.company_id):
        card["branches"] = list(Branch.objects.filter(pk__in=branch_ids).order_by("name"))
        if "employees.view" in codes:
            placed = list(people(branches_for(request.user, request.company_id, "employees.view"))
                          .values_list("pk", flat=True))
            card["items"].append({
                "label": "People placed in your branches", "value": len(placed),
                "url": reverse("employee_list") if may_open(
                    request.user, request.company_id, "employee_list") else "",
            })
            if placed:
                statuses = statuses_for(request.company_id, employee_ids=placed)
                card["items"].append({
                    "label": "In the office now",
                    "value": sum(1 for s in statuses.values() if s.key == "in_office"),
                })
        if membership.role == "manager" or "leave.approve" in codes:
            card["items"].append({
                "label": "Leave waiting for your approval",
                "value": reviewable(membership).filter(status="pending").count(),
                "url": reverse("me:leave_inbox"),
            })
    if may_open(request.user, request.company_id, "organization:access"):
        card["access_url"] = reverse("organization:access")
    return card


def _company_tz(request):
    membership = _membership(request)
    return (membership.company.timezone if membership else None) or "UTC"


@login_required
@require_http_methods(["GET"])
def my_attendance(request):
    """My month, as the same planner calendar the company sees (Nihal's N2)."""
    employee = _my_employee(request)
    if employee is None:
        return _no_employee(request, "My attendance")
    year, month = read_month(request.GET)
    first, last = month_bounds(year, month)
    refresh(request.company_id, employee_ids=[employee.pk], start=first, end=last)
    with use_company(request.company_id):
        calendar = month_view.build_month(
            employee=employee, year=year, month=month,
            company_timezone=_company_tz(request), today=timezone.localdate(),
        )
    previous, following = _month_steps(year, month)
    return render(request, "base_template/me/attendance.html", {
        **month_context(year, month),
        "employee": employee,
        "calendar": calendar,
        "day_url_template": reverse("me:attendance_day", args=["0000-00-00"]),
        "previous_year": previous[0], "previous_month": previous[1],
        "next_year": following[0], "next_month": following[1],
    })


@login_required
@require_http_methods(["GET"])
def my_attendance_day(request, on):
    """One of my days, for the calendar's side panel."""
    employee = _my_employee(request)
    if employee is None:
        raise Http404("No employee record for this login.")
    try:
        day = datetime.date.fromisoformat(str(on))
    except ValueError:
        raise Http404("Not a date.")
    refresh(request.company_id, employee_ids=[employee.pk], start=day, end=day)
    with use_company(request.company_id):
        record = (
            AttendanceRecord.objects.select_related("shift", "employee")
            .filter(employee=employee, work_date=day).first()
        )
        detail = None
        if record is not None:
            detail = month_view.build_day_detail(record=record, company_timezone=_company_tz(request))
            detail["employee"] = employee
    return render(request, "attendance/includes/day_panel.html", {"on": on, "detail": detail})


@login_required
@require_http_methods(["GET"])
def my_leave(request):
    """My leave: every application, and the days taken this year by type."""
    employee = _my_employee(request)
    if employee is None:
        return _no_employee(request, "My leave")
    today = timezone.localdate()
    raw_year = request.GET.get("year", "").strip()
    year = int(raw_year) if raw_year.isdigit() and 2000 <= int(raw_year) <= 2100 else today.year
    with use_company(request.company_id):
        requests = paginate(request,
            LeaveRequest.objects.prefetch_related("segments__leave_type", "segments__days")
            .filter(employee=employee, segments__start_date__lte=datetime.date(year, 12, 31),
                    segments__end_date__gte=datetime.date(year, 1, 1))
            .annotate(table_date=Min("segments__start_date"), table_type=Min("segments__leave_type__name"))
            .distinct().order_by("-pk"),
            search=("table_type", "status", "reason"),
            order=("table_date", "table_type", None, None, "status", "reason", None))
        taken = Counter()
        for name, pay_type, units in LeaveDay.objects.filter(
            employee=employee, status__in=LEAVE_TAKEN,
            work_date__gte=datetime.date(year, 1, 1), work_date__lte=datetime.date(year, 12, 31),
        ).values_list("request_segment__leave_type__name", "approved_pay_type", "balance_units"):
            taken[(name, pay_type)] += units or Decimal("1")
        allowances = []
        for leave_type in LeaveType.objects.filter(
            status="active", days_per_year__isnull=False
        ).order_by("name"):
            left = allowance_left(employee, leave_type, year)
            allowances.append({"type": leave_type.name, "allowance": leave_type.days_per_year,
                               "used": leave_type.days_per_year - left, "left": left})
    rows = []
    for leave in requests:
        segments = [s for s in leave.segments.all()]
        days = sum(
            day.balance_units for segment in segments for day in segment.days.all()
            if day.status in LEAVE_TAKEN
        )
        if leave.status in ('pending', 'rejected'):
            days = sum(segment.requested_units for segment in segments)
        rows.append({
            "request": leave,
            "segments": segments,
            "first": min((s.start_date for s in segments), default=None),
            "last": max((s.end_date for s in segments), default=None),
            "days": days,
        })
    return render(request, "base_template/me/leave.html", {
        "employee": employee,
        "rows": rows,
        "allowances": allowances,
        "taken": [
            {"type": name, "pay_type": pay_type, "days": days}
            for (name, pay_type), days in sorted(taken.items())
        ],
        "year": year,
        "years": list(range(today.year + 1, today.year - 5, -1)),
    })


def _my_payslips(employee):
    """My payslips: finalised months only. A draft can still change, so it is
    the company's to check before anybody reads it as their salary."""
    return payslip_records().filter(
        employee=employee, payroll_run__status=PayrollRun.Status.POSTED
    ).order_by("-payroll_run__payroll_period__start_date")


@login_required
@require_http_methods(["GET"])
def my_payslips(request):
    employee = _my_employee(request)
    if employee is None:
        return _no_employee(request, "My payslips")
    with use_company(request.company_id):
        payslips = paginate(request, _my_payslips(employee),
            search=("payroll_run__payroll_period__name",),
            order=("payroll_run__payroll_period__start_date", "gross_earnings", "total_deductions", "net_pay", None))
    return render(request, "base_template/me/payslips.html", {
        "employee": employee, "payslips": payslips,
    })


@login_required
@require_http_methods(["GET"])
def my_payslip(request, pk):
    employee = _my_employee(request)
    if employee is None:
        raise Http404("No employee record for this login.")
    with use_company(request.company_id):
        record = _my_payslips(employee).filter(pk=pk).first()
        if record is None:
            # Somebody else's, or a draft: either way not this person's to see.
            raise Http404("Payslip not found.")
        context = payslip_context(record, for_employee=True)
    return render(request, "payroll/payslip.html", context)


class MyPasswordForm(StyledFormMixin, PasswordChangeForm):
    """Django's own change-password form, in the project's field style."""


class MyPasswordView(PasswordChangeView):
    form_class = MyPasswordForm
    template_name = "base_template/me/password.html"
    success_url = reverse_lazy("me:home")

    def form_valid(self, form):
        messages.success(self.request, "Password changed.")
        return super().form_valid(form)
