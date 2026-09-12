"""Company pages for the working calendar.

Thin adapters: every authorization decision and every write happens in
``scheduling.services``, so a future API or background job enforces the same
rules without re-implementing them.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from common.choices import ActiveStatus
from common.tenant import use_company
from organization.services import (
    STRUCTURE_ROLES,
    require_company_membership,
    require_structure_manager,
    visible_branches,
)
from organization.views import _company_or_redirect
from scheduling import services
from scheduling.forms import (
    AttendanceSettingsForm,
    EndWeeklyOffForm,
    HolidayForm,
    ShiftForm,
    ShiftStatusForm,
    WeeklyOffForm,
)
from scheduling.models import CompanyAttendanceSettings, Holiday, Shift, WeeklyOffRule


def _apply_errors(form, exc):
    """Put a service's ValidationError on the fields it names, the rest on top."""
    if hasattr(exc, "error_dict"):
        for field, errors in exc.error_dict.items():
            target = field if field in form.fields else None
            for error in errors:
                form.add_error(target, error)
    else:
        for message in exc.messages:
            form.add_error(None, message)


def _form_page(request, *, form, title, submit_label, action, success, explanation=""):
    """Shared POST handling: validate the form, call the service, report back."""
    if request.method == "POST" and form.is_valid():
        try:
            action(form.cleaned_data)
        except ValidationError as exc:
            _apply_errors(form, exc)
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

    ready = bool(
        settings
        and settings.shift_mode == CompanyAttendanceSettings.ShiftMode.COMPANY_SINGLE_SHIFT
        and settings.company_shift_id
    )
    return render(request, "scheduling/overview.html", {
        "settings": settings,
        "ready": ready,
        "shifts": shifts,
        "weekly_offs": weekly_offs,
        "upcoming": upcoming,
        "holiday_count": holiday_count,
        "year": today.year,
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
                "Every employee is measured against the company shift. Separate "
                "shifts per department are not available yet."
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
            initial={"effective_from": timezone.localdate(), "is_paid": True},
        )
        return _form_page(
            request,
            form=form,
            title="Add weekly off day",
            submit_label="Add weekly off day",
            success="Weekly off day added.",
            action=lambda data: services.add_weekly_off(
                actor=request.user, company_id=company_id, values=data
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
            initial={"is_paid": True},
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
