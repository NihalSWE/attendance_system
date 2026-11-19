"""The profile's Education history, Employee documents and Device permissions
(Ajay, 2026-09-27). Each is a modal on the profile: saved, back to its tab;
refused, the profile again with that modal open and its reasons."""

import mimetypes

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from common.forms import apply_service_errors
from common.tenant import use_company
from employees.models import EmployeeEducation
from devices.services import verification
from devices.services.panel_access import assert_may_manage_devices
from organization import employee_devices
from organization import employee_records as records
from organization.employee_detail_views import _profile
from organization.views import _company_or_redirect


def _tab(pk, tab):
    return redirect(reverse("organization:employee_detail", args=[pk]) + f"#{tab}")


def _verification_messages(request, changes, *, bulk=False):
    automatic = set()
    if changes.card_updates and not changes.card_manual:
        automatic.add(verification.Method.CARD)
    if changes.fingerprint_updates and not changes.fingerprint_manual:
        automatic.add(verification.Method.FINGERPRINT)
    manual_blocks = changes.blocked - automatic - {verification.Method.FACE}
    manual_unblocks = changes.unblocked - automatic - {verification.Method.FACE}
    status_location = "Terminal updates" if bulk else "Device permissions"
    if changes.card_updates:
        messages.info(request, f"Card updates queued for {changes.card_updates} terminal enrollment(s). "
                               f"Check the status in {status_location} and verify at the reader.")
    if changes.fingerprint_updates:
        messages.info(request, f"Fingerprint updates queued for {changes.fingerprint_updates} terminal enrollment(s). "
                               f"Check the status in {status_location} and verify at the reader.")
    if verification.Method.FACE in changes.blocked:
        messages.warning(
            request, "Face is blocked in HRM only. The reader can still accept face scans. "
            "HRM will exclude those scans from attendance.",
        )
    if verification.Method.FACE in changes.unblocked:
        messages.info(request, "Face is allowed again for attendance in HRM.")
    if manual_blocks:
        methods = ", ".join(method.label for method in verification.METHODS if method in manual_blocks)
        messages.warning(
            request, f"{methods} blocked in HRM. On each linked terminal, open the affected employee’s "
            "user record and remove only the blocked credentials.",
        )
    if manual_unblocks:
        methods = ", ".join(method.label for method in verification.METHODS if method in manual_unblocks)
        messages.info(
            request, f"{methods} unblocked in HRM. To activate a method that was removed, "
            "enroll it again on each linked terminal for the affected employee.",
        )


@login_required
@require_POST
def employee_education_save(request, pk, row=None):
    """Add a qualification, or change one."""
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    instance = None
    if row is not None:
        with use_company(company_id):
            instance = EmployeeEducation.objects.filter(pk=row, employee_id=pk).first()
        if instance is None:
            raise Http404("No such qualification.")
    key = f"education_{row}" if row is not None else "education_new"
    form = records.EducationForm(request.POST, instance=instance, auto_id=f"{key}_%s")
    if form.is_valid():
        try:
            records.save_education(actor=request.user, company_id=company_id, employee_id=pk,
                                   values=form.cleaned_data, row_id=row)
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, "Qualification saved.")
            return _tab(pk, "profile")
    return _profile(request, company_id, pk, bound={key: form}, open_dialog=f"{key}-dialog")


@login_required
@require_POST
def employee_education_remove(request, pk, row):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    records.remove_education(actor=request.user, company_id=company_id, employee_id=pk,
                             row_id=row)
    messages.success(request, "Qualification removed.")
    return _tab(pk, "profile")


@login_required
@require_POST
def employee_document_add(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    form = records.DocumentForm(request.POST, request.FILES, auto_id="document_%s")
    if form.is_valid():
        try:
            records.add_document(actor=request.user, company_id=company_id, employee_id=pk,
                                 values=form.cleaned_data)
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, "Document added.")
            return _tab(pk, "profile")
    return _profile(request, company_id, pk, bound={"document": form},
                    open_dialog="document-dialog")


@login_required
@require_POST
def employee_document_remove(request, pk, row):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    records.remove_document(actor=request.user, company_id=company_id, employee_id=pk,
                            row_id=row)
    messages.success(request, "Document removed.")
    return _tab(pk, "profile")


@login_required
@require_GET
def employee_document(request, pk, row):
    """The file, only to someone who may see this employee - never a public
    file address."""
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    document = records.document_for(actor=request.user, company_id=company_id,
                                    employee_id=pk, row_id=row)
    kind = mimetypes.guess_type(document.file.name)[0] or "application/octet-stream"
    reply = FileResponse(document.file.open("rb"), content_type=kind,
                         filename=document.file_name or None)
    reply["Cache-Control"] = "private, max-age=300"
    reply["X-Content-Type-Options"] = "nosniff"
    return reply


