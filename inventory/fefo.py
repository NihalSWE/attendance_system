"""First expiry, first out (D8): which batch to take, and how much of each.

Usable stock is stock of a released, unexpired batch, in a store whose stock
is for use (not quarantine, rejected or in transit), less what sales orders
reserved. Batches are taken earliest expiry first (no expiry last), then by
batch number. Items without batches are taken from their rows in the store
in bin order.

Example: 120 kg asked; batch A (expires March) has 80 kg, batch B (expires
June) 100 kg -> 80 kg of A and 40 kg of B.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import F

from inventory.models import StockBalance
from inventory.posting import UNUSABLE_STORES


def usable_rows(company_id, item, warehouse, day, location=None):
    """Balance rows of ``item`` in ``warehouse`` that may be used on ``day``,
    in the order FEFO takes them, each with ``available`` set."""
    rows = (StockBalance.all_objects.filter(company_id=company_id, item=item, warehouse=warehouse,
                                            qty_on_hand__gt=F("qty_reserved"))
            .select_related("batch", "location"))
    if location is not None:
        rows = rows.filter(location=location)
    if warehouse.kind in UNUSABLE_STORES:
        return []
    usable = []
    for row in rows:
        if row.batch_id and not row.batch.usable_on(day):
            continue
        row.available = row.qty_on_hand - row.qty_reserved
        usable.append(row)
    far = datetime.date.max
    usable.sort(key=lambda r: (r.batch.expiry_date or far if r.batch_id else far,
                               r.batch.batch_no if r.batch_id else "",
                               r.location.code if r.location_id else ""))
    return usable


def allocate(company_id, item, warehouse, qty_base, day, location=None):
    """``[(row, qty_base)]`` covering ``qty_base`` FEFO, or refused when the
    usable stock is short (saying how much there is)."""
    remaining = Decimal(qty_base)
    picked = []
    for row in usable_rows(company_id, item, warehouse, day, location):
        if remaining <= 0:
            break
        take = min(row.available, remaining)
        picked.append((row, take))
        remaining -= take
    if remaining > 0:
        have = sum((take for _row, take in picked), Decimal("0"))
        raise ValidationError(
            f"Not enough usable {item.code} in {warehouse.code}: {have.normalize():f} "
            f"{item.base_uom.code} can be used, {Decimal(qty_base).normalize():f} needed.")
    return picked


def first_batch(company_id, item, warehouse, day):
    """The batch FEFO would take first, or None."""
    for row in usable_rows(company_id, item, warehouse, day):
        if row.batch_id:
            return row.batch
    return None


def expiring_first(company_id, item, warehouse, day, batch):
    """True when ``batch`` is not the first FEFO batch (a reason is then asked)."""
    first = first_batch(company_id, item, warehouse, day)
    return first is not None and batch is not None and first.pk != batch.pk and (
        (first.expiry_date or datetime.date.max)
        < (batch.expiry_date or datetime.date.max))

