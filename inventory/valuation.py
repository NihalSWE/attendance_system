"""Stock, value and age at any date, read from the stock ledger (Phase 7).

The ledger (``StockMove``) is the truth. Every quantity and value here is a
sum of moves dated (``work_date``) on or before the day asked for, so the
valuation at a past date is exactly what the ledger held at that day's close
- including entries posted later but dated back into an open month.

A move's value is its quantity at the average cost it went in or out at
(``inventory.posting``), so the sum of values is the stock's value: no
re-pricing, no averages worked out again here.

**Example:** 100 kg in on 1 Oct at 120 (value 12,000), 100 kg in on 5 Oct at
130 (13,000, average 125), 50 kg out on 8 Oct at 125 (-6,250). Valued at
6 Oct: 200 kg, 25,000. At 8 Oct: 150 kg, 18,750 (average 125).
"""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Max, Q, Sum

from inventory.models import MoveType, StockMove

ZERO = Decimal("0")
CENT = Decimal("0.01")

#: Moves that use stock up (store issues, production, deliveries): what
#: "slow-moving" looks for. Transfers and adjustments are not use.
USE_MOVES = (MoveType.ISSUE, MoveType.PRODUCTION_CONSUME, MoveType.DELIVERY)

#: Age brackets in days, for the aging report: (label, up to and including).
AGE_BUCKETS = (("0-30 days", 30), ("31-60 days", 60), ("61-90 days", 90),
               ("91-180 days", 180), ("181-365 days", 365), ("Over a year", None))


def moves(company_id, *, store_ids=None, item_ids=None):
    """The company's ledger, narrowed to these stores and items (None = all)."""
    rows = StockMove.all_objects.filter(company_id=company_id)
    if store_ids is not None:
        rows = rows.filter(warehouse_id__in=store_ids)
    if item_ids is not None:
        rows = rows.filter(item_id__in=item_ids)
    return rows


def _shows(qty, value):
    return qty != 0 or value.quantize(CENT) != 0


def at(company_id, on_date, *, store_ids=None, item_ids=None, by_store=False):
    """``{(item_id, store_id or None): (qty, value)}`` at the close of ``on_date``.

    Rows that hold nothing (no quantity, no value to the paisa) are left out.
    """
    keys = ("item_id", "warehouse_id") if by_store else ("item_id",)
    held = {}
    for row in (moves(company_id, store_ids=store_ids, item_ids=item_ids)
                .filter(work_date__lte=on_date).order_by().values(*keys)
                .annotate(q=Sum("qty"), v=Sum("value"))):
        if _shows(row["q"], row["v"]):
            held[(row["item_id"], row.get("warehouse_id"))] = (row["q"], row["v"])
    return held


def movement(company_id, date_from, date_to, *, store_ids=None, item_ids=None):
    """Per item: opening, in, out and closing, quantity and value, over the dates.

    ``{item_id: {"open_q", "open_v", "in_q", "in_v", "out_q", "out_v",
    "close_q", "close_v"}}`` - "out" as positive numbers. A reversal counts
    the way it moved (a reversed receipt is stock going out).
    """
    found = defaultdict(lambda: dict.fromkeys(
        ("open_q", "open_v", "in_q", "in_v", "out_q", "out_v", "close_q", "close_v"), ZERO))
    for (item_id, _store), (qty, value) in at(company_id, date_from - timedelta(days=1),
                                              store_ids=store_ids, item_ids=item_ids).items():
        found[item_id]["open_q"], found[item_id]["open_v"] = qty, value
    inward, outward = Q(qty__gt=0), Q(qty__lt=0)
    for row in (moves(company_id, store_ids=store_ids, item_ids=item_ids)
                .filter(work_date__gte=date_from, work_date__lte=date_to).order_by()
                .values("item_id").annotate(
                    in_q=Sum("qty", filter=inward), in_v=Sum("value", filter=inward),
                    out_q=Sum("qty", filter=outward), out_v=Sum("value", filter=outward))):
        line = found[row["item_id"]]
        line["in_q"], line["in_v"] = row["in_q"] or ZERO, row["in_v"] or ZERO
        line["out_q"], line["out_v"] = -(row["out_q"] or ZERO), -(row["out_v"] or ZERO)
    for line in found.values():
        line["close_q"] = line["open_q"] + line["in_q"] - line["out_q"]
        line["close_v"] = line["open_v"] + line["in_v"] - line["out_v"]
    return dict(found)


