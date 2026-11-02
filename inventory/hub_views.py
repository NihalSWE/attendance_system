"""The Products and stock pages on real records (2026-10-27,
docs/inventory/PRODUCTION_PLAN.md §15 phase 4): Start here with its
checklist, Materials, Products and the product hub. The screens are the ones
approved on sample data (``inventory.preview_views``).

Adding and editing save with fetch (``base_template/fetch.py``): the answer
shows just above the Save button and the page goes on without a reload.
"""

from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from base_template import fetch
from base_template.tables import paginate_rows, render
from common.tenant import use_company
from inventory import access, forms, hub, masters
from inventory.hub_forms import MaterialForm, ProductForm, form_for, is_material
from inventory.models import Item, ItemCategory, ItemKind, StockMove, Warehouse
from inventory.views import company_or_bail
from production.gate import company_or_bail as production_or_bail


def _common(request, company_id, active):
    return {
        "active": active, "production": access.production_on(company_id),
        "can_manage": access.may(request.user, company_id, masters.CODE),
        "sees_costs": access.sees_costs(request.user, company_id),
    }


def _filters(request, company_id, kinds):
    with use_company(company_id):
        categories = list(ItemCategory.objects.filter(kind__in=kinds).order_by("name"))
        stores = list(access.scope_warehouses(Warehouse.objects.order_by("name"), request.user,
                                              company_id, "inventory.view"))
    chosen = {name: request.GET.get(name, "").strip() for name in ("category", "store", "status")}
    return {"categories": categories, "stores": stores, "statuses": hub.STATUSES, **chosen,
            "filtered": any(chosen.values())}


class _Row(list):
    """A list row: its cells for search and sort, and its line for the page."""

    def __init__(self, cells, line):
        super().__init__(cells)
        self.line = line


def _table(request, found, cells):
    rows = [_Row(cells(line), line) for line in found]
    return paginate_rows(request, rows, columns=len(cells(found[0])) if found else 1)


# --- Start here -------------------------------------------------------------

