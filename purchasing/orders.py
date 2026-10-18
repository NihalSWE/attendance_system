"""Purchase orders: what is bought from which supplier, at what price.

Draft -> Waiting for approval -> Approved (sent to the supplier as a PDF) ->
Partly received -> Received; Closed when the rest will not come; Cancelled
while nothing is received. A draft is made from approved requisition lines
(their open quantities) or line by line. Making and editing need "Make
purchase orders" (``purchase.order``) in the order's branch; approving needs
``purchase.approve`` there.

Prices are per unit written on the line, before VAT; the discount comes off
the price; VAT is on the discounted amount. The cost that goes into stock at
the goods receipt is the discounted price without VAT (VAT is claimed back).
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F

from common.choices import ActiveStatus
from common.tenant import use_company
from inventory import posting
from purchasing import requisitions
from purchasing.common import (
    _writable,
    audit,
    check_item,
    check_own_approval,
    get,
    now,
    require,
    unit_factor,
)
from purchasing.models import (
    FOUR,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseRequisition,
    PurchaseRequisitionLine,
)

Status = PurchaseOrder.Status
HEADER_FIELDS = ("supplier", "branch", "order_date", "expected_date", "terms", "note")
LINE_FIELDS = ("item", "qty", "uom", "unit_price", "discount_pct", "vat_rate", "note")


def get_order(*, actor, company_id, pk, code="purchase.order"):
    order = get(PurchaseOrder, company_id, pk, "Purchase order")
    membership = require(actor, company_id, code, order.branch_id)
    return membership, order


def _draft(order):
    if order.status != Status.DRAFT:
        raise ValidationError(f"{order.number} is {order.get_status_display().lower()}; "
                              "only a draft can change.")


def totals(order):
    lines = list(PurchaseOrderLine.all_objects.filter(order=order))
    amount = sum((line.amount for line in lines), Decimal("0"))
    vat = sum((line.vat_amount for line in lines), Decimal("0"))
    return {"amount": amount, "vat": vat, "total": amount + vat, "lines": len(lines)}


def _clean_header(values):
    supplier, branch = values.get("supplier"), values.get("branch")
    if supplier is None:
        raise ValidationError({"supplier": "Choose the supplier."})
    if supplier.status != ActiveStatus.ACTIVE:
        raise ValidationError({"supplier": f"{supplier} is inactive."})
    if branch is None:
        raise ValidationError({"branch": "Choose the branch it is for."})
    if values.get("order_date") is None:
        raise ValidationError({"order_date": "Give the order date."})
    if values.get("expected_date") and values["expected_date"] < values["order_date"]:
        raise ValidationError({"expected_date": "Delivery before the order?"})


@transaction.atomic
def create_order(*, actor, company_id, values):
    values = _writable(values, HEADER_FIELDS)
    _clean_header(values)
    membership = require(actor, company_id, "purchase.order", values["branch"].pk)
    with use_company(company_id):
        order = PurchaseOrder(
            company=membership.company, created_by=actor, updated_by=actor,
            number=posting.next_number(company_id, PurchaseOrder.DOC_TYPE, PurchaseOrder.PREFIX,
                                       values["order_date"].year),
            **values)
        if not order.terms and values["supplier"].payment_terms_days:
            order.terms = f"Payment {values['supplier'].payment_terms_days} days after delivery."
        order.full_clean()
        order.save()
        audit(actor, membership, "purchase.order.created", order,
              after={"number": order.number, "supplier": order.supplier_id})
    return order


def _add(membership, order, values, requisition_line=None):
    item = values.get("item")
    if item is None:
        raise ValidationError({"item": "Choose the item."})
    factor = unit_factor(item, values.get("uom"))
    check_item(item, values.get("qty"), factor)
    if values.get("unit_price") is None:
        raise ValidationError({"unit_price": "Give the price."})
    line = PurchaseOrderLine(company=membership.company, order=order, factor=factor,
                             requisition_line=requisition_line, **values)
    line.full_clean()
    line.save()
    return line


@transaction.atomic
def add_line(*, actor, company_id, order_id, values):
    membership, order = get_order(actor=actor, company_id=company_id, pk=order_id)
    _draft(order)
    values = _writable(values, LINE_FIELDS)
    values.setdefault("discount_pct", Decimal("0"))
    values.setdefault("vat_rate", values["item"].vat_rate if values.get("item") and
                      values["item"].vat_rate is not None else Decimal("0"))
    with use_company(company_id):
        return _add(membership, order, values)


@transaction.atomic
def remove_line(*, actor, company_id, order_id, line_id):
    _, order = get_order(actor=actor, company_id=company_id, pk=order_id)
    _draft(order)
    with use_company(company_id):
        line = order.lines.filter(pk=line_id).select_related("requisition_line").first()
        if line is None:
            raise ValidationError("That line is not on this order.")
        _release(line)
        line.delete()


def _release(line):
    """Give a requisition line back the quantity this order line took."""
    if line.requisition_line_id:
        PurchaseRequisitionLine.all_objects.filter(pk=line.requisition_line_id).update(
            qty_ordered_base=F("qty_ordered_base") - line.qty_base)
        requisitions.refresh_status(PurchaseRequisition.all_objects.get(
            pk=line.requisition_line.requisition_id))


@transaction.atomic
def order_from_requisitions(*, actor, company_id, values, requisition_line_ids):
    """A draft order for the open quantities of these approved requisition lines.

    Each line keeps the requisition's unit when the open quantity is a whole
    number of it, else it is ordered in the base unit. The price starts at the
    item's standard cost (change it on the order).
    """
    order = create_order(actor=actor, company_id=company_id, values=values)
    membership, order = get_order(actor=actor, company_id=company_id, pk=order.pk)
    with use_company(company_id):
        lines = list(PurchaseRequisitionLine.objects.select_for_update(of=("self",))
                     .filter(pk__in=requisition_line_ids)
                     .select_related("requisition", "item__base_uom", "uom"))
        if not lines or len(lines) != len(set(requisition_line_ids)):
            raise ValidationError("Choose lines of approved requisitions.")
        for line in lines:
            if line.requisition.status != PurchaseRequisition.Status.APPROVED:
                raise ValidationError(f"{line.requisition.number} is not approved.")
            if line.requisition.branch_id != order.branch_id:
                raise ValidationError(f"{line.requisition.number} is for another branch.")
            open_base = line.qty_open_base
            if open_base <= 0:
                raise ValidationError(f"{line.item.code} on {line.requisition.number} is ordered already.")
            in_unit = (open_base / line.factor).quantize(FOUR)
            uom, qty = (line.uom, in_unit) if in_unit * line.factor == open_base else (
                line.item.base_uom, open_base)
            factor = line.factor if uom == line.uom else Decimal("1")
            price = (line.item.standard_cost or Decimal("0")) * factor
            _add(membership, order, {"item": line.item, "qty": qty, "uom": uom,
                                     "unit_price": price.quantize(FOUR),
                                     "discount_pct": Decimal("0"),
                                     "vat_rate": line.item.vat_rate or Decimal("0"),
                                     "note": f"From {line.requisition.number}"},
                 requisition_line=line)
            line.qty_ordered_base += open_base
            line.save(update_fields=["qty_ordered_base"])
            requisitions.refresh_status(line.requisition)
    return order


@transaction.atomic
def submit(*, actor, company_id, order_id):
    membership, order = get_order(actor=actor, company_id=company_id, pk=order_id)
    _draft(order)
    with use_company(company_id):
        if not order.lines.exists():
            raise ValidationError("Add what is ordered before sending it for approval.")
        order.status = Status.SUBMITTED
        order.submitted_at, order.submitted_by, order.updated_by = now(), actor, actor
        order.save()
        audit(actor, membership, "purchase.order.submitted", order,
              before={"status": "draft"}, after={"status": order.status, **_money(order)})
    return order


def _money(order):
    figures = totals(order)
    return {key: format(value, "f") for key, value in figures.items() if key != "lines"}


@transaction.atomic
def approve(*, actor, company_id, order_id):
    membership, order = get_order(actor=actor, company_id=company_id, pk=order_id,
                                  code="purchase.approve")
    if order.status != Status.SUBMITTED:
        raise ValidationError(f"{order.number} is not waiting for approval.")
    check_own_approval(membership, actor, order.submitted_by_id)
    with use_company(company_id):
        order.status = Status.APPROVED
        order.approved_at, order.approved_by, order.updated_by = now(), actor, actor
        order.save()
        audit(actor, membership, "purchase.order.approved", order,
              before={"status": "submitted"}, after={"status": order.status, **_money(order)})
    return order


@transaction.atomic
def send_back(*, actor, company_id, order_id, note):
    """Not approved: back to draft with the reason, to be changed and sent again."""
    membership, order = get_order(actor=actor, company_id=company_id, pk=order_id,
                                  code="purchase.approve")
    if order.status != Status.SUBMITTED:
        raise ValidationError(f"{order.number} is not waiting for approval.")
    note = (note or "").strip()
    if not note:
        raise ValidationError({"note": "Say what must change."})
    with use_company(company_id):
        order.status = Status.DRAFT
        order.closed_note = f"Sent back: {note}"[:255]
        order.updated_by = actor
        order.save()
        audit(actor, membership, "purchase.order.sent_back", order,
              before={"status": "submitted"}, after={"status": "draft", "note": note})
    return order


@transaction.atomic
def cancel(*, actor, company_id, order_id, note=""):
    membership, order = get_order(actor=actor, company_id=company_id, pk=order_id)
    if order.status not in (Status.DRAFT, Status.SUBMITTED, Status.APPROVED):
        raise ValidationError(f"{order.number} is {order.get_status_display().lower()}: "
                              "close it instead.")
    with use_company(company_id):
        if order.receipts.exclude(status__in=["cancelled", "reversed"]).exists():
            raise ValidationError("Goods were received against it; close it instead.")
        for line in order.lines.select_related("requisition_line"):
            _release(line)
        before = order.status
        order.status = Status.CANCELLED
        order.closed_note = (note or "")[:255]
        order.updated_by = actor
        order.save()
        audit(actor, membership, "purchase.order.cancelled", order,
              before={"status": before}, after={"status": "cancelled", "note": note})
    return order


@transaction.atomic
def close(*, actor, company_id, order_id, note):
    """The rest of a partly received order will not come."""
    membership, order = get_order(actor=actor, company_id=company_id, pk=order_id)
    if order.status != Status.PARTLY_RECEIVED:
        raise ValidationError("Only a partly received order is closed; cancel one with nothing received.")
    note = (note or "").strip()
    if not note:
        raise ValidationError({"note": "Say why the rest will not come."})
    with use_company(company_id):
        order.status = Status.CLOSED
        order.closed_note = note[:255]
        order.updated_by = actor
        order.save()
        audit(actor, membership, "purchase.order.closed", order,
              before={"status": "partly_received"}, after={"status": "closed", "note": note})
    return order


def refresh_status(order):
    """Approved / Partly received / Received from what the GRNs accepted."""
    if order.status not in (Status.APPROVED, Status.PARTLY_RECEIVED, Status.RECEIVED):
        return
    lines = list(PurchaseOrderLine.all_objects.filter(order=order))
    received = [line.qty_received_base for line in lines]
    if all(line.qty_open_base <= 0 for line in lines):
        status = Status.RECEIVED
    elif any(r > 0 for r in received):
        status = Status.PARTLY_RECEIVED
    else:
        status = Status.APPROVED
    if status != order.status:
        order.status = status
        order.save(update_fields=["status", "updated_at"])


def mark_sent(order, to):
    order.sent_at, order.sent_to = now(), to[:255]
    order.save(update_fields=["sent_at", "sent_to", "updated_at"])
