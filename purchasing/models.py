"""Purchasing (docs/inventory/00-PLAN.md): suppliers (Phase 1); purchase
requisitions, orders, goods receipts (GRN), quality checks and purchase
returns (Phase 3). Every write is in ``purchasing.services``; only a posted
GRN or purchase return moves stock, through ``inventory.posting``."""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from common.choices import ActiveStatus
from common.models import ActorTracked, TenantOwned

QTY = {"max_digits": 18, "decimal_places": 4}
FOUR = Decimal("0.0001")
CENT = Decimal("0.01")
ZERO = Decimal("0")


class Supplier(TenantOwned, ActorTracked):
    code = models.CharField(max_length=32)
    name = models.CharField(max_length=255)
    contact_person = models.CharField(max_length=128, blank=True)
    phone = models.CharField(max_length=32, blank=True)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=500, blank=True)
    bin = models.CharField("BIN", max_length=32, blank=True, help_text="VAT registration number.")
    tin = models.CharField("TIN", max_length=32, blank=True)
    payment_terms_days = models.PositiveSmallIntegerField(
        "Payment terms (days)", default=0, validators=[MaxValueValidator(365)],
        help_text="0 = cash on delivery.",
    )
    items_supplied = models.CharField(max_length=500, blank=True,
                                      help_text="Item codes, separated by commas.")
    notes = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=16, choices=ActiveStatus.choices, default=ActiveStatus.ACTIVE)

    class Meta:
        db_table = "purchasing_supplier"
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="uniq_supplier_code_per_company"),
        ]

    def __str__(self):
        return f"{self.code} · {self.name}"


def _user_fk():
    return models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name="+")


def _factor():
    return models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("1"))


class PurchaseRequisition(TenantOwned, ActorTracked):
    """A request to buy: what, how much, by when, why. Approved, then ordered."""

    DOC_TYPE = "purchase_requisition"
    PREFIX = "PR"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Waiting for approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        ORDERED = "ordered", "Ordered"
        CANCELLED = "cancelled", "Cancelled"

    number = models.CharField(max_length=32)
    branch = models.ForeignKey("organization.Branch", on_delete=models.PROTECT, related_name="+")
    department = models.ForeignKey("organization.Department", null=True, blank=True,
                                   on_delete=models.PROTECT, related_name="+")
    request_date = models.DateField()
    needed_by = models.DateField(null=True, blank=True)
    reason = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    submitted_at = models.DateTimeField(null=True, blank=True)
    submitted_by = _user_fk()
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = _user_fk()
    decision_note = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "purchasing_requisition"
        ordering = ("-request_date", "-pk")
        constraints = [
            models.UniqueConstraint(fields=["company", "number"], name="uniq_requisition_number"),
        ]

    def __str__(self):
        return self.number


class PurchaseRequisitionLine(TenantOwned):
    requisition = models.ForeignKey(PurchaseRequisition, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey("inventory.Item", on_delete=models.PROTECT, related_name="+")
    qty = models.DecimalField("Quantity", **QTY, validators=[MinValueValidator(FOUR)])
    uom = models.ForeignKey("inventory.Uom", on_delete=models.PROTECT, related_name="+")
    factor = _factor()
    note = models.CharField(max_length=255, blank=True)
    # Base units already put on purchase orders.
    qty_ordered_base = models.DecimalField(**QTY, default=ZERO)

    class Meta:
        db_table = "purchasing_requisition_line"
        ordering = ("requisition", "pk")

    def __str__(self):
        return f"{self.qty} {self.uom_id and self.uom.code} {self.item_id and self.item.code}"

    @property
    def qty_base(self):
        return (self.qty * self.factor).quantize(FOUR)

    @property
    def qty_open_base(self):
        return max(self.qty_base - self.qty_ordered_base, ZERO)


class PurchaseOrder(TenantOwned, ActorTracked):
    """An order to a supplier. Approved, sent, then received in one or more GRNs."""

    DOC_TYPE = "purchase_order"
    PREFIX = "PO"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Waiting for approval"
        APPROVED = "approved", "Approved"
        PARTLY_RECEIVED = "partly_received", "Partly received"
        RECEIVED = "received", "Received"
        CLOSED = "closed", "Closed"
        CANCELLED = "cancelled", "Cancelled"

    number = models.CharField(max_length=32)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="orders")
    branch = models.ForeignKey("organization.Branch", on_delete=models.PROTECT, related_name="+")
    order_date = models.DateField()
    expected_date = models.DateField("Delivery by", null=True, blank=True)
    terms = models.CharField("Payment and delivery terms", max_length=500, blank=True)
    note = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    submitted_at = models.DateTimeField(null=True, blank=True)
    submitted_by = _user_fk()
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = _user_fk()
    sent_at = models.DateTimeField(null=True, blank=True)
    sent_to = models.CharField(max_length=255, blank=True)
    closed_note = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "purchasing_order"
        ordering = ("-order_date", "-pk")
        constraints = [
            models.UniqueConstraint(fields=["company", "number"], name="uniq_purchase_order_number"),
        ]

    def __str__(self):
        return self.number

    @property
    def receivable(self):
        return self.status in (self.Status.APPROVED, self.Status.PARTLY_RECEIVED)


