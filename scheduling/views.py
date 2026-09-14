"""Company pages for the working calendar.

Thin adapters: every authorization decision and every write happens in
``scheduling.services``, so a future API or background job enforces the same
rules without re-implementing them.
"""

import calendar
import datetime
from collections import defaultdict

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from common.choices import ActiveStatus
from common.forms import apply_service_errors
from common.tenant import use_company
from organization.services import (
    STRUCTURE_ROLES,
    require_company_membership,
    require_structure_manager,
    visible_branches,
)
from organization.views import _company_or_redirect
from scheduling import services
from organization.models import CompanyDepartment
from scheduling.forms import (
    AttendanceSettingsForm,
    ChangeWeeklyOffStartForm,
    DepartmentShiftForm,
    EndWeeklyOffForm,
    HolidayForm,
    HolidayYearForm,
    ShiftForm,
    ShiftStatusForm,
    WeeklyOffForm,
)
from scheduling.calendar import WEEKLY_OFF, WorkCalendar
from scheduling.models import CompanyAttendanceSettings, Holiday, Shift, WeeklyOffRule


def _form_page(request, *, form, title, submit_label, action, success, explanation=""):
    """Shared POST handling: validate the form, call the service, report back."""
    if request.method == "POST" and form.is_valid():
        try:
            action(form.cleaned_data)
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, success)
            return redirect("scheduling:schedule_overview")
    return render(request, "scheduling/form.html", {
        "form": form,
        "title": title,
        "submit_label": submit_label,
        "explanation": explanation,
    })


# --------------------------------------------------------------------------
# Overview
# --------------------------------------------------------------------------

@login_required
@require_http_methods(["GET"])
def schedule_overview(request):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    membership = require_company_membership(request.user, company_id)

    with use_company(company_id):
        settings = (
            CompanyAttendanceSettings.objects.select_related("company_shift").first()
        )
        shifts = list(Shift.objects.order_by("status", "name"))
        weekly_offs = list(
            WeeklyOffRule.objects.select_related("branch").order_by(
                "status", "weekday", "branch__name"
            )
        )
        today = timezone.localdate()
        upcoming = list(
            Holiday.objects.select_related("branch")
            .filter(status=Holiday.Status.ACTIVE, holiday_date__gte=today)
            .order_by("holiday_date")[:5]
        )
        holiday_count = Holiday.objects.filter(
            status=Holiday.Status.ACTIVE, holiday_date__year=today.year
        ).count()

        by_department = (
            settings is not None
            and settings.shift_mode == CompanyAttendanceSettings.ShiftMode.DEPARTMENT_SHIFTS
        )
        current = services.current_department_shifts(company_id, today)
        department_rows = [
            {
                "department": adoption,
                "link": current.get(adoption.pk),
            }
            for adoption in CompanyDepartment.objects.select_related("branch", "department")
            .filter(status=ActiveStatus.ACTIVE)
            .order_by("branch__name", "department__name")
        ]

    company_shift = settings.company_shift if settings else None
    uncovered = [
        row["department"] for row in department_rows
        if row["link"] is None and company_shift is None
    ] if by_department else []
    ready = bool(company_shift) if not by_department else not uncovered and (
        bool(company_shift) or any(row["link"] for row in department_rows)
    )
    return render(request, "scheduling/overview.html", {
        "settings": settings,
        "ready": ready,
        "shifts": shifts,
        "weekly_offs": weekly_offs,
        "upcoming": upcoming,
        "holiday_count": holiday_count,
        "year": today.year,
        "by_department": by_department,
        "department_rows": department_rows,
        "uncovered": uncovered,
        "can_manage": membership.role in STRUCTURE_ROLES,
    })


# --------------------------------------------------------------------------
# Attendance settings
# --------------------------------------------------------------------------

