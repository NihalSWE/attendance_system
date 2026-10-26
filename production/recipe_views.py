"""The Materials card on a finished good's page (production.recipes, 2026-10-26).

Each action posts, says what happened inside the card (where the page lands,
not in the message bar above) and goes back to the item."""

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from inventory import access, masters
from inventory.models import Item
from inventory.stock_views import item_page
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
    can_create = access.may(request.user, company_id, masters.CODE)
    form = forms.RecipeMaterialForm(request.POST, company_id=company_id, product=item,
                                    can_create=can_create)
    if form.is_valid():
        data = form.cleaned_data
        try:
            with transaction.atomic():
                material = data.get("material")
                if material is None:
                    # A new raw material or packaging item, made here (2026-10-26).
                    material = masters.create(masters.ITEM, actor=request.user, company_id=company_id,
                                              values=form.new_item_values())
                recipes.add_material(actor=request.user, company_id=company_id, item=item,
                                     material=material, qty=data["qty"],
                                     scrap_pct=data.get("scrap_pct"))
        except ValidationError as exc:
            _service_errors(form, exc)
        else:
            return _back(request, item, "success",
                         f"Added {material.code} {material.name}: {data['qty'].normalize():f} "
                         f"{material.base_uom.code} per batch. The recipe is in use.")
    # Refused: the modal opens again with what was written and why.
    return item_page(request, company_id, item, material_form=form, open_dialog="material-dialog")


def _service_errors(form, exc):
    """A service's reasons on the form's fields; the new item's under ``new_``."""
    if not hasattr(exc, "error_dict"):
        form.add_error(None, " ".join(exc.messages))
        return
    for field, errors in exc.error_dict.items():
        name = field if field in form.fields else f"new_{'uom' if field == 'base_uom' else field}"
        for error in errors:
            form.add_error(name if name in form.fields else None, " ".join(error.messages))


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
