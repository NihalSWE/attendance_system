"""Missed-scan requests (plan step N11).

The employee's pages live under /me/ (``base_template/me_urls.py``), which the
self-service gate always opens. The deciding pages live under /attendance/ and
open through ``BRANCH_PAGES`` to anyone who may fix attendance somewhere; each
service checks the branch itself.
"""

import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from attendance import month_view, scan_requests
from attendance.forms import DecideMissedScanForm, EnterMissingForm, MissedScanForm
from attendance.models import AttendanceRecord, MissedScanRequest
from base_template.tables import paginate, render
from common.forms import apply_service_errors
from common.tenant import use_company
from organization.views import _company_or_redirect

STATUSES = ("pending", "approved", "rejected")


def _company_tz(request):
    from tenants.models import Company

    return Company.objects.filter(pk=request.company_id).values_list("timezone", flat=True).first() or "UTC"


# --------------------------------------------------------------------------
# The employee
# --------------------------------------------------------------------------


@login_required
@require_http_methods(["GET"])
def my_missed_scans(request):
    employee = scan_requests.my_employee(request.user, request.company_id) if request.company_id else None
    if employee is None:
        return render(request, "base_template/me/no_employee.html", {"title": "Missed scans"})
    with use_company(request.company_id):
        page = paginate(
            request,
            MissedScanRequest.objects.filter(employee=employee).select_related("decided_by")
            .order_by("-submitted_at", "-pk"),
            search=("reason", "status", "decision_note"),
            order=("work_date", "scan_at", "status", None, None),
        )
    return render(request, "attendance/missed_scans_mine.html", {
        "page": page,
        "company_tz": _company_tz(request),
    })


@login_required
@require_http_methods(["GET", "POST"])
def report_missed_scan(request):
    employee = scan_requests.my_employee(request.user, request.company_id) if request.company_id else None
    if employee is None:
        return render(request, "base_template/me/no_employee.html", {"title": "Report a missed scan"})
    initial = {}
    raw = request.GET.get("date", "")
    try:
        day = datetime.date.fromisoformat(raw)
    except ValueError:
        day = None
    if day is not None:
        initial = {"work_date": day, "at": day}
    form = MissedScanForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            scan_requests.submit(
                actor=request.user, company_id=request.company_id,
                work_date=data["work_date"], at=data["at"], reason=data["reason"],
                kind=data["kind"], at_out=data["at_out"],
            )
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, "Sent. Your attendance changes once it is approved.")
            return redirect("me:missed_scans")
    return render(request, "attendance/missed_scan_form.html", {"form": form})


@require_POST
@login_required
def withdraw_missed_scan(request, pk):
    try:
        scan_requests.withdraw(actor=request.user, company_id=request.company_id, request_id=pk)
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    else:
        messages.success(request, "Request withdrawn.")
    return redirect("me:missed_scans")


# --------------------------------------------------------------------------
# Whoever decides
# --------------------------------------------------------------------------


@login_required
@require_http_methods(["GET"])
def missed_scan_list(request):
    """Attendance → Missed scans: requests from the viewer's branches."""
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    _membership, queryset = scan_requests.reviewable(request.user, company_id)
    status = request.GET.get("status", "pending")
    if status not in STATUSES:
        status = "pending"
    with use_company(company_id):
        page = paginate(
            request,
            queryset.filter(status=status).select_related("employee", "branch")
            .order_by("submitted_at" if status == "pending" else "-decided_at", "pk"),
            search=("employee__first_name", "employee__last_name", "branch__name", "reason"),
            order=(("employee__first_name", "employee__last_name"), "branch__name",
                   "work_date", "scan_at", None, None),
        )
        waiting = queryset.filter(status="pending").count()
    from attendance import access
    from attendance.views import _pickable

    _m, scope = access.view_scope(request.user, company_id)
    with use_company(company_id):
        people = list(_pickable(scope).exclude(user=request.user).filter(
            employment_status__in=scan_requests.WORKING).order_by("first_name", "last_name"))
    return render(request, "attendance/missed_scan_list.html", {
        "people": people,
        "page": page,
        "status": status,
        "waiting": waiting,
        "company_tz": _company_tz(request),
    })


