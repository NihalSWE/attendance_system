"""Opening stock: what was counted on the opening day, entered or imported,
checked as a draft, then posted to the ledger in one go.

- **Draft** - lines can be added, removed or imported; nothing has moved.
  Needs "Enter stock adjustments, counts and opening stock"
  (``inventory.adjust``) in each line's store branch.
- **Posted** - every line becomes an ``opening`` move at its cost (which sets
  the item's first average cost) and the batches are made. Needs "Approve and
  post adjustments and opening stock" (``inventory.adjust_approve``) in
  every store on the document.
- **Reversed** - a mistake in a posted document is undone by counter-moves
  (dated today), never by editing it; then a corrected one is posted.
  Refused when the stock was already used.
- **Cancelled** - a draft that will not be posted.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from auditlog.services import record_company_event
from common.choices import ActiveStatus
from common.tenant import use_company
from inventory import access, posting
from inventory.masters import _writable, plain, units_for
from inventory.models import Batch, DocumentStatus, MoveType, OpeningStock, OpeningStockLine

HEADER_FIELDS = ("work_date", "note")
LINE_FIELDS = ("warehouse", "location", "item", "batch_no", "supplier_batch_no", "mfg_date",
               "expiry_date", "qty", "uom", "unit_cost", "note")


def local_today(company):
    from zoneinfo import ZoneInfo

    return timezone.now().astimezone(ZoneInfo(company.timezone or "Asia/Dhaka")).date()


def get_document(*, actor, company_id, pk, code="inventory.view"):
    """``(membership, document)`` for someone who holds ``code`` somewhere."""
    membership = access.require(actor, company_id, code)
    with use_company(company_id):
        document = OpeningStock.objects.filter(pk=pk).first()
    if document is None:
        raise PermissionDenied("Opening stock not found in this company.")
    return membership, document


def _editable(document):
    if not document.editable:
        raise ValidationError(f"{document.number} is {document.get_status_display().lower()}; "
                              "only a draft can change.")


@transaction.atomic
def create_document(*, actor, company_id, values):
    membership = access.require(actor, company_id, "inventory.adjust")
    values = _writable(values, HEADER_FIELDS)
    if not values.get("work_date"):
        raise ValidationError({"work_date": "Give the opening day."})
    with use_company(company_id):
        document = OpeningStock(
            company=membership.company, created_by=actor, updated_by=actor,
            number=posting.next_number(company_id, OpeningStock.DOC_TYPE, OpeningStock.PREFIX,
                                       values["work_date"].year),
            **values,
        )
        document.full_clean()
        document.save()
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action="inventory.opening.created", obj=document,
                             after={"number": document.number,
                                    "work_date": plain(document.work_date)})
    return document


def clean_line(document, values):
    """A line's values checked against its item: unit, batch, expiry, bin.

    Returns the values with ``factor`` set and the expiry worked out from the
    shelf life when only the manufacture date is given.
    """
    item, warehouse, location, uom = (values.get(k) for k in ("item", "warehouse", "location", "uom"))
    errors = {}
    if item is None:
        raise ValidationError({"item": "Choose the item."})
    if warehouse is None:
        raise ValidationError({"warehouse": "Choose the store."})
    if item.status != ActiveStatus.ACTIVE:
        errors["item"] = f"{item.code} is inactive."
    if warehouse.status != ActiveStatus.ACTIVE:
        errors["warehouse"] = f"The store {warehouse.code} is inactive."
    if location is not None and location.warehouse_id != warehouse.pk:
        errors["location"] = f"Bin {location.code} is not in {warehouse.code}."
    factors = units_for(item)
    if uom is None or uom.pk not in factors:
        errors["uom"] = (f"{item.code} is counted in {item.base_uom.code}"
                         + (" or its pack units." if len(factors) > 1 else "."))
    batch_no = (values.get("batch_no") or "").strip()
    if item.track_batch and not batch_no:
        errors["batch_no"] = f"{item.code} keeps batches: give the batch number."
    if not item.track_batch and batch_no:
        errors["batch_no"] = f"{item.code} does not keep batches; leave it empty."
    mfg, expiry = values.get("mfg_date"), values.get("expiry_date")
    if item.track_expiry and not expiry and mfg and item.shelf_life_days:
        expiry = mfg + datetime.timedelta(days=item.shelf_life_days)
    if item.track_expiry and not expiry:
        errors["expiry_date"] = f"{item.code} has an expiry date: give it."
    if mfg and expiry and expiry < mfg:
        errors["expiry_date"] = "The expiry date is before the manufacture date."
    if mfg and mfg > document.work_date:
        errors["mfg_date"] = "Made after the opening day?"
    if errors:
        raise ValidationError(errors)
    values = dict(values, batch_no=batch_no, expiry_date=expiry, factor=factors[uom.pk])
    if values.get("qty") is None or values["qty"] <= 0:
        raise ValidationError({"qty": "Give a quantity above zero."})
    qty_base = values["qty"] * values["factor"]
    decimals = item.base_uom.decimals
    if qty_base != qty_base.quantize(Decimal(1).scaleb(-decimals)):
        if decimals == 0:
            message = f"{item.code} is counted in whole {item.base_uom.code}."
        else:
            message = f"{item.code} takes at most {decimals} digits after the point in {item.base_uom.code}."
        raise ValidationError({"qty": message})
    return values


@transaction.atomic
def add_line(*, actor, company_id, document_id, values):
    membership, document = get_document(actor=actor, company_id=company_id, pk=document_id,
                                        code="inventory.adjust")
    _editable(document)
    values = _writable(values, LINE_FIELDS)
    warehouse = values.get("warehouse")
    if warehouse is not None:
        access.require(actor, company_id, "inventory.adjust", warehouse.branch_id)
    with use_company(company_id):
        values = clean_line(document, values)
        line = OpeningStockLine(company=membership.company, document=document, **values)
        line.full_clean()
        line.save()
        document.updated_by = actor
        document.save(update_fields=["updated_by", "updated_at"])
    return line


@transaction.atomic
def remove_line(*, actor, company_id, document_id, line_id):
    _, document = get_document(actor=actor, company_id=company_id, pk=document_id,
                               code="inventory.adjust")
    _editable(document)
    with use_company(company_id):
        line = document.lines.filter(pk=line_id).select_related("warehouse").first()
        if line is None:
            raise PermissionDenied("Line not found.")
        access.require(actor, company_id, "inventory.adjust", line.warehouse.branch_id)
        line.delete()


@transaction.atomic
def cancel_document(*, actor, company_id, document_id):
    membership, document = get_document(actor=actor, company_id=company_id, pk=document_id,
                                        code="inventory.adjust")
    _editable(document)
    with use_company(company_id):
        document.status = DocumentStatus.CANCELLED
        document.updated_by = actor
        document.save(update_fields=["status", "updated_by", "updated_at"])
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action="inventory.opening.cancelled", obj=document,
                             before={"status": "draft"}, after={"status": "cancelled"})
    return document


def _require_on_every_store(actor, company_id, document, code):
    branches = set(document.lines.values_list("warehouse__branch_id", flat=True))
    for branch_id in branches:
        access.require(actor, company_id, code, branch_id)


@transaction.atomic
def post_document(*, actor, company_id, document_id):
    """Post every line: batches made, ``opening`` moves written, costs set."""
    membership, document = get_document(actor=actor, company_id=company_id, pk=document_id,
                                        code="inventory.adjust_approve")
    with use_company(company_id):
        document = OpeningStock.objects.select_for_update().get(pk=document.pk)
        _editable(document)
        lines = list(document.lines.select_related(
            "item__base_uom", "warehouse", "location", "uom").order_by("pk"))
        if not lines:
            raise ValidationError("Add the counted stock before posting.")
        _require_on_every_store(actor, company_id, document, "inventory.adjust_approve")
        to_post = []
        for line in lines:
            batch = None
            if line.item.track_batch:
                batch = posting.batch_for(
                    company_id=company_id, actor=actor, item=line.item, batch_no=line.batch_no,
                    source=Batch.Source.OPENING, supplier_batch_no=line.supplier_batch_no,
                    mfg_date=line.mfg_date, expiry_date=line.expiry_date,
                    unit_cost=line.unit_cost_base,
                )
            to_post.append(posting.Line(
                item=line.item, warehouse=line.warehouse, location=line.location, batch=batch,
                qty=line.qty_base, unit_cost=line.unit_cost_base, line_id=line.pk,
                note=line.note,
            ))
        moves = posting.post(company_id=company_id, actor=actor,
                             document_type=OpeningStock.DOC_TYPE, document_id=document.pk,
                             move_type=MoveType.OPENING, lines=to_post,
                             work_date=document.work_date)
        document.status = DocumentStatus.POSTED
        document.posted_at = moves[0].posted_at
        document.posted_by = actor
        document.updated_by = actor
        document.save(update_fields=["status", "posted_at", "posted_by", "updated_by", "updated_at"])
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action="inventory.opening.posted", obj=document,
                             before={"status": "draft"},
                             after={"status": "posted", "lines": len(lines),
                                    "value": plain(sum((m.value for m in moves), Decimal("0")))})
    return document


@transaction.atomic
def reverse_document(*, actor, company_id, document_id, reason):
    """Undo a posted opening stock with counter-moves dated today."""
    membership, document = get_document(actor=actor, company_id=company_id, pk=document_id,
                                        code="inventory.adjust_approve")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "Say why it is being reversed."})
    with use_company(company_id):
        document = OpeningStock.objects.select_for_update().get(pk=document.pk)
        if document.status != DocumentStatus.POSTED:
            raise ValidationError("Only a posted opening stock can be reversed.")
        _require_on_every_store(actor, company_id, document, "inventory.adjust_approve")
        posting.reverse(company_id=company_id, actor=actor, document_type=OpeningStock.DOC_TYPE,
                        document_id=document.pk, work_date=local_today(membership.company),
                        note=f"Reversal of {document.number}: {reason}")
        document.status = DocumentStatus.REVERSED
        document.reversed_at = timezone.now()
        document.reversed_by = actor
        document.reverse_reason = reason[:255]
        document.updated_by = actor
        document.save(update_fields=["status", "reversed_at", "reversed_by", "reverse_reason",
                                     "updated_by", "updated_at"])
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action="inventory.opening.reversed", obj=document,
                             before={"status": "posted"},
                             after={"status": "reversed", "reason": reason})
    return document