@login_required
@require_http_methods(["GET", "POST"])
def attendance_settings_edit(request):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    require_structure_manager(request.user, company_id)
    settings = services.get_attendance_settings(company_id)
    with use_company(company_id):
        shifts = Shift.objects.filter(status=ActiveStatus.ACTIVE).order_by("name")
        form = AttendanceSettingsForm(
            request.POST or None, instance=settings, shifts=shifts
        )
        return _form_page(
            request,
            form=form,
            title="Attendance settings",
            submit_label="Save settings",
            success="Attendance settings saved.",
            explanation=(
                "Shifts per department: each employee works their department's "
                "shift, and the company shift covers departments without one. "
                "One shift for the company: everyone works the company shift."
            ),
            action=lambda data: services.update_attendance_settings(
                actor=request.user, company_id=company_id, values=data
            ),
        )


# --------------------------------------------------------------------------
# Shifts
# --------------------------------------------------------------------------

@login_required
@require_http_methods(["GET", "POST"])
def shift_create(request):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    require_structure_manager(request.user, company_id)
    with use_company(company_id):
        form = ShiftForm(request.POST or None)
        return _form_page(
            request,
            form=form,
            title="Add shift",
            submit_label="Add shift",
            success="Shift added.",
            action=lambda data: services.create_shift(
                actor=request.user, company_id=company_id, values=data
            ),
        )


