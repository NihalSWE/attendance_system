"""The posting service: the only code that moves stock (plan §3 D1, §5).

Every document (opening stock, goods receipt, issue, transfer, production,
delivery, adjustment) posts through ``post`` and undoes through ``reverse``.
In one transaction, for each line:

1. the stock balance row (item x batch x store x bin) is locked - always in
   the same order, so two postings never wait on each other in a circle;
2. the move is refused if it would take the balance below zero, dip into
   reserved stock, or issue from a batch that is quarantined, blocked or
   expired;
3. the average cost is updated (moving weighted average, D2) and the move
   is written to the ledger at that cost;
4. the balance row is updated.

Nothing here checks *who* may post; the document's service does that first.
Quantities arrive in the item's base unit.

**Weighted average, worked through** (one average per item for the company):
100 kg in stock at 120. A receipt of 100 kg at 130 makes the average
(100 x 120 + 100 x 130) / 200 = 125. An issue of 50 kg goes out at 125
(value -6,250) and leaves the average at 125 on 150 kg.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from common.choices import ActiveStatus
from common.tenant import use_company
from inventory.models import (
    ZERO,
    Batch,
    InventorySettings,
    ItemCost,
    MoveType,
    PeriodLock,
    StockBalance,
    StockMove,
)

FOUR = Decimal("0.0001")

#: Outgoing moves that need a released, unexpired batch (D8): stock that is
#: used, issued or sold. Transfers may move a quarantined batch (to the
#: quarantine or rejected store), and a reversal may take back anything.
NEEDS_USABLE_BATCH = frozenset({MoveType.ISSUE, MoveType.PRODUCTION_CONSUME, MoveType.DELIVERY})
#: Outgoing moves that may use reserved stock: a delivery is what releases
#: a sales order's reservation.
MAY_USE_RESERVED = frozenset({MoveType.DELIVERY, MoveType.REVERSAL})
#: Stores whose stock is not for use: nothing is issued, consumed or
#: delivered from them (it is transferred out, returned or written off).
UNUSABLE_STORES = frozenset({"quarantine", "rejected", "transit"})


@dataclass
class Line:
    """One movement to post. ``qty`` is in the base unit: + in, - out.

    ``unit_cost``: for a receipt-like move (opening, receipt, production
    output, an adjustment that found stock) the cost of one base unit, which
    updates the average. Left empty, the move goes at the current average.
    """

    item: object
    warehouse: object
    qty: Decimal
    batch: object = None
    location: object = None
    unit_cost: Decimal = None
    line_id: int = None
    note: str = ""
    reverses: object = None


def _key(line):
    """The balance row's identity, and the lock order."""
    return (line.item.pk, line.batch.pk if line.batch else 0,
            line.warehouse.pk, line.location.pk if line.location else 0)


def settings_for(company_id):
    """The company's inventory settings; unsaved defaults when there are none."""
    with use_company(company_id):
        return InventorySettings.objects.filter().first() or InventorySettings(company_id=company_id)


def check_period_open(company_id, work_date):
    """Refuse a posting dated in a closed month (D9)."""
    first = work_date.replace(day=1)
    if PeriodLock.all_objects.filter(company_id=company_id, month=first).exists():
        raise ValidationError(
            f"{first:%B %Y} is closed for stock. Date the entry in an open month."
        )


def _quantity_text(qty, item):
    return f"{qty.normalize():f} {item.base_uom.code}"


def _validate(line, company_id, move_type):
    item, warehouse = line.item, line.warehouse
    for name, obj in (("item", item), ("store", warehouse), ("batch", line.batch),
                      ("bin", line.location)):
        if obj is not None and obj.company_id != company_id:
            raise ValidationError(f"The {name} belongs to another company.")
    if not line.qty:
        raise ValidationError(f"{item.code}: the quantity is zero.")
    decimals = item.base_uom.decimals
    if line.qty != line.qty.quantize(Decimal(1).scaleb(-decimals)):
        raise ValidationError(
            f"{item.code} is counted in {item.base_uom.code} with {decimals} "
            f"digit{'s' if decimals != 1 else ''} after the point; "
            f"{line.qty.normalize():f} has more."
        )
    if line.reverses is None:
        if item.track_batch and line.batch is None:
            raise ValidationError(f"{item.code} keeps batches: say which batch.")
        if not item.track_batch and line.batch is not None:
            raise ValidationError(f"{item.code} does not keep batches.")
        if warehouse.status != ActiveStatus.ACTIVE:
            raise ValidationError(f"The store {warehouse.code} is inactive.")
    if line.batch is not None and line.batch.item_id != item.pk:
        raise ValidationError(f"Batch {line.batch.batch_no} is not a batch of {item.code}.")
    if line.location is not None and line.location.warehouse_id != warehouse.pk:
        raise ValidationError(f"Bin {line.location.code} is not in the store {warehouse.code}.")
    if line.unit_cost is not None and line.unit_cost < 0:
        raise ValidationError(f"{item.code}: a cost cannot be below zero.")
    if move_type == MoveType.REVERSAL and line.reverses is None:
        raise ValidationError("A reversal line must name the move it reverses.")


