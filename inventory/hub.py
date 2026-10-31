"""What the Products and stock pages show, worked out from real records
(2026-10-27, docs/inventory/PRODUCTION_PLAN.md §3, §7 and §15 phase 4).

No HTTP here: the pages (``inventory.hub_views``) call these. Every amount of
stock is in the stores the person may see; every money amount is shown only
to someone who sees costs (the pages decide).
"""

from dataclasses import dataclass, field
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Q, Sum
from django.urls import reverse
from django.utils import timezone

from common.tenant import use_company
from inventory import access, keep, masters
from inventory.models import (
    InventorySettings,
    Item,
    ItemCost,
    ItemKind,
    ItemUom,
    MoveType,
    StockBalance,
    StockMove,
    Warehouse,
)
from inventory.posting import settings_for

ZERO = Decimal("0")
MATERIAL_KINDS = (ItemKind.RAW, ItemKind.PACKAGING)
#: The start of a suggested code, per item type: RM-0012.
PREFIX = {ItemKind.RAW: "RM", ItemKind.PACKAGING: "PK", ItemKind.FINISHED: "FG",
          ItemKind.SPARE: "SP", ItemKind.GENERAL: "GI"}


def suggest_code(company_id, kind):
    """The next free code of the type: RM-0012 after RM-0011."""
    prefix = PREFIX.get(kind, "IT")
    codes = Item.all_objects.filter(company_id=company_id, code__startswith=f"{prefix}-").values_list(
        "code", flat=True)
    numbers = [int(code.split("-", 1)[1]) for code in codes if code.split("-", 1)[1].isdigit()]
    return f"{prefix}-{max(numbers, default=0) + 1:04d}"


@transaction.atomic
def add_item(*, actor, company_id, values, pack=None):
    """A new material or product, with the bigger unit it is bought or sold in
    (1 bag = 50 kg; 1 carton = 24 packets, at its own price) in one go."""
    item = masters.create(masters.ITEM, actor=actor, company_id=company_id, values=values)
    if pack:
        masters.add_pack_unit(actor=actor, company_id=company_id, item_id=item.pk, values=pack)
    return item


# --- stock --------------------------------------------------------------------

def _balances(company_id, actor, **filters):
    rows = StockBalance.all_objects.filter(company_id=company_id, qty_on_hand__gt=0, **filters)
    return access.scope_warehouses(rows, actor, company_id, "inventory.view", field="warehouse__branch")


def _costs(company_id):
    """``cost(item_id, store_id)``: the average cost that values stock there."""
    per_store = settings_for(company_id).valuation_scope == InventorySettings.ValuationScope.WAREHOUSE
    rows = ItemCost.all_objects.filter(company_id=company_id)
    rows = rows.exclude(warehouse=None) if per_store else rows.filter(warehouse=None)
    found = {(item_id, store_id if per_store else None): cost
             for item_id, store_id, cost in rows.values_list("item_id", "warehouse_id", "avg_cost")}
    return lambda item_id, store_id: found.get((item_id, store_id if per_store else None), ZERO)


def totals(company_id, actor, item_ids, store_id=None):
    """``({item id: on hand}, {item id: value})`` in the stores ``actor`` sees."""
    rows = _balances(company_id, actor, item_id__in=item_ids)
    if store_id:
        rows = rows.filter(warehouse_id=store_id)
    cost = _costs(company_id)
    qty, value = {}, {}
    for item_id, store_id_, total in (rows.values("item_id", "warehouse_id")
                                      .annotate(total=Sum("qty_on_hand"))
                                      .values_list("item_id", "warehouse_id", "total")):
        qty[item_id] = qty.get(item_id, ZERO) + total
        value[item_id] = value.get(item_id, ZERO) + total * cost(item_id, store_id_)
    return qty, {pk: amount.quantize(Decimal("0.01")) for pk, amount in value.items()}


