"""The profile's Education history, Employee documents and Device permissions
(Ajay, 2026-09-27). Each is a modal on the profile: saved, back to its tab;
refused, the profile again with that modal open and its reasons."""

import mimetypes

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from common.forms import apply_service_errors
from common.tenant import use_company
from employees.models import EmployeeEducation
from organization import employee_devices
from organization import employee_records as records
from organization.employee_detail_views import _profile
from organization.views import _company_or_redirect


def _tab(pk, tab):
    return redirect(reverse("organization:employee_detail", args=[pk]) + f"#{tab}")


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
