"""Purchase returns: goods sent back to the supplier.

Usually a batch that failed QC, or damaged goods found later. A return made
from a goods receipt line goes out at **that receipt's cost**, so the average
cost un-blends exactly (100 kg at 120 + 100 kg at 130 = average 125; returning
the second 100 kg at 130 brings it back to 120). A return of stock not from a
known receipt goes out at the current average.

Draft (``purchase.receive`` in each line's store branch) -> Posted
(``purchase.approve`` there). Quarantined and blocked batches can be returned;
that is what they wait for. Reversed by counter-moves if it was a mistake.
"""

from django.core.exceptions import ValidationError
from django.db import transaction

from common.tenant import use_company
from inventory import posting
from inventory.models import MoveType
from purchasing.common import (
    _writable,
    audit,
    get,
    local_today,
    now,
    plain,
    require,
    unit_factor,
)
from purchasing.models import GoodsReceipt, GoodsReceiptLine, PurchaseReturn, PurchaseReturnLine

Status = PurchaseReturn.Status
HEADER_FIELDS = ("supplier", "receipt", "return_date", "reason")
LINE_FIELDS = ("receipt_line", "item", "batch", "warehouse", "location", "qty", "uom", "note")


def get_return(*, actor, company_id, pk, code="purchase.receive"):
    row = get(PurchaseReturn, company_id, pk, "Purchase return")
    membership = require(actor, company_id, code)
    return membership, row


def _draft(row):
    if row.status != Status.DRAFT:
        raise ValidationError(f"{row.number} is {row.get_status_display().lower()}; "
                              "only a draft can change.")


@transaction.atomic
def create_return(*, actor, company_id, values):
    values = _writable(values, HEADER_FIELDS)
    receipt = values.get("receipt")
    if receipt is not None:
        if receipt.status != GoodsReceipt.Status.POSTED:
            raise ValidationError({"receipt": f"{receipt.number} is not posted."})
        values["supplier"] = receipt.supplier
    supplier = values.get("supplier")
    if supplier is None:
        raise ValidationError({"supplier": "Choose the supplier."})
    if values.get("return_date") is None:
        raise ValidationError({"return_date": "Give the date."})
    if not (values.get("reason") or "").strip():
        raise ValidationError({"reason": "Say why the goods go back."})
    membership = require(actor, company_id, "purchase.receive",
                         receipt.warehouse.branch_id if receipt else None)
    with use_company(company_id):
        row = PurchaseReturn(
            company=membership.company, created_by=actor, updated_by=actor,
            number=posting.next_number(company_id, PurchaseReturn.DOC_TYPE, PurchaseReturn.PREFIX,
                                       values["return_date"].year),
            **values)
        row.full_clean()
        row.save()
        audit(actor, membership, "purchase.return.created", row,
              after={"number": row.number, "supplier": row.supplier_id, "receipt": row.receipt_id})
    return row


def _returned_base(receipt_line, exclude_return=None):
    """Base units of a receipt line on posted returns (and other drafts of this one)."""
    rows = PurchaseReturnLine.all_objects.filter(
        receipt_line=receipt_line, purchase_return__status__in=[Status.POSTED, Status.DRAFT])
    if exclude_return is not None:
        rows = rows.exclude(purchase_return=exclude_return, purchase_return__status=Status.DRAFT)
    return sum((line.qty_base for line in rows), 0)


@transaction.atomic
def add_line(*, actor, company_id, return_id, values):
    membership, row = get_return(actor=actor, company_id=company_id, pk=return_id)
    _draft(row)
    values = _writable(values, LINE_FIELDS)
    receipt_line = values.get("receipt_line")
    with use_company(company_id):
        if receipt_line is not None:
            # As it is now, not as the caller's copy remembers it.
            receipt_line = values["receipt_line"] = GoodsReceiptLine.objects.select_related(
                "receipt__warehouse", "item", "batch").filter(pk=receipt_line.pk).first()
            if receipt_line is None:
                raise ValidationError({"receipt_line": "That line is not in this company."})
            if receipt_line.receipt.status != GoodsReceipt.Status.POSTED:
                raise ValidationError({"receipt_line": "That receipt is not posted."})
            if receipt_line.receipt.supplier_id != row.supplier_id:
                raise ValidationError({"receipt_line": "That receipt is from another supplier."})
            if row.receipt_id and receipt_line.receipt_id != row.receipt_id:
                raise ValidationError({"receipt_line": "That line is on another receipt."})
            values.update(item=receipt_line.item, batch=receipt_line.batch,
                          warehouse=receipt_line.receipt.warehouse,
                          location=values.get("location") or receipt_line.location)
        item, warehouse = values.get("item"), values.get("warehouse")
        if item is None or warehouse is None:
            raise ValidationError({"item": "Choose the received line, or the item and store."})
        require(actor, company_id, "purchase.receive", warehouse.branch_id)
        factor = unit_factor(item, values.get("uom"))
        # An inactive item can still go back to its supplier: only the quantity is checked.
        if values.get("qty") is None or values["qty"] <= 0:
            raise ValidationError({"qty": "Give a quantity above zero."})
        if item.track_batch and values.get("batch") is None:
            raise ValidationError({"batch": f"{item.code} keeps batches: say which batch goes back."})
        if values.get("batch") is not None and values["batch"].item_id != item.pk:
            raise ValidationError({"batch": "That batch is of another item."})
        cost = None
        if receipt_line is not None:
            already = _returned_base(receipt_line)
            if already + values["qty"] * factor > receipt_line.qty_accepted_base:
                raise ValidationError({"qty": (
                    f"More than received: {receipt_line.qty_accepted_base - already:f} "
                    f"{item.base_uom.code} of that line can still go back.")})
            cost = receipt_line.unit_cost_base
        line = PurchaseReturnLine(company=membership.company, purchase_return=row, factor=factor,
                                  unit_cost_base=cost, **values)
        line.full_clean()
        line.save()
    return line


