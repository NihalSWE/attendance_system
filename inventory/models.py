"""Store & Inventory (docs/inventory/00-PLAN.md): masters and the stock ledger.

Every table is company-owned (``TenantOwned``) and scoped like the rest of
the product. Quantities are ``Decimal(18,4)`` in the item's **base unit**;
money is ``Decimal(18,4)`` in the company's currency. Never float.

The heart of the module is two tables, written only by
``inventory.posting``:

- ``StockMove`` - the ledger. One row per movement, + in / - out. Never
  updated, never deleted; a mistake is undone by a reversal (counter-moves).
- ``StockBalance`` - one row per item x batch x store x bin, updated in the
  same transaction as the moves, under a row lock, so pages read stock
  without adding up the ledger. ``check_stock`` proves the two agree.

``ItemCost`` holds the moving weighted average cost (D2), per item for the
whole company, or per item per store (``InventorySettings.valuation_scope``).
A balance row's value is its quantity x that average, worked out when shown;
storing it would mean rewriting every row of the item on each receipt.
"""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q, Value
from django.db.models.functions import Coalesce

from common.choices import ActiveStatus
from common.models import ActorTracked, TenantManager, TenantOwned

#: Quantity and money columns: 18 digits, 4 after the point.
QTY = {"max_digits": 18, "decimal_places": 4}
MONEY = {"max_digits": 18, "decimal_places": 4}
ZERO = Decimal("0")


class ItemKind(models.TextChoices):
    """The five item types the client named (plan §1)."""

    RAW = "raw", "Raw material"
    PACKAGING = "packaging", "Packaging"
    FINISHED = "finished", "Finished goods"
    SPARE = "spare", "Spare part"
    GENERAL = "general", "General store"


class InventorySettings(TenantOwned, ActorTracked):
    """Company-wide inventory choices; one row per company, made on first save.

    Pages read ``settings_for`` (defaults when there is no row yet), so
    opening a page never writes.
    """

    class ValuationScope(models.TextChoices):
        COMPANY = "company", "One average cost per item for the whole company"
        WAREHOUSE = "warehouse", "One average cost per item in each store"

    valuation_scope = models.CharField(
        max_length=16, choices=ValuationScope.choices, default=ValuationScope.COMPANY,
        help_text="Fixed once stock has moved: changing it would rewrite every cost.",
    )
    expiry_alert_days = models.PositiveIntegerField(
        default=30, validators=[MaxValueValidator(3650)],
        help_text="Warn this many days before a batch expires (an item can say otherwise).",
    )

    class SmallLabel(models.TextChoices):
        """Item and bin labels (D3/D4): the label stock in the printer, in mm."""

        S50X25 = "50x25", "50 × 25 mm"
        S40X30 = "40x30", "40 × 30 mm"
        S60X40 = "60x40", "60 × 40 mm"

    class LargeLabel(models.TextChoices):
        """Batch and package labels: room for a QR code and the details."""

        L100X50 = "100x50", "100 × 50 mm"
        L100X75 = "100x75", "100 × 75 mm"
        L75X50 = "75x50", "75 × 50 mm"

    small_label = models.CharField(
        "Item and bin label size", max_length=8, choices=SmallLabel.choices,
        default=SmallLabel.S50X25,
    )
    large_label = models.CharField(
        "Batch and package label size", max_length=8, choices=LargeLabel.choices,
        default=LargeLabel.L100X50,
    )
    gs1_labels = models.BooleanField(
        "GS1-128 on package labels", default=False,
        help_text="Adds a GS1-128 barcode (item, batch, expiry, quantity) for items with a "
                  "13- or 14-digit barcode. Only when a customer asks for GS1.",
    )

    # Phase 7: the slow-moving report and the morning alert.
    slow_moving_days = models.PositiveIntegerField(
        "Slow-moving after (days)", default=90, validators=[MinValueValidator(7), MaxValueValidator(3650)],
        help_text="An item in stock not issued, used or sold for this long is slow-moving.",
    )
    alert_emails = models.TextField(
        "Send the daily stock alert to", blank=True,
        help_text="Email addresses, one per line or separated by commas. Every morning they get "
                  "what is below its reorder point and what is expired or expiring. Empty: no alert.",
    )

    class Meta:
        db_table = "inventory_settings"
        constraints = [
            models.UniqueConstraint(fields=["company"], name="uniq_inventory_settings_per_company"),
        ]

    def __str__(self):
        return "Inventory settings"


