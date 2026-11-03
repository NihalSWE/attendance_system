"""One screen for each everyday stock job (2026-10-28, docs/inventory/PRODUCTION_PLAN.md
§3, §15 phase 9.4): Stock in, Move stock and Sell, on top of the documents
that already exist - goods receipts, first counts (opening stock), sales
returns, transfers, sales orders and deliveries. Each screen fills its
document and posts it in one go, inside one transaction: every check the
document makes still applies, and a refusal leaves nothing behind.

A reason given for one line comes back under that line's box (``qty_2``).
"""

from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from inventory import access, opening, transfers
from inventory.models import Batch

STOCK_IN_SOURCES = (("purchase", "A purchase (goods received)"), ("count", "First count (opening stock)"),
                    ("return", "A customer returned it"))
# What each one means, under its choice on Stock in (2026-11-02).
STOCK_IN_HELP = {
    "purchase": "You bought it from a supplier and it arrived today: choose the supplier, write what the bill "
                "says it cost, and the VAT and delivery cost if you paid them - they are added to what the goods "
                "really cost. It makes a goods receipt (GRN).",
    "count": "Stock you already had on the first day you use this software, counted on the shelf. Use it once "
             "per item to start; after that stock comes in by purchase or production.",
    "return": "A customer sent back what you sold them. Good goods go back to stock; damaged ones go to the "
              "rejected store. No cost is asked: it comes back at what it cost when it left.",
}
ZERO = Decimal("0")
CENT = Decimal("0.01")


@dataclass
class Line:
    """One line typed on a screen, with its row number on the page."""

    row: int
    item: object
    qty: Decimal
    uom: object
    unit_cost: Decimal = None
    batch_no: str = ""
    expiry_date: object = None
    mfg_date: object = None
    condition: str = "good"
    batch: object = None
    unit_price: Decimal = None
    sale_price: Decimal = None       # Stock in: the item's (or pack's) new sale price


def _on_row(row, exc):
    """A line's refusal, named after that row's boxes."""
    if hasattr(exc, "error_dict"):
        found = {}
        for name, errors in exc.error_dict.items():
            box = {"qty_received": "qty", "unit_price": "price", "unit_cost": "cost", "batch_no": "batch",
                   "expiry_date": "expiry", "mfg_date": "mfg", "uom": "unit"}.get(name, name)
            found[f"{box}_{row}" if name != "__all__" else "__all__"] = [
                text for error in errors for text in error.messages]
        return ValidationError(found)
    return ValidationError({f"item_{row}": list(exc.messages)})


def _each(lines, add):
    for line in lines:
        try:
            add(line)
        except ValidationError as exc:
            raise _on_row(line.row, exc) from None


def _need_lines(lines):
    if not lines:
        raise ValidationError("Add at least one line: choose an item and its quantity.")


# --- Stock in -----------------------------------------------------------------

def _set_prices(actor, company_id, lines, below_reason):
    """The sale prices given on Stock in lines, now that the buy price is in
    (2026-10-31): below the new cost only with a reason (inventory.pricing)."""
    from inventory import pricing

    for line in lines:
        if line.sale_price is not None:
            pricing.set_price(actor=actor, company_id=company_id, item=line.item, price=line.sale_price,
                              uom=line.uom, reason=below_reason, field=f"sale_{line.row}")


def subtotal(lines):
    """What the lines cost before VAT: quantity × cost, line by line."""
    return sum(((line.qty or ZERO) * (line.unit_cost or ZERO) for line in lines), ZERO).quantize(CENT)


def vat_given(rate=None, amount=None, by=""):
    """``(by, rate, amount)`` as the goods receipt keeps the VAT (2026-11-03):
    the box typed in last (``by``, from the page) wins; else the amount if
    only it is given; neither is no VAT."""
    from purchasing.models import GoodsReceipt

    if amount is not None and (by == "amount" or rate is None):
        return GoodsReceipt.VatBy.AMOUNT, ZERO, amount
    return GoodsReceipt.VatBy.RATE, rate or ZERO, ZERO


def _new_buy_prices(company_id, lines):
    """The cost typed on a purchase line becomes the item's expected buy price
    (2026-11-02): the next Stock in starts from it, and so do prices and recipes."""
    from inventory.masters import units_for
    from inventory.models import Item

    for line in lines:
        if line.unit_cost is None:
            continue
        factor = units_for(line.item).get(line.uom.pk) or Decimal("1")
        Item.all_objects.filter(company_id=company_id, pk=line.item.pk).update(
            standard_cost=(line.unit_cost / factor).quantize(Decimal("0.0001")))


