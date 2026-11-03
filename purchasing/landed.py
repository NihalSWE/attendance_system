"""The real cost of what is bought (2026-11-03).

What the supplier charges is not all a purchase costs: the delivery (transport,
loading, unloading) is paid too, and so is the VAT, which only a VAT-registered
company claims back. Both are added to the goods, so stock, recipes, the cost of
one product, prices and profit are counted at what the goods really cost.

One goods receipt's bill::

    Subtotal  = each accepted line's quantity x cost (the supplier's price)
    VAT       = the % of the subtotal, or the amount written
    Delivery  = the delivery / carrying cost written
    Total paid = subtotal + VAT + delivery

The extra costs - the delivery, plus the VAT unless the company claims it back
(Inventory settings, "VAT paid on purchases") - are shared over the lines by
their value: a line worth half the subtotal carries half. Each line's real cost
is its price plus its share, and that is the cost it goes into stock at
(``post_receipt``). The shares are rounded to the paisa and add up to the extra
costs exactly. Stock in and a receipt against a purchase order work the same
way: both are goods receipts.
"""

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal("0")
CENT = Decimal("0.01")
HUNDRED = Decimal("100")


@dataclass
class Bill:
    subtotal: Decimal
    vat_rate: Decimal
    vat: Decimal
    delivery: Decimal
    vat_in_cost: bool
    shares: dict = field(default_factory=dict)    # line pk (or index) -> its extra cost

    @property
    def total(self):
        return self.subtotal + self.vat + self.delivery

    @property
    def extra(self):
        """What is added to the goods' cost."""
        return self.delivery + (self.vat if self.vat_in_cost else ZERO)


def vat_in_cost(company_id):
    """Whether this company's VAT paid on purchases is part of the goods' cost."""
    from inventory.models import InventorySettings
    from inventory.posting import settings_for

    return settings_for(company_id).purchase_vat == InventorySettings.PurchaseVat.COST


def vat_on(subtotal, *, by, rate, amount):
    """``(rate, amount)``: a % follows the subtotal; an amount stays as written
    and its % is worked out."""
    from purchasing.models import GoodsReceipt

    if by == GoodsReceipt.VatBy.AMOUNT:
        amount = (amount or ZERO).quantize(CENT)
        return ((amount / subtotal * HUNDRED).quantize(CENT) if subtotal else ZERO), amount
    rate = rate or ZERO
    return rate, (subtotal * rate / HUNDRED).quantize(CENT, ROUND_HALF_UP)


def share(extra, weights):
    """``extra`` shared over ``{key: weight}`` in proportion, rounded to the
    paisa; what rounding leaves over goes to the largest, so the shares add
    up to ``extra`` exactly. With no weight at all, nothing is shared."""
    total = sum(weights.values(), ZERO)
    if not extra or not total:
        return {key: ZERO for key in weights}
    shares = {key: (extra * weight / total).quantize(CENT, ROUND_HALF_UP) for key, weight in weights.items()}
    left = extra - sum(shares.values(), ZERO)
    if left:
        largest = max(weights, key=lambda key: weights[key])
        shares[largest] += left
    return shares


def bill(receipt, lines, in_cost=None):
    """The bill of ``receipt`` with these lines. A posted receipt keeps what
    it was posted with; a draft follows the company's setting today."""
    if in_cost is None:
        in_cost = receipt.vat_in_cost if receipt.status == receipt.Status.POSTED else vat_in_cost(receipt.company_id)
    subtotal = sum((line.value for line in lines), ZERO)
    rate, vat = vat_on(subtotal, by=receipt.vat_by, rate=receipt.vat_rate, amount=receipt.vat_amount)
    found = Bill(subtotal=subtotal, vat_rate=rate, vat=vat, delivery=receipt.delivery_cost or ZERO,
                 vat_in_cost=in_cost)
    if receipt.status == receipt.Status.POSTED:
        found.shares = {line.pk: line.extra_cost for line in lines}
    else:
        # By value; lines that cost nothing (samples) share by quantity only
        # when every line is free.
        weights = {line.pk: line.value for line in lines}
        if not any(weights.values()):
            weights = {line.pk: line.qty_accepted_base for line in lines}
        found.shares = share(found.extra, weights)
    return found


def with_real_cost(receipt, lines, found=None):
    """``lines`` with ``extra_cost`` set from the bill (not saved), so their
    ``real_unit_cost`` reads right on a draft too."""
    found = found or bill(receipt, lines)
    for line in lines:
        line.extra_cost = found.shares.get(line.pk, ZERO)
    return lines
