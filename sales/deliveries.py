"""Delivery challans, invoices and sales returns (Phase 6).

**Delivery** (``sales.deliver`` in the challan's store branch): against a
confirmed order, one challan per store (Phase 8) - an order with juice from
finished goods and sugar from the raw material store goes out on two. Started from the order, it is filled from the stock the
order reserved, first expiry first; package labels can be scanned in. A
delivery may use stock reserved for its own order, and free stock - never
another order's reservation. Posting takes the goods out, releases the
reservation it used, and counts the delivered quantity on the order.

**Invoice** (``sales.invoice``): one per delivery - exactly the delivered
quantities, at the order's prices, discount and VAT. Draft -> Issued (due
date from the customer's payment terms) -> Paid (date and reference);
cancelled with a reason (then the delivery can be reversed).

**Sales return**: goods back from a customer, at the delivery's cost; good
goods to the store, damaged ones to the branch's Rejected store.
"""

import datetime
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Sum

from common.choices import ActiveStatus
from common.tenant import use_company
from inventory import fefo, posting
from inventory.doc_common import _writable, audit, check_qty, get, local_today, now, plain, require, unit_factor
from inventory.issues import _in_unit, label_from_scan, rejected_store
from inventory.models import BarcodeLabel, MoveType, StockBalance, StockMove
from sales import orders
from sales.models import (
    CENT,
    Delivery,
    DeliveryLine,
    Reservation,
    SalesInvoice,
    SalesInvoiceLine,
    SalesOrder,
    SalesOrderLine,
    SalesReturn,
    SalesReturnLine,
)

FOUR = Decimal("0.0001")


def _draft(row):
    if row.status != "draft":
        raise ValidationError(f"{row.number} is {row.get_status_display().lower()}; only a draft can change.")


def get_delivery(*, actor, company_id, pk, code="sales.deliver"):
    row = get(Delivery, company_id, pk, "Delivery")
    return require(actor, company_id, code, row.warehouse.branch_id), row


def open_stores(order):
    """The stores with something still to deliver on ``order``, the order's own first."""
    found = {}
    lines = SalesOrderLine.all_objects.filter(order=order).select_related("warehouse").order_by("pk")
    for line in sorted(lines, key=lambda l: l.warehouse_id != order.warehouse_id):
        if line.qty_open_base > 0:
            found.setdefault(line.warehouse_id, line.warehouse)
    return list(found.values())


def _rows_for_line(order_line, warehouse, day):
    """Rows this order line may take from, first expiry first, each with
    ``free_for_line`` = on hand less what other orders reserved there."""
    rows = []
    for row in fefo.usable_rows(order_line.company_id, order_line.item, warehouse, day):
        row.free_for_line = row.available
        rows.append(row)
    seen = {row.pk for row in rows}
    for reservation in Reservation.all_objects.filter(order_line=order_line).select_related("balance__batch",
                                                                                            "balance__location"):
        balance = reservation.balance
        if balance.pk in seen:
            next(r for r in rows if r.pk == balance.pk).free_for_line += reservation.qty_base
        elif balance.batch_id is None or balance.batch.usable_on(day):
            balance.free_for_line = reservation.qty_base + max(
                balance.qty_on_hand - balance.qty_reserved, Decimal("0"))
            rows.append(balance)
            seen.add(balance.pk)
    far = datetime.date.max
    rows.sort(key=lambda r: (r.batch.expiry_date or far if r.batch_id else far,
                             r.batch.batch_no if r.batch_id else "", r.location.code if r.location_id else ""))
    return rows