@transaction.atomic
def stock_in(*, actor, company_id, source, store, day, lines, supplier=None, customer=None, reason="", note="",
             below_reason="", vat_rate=None, vat_amount=None, vat_by="", delivery_cost=None):
    """``(document, posted, words)``: a purchase is received, a first count
    posted (or left for approval when the person may not post it), a customer
    return posted. A purchase keeps the VAT and delivery cost paid on the
    bill, and its goods go into stock at their real cost (purchasing.landed)."""
    _need_lines(lines)
    if source == "purchase":
        from purchasing import landed, receipts

        by, rate, amount = vat_given(vat_rate, vat_amount, vat_by)
        receipt = receipts.create_receipt(actor=actor, company_id=company_id, fill_from_order=False, values={
            "order": None, "supplier": supplier, "warehouse": store, "received_date": day, "challan_no": "",
            "invoice_no": "", "vehicle_no": "", "note": note, "vat_by": by, "vat_rate": rate,
            "vat_amount": amount, "delivery_cost": delivery_cost or ZERO})
        _each(lines, lambda line: receipts.add_line(actor=actor, company_id=company_id, receipt_id=receipt.pk,
                                                     values={"order_line": None, "item": line.item, "location": None,
                                                             "qty_received": line.qty, "qty_rejected": Decimal("0"),
                                                             "uom": line.uom, "unit_cost": line.unit_cost,
                                                             "batch_no": line.batch_no, "supplier_batch_no": "",
                                                             "mfg_date": line.mfg_date,
                                                             "expiry_date": line.expiry_date,
                                                             "packages": None, "note": ""}))
        receipt = receipts.post_receipt(actor=actor, company_id=company_id, receipt_id=receipt.pk)
        _new_buy_prices(company_id, lines)
        _set_prices(actor, company_id, lines, below_reason)
        found = landed.bill(receipt, list(receipt.lines.all()))
        extras = [f"VAT {found.vat:,.2f}" if found.vat else "", f"delivery {found.delivery:,.2f}" if found.delivery
                  else ""]
        extras = " and ".join(text for text in extras if text)
        money = (f" Total paid {found.total:,.2f} Tk" + (f" with {extras}" if extras else "") + "."
                 if found.total else "")
        if found.extra:
            money += " The goods are in stock at their real cost, with " + (
                "the VAT and delivery" if found.vat_in_cost and found.vat and found.delivery else
                "the VAT" if found.vat_in_cost and found.vat else "the delivery") + " shared over them."
        return receipt, True, (f"{receipt.number}: {len(lines)} line{'s' if len(lines) != 1 else ''} received into "
                               f"{store.name}.{money}")
    if source == "count":
        document = opening.create_document(actor=actor, company_id=company_id, values={"work_date": day, "note": note})
        _each(lines, lambda line: opening.add_line(actor=actor, company_id=company_id, document_id=document.pk,
                                                   values={"warehouse": store, "location": None, "item": line.item,
                                                           "batch_no": line.batch_no, "supplier_batch_no": "",
                                                           "mfg_date": line.mfg_date,
                                                           "expiry_date": line.expiry_date,
                                                           "qty": line.qty, "uom": line.uom,
                                                           "unit_cost": line.unit_cost, "note": ""}))
        if access.may(actor, company_id, "inventory.adjust_approve", store.branch_id):
            opening.post_document(actor=actor, company_id=company_id, document_id=document.pk)
            _set_prices(actor, company_id, lines, below_reason)
            return document, True, f"{document.number}: the count is in stock in {store.name}."
        return document, False, (f"{document.number} saved: someone who may approve opening stock posts it "
                                 "(Opening stock).")
    if source == "return":
        from sales import deliveries

        back = deliveries.create_return(actor=actor, company_id=company_id, values={
            "customer": customer, "delivery": None, "warehouse": store, "return_date": day, "reason": reason})

        def add(line):
            batch = None
            if line.item.track_batch:
                batch = Batch.all_objects.filter(company_id=company_id, item=line.item,
                                                 batch_no=(line.batch_no or "").strip()).first()
                if batch is None:
                    raise ValidationError({"batch_no": "Give the batch number on the goods (one already made)."})
            deliveries.add_return_line(actor=actor, company_id=company_id, return_id=back.pk, values={
                "delivery_line": None, "item": line.item, "batch": batch, "condition": line.condition,
                "qty": line.qty, "uom": line.uom, "note": ""})

        _each(lines, add)
        deliveries.post_return(actor=actor, company_id=company_id, return_id=back.pk)
        return back, True, f"{back.number}: returned goods are back (damaged ones in the rejected store)."
    raise ValidationError({"source": "Choose where the stock comes from."})


