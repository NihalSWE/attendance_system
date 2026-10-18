"""Month-end close (D9, Phase 7): once a month is closed, nothing more is
posted dated in it - corrections go in the open month.

Closing is refused while documents dated in that month are still waiting
(a draft GRN, an issue not posted...): they would no longer post, so they
are posted, cancelled or re-dated first. It is refused too while the stock
check finds the ledger and the balances apart.

Accounts close with "Close a stock month" (``inventory.period_close``), by
the 5th of the next month. Reopening a month takes the same permission and
a reason, and is recorded.
"""

from datetime import date

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from common.tenant import use_company
from inventory.doc_common import audit, local_today, require
from inventory.models import PeriodLock
from inventory.posting import ledger_differences

CODE = "inventory.period_close"

#: Statuses that mean a document is done with (posted, undone or dropped).
FINAL = frozenset({"posted", "reversed", "cancelled", "rejected", "received", "in_transit", "closed",
                   "completed", "issued", "partly_issued", "applied"})


def _dated_documents():
    """``(model, date field, what it is called)`` for every document that posts stock."""
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

    return (
        (OpeningStock, "work_date", "Opening stock"),
        (GoodsReceipt, "received_date", "Goods receipt"),
        (PurchaseReturn, "return_date", "Purchase return"),
        (StoreIssue, "issue_date", "Store issue"),
        (MaterialReturn, "return_date", "Material return"),
        (StockTransfer, "transfer_date", "Transfer"),
        (StockAdjustment, "adjustment_date", "Adjustment"),
        (StockCount, "count_date", "Stock count"),
        (Consumption, "work_date", "Material use"),
        (Output, "output_date", "Production output"),
        (Delivery, "delivery_date", "Delivery"),
        (SalesReturn, "return_date", "Sales return"),
    )


def month_end(first):
    nxt = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


def waiting(company_id, first):
    """Documents dated in the month that are not posted, cancelled or done: ``[(what, number, status)]``."""
    last = month_end(first)
    found = []
    for model, field, what in _dated_documents():
        rows = (model.all_objects.filter(company_id=company_id, **{f"{field}__gte": first, f"{field}__lte": last})
                .exclude(status__in=FINAL).order_by(field, "number"))
        for row in rows:
            found.append((what, row.number, row.get_status_display()))
    return found


def first_of(value):
    if isinstance(value, str):
        value = date.fromisoformat(value)
    return value.replace(day=1)


@transaction.atomic
def close_month(*, actor, company_id, month, reason=""):
    """Close ``month`` (any day in it) for stock postings."""
    membership = require(actor, company_id, CODE)
    first = first_of(month)
    today = local_today(membership.company)
    if first >= today.replace(day=1):
        raise ValidationError({"month": "Only a month that has ended can be closed."})
    if PeriodLock.all_objects.filter(company_id=company_id, month=first).exists():
        raise ValidationError({"month": f"{first:%B %Y} is already closed."})
    pending = waiting(company_id, first)
    if pending:
        shown = ", ".join(f"{what} {number} ({status.lower()})" for what, number, status in pending[:8])
        more = f" and {len(pending) - 8} more" if len(pending) > 8 else ""
        raise ValidationError({"month": f"{len(pending)} document(s) dated in {first:%B %Y} are not posted "
                                        f"yet: {shown}{more}. Post, cancel or re-date them first."})
    if ledger_differences(company_id):
        raise ValidationError({"month": "The stock check finds the ledger and the balances apart. "
                                        "Ask the developers to look before closing."})
    try:
        with transaction.atomic(), use_company(company_id):
            lock = PeriodLock.objects.create(company=membership.company, month=first, locked_by=actor,
                                             reason=(reason or "").strip()[:255])
    except IntegrityError:
        raise ValidationError({"month": f"{first:%B %Y} is already closed."}) from None
    with use_company(company_id):
        audit(actor, membership, "inventory.period.closed", lock,
              after={"month": first.isoformat(), "reason": lock.reason})
    return lock


@transaction.atomic
def reopen_month(*, actor, company_id, lock_id, reason):
    """Open a closed month again, with a reason (it is recorded)."""
    membership = require(actor, company_id, CODE)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "Say why the month is opened again."})
    lock = PeriodLock.all_objects.select_for_update().filter(company_id=company_id, pk=lock_id).first()
    if lock is None:
        raise ValidationError("That closed month was not found.")
    with use_company(company_id):
        audit(actor, membership, "inventory.period.reopened", lock,
              before={"month": lock.month.isoformat(), "reason": lock.reason}, after={"reason": reason[:255]})
    PeriodLock.all_objects.filter(pk=lock.pk).delete()
    return lock.month
