"""How a made product is sold (2026-11-03): as it is, and in bottles.

A factory makes orange juice in bulk (ML) and sells it two ways: the liquid
itself, by the ML or the L, and filled into 250 ml, 500 ml or 1 L bottles.
A bottle size is a product of its own (counted in PCS, its own stock and
price) whose recipe takes the juice and the empty bottle, cap and label, so
filling bottles takes the juice out of stock with the bottles, and the cost
of one bottle is the juice in it + its packaging + the bottling cost.

``add_size`` makes that product and its recipe in one go; ``ways`` lists the
ways the product is sold: as it is, and every product made from it.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from inventory import hub, masters, pricing
from inventory.doc_common import check_qty
from inventory.models import Item, ItemKind
from production import costing, recipes

ZERO = Decimal("0")


@dataclass
class Way:
    """One way the product is sold."""

    item: Item
    uses: Decimal | None        # how much of the product one unit holds; None: the product itself
    cost: Decimal | None
    on_hand: Decimal
    packs: list = field(default_factory=list)   # its bigger units: 1 CARTON = 24 PCS

    @property
    def price(self):
        return self.item.sale_price

    @property
    def margin(self):
        if self.cost is None or not self.price:
            return None
        return (self.price - self.cost).quantize(pricing.CENT)

    @property
    def margin_pct(self):
        margin = self.margin
        return None if margin is None else (margin / self.price * 100).quantize(Decimal("0.1"))

    @property
    def price_per_bulk_unit(self):
        """The price of one unit of the product when sold this way (0.38 a ML in a bottle)."""
        if not self.uses or not self.price:
            return None
        return (self.price / self.uses).quantize(Decimal("0.0001"))


def ways(company_id, actor, product):
    """``[Way]``: the product as it is first, then each product made from it."""
    filled = []
    for line in hub.used_in(company_id, product):
        made = line.bom.item
        if made.status != "active" or not line.bom.batch_size_base:
            continue
        filled.append((made, (line.qty_base / line.bom.batch_size_base).quantize(Decimal("0.0001")).normalize()))
    ids = [product.pk] + [made.pk for made, _uses in filled]
    qty, _value = hub.totals(company_id, actor, ids)
    packs = hub.packs_of(company_id, ids)
    return [Way(item, uses, pricing.cost_per_unit(company_id, item), qty.get(item.pk, ZERO), packs.get(item.pk, []))
            for item, uses in [(product, None), *filled]]


def size_name(product, size):
    """``Orange Juice 250 ml``; 1,000 ML is ``1 L``, 1,000 G is ``1 KG``."""
    unit = product.base_uom.code.upper()
    bigger = {"ML": "L", "G": "KG", "GM": "KG"}
    if unit in bigger and size >= 1000 and size % 1000 == 0:
        return f"{product.name} {(size / 1000).normalize():f} {bigger[unit]}"
    return f"{product.name} {size.normalize():f} {product.base_uom.code.lower()}"


@transaction.atomic
def add_size(*, actor, company_id, product, size, unit, containers=(), name="", code="",
             bottling_cost=None, sale_price=None, carton=None, carton_factor=None):
    """A bottle size of ``product``: a new product counted in ``unit`` (PCS),
    made here, with ``product``'s category, batches, expiry and QC, whose
    recipe for one is ``size`` of ``product`` + one of each container. With a
    bottling cost per bottle, a sale price and a carton when given."""
    if product.kind != ItemKind.FINISHED or product.sourcing == Item.Sourcing.BUY:
        raise ValidationError("Only a product made here is filled into bottles.")
    if size is None or size <= 0:
        raise ValidationError({"size": "Write how much one bottle holds, above 0."})
    check_qty(product, size, Decimal("1"), field="size")
    if unit is None:
        raise ValidationError({"unit": "Choose how the bottles are counted."})
    if unit.pk == product.base_uom_id:
        raise ValidationError({"unit": f"Bottles are counted in their own unit, not {unit.code}."})
    for material in containers:
        if material.kind not in recipes.MATERIAL_KINDS:
            raise ValidationError({"containers": f"{material.code} is not a material or a packaging item."})
    if carton_factor and carton is None:
        raise ValidationError({"carton_factor": "There is no carton unit: add one in Units first."})
    name = (name or "").strip() or size_name(product, size)
    if Item.all_objects.filter(company_id=company_id, name__iexact=name).exists():
        raise ValidationError({"name": f"{name} exists already."})
    bottle = masters.create(masters.ITEM, actor=actor, company_id=company_id, values={
        "code": (code or "").strip() or hub.suggest_code(company_id, ItemKind.FINISHED),
        "name": name, "kind": ItemKind.FINISHED, "category": product.category, "base_uom": unit,
        "sourcing": Item.Sourcing.MAKE, "track_batch": product.track_batch, "track_expiry": product.track_expiry,
        "shelf_life_days": product.shelf_life_days, "qc_required": product.qc_required,
        "default_warehouse": product.default_warehouse, "vat_rate": product.vat_rate})
    recipes.set_batch(actor=actor, company_id=company_id, item=bottle, batch_size=Decimal("1"))
    recipes.add_material(actor=actor, company_id=company_id, item=bottle, material=product, qty=size)
    for material in containers:
        recipes.add_material(actor=actor, company_id=company_id, item=bottle, material=material,
                             qty=Decimal("1"))
    if bottling_cost:
        costing.add_line(actor=actor, company_id=company_id, item=bottle, values={
            "how": "fixed", "label": "Bottling", "amount": bottling_cost})
    if carton_factor:
        masters.add_pack_unit(actor=actor, company_id=company_id, item_id=bottle.pk,
                              values={"uom": carton, "factor": carton_factor})
    if sale_price is not None:
        cost = pricing.cost_per_unit(company_id, bottle)
        if cost is not None and sale_price < cost:
            raise ValidationError({"sale_price": (
                f"Below cost: one filled bottle costs {cost:,.2f} (the juice in it, the empty bottle, cap, "
                "label and bottling). Give a higher price, or leave it empty and set it on the bottle's own "
                "page (Sell below cost, with a reason).")})
        pricing.set_price(actor=actor, company_id=company_id, item=bottle, price=sale_price, field="sale_price")
    return bottle