@login_required
def start(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require(request.user, company_id, "inventory.view")
    context = _common(request, company_id, "start")
    steps = hub.checklist(company_id, request.user)
    kinds = ([*hub.MATERIAL_KINDS] if context["production"] else []) + [ItemKind.FINISHED]
    found = hub.lines(company_id, request.user, kinds)
    materials = [line for line in found if line.item.kind in hub.MATERIAL_KINDS]
    products = [line for line in found if line.item.kind == ItemKind.FINISHED]
    return render(request, "inventory/hub/start.html", {
        **context, "steps": steps, "next": hub.next_from(steps),
        "done": sum(1 for step in steps if step["done"]),
        "low_materials": [line for line in materials if line.tone != "ok"],
        "low_products": [line for line in products if line.tone != "ok"],
        "value": sum((line.value for line in found), hub.ZERO),
        "productions": hub.recent_productions(company_id) if context["production"] else [],
    })


# --- Materials --------------------------------------------------------------

def _material_cells(sees_costs, can_manage=False):
    def cells(line):
        row = [line.item.code, line.item.name, line.item.get_kind_display(), line.on_hand, line.keep]
        if sees_costs:
            row.append(line.value)
        return row + [line.words] + ([""] if can_manage else [])     # the Edit button's column
    return cells


@login_required
def materials(request):
    company_id, bail = production_or_bail(request)
    if bail:
        return bail
    if request.method == "POST":
        return _add(request, company_id, MaterialForm)
    access.require(request.user, company_id, "inventory.view")
    context = _common(request, company_id, "materials")
    # By type (2026-11-02): raw materials, packaging and finished goods each
    # on their own tab; "All materials" is raw and packaging together.
    kind = request.GET.get("kind", "").strip()
    kinds = [kind] if kind in dict(MATERIAL_TABS) else list(hub.MATERIAL_KINDS)
    filters = _filters(request, company_id, kinds)
    found = hub.lines(company_id, request.user, kinds, category=filters["category"],
                      store=filters["store"], state=filters["status"])
    every = hub.lines(company_id, request.user, [*hub.MATERIAL_KINDS, ItemKind.FINISHED])
    materials = [line for line in every if line.item.kind in hub.MATERIAL_KINDS]
    table = _table(request, found, _material_cells(context["sees_costs"], context["can_manage"]))
    counts = {value: sum(1 for line in every if line.item.kind == value) for value in dict(MATERIAL_TABS)}
    keep = request.GET.copy()
    for name in ("kind", "page", "table", "draw", "start", "length"):
        keep.pop(name, None)
    return render(request, "inventory/hub/materials.html", {
        **context, **filters, "rows": table, "count": len(materials),
        "low": sum(1 for line in materials if line.tone != "ok"),
        "value": sum((line.value for line in materials), hub.ZERO),
        "kind": kind if kind in dict(MATERIAL_TABS) else "",
        "tabs": [{"kind": "", "label": "All materials", "count": len(materials),
                  "about": "Everything you buy or make and use up in production."}]
        + [{"kind": value, "label": label, "count": counts[value], "about": about}
           for value, (label, about) in MATERIAL_TABS],
        "keep_query": keep.urlencode(),
        "form": MaterialForm(company_id=company_id) if context["can_manage"] else None,
    })


MATERIAL_TABS = (
    (ItemKind.RAW, ("Raw materials", "What goes into the product: fruit pulp, sugar, flour, chemicals.")),
    (ItemKind.PACKAGING, ("Packaging", "What the product is packed in: bottles, caps, labels, cartons.")),
    (ItemKind.FINISHED, ("Finished goods", "What you make or buy to sell. Each opens on its product page.")),
)


@login_required
def material(request, pk):
    company_id, bail = production_or_bail(request)
    if bail:
        return bail
    access.require(request.user, company_id, "inventory.view")
    item = _item(company_id, pk)
    if not is_material(item):
        return redirect(_page_of(item))
    context = _common(request, company_id, "materials")
    [line] = hub.lines(company_id, request.user, [item.kind], only=item.pk)
    used = hub.used_in(company_id, item)
    batches = hub.stock_by_batch(company_id, request.user, item)
    stock_in = reverse("inventory:stock_in_new") + f"?item={item.pk}"
    # A new material is put in stock first (2026-10-31): the quantity and the
    # buy price come in there, not on the material.
    if not line.on_hand and not batches:
        next_step = ("Put it in stock: the quantity and the buy price come in with Stock in.",
                     stock_in, "Put it in stock")
    elif not used:
        next_step = ("Add it to the recipe of a product that uses it.", reverse("inventory:products"),
                     "Products")
    else:
        product = used[0].bom.item
        next_step = (f"See {product.name}, which uses it, and make it.",
                     reverse("inventory:product", args=[product.pk]), product.name)
    parts = _item_parts(request, company_id, item)
    makes = context["production"] and item.sourcing != Item.Sourcing.BUY
    recipe = hub.recipe(company_id, request.user, item) if makes else None
    making = _making(request, company_id, item, recipe, parts["packs"]) if makes else {}
    if makes and recipe is None:
        next_step = ("Write its recipe: what one batch makes and what it uses.",
                     _page_of(item) + "#recipe", "Write the recipe")
    return render(request, "inventory/hub/material.html", {
        **context, **parts, **making, "line": line, "used": used, "makes": makes, "recipe": recipe,
        "batches": batches, "next_step": next_step,
    })


# --- Products ---------------------------------------------------------------

def _product_cells(production, sees_costs, can_manage=False):
    def cells(line):
        row = [line.item.code, line.item.name]
        if production:
            row.append(line.item.get_sourcing_display())
        row += [line.on_hand, line.item.sale_price or hub.ZERO]
        if sees_costs:
            row += [line.cost or hub.ZERO, line.margin or hub.ZERO]
        return row + [line.words] + ([""] if can_manage else [])
    return cells


@login_required
def products(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    if request.method == "POST":
        return _add(request, company_id, ProductForm)
    access.require(request.user, company_id, "inventory.view")
    context = _common(request, company_id, "products")
    filters = _filters(request, company_id, [ItemKind.FINISHED])
    found = hub.lines(company_id, request.user, [ItemKind.FINISHED], category=filters["category"],
                      store=filters["store"], state=filters["status"])
    table = _table(request, found, _product_cells(context["production"], context["sees_costs"],
                                                  context["can_manage"]))
    return render(request, "inventory/hub/products.html", {
        **context, **filters, "rows": table, "count": len(found),
        "form": ProductForm(company_id=company_id) if context["can_manage"] else None,
    })


@login_required
def product(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require(request.user, company_id, "inventory.view")
    item = _item(company_id, pk)
    if item.kind != ItemKind.FINISHED:
        return redirect(_page_of(item))
    context = _common(request, company_id, "products")
    [line] = hub.lines(company_id, request.user, [ItemKind.FINISHED], only=item.pk)
    makes = context["production"] and item.sourcing != Item.Sourcing.BUY
    buys = item.sourcing != Item.Sourcing.MAKE or not context["production"]
    recipe = hub.recipe(company_id, request.user, item) if makes else None
    sold, per_day = hub.sold(company_id, item)
    parts = _item_parts(request, company_id, item)
    making = _making(request, company_id, item, recipe, parts["packs"]) if makes else {}
    if makes and recipe is None:
        next_step = ("Write its recipe: what one batch makes and the materials it uses.",
                     reverse("inventory:product", args=[item.pk]) + "#recipe", "Write the recipe")
    elif not item.sale_price and parts["price_cost"] is not None:
        next_step = ("The cost is known: set its sale price.", reverse("inventory:product", args=[item.pk]) + "#price",
                     "Set the price")
    elif line.tone != "ok" and makes:
        next_step = ("Stock is below what you keep: plan a production.",
                     reverse("production:plan", args=[item.pk]), "Make")
    elif line.tone != "ok":
        next_step = ("Stock is below what you keep: receive a purchase.",
                     reverse("inventory:stock_in_new") + f"?item={item.pk}", "Stock in")
    else:
        next_step = ("Sell it: deliver to a customer from stock.", reverse("inventory:sale_new"), "Sell")
    productions = hub.productions(company_id, item) if makes else []
    steps = _product_steps(item, makes, making.get("has_recipe", False), parts["price_cost"], line, productions,
                           sold, context["sees_costs"])
    return render(request, "inventory/hub/product.html", {
        **context, **parts, **making, "line": line, "makes": makes, "steps": steps,
        "step_no": {step["key"]: step["number"] for step in steps},
        "buys": buys, "recipe": recipe, "sold": sold, "per_day": per_day,
        "batches": hub.stock_by_batch(company_id, request.user, item),
        "productions": productions,
        "tones": hub.PRODUCTION_TONE,
        "received": hub.received(company_id, request.user, item) if buys else [],
        "can_plan": access.may(request.user, company_id, "production.plan"),
        "next_step": next_step,
    })


def _product_steps(item, makes, has_recipe, cost, line, productions, sold, sees_costs):
    """The product page in the order the work is done (2026-11-01): each step
    done or not, the first not done marked "now", each with its section."""
    made = any(p.good_qty_base for p in productions)
    found = [("details", "Details and units", True, "#details")]
    if makes:
        found += [("recipe", "Recipe", has_recipe, "#recipe")]
        if sees_costs:
            found += [("cost", "Cost of one batch", cost is not None, "#costs")]
        found += [("price", "Sale price", bool(item.sale_price), "#price"),
                  ("make", "Make", made, "#make")]
    else:
        found += [("stock_in", "Stock in", bool(line.on_hand) or cost is not None, "#stock-in"),
                  ("price", "Sale price", bool(item.sale_price), "#price")]
    found += [("sell", "Stock and sell", bool(sold), "#stock")]
    steps, now = [], False
    for number, (key, title, done, anchor) in enumerate(found, 1):
        state = "done" if done else ("later" if now else "now")
        now = now or not done
        steps.append({"key": key, "number": number, "title": title, "state": state, "anchor": anchor})
    return steps


def _making(request, company_id, item, recipe, packs):
    """The recipe's and the cost sheet's forms and numbers (plan §7.3, §7.4)."""
    from production import costing
    from production import forms as production_forms

    can_edit = access.may(request.user, company_id, "production.bom")
    sheet = costing.sheet(company_id, item)
    if recipe is not None and sheet is not None:
        amounts = {row.line.pk: row for row in sheet.materials}
        for line in recipe["lines"]:
            line.cost_row = amounts.get(line.pk)
    found = {"sheet": sheet, "per_pack": sheet.per_pack(packs) if sheet else [], "can_edit_recipe": can_edit,
             "can_plan": access.may(request.user, company_id, "production.plan")}
    # How many to make (plan §8.1), once the recipe has its materials.
    if recipe is not None and recipe["lines"]:
        from production import planning

        into = planning.default_into(item, planning.output_stores(company_id))
        found["suggestion"] = planning.suggest(company_id, item, into) if into else None
        found["has_recipe"] = True
    if can_edit:
        material_form = production_forms.RecipeMaterialForm(
            company_id=company_id, product=item, can_create=access.may(request.user, company_id, masters.CODE))
        material_form.fields["qty"].unit_from = "material new_uom"
        material_form.fields["scrap_pct"].unit_text = "%"
        material_form.fields["scrap_pct"].label = "Normal wastage"
        if "new_uom" in material_form.fields:
            material_form.fields["new_uom"].widget.option_attrs = lambda rows: {
                pk: {"data-unit": code} for pk, code in rows.values_list("pk", "code")}
        batch_form = production_forms.RecipeBatchForm(
            initial={"batch_size": recipe["bom"].batch_size.normalize() if recipe else None})
        batch_form.fields["batch_size"].unit_text = item.base_uom.code
        found.update({"material_form": material_form, "batch_form": batch_form,
                      "cost_form": production_forms.CostLineForm(company_id=company_id)})
    return found


# --- shared ---------------------------------------------------------------------

def _item(company_id, pk):
    with use_company(company_id):
        item = Item.objects.select_related("category", "base_uom", "default_warehouse").filter(pk=pk).first()
    if item is None:
        raise Http404("Not found in this company.")
    return item


def _page_of(item):
    """An item's own page: the material page, the product hub, or the item page."""
    if is_material(item):
        return reverse("inventory:material", args=[item.pk])
    if item.kind == ItemKind.FINISHED:
        return reverse("inventory:product", args=[item.pk])
    return reverse("inventory:item_detail", args=[item.pk])


def _list_of(item):
    return reverse("inventory:materials" if is_material(item) else "inventory:products")


def _item_parts(request, company_id, item):
    """What the material page and the product hub share: its units and prices,
    the Edit and Pack forms, and whether it can be deleted."""
    can_manage = access.may(request.user, company_id, masters.CODE)
    packs = hub.packs_of(company_id, [item.pk]).get(item.pk, [])
    for pack in packs:
        pack.price = pack.sale_price if pack.sale_price is not None else (
            item.sale_price * pack.factor if item.sale_price else None)
    with use_company(company_id):
        moved = StockMove.objects.filter(item=item).exists()
    # The sale price beside its cost (2026-10-31, inventory.pricing).
    from inventory import pricing

    cost = pricing.cost_per_unit(company_id, item)
    margin = (item.sale_price - cost).quantize(pricing.CENT) if cost is not None and item.sale_price else None
    from inventory.posting import settings_for
    from production.costing import last_cost

    last = last_cost(company_id, item)
    price = {"price_cost": cost, "price_margin": margin, "can_price": can_manage,
             # Which cost that is (2026-11-02): the last price paid, or the average.
             "price_cost_from": settings_for(company_id).price_cost, "last_cost": last,
             "price_margin_pct": (margin / item.sale_price * 100).quantize(Decimal("0.1"))
             if margin is not None else None}
    # Edit from a list's row (2026-10-31): the page opens with its Edit modal
    # open, and saving goes back to that list.
    back = request.GET.get("back", "")
    # Its batches waiting for a quality check (2026-11-01, inventory/qc.py).
    from inventory import qc

    return {
        **price,
        "qc_waiting": qc.waiting(company_id, item=item),
        "can_qc": access.may(request.user, company_id, qc.CODE),
        "item": item, "packs": packs, "list_url": _list_of(item), "moved": moved,
        "edit_open": can_manage and request.GET.get("edit") == "1",
        "edit_next": back if back in (reverse("inventory:materials"), reverse("inventory:products")) else "",
        "edit_form": form_for(item, company_id=company_id) if can_manage else None,
        "pack_form": forms.PackUnitForm(company_id=company_id, item=item) if can_manage else None,
    }


def _add(request, company_id, form_class):
    access.require(request.user, company_id, masters.CODE)
    form = form_class(request.POST, company_id=company_id)
    if not form.is_valid():
        return fetch.refused(form)
    try:
        item = hub.add_item(actor=request.user, company_id=company_id, values=form.item_values(),
                            pack=form.pack_values())
    except ValidationError as exc:
        _service_errors(form, exc)
        return fetch.refused(form)
    return fetch.saved(f"{item.code} {item.name} added.", _page_of(item))


def _service_errors(form, exc):
    from common.forms import apply_service_errors

    apply_service_errors(form, exc)


@login_required
@require_POST
def item_price(request, pk):
    """Set the sale price of one base unit (2026-10-31): below the cost only
    when "Sell below cost" is ticked with a reason."""
    from inventory import pricing

    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    item = _item(company_id, pk)
    text = request.POST.get("price", "").replace(",", "").strip()
    try:
        price = Decimal(text) if text else None
    except InvalidOperation:
        price = None
    below = request.POST.get("below_cost") == "on"
    reason = request.POST.get("reason", "").strip() if below else ""
    if below and not reason:
        return fetch.refused_errors(ValidationError({"reason": "Say why it is sold below cost."}))
    try:
        pricing.set_price(actor=request.user, company_id=company_id, item=item, price=price, reason=reason)
    except ValidationError as exc:
        return fetch.refused_errors(exc)
    return fetch.saved(f"{item.name}: {price:,.2f} per {item.base_uom.code}.", _page_of(item) + "#price")


@login_required
@require_POST
def item_save(request, pk):
    """Edit a material or product from its page."""
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    try:
        _, item = masters.get_for_edit(masters.ITEM, actor=request.user, company_id=company_id, pk=pk)
    except PermissionDenied as exc:
        return fetch.refused(message=str(exc) or "You do not have access to do this.", status=403)
    form = form_for(item, request.POST, company_id=company_id)
    if not form.is_valid():
        return fetch.refused(form)
    try:
        item = masters.update_item(actor=request.user, company_id=company_id, pk=pk,
                                   values=form.item_values())
    except ValidationError as exc:
        _service_errors(form, exc)
        return fetch.refused(form)
    back = request.POST.get("next", "")
    if back not in (reverse("inventory:materials"), reverse("inventory:products")):
        back = _page_of(item)
    return fetch.saved(f"{item.code} {item.name} saved.", back)