def status(have, keep_qty):
    """``(tone, words)`` for a chip: out, below stock to keep, or fine."""
    if have <= 0:
        return "bad", "Out"
    if keep_qty and have < keep_qty:
        return "wait", "Low"
    return "ok", "OK"


STATUSES = (("ok", "OK"), ("wait", "Low"), ("bad", "Out"))


def in_packs(qty, item, packs):
    """``"1,152 PCS (48 CARTON)"``: the quantity, and in its biggest pack too."""
    text = f"{qty.normalize():,f} {item.base_uom.code}"
    biggest = max((p for p in packs if p.factor and qty >= p.factor), key=lambda p: p.factor, default=None)
    if biggest is not None:
        text += f" ({(qty / biggest.factor).quantize(Decimal('0.01')).normalize():,f} {biggest.uom.code})"
    return text


def packs_of(company_id, item_ids):
    """``{item id: [pack, ...]}``."""
    found = {}
    for pack in ItemUom.all_objects.filter(company_id=company_id, item_id__in=item_ids).select_related("uom"):
        found.setdefault(pack.item_id, []).append(pack)
    return found


# --- lists --------------------------------------------------------------------

@dataclass
class Line:
    """One item on the Materials or Products list."""

    item: Item
    on_hand: Decimal
    keep: Decimal
    value: Decimal
    tone: str
    words: str
    packs: list = field(default_factory=list)
    cost: Decimal = None
    expected: Decimal = None
    margin: Decimal = None
    margin_pct: Decimal = None

    @property
    def below_cost(self):
        """Priced below what one costs (2026-10-31)."""
        return self.margin is not None and self.margin < 0

    @property
    def avg_cost(self):
        """What one unit in stock is worth; None with nothing in stock."""
        return (self.value / self.on_hand).quantize(Decimal("0.01")) if self.on_hand else None

    @property
    def stock_text(self):
        return in_packs(self.on_hand, self.item, self.packs)


def lines(company_id, actor, kinds, *, category="", store="", state="", today=None, only=None):
    """The items of ``kinds`` with their stock, stock to keep and status,
    filtered; ``only`` an item id for that one item's line."""
    with use_company(company_id):
        items = Item.objects.filter(kind__in=kinds).select_related("category", "base_uom")
        if only is not None:
            items = items.filter(pk=only)
        items = list(items.order_by("code"))
    if category.isdigit():
        items = [item for item in items if item.category_id == int(category)]
    ids = [item.pk for item in items]
    qty, value = totals(company_id, actor, ids, int(store) if store.isdigit() else None)
    kept = keep.stock_to_keep(company_id, items, today)
    packs = packs_of(company_id, ids)
    cost = _costs(company_id)
    production = access.production_on(company_id)
    found = []
    for item in items:
        have = qty.get(item.pk, ZERO)
        tone, words = status(have, kept.get(item.pk))
        if state and state != tone:
            continue
        line = Line(item, have, kept.get(item.pk) or ZERO, value.get(item.pk, ZERO), tone, words,
                    packs.get(item.pk, []))
        if item.kind != ItemKind.FINISHED and item.sale_price:
            # A material that is also sold (2026-10-31): its margin, to show one
            # priced below its cost.
            line.cost = _unit_cost(item, have, value.get(item.pk), cost)
            if line.cost:
                line.margin = (item.sale_price - line.cost).quantize(Decimal("0.01"))
                line.margin_pct = (line.margin / item.sale_price * 100).quantize(Decimal("0.1"))
        if item.kind == ItemKind.FINISHED:
            # Made here: what making one is expected to cost (plan §7.4); else
            # what the stock is worth per unit.
            line.expected = None
            if production and item.sourcing != Item.Sourcing.BUY:
                from production.costing import expected_unit_cost

                line.expected = expected_unit_cost(company_id, item)
            line.cost = (line.expected.quantize(Decimal("0.01")) if line.expected is not None
                         else _unit_cost(item, have, value.get(item.pk), cost))
            if item.sale_price and line.cost:
                line.margin = (item.sale_price - line.cost).quantize(Decimal("0.01"))
                line.margin_pct = (line.margin / item.sale_price * 100).quantize(Decimal("0.1"))
        found.append(line)
    return found


