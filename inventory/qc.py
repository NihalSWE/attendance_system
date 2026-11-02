"""Stock waiting for a quality check, wherever it came from (2026-11-01).

An item with "Quality check before use" puts every new batch in quarantine: it
is counted in stock but can never be used, moved or sold until it passes. The
check used to be a small button on each production run, or the Purchase ->
Quality check page for goods received, so made stock seemed stuck. This module
lists every batch waiting, with where it came from and how much is in stock,
and passes or fails it through the check of its own document:

- **made here**: ``production.orders.record_qc`` (every batch of that run);
- **bought**: ``purchasing.receipts.record_check`` (that received line);
- **anything else** (a first count, an adjustment): the batch itself.

Passing releases the batch; failing blocks it (a reason is needed). Deciding
needs "Record quality checks" (``purchase.qc``) in the store's branch.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Sum

from common.tenant import use_company
from inventory import access
from inventory.doc_common import audit
from inventory.models import Batch, StockBalance, StockMove

CODE = "purchase.qc"
ZERO = Decimal("0")


@dataclass
class Waiting:
    batch: object
    item: object
    qty: Decimal = ZERO
    stores: list = field(default_factory=list)
    source: str = "other"          # "made", "bought" or "other"
    document: str = ""             # PRD-..., GRN-...
    url: str = ""
    order_id: int = None
    line_id: int = None


def waiting(company_id, *, branches=None, item=None):
    """Every batch in quarantine with stock, oldest first: ``[Waiting]``."""
    from django.urls import reverse

    with use_company(company_id):
        batches = Batch.objects.select_related("item", "item__base_uom").filter(status=Batch.Status.QUARANTINE)
        if item is not None:
            batches = batches.filter(item=item)
        batches = list(batches.order_by("created_at", "pk"))
        if not batches:
            return []
        stock = (StockBalance.objects.filter(batch__in=batches, qty_on_hand__gt=0)
                 .values("batch_id", "warehouse__name", "warehouse__branch_id")
                 .annotate(qty=Sum("qty_on_hand")))
        by_batch = defaultdict(list)
        for row in stock:
            if branches is not None and row["warehouse__branch_id"] not in branches:
                continue
            by_batch[row["batch_id"]].append((row["warehouse__name"], row["qty"]))
        found = {}
        for one in batches:
            if one.pk in by_batch:
                stores = by_batch[one.pk]
                found[one.pk] = Waiting(batch=one, item=one.item, qty=sum((q for _s, q in stores), ZERO),
                                        stores=stores)
        if not found:
            return []
        # Made here: the run that finished the batch.
        from production.models import Output

        made = dict(StockMove.objects.filter(document_type=Output.DOC_TYPE, batch_id__in=found)
                    .values_list("batch_id", "document_id"))
        outputs = {o.pk: o for o in Output.objects.select_related("order").filter(pk__in=set(made.values()))}
        for batch_id, output_id in made.items():
            output = outputs.get(output_id)
            if output is not None:
                row = found[batch_id]
                row.source, row.order_id, row.document = "made", output.order_id, output.order.number
                row.url = reverse("production:run", args=[output.order_id])
        # Bought: the received line still waiting for its check.
        from purchasing.models import GoodsReceiptLine

        for line in (GoodsReceiptLine.objects.select_related("receipt")
                     .filter(batch_id__in=found, qc_status=GoodsReceiptLine.Qc.PENDING)):
            row = found[line.batch_id]
            row.source, row.line_id, row.document = "bought", line.pk, line.receipt.number
            row.url = reverse("purchasing:receipt_detail", args=[line.receipt_id])
    return list(found.values())


def count(company_id, *, branches=None, item=None):
    return len(waiting(company_id, branches=branches, item=item))


def decide(*, actor, company_id, batch_id, passed, notes=""):
    """Pass (release) or fail (block) one waiting batch through its own document."""
    notes = (notes or "").strip()
    if not passed and not notes:
        raise ValidationError({"notes": "Say what failed."})
    rows = {row.batch.pk: row for row in waiting(company_id)}
    row = rows.get(int(batch_id))
    if row is None:
        raise ValidationError("This batch is not waiting for a quality check any more.")
    if row.source == "made":
        from production import orders

        orders.record_qc(actor=actor, company_id=company_id, order_id=row.order_id, passed=passed, notes=notes)
    elif row.source == "bought":
        from purchasing import receipts
        from purchasing.models import QualityCheck

        receipts.record_check(actor=actor, company_id=company_id, line_id=row.line_id,
                              result=QualityCheck.Result.PASSED if passed else QualityCheck.Result.FAILED,
                              notes=notes)
    else:
        stores = StockBalance.all_objects.filter(company_id=company_id, batch=row.batch).values_list(
            "warehouse__branch_id", flat=True).distinct()
        membership = None
        for branch_id in stores:
            membership = access.require(actor, company_id, CODE, branch_id)
        if membership is None:
            raise PermissionDenied("Recording a quality check needs that permission.")
        with use_company(company_id):
            batch = Batch.objects.select_for_update().get(pk=row.batch.pk)
            batch.status = Batch.Status.RELEASED if passed else Batch.Status.BLOCKED
            batch.updated_by = actor
            batch.save(update_fields=["status", "updated_by", "updated_at"])
        audit(actor, membership, f"inventory.qc.{'passed' if passed else 'failed'}", batch,
              after={"batch": batch.batch_no, "item": row.item.code, "notes": notes})
    return row