# --- Move stock ---------------------------------------------------------------

@transaction.atomic
def move(*, actor, company_id, from_store, to_store, day, lines, note=""):
    """The goods leave ``from_store`` now and wait in transit until the other
    store receives them."""
    _need_lines(lines)
    row = transfers.create_transfer(actor=actor, company_id=company_id, values={
        "from_warehouse": from_store, "to_warehouse": to_store, "transfer_date": day, "vehicle_no": "",
        "note": note})
    _each(lines, lambda line: transfers.add_line(actor=actor, company_id=company_id, transfer_id=row.pk, values={
        "item": line.item, "batch": line.batch, "from_location": None, "to_location": None, "qty": line.qty,
        "uom": line.uom, "note": ""}))
    transfers.dispatch(actor=actor, company_id=company_id, transfer_id=row.pk)
    return row


def receive_all(*, actor, company_id, transfer, day):
    """Everything sent arrived."""
    return transfers.receive(actor=actor, company_id=company_id, transfer_id=transfer.pk, received_date=day)


# --- Sell ---------------------------------------------------------------------

def _below_cost(company_id, customer, day, lines, below_reason):
    """Each line's price against the cost of what goes out (2026-10-31): below
    it only with a reason. True when any line is below cost."""
    from inventory import masters, pricing
    from sales.orders import price_for

    below = False
    for line in lines:
        price = line.unit_price if line.unit_price is not None else price_for(
            customer, line.item, line.uom, line.qty, day)
        if price is None:
            continue
        factor = masters.units_for(line.item).get(line.uom.pk, Decimal("1"))
        cost = pricing.check(company_id, line.item, price, factor=factor, reason=below_reason,
                             field=f"price_{line.row}")
        below = below or (cost is not None and price < cost)
    return below


@transaction.atomic
def sell(*, actor, company_id, customer, store, day, lines, credit_note="", invoice=False, below_reason=""):
    """``(order, delivery, invoice, words)``: the sale, its delivery from the
    store (the batch that expires first goes first) and, when asked, its
    invoice. Someone who may not confirm their own sale leaves it for the
    person who confirms."""
    from sales import deliveries, orders

    _need_lines(lines)
    below = _below_cost(company_id, customer, day, lines, below_reason)
    order = orders.create_order(actor=actor, company_id=company_id, values={
        "customer": customer, "warehouse": store, "order_date": day, "delivery_date": None, "source": "manual",
        "customer_ref": "", "delivery_address": "",
        "note": f"Sold below cost: {below_reason.strip()}"[:500] if below else ""})
    _each(lines, lambda line: orders.add_line(actor=actor, company_id=company_id, order_id=order.pk, values={
        "item": line.item, "warehouse": None, "qty": line.qty, "uom": line.uom, "unit_price": line.unit_price,
        "discount_pct": None, "vat_rate": None, "note": ""}))
    try:
        orders.confirm(actor=actor, company_id=company_id, order_id=order.pk, credit_note=credit_note)
    except PermissionDenied:
        return order, None, None, f"{order.number} saved: someone else confirms it, then it is delivered."
    delivery = deliveries.create_delivery(actor=actor, company_id=company_id, values={
        "order": order, "delivery_date": day, "vehicle_no": "", "driver": "", "note": ""})
    if delivery.shortages:
        reasons = ["Not enough in " + store.name + ": " + "; ".join(delivery.shortages) + "."]
        # Counted in stock but waiting for a quality check (2026-11-01): say so.
        from inventory import qc

        for line in lines:
            held = sum((qty for row in qc.waiting(company_id, item=line.item)
                        for name, qty in row.stores if name == store.name), Decimal("0"))
            if held:
                reasons.append(f"{held.normalize():,f} {line.item.base_uom.code} of {line.item.name} in "
                               f"{store.name} waits for a quality check: pass it on Stock → Quality check, "
                               "then sell.")
        reasons.append("Sell less, or from another store.")
        raise ValidationError(reasons)
    deliveries.post_delivery(actor=actor, company_id=company_id, delivery_id=delivery.pk)
    bill = None
    if invoice:
        bill = deliveries.create_invoice(actor=actor, company_id=company_id, delivery_id=delivery.pk, invoice_date=day)
        deliveries.issue_invoice(actor=actor, company_id=company_id, invoice_id=bill.pk)
    words = f"{order.number} delivered as {delivery.number}" + (f", invoice {bill.number}" if bill else "") + "."
    return order, delivery, bill, words