class Uom(TenantOwned, ActorTracked):
    """A unit of measure: KG, L, PCS, CARTON..."""

    class Kind(models.TextChoices):
        WEIGHT = "weight", "Weight"
        VOLUME = "volume", "Volume"
        COUNT = "count", "Count"
        LENGTH = "length", "Length"
        OTHER = "other", "Other"

    code = models.CharField(max_length=16)
    name = models.CharField(max_length=64)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.COUNT)
    decimals = models.PositiveSmallIntegerField(
        default=0, validators=[MaxValueValidator(4)],
        help_text="Digits after the point allowed in quantities (0 for pieces, 3 for kg).",
    )
    status = models.CharField(max_length=16, choices=ActiveStatus.choices, default=ActiveStatus.ACTIVE)

    class Meta:
        db_table = "inventory_uom"
        ordering = ("code",)
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="uniq_uom_code_per_company"),
        ]

    def __str__(self):
        return self.code


class ItemCategory(TenantOwned, ActorTracked):
    """The client's own grouping (Sweetener, Bottle, Bearing), under one item type."""

    code = models.CharField(max_length=32)
    name = models.CharField(max_length=128)
    kind = models.CharField(max_length=16, choices=ItemKind.choices)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="children"
    )
    status = models.CharField(max_length=16, choices=ActiveStatus.choices, default=ActiveStatus.ACTIVE)

    class Meta:
        db_table = "inventory_item_category"
        ordering = ("kind", "name")
        verbose_name_plural = "item categories"
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="uniq_item_category_code_per_company"),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if self.parent_id:
            if self.pk and self.parent_id == self.pk:
                raise ValidationError({"parent": "A category cannot be inside itself."})
            parent = ItemCategory.all_objects.filter(pk=self.parent_id).first()
            seen = {self.pk}
            while parent is not None:
                if parent.pk in seen:
                    raise ValidationError({"parent": "That would put the category inside itself."})
                seen.add(parent.pk)
                parent = parent.parent
            if ItemCategory.all_objects.get(pk=self.parent_id).kind != self.kind:
                raise ValidationError({"parent": "The parent category is for another item type."})


class Warehouse(TenantOwned, ActorTracked):
    """A store: the raw material store, the FG warehouse... It belongs to a branch."""

    class Kind(models.TextChoices):
        RAW = "raw", "Raw material"
        PACKAGING = "packaging", "Packaging"
        FINISHED = "finished", "Finished goods"
        SPARE = "spare", "Spare parts"
        GENERAL = "general", "General"
        PRODUCTION = "production", "Production floor"
        QUARANTINE = "quarantine", "Quarantine"
        TRANSIT = "transit", "In transit"
        REJECTED = "rejected", "Rejected"

    branch = models.ForeignKey("organization.Branch", on_delete=models.PROTECT, related_name="warehouses")
    code = models.CharField(max_length=32)
    name = models.CharField(max_length=128)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    notes = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=16, choices=ActiveStatus.choices, default=ActiveStatus.ACTIVE)

    class Meta:
        db_table = "inventory_warehouse"
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="uniq_warehouse_code_per_company"),
        ]

    def __str__(self):
        return f"{self.code} · {self.name}"