@transaction.atomic
def create_delivery(*, actor, company_id, values, fill=True):
    values = _writable(values, ("order", "warehouse", "delivery_date", "vehicle_no", "driver", "note"))
    order = values.get("order")
    if order is None:
        raise ValidationError({"order": "Choose the sales order."})
    order = values["order"] = get(SalesOrder, company_id, order.pk, "Sales order", lock=True)
    if not order.deliverable:
        raise ValidationError({"order": f"{order.number} is {order.get_status_display().lower()}: only a "
                                        "confirmed order is delivered."})
    if values.get("delivery_date") is None:
        raise ValidationError({"delivery_date": "Give the date."})
    pending = open_stores(order)
    warehouse = values.get("warehouse")
    if warehouse is None:
        if len(pending) > 1:
            raise ValidationError({"warehouse": f"{order.number} goes from "
                                                f"{', '.join(w.code for w in pending)}: choose the store."})
        warehouse = pending[0] if pending else order.warehouse
    if warehouse.pk not in {w.pk for w in pending}:
        raise ValidationError({"warehouse": f"Nothing is left to deliver on {order.number} from {warehouse.code}."})
    values["warehouse"] = warehouse
    membership = require(actor, company_id, "sales.deliver", warehouse.branch_id)
    short = []
    with use_company(company_id):
        if order.deliveries.filter(status="draft", warehouse=warehouse).exists():
            raise ValidationError({"order": f"A draft delivery of this order from {warehouse.code} is open: "
                                            "post or cancel it first."})
        row = Delivery(company=membership.company, created_by=actor, updated_by=actor,
                       number=posting.next_number(company_id, Delivery.DOC_TYPE, Delivery.PREFIX,
                                                  values["delivery_date"].year), **values)
        row.full_clean()
        row.save()
        if fill:
            for line in order.lines.filter(warehouse=warehouse).select_related("item__base_uom", "uom"):
                want = line.qty_open_base
                for balance in _rows_for_line(line, warehouse, row.delivery_date):
                    if want <= 0:
                        break
                    take = min(balance.free_for_line, want)
                    if take <= 0:
                        continue
                    qty, uom, factor = _in_unit(take, line.uom, line.factor, line.item)
                    DeliveryLine.objects.create(company=membership.company, delivery=row, order_line=line,
                                                item=line.item, batch=balance.batch, location=balance.location,
                                                qty=qty, uom=uom, factor=factor)
                    want -= take
                if want > 0:
                    short.append(f"{line.item.code} short by {want.normalize():f} {line.item.base_uom.code}")
        audit(actor, membership, "sales.delivery.created", row,
              after={"number": row.number, "order": order.number, "store": warehouse.code})
    row.shortages = short
    return row


@transaction.atomic
def deliver_order(*, actor, company_id, order_id, delivery_date):
    """Draft a challan from every store with something left to deliver on the
    order (and no draft open there), from the reserved stock.

    Returns ``(made, skipped)``: the new drafts, and why a store got none
    (a draft already open, or no "Deliver goods" in its branch).
    """
    from inventory.access import may

    order = get(SalesOrder, company_id, order_id, "Sales order")
    made, skipped = [], []
    for warehouse in open_stores(order):
        if not may(actor, company_id, "sales.deliver", warehouse.branch_id):
            skipped.append(f"{warehouse.code}: you do not deliver from there")
            continue
        if Delivery.all_objects.filter(order=order, warehouse=warehouse, status="draft").exists():
            skipped.append(f"{warehouse.code}: a draft is open already")
            continue
        made.append(create_delivery(actor=actor, company_id=company_id, values={
            "order": order, "warehouse": warehouse, "delivery_date": delivery_date,
            "vehicle_no": "", "driver": "", "note": ""}))
    if not made:
        reason = "; ".join(skipped) if skipped else "nothing is left to deliver"
        raise ValidationError(f"No delivery drafted: {reason}.")
    return made, skipped