@login_required
@require_POST
def employee_device_permission(request, pk, enrollment):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    rows = {str(row.pk): row for row in employee_devices.current(
        company_id, records.get_employee_for_edit(
            actor=request.user, company_id=company_id, employee_id=pk,
            code="employees.view")[1])}
    instance = rows.get(str(enrollment))
    if instance is None:
        raise Http404("No such device enrolment.")
    key = f"device_{enrollment}"
    form = employee_devices.DevicePermissionForm(request.POST, instance=instance,
                                                 auto_id=f"{key}_%s")
    if form.is_valid():
        try:
            saved, result = employee_devices.change(
                actor=request.user, company_id=company_id, employee_id=pk,
                enrollment_id=enrollment, values=form.cleaned_data)
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            note = ""
            if result is not None and result.sent:
                note = (f" The card and role are on their way to {len(result.sent)} "
                        "device(s); they apply on the next check-in.")
            for _, _, reason in (result.failed[:3] if result is not None else []):
                messages.warning(request, reason)
            messages.success(request, f"{saved.device.name}: saved.{note}")
            return _tab(pk, "device-permissions")
    return _profile(request, company_id, pk, bound={key: form}, open_dialog=f"{key}-dialog")


@login_required
@require_POST
def employee_verification_methods(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    assert_may_manage_devices(request.user, company_id)
    employee = records.get_employee_for_edit(actor=request.user, company_id=company_id,
                                             employee_id=pk, code="employees.view")[1]
    form = employee_devices.VerificationMethodsForm(request.POST, auto_id="verification_%s")
    if form.is_valid():
        try:
            changes = verification.change(actor=request.user, employee=employee,
                                          methods=form.cleaned_data["blocked_methods"])
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, "Attendance verification settings saved.")
            _verification_messages(request, changes)
            return _tab(pk, "device-permissions")
    return _profile(request, company_id, pk, bound={"verification": form},
                    open_dialog="verification-dialog")


@login_required
@require_POST
def employee_verification_bulk(request):
    from base_template.tables import table_queryset
    from base_template.views import employee_list_query

    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    assert_may_manage_devices(request.user, company_id)
    back = reverse("employee_list")
    filters = request.GET.copy()
    if "search[value]" in filters:
        filters["table_q"] = filters.pop("search[value]")[-1]
    if not filters.get("blocked_method"):
        filters["blocked_method"] = "all"
    if filters:
        back += f"?{filters.urlencode()}"
    preview = request.POST.get("preview") == "1"
    with use_company(company_id):
        if preview or request.POST.get("all") == "1":
            listing = employee_list_query(request)
        if request.POST.get("all") == "1":
            matches, _, _ = table_queryset(request, listing.queryset, search=listing.search_fields)
            employee_ids = list(matches.values_list("pk", flat=True))
        else:
            employee_ids = request.POST.getlist("employee")
            if any(not pk.isascii() or not pk.isdigit() or len(pk) > 18 for pk in employee_ids):
                if preview:
                    return JsonResponse({"error": "Select the employees again."}, status=400)
                messages.error(request, "Select the employees again.")
                return redirect(back)
            employee_ids = [int(pk) for pk in employee_ids]
            if preview:
                employee_ids = list(listing.queryset.filter(pk__in=employee_ids).values_list("pk", flat=True))
    if preview:
        current = verification.blocked_methods_by_employee(company_id, employee_ids, timezone.now())
        return JsonResponse({"selected_count": len(set(employee_ids)),
                             "counts": {method.value: sum(method in methods for methods in current.values())
                                        for method in verification.METHODS}})
    if not employee_ids:
        messages.error(request, "Select at least one employee.")
        return redirect(back)
    form = employee_devices.VerificationMethodsForm(request.POST)
    if not form.is_valid() or not form.cleaned_data["blocked_methods"]:
        messages.error(request, "Choose at least one verification method.")
        return redirect(back)
    try:
        changes = verification.change_bulk(actor=request.user, company_id=company_id,
                                           employee_ids=employee_ids, methods=form.cleaned_data["blocked_methods"],
                                           operation=request.POST.get("operation"))
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, f"Updated attendance verification for {changes.count} of "
                                  f"{len(set(employee_ids))} selected employees.")
        _verification_messages(request, changes, bulk=True)
        if changes.terminal_updates:
            request.session[verification.BULK_UPDATE_SESSION_KEY] = {
                "company_id": company_id, "operation": request.POST["operation"],
                "updates": changes.terminal_updates, "selected_count": len(set(employee_ids)),
            }
            back += "#terminal-updates"
    return redirect(back)