class Location(TenantOwned, ActorTracked):
    """A bin/rack/shelf inside a store (R1-S2-B3). Optional: a store may have none."""

    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="locations")
    code = models.CharField(max_length=32)
    description = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=16, choices=ActiveStatus.choices, default=ActiveStatus.ACTIVE)

    class Meta:
        db_table = "inventory_location"
        ordering = ("warehouse", "code")
        constraints = [
            models.UniqueConstraint(fields=["company", "warehouse", "code"],
                                    name="uniq_location_code_per_warehouse"),
        ]

    def __str__(self):
        return self.code


class Item(TenantOwned, ActorTracked):
    """Anything the factory buys, makes, stores or sells."""

    code = models.CharField(max_length=32)
    name = models.CharField(max_length=255)
    name_bn = models.CharField("Name in Bangla", max_length=255, blank=True)
    kind = models.CharField(max_length=16, choices=ItemKind.choices)
    category = models.ForeignKey(ItemCategory, on_delete=models.PROTECT, related_name="items")
    base_uom = models.ForeignKey(Uom, on_delete=models.PROTECT, related_name="+")
    # A barcode already printed on the product (EAN-13). Labels the software
    # prints come in Phase 2 and have their own codes.
    barcode = models.CharField(max_length=64, blank=True)
    track_batch = models.BooleanField(default=False)
    track_expiry = models.BooleanField(default=False)
    shelf_life_days = models.PositiveIntegerField(null=True, blank=True)
    qc_required = models.BooleanField(default=False)
    min_stock = models.DecimalField(**QTY, null=True, blank=True, validators=[MinValueValidator(ZERO)])
    reorder_point = models.DecimalField(**QTY, null=True, blank=True, validators=[MinValueValidator(ZERO)])
    reorder_qty = models.DecimalField(**QTY, null=True, blank=True, validators=[MinValueValidator(ZERO)])
    max_stock = models.DecimalField(**QTY, null=True, blank=True, validators=[MinValueValidator(ZERO)])
    default_warehouse = models.ForeignKey(
        Warehouse, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    standard_cost = models.DecimalField(**MONEY, null=True, blank=True, validators=[MinValueValidator(ZERO)])
    sale_price = models.DecimalField(**MONEY, null=True, blank=True, validators=[MinValueValidator(ZERO)])
    vat_rate = models.DecimalField(
        "VAT %", max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(ZERO), MaxValueValidator(Decimal("100"))],
    )
    hs_code = models.CharField("HS code", max_length=32, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=16, choices=ActiveStatus.choices, default=ActiveStatus.ACTIVE)

    class Meta:
        db_table = "inventory_item"
        ordering = ("code",)
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="uniq_item_code_per_company"),
            models.UniqueConstraint(fields=["company", "barcode"], condition=~Q(barcode=""),
                                    name="uniq_item_barcode_per_company"),
        ]
        indexes = [models.Index(fields=["company", "kind"], name="inv_item_company_kind")]

    def __str__(self):
        return f"{self.code} · {self.name}"

    def clean(self):
        super().clean()
        errors = {}
        if self.track_expiry and not self.track_batch:
            errors["track_expiry"] = "An item with an expiry date must also keep batches."
        if self.category_id:
            category = ItemCategory.all_objects.filter(pk=self.category_id).first()
            if category is not None and category.kind != self.kind:
                errors["category"] = "This category is for another item type."
        for low, high, field in (("min_stock", "max_stock", "max_stock"),
                                 ("reorder_point", "max_stock", "reorder_point")):
            a, b = getattr(self, low), getattr(self, high)
            if a is not None and b is not None and a > b:
                errors[field] = f"{self._meta.get_field(low).verbose_name.capitalize()} is above the maximum."
        if errors:
            raise ValidationError(errors)