@transaction.atomic
def add_line(*, actor, company_id, delivery_id, values, label=None):
    membership, row = get_delivery(actor=actor, company_id=company_id, pk=delivery_id)
    _draft(row)
    values = _writable(values, ("order_line", "batch", "location", "qty", "uom", "not_fefo_reason"))
    line = values.get("order_line")
    if line is None or line.order_id != row.order_id:
        raise ValidationError({"order_line": "Choose a line of this order."})
    if line.warehouse_id != row.warehouse_id:
        raise ValidationError({"order_line": f"{line.item.code} goes from {line.warehouse.code}; this challan is "
                                             f"from {row.warehouse.code}."})
    item = line.item
    factor = unit_factor(item, values.get("uom"))
    check_qty(item, values.get("qty"), factor, active=False)
    qty_base = values["qty"] * factor
    with use_company(company_id):
        if label is not None and DeliveryLine.objects.filter(delivery=row, label=label).exists():
            raise ValidationError(f"Label {label.code} is on this delivery already.")
        on_draft = sum((l.qty_base for l in row.lines.filter(order_line=line)), Decimal("0"))
        if qty_base + on_draft > line.qty_open_base:
            raise ValidationError({"qty": f"More than ordered: {line.qty_open_base - on_draft:f} "
                                          f"{item.base_uom.code} of {item.code} is still to deliver."})
        rows = _rows_for_line(line, row.warehouse, row.delivery_date)
        # What this draft already takes from each row is not free any more.
        taken = {}
        for other in row.lines.all():
            key = (other.batch_id, other.location_id)
            taken[key] = taken.get(key, Decimal("0")) + other.qty_base
        for balance in rows:
            balance.free_for_line -= taken.get((balance.batch_id, balance.location_id), Decimal("0"))
        batch, location = values.get("batch"), values.get("location")
        if item.track_batch and batch is None:
            made, want = [], qty_base
            for balance in rows:
                if want <= 0:
                    break
                if location is not None and balance.location_id != location.pk:
                    continue
                take = min(balance.free_for_line, want)
                if take <= 0:
                    continue
                qty, uom, line_factor = _in_unit(take, values["uom"], factor, item)
                made.append(DeliveryLine.objects.create(company=membership.company, delivery=row, order_line=line,
                                                        item=item, batch=balance.batch, location=balance.location,
                                                        qty=qty, uom=uom, factor=line_factor))
                want -= take
            if want > 0:
                raise ValidationError(f"Not enough {item.code} free for this order in {row.warehouse.code}: "
                                      f"{(qty_base - want).normalize():f} {item.base_uom.code} can go.")
            return made
        match = [b for b in rows if b.batch_id == (batch.pk if batch else None)
                 and (location is None or b.location_id == location.pk)]
        free = sum((b.free_for_line for b in match), Decimal("0"))
        if batch is not None and not batch.usable_on(row.delivery_date):
            raise ValidationError({"batch": f"Batch {batch.batch_no} cannot be sold "
                                            f"({'expired' if batch.expiry_date and batch.expiry_date < row.delivery_date else batch.get_status_display().lower()})."})
        if qty_base > free:
            raise ValidationError({"qty": f"Only {free.normalize():f} {item.base_uom.code} of that "
                                          f"{'batch' if batch else 'item'} is free for this order."})
        if batch is not None and rows and rows[0].batch_id != batch.pk and rows[0].batch.expiry_date \
                and (batch.expiry_date is None or rows[0].batch.expiry_date < batch.expiry_date) \
                and not (values.get("not_fefo_reason") or "").strip():
            raise ValidationError({"not_fefo_reason": (
                f"Batch {rows[0].batch.batch_no} expires first ({rows[0].batch.expiry_date:%d %b %Y}); "
                "say why this one goes instead.")})
        if location is None and len(match) == 1:
            location = match[0].location
        new = DeliveryLine(company=membership.company, delivery=row, order_line=line, item=item, batch=batch,
                           location=location, qty=values["qty"], uom=values["uom"], factor=factor, label=label,
                           not_fefo_reason=values.get("not_fefo_reason", ""))
        new.full_clean()
        new.save()
        return [new]


