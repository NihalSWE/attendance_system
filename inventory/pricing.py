"""Sale prices, set only when the cost is known, and never below it by mistake
(2026-10-31, the inventory update list items 2-5).

- No sale price when an item is added: a price typed before the cost is known is
  a guess, and a wrong guess (50 for something that costs 70) sells at a loss.
- The price is set on the item's page (with the cost and the margin beside it,
  and a price suggested for a margin), or on a Stock in line when the buy price
  is in front of you.
- **Below cost** - set, or sold on a Sell line - is refused unless it is meant:
  "Sell below cost" ticked and a reason, which the audit log keeps.

The cost is what one unit is worth now: its average cost in stock, else (made
here) what making one costs, else the expected buy price (``costing.unit_cost``).
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from auditlog.services import record_company_event
from common.tenant import use_company
from inventory import access, masters
from inventory.models import Item, ItemUom

CENT = Decimal("0.01")


def cost_per_unit(company_id, item):
    """What one base unit costs now; None when nothing says."""
    from production.costing import unit_cost

    cost = unit_cost(company_id, item)
    return cost.quantize(CENT) if cost else None


def suggest(cost, margin_pct):
    """The price that leaves ``margin_pct`` % of it as margin: cost 10, 30 % -> 14.29."""
    if not cost or margin_pct is None or margin_pct >= 100:
        return None
    return (cost / (1 - Decimal(margin_pct) / 100)).quantize(CENT)


def check(company_id, item, price, *, factor=Decimal("1"), reason="", field="price"):
    """Refuse ``price`` (for one unit of ``factor`` base units) below its cost
    unless a reason is given. Returns the cost of that unit, or None."""
    cost = cost_per_unit(company_id, item)
    if cost is None or price is None:
        return None
    unit = (cost * factor).quantize(CENT)
    if price < unit and not (reason or "").strip():
        raise ValidationError({field: (
            f"Below cost: one costs {unit:,.2f}, the price is {price:,.2f} - a loss of {unit - price:,.2f}. "
            "If you mean it, tick Sell below cost and say why.")})
    return unit


@transaction.atomic
def set_price(*, actor, company_id, item, price, uom=None, reason="", field="price"):
    """The sale price of one base unit, or of one pack (``uom``) of the item."""
    membership = access.require(actor, company_id, masters.CODE)
    if price is None or price < 0:
        raise ValidationError({field: "Give a price of 0 or more."})
    factors = masters.units_for(item)
    uom_id = getattr(uom, "pk", uom) or item.base_uom_id
    if uom_id not in factors:
        raise ValidationError({field: "That is not one of the item's units."})
    cost = check(company_id, item, price, factor=factors[uom_id], reason=reason, field=field)
    with use_company(company_id):
        if uom_id == item.base_uom_id:
            before = item.sale_price
            Item.objects.filter(pk=item.pk).update(sale_price=price)
            item.sale_price = price
        else:
            pack = ItemUom.objects.get(item=item, uom_id=uom_id)
            before = pack.sale_price
            pack.sale_price = price
            pack.save(update_fields=["sale_price"])
    record_company_event(actor=actor, membership=membership, company=membership.company,
                         action="inventory.item.price_set", obj=item,
                         before={"price": masters.plain(before)},
                         after={"price": masters.plain(price), "unit": uom_id, "cost": masters.plain(cost),
                                "below_cost_reason": (reason or "").strip()})
    return price