class ItemUom(TenantOwned, ActorTracked):
    """A pack an item comes in: 1 CARTON = 24 PCS, 1 BAG = 50 KG."""

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="pack_units")
    uom = models.ForeignKey(Uom, on_delete=models.PROTECT, related_name="+")
    factor = models.DecimalField(
        "Base units in one pack", max_digits=18, decimal_places=6,
        validators=[MinValueValidator(Decimal("0.000001"))],
    )
    barcode = models.CharField(max_length=64, blank=True)

    class Meta:
        db_table = "inventory_item_uom"
        ordering = ("item", "uom")
        constraints = [
            models.UniqueConstraint(fields=["company", "item", "uom"], name="uniq_item_uom"),
            models.UniqueConstraint(fields=["company", "barcode"], condition=~Q(barcode=""),
                                    name="uniq_item_uom_barcode_per_company"),
        ]

    def __str__(self):
        return f"1 {self.uom_id and self.uom.code} = {self.factor.normalize()} {self.item.base_uom.code}"

    def clean(self):
        super().clean()
        if self.item_id and self.uom_id:
            if Item.all_objects.filter(pk=self.item_id, base_uom_id=self.uom_id).exists():
                raise ValidationError({"uom": "That is the item's base unit already."})


class Batch(TenantOwned, ActorTracked):
    """A lot of one item: made by a GRN, a production run or the opening stock."""

    class Status(models.TextChoices):
        QUARANTINE = "quarantine", "Quarantine"
        RELEASED = "released", "Released"
        BLOCKED = "blocked", "Blocked"
        EXPIRED = "expired", "Expired"

    class Source(models.TextChoices):
        OPENING = "opening", "Opening stock"
        GRN = "grn", "Goods receipt"
        PRODUCTION = "production", "Production"
        ADJUSTMENT = "adjustment", "Adjustment"

    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="batches")
    batch_no = models.CharField(max_length=64)
    supplier_batch_no = models.CharField(max_length=64, blank=True)
    mfg_date = models.DateField("Manufacture date", null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RELEASED)
    source = models.CharField(max_length=16, choices=Source.choices)
    unit_cost = models.DecimalField(**MONEY, null=True, blank=True)

    class Meta:
        db_table = "inventory_batch"
        ordering = ("item", "expiry_date", "batch_no")
        verbose_name_plural = "batches"
        constraints = [
            models.UniqueConstraint(fields=["company", "item", "batch_no"], name="uniq_batch_no_per_item"),
        ]
        indexes = [models.Index(fields=["company", "expiry_date"], name="inv_batch_expiry")]

    def __str__(self):
        return self.batch_no

    def clean(self):
        super().clean()
        if self.mfg_date and self.expiry_date and self.expiry_date < self.mfg_date:
            raise ValidationError({"expiry_date": "The expiry date is before the manufacture date."})

    def usable_on(self, day):
        """May this batch be issued, used or sold on ``day``?"""
        if self.status != self.Status.RELEASED:
            return False
        return self.expiry_date is None or self.expiry_date >= day


