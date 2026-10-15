"""Goods receipts (GRN) and quality checks.

A GRN records what arrived at one store: against an approved order (any
number of partial GRNs) or direct. While a draft, lines are added (or filled
from the order's open quantities); **posting** it:

1. makes each line's batch - the number given, or ``YYMMDD-NN`` from the
   date received - in **Quarantine** when the item needs QC, else Released;
2. puts the accepted quantity (received less refused at the gate) on hand
   through ``inventory.posting`` at the line's cost, which updates the
   average cost;
3. adds it to the order line's received quantity (Partly received ->
   Received);
4. makes one **package label** per package when the line says how many
   packages it came in (printed from the GRN page).

**QC** (``purchase.qc`` in the store's branch): Passed releases the batch,
Failed blocks it - it stays in the store, unusable, until returned to the
supplier with a purchase return. Receiving needs "Receive goods"
(``purchase.receive``) in the store's branch. A posted GRN is undone by
**reversal** (counter-moves dated today, its labels voided), refused once
its goods were used or returned.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F

from common.choices import ActiveStatus
from common.tenant import use_company
from inventory import labels as label_service
from inventory import posting
from inventory.models import Batch, BarcodeLabel, MoveType
from purchasing import orders
from purchasing.common import (
    _writable,
    audit,
    check_item,
    get,
    local_today,
    now,
    plain,
    require,
    unit_factor,
)
from purchasing.models import (
    FOUR,
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLine,
    QualityCheck,
)

Status = GoodsReceipt.Status
Qc = GoodsReceiptLine.Qc
HEADER_FIELDS = ("order", "supplier", "warehouse", "received_date", "challan_no", "invoice_no",
                 "vehicle_no", "note")
LINE_FIELDS = ("order_line", "item", "location", "qty_received", "qty_rejected", "uom", "unit_cost",
               "batch_no", "supplier_batch_no", "mfg_date", "expiry_date", "packages", "note")


def get_receipt(*, actor, company_id, pk, code="purchase.receive"):
    receipt = get(GoodsReceipt, company_id, pk, "Goods receipt")
    membership = require(actor, company_id, code, receipt.warehouse.branch_id)
    return membership, receipt


def _draft(receipt):
    if receipt.status != Status.DRAFT:
        raise ValidationError(f"{receipt.number} is {receipt.get_status_display().lower()}; "
                              "only a draft can change.")


@transaction.atomic
def create_receipt(*, actor, company_id, values, fill_from_order=True):
    values = _writable(values, HEADER_FIELDS)
    warehouse, order = values.get("warehouse"), values.get("order")
    if warehouse is None:
        raise ValidationError({"warehouse": "Choose the store it arrived at."})
    if warehouse.status != ActiveStatus.ACTIVE:
        raise ValidationError({"warehouse": f"The store {warehouse.code} is inactive."})
    if values.get("received_date") is None:
        raise ValidationError({"received_date": "Give the date it arrived."})
    if order is not None:
        # Decide on the order as it is now, not as the caller last saw it.
        order = values["order"] = get(PurchaseOrder, company_id, order.pk, "Purchase order", lock=True)
        if not order.receivable:
            raise ValidationError({"order": f"{order.number} is {order.get_status_display().lower()}: "
                                            "only an approved order is received."})
        if warehouse.branch_id != order.branch_id:
            raise ValidationError({"warehouse": "The store is not in the order's branch."})
        values["supplier"] = order.supplier
    if values.get("supplier") is None:
        raise ValidationError({"supplier": "Choose the supplier."})
    membership = require(actor, company_id, "purchase.receive", warehouse.branch_id)
    with use_company(company_id):
        receipt = GoodsReceipt(
            company=membership.company, created_by=actor, updated_by=actor,
            number=posting.next_number(company_id, GoodsReceipt.DOC_TYPE, GoodsReceipt.PREFIX,
                                       values["received_date"].year),
            **values)
        receipt.full_clean()
        receipt.save()
        if order is not None and fill_from_order:
            for line in order.lines.select_related("item__base_uom", "uom"):
                open_base = line.qty_open_base
                if open_base <= 0:
                    continue
                in_unit = (open_base / line.factor).quantize(FOUR)
                uom, qty, factor, cost = (line.uom, in_unit, line.factor, line.net_price) \
                    if in_unit * line.factor == open_base else \
                    (line.item.base_uom, open_base, Decimal("1"), (line.net_price / line.factor).quantize(FOUR))
                GoodsReceiptLine.objects.create(
                    company=membership.company, receipt=receipt, order_line=line, item=line.item,
                    qty_received=qty, uom=uom, factor=factor, unit_cost=cost)
        audit(actor, membership, "purchase.receipt.created", receipt,
              after={"number": receipt.number, "order": receipt.order_id,
                     "warehouse": receipt.warehouse_id})
    return receipt


def clean_line(receipt, values):
    """A line checked against its item, the order line and the store."""
    item, order_line, location = values.get("item"), values.get("order_line"), values.get("location")
    if order_line is not None:
        if order_line.order_id != receipt.order_id:
            raise ValidationError({"order_line": "That line is on another order."})
        item = values["item"] = order_line.item
    if item is None:
        raise ValidationError({"item": "Choose the item."})
    factor = unit_factor(item, values.get("uom"))
    check_item(item, values.get("qty_received"), factor, field="qty_received")
    rejected = values.get("qty_rejected") or Decimal("0")
    values["qty_rejected"] = rejected
    errors = {}
    if location is not None and location.warehouse_id != receipt.warehouse_id:
        errors["location"] = f"Bin {location.code} is not in {receipt.warehouse.code}."
    if values.get("unit_cost") is None:
        errors["unit_cost"] = "Give the cost."
    if not item.track_batch and (values.get("batch_no") or values.get("expiry_date")):
        errors["batch_no"] = f"{item.code} does not keep batches; leave batch and expiry empty."
    mfg, expiry = values.get("mfg_date"), values.get("expiry_date")
    if item.track_expiry and not expiry and mfg and item.shelf_life_days:
        expiry = values["expiry_date"] = mfg + datetime.timedelta(days=item.shelf_life_days)
    if item.track_expiry and not expiry:
        errors["expiry_date"] = f"{item.code} has an expiry date: give it (or the manufacture date)."
    if mfg and expiry and expiry < mfg:
        errors["expiry_date"] = "The expiry date is before the manufacture date."
    if mfg and mfg > receipt.received_date:
        errors["mfg_date"] = "Made after it arrived?"
    if errors:
        raise ValidationError(errors)
    if order_line is not None:
        accepted = (values["qty_received"] - rejected) * factor
        others = sum(l.qty_accepted_base for l in GoodsReceiptLine.all_objects.filter(
            receipt=receipt, order_line=order_line))
        if accepted + others > order_line.qty_open_base:
            raise ValidationError({"qty_received": (
                f"More than ordered: {order_line.qty_open_base.normalize():f} "
                f"{item.base_uom.code} of {item.code} is still open on {receipt.order.number}.")})
    values["factor"] = factor
    values["batch_no"] = (values.get("batch_no") or "").strip()
    return values


@transaction.atomic
def add_line(*, actor, company_id, receipt_id, values):
    membership, receipt = get_receipt(actor=actor, company_id=company_id, pk=receipt_id)
    _draft(receipt)
    values = _writable(values, LINE_FIELDS)
    with use_company(company_id):
        values = clean_line(receipt, values)
        line = GoodsReceiptLine(company=membership.company, receipt=receipt, **values)
        line.full_clean()
        line.save()
    return line


@transaction.atomic
def update_line(*, actor, company_id, receipt_id, line_id, values):
    membership, receipt = get_receipt(actor=actor, company_id=company_id, pk=receipt_id)
    _draft(receipt)
    values = _writable(values, LINE_FIELDS)
    with use_company(company_id):
        line = receipt.lines.filter(pk=line_id).first()
        if line is None:
            raise ValidationError("That line is not on this receipt.")
        values.setdefault("order_line", line.order_line)
        other = {k: getattr(line, k) for k in LINE_FIELDS if k not in values}
        line.delete()
        values = clean_line(receipt, {**other, **values})
        new = GoodsReceiptLine(company=membership.company, receipt=receipt, **values)
        new.full_clean()
        new.save()
    return new


@transaction.atomic
def remove_line(*, actor, company_id, receipt_id, line_id):
    _, receipt = get_receipt(actor=actor, company_id=company_id, pk=receipt_id)
    _draft(receipt)
    with use_company(company_id):
        deleted, _ = receipt.lines.filter(pk=line_id).delete()
    if not deleted:
        raise ValidationError("That line is not on this receipt.")


def _new_batch_no(company_id, item, day):
    prefix = day.strftime("%y%m%d")
    taken = set(Batch.all_objects.filter(company_id=company_id, item=item,
                                         batch_no__startswith=prefix).values_list("batch_no", flat=True))
    number = 1
    while f"{prefix}-{number:02d}" in taken:
        number += 1
    return f"{prefix}-{number:02d}"


@transaction.atomic
def post_receipt(*, actor, company_id, receipt_id):
    membership, receipt = get_receipt(actor=actor, company_id=company_id, pk=receipt_id)
    with use_company(company_id):
        receipt = GoodsReceipt.objects.select_for_update(of=("self",)).select_related("warehouse", "order").get(
            pk=receipt.pk)
        _draft(receipt)
        lines = list(receipt.lines.select_related("item__base_uom", "uom", "location", "order_line")
                     .order_by("pk"))
        if not lines:
            raise ValidationError("Add what arrived before posting.")
        if receipt.order_id and not receipt.order.receivable:
            raise ValidationError(f"{receipt.order.number} is "
                                  f"{receipt.order.get_status_display().lower()}.")
        to_post = []
        for line in lines:
            # The order may have been received meanwhile by another GRN.
            if line.order_line_id:
                order_line = PurchaseOrderLine.objects.select_for_update().get(pk=line.order_line_id)
                if line.qty_accepted_base > order_line.qty_open_base:
                    raise ValidationError(
                        f"{line.item.code}: only {order_line.qty_open_base.normalize():f} "
                        f"{line.item.base_uom.code} is still open on the order.")
            batch = None
            if line.item.track_batch:
                number = line.batch_no or _new_batch_no(company_id, line.item, receipt.received_date)
                existing = Batch.objects.filter(item=line.item, batch_no=number).first()
                if existing is not None and line.item.qc_required:
                    raise ValidationError(f"{line.item.code} needs QC: batch {number} exists already, "
                                          "give this delivery its own batch number.")
                batch = posting.batch_for(
                    company_id=company_id, actor=actor, item=line.item, batch_no=number,
                    source=Batch.Source.GRN, supplier_batch_no=line.supplier_batch_no,
                    mfg_date=line.mfg_date, expiry_date=line.expiry_date,
                    unit_cost=line.unit_cost_base,
                    status=Batch.Status.QUARANTINE if line.item.qc_required else Batch.Status.RELEASED)
                line.batch, line.batch_no = batch, number
            line.qc_status = Qc.PENDING if line.item.qc_required else Qc.NOT_REQUIRED
            line.save(update_fields=["batch", "batch_no", "qc_status"])
            to_post.append(posting.Line(
                item=line.item, warehouse=receipt.warehouse, location=line.location, batch=batch,
                qty=line.qty_accepted_base, unit_cost=line.unit_cost_base, line_id=line.pk,
                note=f"{receipt.number}{' · ' + line.note if line.note else ''}"))
        moves = posting.post(company_id=company_id, actor=actor, document_type=GoodsReceipt.DOC_TYPE,
                             document_id=receipt.pk, move_type=MoveType.RECEIPT, lines=to_post,
                             work_date=receipt.received_date)
        for line in lines:
            if line.order_line_id:
                PurchaseOrderLine.objects.filter(pk=line.order_line_id).update(
                    qty_received_base=F("qty_received_base") + line.qty_accepted_base)
        if receipt.order_id:
            orders.refresh_status(receipt.order)
        for line in lines:
            if line.packages:
                label_service.package_labels(
                    actor=actor, company_id=company_id, item_id=line.item_id,
                    batch_id=line.batch_id, uom_id=line.uom_id,
                    quantity=(line.qty_accepted / line.packages).quantize(FOUR),
                    count=line.packages, warehouse_id=receipt.warehouse_id,
                    location_id=line.location_id, source_type="goods_receipt_line",
                    source_id=line.pk, permission="purchase.receive")
        receipt.status = Status.POSTED
        receipt.posted_at, receipt.posted_by, receipt.updated_by = moves[0].posted_at, actor, actor
        receipt.save()
        audit(actor, membership, "purchase.receipt.posted", receipt, before={"status": "draft"},
              after={"status": "posted", "lines": len(lines),
                     "value": plain(sum((m.value for m in moves), Decimal("0")))})
    return receipt


def receipt_labels(receipt):
    return BarcodeLabel.all_objects.filter(
        company_id=receipt.company_id, source_type="goods_receipt_line",
        source_id__in=GoodsReceiptLine.all_objects.filter(receipt=receipt).values("pk")).order_by("pk")


@transaction.atomic
def reverse_receipt(*, actor, company_id, receipt_id, reason):
    membership, receipt = get_receipt(actor=actor, company_id=company_id, pk=receipt_id)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "Say why it is being reversed."})
    with use_company(company_id):
        receipt = GoodsReceipt.objects.select_for_update().get(pk=receipt.pk)
        if receipt.status != Status.POSTED:
            raise ValidationError("Only a posted goods receipt is reversed.")
        if receipt.returns.filter(status="posted").exists():
            raise ValidationError("Goods of this receipt were returned to the supplier; "
                                  "reverse that return first.")
        posting.reverse(company_id=company_id, actor=actor, document_type=GoodsReceipt.DOC_TYPE,
                        document_id=receipt.pk, work_date=local_today(membership.company),
                        note=f"Reversal of {receipt.number}: {reason}")
        for line in receipt.lines.all():
            if line.order_line_id:
                PurchaseOrderLine.objects.filter(pk=line.order_line_id).update(
                    qty_received_base=F("qty_received_base") - line.qty_accepted_base)
        if receipt.order_id:
            orders.refresh_status(receipt.order)
        receipt_labels(receipt).filter(status=BarcodeLabel.Status.ACTIVE).update(
            status=BarcodeLabel.Status.VOID, void_reason=f"{receipt.number} reversed"[:255])
        receipt.status = Status.REVERSED
        receipt.reversed_at, receipt.reversed_by = now(), actor
        receipt.reverse_reason, receipt.updated_by = reason[:255], actor
        receipt.save()
        audit(actor, membership, "purchase.receipt.reversed", receipt, before={"status": "posted"},
              after={"status": "reversed", "reason": reason})
    return receipt


@transaction.atomic
def cancel_receipt(*, actor, company_id, receipt_id):
    membership, receipt = get_receipt(actor=actor, company_id=company_id, pk=receipt_id)
    _draft(receipt)
    with use_company(company_id):
        receipt.status = Status.CANCELLED
        receipt.updated_by = actor
        receipt.save()
        audit(actor, membership, "purchase.receipt.cancelled", receipt,
              before={"status": "draft"}, after={"status": "cancelled"})
    return receipt


# --------------------------------------------------------------------------
# Quality check
# --------------------------------------------------------------------------

def pending_lines(company_id):
    return GoodsReceiptLine.all_objects.filter(
        company_id=company_id, qc_status=Qc.PENDING, receipt__status=Status.POSTED)


@transaction.atomic
def record_check(*, actor, company_id, line_id, result, notes=""):
    """Passed releases the line's batch; Failed blocks it (return it to the supplier)."""
    with use_company(company_id):
        line = (GoodsReceiptLine.objects.select_for_update(of=("self",))
                .select_related("receipt__warehouse", "batch", "item").filter(pk=line_id).first())
    if line is None:
        raise PermissionDenied("Received line not found in this company.")
    membership = require(actor, company_id, "purchase.qc", line.receipt.warehouse.branch_id)
    if line.receipt.status != Status.POSTED or line.qc_status != Qc.PENDING:
        raise ValidationError("This line is not waiting for QC.")
    if result not in QualityCheck.Result.values:
        raise ValidationError({"result": "Choose passed or failed."})
    notes = (notes or "").strip()
    if result == QualityCheck.Result.FAILED and not notes:
        raise ValidationError({"notes": "Say what failed."})
    with use_company(company_id):
        check = QualityCheck.objects.create(company=membership.company, receipt_line=line,
                                            result=result, notes=notes[:500], checked_at=now(),
                                            checked_by=actor)
        line.qc_status = Qc.PASSED if result == QualityCheck.Result.PASSED else Qc.FAILED
        line.save(update_fields=["qc_status"])
        if line.batch_id:
            line.batch.status = (Batch.Status.RELEASED if result == QualityCheck.Result.PASSED
                                 else Batch.Status.BLOCKED)
            line.batch.updated_by = actor
            line.batch.save(update_fields=["status", "updated_by", "updated_at"])
        audit(actor, membership, f"purchase.qc.{result}", line.receipt,
              after={"line": line.pk, "item": line.item.code,
                     "batch": line.batch.batch_no if line.batch_id else "", "notes": notes})
    return check
