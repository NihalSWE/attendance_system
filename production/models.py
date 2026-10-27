"""Production and BOM (docs/inventory/00-PLAN.md, Phase 5).

- **BOM** (bill of materials): what one batch of a finished product needs,
  in versions; one active version per product.
- **Production order**: make N of a product with an active BOM version.
  Planned -> Released -> (materials consumed) -> Completed (output posted)
  -> Closed; Cancelled while nothing was consumed.
- **Material consumption**: materials taken from a store into the order
  (first expiry first, or by scanning labels), and unused material returned.
- **Output**: posted once, when the run is complete: the good quantity goes
  into the finished-goods store as a new batch (expiry from shelf life) with
  its labels, at **unit cost = materials consumed ÷ good quantity**. Rejects
  are recorded as wastage; they absorb no stock.

Every write is in ``production.services``; stock moves only through
``inventory.posting``.
"""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from common.models import ActorTracked, TenantOwned

QTY = {"max_digits": 18, "decimal_places": 4}
FOUR = Decimal("0.0001")
ZERO = Decimal("0")


def _user():
    return models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name="+")


def _factor():
    return models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("1"))


class Bom(TenantOwned, ActorTracked):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    item = models.ForeignKey("inventory.Item", on_delete=models.PROTECT, related_name="boms")
    version = models.PositiveIntegerField()
    batch_size = models.DecimalField("Batch size", **QTY, validators=[MinValueValidator(FOUR)],
                                     help_text="How much one batch of this recipe makes.")
    batch_uom = models.ForeignKey("inventory.Uom", on_delete=models.PROTECT, related_name="+",
                                  verbose_name="Batch unit")
    batch_factor = _factor()
    effective_from = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    activated_at = models.DateTimeField(null=True, blank=True)
    activated_by = _user()

    class Meta:
        db_table = "production_bom"
        ordering = ("item", "-version")
        verbose_name = "BOM"
        constraints = [
            models.UniqueConstraint(fields=["company", "item", "version"], name="uniq_bom_version"),
            models.UniqueConstraint(fields=["company", "item"], condition=Q(status="active"),
                                    name="uniq_active_bom_per_item"),
        ]

    def __str__(self):
        return f"{self.item_id and self.item.code} v{self.version}"

    @property
    def batch_size_base(self):
        return (self.batch_size * self.batch_factor).quantize(FOUR)


class BomLine(TenantOwned):
    bom = models.ForeignKey(Bom, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey("inventory.Item", on_delete=models.PROTECT, related_name="+",
                             verbose_name="Material")
    qty = models.DecimalField("Quantity per batch", **QTY, validators=[MinValueValidator(FOUR)])
    uom = models.ForeignKey("inventory.Uom", on_delete=models.PROTECT, related_name="+")
    factor = _factor()
    scrap_pct = models.DecimalField("Scrap %", max_digits=5, decimal_places=2, default=ZERO,
                                    validators=[MinValueValidator(ZERO), MaxValueValidator(Decimal("100"))],
                                    help_text="Normal wastage of this material, added to what is planned.")
    is_packaging = models.BooleanField("Packaging", default=False)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "production_bom_line"
        ordering = ("bom", "is_packaging", "pk")

    def __str__(self):
        return f"{self.qty} {self.uom_id and self.uom.code} {self.item_id and self.item.code}"

    @property
    def qty_base(self):
        return (self.qty * self.factor).quantize(FOUR)

    def planned_base(self, output_base, batch_size_base):
        """Base units of this material for ``output_base`` of the product, scrap included."""
        need = self.qty_base * output_base / batch_size_base * (1 + self.scrap_pct / 100)
        return need.quantize(FOUR)


class ProductionOrder(TenantOwned, ActorTracked):
    DOC_TYPE = "production_order"
    PREFIX = "PRD"

    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        # Plain words (2026-10-27, plan §8.6): Plan → Start → Finish.
        RELEASED = "released", "Started"
        COMPLETED = "completed", "Finished"
        CLOSED = "closed", "Closed"
        CANCELLED = "cancelled", "Cancelled"
        FAILED = "failed", "Failed"
        REVERSED = "reversed", "Reversed"

    number = models.CharField(max_length=32)
    item = models.ForeignKey("inventory.Item", on_delete=models.PROTECT, related_name="+",
                             verbose_name="Product")
    bom = models.ForeignKey(Bom, on_delete=models.PROTECT, related_name="orders", verbose_name="BOM version")
    planned_qty = models.DecimalField("Planned quantity", **QTY, validators=[MinValueValidator(FOUR)])
    uom = models.ForeignKey("inventory.Uom", on_delete=models.PROTECT, related_name="+")
    factor = _factor()
    planned_date = models.DateField()
    material_warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, related_name="+",
                                           verbose_name="Materials from store")
    output_warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, related_name="+",
                                         verbose_name="Finished goods into store")
    line_name = models.CharField("Production line", max_length=64, blank=True)
    shift_name = models.CharField("Shift", max_length=64, blank=True)
    note = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PLANNED)
    released_at = models.DateTimeField(null=True, blank=True)
    released_by = _user()
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = _user()
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = _user()
    # Filled when the output is posted.
    material_cost = models.DecimalField(**QTY, null=True, blank=True)
    good_qty_base = models.DecimalField(**QTY, null=True, blank=True)
    rejected_qty_base = models.DecimalField(**QTY, null=True, blank=True)
    unit_cost = models.DecimalField(**QTY, null=True, blank=True)
    # Full cost (2026-10-27, plan §8.5): what one unit was expected to cost at
    # Start, the other costs of the day (people, machines...) at Finish, and why
    # the result was far from the plan, failed or was reversed.
    expected_unit_cost = models.DecimalField(**QTY, null=True, blank=True)
    other_cost = models.DecimalField(**QTY, null=True, blank=True)
    reason = models.CharField(max_length=500, blank=True)
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = _user()

    class Meta:
        db_table = "production_order"
        ordering = ("-planned_date", "-pk")
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="uniq_production_order_number")]

    def __str__(self):
        return self.number

    @property
    def planned_base(self):
        return (self.planned_qty * self.factor).quantize(FOUR)

    @property
    def yield_pct(self):
        if not self.good_qty_base or not self.planned_base:
            return None
        return (self.good_qty_base / self.planned_base * 100).quantize(Decimal("0.1"))