class BarcodeLabel(TenantOwned, ActorTracked):
    """A label the software printed (D3). Its code resolves to exactly one record.

    - **item**: the item's own barcode (EAN-13) or its code - a shelf card or
      a loose item. One per item.
    - **batch**: one batch of an item, no quantity - the batch's card.
    - **package**: one bag, drum, carton or roll: item + batch + quantity
      (+ store/bin where it was put). Scanning it fills all of them at once.
    - **location**: a bin. One active label per bin.

    Generated codes are ``P<company id>-000001`` (package), ``B<id>-...``
    (batch) and ``L<id>-...`` (bin): letters, digits and a dash only, so
    every keyboard-wedge scanner types them right, short enough for a 40 mm
    label, and the company number stops one company's label resolving in
    another's software.
    """

    class Kind(models.TextChoices):
        ITEM = "item", "Item"
        BATCH = "batch", "Batch"
        PACKAGE = "package", "Package"
        LOCATION = "location", "Bin"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        CONSUMED = "consumed", "Used up"
        VOID = "void", "Void"

    code = models.CharField(max_length=64)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    item = models.ForeignKey(Item, null=True, blank=True, on_delete=models.PROTECT, related_name="labels")
    batch = models.ForeignKey(Batch, null=True, blank=True, on_delete=models.PROTECT, related_name="labels")
    uom = models.ForeignKey(Uom, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(**QTY, null=True, blank=True,
                                   validators=[MinValueValidator(Decimal("0.0001"))])
    warehouse = models.ForeignKey(Warehouse, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.PROTECT, related_name="labels")
    # Where it was made, for the register: e.g. opening_stock_line 12.
    source_type = models.CharField(max_length=32, blank=True)
    source_id = models.PositiveBigIntegerField(null=True, blank=True)
    printed_count = models.PositiveIntegerField(default=0)
    last_printed_at = models.DateTimeField(null=True, blank=True)
    last_printed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    void_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "inventory_barcode_label"
        ordering = ("-pk",)
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="uniq_label_code_per_company"),
            models.UniqueConstraint(
                fields=["company", "location"],
                condition=Q(kind="location", status="active"),
                name="uniq_active_label_per_location",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "item"], name="inv_label_item"),
            models.Index(fields=["company", "batch"], name="inv_label_batch"),
        ]

    def __str__(self):
        return self.code

    def clean(self):
        super().clean()
        need = {
            self.Kind.ITEM: ("item",),
            self.Kind.BATCH: ("item", "batch"),
            self.Kind.PACKAGE: ("item", "uom", "quantity"),
            self.Kind.LOCATION: ("warehouse", "location"),
        }.get(self.kind, ())
        missing = [field for field in need if getattr(self, f"{field}_id", getattr(self, field, None)) in (None, "")]
        if missing:
            raise ValidationError({field: "Required for this kind of label." for field in missing})
        if self.batch_id and self.item_id and Batch.all_objects.filter(pk=self.batch_id).exclude(
                item_id=self.item_id).exists():
            raise ValidationError({"batch": "This batch is of another item."})
        if self.kind == self.Kind.PACKAGE and self.item_id and not self.batch_id and Item.all_objects.filter(
                pk=self.item_id, track_batch=True).exists():
            raise ValidationError({"batch": "This item keeps batches: a package label names its batch."})
        if self.location_id and self.warehouse_id and Location.all_objects.filter(
                pk=self.location_id).exclude(warehouse_id=self.warehouse_id).exists():
            raise ValidationError({"location": "This bin is in another store."})


class LabelPrint(TenantOwned):
    """Every time a label was printed: who, when, how many, and why for a reprint."""

    label = models.ForeignKey(BarcodeLabel, on_delete=models.CASCADE, related_name="prints")
    printed_at = models.DateTimeField()
    printed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    copies = models.PositiveSmallIntegerField(default=1)
    reprint = models.BooleanField(default=False)
    reason = models.CharField(max_length=255, blank=True)
    size = models.CharField(max_length=8)

    class Meta:
        db_table = "inventory_label_print"
        ordering = ("-printed_at", "-pk")

    def __str__(self):
        return f"{self.label_id} x{self.copies}"


class MoveType(models.TextChoices):
    OPENING = "opening", "Opening stock"
    RECEIPT = "receipt", "Goods receipt"
    PURCHASE_RETURN = "purchase_return", "Purchase return"
    ISSUE = "issue", "Store issue"
    ISSUE_RETURN = "issue_return", "Material return"
    TRANSFER_OUT = "transfer_out", "Transfer out"
    TRANSFER_IN = "transfer_in", "Transfer in"
    ADJUSTMENT = "adjustment", "Adjustment"
    COUNT = "count", "Stock count"
    PRODUCTION_CONSUME = "production_consume", "Production consumption"
    PRODUCTION_OUTPUT = "production_output", "Production output"
    DELIVERY = "delivery", "Delivery"
    SALES_RETURN = "sales_return", "Sales return"
    REVERSAL = "reversal", "Reversal"


class ImmutableQuerySet(models.QuerySet):
    """The ledger cannot be changed in bulk either."""

    def update(self, **kwargs):
        raise ValidationError("Stock moves are never changed; post a reversal instead.")

    def delete(self):
        raise ValidationError("Stock moves are never deleted; post a reversal instead.")


