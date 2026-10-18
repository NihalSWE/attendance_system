"""Stock transfers between stores (Phase 4): dispatch -> in transit -> receive.

- **Dispatch** (sending store's branch, ``inventory.transfer``): every line
  leaves the sending store and goes into the branch's **in-transit store**
  (made the first time it is needed: ``TRN-<branch code>``), at the sending
  store's cost. Stock in transit cannot be used.
- **Receive** (receiving store's branch): what arrived goes into the
  receiving store at the same cost; anything short is written off from
  transit with its reason (a transfer shortage). By default everything
  dispatched arrived.
- A line without a batch, for an item that keeps batches, is taken first
  expiry first. A transfer may move a quarantined or blocked batch (to the
  quarantine or rejected store) when the batch is named.
- **Reverse**: counter-moves dated today put everything back in the sending
  store - refused once the received goods were used.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from common.choices import ActiveStatus
from common.tenant import use_company
from inventory import fefo, posting
from inventory.doc_common import (
    _writable,
    audit,
    check_qty,
    get,
    local_today,
    now,
    require,
    unit_factor,
)
from inventory.issues import _in_unit, label_from_scan
from inventory.models import MoveType, StockTransfer, StockTransferLine, Warehouse

Status = StockTransfer.Status
FIELDS = ("from_warehouse", "to_warehouse", "transfer_date", "vehicle_no", "note")
LINE_FIELDS = ("item", "batch", "from_location", "to_location", "qty", "uom", "note")


def transit_store(company, branch, actor=None):
    """The branch's in-transit store, made the first time a transfer needs it."""
    store = Warehouse.all_objects.filter(company=company, branch=branch,
                                         kind=Warehouse.Kind.TRANSIT).first()
    if store is None:
        code = f"TRN-{branch.code}"[:32]
        store = Warehouse(company=company, branch=branch, code=code, name=f"In transit ({branch.name})",
                          kind=Warehouse.Kind.TRANSIT, notes="Made by the software for transfers.",
                          created_by=actor, updated_by=actor)
        store.full_clean()
        store.save()
    return store


def get_transfer(*, actor, company_id, pk, code="inventory.transfer", side="from"):
    row = get(StockTransfer, company_id, pk, "Transfer")
    branch = row.from_warehouse.branch_id if side == "from" else row.to_warehouse.branch_id
    return require(actor, company_id, code, branch), row


@transaction.atomic
def create_transfer(*, actor, company_id, values):
    values = _writable(values, FIELDS)
    source, target = values.get("from_warehouse"), values.get("to_warehouse")
    for field, store in (("from_warehouse", source), ("to_warehouse", target)):
        if store is None or store.status != ActiveStatus.ACTIVE:
            raise ValidationError({field: "Choose an active store."})
        if store.kind == Warehouse.Kind.TRANSIT:
            raise ValidationError({field: "The in-transit store is used by the software itself."})
    if source.pk == target.pk:
        raise ValidationError({"to_warehouse": "Choose another store to send it to."})
    if values.get("transfer_date") is None:
        raise ValidationError({"transfer_date": "Give the date."})
    membership = require(actor, company_id, "inventory.transfer", source.branch_id)
    with use_company(company_id):
        row = StockTransfer(company=membership.company, created_by=actor, updated_by=actor,
                            number=posting.next_number(company_id, StockTransfer.DOC_TYPE,
                                                       StockTransfer.PREFIX, values["transfer_date"].year),
                            **values)
        row.full_clean()
        row.save()
        audit(actor, membership, "inventory.transfer.created", row,
              after={"number": row.number, "from": source.pk, "to": target.pk})
    return row


def _draft(row):
    if row.status != Status.DRAFT:
        raise ValidationError(f"{row.number} is {row.get_status_display().lower()}; only a draft can change.")