def _lock_balance(company_id, line):
    """The balance row for this line, locked; made (empty) if it is new."""
    filters = {
        "company_id": company_id, "item_id": line.item.pk, "warehouse_id": line.warehouse.pk,
        "batch_id": line.batch.pk if line.batch else None,
        "location_id": line.location.pk if line.location else None,
    }
    row = StockBalance.all_objects.select_for_update().filter(**filters).first()
    if row is not None:
        return row
    try:
        with transaction.atomic():
            return StockBalance.all_objects.create(**filters)
    except IntegrityError:
        # Another posting made it a moment ago: wait for its lock.
        return StockBalance.all_objects.select_for_update().get(**filters)


def _lock_cost(company_id, item, warehouse):
    filters = {"company_id": company_id, "item_id": item.pk,
               "warehouse_id": warehouse.pk if warehouse else None}
    row = ItemCost.all_objects.select_for_update().filter(**filters).first()
    if row is not None:
        return row
    try:
        with transaction.atomic():
            return ItemCost.all_objects.create(**filters)
    except IntegrityError:
        return ItemCost.all_objects.select_for_update().get(**filters)


def _apply_cost(cost, qty, unit_cost):
    """Update the average for one move; return the unit cost the move is valued at."""
    basis = cost.qty_basis
    new_basis = basis + qty
    if qty > 0 and unit_cost is not None:
        # A receipt: blend its cost into the average.
        if new_basis > 0 and basis > 0:
            cost.avg_cost = ((basis * cost.avg_cost + qty * unit_cost) / new_basis).quantize(
                FOUR, ROUND_HALF_UP)
        else:
            cost.avg_cost = unit_cost
        used = unit_cost
    elif qty < 0 and unit_cost is not None:
        # Taking back a receipt at its own cost (a reversal): un-blend it.
        if new_basis > 0:
            average = (basis * cost.avg_cost + qty * unit_cost) / new_basis
            if average >= 0:
                cost.avg_cost = average.quantize(FOUR, ROUND_HALF_UP)
        used = unit_cost
    else:
        # Everything else goes at the current average and leaves it alone.
        used = cost.avg_cost
    cost.qty_basis = new_basis
    return used