class Consumption(TenantOwned, ActorTracked):
    """Materials into a production order (or unused material back out of it)."""

    DOC_TYPE = "production_consumption"
    PREFIX = "PC"

    class Kind(models.TextChoices):
        ISSUE = "issue", "Materials used"
        RETURN = "return", "Unused material returned"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"
        CANCELLED = "cancelled", "Cancelled"

    number = models.CharField(max_length=32)
    order = models.ForeignKey(ProductionOrder, on_delete=models.PROTECT, related_name="consumptions")
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.ISSUE)
    work_date = models.DateField()
    note = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = _user()
    # The store it went out of (or back into); empty: the order's materials store.
    warehouse = models.ForeignKey("inventory.Warehouse", null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="+")

    class Meta:
        db_table = "production_consumption"
        ordering = ("order", "pk")
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="uniq_consumption_number")]

    def __str__(self):
        return self.number


class ConsumptionLine(TenantOwned):
    consumption = models.ForeignKey(Consumption, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey("inventory.Item", on_delete=models.PROTECT, related_name="+")
    batch = models.ForeignKey("inventory.Batch", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    location = models.ForeignKey("inventory.Location", null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="+")
    label = models.ForeignKey("inventory.BarcodeLabel", null=True, blank=True, on_delete=models.PROTECT,
                              related_name="+")
    qty = models.DecimalField("Quantity", **QTY, validators=[MinValueValidator(FOUR)])
    uom = models.ForeignKey("inventory.Uom", on_delete=models.PROTECT, related_name="+")
    factor = _factor()
    not_fefo_reason = models.CharField("Why not the first to expire", max_length=255, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "production_consumption_line"
        ordering = ("consumption", "pk")

    @property
    def qty_base(self):
        return (self.qty * self.factor).quantize(FOUR)


class Output(TenantOwned, ActorTracked):
    """What one production order made: posted once, when the run is complete."""

    DOC_TYPE = "production_output"
    PREFIX = "OUT"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"

    number = models.CharField(max_length=32)
    order = models.OneToOneField(ProductionOrder, on_delete=models.PROTECT, related_name="output")
    output_date = models.DateField()
    qty_good = models.DecimalField("Good", **QTY, validators=[MinValueValidator(FOUR)])
    qty_rejected = models.DecimalField("Rejected / wastage", **QTY, default=ZERO,
                                       validators=[MinValueValidator(ZERO)])
    uom = models.ForeignKey("inventory.Uom", on_delete=models.PROTECT, related_name="+")
    factor = _factor()
    batch_no = models.CharField("Batch no.", max_length=64, blank=True,
                                help_text="Left empty, one is made from the product code, date and line.")
    mfg_date = models.DateField("Manufacture date")
    expiry_date = models.DateField(null=True, blank=True,
                                   help_text="Left empty, worked out from the product's shelf life.")
    location = models.ForeignKey("inventory.Location", null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="+")
    packages = models.PositiveIntegerField("Packages (labels)", null=True, blank=True,
                                           help_text="Cartons or cases: one label each, made when posted.")
    note = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    batch = models.ForeignKey("inventory.Batch", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = _user()

    class Meta:
        db_table = "production_output"
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="uniq_output_number")]

    def __str__(self):
        return self.number

    @property
    def good_base(self):
        return (self.qty_good * self.factor).quantize(FOUR)

    @property
    def rejected_base(self):
        return (self.qty_rejected * self.factor).quantize(FOUR)


# --- Cost of making a product (2026-10-27, docs/inventory/PRODUCTION_PLAN.md §7.4) ---

MONEY = {"max_digits": 18, "decimal_places": 2}


class CostRate(TenantOwned, ActorTracked):
    """What an hour costs: Line worker 75, Supervisor 150, Oven 300. Used by
    the products' cost sheets; a change counts for productions after it."""

    class Kind(models.TextChoices):
        LABOUR = "labour", "People"
        MACHINE = "machine", "Machine"

    name = models.CharField(max_length=64)
    kind = models.CharField("For", max_length=16, choices=Kind.choices)
    rate = models.DecimalField("Tk per hour", **MONEY, validators=[MinValueValidator(ZERO)])
    status = models.CharField(max_length=16, default="active",
                              choices=(("active", "Active"), ("inactive", "Inactive")))

    class Meta:
        db_table = "production_cost_rate"
        ordering = ("kind", "name")
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="uniq_cost_rate_name")]

    def __str__(self):
        return self.name