@transaction.atomic
def add_line(*, actor, company_id, transfer_id, values, label=None):
    membership, row = get_transfer(actor=actor, company_id=company_id, pk=transfer_id)
    _draft(row)
    values = _writable(values, LINE_FIELDS)
    item, batch = values.get("item"), values.get("batch")
    if item is None:
        raise ValidationError({"item": "Choose the item."})
    factor = unit_factor(item, values.get("uom"))
    check_qty(item, values.get("qty"), factor)
    for field, store in (("from_location", row.from_warehouse), ("to_location", row.to_warehouse)):
        location = values.get(field)
        if location is not None and location.warehouse_id != store.pk:
            raise ValidationError({field: f"Bin {location.code} is not in {store.code}."})
    if batch is not None and batch.item_id != item.pk:
        raise ValidationError({"batch": "That batch is of another item."})
    with use_company(company_id):
        if label is not None and StockTransferLine.objects.filter(transfer=row, label=label).exists():
            raise ValidationError(f"Label {label.code} is on this transfer already.")
        if item.track_batch and batch is None:
            made = []
            for balance, take in fefo.allocate(company_id, item, row.from_warehouse, values["qty"] * factor,
                                               row.transfer_date, values.get("from_location")):
                qty, uom, line_factor = _in_unit(take, values["uom"], factor, item)
                made.append(StockTransferLine.objects.create(
                    company=membership.company, transfer=row, item=item, batch=balance.batch,
                    from_location=balance.location, to_location=values.get("to_location"),
                    qty=qty, uom=uom, factor=line_factor, note=values.get("note", "")))
            return made
        line = StockTransferLine(company=membership.company, transfer=row, factor=factor, label=label, **values)
        line.full_clean()
        line.save()
        return [line]


@transaction.atomic
def scan_into_transfer(*, actor, company_id, transfer_id, code):
    _, row = get_transfer(actor=actor, company_id=company_id, pk=transfer_id)
    values, label = label_from_scan(company_id, code, row.from_warehouse)
    location = values.pop("location", None)
    values.update(from_location=location, to_location=None)
    return add_line(actor=actor, company_id=company_id, transfer_id=transfer_id, values=values, label=label)


@transaction.atomic
def remove_line(*, actor, company_id, transfer_id, line_id):
    _, row = get_transfer(actor=actor, company_id=company_id, pk=transfer_id)
    _draft(row)
    with use_company(company_id):
        if not row.lines.filter(pk=line_id).delete()[0]:
            raise ValidationError("That line is not on this transfer.")


@transaction.atomic
def dispatch(*, actor, company_id, transfer_id):
    membership, row = get_transfer(actor=actor, company_id=company_id, pk=transfer_id)
    with use_company(company_id):
        row = StockTransfer.objects.select_for_update(of=("self",)).select_related(
            "from_warehouse__branch", "to_warehouse").get(pk=row.pk)
        _draft(row)
        lines = list(row.lines.select_related("item__base_uom", "batch", "from_location").order_by("pk"))
        if not lines:
            raise ValidationError("Add what is sent before dispatching.")
        transit = transit_store(membership.company, row.from_warehouse.branch, actor)
        out = posting.post(
            company_id=company_id, actor=actor, document_type=StockTransfer.DOC_TYPE, document_id=row.pk,
            move_type=MoveType.TRANSFER_OUT, work_date=row.transfer_date,
            lines=[posting.Line(item=line.item, warehouse=row.from_warehouse, location=line.from_location,
                                batch=line.batch, qty=-line.qty_base, line_id=line.pk,
                                note=f"{row.number} to {row.to_warehouse.code}") for line in lines])
        posting.post(
            company_id=company_id, actor=actor, document_type=StockTransfer.DOC_TYPE, document_id=row.pk,
            move_type=MoveType.TRANSFER_IN, work_date=row.transfer_date,
            lines=[posting.Line(item=line.item, warehouse=transit, batch=line.batch, qty=line.qty_base,
                                unit_cost=move.unit_cost, line_id=line.pk,
                                note=f"{row.number} in transit") for line, move in zip(lines, out)])
        row.status, row.dispatched_at, row.dispatched_by, row.updated_by = Status.IN_TRANSIT, now(), actor, actor
        row.save()
        audit(actor, membership, "inventory.transfer.dispatched", row, before={"status": "draft"},
              after={"status": "in_transit", "lines": len(lines)})
    return row