class PurchaseOrderLine(TenantOwned):
    order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines")
    requisition_line = models.ForeignKey(PurchaseRequisitionLine, null=True, blank=True,
                                         on_delete=models.PROTECT, related_name="order_lines")
    item = models.ForeignKey("inventory.Item", on_delete=models.PROTECT, related_name="+")
    qty = models.DecimalField("Quantity", **QTY, validators=[MinValueValidator(FOUR)])
    uom = models.ForeignKey("inventory.Uom", on_delete=models.PROTECT, related_name="+")
    factor = _factor()
    unit_price = models.DecimalField("Unit price", **QTY, validators=[MinValueValidator(ZERO)],
                                     help_text="Price of one unit written in Unit, before VAT.")
    discount_pct = models.DecimalField("Discount %", max_digits=5, decimal_places=2, default=ZERO,
                                       validators=[MinValueValidator(ZERO), MaxValueValidator(100)])
    vat_rate = models.DecimalField("VAT %", max_digits=5, decimal_places=2, default=ZERO,
                                   validators=[MinValueValidator(ZERO), MaxValueValidator(100)])
    note = models.CharField(max_length=255, blank=True)
    # Base units accepted into stock by posted GRNs (less reversed GRNs).
    qty_received_base = models.DecimalField(**QTY, default=ZERO)

    class Meta:
        db_table = "purchasing_order_line"
        ordering = ("order", "pk")

    def __str__(self):
        return f"{self.qty} {self.uom_id and self.uom.code} {self.item_id and self.item.code}"

    @property
    def qty_base(self):
        return (self.qty * self.factor).quantize(FOUR)

    @property
    def qty_open_base(self):
        return max(self.qty_base - self.qty_received_base, ZERO)

    @property
    def net_price(self):
        """One unit's price after the discount, before VAT."""
        return (self.unit_price * (100 - self.discount_pct) / 100).quantize(FOUR)

    @property
    def amount(self):
        return (self.qty * self.net_price).quantize(CENT)

    @property
    def vat_amount(self):
        return (self.amount * self.vat_rate / 100).quantize(CENT)


class GoodsReceipt(TenantOwned, ActorTracked):
    """A GRN: goods that arrived at a store, against an order or direct."""

    DOC_TYPE = "goods_receipt"
    PREFIX = "GRN"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"
        REVERSED = "reversed", "Reversed"
        CANCELLED = "cancelled", "Cancelled"

    number = models.CharField(max_length=32)
    order = models.ForeignKey(PurchaseOrder, null=True, blank=True, on_delete=models.PROTECT,
                              related_name="receipts")
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="receipts")
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, related_name="+")
    received_date = models.DateField()
    challan_no = models.CharField("Supplier challan no.", max_length=64, blank=True)
    invoice_no = models.CharField("Supplier invoice no.", max_length=64, blank=True)
    vehicle_no = models.CharField("Vehicle no.", max_length=32, blank=True)
    note = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = _user_fk()
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = _user_fk()
    reverse_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "purchasing_goods_receipt"
        ordering = ("-received_date", "-pk")
        constraints = [
            models.UniqueConstraint(fields=["company", "number"], name="uniq_goods_receipt_number"),
        ]

    def __str__(self):
        return self.number


