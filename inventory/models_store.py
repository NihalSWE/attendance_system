"""Store documents (docs/inventory/00-PLAN.md §4.3, Phase 4): requisitions,
issues, material returns, transfers, adjustments and stock counts.

Each is a header + lines, numbered per type and year, and moves stock only
when posted, through ``inventory.posting``. Imported by ``inventory.models``.
"""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from common.models import ActorTracked, TenantOwned
from inventory.models import (
    MONEY,
    QTY,
    ZERO,
    Batch,
    BarcodeLabel,
    Item,
    Location,
    Uom,
    Warehouse,
)

FOUR = Decimal("0.0001")

__all__ = [
    "StoreRequisition", "StoreRequisitionLine", "StoreIssue", "StoreIssueLine", "MaterialReturn",
    "MaterialReturnLine", "StockTransfer", "StockTransferLine", "StockAdjustment",
    "StockAdjustmentLine", "StockCount", "StockCountLine",
]


def _user():
    return models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name="+")


def _factor():
    return models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("1"))


class StoreDocument(TenantOwned, ActorTracked):
    """What every store document has: a number, a date, a status, who posted."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"
        REVERSED = "reversed", "Reversed"
        CANCELLED = "cancelled", "Cancelled"

    number = models.CharField(max_length=32)
    note = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = _user()
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = _user()
    reverse_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        abstract = True

    def __str__(self):
        return self.number


class LineQty(models.Model):
    """A quantity written in a unit of the item, with its base-unit value."""

    qty = models.DecimalField("Quantity", **QTY, validators=[MinValueValidator(FOUR)])
    uom = models.ForeignKey(Uom, on_delete=models.PROTECT, related_name="+")
    factor = _factor()

    class Meta:
        abstract = True

    @property
    def qty_base(self):
        return (self.qty * self.factor).quantize(FOUR)


# --------------------------------------------------------------------------
# Store requisition -> issue -> material return
# --------------------------------------------------------------------------

class StoreRequisition(TenantOwned, ActorTracked):
    """A department asks the store for materials (no stock moves)."""

    DOC_TYPE = "store_requisition"
    PREFIX = "SR"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Waiting for approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        PARTLY_ISSUED = "partly_issued", "Partly issued"
        ISSUED = "issued", "Issued"
        CLOSED = "closed", "Closed"
        CANCELLED = "cancelled", "Cancelled"

    number = models.CharField(max_length=32)
    department = models.ForeignKey("organization.Department", on_delete=models.PROTECT, related_name="+")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="+",
                                  help_text="The store asked.")
    request_date = models.DateField()
    needed_by = models.DateField(null=True, blank=True)
    purpose = models.CharField(max_length=500, blank=True,
                               help_text="Machine, production run, maintenance job…")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    submitted_at = models.DateTimeField(null=True, blank=True)
    submitted_by = _user()
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = _user()
    decision_note = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "inventory_store_requisition"
        ordering = ("-request_date", "-pk")
        constraints = [models.UniqueConstraint(fields=["company", "number"],
                                               name="uniq_store_requisition_number")]

    def __str__(self):
        return self.number


class StoreRequisitionLine(TenantOwned, LineQty):
    requisition = models.ForeignKey(StoreRequisition, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    note = models.CharField(max_length=255, blank=True)
    qty_issued_base = models.DecimalField(**QTY, default=ZERO)

    class Meta:
        db_table = "inventory_store_requisition_line"
        ordering = ("requisition", "pk")

    @property
    def qty_open_base(self):
        return max(self.qty_base - self.qty_issued_base, ZERO)


class StoreIssue(StoreDocument):
    """Goods given out of a store to a department (against a requisition or direct)."""

    DOC_TYPE = "store_issue"
    PREFIX = "SI"

    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="+")
    department = models.ForeignKey("organization.Department", on_delete=models.PROTECT, related_name="+")
    requisition = models.ForeignKey(StoreRequisition, null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="issues")
    issue_date = models.DateField()
    received_by = models.CharField("Received by", max_length=128, blank=True,
                                   help_text="Who took the goods.")

    class Meta:
        db_table = "inventory_store_issue"
        ordering = ("-issue_date", "-pk")
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="uniq_store_issue_number")]


class StoreIssueLine(TenantOwned, LineQty):
    issue = models.ForeignKey(StoreIssue, on_delete=models.CASCADE, related_name="lines")
    requisition_line = models.ForeignKey(StoreRequisitionLine, null=True, blank=True,
                                         on_delete=models.PROTECT, related_name="issue_lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    batch = models.ForeignKey(Batch, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    label = models.ForeignKey(BarcodeLabel, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    not_fefo_reason = models.CharField("Why not the first to expire", max_length=255, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "inventory_store_issue_line"
        ordering = ("issue", "pk")


class MaterialReturn(StoreDocument):
    """Goods coming back to a store from a department or production."""

    DOC_TYPE = "material_return"
    PREFIX = "MR"

    department = models.ForeignKey("organization.Department", on_delete=models.PROTECT, related_name="+")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="+",
                                  help_text="Good goods come back here.")
    issue = models.ForeignKey(StoreIssue, null=True, blank=True, on_delete=models.PROTECT,
                              related_name="returns")
    return_date = models.DateField()

    class Meta:
        db_table = "inventory_material_return"
        ordering = ("-return_date", "-pk")
        constraints = [models.UniqueConstraint(fields=["company", "number"],
                                               name="uniq_material_return_number")]


class MaterialReturnLine(TenantOwned, LineQty):
    class Condition(models.TextChoices):
        GOOD = "good", "Good: back to use"
        DAMAGED = "damaged", "Damaged: to the rejected store"

    material_return = models.ForeignKey(MaterialReturn, on_delete=models.CASCADE, related_name="lines")
    issue_line = models.ForeignKey(StoreIssueLine, null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="return_lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    batch = models.ForeignKey(Batch, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    condition = models.CharField(max_length=16, choices=Condition.choices, default=Condition.GOOD)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="+")
    location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    unit_cost_base = models.DecimalField(**MONEY, null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "inventory_material_return_line"
        ordering = ("material_return", "pk")


# --------------------------------------------------------------------------
# Transfer
# --------------------------------------------------------------------------

class StockTransfer(TenantOwned, ActorTracked):
    """Stock moved between stores: dispatched (in transit), then received.

    Dispatching takes it out of the sending store into the company's in-transit
    store; receiving puts what arrived into the receiving store and writes off
    any shortage (with its reason) from transit.
    """

    DOC_TYPE = "stock_transfer"
    PREFIX = "ST"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        IN_TRANSIT = "in_transit", "In transit"
        RECEIVED = "received", "Received"
        REVERSED = "reversed", "Reversed"
        CANCELLED = "cancelled", "Cancelled"

    number = models.CharField(max_length=32)
    from_warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="+")
    to_warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="+")
    transfer_date = models.DateField()
    vehicle_no = models.CharField("Vehicle no.", max_length=32, blank=True)
    note = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    dispatched_by = _user()
    received_date = models.DateField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    received_by = _user()
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = _user()
    reverse_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "inventory_stock_transfer"
        ordering = ("-transfer_date", "-pk")
        constraints = [models.UniqueConstraint(fields=["company", "number"],
                                               name="uniq_stock_transfer_number")]

    def __str__(self):
        return self.number


class StockTransferLine(TenantOwned, LineQty):
    transfer = models.ForeignKey(StockTransfer, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    batch = models.ForeignKey(Batch, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    from_location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.PROTECT,
                                      related_name="+")
    to_location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="+")
    label = models.ForeignKey(BarcodeLabel, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    # Filled when received: what arrived (base units) and why the rest did not.
    qty_received_base = models.DecimalField(**QTY, null=True, blank=True)
    shortage_reason = models.CharField(max_length=255, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "inventory_stock_transfer_line"
        ordering = ("transfer", "pk")

    @property
    def shortage_base(self):
        if self.qty_received_base is None:
            return ZERO
        return self.qty_base - self.qty_received_base


# --------------------------------------------------------------------------
# Adjustment and count
# --------------------------------------------------------------------------

class StockAdjustment(TenantOwned, ActorTracked):
    """Stock written off or found, with a reason; approved, then posted."""

    DOC_TYPE = "stock_adjustment"
    PREFIX = "SA"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Waiting for approval"
        POSTED = "posted", "Approved and posted"
        REJECTED = "rejected", "Rejected"
        REVERSED = "reversed", "Reversed"
        CANCELLED = "cancelled", "Cancelled"

    class Reason(models.TextChoices):
        DAMAGE = "damage", "Damaged"
        EXPIRY = "expiry", "Expired: written off"
        FOUND = "found", "Found"
        LOSS = "loss", "Lost or stolen"
        SAMPLE = "sample", "Sample or test"
        CORRECTION = "correction", "Correction of a mistake"
        COUNT = "count", "Stock count difference"
        TRANSIT = "transit", "Transfer shortage"

    number = models.CharField(max_length=32)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="+")
    adjustment_date = models.DateField()
    reason = models.CharField(max_length=16, choices=Reason.choices)
    note = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    count = models.OneToOneField("inventory.StockCount", null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="adjustment")
    submitted_at = models.DateTimeField(null=True, blank=True)
    submitted_by = _user()
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = _user()
    decision_note = models.CharField(max_length=255, blank=True)
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = _user()
    reverse_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "inventory_stock_adjustment"
        ordering = ("-adjustment_date", "-pk")
        constraints = [models.UniqueConstraint(fields=["company", "number"],
                                               name="uniq_stock_adjustment_number")]

    def __str__(self):
        return self.number


class StockAdjustmentLine(TenantOwned):
    """``qty_change`` in the line's unit: + found, - written off."""

    adjustment = models.ForeignKey(StockAdjustment, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    batch = models.ForeignKey(Batch, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    batch_no = models.CharField("New batch no.", max_length=64, blank=True,
                                help_text="For stock found in a batch not in the software.")
    expiry_date = models.DateField(null=True, blank=True)
    location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    qty_change = models.DecimalField("Change (+ found, − out)", **QTY)
    uom = models.ForeignKey(Uom, on_delete=models.PROTECT, related_name="+")
    factor = _factor()
    unit_cost = models.DecimalField("Unit cost (found stock)", **MONEY, null=True, blank=True,
                                    validators=[MinValueValidator(ZERO)],
                                    help_text="Left empty, found stock comes in at the average cost.")
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "inventory_stock_adjustment_line"
        ordering = ("adjustment", "pk")

    @property
    def qty_change_base(self):
        return (self.qty_change * self.factor).quantize(FOUR)


class StockCount(TenantOwned, ActorTracked):
    """A physical or cycle count of one store.

    Started: the system's quantities are frozen on the lines. Counted (typed
    or by scanning labels), submitted, then approved - which makes and posts
    the adjustment for every difference.
    """

    DOC_TYPE = "stock_count"
    PREFIX = "SC"

    class Status(models.TextChoices):
        COUNTING = "counting", "Counting"
        SUBMITTED = "submitted", "Waiting for approval"
        APPROVED = "approved", "Approved"
        CANCELLED = "cancelled", "Cancelled"

    number = models.CharField(max_length=32)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="+")
    count_date = models.DateField()
    note = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.COUNTING)
    started_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    submitted_by = _user()
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = _user()

    class Meta:
        db_table = "inventory_stock_count"
        ordering = ("-count_date", "-pk")
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="uniq_stock_count_number")]

    def __str__(self):
        return self.number


class StockCountLine(TenantOwned):
    count = models.ForeignKey(StockCount, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    batch = models.ForeignKey(Batch, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    system_qty = models.DecimalField("System (base unit)", **QTY, default=ZERO)
    counted_qty = models.DecimalField("Counted (base unit)", **QTY, null=True, blank=True,
                                      validators=[MinValueValidator(ZERO)])
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "inventory_stock_count_line"
        ordering = ("count", "item", "pk")

    @property
    def difference(self):
        return None if self.counted_qty is None else self.counted_qty - self.system_qty