def _unit_cost(item, have, value, cost):
    """Cost of one unit: what the stock is worth per unit, else the company average."""
    if have and value:
        return (value / have).quantize(Decimal("0.01"))
    average = cost(item.pk, None)
    return average.quantize(Decimal("0.01")) if average else None


# --- one item ---------------------------------------------------------------

def stock_by_batch(company_id, actor, item):
    """Each store-and-batch row with stock, oldest expiry first, with its cost."""
    cost = _costs(company_id)
    rows = list(_balances(company_id, actor, item=item).select_related("batch", "warehouse", "location")
                .order_by("batch__expiry_date", "warehouse__code", "pk"))
    for row in rows:
        unit = row.batch.unit_cost if row.batch_id and row.batch.unit_cost is not None else cost(
            item.pk, row.warehouse_id)
        row.unit_cost = unit
        row.value = (row.qty_on_hand * (unit or ZERO)).quantize(Decimal("0.01"))
    return rows


def sold(company_id, item, today=None):
    """``(sold in the last 30 days, average per day)`` in the item's unit."""
    today = today or timezone.localdate()
    per_day = keep.sold_per_day(company_id, [item.pk], today).get(item.pk, ZERO)
    return (per_day * keep.DAYS_MEASURED).quantize(Decimal("0.01")), per_day.quantize(Decimal("0.01"))


def used_in(company_id, material):
    """The products whose recipe in use takes this material, and how much."""
    from production.models import Bom, BomLine

    with use_company(company_id):
        return list(BomLine.objects.filter(item=material, bom__status=Bom.Status.ACTIVE)
                    .select_related("bom__item", "uom").order_by("bom__item__code"))


def received(company_id, actor, item, limit=20):
    """The latest stock that came in from outside: purchases and first counts."""
    return list(access.scope_warehouses(
        StockMove.all_objects.filter(company_id=company_id, item=item, qty__gt=0,
                                     move_type__in=(MoveType.RECEIPT, MoveType.OPENING)),
        actor, company_id, "inventory.view", field="warehouse__branch",
    ).select_related("warehouse", "batch").order_by("-work_date", "-pk")[:limit])


def productions(company_id, item, limit=20):
    from production.models import ProductionOrder

    with use_company(company_id):
        return list(ProductionOrder.objects.filter(item=item).select_related("uom", "output_warehouse")
                    .order_by("-planned_date", "-pk")[:limit])


PRODUCTION_TONE = {"planned": "info", "released": "wait", "completed": "ok", "closed": "ok",
                   "cancelled": "bad"}


