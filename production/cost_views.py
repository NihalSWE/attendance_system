"""Cost rates, and the costs of one batch on a product's page (2026-10-27,
docs/inventory/PRODUCTION_PLAN.md §7.4). Every form saves with fetch
(``base_template/fetch.py``): the answer shows above its Save button."""

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.views.decorators.http import require_POST

from base_template import fetch
from base_template.tables import render
from common.forms import apply_service_errors
from common.tenant import use_company
from inventory import access
from inventory.models import Item
from production import costing, forms
from production.gate import company_or_bail
from production.models import CostRate


def _refused(form, exc):
    apply_service_errors(form, exc)
    return fetch.refused(form)


@login_required
def cost_rates(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    if request.method == "POST":
        form = forms.CostRateForm(request.POST, company_id=company_id)
        if not form.is_valid():
            return fetch.refused(form)
        try:
            rate = costing.add_rate(actor=request.user, company_id=company_id, values=form.cleaned_data)
        except ValidationError as exc:
            return _refused(form, exc)
        return fetch.saved(f"{rate.name}: {rate.rate.normalize():f} Tk per hour added.",
                           reverse("production:cost_rates"))
    access.require(request.user, company_id, ("inventory.view", costing.CODE))
    can_edit = access.may(request.user, company_id, costing.CODE)
    with use_company(company_id):
        rates = list(CostRate.objects.order_by("kind", "name"))
        used = dict(CostRate.objects.filter(lines__isnull=False).values_list("pk", "lines__item__name"))
    for rate in rates:
        rate.form = forms.CostRateForm(instance=rate, company_id=company_id, prefix=f"r{rate.pk}") if can_edit else None
        rate.used_by = used.get(rate.pk)
        rate.dialog_id = f"rate-{rate.pk}"
    return render(request, "inventory/hub/cost_rates.html", {
        "active": "rates", "production": True, "rates": rates, "can_edit": can_edit,
        "sees_costs": access.sees_costs(request.user, company_id),
        "form": forms.CostRateForm(company_id=company_id) if can_edit else None,
    })


def _rate(company_id, pk):
    with use_company(company_id):
        rate = CostRate.objects.filter(pk=pk).first()
    if rate is None:
        raise PermissionDenied("Rate not found in this company.")
    return rate


@login_required
@require_POST
def cost_rate_save(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    rate = _rate(company_id, pk)
    form = forms.CostRateForm(request.POST, instance=rate, company_id=company_id, prefix=f"r{pk}")
    if not form.is_valid():
        return fetch.refused(form)
    try:
        rate = costing.update_rate(actor=request.user, company_id=company_id, pk=pk, values=form.cleaned_data)
    except ValidationError as exc:
        return _refused(form, exc)
    return fetch.saved(f"{rate.name} saved. Productions from now on use {rate.rate.normalize():f} Tk per hour.",
                       reverse("production:cost_rates"))


@login_required
@require_POST
def cost_rate_status(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    active = request.POST.get("status") == "active"
    rate = costing.set_rate_status(actor=request.user, company_id=company_id, pk=pk, active=active)
    return fetch.saved(f"{rate.name} is {'active again' if active else 'switched off: no new cost can use it'}.",
                       reverse("production:cost_rates"))


def _product(company_id, pk):
    item = Item.all_objects.select_related("base_uom").filter(company_id=company_id, pk=pk).first()
    if item is None:
        raise PermissionDenied("Product not found in this company.")
    return item


def _hub(item):
    from inventory.hub_views import _page_of

    return _page_of(item) + "#costs"


@login_required
@require_POST
def cost_line_add(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    item = _product(company_id, pk)
    form = forms.CostLineForm(request.POST, company_id=company_id)
    if not form.is_valid():
        return fetch.refused(form)
    try:
        line = costing.add_line(actor=request.user, company_id=company_id, item=item, values=form.cleaned_data)
    except ValidationError as exc:
        return _refused(form, exc)
    return fetch.saved(f"{line.name} added to the cost of one batch.", _hub(item))


@login_required
@require_POST
def cost_line_remove(request, pk, line_pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    item = _product(company_id, pk)
    try:
        costing.remove_line(actor=request.user, company_id=company_id, item=item, line_id=line_pk)
    except ValidationError as exc:
        return fetch.refused(message=" ".join(exc.messages))
    return fetch.saved("Removed from the cost of one batch.", _hub(item))
