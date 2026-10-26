"""The Materials card on a finished good's page (production.recipes, 2026-10-26).

Each action posts, says what happened inside the card (where the page lands,
not in the message bar above) and goes back to the item."""

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from inventory.models import Item
from inventory.views import company_or_bail
from production import forms, recipes


def _item(company_id, pk):
    item = Item.all_objects.select_related("base_uom").filter(company_id=company_id, pk=pk).first()
    if item is None:
        raise PermissionDenied("Item not found in this company.")
    return item


def _back(request, item, tone, text):
    request.session["recipe_note"] = [tone, text]
    return redirect(reverse("inventory:item_detail", args=[item.pk]) + "#materials")


def _errors(form):
    return " ".join(error for errors in form.errors.values() for error in errors)


@login_required
@require_POST
def recipe_batch(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    item = _item(company_id, pk)
    form = forms.RecipeBatchForm(request.POST)
    if not form.is_valid():
        return _back(request, item, "danger", _errors(form))
    try:
        recipes.set_batch(actor=request.user, company_id=company_id, item=item,
                          batch_size=form.cleaned_data["batch_size"])
    except ValidationError as exc:
        return _back(request, item, "danger", " ".join(exc.messages))
    size = form.cleaned_data["batch_size"].normalize()
    return _back(request, item, "success",
                 f"Saved: one batch makes {size:f} {item.base_uom.code}. Now add its materials.")


@login_required
@require_POST
def recipe_add(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    item = _item(company_id, pk)
    form = forms.RecipeMaterialForm(request.POST, company_id=company_id, product=item)
    if not form.is_valid():
        return _back(request, item, "danger", _errors(form))
    data = form.cleaned_data
    try:
        recipes.add_material(actor=request.user, company_id=company_id, item=item,
                             material=data["material"], qty=data["qty"],
                             scrap_pct=data.get("scrap_pct"))
    except ValidationError as exc:
        return _back(request, item, "danger", " ".join(exc.messages))
    material = data["material"]
    return _back(request, item, "success",
                 f"Added {material.code} {material.name}: {data['qty'].normalize():f} "
                 f"{material.base_uom.code} per batch. The recipe is in use.")


@login_required
@require_POST
def recipe_remove(request, pk, material_pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    item = _item(company_id, pk)
    try:
        recipes.remove_material(actor=request.user, company_id=company_id, item=item,
                                material_id=material_pk)
    except ValidationError as exc:
        return _back(request, item, "danger", " ".join(exc.messages))
    return _back(request, item, "success", "Removed from the recipe.")
