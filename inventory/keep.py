"""How much of an item to keep (2026-10-27, plan §7.1).

An item keeps either a fixed quantity (``min_stock``) or a number of days of
sales (``keep_days``), worked out from what was delivered to customers over
the last 30 days, less what customers returned. Used wherever the software
says an item is low: the reorder report, the morning email, and the "how many
to make" suggestion.
"""

import datetime
from decimal import ROUND_UP, Decimal

from django.db.models import Sum
from django.utils import timezone

from inventory.models import Item, MoveType, StockMove

DAYS_MEASURED = 30
ZERO = Decimal("0")


def sold_per_day(company_id, item_ids, today=None):
    """``{item id: average sold per day}`` over the last 30 days."""
    today = today or timezone.localdate()
    since = today - datetime.timedelta(days=DAYS_MEASURED)
    sold = dict(
        StockMove.all_objects.filter(
            company_id=company_id, item_id__in=item_ids, work_date__gt=since, work_date__lte=today,
            move_type__in=(MoveType.DELIVERY, MoveType.SALES_RETURN))
        .values("item_id").annotate(total=Sum("qty")).values_list("item_id", "total"))
    # Deliveries go out (negative), returns come back (positive).
    return {pk: max(-(total or ZERO), ZERO) / DAYS_MEASURED for pk, total in sold.items()}


def stock_to_keep(company_id, items, today=None):
    """``{item id: quantity to keep, in the base unit}``; an item with nothing
    to keep is left out."""
    by_days = [item.pk for item in items if item.keep_by == Item.KeepBy.DAYS and item.keep_days]
    per_day = sold_per_day(company_id, by_days, today) if by_days else {}
    kept = {}
    for item in items:
        if item.keep_by == Item.KeepBy.DAYS:
            if item.keep_days and per_day.get(item.pk):
                kept[item.pk] = (per_day[item.pk] * item.keep_days).quantize(Decimal("1"), rounding=ROUND_UP)
        elif item.min_stock:
            kept[item.pk] = item.min_stock
    return kept