@transaction.atomic
def post(*, company_id, actor, document_type, document_id, move_type, lines, work_date,
         posted_at=None):
    """Post ``lines`` for one document. Returns the ``StockMove`` rows, in order.

    Raises ValidationError (and writes nothing) when any line cannot be posted.
    """
    if not lines:
        raise ValidationError("There is nothing to post.")
    if move_type not in MoveType.values:
        raise ValidationError("Unknown kind of stock move.")
    posted_at = posted_at or timezone.now()
    check_period_open(company_id, work_date)
    for line in lines:
        _validate(line, company_id, move_type)

    per_store = settings_for(company_id).valuation_scope == InventorySettings.ValuationScope.WAREHOUSE
    balances, costs = {}, {}
    # Lock in one fixed order: every balance row by key, then every cost row.
    for line in sorted(lines, key=_key):
        if _key(line) not in balances:
            balances[_key(line)] = _lock_balance(company_id, line)
    cost_keys = sorted({(line.item.pk, line.warehouse.pk if per_store else 0) for line in lines})
    by_item = {line.item.pk: line.item for line in lines}
    by_store = {line.warehouse.pk: line.warehouse for line in lines}
    for item_id, store_id in cost_keys:
        costs[(item_id, store_id)] = _lock_cost(company_id, by_item[item_id], by_store.get(store_id))

    moves = []
    for line in lines:
        balance = balances[_key(line)]
        item = line.item
        if line.qty < 0:
            batch = line.batch
            if (batch is not None and move_type in NEEDS_USABLE_BATCH
                    and not batch.usable_on(work_date)):
                reason = ("expired" if batch.expiry_date and batch.expiry_date < work_date
                          else batch.get_status_display().lower())
                raise ValidationError(f"Batch {batch.batch_no} of {item.code} is {reason} "
                                      "and cannot be used.")
            if move_type in NEEDS_USABLE_BATCH and line.warehouse.kind in UNUSABLE_STORES:
                raise ValidationError(f"{line.warehouse.code} is a {line.warehouse.get_kind_display().lower()} "
                                      "store: its stock cannot be used.")
            new_qty = balance.qty_on_hand + line.qty
            where = f"{line.warehouse.code}" + (f" / {line.location.code}" if line.location else "")
            which = f" (batch {batch.batch_no})" if batch else ""
            if new_qty < 0:
                raise ValidationError(
                    f"Not enough {item.code}{which} in {where}: "
                    f"{_quantity_text(balance.qty_on_hand, item)} there, "
                    f"{_quantity_text(-line.qty, item)} needed."
                )
            if move_type not in MAY_USE_RESERVED and new_qty < balance.qty_reserved:
                raise ValidationError(
                    f"{item.code}{which} in {where}: "
                    f"{_quantity_text(balance.qty_reserved, item)} is reserved for sales orders; "
                    f"only {_quantity_text(balance.qty_available, item)} can go."
                )
        cost = costs[(item.pk, line.warehouse.pk if per_store else 0)]
        unit = _apply_cost(cost, line.qty, line.unit_cost)
        moves.append(StockMove.all_objects.create(
            company_id=company_id, posted_at=posted_at, work_date=work_date,
            item=item, batch=line.batch, warehouse=line.warehouse, location=line.location,
            qty=line.qty, unit_cost=unit, value=(line.qty * unit).quantize(FOUR, ROUND_HALF_UP),
            move_type=move_type, document_type=document_type, document_id=document_id,
            document_line_id=line.line_id, reversed_move=line.reverses, note=line.note[:255],
            posted_by=actor if getattr(actor, "pk", None) else None,
        ))
        balance.qty_on_hand += line.qty
    for balance in balances.values():
        balance.save(update_fields=["qty_on_hand", "updated_at"])
    for cost in costs.values():
        cost.save(update_fields=["avg_cost", "qty_basis", "updated_at"])
    return moves


@transaction.atomic
def reverse(*, company_id, actor, document_type, document_id, work_date, note=""):
    """Undo every move of a document with counter-moves at the same cost.

    Refused when it would take stock below zero (the goods were already used)
    or when the document has nothing left to reverse.
    """
    originals = list(
        StockMove.all_objects.select_related("item__base_uom", "batch", "warehouse", "location")
        .filter(company_id=company_id, document_type=document_type, document_id=document_id,
                reversed_move__isnull=True, reversal__isnull=True)
        .order_by("pk")
    )
    if not originals:
        raise ValidationError("This document has no stock moves to reverse.")
    lines = [
        Line(item=m.item, warehouse=m.warehouse, qty=-m.qty, batch=m.batch, location=m.location,
             unit_cost=m.unit_cost, line_id=m.document_line_id, note=note, reverses=m)
        for m in originals
    ]
    # Stock coming back first, then stock going out: a document that passed
    # goods through a store (a transfer via transit) never dips it below zero.
    lines.sort(key=lambda line: line.qty < 0)
    return post(company_id=company_id, actor=actor, document_type=document_type,
                document_id=document_id, move_type=MoveType.REVERSAL, lines=lines,
                work_date=work_date)


def batch_for(*, company_id, actor, item, batch_no, source, supplier_batch_no="", mfg_date=None,
              expiry_date=None, unit_cost=None, status=Batch.Status.RELEASED):
    """The item's batch with this number, made if new. Call inside the posting transaction.

    An existing batch keeps its dates: a second line of the same batch with
    other dates is refused, because one batch has one expiry.
    """
    batch = Batch.all_objects.select_for_update().filter(
        company_id=company_id, item=item, batch_no=batch_no).first()
    if batch is None:
        batch = Batch(company_id=company_id, item=item, batch_no=batch_no, source=source,
                      supplier_batch_no=supplier_batch_no, mfg_date=mfg_date,
                      expiry_date=expiry_date, unit_cost=unit_cost, status=status,
                      created_by=actor, updated_by=actor)
        batch.full_clean()
        batch.save()
        return batch
    if (expiry_date and batch.expiry_date and expiry_date != batch.expiry_date) or (
            mfg_date and batch.mfg_date and mfg_date != batch.mfg_date):
        raise ValidationError(
            f"Batch {batch_no} of {item.code} already exists with other dates "
            f"(expiry {batch.expiry_date or '—'})."
        )
    return batch


