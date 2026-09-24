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


@login_required
@require_http_methods(["GET", "POST"])
def mail_settings(request):
    """Organisation → Email settings: the company's own mail account, and a test."""
    from organization import mail_settings as mail

    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    require_structure_manager(request.user, company_id)
    saved = mail.settings_for(company_id)
    form = mail.MailSettingsForm(
        request.POST if request.POST.get("action") == "save" else None,
        instance=saved, has_password=bool(saved and saved.password_encrypted),
    )
    if request.method == "POST":
        if request.POST.get("action") == "test":
            try:
                to = mail.send_test(actor=request.user, company_id=company_id,
                                    to=request.POST.get("test_to"))
            except ValidationError as exc:
                messages.error(request, "The test email was not sent. " + " ".join(exc.messages))
            else:
                messages.success(request, f"Test email sent to {to}. Check that inbox.")
            return redirect("organization:mail_settings")
        if form.is_valid():
            try:
                mail.save_settings(actor=request.user, company_id=company_id, form=form)
            except ValidationError as exc:
                apply_service_errors(form, exc)
            else:
                messages.success(request, "Email settings saved. Send a test to check them.")
                return redirect("organization:mail_settings")
    how, from_email, _name = mail.sender(company_id)
    return render(request, "organization/mail_settings.html", {
        "form": form,
        "saved": saved,
        "how": how,
        "from_email": from_email,
        "providers": mail.PROVIDERS,
        "test_to": request.user.email,
    })