@transaction.atomic
def scan_into_delivery(*, actor, company_id, delivery_id, code):
    _, row = get_delivery(actor=actor, company_id=company_id, pk=delivery_id)
    values, label = label_from_scan(company_id, code, row.warehouse)
    with use_company(company_id):
        line = next((l for l in row.order.lines.filter(item=values["item"], warehouse=row.warehouse).order_by("pk")
                     if l.qty_open_base > 0), None)
    if line is None:
        raise ValidationError(f"{values['item'].code} is not (or no longer) to deliver on {row.order.number} "
                              f"from {row.warehouse.code}.")
    return add_line(actor=actor, company_id=company_id, delivery_id=delivery_id, label=label, values={
        "order_line": line, "batch": values.get("batch"), "location": values.get("location"),
        "qty": values["qty"], "uom": values["uom"],
        "not_fefo_reason": "Taken by scanning" if values.get("batch") else ""})


@transaction.atomic
def remove_line(*, actor, company_id, delivery_id, line_id):
    _, row = get_delivery(actor=actor, company_id=company_id, pk=delivery_id)
    _draft(row)
    with use_company(company_id):
        if not row.lines.filter(pk=line_id).delete()[0]:
            raise ValidationError("That line is not on this delivery.")


def _balance_id(company_id, line, warehouse):
    row = StockBalance.all_objects.filter(company_id=company_id, item=line.item, batch=line.batch,
                                          warehouse=warehouse, location=line.location).first()
    return row.pk if row else None


@transaction.atomic
def post_delivery(*, actor, company_id, delivery_id):
    membership, row = get_delivery(actor=actor, company_id=company_id, pk=delivery_id)
    with use_company(company_id):
        row = Delivery.objects.select_for_update(of=("self",)).get(pk=row.pk)
        _draft(row)
        order = SalesOrder.objects.select_for_update(of=("self",)).select_related("warehouse", "customer").get(
            pk=row.order_id)
        if not order.deliverable:
            raise ValidationError(f"{order.number} is {order.get_status_display().lower()}.")
        lines = list(row.lines.select_related("item__base_uom", "batch", "location", "label", "order_line")
                     .order_by("pk"))
        if not lines:
            raise ValidationError("Add what goes out before posting.")
        store = row.warehouse
        if any(line.order_line.warehouse_id != store.pk for line in lines):
            raise ValidationError(f"Every line of {row.number} must go from {store.code}.")
        # Per order line: no more than it still needs; per row: never another order's reservation.
        per_line, per_row = {}, {}
        for line in lines:
            per_line[line.order_line_id] = per_line.get(line.order_line_id, Decimal("0")) + line.qty_base
            key = (line.order_line_id, _balance_id(company_id, line, store))
            per_row[key] = per_row.get(key, Decimal("0")) + line.qty_base
        for line_id, qty in per_line.items():
            order_line = SalesOrderLine.objects.select_for_update().get(pk=line_id)
            if qty > order_line.qty_open_base:
                raise ValidationError(f"{order_line.item.code}: more than the order still needs.")
        for (line_id, balance_id), qty in per_row.items():
            if balance_id is None:
                continue
            balance = StockBalance.objects.select_for_update().get(pk=balance_id)
            others = Reservation.objects.filter(balance_id=balance_id).exclude(order_line_id=line_id).aggregate(
                q=Sum("qty_base"))["q"] or Decimal("0")
            if balance.qty_on_hand - others < qty:
                raise ValidationError(f"{balance.item.code}: {others.normalize():f} of that stock is reserved "
                                      "for other orders.")
        # Release this order's reservation on what goes, from the rows it goes from first.
        for line_id, qty in per_line.items():
            order_line = SalesOrderLine.objects.get(pk=line_id)
            prefer = [b for (l, b), _q in per_row.items() if l == line_id and b]
            orders.release_line(order_line, qty, prefer_balance_ids=prefer)
        moves = posting.post(
            company_id=company_id, actor=actor, document_type=Delivery.DOC_TYPE, document_id=row.pk,
            move_type=MoveType.DELIVERY, work_date=row.delivery_date,
            lines=[posting.Line(item=l.item, warehouse=store, location=l.location, batch=l.batch,
                                qty=-l.qty_base, line_id=l.pk, note=f"{row.number} to {order.customer.name}"[:255])
                   for l in lines])
        for line_id, qty in per_line.items():
            SalesOrderLine.objects.filter(pk=line_id).update(qty_delivered_base=F("qty_delivered_base") + qty)
        for line in lines:
            if line.label_id and line.qty_base >= line.label.quantity * unit_factor(line.item, line.label.uom):
                BarcodeLabel.objects.filter(pk=line.label_id).update(status=BarcodeLabel.Status.CONSUMED)
        orders.refresh_status(order)
        row.status, row.posted_at, row.posted_by, row.updated_by = Delivery.Status.POSTED, moves[0].posted_at, actor, actor
        row.save()
        audit(actor, membership, "sales.delivery.posted", row, before={"status": "draft"},
              after={"status": "posted", "order": order.number, "lines": len(lines)})
    return row