@transaction.atomic
def receive(*, actor, company_id, transfer_id, received_date, received=None, reasons=None):
    """``received``: ``{line_id: base units that arrived}`` (all of it when left out);
    ``reasons``: ``{line_id: why the rest did not}`` - needed for every shortage."""
    membership, row = get_transfer(actor=actor, company_id=company_id, pk=transfer_id, side="to")
    received, reasons = received or {}, reasons or {}
    with use_company(company_id):
        row = StockTransfer.objects.select_for_update(of=("self",)).select_related(
            "from_warehouse__branch", "to_warehouse").get(pk=row.pk)
        if row.status != Status.IN_TRANSIT:
            raise ValidationError(f"{row.number} is not in transit.")
        if received_date is None or received_date < row.transfer_date:
            raise ValidationError({"received_date": "Received before it was sent?"})
        transit = transit_store(membership.company, row.from_warehouse.branch, actor)
        lines = list(row.lines.select_related("item__base_uom", "batch", "to_location").order_by("pk"))
        moves = {m.document_line_id: m for m in posting.StockMove.all_objects.filter(
            document_type=StockTransfer.DOC_TYPE, document_id=row.pk, move_type=MoveType.TRANSFER_IN)}
        arrive, short = [], []
        for line in lines:
            got = Decimal(received.get(line.pk, line.qty_base))
            if got < 0 or got > line.qty_base:
                raise ValidationError(f"{line.item.code}: between 0 and {line.qty_base.normalize():f} arrived.")
            check = got.quantize(Decimal(1).scaleb(-line.item.base_uom.decimals))
            if check != got:
                raise ValidationError(f"{line.item.code} is counted in whole {line.item.base_uom.code}.")
            missing = line.qty_base - got
            reason = (reasons.get(line.pk) or "").strip()
            if missing > 0 and not reason:
                raise ValidationError(f"{line.item.code}: {missing.normalize():f} {line.item.base_uom.code} "
                                      "did not arrive - say why.")
            line.qty_received_base, line.shortage_reason = got, reason[:255] if missing > 0 else ""
            line.save(update_fields=["qty_received_base", "shortage_reason"])
            cost = moves[line.pk].unit_cost
            if got > 0:
                arrive.append((line, got, cost))
            if missing > 0:
                short.append((line, missing, reason))
        if arrive:
            posting.post(
                company_id=company_id, actor=actor, document_type=StockTransfer.DOC_TYPE, document_id=row.pk,
                move_type=MoveType.TRANSFER_OUT, work_date=received_date,
                lines=[posting.Line(item=l.item, warehouse=transit, batch=l.batch, qty=-got, line_id=l.pk,
                                    note=f"{row.number} arrived") for l, got, _c in arrive])
            posting.post(
                company_id=company_id, actor=actor, document_type=StockTransfer.DOC_TYPE, document_id=row.pk,
                move_type=MoveType.TRANSFER_IN, work_date=received_date,
                lines=[posting.Line(item=l.item, warehouse=row.to_warehouse, location=l.to_location,
                                    batch=l.batch, qty=got, unit_cost=cost, line_id=l.pk,
                                    note=f"{row.number} from {row.from_warehouse.code}")
                       for l, got, cost in arrive])
        if short:
            posting.post(
                company_id=company_id, actor=actor, document_type=StockTransfer.DOC_TYPE, document_id=row.pk,
                move_type=MoveType.ADJUSTMENT, work_date=received_date,
                lines=[posting.Line(item=l.item, warehouse=transit, batch=l.batch, qty=-missing, line_id=l.pk,
                                    note=f"{row.number} shortage: {reason}"[:255]) for l, missing, reason in short])
        row.status, row.received_date = Status.RECEIVED, received_date
        row.received_at, row.received_by, row.updated_by = now(), actor, actor
        row.save()
        audit(actor, membership, "inventory.transfer.received", row, before={"status": "in_transit"},
              after={"status": "received", "shortages": [
                  {"item": l.item.code, "qty": format(m, "f"), "reason": r} for l, m, r in short]})
    return row


@transaction.atomic
def reverse_transfer(*, actor, company_id, transfer_id, reason):
    membership, row = get_transfer(actor=actor, company_id=company_id, pk=transfer_id)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "Say why it is being reversed."})
    with use_company(company_id):
        row = StockTransfer.objects.select_for_update().get(pk=row.pk)
        if row.status not in (Status.IN_TRANSIT, Status.RECEIVED):
            raise ValidationError("Only a dispatched or received transfer is reversed.")
        posting.reverse(company_id=company_id, actor=actor, document_type=StockTransfer.DOC_TYPE,
                        document_id=row.pk, work_date=local_today(membership.company),
                        note=f"Reversal of {row.number}: {reason}")
        before = row.status
        row.status, row.reversed_at, row.reversed_by = Status.REVERSED, now(), actor
        row.reverse_reason, row.updated_by = reason[:255], actor
        row.save()
        audit(actor, membership, "inventory.transfer.reversed", row, before={"status": before},
              after={"status": "reversed", "reason": reason})
    return row


@transaction.atomic
def cancel_transfer(*, actor, company_id, transfer_id):
    membership, row = get_transfer(actor=actor, company_id=company_id, pk=transfer_id)
    _draft(row)
    with use_company(company_id):
        row.status, row.updated_by = Status.CANCELLED, actor
        row.save()
        audit(actor, membership, "inventory.transfer.cancelled", row,
              before={"status": "draft"}, after={"status": "cancelled"})
    return row