def recipe(company_id, actor, product):
    """The recipe in use, each material with what is in stock and how many
    batches that is enough for; None when there is none."""
    from production import recipes

    found = recipes.page(company_id, product)
    if found["bom"] is None:
        return None
    ids = [line.item_id for line in found["lines"]]
    qty, _value = totals(company_id, actor, ids)
    for line in found["lines"]:
        line.in_stock = qty.get(line.item_id, ZERO)
        need = line.qty_base * (1 + line.scrap_pct / 100)
        line.enough = int(line.in_stock // need) if need else 0
    found["enough"] = min((line.enough for line in found["lines"]), default=0)
    return found


# --- the checklist (plan §3) ----------------------------------------------

def checklist(company_id, actor, today=None):
    """What is done and what is next, from the company's own records. A
    company without Production sees Products → Stock in → Sell."""
    production = access.production_on(company_id)
    with use_company(company_id):
        stores = Warehouse.objects.count()
        counts = dict(Item.objects.values("kind").annotate(n=Count("pk")).values_list("kind", "n"))
        moved = set(StockMove.objects.values_list("move_type", flat=True).distinct())
    materials = sum(counts.get(kind, 0) for kind in MATERIAL_KINDS)
    products = counts.get(ItemKind.FINISHED, 0)
    steps = [{
        "done": bool(stores), "what": "Stores set up",
        "detail": f"{stores} store{'s' if stores != 1 else ''}." if stores else
        "Add the rooms or warehouses stock is kept in.",
        "url": reverse("inventory:stores"), "button": "Stores",
    }]
    if production:
        steps.append({
            "done": bool(materials), "what": "Materials added",
            "detail": f"{materials} material{'s' if materials != 1 else ''}." if materials else
            "What you buy and use up: flour, sugar, wrappers, cartons.",
            "url": reverse("inventory:materials"), "button": "Materials",
        })
    steps.append({
        "done": bool(products), "what": "Products added",
        "detail": f"{products} product{'s' if products != 1 else ''}." if products else "What you sell.",
        "url": reverse("inventory:products"), "button": "Products",
    })
    if production and products:
        missing = _without_recipe(company_id)
        steps.append({
            "done": not missing, "what": "Recipes saved",
            "detail": ("Every product you make has its recipe." if not missing else
                       "No recipe yet: " + ", ".join(item.name for item in missing[:3])
                       + ("…" if len(missing) > 3 else "")),
            "url": reverse("inventory:product", args=[missing[0].pk]) if missing else reverse(
                "inventory:products"),
            "button": "Add recipe" if missing else "Products",
        })
    stocked = bool(moved & {MoveType.OPENING, MoveType.RECEIPT})
    steps.append({
        "done": stocked, "what": "Stock in",
        "detail": "Stock has come in." if stocked else
        "Count what you have (first count), or receive a purchase.",
        "url": reverse("inventory:stock_in_new" if not stocked else "inventory:stock_now"),
        "button": "First count" if not stocked else "Stock",
    })
    if production:
        made = MoveType.PRODUCTION_OUTPUT in moved
        steps.append({
            "done": made, "what": "First production finished",
            "detail": "Products have been made." if made else "Plan a production, start it, finish it.",
            "url": reverse("production:runs"), "button": "Make",
        })
    sold_any = MoveType.DELIVERY in moved
    steps.append({
        "done": sold_any, "what": "First sale delivered",
        "detail": "Stock has gone to customers." if sold_any else "Deliver to a customer from stock.",
        "url": reverse("inventory:sale_new"), "button": "Sell",
    })
    low = [line for line in lines(company_id, actor, list(MATERIAL_KINDS) + [ItemKind.FINISHED],
                                  today=today) if line.tone == "wait" or (line.tone == "bad" and line.keep)]
    if low and stocked:
        steps.append({
            "done": False, "what": "Below stock to keep",
            "detail": ", ".join(line.item.name for line in low[:4]) + ("…" if len(low) > 4 else ""),
            "url": reverse("inventory:materials" if production and low[0].item.kind in MATERIAL_KINDS
                           else "inventory:products") + "?status=" + low[0].tone,
            "button": "See them",
        })
    return steps


def _without_recipe(company_id):
    """Products made here with no recipe in use (or one without a material)."""
    from production.models import Bom

    with use_company(company_id):
        with_recipe = Bom.objects.filter(status=Bom.Status.ACTIVE).filter(
            Q(lines__isnull=False)).values("item_id")
        return list(Item.objects.filter(kind__in=("finished", "raw", "packaging"), status="active")
                    .exclude(sourcing=Item.Sourcing.BUY).exclude(pk__in=with_recipe).order_by("code"))


def next_from(steps):
    """The first step not done: the Next step box of the first page."""
    return next((step for step in steps if not step["done"]), None)


def recent_productions(company_id, limit=5):
    from production.models import ProductionOrder

    with use_company(company_id):
        rows = list(ProductionOrder.objects.select_related("item", "uom").order_by("-planned_date", "-pk")[:limit])
    for row in rows:
        row.tone = PRODUCTION_TONE.get(row.status, "info")
    return rows