@login_required
@require_http_methods(["GET", "POST"])
def missed_scan_decide(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    _membership, queryset = scan_requests.reviewable(request.user, company_id)
    with use_company(company_id):
        item = get_object_or_404(queryset.select_related("employee", "branch", "decided_by"), pk=pk)
    form = DecideMissedScanForm(request.POST or None)
    if request.method == "POST" and item.status == "pending":
        decision = request.POST.get("decision", "")
        if decision not in ("approve", "reject"):
            raise PermissionDenied("Choose approve or reject.")
        if form.is_valid():
            try:
                scan_requests.decide(
                    actor=request.user, company_id=company_id, request_id=item.pk,
                    approve=decision == "approve", note=form.cleaned_data["note"],
                )
            except ValidationError as exc:
                # A correction's refusal ("that time belongs to another day")
                # is about the scan, not the note: show it above the form.
                if hasattr(exc, "error_dict") and "at" in exc.error_dict:
                    exc = ValidationError(exc.message_dict["at"])
                apply_service_errors(form, exc)
            else:
                messages.success(
                    request,
                    "Approved. The scan is added and the day worked out again."
                    if decision == "approve" else "Rejected.",
                )
                return redirect(reverse("attendance:missed_scan_list"))

    entered_by = ""
    if scan_requests.entered_by_someone_else(item) and item.created_by_id:
        from django.contrib.auth import get_user_model

        creator = get_user_model().objects.filter(pk=item.created_by_id).first()
        entered_by = creator.get_username() if creator else ""
    company_tz = _company_tz(request)
    with use_company(company_id):
        record = (
            AttendanceRecord.objects.select_related("shift", "employee")
            .filter(employee_id=item.employee_id, work_date=item.work_date).first()
        )
        detail = (
            month_view.build_day_detail(record=record, company_timezone=company_tz)
            if record is not None else None
        )
    return render(request, "attendance/missed_scan_decide.html", {
        "entered_by": entered_by,
        "entered_by_you": bool(entered_by) and item.created_by_id == request.user.pk,
        "item": item,
        "form": form,
        "record": record,
        "detail": detail,
        "company_tz": company_tz,
        "fix_url": reverse(
            "attendance:attendance_day_fix", args=[item.employee_id, item.work_date.isoformat()]
        ),
        "today": timezone.localdate(),
    })


@login_required
@require_http_methods(["GET", "POST"])
def missed_scan_enter(request, employee_pk):
    """Enter a missing scan or day for someone (2026-09-26): HR, their branch
    manager, their department head. It waits for approval like their own
    request; the profile opens this form in a modal."""
    from employees.models import Employee

    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    with use_company(company_id):
        employee = get_object_or_404(Employee, pk=employee_pk)
    initial = {}
    try:
        day = datetime.date.fromisoformat(request.GET.get("date", ""))
    except ValueError:
        day = None
    if day is not None:
        initial = {"work_date": day, "at": day, "at_out": day}
    form = EnterMissingForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            scan_requests.enter_for(
                actor=request.user, company_id=company_id, employee_id=employee.pk,
                work_date=data["work_date"], kind=data["kind"], at=data["at"],
                at_out=data["at_out"], reason=data["reason"],
            )
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, (
                f"Sent for approval. {employee.full_name}'s attendance changes once someone "
                "who may fix it approves - not you, if you entered it."))
            return redirect(reverse("organization:employee_detail", args=[employee.pk])
                            + "#attendance")
    return render(request, "attendance/missed_scan_enter.html", {
        "form": form, "employee": employee,
    })


@login_required
@require_http_methods(["GET"])
def missed_scan_pick(request):
    """Missed scans → Enter missing attendance: choose the person, then the
    entry form. HR has no employee profile to start from, so it starts here."""
    from attendance import access
    from attendance.views import _pickable

    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    _membership, scope = access.view_scope(request.user, company_id)
    chosen = request.GET.get("employee", "")
    with use_company(company_id):
        if chosen.isdigit() and _pickable(scope).filter(pk=int(chosen)).exists():
            return redirect("attendance:missed_scan_enter", employee_pk=int(chosen))
    messages.error(request, "Choose someone whose attendance you look after.")
    return redirect("attendance:missed_scan_list")
