"""An employee's profile and history, and ending their employment (plan step
N6; the profile - photo, personal information, summaries, actions - 2026-09-26)."""

import mimetypes

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from attendance.models import AttendanceRecord
from common.forms import apply_service_errors
from organization import employee_detail_services as services
from organization import employee_profile as profile
from organization.employee_detail_forms import EndEmploymentForm
from organization.employee_edit_services import is_company_wide
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


def _month(source, today):
    try:
        year, month = int(source.get("year", today.year)), int(source.get("month", today.month))
    except (TypeError, ValueError):
        return today.year, today.month
    if not (2000 <= year <= today.year + 1 and 1 <= month <= 12):
        return today.year, today.month
    return year, month


def _may_record_leave(user, company_id, assignment):
    """May this login record leave for someone placed here (Record leave's rule)?"""
    from access_control.branch_access import ALL_BRANCHES, branches_for

    record = branches_for(user, company_id, "leave.record")
    return record is ALL_BRANCHES or bool(assignment and assignment.branch_id in record)


def _profile(request, company_id, pk, *, personal=None, photo=None, open_dialog=""):
    """The profile page. A refused form comes back bound, so its modal opens
    again with the reasons and what was typed."""
    page = services.employee_history(actor=request.user, company_id=company_id, employee_id=pk)
    employee, may = page["employee"], page["may"]
    counts = page["month_counts"]
    today = page["today"]
    company = page["membership"].company
    year, month = _month(request.GET, today)
    summary = (profile.month_summary(company=company, scope=may["attendance"],
                                     employee=employee, year=year, month=month)
               if may["attendance"] else None)
    earlier = (year - 1, 12) if month == 1 else (year, month - 1)
    later = (year + 1, 1) if month == 12 else (year, month + 1)
    personal_form = personal or profile.PersonalForm(instance=employee)
    return render(request, "organization/employee_detail.html", {
        **page,
        "month_rows": [(label, counts.get(status, 0)) for status, label in MONTH_ROWS],
        # The Calendar opens only people placed in branches whose attendance
        # the viewer may see (A12 part 7).
        "calendar_url": (
            reverse("attendance:attendance_calendar")
            + f"?employee={pk}&year={year}&month={month}"
        ) if may["attendance"] else "",
        "summary": summary,
        "summary_earlier": earlier,
        "summary_later": later,
        "leave": profile.year_leave(actor=request.user, company=company, employee=employee,
                                    year=year),
        "leave_year": year,
        "personal_form": personal_form,
        "photo_form": photo or profile.PhotoForm(),
        # What the Personal tab lists: each field's label and value, choices
        # shown as words.
        "personal_rows": [
            (personal_form.fields[name].label,
             dict(profile.CHOICES.get(name, ())).get(getattr(employee, name),
                                                     getattr(employee, name)))
            for name in profile.PERSONAL_FIELDS
        ],
        "may_record_leave": _may_record_leave(request.user, company_id, page["assignment"]),
        "open_dialog": open_dialog,
    })


@login_required
@require_http_methods(["GET"])
def employee_detail(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    return _profile(request, company_id, pk)


@login_required
@require_http_methods(["GET"])
def employee_photo(request, pk):
    """The photo, only to someone who may see this employee - never a public
    file address."""
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    _membership, employee, _assignment, _pay = services.get_employee_for_edit(
        actor=request.user, company_id=company_id, employee_id=pk, code="employees.view")
    if not employee.photo:
        raise Http404("No photo.")
    kind = mimetypes.guess_type(employee.photo.name)[0] or "application/octet-stream"
    reply = FileResponse(employee.photo.open("rb"), content_type=kind)
    reply["Cache-Control"] = "private, max-age=300"
    reply["X-Content-Type-Options"] = "nosniff"
    return reply


@login_required
@require_POST
def employee_photo_change(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    profile.editable(request.user, company_id, pk)
    form = profile.PhotoForm(request.POST, request.FILES)
    if form.is_valid():
        try:
            profile.save_photo(actor=request.user, company_id=company_id, employee_id=pk,
                               upload=form.cleaned_data.get("photo") or None,
                               remove=form.cleaned_data.get("remove"))
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, "Photo saved." if form.cleaned_data.get("photo")
                             else "Photo removed.")
            return redirect("organization:employee_detail", pk=pk)
    return _profile(request, company_id, pk, photo=form, open_dialog="photo-dialog")


@login_required
@require_POST
def employee_personal(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    _membership, employee = profile.editable(request.user, company_id, pk)
    form = profile.PersonalForm(request.POST, instance=employee)
    if form.is_valid():
        try:
            profile.save_personal(actor=request.user, company_id=company_id, employee_id=pk,
                                  form=form)
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, "Personal information saved.")
            return redirect(reverse("organization:employee_detail", args=[pk]) + "#personal")
    return _profile(request, company_id, pk, personal=form, open_dialog="personal-dialog")


@login_required
@require_POST
def employee_report_visibility(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    hidden = request.POST.get("hidden") == "1"
    employee = profile.set_report_visibility(actor=request.user, company_id=company_id,
                                             employee_id=pk, hidden=hidden)
    messages.success(request, (
        f"{employee.full_name} is left out of the reports. Their attendance still counts."
        if hidden else f"{employee.full_name} is shown in the reports again."))
    return redirect("organization:employee_detail", pk=pk)


@login_required
@require_http_methods(["GET", "POST"])
def employee_end(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    page = services.employee_history(actor=request.user, company_id=company_id, employee_id=pk)
    employee = page["employee"]
    if not page["may"]["end"]:
        # end_employment refuses too; this keeps the form from being offered.
        raise PermissionDenied("Ending this person's employment is not yours to do.")
    if page["is_ended"]:
        messages.info(request, f"{employee.full_name} has already left.")
        return redirect("organization:employee_detail", pk=employee.pk)

    form = EndEmploymentForm(
        request.POST or None,
        initial={
            "last_day": page["today"], "disable_login": page["may"]["logins"],
            "end_device_enrollments": True,
        },
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
            if summary["devices_cleared"]:
                parts.append(
                    "Being removed from " + ", ".join(summary["devices_cleared"])
                    + " (on their next check-in); the saved fingerprint and face are kept."
                )
            messages.success(request, " ".join(parts))
            if summary["devices_by_hand"]:
                # Not pretending: these terminals have no proven delete, so a
                # person must do it there or the leaver still opens the door.
                messages.warning(request, "Delete them on the terminal itself: " + "; ".join(
                    f"{item['device']} (user {item['pin']}) — {item['reason']}"
                    for item in summary["devices_by_hand"]))
            if not is_company_wide(page["membership"]):
                # Once ended they are placed nowhere, so no longer in a branch
                # this login looks after; their page stays with the company.
                return redirect("employee_list")
            return redirect("organization:employee_detail", pk=employee.pk)

    active_devices = [d for d in page["devices"] if d.is_current]
    return render(request, "organization/employee_end.html", {
        **page,
        "form": form,
        "active_devices": active_devices,
        # A login is disabled with the ending only by someone who may manage
        # logins in the branch; the service checks the same.
        "has_active_login": bool(
            page["login"] and page["login"].status == "active" and page["may"]["logins"]
        ),
    })