@transaction.atomic
def remove_line(*, actor, company_id, return_id, line_id):
    _, row = get_return(actor=actor, company_id=company_id, pk=return_id)
    _draft(row)
    with use_company(company_id):
        deleted, _ = row.lines.filter(pk=line_id).delete()
    if not deleted:
        raise ValidationError("That line is not on this return.")


def _every_branch(actor, company_id, row, code):
    with use_company(company_id):
        for branch_id in set(row.lines.values_list("warehouse__branch_id", flat=True)):
            require(actor, company_id, code, branch_id)


@transaction.atomic
def post_return(*, actor, company_id, return_id):
    membership, row = get_return(actor=actor, company_id=company_id, pk=return_id,
                                 code="purchase.approve")
    with use_company(company_id):
        row = PurchaseReturn.objects.select_for_update().get(pk=row.pk)
        _draft(row)
        lines = list(row.lines.select_related("item__base_uom", "batch", "warehouse", "location",
                                              "receipt_line").order_by("pk"))
        if not lines:
            raise ValidationError("Add what goes back before posting.")
        _every_branch(actor, company_id, row, "purchase.approve")
        moves = posting.post(
            company_id=company_id, actor=actor, document_type=PurchaseReturn.DOC_TYPE,
            document_id=row.pk, move_type=MoveType.PURCHASE_RETURN, work_date=row.return_date,
            lines=[posting.Line(item=line.item, warehouse=line.warehouse, location=line.location,
                                batch=line.batch, qty=-line.qty_base, unit_cost=line.unit_cost_base,
                                line_id=line.pk, note=f"{row.number}: {row.reason}"[:255])
                   for line in lines])
        row.status = Status.POSTED
        row.posted_at, row.posted_by, row.updated_by = moves[0].posted_at, actor, actor
        row.save()
        audit(actor, membership, "purchase.return.posted", row, before={"status": "draft"},
              after={"status": "posted", "lines": len(lines),
                     "value": plain(sum((m.value for m in moves), 0))})
    return row


@transaction.atomic
def reverse_return(*, actor, company_id, return_id, reason):
    membership, row = get_return(actor=actor, company_id=company_id, pk=return_id,
                                 code="purchase.approve")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "Say why it is being reversed."})
    with use_company(company_id):
        row = PurchaseReturn.objects.select_for_update().get(pk=row.pk)
        if row.status != Status.POSTED:
            raise ValidationError("Only a posted return is reversed.")
        _every_branch(actor, company_id, row, "purchase.approve")
        posting.reverse(company_id=company_id, actor=actor, document_type=PurchaseReturn.DOC_TYPE,
                        document_id=row.pk, work_date=local_today(membership.company),
                        note=f"Reversal of {row.number}: {reason}")
        row.status = Status.REVERSED
        row.reversed_at, row.reversed_by = now(), actor
        row.reverse_reason, row.updated_by = reason[:255], actor
        row.save()
        audit(actor, membership, "purchase.return.reversed", row, before={"status": "posted"},
              after={"status": "reversed", "reason": reason})
    return row


@transaction.atomic
def cancel_return(*, actor, company_id, return_id):
    membership, row = get_return(actor=actor, company_id=company_id, pk=return_id)
    _draft(row)
    with use_company(company_id):
        row.status = Status.CANCELLED
        row.updated_by = actor
        row.save()
        audit(actor, membership, "purchase.return.cancelled", row,
              before={"status": "draft"}, after={"status": "cancelled"})
    return row


def returnable_lines(company_id, row):
    """Posted receipt lines of this supplier (and receipt) with something left to return."""
    lines = GoodsReceiptLine.all_objects.filter(
        company_id=company_id, receipt__status=GoodsReceipt.Status.POSTED,
        receipt__supplier=row.supplier).select_related("receipt", "item__base_uom", "batch")
    if row.receipt_id:
        lines = lines.filter(receipt=row.receipt)
    return [line for line in lines.order_by("-receipt__received_date", "pk")
            if line.qty_accepted_base - _returned_base(line) > 0]
