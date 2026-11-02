"""The Materials card on a finished good's page (production.recipes, 2026-10-26).

Each action posts, says what happened inside the card (where the page lands,
not in the message bar above) and goes back to the item."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from base_template import fetch
from inventory import access, masters
from inventory.models import Item
from production import forms, recipes
from production.gate import company_or_bail


def _item(company_id, pk):
    item = Item.all_objects.select_related("base_uom").filter(company_id=company_id, pk=pk).first()
    if item is None:
        raise PermissionDenied("Item not found in this company.")
    return item


def _back(request, item, tone, text):
    """The answer: in place for the product page's forms (fetch), else as a
    message on the product page, at its recipe (2026-10-27)."""
    from inventory.hub_views import _page_of

    page = _page_of(item) + "#recipe"
    if fetch.wants_json(request):
        if tone == "danger":
            return fetch.refused(message=text)
        return fetch.saved(text, page)
    getattr(messages, "error" if tone == "danger" else "success")(request, text)
    return redirect(page)


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
        if fetch.wants_json(request):
            return fetch.refused(form)
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
    if request.POST.getlist("materials") and request.POST.get("source", "existing") != "new":
        return _add_many(request, company_id, item, request.POST.getlist("materials"))
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
    # Refused: the modal stays open with what was written and why.
    if fetch.wants_json(request):
        return fetch.refused(form)
    return _back(request, item, "danger", _errors(form))


def _number(text):
    from decimal import Decimal, InvalidOperation

    text = (text or "").replace(",", "").strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        raise ValueError from None


def _add_many(request, company_id, item, picked):
    """Several materials at once (2026-11-02): each chosen one with its own
    quantity (``qty_<id>``) and wastage (``scrap_<id>``); one refused, none added."""
    from decimal import Decimal

    offered = forms.RecipeMaterialForm(company_id=company_id, product=item).fields["material"].queryset
    chosen = {str(row.pk): row for row in offered.filter(pk__in=[pk for pk in picked if pk.isdigit()])}
    errors, rows = {}, []
    for pk in dict.fromkeys(picked):
        material = chosen.get(pk)
        if material is None:
            errors["materials"] = ["One of the chosen materials is not offered any more: choose again."]
            continue
        try:
            qty, scrap = _number(request.POST.get(f"qty_{pk}")), _number(request.POST.get(f"scrap_{pk}"))
        except ValueError:
            errors[f"qty_{pk}"] = ["Write a number."]
            continue
        if qty is None or qty <= 0:
            errors[f"qty_{pk}"] = [f"How much of {material.name} one batch uses, above 0."]
        elif scrap is not None and not Decimal("0") <= scrap <= Decimal("100"):
            errors[f"scrap_{pk}"] = ["Wastage is 0 to 100 %."]
        else:
            rows.append((material, qty, scrap))
    if errors:
        return fetch.refused_errors(ValidationError(errors)) if fetch.wants_json(request) else             _back(request, item, "danger", " ".join(text for texts in errors.values() for text in texts))
    try:
        with transaction.atomic():
            for material, qty, scrap in rows:
                try:
                    recipes.add_material(actor=request.user, company_id=company_id, item=item, material=material,
                                         qty=qty, scrap_pct=scrap)
                except ValidationError as exc:
                    raise ValidationError({f"qty_{material.pk}": [f"{material.name}: {text}"
                                                                  for text in exc.messages]}) from None
    except ValidationError as exc:
        return fetch.refused_errors(exc) if fetch.wants_json(request) else             _back(request, item, "danger", " ".join(exc.messages))
    names = ", ".join(f"{material.name} {qty.normalize():f} {material.base_uom.code}" for material, qty, _s in rows)
    return _back(request, item, "success", f"Added {len(rows)} material{'s' if len(rows) != 1 else ''} per batch: "
                                           f"{names}. The recipe is in use.")


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