def next_number(company_id, doc_type, prefix, year):
    """The next document number, e.g. ``OS-2026-00001``. Call inside a transaction:
    the sequence row stays locked until it commits, so numbers never repeat."""
    from inventory.models import DocumentSequence

    filters = {"company_id": company_id, "doc_type": doc_type, "year": year}
    row = DocumentSequence.all_objects.select_for_update().filter(**filters).first()
    if row is None:
        try:
            with transaction.atomic():
                row = DocumentSequence.all_objects.create(prefix=prefix, **filters)
        except IntegrityError:
            row = DocumentSequence.all_objects.select_for_update().get(**filters)
        row = DocumentSequence.all_objects.select_for_update().get(pk=row.pk)
    number = f"{row.prefix}-{year}-{row.next_number:05d}"
    row.next_number += 1
    row.save(update_fields=["next_number"])
    return number


def ledger_differences(company_id):
    """Where the ledger and the balances disagree (plan §5 rule 10).

    ``[{"item", "batch", "warehouse", "location", "ledger", "balance"}]`` -
    empty when every balance row equals the sum of its moves and every
    average's quantity equals the stock it covers.
    """
    from django.db.models import Sum

    ledger = {
        (r["item_id"], r["batch_id"], r["warehouse_id"], r["location_id"]): r["total"]
        for r in StockMove.all_objects.filter(company_id=company_id)
        .values("item_id", "batch_id", "warehouse_id", "location_id").annotate(total=Sum("qty"))
    }
    balances = {
        (r.item_id, r.batch_id, r.warehouse_id, r.location_id): r.qty_on_hand
        for r in StockBalance.all_objects.filter(company_id=company_id)
    }
    differences = []
    for key in sorted(set(ledger) | set(balances), key=lambda k: tuple(v or 0 for v in k)):
        moved, held = ledger.get(key, ZERO), balances.get(key, ZERO)
        if moved != held:
            differences.append({"item": key[0], "batch": key[1], "warehouse": key[2],
                                "location": key[3], "ledger": moved, "balance": held})
    per_store = settings_for(company_id).valuation_scope == InventorySettings.ValuationScope.WAREHOUSE
    on_hand = {}
    for (item_id, _batch, store_id, _bin), qty in balances.items():
        scope = (item_id, store_id if per_store else None)
        on_hand[scope] = on_hand.get(scope, ZERO) + qty
    for cost in ItemCost.all_objects.filter(company_id=company_id):
        scope = (cost.item_id, cost.warehouse_id)
        if cost.qty_basis != on_hand.get(scope, ZERO):
            differences.append({"item": cost.item_id, "batch": None, "warehouse": cost.warehouse_id,
                                "location": None, "ledger": on_hand.get(scope, ZERO),
                                "balance": cost.qty_basis, "cost": True})
    return differences


# --------------------------------------------------------------------------
# Reservations (sales orders, Phase 6)
# --------------------------------------------------------------------------

@transaction.atomic
def change_reserved(company_id, changes):
    """Add (+) or release (-) reserved quantities: ``[(balance_id, qty_base)]``.

    Rows are locked in id order. Reserved never goes below zero, nor above
    what is on hand. Returns the locked rows by id.
    """
    rows = {row.pk: row for row in StockBalance.all_objects.select_for_update().filter(
        company_id=company_id, pk__in=sorted({balance_id for balance_id, _qty in changes})).order_by("pk")}
    for balance_id, qty in changes:
        row = rows.get(balance_id)
        if row is None:
            raise ValidationError("A stock row to reserve was not found.")
        new = row.qty_reserved + qty
        if new < 0:
            new = ZERO
        if new > row.qty_on_hand:
            raise ValidationError(f"Only {row.qty_on_hand - row.qty_reserved:f} of item {row.item_id} is free "
                                  "to reserve on that row.")
        row.qty_reserved = new
    for row in rows.values():
        row.save(update_fields=["qty_reserved", "updated_at"])
    return rows
