"""Organisation → Company profile."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.views.decorators.http import require_http_methods

from base_template.tables import render
from common.forms import apply_service_errors
from organization.company_profile import CompanyProfileForm, save_profile
from organization.services import require_structure_manager
from organization.views import _company_or_redirect
from tenants.models import Company


@login_required
@require_http_methods(["GET", "POST"])
def company_profile(request):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    require_structure_manager(request.user, company_id)
    company = Company.objects.get(pk=company_id)
    form = CompanyProfileForm(
        request.POST or None, request.FILES or None, instance=company
    )
    if request.method == "POST" and form.is_valid():
        try:
            save_profile(actor=request.user, company_id=company_id, form=form)
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, "Company profile saved.")
            return redirect("organization:company_profile")
    return render(request, "organization/company_profile.html", {
        "form": form,
        "company": company,
    })