class StockMove(TenantOwned):
    """One line of the stock ledger. Written only by ``inventory.posting``."""

    posted_at = models.DateTimeField()
    work_date = models.DateField()
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="moves")
    batch = models.ForeignKey(Batch, null=True, blank=True, on_delete=models.PROTECT, related_name="moves")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="moves")
    location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.PROTECT, related_name="moves")
    qty = models.DecimalField(**QTY)
    unit_cost = models.DecimalField(**MONEY)
    value = models.DecimalField(**MONEY)
    move_type = models.CharField(max_length=24, choices=MoveType.choices)
    document_type = models.CharField(max_length=32)
    document_id = models.PositiveBigIntegerField()
    document_line_id = models.PositiveBigIntegerField(null=True, blank=True)
    reversed_move = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversal"
    )
    note = models.CharField(max_length=255, blank=True)
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    objects = TenantManager.from_queryset(ImmutableQuerySet)()
    all_objects = models.Manager.from_queryset(ImmutableQuerySet)()

    class Meta:
        db_table = "inventory_stock_move"
        ordering = ("posted_at", "pk")
        base_manager_name = "all_objects"
        constraints = [
            models.CheckConstraint(condition=~Q(qty=0), name="stock_move_qty_not_zero"),
        ]
        indexes = [
            models.Index(fields=["company", "item", "warehouse"], name="inv_move_item_wh"),
            models.Index(fields=["company", "batch"], name="inv_move_batch"),
            models.Index(fields=["company", "document_type", "document_id"], name="inv_move_document"),
            models.Index(fields=["company", "posted_at"], name="inv_move_posted"),
            models.Index(fields=["company", "work_date"], name="inv_move_work_date"),
        ]

    def __str__(self):
        return f"{self.get_move_type_display()} {self.qty} of item {self.item_id}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Stock moves are never changed; post a reversal instead.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Stock moves are never deleted; post a reversal instead.")


def _balance_key_constraint(name, *fields):
    """Unique over the key with an empty batch/bin counted as one value.

    A plain unique constraint lets many rows with NULL batch through
    (NULL never equals NULL); coalescing to 0 closes that on any PostgreSQL.
    """
    expressions = []
    for field in fields:
        if field in ("batch", "location", "warehouse_scope"):
            column = "warehouse" if field == "warehouse_scope" else field
            expressions.append(Coalesce(column, Value(0)))
        else:
            expressions.append(models.F(field))
    return models.UniqueConstraint(*expressions, name=name)


class StockBalance(TenantOwned):
    """Stock on hand per item x batch x store x bin. Written only by the posting service."""

    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="balances")
    batch = models.ForeignKey(Batch, null=True, blank=True, on_delete=models.PROTECT, related_name="balances")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="balances")
    location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.PROTECT, related_name="balances")
    qty_on_hand = models.DecimalField(**QTY, default=ZERO)
    qty_reserved = models.DecimalField(**QTY, default=ZERO)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "inventory_stock_balance"
        constraints = [
            _balance_key_constraint("uniq_stock_balance_key", "company", "item", "batch", "warehouse", "location"),
            models.CheckConstraint(condition=Q(qty_on_hand__gte=0), name="stock_balance_not_negative"),
            models.CheckConstraint(condition=Q(qty_reserved__gte=0), name="stock_balance_reserved_not_negative"),
        ]
        indexes = [models.Index(fields=["company", "warehouse", "item"], name="inv_balance_wh_item")]

    def __str__(self):
        return f"{self.qty_on_hand} of item {self.item_id} in {self.warehouse_id}"

    @property
    def qty_available(self):
        return self.qty_on_hand - self.qty_reserved


