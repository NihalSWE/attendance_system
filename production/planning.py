"""Planning a production (2026-10-27, docs/inventory/PRODUCTION_PLAN.md §8.1,
§8.2, §11; §15 phase 6).

- **How many to make**: stock to keep − in stock − what planned productions
  will make, in whole batches, and no more than the materials allow.
- **Where the materials come from**: every usable store of the branch the
  products go into - the material's usual store first, then the others - and
  inside a store the batch that expires first. Expired batches, batches
  waiting for a quality check and quarantine, rejected or in-transit stores
  are never counted. Stock in another branch is not taken: it must be moved.
- **Short**: a plan can be saved while short (it cannot start). The screen
  says by how much, offers **Make fewer** (the most the materials allow) and
  **Buy the rest** (a purchase requisition for what is missing), and names
  other planned productions that need the same material. Nothing is held
  back until Start.
"""

import datetime
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from common.choices import ActiveStatus
from common.tenant import use_company
from inventory import fefo, keep
from inventory.models import Item, StockBalance, Warehouse
from inventory.posting import UNUSABLE_STORES
from production import orders, recipes
from production.models import Bom, ProductionOrder

ZERO = Decimal("0")
#: Productions that are planned or under way: their output is coming, and
#: (until Start takes them) their materials are wanted.
OPEN = (ProductionOrder.Status.PLANNED, ProductionOrder.Status.RELEASED)


def _up(qty, item):
    """``qty`` rounded up to what the item's unit can hold (1,015.5 bottles → 1,016)."""
    return qty.quantize(Decimal(1).scaleb(-item.base_uom.decimals), rounding=ROUND_CEILING)


@dataclass
class Take:
    store: Warehouse
    batch: object
    qty: Decimal
    location: object = None
    available: Decimal = ZERO

    @property
    def key(self):
        """How a Start screen names this line: material-store-batch."""
        return f"{self.store.pk}-{self.batch.pk if self.batch else 0}-{self.location.pk if self.location else 0}"


@dataclass
class Need:
    """One material of the plan: how much, from where, and what is missing."""

    item: Item
    per_batch: Decimal                # base unit, wastage included
    need: Decimal
    available: Decimal
    takes: list = field(default_factory=list)
    others: list = field(default_factory=list)

    @property
    def short(self):
        return max(self.need - self.available, ZERO)

    @property
    def enough_for(self):
        """Whole batches the usable stock is enough for."""
        return int((self.available / self.per_batch).to_integral_value(ROUND_FLOOR)) if self.per_batch else 0

    @property
    def stores(self):
        """``[(store, qty)]``: how much each store gives."""
        found = {}
        for take in self.takes:
            found[take.store] = found.get(take.store, ZERO) + take.qty
        return list(found.items())


@dataclass
class Plan:
    product: Item
    bom: Bom
    batches: int
    into: Warehouse
    day: datetime.date
    needs: list = field(default_factory=list)

    @property
    def makes(self):
        return self.bom.batch_size_base * self.batches

    @property
    def short(self):
        return [need for need in self.needs if need.short > 0]

    @property
    def possible(self):
        """The most whole batches the materials allow."""
        return min((need.enough_for for need in self.needs), default=0)

    @property
    def main_store(self):
        """The store most of the materials come from (the order's materials store
        until Start takes from several stores, phase 7)."""
        count = {}
        for need in self.needs:
            for store, _qty in need.stores:
                count[store] = count.get(store, 0) + 1
        return max(count, key=count.get) if count else None


def recipe_in_use(company_id, product):
    """The recipe a plan uses, or refused with what to do."""
    with use_company(company_id):
        bom = Bom.objects.filter(item=product, status=Bom.Status.ACTIVE).select_related("batch_uom").first()
        if bom is None or not bom.lines.exists():
            raise ValidationError(f"{product.name} has no recipe yet: write it on its page first.")
    return bom


def stores_of(company_id, branch_id):
    """The stores of a branch materials can be taken from."""
    with use_company(company_id):
        return list(Warehouse.objects.filter(branch_id=branch_id, status=ActiveStatus.ACTIVE)
                    .exclude(kind__in=UNUSABLE_STORES).order_by("code"))


def _usable(company_id, item, stores, day):
    """``[(store, row)]`` of usable stock: the usual store first, then the
    others; inside each store first expiry first (``inventory.fefo``)."""
    ordered = sorted(stores, key=lambda store: (store.pk != item.default_warehouse_id, store.code))
    rows = []
    for store in ordered:
        rows += [(store, row) for row in fefo.usable_rows(company_id, item, store, day)]
    return rows


def _wanted_elsewhere(company_id, branch_id, item_ids, exclude=None):
    """``{material id: [production numbers]}`` of other open productions of the
    branch whose recipe takes it."""
    with use_company(company_id):
        open_orders = (ProductionOrder.objects.filter(status__in=OPEN, output_warehouse__branch_id=branch_id)
                       .exclude(pk=exclude).select_related("bom"))
        found = {}
        for order in open_orders:
            for item_id in order.bom.lines.filter(item_id__in=item_ids).values_list("item_id", flat=True):
                found.setdefault(item_id, []).append(order.number)
    return found


