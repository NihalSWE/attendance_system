"""Stock adjustments and stock counts (Phase 4).

**Adjustment**: stock written off (damaged, expired, lost, sample) or found,
with a reason. Draft (``inventory.adjust`` in the store's branch) -> Waiting
for approval -> **approved and posted** in one step (``inventory.adjust_approve``;
not by the person who submitted it, unless owner/admin) or Rejected with a
reason. Stock found comes in at the cost given, or the current average.
Stock written off goes out at the current average.

**Count**: a physical or cycle count of one store. Starting it freezes what
the software says is there (every item, batch and bin with stock) on its
lines; people type what they count or scan package labels (each scan adds
the package's quantity); stock found that the software did not know is added.
When every line is counted it is submitted; **approving** it makes and posts a
stock adjustment for every difference (reason: stock count difference). Moves
in the store after the count started are shown as a warning: count when the
store is quiet.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from common.choices import ActiveStatus
from common.tenant import use_company
from inventory import posting
from inventory.doc_common import (
    _writable,
    audit,
    check_own_approval,
    check_qty,
    get,
    local_today,
    now,
    plain,
    require,
    unit_factor,
)
from inventory.models import (
    Batch,
    MoveType,
    StockAdjustment,
    StockAdjustmentLine,
    StockBalance,
    StockCount,
    StockCountLine,
    StockMove,
)

FOUR = Decimal("0.0001")
AStatus = StockAdjustment.Status
CStatus = StockCount.Status
FIELDS = ("warehouse", "adjustment_date", "reason", "note")
LINE_FIELDS = ("item", "batch", "batch_no", "expiry_date", "location", "qty_change", "uom", "unit_cost", "note")


def get_adjustment(*, actor, company_id, pk, code="inventory.adjust"):
    row = get(StockAdjustment, company_id, pk, "Stock adjustment")
    return require(actor, company_id, code, row.warehouse.branch_id), row


def _draft(row):
    if row.status != AStatus.DRAFT:
        raise ValidationError(f"{row.number} is {row.get_status_display().lower()}; only a draft can change.")


@transaction.atomic
def create_adjustment(*, actor, company_id, values, count=None):
    values = _writable(values, FIELDS)
    warehouse = values.get("warehouse")
    if warehouse is None or warehouse.status != ActiveStatus.ACTIVE:
        raise ValidationError({"warehouse": "Choose an active store."})
    if values.get("adjustment_date") is None:
        raise ValidationError({"adjustment_date": "Give the date."})
    if values.get("reason") not in StockAdjustment.Reason.values:
        raise ValidationError({"reason": "Choose the reason."})
    membership = require(actor, company_id, "inventory.adjust", warehouse.branch_id)
    with use_company(company_id):
        row = StockAdjustment(company=membership.company, created_by=actor, updated_by=actor, count=count,
                              number=posting.next_number(company_id, StockAdjustment.DOC_TYPE,
                                                         StockAdjustment.PREFIX, values["adjustment_date"].year),
                              **values)
        row.full_clean()
        row.save()
        audit(actor, membership, "inventory.adjustment.created", row,
              after={"number": row.number, "reason": row.reason})
    return row


def _clean_line(row, values):
    item, batch, location = values.get("item"), values.get("batch"), values.get("location")
    if item is None:
        raise ValidationError({"item": "Choose the item."})
    factor = unit_factor(item, values.get("uom"))
    check_qty(item, values.get("qty_change"), factor, field="qty_change", allow_negative=True, active=False)
    if location is not None and location.warehouse_id != row.warehouse_id:
        raise ValidationError({"location": f"Bin {location.code} is not in {row.warehouse.code}."})
    if batch is not None and batch.item_id != item.pk:
        raise ValidationError({"batch": "That batch is of another item."})
    batch_no = (values.get("batch_no") or "").strip()
    if item.track_batch:
        if values["qty_change"] < 0 and batch is None:
            raise ValidationError({"batch": f"{item.code} keeps batches: say which batch goes out."})
        if values["qty_change"] > 0 and batch is None and not batch_no:
            raise ValidationError({"batch": f"{item.code} keeps batches: pick the batch found, or give "
                                            "the new batch number."})
        if batch_no and item.track_expiry and batch is None and not values.get("expiry_date"):
            raise ValidationError({"expiry_date": f"{item.code} has an expiry date: give the new batch's."})
    elif batch is not None or batch_no:
        raise ValidationError({"batch": f"{item.code} does not keep batches."})
    if values["qty_change"] < 0 and values.get("unit_cost") is not None:
        raise ValidationError({"unit_cost": "Stock written off goes out at the average cost."})
    values.update(factor=factor, batch_no=batch_no if batch is None else "")
    return values


@transaction.atomic
def add_line(*, actor, company_id, adjustment_id, values):
    membership, row = get_adjustment(actor=actor, company_id=company_id, pk=adjustment_id)
    _draft(row)
    values = _clean_line(row, _writable(values, LINE_FIELDS))
    with use_company(company_id):
        line = StockAdjustmentLine(company=membership.company, adjustment=row, **values)
        line.full_clean()
        line.save()
    return line


@transaction.atomic
def remove_line(*, actor, company_id, adjustment_id, line_id):
    _, row = get_adjustment(actor=actor, company_id=company_id, pk=adjustment_id)
    _draft(row)
    with use_company(company_id):
        if not row.lines.filter(pk=line_id).delete()[0]:
            raise ValidationError("That line is not on this adjustment.")


@transaction.atomic
def submit(*, actor, company_id, adjustment_id):
    membership, row = get_adjustment(actor=actor, company_id=company_id, pk=adjustment_id)
    _draft(row)
    with use_company(company_id):
        if not row.lines.exists():
            raise ValidationError("Add what changes before sending it for approval.")
        row.status, row.submitted_at, row.submitted_by, row.updated_by = AStatus.SUBMITTED, now(), actor, actor
        row.save()
        audit(actor, membership, "inventory.adjustment.submitted", row,
              before={"status": "draft"}, after={"status": row.status})
    return row


def _post(actor, company_id, row):
    lines = list(row.lines.select_related("item__base_uom", "batch", "location").order_by("pk"))
    to_post = []
    for line in lines:
        batch = line.batch
        if batch is None and line.batch_no:
            batch = posting.batch_for(company_id=company_id, actor=actor, item=line.item,
                                      batch_no=line.batch_no, source=Batch.Source.ADJUSTMENT,
                                      expiry_date=line.expiry_date,
                                      unit_cost=(line.unit_cost / line.factor).quantize(FOUR)
                                      if line.unit_cost is not None else None)
            line.batch = batch
            line.save(update_fields=["batch"])
        cost = (line.unit_cost / line.factor).quantize(FOUR) if line.unit_cost is not None else None
        to_post.append(posting.Line(item=line.item, warehouse=row.warehouse, location=line.location,
                                    batch=batch, qty=line.qty_change_base, unit_cost=cost, line_id=line.pk,
                                    note=f"{row.number}: {row.get_reason_display()}"
                                         + (f" · {line.note}" if line.note else "")))
    # Stock found first, then stock written off (a move between bins nets out).
    to_post.sort(key=lambda line: line.qty < 0)
    return posting.post(company_id=company_id, actor=actor, document_type=StockAdjustment.DOC_TYPE,
                        document_id=row.pk, move_type=MoveType.ADJUSTMENT, lines=to_post,
                        work_date=row.adjustment_date)


@transaction.atomic
def decide(*, actor, company_id, adjustment_id, approve, note=""):
    membership, row = get_adjustment(actor=actor, company_id=company_id, pk=adjustment_id,
                                     code="inventory.adjust_approve")
    with use_company(company_id):
        row = StockAdjustment.objects.select_for_update(of=("self",)).select_related("warehouse").get(pk=row.pk)
        if row.status != AStatus.SUBMITTED:
            raise ValidationError(f"{row.number} is not waiting for approval.")
        check_own_approval(membership, actor, row.submitted_by_id)
        note = (note or "").strip()
        if not approve and not note:
            raise ValidationError({"note": "Say why it is rejected."})
        value = None
        if approve:
            moves = _post(actor, company_id, row)
            value = plain(sum((m.value for m in moves), Decimal("0")))
        row.status = AStatus.POSTED if approve else AStatus.REJECTED
        row.decided_at, row.decided_by, row.decision_note, row.updated_by = now(), actor, note[:255], actor
        row.save()
        audit(actor, membership, f"inventory.adjustment.{'approved' if approve else 'rejected'}", row,
              before={"status": "submitted"}, after={"status": row.status, "note": note, "value": value})
    return row


@transaction.atomic
def reverse_adjustment(*, actor, company_id, adjustment_id, reason):
    membership, row = get_adjustment(actor=actor, company_id=company_id, pk=adjustment_id,
                                     code="inventory.adjust_approve")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "Say why it is being reversed."})
    with use_company(company_id):
        row = StockAdjustment.objects.select_for_update().get(pk=row.pk)
        if row.status != AStatus.POSTED:
            raise ValidationError("Only a posted adjustment is reversed.")
        posting.reverse(company_id=company_id, actor=actor, document_type=StockAdjustment.DOC_TYPE,
                        document_id=row.pk, work_date=local_today(membership.company),
                        note=f"Reversal of {row.number}: {reason}")
        row.status, row.reversed_at, row.reversed_by = AStatus.REVERSED, now(), actor
        row.reverse_reason, row.updated_by = reason[:255], actor
        row.save()
        audit(actor, membership, "inventory.adjustment.reversed", row, before={"status": "posted"},
              after={"status": "reversed", "reason": reason})
    return row


@transaction.atomic
def cancel_adjustment(*, actor, company_id, adjustment_id):
    membership, row = get_adjustment(actor=actor, company_id=company_id, pk=adjustment_id)
    _draft(row)
    if row.count_id:
        raise ValidationError("This adjustment belongs to a stock count.")
    with use_company(company_id):
        row.status, row.updated_by = AStatus.CANCELLED, actor
        row.save()
        audit(actor, membership, "inventory.adjustment.cancelled", row,
              before={"status": "draft"}, after={"status": "cancelled"})
    return row


# --------------------------------------------------------------------------
# Stock count
# --------------------------------------------------------------------------

def get_count(*, actor, company_id, pk, code="inventory.adjust"):
    row = get(StockCount, company_id, pk, "Stock count")
    return require(actor, company_id, code, row.warehouse.branch_id), row


def _counting(row):
    if row.status != CStatus.COUNTING:
        raise ValidationError(f"{row.number} is {row.get_status_display().lower()}: counting is over.")


@transaction.atomic
def start_count(*, actor, company_id, values):
    values = _writable(values, ("warehouse", "count_date", "note"))
    warehouse = values.get("warehouse")
    if warehouse is None or warehouse.status != ActiveStatus.ACTIVE:
        raise ValidationError({"warehouse": "Choose an active store."})
    if values.get("count_date") is None:
        raise ValidationError({"count_date": "Give the date."})
    membership = require(actor, company_id, "inventory.adjust", warehouse.branch_id)
    with use_company(company_id):
        if StockCount.objects.filter(warehouse=warehouse, status__in=[CStatus.COUNTING, CStatus.SUBMITTED]).exists():
            raise ValidationError({"warehouse": f"{warehouse.code} has a count going on already."})
        row = StockCount(company=membership.company, created_by=actor, updated_by=actor, started_at=now(),
                         number=posting.next_number(company_id, StockCount.DOC_TYPE, StockCount.PREFIX,
                                                    values["count_date"].year), **values)
        row.full_clean()
        row.save()
        balances = StockBalance.objects.filter(warehouse=warehouse).exclude(qty_on_hand=0)
        StockCountLine.objects.bulk_create([
            StockCountLine(company=membership.company, count=row, item_id=b.item_id, batch_id=b.batch_id,
                           location_id=b.location_id, system_qty=b.qty_on_hand) for b in balances])
        audit(actor, membership, "inventory.count.started", row,
              after={"number": row.number, "warehouse": warehouse.pk, "lines": balances.count()})
    return row


@transaction.atomic
def set_counted(*, actor, company_id, count_id, counted):
    """``counted``: ``{line_id: base units counted (Decimal or None)}``."""
    _, row = get_count(actor=actor, company_id=company_id, pk=count_id)
    _counting(row)
    with use_company(company_id):
        lines = {line.pk: line for line in row.lines.select_related("item__base_uom")
                 .filter(pk__in=list(counted))}
        for line_id, value in counted.items():
            line = lines.get(int(line_id))
            if line is None:
                raise ValidationError("That line is not on this count.")
            if value is not None:
                value = Decimal(value)
                if value < 0:
                    raise ValidationError(f"{line.item.code}: a count is zero or more.")
                if value != value.quantize(Decimal(1).scaleb(-line.item.base_uom.decimals)):
                    raise ValidationError(f"{line.item.code} is counted in whole {line.item.base_uom.code}.")
            line.counted_qty = value
            line.save(update_fields=["counted_qty"])


@transaction.atomic
def add_found(*, actor, company_id, count_id, values):
    """Stock counted that the software did not have (system quantity 0)."""
    membership, row = get_count(actor=actor, company_id=company_id, pk=count_id)
    _counting(row)
    values = _writable(values, ("item", "batch", "location", "counted_qty", "note"))
    item = values.get("item")
    if item is None:
        raise ValidationError({"item": "Choose the item."})
    if item.track_batch and values.get("batch") is None:
        raise ValidationError({"batch": f"{item.code} keeps batches: pick the batch (add a new batch with a "
                                        "stock adjustment)."})
    if values.get("batch") is not None and values["batch"].item_id != item.pk:
        raise ValidationError({"batch": "That batch is of another item."})
    location = values.get("location")
    if location is not None and location.warehouse_id != row.warehouse_id:
        raise ValidationError({"location": f"Bin {location.code} is not in {row.warehouse.code}."})
    check_qty(item, values.get("counted_qty"), Decimal("1"), field="counted_qty", active=False)
    with use_company(company_id):
        line = row.lines.filter(item=item, batch=values.get("batch"), location=location).first()
        if line is not None:
            raise ValidationError(f"{item.code} is on the count already: type the number on its line.")
        line = StockCountLine(company=membership.company, count=row, system_qty=Decimal("0"), **values)
        line.full_clean()
        line.save()
    return line


@transaction.atomic
def scan_into_count(*, actor, company_id, count_id, code):
    """A package label adds its quantity to its line (made if the stock is new)."""
    from inventory.issues import label_from_scan

    membership, row = get_count(actor=actor, company_id=company_id, pk=count_id)
    _counting(row)
    values, label = label_from_scan(company_id, code, row.warehouse)
    if label is None:
        raise ValidationError("Scan package labels to count; type loose items on their line.")
    item = values["item"]
    qty_base = values["qty"] * unit_factor(item, values["uom"])
    with use_company(company_id):
        lines = row.lines.filter(item=item, batch=values.get("batch"))
        line = (lines.filter(location=values.get("location")).first() if values.get("location")
                else None) or lines.first()
        if line is None:
            line = StockCountLine.objects.create(company=membership.company, count=row, item=item,
                                                 batch=values.get("batch"), location=values.get("location"),
                                                 system_qty=Decimal("0"), note="Found by scanning")
        scanned = set(filter(None, (line.note or "").split(" ")))
        if label.code in scanned:
            raise ValidationError(f"Label {label.code} was counted already.")
        line.counted_qty = (line.counted_qty or Decimal("0")) + qty_base
        line.note = f"{line.note} {label.code}".strip()[:255]
        line.save(update_fields=["counted_qty", "note"])
    return line


@transaction.atomic
def zero_uncounted(*, actor, company_id, count_id):
    """Everything not counted was not found: counted as zero."""
    _, row = get_count(actor=actor, company_id=company_id, pk=count_id)
    _counting(row)
    with use_company(company_id):
        return row.lines.filter(counted_qty__isnull=True).update(counted_qty=Decimal("0"))


@transaction.atomic
def submit_count(*, actor, company_id, count_id):
    membership, row = get_count(actor=actor, company_id=company_id, pk=count_id)
    _counting(row)
    with use_company(company_id):
        missing = row.lines.filter(counted_qty__isnull=True).count()
        if missing:
            raise ValidationError(f"{missing} line{'s are' if missing != 1 else ' is'} not counted yet "
                                  "(or use “Not found = 0”).")
        row.status, row.submitted_at, row.submitted_by, row.updated_by = CStatus.SUBMITTED, now(), actor, actor
        row.save()
        audit(actor, membership, "inventory.count.submitted", row,
              before={"status": "counting"}, after={"status": "submitted"})
    return row


def moves_since_start(row):
    """Moves in the counted store after the count started (a warning on approval)."""
    return StockMove.all_objects.filter(company_id=row.company_id, warehouse_id=row.warehouse_id,
                                        posted_at__gt=row.started_at).exclude(
        document_type=StockAdjustment.DOC_TYPE, document_id__in=StockAdjustment.all_objects.filter(
            count=row).values("pk")).count()


@transaction.atomic
def approve_count(*, actor, company_id, count_id):
    """Every difference becomes a line of a stock adjustment, approved and posted."""
    membership, row = get_count(actor=actor, company_id=company_id, pk=count_id,
                                code="inventory.adjust_approve")
    with use_company(company_id):
        row = StockCount.objects.select_for_update(of=("self",)).select_related("warehouse").get(pk=row.pk)
        if row.status != CStatus.SUBMITTED:
            raise ValidationError(f"{row.number} is not waiting for approval.")
        check_own_approval(membership, actor, row.submitted_by_id)
        differences = [line for line in row.lines.select_related("item__base_uom", "batch", "location")
                       if line.difference]
        adjustment = None
        if differences:
            adjustment = StockAdjustment(
                company=membership.company, created_by=actor, updated_by=actor, count=row,
                warehouse=row.warehouse, adjustment_date=row.count_date,
                reason=StockAdjustment.Reason.COUNT, note=f"From {row.number}",
                number=posting.next_number(company_id, StockAdjustment.DOC_TYPE, StockAdjustment.PREFIX,
                                           row.count_date.year),
                submitted_at=row.submitted_at, submitted_by=row.submitted_by)
            adjustment.full_clean()
            adjustment.save()
            for line in differences:
                StockAdjustmentLine.objects.create(
                    company=membership.company, adjustment=adjustment, item=line.item, batch=line.batch,
                    location=line.location, qty_change=line.difference, uom=line.item.base_uom,
                    note=f"Counted {line.counted_qty.normalize():f}, system {line.system_qty.normalize():f}")
            moves = _post(actor, company_id, adjustment)
            adjustment.status = AStatus.POSTED
            adjustment.decided_at, adjustment.decided_by = now(), actor
            adjustment.save()
            audit(actor, membership, "inventory.adjustment.approved", adjustment,
                  after={"status": "posted", "count": row.number,
                         "value": plain(sum((m.value for m in moves), Decimal("0")))})
        row.status, row.approved_at, row.approved_by, row.updated_by = CStatus.APPROVED, now(), actor, actor
        row.save()
        audit(actor, membership, "inventory.count.approved", row, before={"status": "submitted"},
              after={"status": "approved", "differences": len(differences),
                     "adjustment": adjustment.number if adjustment else ""})
    return row


@transaction.atomic
def cancel_count(*, actor, company_id, count_id):
    membership, row = get_count(actor=actor, company_id=company_id, pk=count_id)
    if row.status not in (CStatus.COUNTING, CStatus.SUBMITTED):
        raise ValidationError(f"{row.number} is {row.get_status_display().lower()}.")
    with use_company(company_id):
        before, row.status, row.updated_by = row.status, CStatus.CANCELLED, actor
        row.save()
        audit(actor, membership, "inventory.count.cancelled", row,
              before={"status": before}, after={"status": "cancelled"})
    return row