@transaction.atomic
def reverse_delivery(*, actor, company_id, delivery_id, reason):
    membership, row = get_delivery(actor=actor, company_id=company_id, pk=delivery_id)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "Say why it is being reversed."})
    with use_company(company_id):
        row = Delivery.objects.select_for_update().get(pk=row.pk)
        if row.status != Delivery.Status.POSTED:
            raise ValidationError("Only a posted delivery is reversed.")
        invoice = SalesInvoice.objects.filter(delivery=row).exclude(status=SalesInvoice.Status.CANCELLED).first()
        if invoice is not None:
            raise ValidationError(f"It is invoiced on {invoice.number}: cancel the invoice first.")
        if row.returns.filter(status="posted").exists():
            raise ValidationError("Goods of it came back on a sales return: reverse that first.")
        posting.reverse(company_id=company_id, actor=actor, document_type=Delivery.DOC_TYPE, document_id=row.pk,
                        work_date=local_today(membership.company), note=f"Reversal of {row.number}: {reason}")
        for line in row.lines.all():
            SalesOrderLine.objects.filter(pk=line.order_line_id).update(
                qty_delivered_base=F("qty_delivered_base") - line.qty_base)
            if line.label_id:
                BarcodeLabel.objects.filter(pk=line.label_id, status=BarcodeLabel.Status.CONSUMED).update(
                    status=BarcodeLabel.Status.ACTIVE)
        order = SalesOrder.objects.get(pk=row.order_id)
        orders.refresh_status(order)
        if order.deliverable:
            orders.reserve_order(order, day=local_today(membership.company))
        row.status, row.reversed_at, row.reversed_by = Delivery.Status.REVERSED, now(), actor
        row.reverse_reason, row.updated_by = reason[:255], actor
        row.save()
        audit(actor, membership, "sales.delivery.reversed", row, before={"status": "posted"},
              after={"status": "reversed", "reason": reason})
    return row


@transaction.atomic
def cancel_delivery(*, actor, company_id, delivery_id):
    membership, row = get_delivery(actor=actor, company_id=company_id, pk=delivery_id)
    _draft(row)
    with use_company(company_id):
        row.status, row.updated_by = Delivery.Status.CANCELLED, actor
        row.save()
        audit(actor, membership, "sales.delivery.cancelled", row, before={"status": "draft"},
              after={"status": "cancelled"})
    return row


# --------------------------------------------------------------------------
# Invoice
# --------------------------------------------------------------------------

def get_invoice(*, actor, company_id, pk, code="sales.invoice"):
    row = get(SalesInvoice, company_id, pk, "Invoice")
    return require(actor, company_id, code, row.delivery.order.warehouse.branch_id), row