class GoodsReceiptLine(TenantOwned):
    class Qc(models.TextChoices):
        NOT_REQUIRED = "not_required", "Not required"
        PENDING = "pending", "Waiting for QC"
        PASSED = "passed", "Passed"
        FAILED = "failed", "Failed"

    receipt = models.ForeignKey(GoodsReceipt, on_delete=models.CASCADE, related_name="lines")
    order_line = models.ForeignKey(PurchaseOrderLine, null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="receipt_lines")
    item = models.ForeignKey("inventory.Item", on_delete=models.PROTECT, related_name="+")
    location = models.ForeignKey("inventory.Location", null=True, blank=True,
                                 on_delete=models.PROTECT, related_name="+")
    qty_received = models.DecimalField("Received", **QTY, validators=[MinValueValidator(FOUR)])
    qty_rejected = models.DecimalField("Refused at the gate", **QTY, default=ZERO,
                                       validators=[MinValueValidator(ZERO)])
    uom = models.ForeignKey("inventory.Uom", on_delete=models.PROTECT, related_name="+")
    factor = _factor()
    unit_cost = models.DecimalField("Unit cost", **QTY, validators=[MinValueValidator(ZERO)],
                                    help_text="Cost of one unit written in Unit, before VAT.")
    batch_no = models.CharField("Batch no.", max_length=64, blank=True,
                                help_text="Left empty, one is made: the date received and a number.")
    supplier_batch_no = models.CharField("Supplier batch no.", max_length=64, blank=True)
    mfg_date = models.DateField("Manufacture date", null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    packages = models.PositiveIntegerField(
        "Packages", null=True, blank=True,
        help_text="Bags, drums or cartons it came in: one label each is made when posted.")
    note = models.CharField(max_length=255, blank=True)
    batch = models.ForeignKey("inventory.Batch", null=True, blank=True, on_delete=models.PROTECT,
                              related_name="+")
    qc_status = models.CharField(max_length=16, choices=Qc.choices, default=Qc.NOT_REQUIRED)

    class Meta:
        db_table = "purchasing_goods_receipt_line"
        ordering = ("receipt", "pk")

    def __str__(self):
        return f"{self.qty_received} {self.uom_id and self.uom.code} {self.item_id and self.item.code}"

    def clean(self):
        super().clean()
        if (self.qty_received is not None and self.qty_rejected is not None
                and self.qty_rejected >= self.qty_received):
            raise ValidationError({"qty_rejected": "Something must be accepted; to refuse the whole "
                                                   "delivery, do not receive it."})

    @property
    def qty_accepted(self):
        return self.qty_received - self.qty_rejected

    @property
    def qty_accepted_base(self):
        return (self.qty_accepted * self.factor).quantize(FOUR)

    @property
    def unit_cost_base(self):
        return (self.unit_cost / self.factor).quantize(FOUR)

    @property
    def value(self):
        return (self.qty_accepted * self.unit_cost).quantize(CENT)


class QualityCheck(TenantOwned):
    """The QC officer's decision on one received line (its batch)."""

    class Result(models.TextChoices):
        PASSED = "passed", "Passed: release the batch"
        FAILED = "failed", "Failed: block the batch"

    receipt_line = models.ForeignKey(GoodsReceiptLine, on_delete=models.PROTECT, related_name="checks")
    result = models.CharField(max_length=16, choices=Result.choices)
    notes = models.CharField(max_length=500, blank=True)
    checked_at = models.DateTimeField()
    checked_by = _user_fk()

    class Meta:
        db_table = "purchasing_quality_check"
        ordering = ("-checked_at", "-pk")

    def __str__(self):
        return f"{self.get_result_display()} for line {self.receipt_line_id}"


class PurchaseReturn(TenantOwned, ActorTracked):
    """Goods sent back to the supplier: failed QC, damaged, wrong item."""

    DOC_TYPE = "purchase_return"
    PREFIX = "PRT"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"
        REVERSED = "reversed", "Reversed"
        CANCELLED = "cancelled", "Cancelled"

    number = models.CharField(max_length=32)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="returns")
    receipt = models.ForeignKey(GoodsReceipt, null=True, blank=True, on_delete=models.PROTECT,
                                related_name="returns")
    return_date = models.DateField()
    reason = models.CharField(max_length=500)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = _user_fk()
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = _user_fk()
    reverse_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "purchasing_return"
        ordering = ("-return_date", "-pk")
        constraints = [
            models.UniqueConstraint(fields=["company", "number"], name="uniq_purchase_return_number"),
        ]

    def __str__(self):
        return self.number


class PurchaseReturnLine(TenantOwned):
    purchase_return = models.ForeignKey(PurchaseReturn, on_delete=models.CASCADE, related_name="lines")
    receipt_line = models.ForeignKey(GoodsReceiptLine, null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="return_lines")
    item = models.ForeignKey("inventory.Item", on_delete=models.PROTECT, related_name="+")
    batch = models.ForeignKey("inventory.Batch", null=True, blank=True, on_delete=models.PROTECT,
                              related_name="+")
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, related_name="+")
    location = models.ForeignKey("inventory.Location", null=True, blank=True,
                                 on_delete=models.PROTECT, related_name="+")
    qty = models.DecimalField("Quantity", **QTY, validators=[MinValueValidator(FOUR)])
    uom = models.ForeignKey("inventory.Uom", on_delete=models.PROTECT, related_name="+")
    factor = _factor()
    # The base-unit cost it goes out at: the receipt's own, so the average un-blends.
    unit_cost_base = models.DecimalField(**QTY, null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "purchasing_return_line"
        ordering = ("purchase_return", "pk")

    def __str__(self):
        return f"{self.qty} {self.uom_id and self.uom.code} {self.item_id and self.item.code}"

    @property
    def qty_base(self):
        return (self.qty * self.factor).quantize(FOUR)