class CostLine(TenantOwned, ActorTracked):
    """One cost of making one batch of a product, besides its materials."""

    class How(models.TextChoices):
        LABOUR = "labour", "People × hours × rate"
        MACHINE = "machine", "Machine hours × rate"
        FIXED = "fixed", "An amount per batch"
        PERCENT = "percent", "A % of the materials"

    item = models.ForeignKey("inventory.Item", on_delete=models.CASCADE, related_name="cost_lines",
                             verbose_name="Product")
    how = models.CharField("How it is worked out", max_length=16, choices=How.choices)
    label = models.CharField("Name", max_length=64, blank=True)
    rate = models.ForeignKey(CostRate, null=True, blank=True, on_delete=models.PROTECT,
                             related_name="lines")
    people = models.DecimalField(**QTY, null=True, blank=True, validators=[MinValueValidator(FOUR)])
    hours = models.DecimalField("Hours per batch", **QTY, null=True, blank=True,
                                validators=[MinValueValidator(FOUR)])
    amount = models.DecimalField("Tk per batch", **MONEY, null=True, blank=True,
                                 validators=[MinValueValidator(ZERO)])
    percent = models.DecimalField("% of materials", max_digits=6, decimal_places=2, null=True, blank=True,
                                  validators=[MinValueValidator(ZERO), MaxValueValidator(Decimal("1000"))])

    class Meta:
        db_table = "production_cost_line"
        ordering = ("item", "pk")

    def __str__(self):
        return self.name

    @property
    def name(self):
        if self.label:
            return self.label
        if self.rate_id:
            return self.rate.name
        return self.get_how_display()

    def clean(self):
        from django.core.exceptions import ValidationError

        needs = {self.How.LABOUR: ("rate", "people", "hours"), self.How.MACHINE: ("rate", "hours"),
                 self.How.FIXED: ("amount",), self.How.PERCENT: ("percent",)}.get(self.how, ())
        errors = {name: "Fill this in." for name in needs if getattr(self, name) in (None, "")}
        if self.rate_id and self.how in (self.How.LABOUR, self.How.MACHINE) and self.rate.kind != self.how:
            errors["rate"] = f"Choose a {'people' if self.how == self.How.LABOUR else 'machine'} rate."
        if errors:
            raise ValidationError(errors)
        # Only what this way of working out uses is kept.
        for name in ("rate", "people", "hours", "amount", "percent"):
            if name not in needs:
                setattr(self, name, None)


# --- Start and Finish (2026-10-27, plan §8.3-8.6, §15 phase 7) -----------------

class OrderCost(TenantOwned):
    """A cost of the day of one production besides its materials: people,
    machines, gas... filled from the product's cost sheet at Finish."""

    order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name="costs")
    name = models.CharField(max_length=64)
    amount = models.DecimalField(**MONEY, validators=[MinValueValidator(ZERO)])

    class Meta:
        db_table = "production_order_cost"
        ordering = ("order", "pk")


class OutputPart(TenantOwned):
    """How much of the output went into which store (a split: Main 30 + Depot 18)."""

    output = models.ForeignKey(Output, on_delete=models.CASCADE, related_name="parts")
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, related_name="+")
    qty_base = models.DecimalField(**QTY, validators=[MinValueValidator(FOUR)])

    class Meta:
        db_table = "production_output_part"
        ordering = ("output", "pk")


class Leftover(TenantOwned):
    """What was left of a material at Finish: returned to its store, or wasted (why)."""

    order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name="leftovers")
    item = models.ForeignKey("inventory.Item", on_delete=models.PROTECT, related_name="+")
    returned = models.DecimalField(**QTY, default=ZERO, validators=[MinValueValidator(ZERO)])
    wasted = models.DecimalField(**QTY, default=ZERO, validators=[MinValueValidator(ZERO)])
    reason = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "production_leftover"
        ordering = ("order", "pk")