@transaction.atomic
def create_invoice(*, actor, company_id, delivery_id, invoice_date=None, note=""):
    """The draft invoice of a delivery: its quantities at the order's prices."""
    delivery = get(Delivery, company_id, delivery_id, "Delivery", lock=True)
    membership = require(actor, company_id, "sales.invoice", delivery.order.warehouse.branch_id)
    if delivery.status != Delivery.Status.POSTED:
        raise ValidationError("Only a posted delivery is invoiced.")
    with use_company(company_id):
        if SalesInvoice.objects.filter(delivery=delivery).exclude(status=SalesInvoice.Status.CANCELLED).exists():
            raise ValidationError(f"{delivery.number} is invoiced already.")
        day = invoice_date or delivery.delivery_date
        invoice = SalesInvoice(company=membership.company, created_by=actor, updated_by=actor, delivery=delivery,
                               customer=delivery.order.customer, invoice_date=day, note=note[:500],
                               number=posting.next_number(company_id, SalesInvoice.DOC_TYPE, SalesInvoice.PREFIX,
                                                          day.year))
        invoice.full_clean()
        invoice.save()
        amount = vat = Decimal("0")
        for line in delivery.lines.select_related("order_line", "item", "uom"):
            so = line.order_line
            price = (so.unit_price / so.factor * line.factor).quantize(FOUR, ROUND_HALF_UP)
            net = (price * (100 - so.discount_pct) / 100)
            line_amount = (line.qty * net).quantize(CENT, ROUND_HALF_UP)
            line_vat = (line_amount * so.vat_rate / 100).quantize(CENT, ROUND_HALF_UP)
            SalesInvoiceLine.objects.create(company=membership.company, invoice=invoice, delivery_line=line,
                                            item=line.item, qty=line.qty, uom=line.uom, unit_price=price,
                                            discount_pct=so.discount_pct, vat_rate=so.vat_rate,
                                            amount=line_amount, vat_amount=line_vat)
            amount += line_amount
            vat += line_vat
        invoice.amount, invoice.vat, invoice.total = amount, vat, amount + vat
        invoice.save(update_fields=["amount", "vat", "total"])
        audit(actor, membership, "sales.invoice.created", invoice,
              after={"number": invoice.number, "delivery": delivery.number, "total": plain(invoice.total)})
    return invoice


@transaction.atomic
def issue_invoice(*, actor, company_id, invoice_id):
    membership, invoice = get_invoice(actor=actor, company_id=company_id, pk=invoice_id)
    if invoice.status != SalesInvoice.Status.DRAFT:
        raise ValidationError(f"{invoice.number} is {invoice.get_status_display().lower()}.")
    with use_company(company_id):
        terms = invoice.customer.payment_terms_days
        invoice.due_date = invoice.invoice_date + datetime.timedelta(days=terms)
        invoice.status, invoice.issued_at, invoice.issued_by, invoice.updated_by = (
            SalesInvoice.Status.ISSUED, now(), actor, actor)
        invoice.save()
        audit(actor, membership, "sales.invoice.issued", invoice, before={"status": "draft"},
              after={"status": "issued", "total": plain(invoice.total), "due": plain(invoice.due_date)})
    return invoice


@transaction.atomic
def mark_paid(*, actor, company_id, invoice_id, paid_on, note=""):
    membership, invoice = get_invoice(actor=actor, company_id=company_id, pk=invoice_id)
    if invoice.status != SalesInvoice.Status.ISSUED:
        raise ValidationError("Only an issued invoice is marked paid.")
    if paid_on is None:
        raise ValidationError({"paid_on": "Give the date it was paid."})
    with use_company(company_id):
        invoice.status, invoice.paid_on, invoice.paid_note = SalesInvoice.Status.PAID, paid_on, (note or "")[:255]
        invoice.updated_by = actor
        invoice.save()
        audit(actor, membership, "sales.invoice.paid", invoice, before={"status": "issued"},
              after={"status": "paid", "paid_on": plain(paid_on), "note": note})
    return invoice