class ItemCost(TenantOwned):
    """The moving weighted average cost of an item (per store when the
    company values per store; ``warehouse`` empty otherwise)."""

    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="costs")
    warehouse = models.ForeignKey(Warehouse, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    avg_cost = models.DecimalField(**MONEY, default=ZERO)
    qty_basis = models.DecimalField(**QTY, default=ZERO)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "inventory_item_cost"
        constraints = [
            _balance_key_constraint("uniq_item_cost_key", "company", "item", "warehouse_scope"),
        ]

    def __str__(self):
        return f"{self.avg_cost} for item {self.item_id}"


class DocumentSequence(TenantOwned):
    """The next number of a document type in a year: OS-2026-00001."""

    doc_type = models.CharField(max_length=32)
    year = models.PositiveSmallIntegerField()
    prefix = models.CharField(max_length=8)
    next_number = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "inventory_document_sequence"
        constraints = [
            models.UniqueConstraint(fields=["company", "doc_type", "year"], name="uniq_document_sequence"),
        ]

    def __str__(self):
        return f"{self.prefix}-{self.year} next {self.next_number}"


class PeriodLock(TenantOwned):
    """A closed month: postings dated in it are refused (D9). Closed in Phase 7."""

    month = models.DateField(help_text="The first day of the closed month.")
    locked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reason = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "inventory_period_lock"
        ordering = ("-month",)
        constraints = [
            models.UniqueConstraint(fields=["company", "month"], name="uniq_period_lock_month"),
            models.CheckConstraint(condition=Q(month__day=1), name="period_lock_first_of_month"),
        ]

    def __str__(self):
        return self.month.strftime("%B %Y")


class DocumentStatus(models.TextChoices):
    """One life cycle for every document (D6). Stock moves only on Posted."""

    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"
    APPROVED = "approved", "Approved"
    POSTED = "posted", "Posted"
    REVERSED = "reversed", "Reversed"
    CANCELLED = "cancelled", "Cancelled"


class OpeningStock(TenantOwned, ActorTracked):
    """The stock counted on the opening day, entered or imported, then posted."""

    DOC_TYPE = "opening_stock"
    PREFIX = "OS"

    number = models.CharField(max_length=32)
    work_date = models.DateField(help_text="The opening day: the moves are dated this day.")
    status = models.CharField(max_length=16, choices=DocumentStatus.choices, default=DocumentStatus.DRAFT)
    note = models.CharField(max_length=255, blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reverse_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "inventory_opening_stock"
        ordering = ("-work_date", "-pk")
        constraints = [
            models.UniqueConstraint(fields=["company", "number"], name="uniq_opening_stock_number"),
        ]

    def __str__(self):
        return self.number

    @property
    def editable(self):
        return self.status == DocumentStatus.DRAFT


class OpeningStockLine(TenantOwned):
    """One counted quantity: store + bin + item + batch."""

    document = models.ForeignKey(OpeningStock, on_delete=models.CASCADE, related_name="lines")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="+")
    location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    batch_no = models.CharField(max_length=64, blank=True)
    supplier_batch_no = models.CharField(max_length=64, blank=True)
    mfg_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    qty = models.DecimalField("Quantity", **QTY, validators=[MinValueValidator(Decimal("0.0001"))])
    uom = models.ForeignKey(Uom, on_delete=models.PROTECT, related_name="+")
    factor = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("1"))
    unit_cost = models.DecimalField("Unit cost", **MONEY, validators=[MinValueValidator(ZERO)],
                                    help_text="Cost of one unit written in Unit.")
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "inventory_opening_stock_line"
        ordering = ("document", "pk")

    def __str__(self):
        return f"{self.qty} {self.uom_id and self.uom.code} of {self.item_id and self.item.code}"

    @property
    def qty_base(self):
        return (self.qty * self.factor).quantize(Decimal("0.0001"))

    @property
    def unit_cost_base(self):
        return (self.unit_cost / self.factor).quantize(Decimal("0.0001"))

    @property
    def value(self):
        return (self.qty * self.unit_cost).quantize(Decimal("0.0001"))


# Phase 4 store documents live in their own module to keep this one readable.
from inventory.models_store import *  # noqa: E402,F401,F403
