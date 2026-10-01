"""Organisation → ERP webhook: the settings, a test, the queue and the guide."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import redirect
from django.utils.text import slugify
from django.views.decorators.http import require_http_methods

from base_template.tables import paginate, render
from common.forms import apply_service_errors
from common.tenant import use_company
from organization.services import require_structure_manager
from organization.views import _company_or_redirect
from tenants.models import Company
from webhooks import guide, services
from webhooks.models import WebhookEvent


TEST_RESULT = "webhook_test_result"


def _tested(request, company_id):
    """Test the saved webhook and show the result once, at the top of the page."""
    result = services.test_connection(actor=request.user, company_id=company_id)
    request.session[TEST_RESULT] = result.as_dict()
    return redirect("webhooks:settings")


@login_required
@require_http_methods(["GET", "POST"])
def webhook_settings(request):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    require_structure_manager(request.user, company_id)
    saved = services.settings_for(company_id)
    action = request.POST.get("action", "") if request.method == "POST" else ""
    form = services.WebhookSettingsForm(
        request.POST if action == "save" else None, instance=saved,
        has_secret=bool(saved and saved.secret_encrypted),
        has_signing_secret=bool(saved and saved.signing_secret_encrypted),
    )
    if action == "test":
        return _tested(request, company_id)
    if action == "send_again":
        again = services.send_again(actor=request.user, company_id=company_id)
        messages.success(request, f"{again} event(s) will be sent again now." if again
                         else "Nothing is waiting to be sent again.")
        return redirect("webhooks:settings")
    if action == "send_now":
        if services.active_settings(company_id) is None:
            messages.error(request, "Switch the webhook on first.")
        else:
            received = services.deliver_due(company_id)
            messages.success(request, f"Sent: {received} event(s) received." if received
                             else "Nothing was received - see the list below for why.")
        return redirect("webhooks:settings")
    if action == "save" and form.is_valid():
        try:
            services.save_settings(actor=request.user, company_id=company_id, form=form)
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            if request.POST.get("then") == "test":
                return _tested(request, company_id)
            messages.success(request, "Saved. Press Test connection to check it works.")
            return redirect("webhooks:settings")

    with use_company(company_id):
        page = paginate(
            request,
            WebhookEvent.objects.select_related("employee").order_by("-created_at", "-pk"),
            search=("employee__first_name", "employee__last_name", "kind", "status",
                    "last_message"),
            order=("created_at", ("employee__first_name", "employee__last_name"), "work_date",
                   "kind", "status", "attempts", "last_message"),
        )
        return render(request, "webhooks/settings.html", {
            "form": form,
            "saved": saved,
            # Shown once, right after a test (2026-10-01).
            "test_result": request.session.pop(TEST_RESULT, None),
            "page": page,
            "counts": services.counts(company_id),
            "ping_url": services.ping_url(saved) if saved else "",
            "Status": WebhookEvent.Status,
        })


@login_required
@require_http_methods(["GET"])
def webhook_guide(request):
    """The guide on screen, or downloaded with ?format=pdf|md."""
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    require_structure_manager(request.user, company_id)
    company = Company.objects.get(pk=company_id)
    row = services.settings_for(company_id)
    fmt = request.GET.get("format", "")
    name = f"attendance-webhook-guide-{slugify(company.code or company.name)}"
    if fmt == "pdf":
        response = HttpResponse(guide.pdf(row, company), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{name}.pdf"'
        return response
    if fmt == "md":
        response = HttpResponse(guide.markdown(row, company),
                                content_type="text/markdown; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{name}.md"'
        return response
    return render(request, "webhooks/guide.html", {
        "company": company,
        "title": guide.TITLE,
        "blocks": guide.blocks(row, company),
    })