def plan(company_id, product, batches, into, day=None, exclude_order=None, bom=None, makes=None):
    """What ``batches`` batches of ``product`` into store ``into`` need, and
    where from; ``bom`` and ``makes`` for a production already planned (its
    recipe, its quantity in the product's unit)."""
    day = day or timezone.localdate()
    bom = bom or recipe_in_use(company_id, product)
    stores = stores_of(company_id, into.branch_id)
    with use_company(company_id):
        lines = list(bom.lines.select_related("item__base_uom", "item__default_warehouse"))
    found = Plan(product=product, bom=bom, batches=batches, into=into, day=day)
    output = makes if makes is not None else bom.batch_size_base * batches
    others = _wanted_elsewhere(company_id, into.branch_id, [line.item_id for line in lines], exclude_order)
    for line in lines:
        per_batch = line.planned_base(bom.batch_size_base, bom.batch_size_base)
        need = _up(line.planned_base(output, bom.batch_size_base), line.item)
        rows = _usable(company_id, line.item, stores, day)
        remaining, takes = need, []
        for store, row in rows:
            if remaining <= 0:
                break
            qty = min(row.available, remaining)
            takes.append(Take(store, row.batch, qty, row.location, row.available))
            remaining -= qty
        available = sum((row.available for _store, row in rows), ZERO)
        found.needs.append(Need(line.item, per_batch, need, available, takes, others.get(line.item_id, [])))
    return found


@dataclass
class Suggestion:
    keep: Decimal
    in_stock: Decimal
    coming: Decimal                   # what open productions will make
    wanted: int                       # batches the stock to keep asks for
    possible: int                     # batches the materials allow
    per_day: Decimal

    @property
    def batches(self):
        return min(self.wanted, self.possible)

    @property
    def have(self):
        return self.in_stock + self.coming


def suggest(company_id, product, into, today=None):
    """How many batches to make (plan §8.1); None without a stock to keep."""
    today = today or timezone.localdate()
    kept = keep.stock_to_keep(company_id, [product], today).get(product.pk)
    if not kept:
        return None
    bom = recipe_in_use(company_id, product)
    with use_company(company_id):
        in_stock = StockBalance.objects.filter(item=product).aggregate(total=Sum("qty_on_hand"))["total"] or ZERO
        coming = sum((order.planned_base for order in ProductionOrder.objects.filter(
            item=product, status__in=OPEN)), ZERO)
    missing = kept - in_stock - coming
    wanted = int((missing / bom.batch_size_base).to_integral_value(ROUND_CEILING)) if missing > 0 else 0
    possible = plan(company_id, product, 1, into, today).possible
    per_day = keep.sold_per_day(company_id, [product.pk], today).get(product.pk, ZERO)
    return Suggestion(kept, in_stock, coming, wanted, possible, per_day)


def output_stores(company_id, branch_ids=None):
    """Stores finished goods can go into: finished-goods stores first."""
    with use_company(company_id):
        rows = Warehouse.objects.filter(status=ActiveStatus.ACTIVE).exclude(kind__in=UNUSABLE_STORES)
        if branch_ids is not None:
            rows = rows.filter(branch_id__in=branch_ids)
        return sorted(rows.select_related("branch"),
                      key=lambda store: (store.kind != Warehouse.Kind.FINISHED, store.name))


def default_into(product, stores):
    """The product's own store when it may be chosen, else the first finished-goods store."""
    for store in stores:
        if store.pk == product.default_warehouse_id:
            return store
    return stores[0] if stores else None


# --- saving -------------------------------------------------------------------

@transaction.atomic
def save(*, actor, company_id, product, batches, into, day):
    """The production, planned: its quantity is whole batches. Saved even when
    short (it cannot start until the materials are there)."""
    found = plan(company_id, product, batches, into, day)
    materials = found.main_store or (stores_of(company_id, into.branch_id) or [into])[0]
    order = orders.create_order(actor=actor, company_id=company_id, values={
        "item": product, "bom": found.bom, "planned_qty": found.makes, "uom": product.base_uom,
        "planned_date": day, "material_warehouse": materials, "output_warehouse": into,
        "line_name": "", "shift_name": "",
        "note": f"{batches} batch{'es' if batches != 1 else ''}"})
    return order, found


@transaction.atomic
def buy_the_rest(*, actor, company_id, product, batches, into, day):
    """A purchase requisition, waiting as a draft, for what the plan is short of."""
    from purchasing import requisitions

    found = plan(company_id, product, batches, into, day)
    short = found.short
    if not short:
        raise ValidationError("Nothing is short: there is nothing to buy.")
    made = [need.item.code for need in short if need.item.sourcing != Item.Sourcing.BUY
            and need.item.kind == "finished"]
    if made:
        raise ValidationError(f"{', '.join(made)} is made here, not bought: make it first.")
    requisition = requisitions.create_requisition(actor=actor, company_id=company_id, values={
        "branch": into.branch, "department": None, "request_date": timezone.localdate(),
        "needed_by": max(day, timezone.localdate()),
        "reason": f"For {batches} batches of {product.name} on {day:%d %b %Y}."})
    for need in short:
        requisitions.add_line(actor=actor, company_id=company_id, requisition_id=requisition.pk, values={
            "item": need.item, "qty": _up(need.short, need.item), "uom": need.item.base_uom,
            "note": f"Short for {product.code}"})
    return requisition