@login_required
@require_http_methods(["GET", "POST"])
def shift_edit(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    _, shift = services.get_shift_for_edit(
        actor=request.user, company_id=company_id, shift_id=pk
    )
    with use_company(company_id):
        form = ShiftForm(request.POST or None, instance=shift)
        return _form_page(
            request,
            form=form,
            title=f"Edit {shift.name}",
            submit_label="Save shift",
            success="Shift saved.",
            explanation=(
                "Changing a shift changes how attendance is calculated from now "
                "on. Recalculate a month to apply it to past days."
            ),
            action=lambda data: services.update_shift(
                actor=request.user, company_id=company_id, shift_id=shift.pk, values=data
            ),
        )


@login_required
@require_http_methods(["GET", "POST"])
def shift_status(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    _, shift = services.get_shift_for_edit(
        actor=request.user, company_id=company_id, shift_id=pk
    )
    form = ShiftStatusForm(request.POST or None, initial={"status": shift.status})
    return _form_page(
        request,
        form=form,
        title=f"Change status of {shift.name}",
        submit_label="Save status",
        success="Shift status saved.",
        explanation=(
            "Shifts are never deleted, so past attendance keeps the shift it was "
            "measured against."
        ),
        action=lambda data: services.set_shift_status(
            actor=request.user, company_id=company_id, shift_id=shift.pk,
            status=data["status"],
        ),
    )


@login_required
@require_http_methods(["GET", "POST"])
def department_shift_set(request):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    membership = require_structure_manager(request.user, company_id)
    preset = request.GET.get("department", "").strip()
    with use_company(company_id):
        branches = visible_branches(membership)
        form = DepartmentShiftForm(
            request.POST or None,
            departments=CompanyDepartment.objects.select_related("branch", "department")
            .filter(status=ActiveStatus.ACTIVE, branch__in=branches)
            .order_by("branch__name", "department__name"),
            shifts=Shift.objects.filter(status=ActiveStatus.ACTIVE).order_by("name"),
            initial={
                "effective_from": timezone.localdate(),
                **({"department": preset} if preset.isdigit() else {}),
            },
        )
        return _form_page(
            request,
            form=form,
            title="Set department shift",
            submit_label="Save department shift",
            success="Department shift saved.",
            explanation=(
                "Everyone in the department works this shift from the date you "
                "choose. Earlier days keep the shift they were worked on."
            ),
            action=lambda data: services.set_department_shift(
                actor=request.user, company_id=company_id, values=data
            ),
        )


# --------------------------------------------------------------------------
# Weekly off days
# --------------------------------------------------------------------------

@login_required
@require_http_methods(["GET", "POST"])
def weekly_off_create(request):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    membership = require_structure_manager(request.user, company_id)
    with use_company(company_id):
        form = WeeklyOffForm(
            request.POST or None,
            branches=visible_branches(membership).order_by("name"),
            initial={"effective_from": timezone.localdate()},
        )
        return _form_page(
            request,
            form=form,
            title="Add weekly off days",
            submit_label="Add weekly off days",
            success="Weekly off days added.",
            action=lambda data: services.add_weekly_offs(
                actor=request.user, company_id=company_id, values=data
            ),
        )


@login_required
@require_http_methods(["GET", "POST"])
def weekly_off_start(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    _, rule = services.get_weekly_off_for_edit(
        actor=request.user, company_id=company_id, rule_id=pk
    )
    form = ChangeWeeklyOffStartForm(
        request.POST or None, initial={"effective_from": rule.effective_from}
    )
    return _form_page(
        request,
        form=form,
        title=f"Change {rule.get_weekday_display()} start date",
        submit_label="Change start date",
        success="Weekly off start date changed. Attendance updated; finalised months kept.",
        explanation=(
            f"{rule.get_weekday_display()} currently starts on {rule.effective_from:%d %b %Y}. "
            "Choose when this weekly off should begin."
        ),
        action=lambda data: services.change_weekly_off_start(
            actor=request.user, company_id=company_id, rule_id=rule.pk,
            effective_from=data["effective_from"],
        ),
    )


@login_required
@require_http_methods(["GET", "POST"])
def weekly_off_end(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    _, rule = services.get_weekly_off_for_edit(
        actor=request.user, company_id=company_id, rule_id=pk
    )
    form = EndWeeklyOffForm(request.POST or None)
    return _form_page(
        request,
        form=form,
        title=f"Stop {rule.get_weekday_display()} as a weekly off",
        submit_label="Stop weekly off",
        success="Weekly off day stopped.",
        explanation=(
            f"{rule.get_weekday_display()} has been a weekly off since "
            f"{rule.effective_from:%d %b %Y}. It is not deleted, so earlier "
            "attendance still treats it as a day off."
        ),
        action=lambda data: services.end_weekly_off(
            actor=request.user, company_id=company_id, rule_id=rule.pk,
            effective_to=data["effective_to"],
        ),
    )


# --------------------------------------------------------------------------
# Holidays
# --------------------------------------------------------------------------

@login_required
@require_http_methods(["GET"])
def holiday_list(request):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    membership = require_company_membership(request.user, company_id)

    today = timezone.localdate()
    raw_year = request.GET.get("year", "").strip()
    year = int(raw_year) if raw_year.isdigit() and 2000 <= int(raw_year) <= 2100 else today.year
    status = request.GET.get("status", "").strip()

    with use_company(company_id):
        queryset = Holiday.objects.select_related("branch").filter(holiday_date__year=year)
        total = queryset.count()
        if status in dict(Holiday.Status.choices):
            queryset = queryset.filter(status=status)
        filtered_total = queryset.count()
        page = Paginator(
            queryset.order_by("holiday_date", "name"), 25
        ).get_page(request.GET.get("page"))
        years = sorted(
            {d.year for d in Holiday.objects.dates("holiday_date", "year")}
            | {today.year, today.year + 1},
            reverse=True,
        )

    return render(request, "scheduling/holiday_list.html", {
        "page": page,
        "year": year,
        "years": years,
        "status": status,
        "statuses": Holiday.Status.choices,
        "total": total,
        "filtered_total": filtered_total,
        "can_manage": membership.role in STRUCTURE_ROLES,
    })


@login_required
@require_http_methods(["GET", "POST"])
def holiday_create(request):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    membership = require_structure_manager(request.user, company_id)
    with use_company(company_id):
        form = HolidayForm(
            request.POST or None,
            branches=visible_branches(membership).order_by("name"),
        )
        return _form_page(
            request,
            form=form,
            title="Add holiday",
            submit_label="Add holiday",
            success="Holiday added.",
            action=lambda data: services.create_holiday(
                actor=request.user, company_id=company_id, values=data
            ),
        )


def _year_or(raw, default):
    raw = (raw or "").strip()
    return int(raw) if raw.isdigit() and 2000 <= int(raw) <= 2100 else default


def _year_months(company_id, year, today, selected):
    """Twelve month grids for the year calendar, Monday first like the date picker.

    Each day says whether it is today, a company weekly off, or already a
    holiday. A company-wide holiday cannot be selected again; a branch-only
    holiday can, since a company-wide one may still be added on that date.
    """
    start, end = datetime.date(year, 1, 1), datetime.date(year, 12, 31)
    work = WorkCalendar(company_id, start, end)
    with use_company(company_id):
        existing = list(
            Holiday.objects.select_related("branch")
            .filter(status=Holiday.Status.ACTIVE, holiday_date__range=(start, end))
            .order_by("holiday_date", "branch__name")
        )
    by_date = defaultdict(list)
    for holiday in existing:
        by_date[holiday.holiday_date].append(holiday)

    grid = calendar.Calendar(firstweekday=0)
    months = []
    for number in range(1, 13):
        weeks = []
        for week in grid.monthdatescalendar(year, number):
            cells = []
            for day in week:
                if day.month != number:
                    cells.append(None)
                    continue
                holidays = by_date.get(day, [])
                company_wide = next((h for h in holidays if h.branch_id is None), None)
                names = ", ".join(
                    h.name + (f" ({h.branch.name})" if h.branch_id else "") for h in holidays
                )
                cells.append({
                    "day": day.day,
                    "iso": day.isoformat(),
                    "label": f"{day:%a, %d %b %Y}",
                    "aria": f"{day:%A %d %B %Y}" + (f", holiday: {names}" if names else ""),
                    "today": day == today,
                    "weekly_off": work.day(None, day).kind == WEEKLY_OFF,
                    "holiday": company_wide is not None,
                    "branch_holiday": bool(holidays) and company_wide is None,
                    "selected": day.isoformat() in selected,
                })
            weeks.append(cells)
        months.append({
            "number": number,
            "name": calendar.month_name[number],
            "weeks": weeks,
            "holidays": [
                h for h in existing if h.holiday_date.month == number
            ],
        })
    return months


@login_required
@require_http_methods(["GET", "POST"])
def holiday_year(request):
    """Select many holiday dates on a year calendar and save them together.

    POST does two things: "save" adds the selected dates; anything else is a
    change of year that must keep the selection, so the page re-renders the
    other year with the posted rows still selected.
    """
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    membership = require_structure_manager(request.user, company_id)
    today = timezone.localdate()
    year = _year_or(
        request.POST.get("go_year") or request.POST.get("year") or request.GET.get("year"),
        today.year,
    )
    with use_company(company_id):
        form = HolidayYearForm(
            request.POST or None,
            branches=visible_branches(membership).order_by("name"),
        )
        if request.method == "POST" and "save" in request.POST and form.is_valid():
            data = form.cleaned_data
            try:
                added = services.add_holidays(
                    actor=request.user, company_id=company_id,
                    values={"branch": data["branch"], "days": data["days"]},
                )
            except ValidationError as exc:
                apply_service_errors(form, exc)
            else:
                count = len(added)
                messages.success(request, f"{count} holiday{'' if count == 1 else 's'} added.")
                return redirect(f"{reverse('scheduling:holiday_year')}?year={year}")

        selected = {row["iso"] for row in form.rows}
        return render(request, "scheduling/holiday_year.html", {
            "form": form,
            "rows": form.rows,
            "year": year,
            "months": _year_months(company_id, year, today, selected),
            "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        })


@login_required
@require_http_methods(["GET", "POST"])
def holiday_edit(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    membership, holiday = services.get_holiday_for_edit(
        actor=request.user, company_id=company_id, holiday_id=pk
    )
    with use_company(company_id):
        form = HolidayForm(
            request.POST or None,
            instance=holiday,
            branches=visible_branches(membership).order_by("name"),
        )
        return _form_page(
            request,
            form=form,
            title=f"Edit {holiday.name}",
            submit_label="Save holiday",
            success="Holiday saved.",
            action=lambda data: services.update_holiday(
                actor=request.user, company_id=company_id, holiday_id=holiday.pk,
                values=data,
            ),
        )


@login_required
@require_http_methods(["GET", "POST"])
def holiday_cancel(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    _, holiday = services.get_holiday_for_edit(
        actor=request.user, company_id=company_id, holiday_id=pk
    )
    if request.method == "POST":
        try:
            services.cancel_holiday(
                actor=request.user, company_id=company_id, holiday_id=holiday.pk
            )
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, f"{holiday.name} cancelled.")
        return redirect("scheduling:holiday_list")
    return render(request, "scheduling/holiday_cancel.html", {"holiday": holiday})
