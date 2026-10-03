"""Organisation → ERP webhook: the settings, a test, the queue and the guide."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
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
    test_form = services.TestEventForm(
        request.POST if action == "send_test_event" else None,
        initial={"work_date": timezone.localdate()}, prefix="test")
    if action == "send_test_event" and test_form.is_valid():
        data = test_form.cleaned_data
        result = services.send_test_event(
            actor=request.user, company_id=company_id, employee_code=data["employee_code"],
            work_date=data["work_date"], check_in=data["check_in"], check_out=data["check_out"])
        request.session[TEST_RESULT] = {**result.as_dict(), "kind": "event"}
        return redirect("webhooks:settings")
    new_secret = ""
    if action == "generate":
        # Create a secret key here, for the company to give its system (Nihal,
        # 2026-10-01: the ERP's developer puts our key in its .env). Shown once,
        # filled into the form with what was typed; Save keeps it.
        new_secret = services.new_secret()
        typed = {name: request.POST.get(name, "") for name in ("url", "ping_url",
                 "employee_key", "mode", "send_from")}
        typed.update({name: request.POST.get(name) == "on"
                      for name in ("is_active", "send_breaks", "batch")})
        form = services.WebhookSettingsForm(
            instance=saved, initial={**typed, "secret": new_secret},
            has_secret=bool(saved and saved.secret_encrypted),
            has_signing_secret=bool(saved and saved.signing_secret_encrypted),
        )
        form.fields["secret"].widget.render_value = True
    if action in ("debug_on", "debug_off"):
        # Debug messages for 15 minutes (2026-10-03), shown on this page.
        try:
            if action == "debug_on":
                services.start_debug(actor=request.user, company_id=company_id)
            else:
                services.stop_debug(actor=request.user, company_id=company_id)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        return redirect(reverse("webhooks:settings") + "#webhook-debug")
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
            "new_secret": new_secret,
            "test_form": test_form,
            "page": page,
            "counts": services.counts(company_id),
            "ping_url": services.ping_url(saved) if saved else "",
            "Status": WebhookEvent.Status,
            "debug": services.debug_state(company_id),
            "debug_minutes": services.DEBUG_MINUTES,
        })


@login_required
@require_http_methods(["GET"])
def webhook_debug(request):
    """The debug messages as JSON, for the page to show as they come."""
    company_id, bail = _company_or_redirect(request)
    if bail:
        return JsonResponse({"active": False, "seconds_left": 0, "entries": []})
    require_structure_manager(request.user, company_id)
    return JsonResponse(services.debug_state(company_id), json_dumps_params={"ensure_ascii": False})


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