def last_used(company_id, *, store_ids=None, item_ids=None, on_date=None):
    """``{item_id: the last day it was issued, used in production or delivered}``."""
    rows = moves(company_id, store_ids=store_ids, item_ids=item_ids).filter(
        move_type__in=USE_MOVES, qty__lt=0)
    if on_date:
        rows = rows.filter(work_date__lte=on_date)
    return dict(rows.order_by().values("item_id").annotate(last=Max("work_date"))
                .values_list("item_id", "last"))


def last_received(company_id, *, store_ids=None, item_ids=None, on_date=None):
    """``{item_id: the last day stock of it came in}`` (any incoming move but a reversal)."""
    rows = moves(company_id, store_ids=store_ids, item_ids=item_ids).filter(qty__gt=0).exclude(
        move_type=MoveType.REVERSAL)
    if on_date:
        rows = rows.filter(work_date__lte=on_date)
    return dict(rows.order_by().values("item_id").annotate(last=Max("work_date"))
                .values_list("item_id", "last"))


def bucket_of(days):
    for index, (_label, upto) in enumerate(AGE_BUCKETS):
        if upto is None or days <= upto:
            return index
    return len(AGE_BUCKETS) - 1


def aging(company_id, on_date, *, store_ids=None, item_ids=None):
    """How old the stock in each store is, first in first out.

    What a store holds is taken to be what came into it last: its incoming
    moves are walked from the newest back until they cover the quantity on
    hand, each part aged from the day it came in (a transfer starts the clock
    again in the new store). ``{(item_id, store_id): {"qty", "value",
    "buckets": [qty per AGE_BUCKETS], "oldest": date}}``.
    """
    held = {key: qv for key, qv in at(company_id, on_date, store_ids=store_ids,
                                      item_ids=item_ids, by_store=True).items() if qv[0] > 0}
    if not held:
        return {}
    left = {key: qty for key, (qty, _value) in held.items()}
    result = {key: {"qty": qty, "value": value, "buckets": [ZERO] * len(AGE_BUCKETS), "oldest": None}
              for key, (qty, value) in held.items()}
    incoming = (moves(company_id, store_ids=store_ids, item_ids=item_ids)
                .filter(qty__gt=0, work_date__lte=on_date,
                        item_id__in={item for item, _store in held},
                        warehouse_id__in={store for _item, store in held})
                .order_by("-work_date", "-pk").values_list("item_id", "warehouse_id", "qty", "work_date"))
    for item_id, store_id, qty, day in incoming.iterator():
        key = (item_id, store_id)
        need = left.get(key, ZERO)
        if need <= 0:
            continue
        part = min(need, qty)
        left[key] = need - part
        result[key]["buckets"][bucket_of((on_date - day).days)] += part
        result[key]["oldest"] = day
    for key, need in left.items():
        if need > 0:
            # More on hand than came in (the ledger began with it): the oldest bracket.
            result[key]["buckets"][-1] += need
    return result


# --------------------------------------------------------------------------
# Which document a move belongs to
# --------------------------------------------------------------------------

def _document_models():
    from inventory.models import (
        MaterialReturn,
        OpeningStock,
        StockAdjustment,
        StockCount,
        StockTransfer,
        StoreIssue,
    )
    from production.models import Consumption, Output
    from purchasing.models import GoodsReceipt, PurchaseReturn
    from sales.models import Delivery, SalesReturn

    return {model.DOC_TYPE: model for model in (
        OpeningStock, StoreIssue, MaterialReturn, StockTransfer, StockAdjustment, StockCount,
        GoodsReceipt, PurchaseReturn, Consumption, Output, Delivery, SalesReturn)}


def document_numbers(pairs):
    """``{(document_type, document_id): "GRN-2026-00012"}`` for the moves' documents."""
    wanted = defaultdict(set)
    for doc_type, doc_id in pairs:
        wanted[doc_type].add(doc_id)
    models = _document_models()
    numbers = {}
    for doc_type, ids in wanted.items():
        model = models.get(doc_type)
        if model is None:
            continue
        for pk, number in model.all_objects.filter(pk__in=ids).values_list("pk", "number"):
            numbers[(doc_type, pk)] = number
    return numbers
