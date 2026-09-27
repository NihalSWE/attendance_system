"""My profile pages (Nihal, 2026-09-27; rules in ``organization.employee_self``).
Every change opens in a modal: saved, back to the page; refused, the page
again with that modal open and its reasons."""

import mimetypes

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from common.forms import apply_service_errors
from common.tenant import use_company
from employees.models import EmployeeEducation
from organization import employee_profile as profile
from organization import employee_records as records
from organization import employee_self as self_service


def _company(request):
    if not request.company_id:
        raise Http404("No company.")
    return request.company_id


def _page(request, *, details=None, photo=None, bound=None, open_dialog=""):
    company_id = _company(request)
    _membership, employee = self_service.mine(request.user, company_id)
    bound = bound or {}
    with use_company(company_id):
        education = [(row, bound.get(f"education_{row.pk}") or records.EducationForm(
            instance=row, auto_id=f"education_{row.pk}_%s"))
            for row in EmployeeEducation.objects.filter(employee=employee)
            .order_by("-passing_year", "-pk")]
    details_form = details or self_service.MyDetailsForm(instance=employee, auto_id="details_%s")
    choices = profile.CHOICES
    rows = [(details_form.fields[name].label,
             dict(choices.get(name, ())).get(getattr(employee, name), getattr(employee, name)))
            for name in self_service.SELF_FIELDS]
    return render(request, "base_template/me/profile.html", {
        "employee": employee,
        "placement": self_service.placement(company_id, employee),
        "details_form": details_form,
        "photo_form": photo or profile.PhotoForm(auto_id="photo_%s"),
        "personal_rows": [row for row in rows if row[0] not in ADDRESS_LABELS],
        "address_rows": [row for row in rows if row[0] in ADDRESS_LABELS],
        "education": education,
        "education_new_form": bound.get("education_new") or records.EducationForm(
            auto_id="education_new_%s"),
        "open_dialog": open_dialog,
    })


ADDRESS_LABELS = ("Address", "Emergency contact", "Their phone", "Relation")


def _back():
    return redirect(reverse("me:profile"))


@login_required
@require_GET
def my_profile(request):
    return _page(request)


@login_required
@require_POST
def my_details(request):
    company_id = _company(request)
    _m, employee = self_service.mine(request.user, company_id)
    form = self_service.MyDetailsForm(request.POST, instance=employee, auto_id="details_%s")
    if form.is_valid():
        try:
            self_service.save_details(actor=request.user, company_id=company_id, form=form)
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, "Your details are saved.")
            return _back()
    return _page(request, details=form, open_dialog="details-dialog")


@login_required
@require_POST
def my_photo_change(request):
    company_id = _company(request)
    form = profile.PhotoForm(request.POST, request.FILES, auto_id="photo_%s")
    if form.is_valid():
        try:
            self_service.save_photo(actor=request.user, company_id=company_id,
                                    upload=form.cleaned_data.get("photo") or None,
                                    remove=form.cleaned_data.get("remove"))
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, "Your photo is saved." if form.cleaned_data.get("photo")
                             else "Your photo is removed.")
            return _back()
    return _page(request, photo=form, open_dialog="photo-dialog")


@login_required
@require_GET
def my_photo(request):
    """Their own photo - never a public file address."""
    company_id = _company(request)
    _m, employee = self_service.mine(request.user, company_id)
    if not employee.photo:
        raise Http404("No photo.")
    kind = mimetypes.guess_type(employee.photo.name)[0] or "application/octet-stream"
    reply = FileResponse(employee.photo.open("rb"), content_type=kind)
    reply["Cache-Control"] = "private, max-age=300"
    reply["X-Content-Type-Options"] = "nosniff"
    return reply


@login_required
@require_POST
def my_education_save(request, row=None):
    company_id = _company(request)
    _m, employee = self_service.mine(request.user, company_id)
    instance = self_service.my_education(company_id, employee, row) if row is not None else None
    key = f"education_{row}" if row is not None else "education_new"
    form = records.EducationForm(request.POST, instance=instance, auto_id=f"{key}_%s")
    if form.is_valid():
        try:
            self_service.save_education(actor=request.user, company_id=company_id,
                                        values=form.cleaned_data, row_id=row)
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, "Qualification saved.")
            return _back()
    return _page(request, bound={key: form}, open_dialog=f"{key}-dialog")


@login_required
@require_POST
def my_education_remove(request, row):
    company_id = _company(request)
    _m, employee = self_service.mine(request.user, company_id)
    self_service.my_education(company_id, employee, row)
    self_service.remove_education(actor=request.user, company_id=company_id, row_id=row)
    messages.success(request, "Qualification removed.")
    return _back()