@transaction.atomic
def cancel_invoice(*, actor, company_id, invoice_id, reason):
    membership, invoice = get_invoice(actor=actor, company_id=company_id, pk=invoice_id)
    if invoice.status not in (SalesInvoice.Status.DRAFT, SalesInvoice.Status.ISSUED):
        raise ValidationError(f"{invoice.number} is {invoice.get_status_display().lower()}.")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "Say why it is cancelled."})
    with use_company(company_id):
        before = invoice.status
        invoice.status, invoice.cancel_reason, invoice.updated_by = SalesInvoice.Status.CANCELLED, reason[:255], actor
        invoice.save()
        audit(actor, membership, "sales.invoice.cancelled", invoice, before={"status": before},
              after={"status": "cancelled", "reason": reason})
    return invoice


# --------------------------------------------------------------------------
# Sales return
# --------------------------------------------------------------------------

def get_return(*, actor, company_id, pk, code="sales.deliver"):
    row = get(SalesReturn, company_id, pk, "Sales return")
    return require(actor, company_id, code, row.warehouse.branch_id), row


@transaction.atomic
def create_return(*, actor, company_id, values):
    values = _writable(values, ("customer", "delivery", "warehouse", "return_date", "reason"))
    delivery = values.get("delivery")
    if delivery is not None:
        delivery = values["delivery"] = get(Delivery, company_id, delivery.pk, "Delivery")
        if delivery.status != Delivery.Status.POSTED:
            raise ValidationError({"delivery": f"{delivery.number} is not delivered."})
        values["customer"] = delivery.order.customer
        values["warehouse"] = values.get("warehouse") or delivery.warehouse
    warehouse = values.get("warehouse")
    if values.get("customer") is None:
        raise ValidationError({"customer": "Choose the customer."})
    if warehouse is None or warehouse.status != ActiveStatus.ACTIVE:
        raise ValidationError({"warehouse": "Choose an active store."})
    if values.get("return_date") is None:
        raise ValidationError({"return_date": "Give the date."})
    if not (values.get("reason") or "").strip():
        raise ValidationError({"reason": "Say why the goods came back."})
    membership = require(actor, company_id, "sales.deliver", warehouse.branch_id)
    with use_company(company_id):
        row = SalesReturn(company=membership.company, created_by=actor, updated_by=actor,
                          number=posting.next_number(company_id, SalesReturn.DOC_TYPE, SalesReturn.PREFIX,
                                                     values["return_date"].year), **values)
        row.full_clean()
        row.save()
        audit(actor, membership, "sales.return.created", row, after={"number": row.number})
    return row


def _returned_base(delivery_line):
    return sum((l.qty_base for l in SalesReturnLine.all_objects.filter(
        delivery_line=delivery_line, sales_return__status__in=["draft", "posted"])), Decimal("0"))


