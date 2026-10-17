"""AI order inbox, the review page and the assistant's settings (Sales menu)."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from ai_assistant import claude, forms, intake, setup
from ai_assistant.models import AiCall, AiRecipient, InboundMessage
from base_template.tables import paginate, render
from common.forms import apply_service_errors
from common.tenant import use_company
from inventory import access
from inventory.views import company_or_bail


def _say(exc):
    return "; ".join(exc.messages)


@login_required
def inbox(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require(request.user, company_id, intake.VIEW)
    own = claude.settings_for(company_id)
    form = forms.PasteForm(request.POST or None)
    open_dialog = ""
    if request.method == "POST":
        if form.is_valid():
            try:
                message = intake.paste(actor=request.user, company_id=company_id, body=form.cleaned_data["body"],
                                       sender=form.cleaned_data["sender"])
            except ValidationError as exc:
                apply_service_errors(form, exc)
                open_dialog = "paste-dialog"
            else:
                messages.success(request, "Saved. " + (message.note or "The AI read it: review the draft."))
                return redirect("ai_assistant:message", pk=message.pk)
        else:
            open_dialog = "paste-dialog"
    status = request.GET.get("status", "")
    with use_company(company_id):
        rows = InboundMessage.objects.select_related("customer", "sales_order")
        if status in InboundMessage.Status.values:
            rows = rows.filter(status=status)
        table = paginate(request, rows.order_by("-received_at", "-pk"),
                         search=("sender", "subject", "body", "customer__name", "sales_order__number"),
                         order=("received_at", "channel", "sender", "subject", "status", None))
        waiting = InboundMessage.objects.filter(status=InboundMessage.Status.NEEDS_REVIEW).count()
    return render(request, "ai_assistant/inbox.html", {
        "rows": table, "status": status, "statuses": InboundMessage.Status.choices, "form": form,
        "open_dialog": open_dialog, "own": own, "waiting": waiting,
        "spent": claude.spent_today(company_id),
        "keeper": _keeper(request.user, company_id),
    })


def _keeper(user, company_id):
    """Whether ``user`` keeps the assistant's settings (owner or company admin)."""
    try:
        intake.require_settings_keeper(user, company_id)
    except PermissionDenied:
        return False
    return True


def _message_page(request, pk, open_dialog="", match_form=None, customer_form=None):
    company_id = request.company_id
    _, message = intake.get_message(request.user, company_id, pk)
    lines = intake.draft_lines(message)
    with use_company(company_id):
        calls = list(AiCall.objects.filter(message=message).order_by("-created_at")[:5])
    open_ = message.status not in (InboundMessage.Status.CONVERTED, InboundMessage.Status.IGNORED)
    may_edit = open_ and access.may(request.user, company_id, "sales.order")
    order = message.sales_order
    may_confirm = (open_ and order is not None and order.status == "draft" and not message.unmatched
                   and access.may(request.user, company_id, "sales.approve", order.warehouse.branch_id))
    return render(request, "ai_assistant/message.html", {
        "message": message, "lines": lines, "order": order, "calls": calls, "may_edit": may_edit,
        "may_confirm": may_confirm, "open_dialog": open_dialog,
        "match_form": match_form or forms.MatchForm(company_id=company_id, auto_id="match_%s"),
        "customer_form": customer_form or forms.CustomerForm(company_id=company_id, auto_id="customer_%s"),
        "reason_form": forms.ReasonForm(auto_id="reason_%s"),
        "may_retry": message.status in (InboundMessage.Status.FAILED, InboundMessage.Status.WAITING,
                                        InboundMessage.Status.NEW) and may_edit,
    })


@login_required
def message(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    return _message_page(request, pk)


@login_required
@require_POST
def message_action(request, pk, action):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    user, post = request.user, request.POST
    try:
        if action == "confirm":
            order = intake.confirm(actor=user, company_id=company_id, message_id=pk, credit_note=post.get("note", ""))
            sent = getattr(order, "summary_sent_to", [])
            messages.success(request, f"{order.number} confirmed: stock reserved." + (
                f" Summary sent to {len(sent)} manager(s)." if sent else ""))
            return redirect("sales:order_detail", pk=order.pk)
        if action == "customer":
            form = forms.CustomerForm(post, company_id=company_id, auto_id="customer_%s")
            if not form.is_valid():
                return _message_page(request, pk, open_dialog="customer-dialog", customer_form=form)
            intake.choose_customer(actor=user, company_id=company_id, message_id=pk,
                                   customer=form.cleaned_data["customer"])
            messages.success(request, "Customer chosen: the draft order is made.")
        elif action == "match":
            form = forms.MatchForm(post, company_id=company_id, auto_id="match_%s")
            if not form.is_valid():
                return _message_page(request, pk, open_dialog="match-dialog", match_form=form)
            data = form.cleaned_data
            intake.match_line(actor=user, company_id=company_id, message_id=pk, index=data["index"],
                              item=data["item"], qty=data["qty"], uom=data["uom"], remember=data["remember"])
            messages.success(request, f"{data['item'].code} added to the draft.")
        elif action == "skip":
            intake.skip_line(actor=user, company_id=company_id, message_id=pk, index=int(post.get("index", -1)))
            messages.success(request, "Line left out.")
        elif action == "ignore":
            intake.ignore(actor=user, company_id=company_id, message_id=pk, reason=post.get("reason", ""))
            messages.success(request, "Ignored.")
        elif action == "retry":
            message = intake.retry(actor=user, company_id=company_id, message_id=pk)
            messages.success(request, message.note or "Read again.")
    except ValidationError as exc:
        messages.error(request, _say(exc))
    except ValueError:
        messages.error(request, "That line was not found.")
    return redirect("ai_assistant:message", pk=pk)


@login_required
def settings_page(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    intake.require_settings_keeper(request.user, company_id)
    own = claude.settings_for(company_id)
    form = forms.SettingsForm(request.POST or None, instance=own, company_id=company_id)
    if request.method == "POST" and form.is_valid():
        try:
            setup.save_settings(actor=request.user, company_id=company_id, values=form.cleaned_data,
                                api_key=form.cleaned_data.get("api_key", ""),
                                imap_password=form.cleaned_data.get("imap_password", ""))
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, "AI assistant settings saved.")
            return redirect("ai_assistant:settings")
    with use_company(company_id):
        people = list(AiRecipient.objects.order_by("name"))
        calls = AiCall.objects.order_by("-created_at")[:10]
        calls = list(calls)
    return render(request, "ai_assistant/settings.html", {
        "form": form, "own": own, "people": people, "calls": calls, "spent": claude.spent_today(company_id),
        "recipient_form": forms.RecipientForm(company_id=company_id),
    })


@login_required
@require_POST
def recipient_add(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    form = forms.RecipientForm(request.POST, company_id=company_id)
    if form.is_valid():
        try:
            setup.add_recipient(actor=request.user, company_id=company_id, values=form.cleaned_data)
        except ValidationError as exc:
            messages.error(request, _say(exc))
        else:
            messages.success(request, f"{form.cleaned_data['name']} added.")
    else:
        messages.error(request, "; ".join(e for errors in form.errors.values() for e in errors))
    return redirect("ai_assistant:settings")


@login_required
@require_POST
def recipient_remove(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    try:
        setup.remove_recipient(actor=request.user, company_id=company_id, recipient_id=pk)
    except ValidationError as exc:
        messages.error(request, _say(exc))
    else:
        messages.success(request, "Removed.")
    return redirect("ai_assistant:settings")
