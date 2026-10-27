"""Plan a production from the product's page (2026-10-27,
docs/inventory/PRODUCTION_PLAN.md §8.1-8.2, §15 phase 6; production.planning).

The page shows, as you change the batches or the store, what the materials
are, where each comes from and what is short. Plan it, Make fewer, or Buy
the rest - each answers in place (base_template/fetch.py)."""

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

from base_template import fetch
from base_template.tables import render
from inventory import access, posting
from inventory.models import Item, ItemKind
from inventory.views import branch_ids
from production import forms, planning
from production.gate import company_or_bail

CODE = "production.plan"


def _product(company_id, pk):
    item = Item.all_objects.select_related("base_uom", "default_warehouse").filter(
        company_id=company_id, pk=pk).first()
    if item is None:
        raise PermissionDenied("Product not found in this company.")
    return item


def _chosen(form, initial):
    """The batches, date and store the page shows: what was typed when it is
    right, else what the page started with."""
    if form.is_bound and form.is_valid():
        data = form.cleaned_data
        return data["batches"], data["planned_date"], data["into"]
    return initial["batches"], initial["planned_date"], initial["into"]


@login_required
def plan_page(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require(request.user, company_id, CODE)
    product = _product(company_id, pk)
    hub_url = reverse("inventory:product", args=[product.pk])
    if product.kind != ItemKind.FINISHED or product.sourcing == Item.Sourcing.BUY:
        return redirect(hub_url)
    stores = planning.output_stores(company_id, branch_ids(request.user, company_id, CODE))
    into = planning.default_into(product, stores)
    try:
        planning.recipe_in_use(company_id, product)
    except ValidationError as exc:
        return render(request, "inventory/hub/plan.html", {
            "active": "make", "production": True, "item": product, "no_recipe": exc.messages[0],
            "hub_url": hub_url})
    suggestion = planning.suggest(company_id, product, into) if into else None
    initial = {"batches": (suggestion.batches if suggestion and suggestion.batches else 1),
               "planned_date": timezone.localdate(), "into": into}
    data = request.POST if request.method == "POST" else None
    if request.method != "POST" and "batches" in request.GET:
        # From a link (Make 2 batches): any box it does not say keeps its start.
        start = {"batches": initial["batches"], "planned_date": initial["planned_date"].isoformat(),
                 "into": initial["into"].pk if initial["into"] else ""}
        data = {name: request.GET.get(name) or value for name, value in start.items()}
    form = forms.PlanForm(data, stores=stores, initial=initial)
    if request.method == "POST":
        return _act(request, company_id, product, form)
    batches, day, store = _chosen(form, initial)
    found = planning.plan(company_id, product, batches, store, day) if store else None
    context = {"active": "make", "production": True, "item": product, "form": form, "plan": found,
               "suggestion": suggestion, "hub_url": hub_url, "stores": stores,
               "sees_costs": access.sees_costs(request.user, company_id),
               "can_buy": access.may(request.user, company_id, "purchase.request")}
    template = "inventory/hub/_plan_lines.html" if request.GET.get("partial") else "inventory/hub/plan.html"
    return render(request, template, context)


def _act(request, company_id, product, form):
    if not form.is_valid():
        return fetch.refused(form)
    data = form.cleaned_data
    values = {"product": product, "batches": data["batches"], "into": data["into"],
              "day": data["planned_date"]}
    try:
        if request.POST.get("action") == "buy":
            requisition = planning.buy_the_rest(actor=request.user, company_id=company_id, **values)
            return fetch.saved(f"Purchase requisition {requisition.number} made for what is short. "
                               "Send it for approval from there.",
                               reverse("purchasing:requisition_detail", args=[requisition.pk]))
        posting.check_period_open(company_id, data["planned_date"])
        order, found = planning.save(actor=request.user, company_id=company_id, **values)
    except ValidationError as exc:
        if hasattr(exc, "error_dict"):
            for name, errors in exc.error_dict.items():
                form.add_error(name if name in form.fields else None, [" ".join(e.messages) for e in errors])
            return fetch.refused(form)
        return fetch.refused(message=" ".join(exc.messages))
    text = f"{order.number} planned: {data['batches']} batch{'es' if data['batches'] != 1 else ''}."
    if found.short:
        text += " Some materials are short: it can start once they are in."
    return fetch.saved(text, reverse("production:run", args=[order.pk]))