@transaction.atomic
def add_return_line(*, actor, company_id, return_id, values):
    membership, row = get_return(actor=actor, company_id=company_id, pk=return_id)
    _draft(row)
    values = _writable(values, ("delivery_line", "item", "batch", "condition", "qty", "uom", "note"))
    delivery_line = values.get("delivery_line")
    with use_company(company_id):
        cost = None
        if delivery_line is not None:
            delivery_line = values["delivery_line"] = DeliveryLine.objects.select_related(
                "delivery__order", "item", "batch").filter(pk=delivery_line.pk).first()
            if delivery_line is None or delivery_line.delivery.status != Delivery.Status.POSTED \
                    or delivery_line.delivery.order.customer_id != row.customer_id:
                raise ValidationError({"delivery_line": "Pick a line delivered to this customer."})
            values.update(item=delivery_line.item, batch=delivery_line.batch)
            move = StockMove.all_objects.filter(document_type=Delivery.DOC_TYPE,
                                                document_id=delivery_line.delivery_id,
                                                document_line_id=delivery_line.pk,
                                                reversed_move__isnull=True).first()
            cost = move.unit_cost if move else None
        item = values.get("item")
        if item is None:
            raise ValidationError({"item": "Choose the delivered line or the item."})
        factor = unit_factor(item, values.get("uom"))
        check_qty(item, values.get("qty"), factor, active=False)
        if item.track_batch and values.get("batch") is None:
            raise ValidationError({"batch": f"{item.code} keeps batches: say which batch came back."})
        if delivery_line is not None and _returned_base(delivery_line) + values["qty"] * factor > delivery_line.qty_base:
            raise ValidationError({"qty": "More than was delivered on that line."})
        condition = values.get("condition") or SalesReturnLine.Condition.GOOD
        store = row.warehouse if condition == SalesReturnLine.Condition.GOOD else rejected_store(
            company_id, row.warehouse.branch_id)
        values["condition"] = condition
        line = SalesReturnLine(company=membership.company, sales_return=row, warehouse=store, factor=factor,
                               unit_cost_base=cost, **values)
        line.full_clean()
        line.save()
    return line


@transaction.atomic
def remove_return_line(*, actor, company_id, return_id, line_id):
    _, row = get_return(actor=actor, company_id=company_id, pk=return_id)
    _draft(row)
    with use_company(company_id):
        if not row.lines.filter(pk=line_id).delete()[0]:
            raise ValidationError("That line is not on this return.")


@transaction.atomic
def post_return(*, actor, company_id, return_id):
    membership, row = get_return(actor=actor, company_id=company_id, pk=return_id)
    with use_company(company_id):
        row = SalesReturn.objects.select_for_update(of=("self",)).get(pk=row.pk)
        _draft(row)
        lines = list(row.lines.select_related("item__base_uom", "batch", "warehouse").order_by("pk"))
        if not lines:
            raise ValidationError("Add what came back before posting.")
        moves = posting.post(
            company_id=company_id, actor=actor, document_type=SalesReturn.DOC_TYPE, document_id=row.pk,
            move_type=MoveType.SALES_RETURN, work_date=row.return_date,
            lines=[posting.Line(item=l.item, warehouse=l.warehouse, batch=l.batch, qty=l.qty_base,
                                unit_cost=l.unit_cost_base, line_id=l.pk,
                                note=f"{row.number}: {row.reason}"[:255]) for l in lines])
        row.status, row.posted_at, row.posted_by, row.updated_by = "posted", moves[0].posted_at, actor, actor
        row.save()
        audit(actor, membership, "sales.return.posted", row, before={"status": "draft"},
              after={"status": "posted", "lines": len(lines)})
    return row


@transaction.atomic
def reverse_return(*, actor, company_id, return_id, reason):
    membership, row = get_return(actor=actor, company_id=company_id, pk=return_id)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "Say why it is being reversed."})
    with use_company(company_id):
        row = SalesReturn.objects.select_for_update().get(pk=row.pk)
        if row.status != "posted":
            raise ValidationError("Only a posted return is reversed.")
        posting.reverse(company_id=company_id, actor=actor, document_type=SalesReturn.DOC_TYPE,
                        document_id=row.pk, work_date=local_today(membership.company),
                        note=f"Reversal of {row.number}: {reason}")
        row.status, row.reversed_at, row.reversed_by = "reversed", now(), actor
        row.reverse_reason, row.updated_by = reason[:255], actor
        row.save()
        audit(actor, membership, "sales.return.reversed", row, before={"status": "posted"},
              after={"status": "reversed", "reason": reason})
    return row


@transaction.atomic
def cancel_return(*, actor, company_id, return_id):
    membership, row = get_return(actor=actor, company_id=company_id, pk=return_id)
    _draft(row)
    with use_company(company_id):
        row.status, row.updated_by = "cancelled", actor
        row.save()
    return row
