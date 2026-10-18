"""Sales (docs/inventory/00-PLAN.md): customers (Phase 1); price lists,
sales orders with reservation, delivery challans, invoices and sales
returns (Phase 6). Writes are in ``sales.orders`` / ``sales.deliveries``;
stock moves and reservations only through ``inventory.posting``."""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from common.choices import ActiveStatus
from common.models import ActorTracked, TenantOwned

QTY = {"max_digits": 18, "decimal_places": 4}
FOUR = Decimal("0.0001")
CENT = Decimal("0.01")
ZERO = Decimal("0")


class Customer(TenantOwned, ActorTracked):
    class Type(models.TextChoices):
        DISTRIBUTOR = "distributor", "Distributor"
        DEALER = "dealer", "Dealer"
        RETAILER = "retailer", "Retailer"
        CORPORATE = "corporate", "Corporate"

    code = models.CharField(max_length=32)
    name = models.CharField(max_length=255)
    customer_type = models.CharField(max_length=16, choices=Type.choices)
    contact_person = models.CharField(max_length=128, blank=True)
    phone = models.CharField(max_length=32, blank=True)
    # The AI assistant (Phase 8) recognises who sent an order by these two.
    whatsapp = models.CharField("WhatsApp number", max_length=32, blank=True)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=500, blank=True)
    territory = models.CharField(max_length=64, blank=True)
    credit_limit = models.DecimalField(
        "Credit limit", max_digits=18, decimal_places=4, default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))], help_text="0 = cash only.",
    )
    payment_terms_days = models.PositiveSmallIntegerField(
        "Payment terms (days)", default=0, validators=[MaxValueValidator(365)],
    )
    price_list_name = models.CharField("Price list", max_length=64, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=16, choices=ActiveStatus.choices, default=ActiveStatus.ACTIVE)

    class Meta:
        db_table = "sales_customer"
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="uniq_customer_code_per_company"),
        ]
        indexes = [
            models.Index(fields=["company", "email"], name="sales_customer_email"),
            models.Index(fields=["company", "whatsapp"], name="sales_customer_whatsapp"),
        ]

    def __str__(self):
        return f"{self.code} · {self.name}"


# --------------------------------------------------------------------------
# Phase 6
# --------------------------------------------------------------------------

def _user():
    return models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name="+")


def _factor():
    return models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("1"))


def _pct(label):
    return models.DecimalField(label, max_digits=5, decimal_places=2, default=ZERO,
                               validators=[MinValueValidator(ZERO), MaxValueValidator(100)])


class PriceList(TenantOwned, ActorTracked):
    """Prices by customer group. A customer uses the list named on their record."""

    name = models.CharField(max_length=64, help_text="As written on customers, e.g. Distributor.")
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=16, choices=ActiveStatus.choices, default=ActiveStatus.ACTIVE)

    class Meta:
        db_table = "sales_price_list"
        ordering = ("name",)
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="uniq_price_list_name")]

    def __str__(self):
        return self.name


class PriceListLine(TenantOwned):
    price_list = models.ForeignKey(PriceList, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey("inventory.Item", on_delete=models.PROTECT, related_name="+")
    uom = models.ForeignKey("inventory.Uom", on_delete=models.PROTECT, related_name="+")
    price = models.DecimalField("Price", **QTY, validators=[MinValueValidator(ZERO)],
                                help_text="For one unit written in Unit, before VAT.")
    min_qty = models.DecimalField("From quantity", **QTY, default=ZERO, validators=[MinValueValidator(ZERO)])

    class Meta:
        db_table = "sales_price_list_line"
        ordering = ("price_list", "item", "min_qty")
        constraints = [models.UniqueConstraint(fields=["company", "price_list", "item", "uom", "min_qty"],
                                               name="uniq_price_list_line")]


class SalesOrder(TenantOwned, ActorTracked):
    DOC_TYPE = "sales_order"
    PREFIX = "SO"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        CONFIRMED = "confirmed", "Confirmed"
        PARTLY_DELIVERED = "partly_delivered", "Partly delivered"
        DELIVERED = "delivered", "Delivered"
        CLOSED = "closed", "Closed"
        CANCELLED = "cancelled", "Cancelled"

    class Source(models.TextChoices):
        MANUAL = "manual", "Entered"
        EMAIL = "email", "Email"
        WHATSAPP = "whatsapp", "WhatsApp"
        API = "api", "API"
        AI = "ai", "AI draft"

    number = models.CharField(max_length=32)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="orders")
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, related_name="+",
                                  verbose_name="Deliver from store",
                                  help_text="The order's store: its branch, and the store a line uses "
                                            "unless the line names another.")
    order_date = models.DateField()
    delivery_date = models.DateField("Deliver by", null=True, blank=True)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.MANUAL)
    customer_ref = models.CharField("Customer order no.", max_length=64, blank=True)
    delivery_address = models.CharField(max_length=500, blank=True)
    note = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = _user()
    credit_note = models.CharField("Why over the credit limit", max_length=255, blank=True)
    closed_note = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "sales_order"
        ordering = ("-order_date", "-pk")
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="uniq_sales_order_number")]

    def __str__(self):
        return self.number

    @property
    def deliverable(self):
        return self.status in (self.Status.CONFIRMED, self.Status.PARTLY_DELIVERED)


class SalesOrderLine(TenantOwned):
    order = models.ForeignKey(SalesOrder, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey("inventory.Item", on_delete=models.PROTECT, related_name="+")
    # Phase 8: each line goes from its own store - juice from finished goods,
    # sugar from the raw material store - on one order.
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, related_name="+",
                                  verbose_name="From store")
    qty = models.DecimalField("Quantity", **QTY, validators=[MinValueValidator(FOUR)])
    uom = models.ForeignKey("inventory.Uom", on_delete=models.PROTECT, related_name="+")
    factor = _factor()
    unit_price = models.DecimalField("Unit price", **QTY, validators=[MinValueValidator(ZERO)])
    discount_pct = _pct("Discount %")
    vat_rate = _pct("VAT %")
    note = models.CharField(max_length=255, blank=True)
    qty_reserved_base = models.DecimalField(**QTY, default=ZERO)
    qty_delivered_base = models.DecimalField(**QTY, default=ZERO)

    class Meta:
        db_table = "sales_order_line"
        ordering = ("order", "pk")

    @property
    def qty_base(self):
        return (self.qty * self.factor).quantize(FOUR)

    @property
    def qty_open_base(self):
        return max(self.qty_base - self.qty_delivered_base, ZERO)

    @property
    def net_price(self):
        return (self.unit_price * (100 - self.discount_pct) / 100).quantize(FOUR)

    @property
    def amount(self):
        return (self.qty * self.net_price).quantize(CENT)

    @property
    def vat_amount(self):
        return (self.amount * self.vat_rate / 100).quantize(CENT)


class Reservation(TenantOwned):
    """Stock held for an order line on one balance row (item x batch x store x bin)."""

    order_line = models.ForeignKey(SalesOrderLine, on_delete=models.CASCADE, related_name="reservations")
    balance = models.ForeignKey("inventory.StockBalance", on_delete=models.PROTECT, related_name="+")
    qty_base = models.DecimalField(**QTY)

    class Meta:
        db_table = "sales_reservation"
        constraints = [models.UniqueConstraint(fields=["order_line", "balance"], name="uniq_reservation_row")]


class Delivery(TenantOwned, ActorTracked):
    """A delivery challan: goods out to the customer against a confirmed order."""

    DOC_TYPE = "delivery"
    PREFIX = "DC"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Delivered"
        REVERSED = "reversed", "Reversed"
        CANCELLED = "cancelled", "Cancelled"

    number = models.CharField(max_length=32)
    order = models.ForeignKey(SalesOrder, on_delete=models.PROTECT, related_name="deliveries")
    # Phase 8: one challan per store - the goods leave from here.
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, related_name="+",
                                  verbose_name="From store")
    delivery_date = models.DateField()
    vehicle_no = models.CharField("Vehicle no.", max_length=32, blank=True)
    driver = models.CharField("Driver", max_length=128, blank=True)
    note = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = _user()
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = _user()
    reverse_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "sales_delivery"
        ordering = ("-delivery_date", "-pk")
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="uniq_delivery_number")]

    def __str__(self):
        return self.number


class DeliveryLine(TenantOwned):
    delivery = models.ForeignKey(Delivery, on_delete=models.CASCADE, related_name="lines")
    order_line = models.ForeignKey(SalesOrderLine, on_delete=models.PROTECT, related_name="delivery_lines")
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

    class Meta:
        db_table = "sales_delivery_line"
        ordering = ("delivery", "pk")

    @property
    def qty_base(self):
        return (self.qty * self.factor).quantize(FOUR)


class SalesInvoice(TenantOwned, ActorTracked):
    """The invoice of one delivery: exactly what was delivered, at the order's prices."""

    DOC_TYPE = "sales_invoice"
    PREFIX = "INV"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ISSUED = "issued", "Issued"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"

    number = models.CharField(max_length=32)
    delivery = models.ForeignKey(Delivery, on_delete=models.PROTECT, related_name="invoices")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="invoices")
    invoice_date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    amount = models.DecimalField(**QTY, default=ZERO)
    vat = models.DecimalField(**QTY, default=ZERO)
    total = models.DecimalField(**QTY, default=ZERO)
    note = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    issued_at = models.DateTimeField(null=True, blank=True)
    issued_by = _user()
    paid_on = models.DateField(null=True, blank=True)
    paid_note = models.CharField("Payment reference", max_length=255, blank=True)
    cancel_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "sales_invoice"
        ordering = ("-invoice_date", "-pk")
        constraints = [
            models.UniqueConstraint(fields=["company", "number"], name="uniq_sales_invoice_number"),
            # One live invoice per delivery; a cancelled one can be replaced.
            models.UniqueConstraint(fields=["delivery"], condition=~models.Q(status="cancelled"),
                                    name="uniq_live_invoice_per_delivery"),
        ]

    def __str__(self):
        return self.number


class SalesInvoiceLine(TenantOwned):
    invoice = models.ForeignKey(SalesInvoice, on_delete=models.CASCADE, related_name="lines")
    delivery_line = models.ForeignKey(DeliveryLine, on_delete=models.PROTECT, related_name="invoice_lines")
    item = models.ForeignKey("inventory.Item", on_delete=models.PROTECT, related_name="+")
    qty = models.DecimalField(**QTY)
    uom = models.ForeignKey("inventory.Uom", on_delete=models.PROTECT, related_name="+")
    unit_price = models.DecimalField(**QTY)
    discount_pct = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO)
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO)
    amount = models.DecimalField(**QTY)
    vat_amount = models.DecimalField(**QTY)

    class Meta:
        db_table = "sales_invoice_line"
        ordering = ("invoice", "pk")


class SalesReturn(TenantOwned, ActorTracked):
    """Goods a customer sends back: good stock back to the store, damaged to Rejected."""

    DOC_TYPE = "sales_return"
    PREFIX = "SRT"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"
        REVERSED = "reversed", "Reversed"
        CANCELLED = "cancelled", "Cancelled"

    number = models.CharField(max_length=32)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="returns")
    delivery = models.ForeignKey(Delivery, null=True, blank=True, on_delete=models.PROTECT, related_name="returns")
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, related_name="+",
                                  verbose_name="Back to store")
    return_date = models.DateField()
    reason = models.CharField(max_length=500)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = _user()
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = _user()
    reverse_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "sales_return"
        ordering = ("-return_date", "-pk")
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="uniq_sales_return_number")]

    def __str__(self):
        return self.number


class SalesReturnLine(TenantOwned):
    class Condition(models.TextChoices):
        GOOD = "good", "Good: back to stock"
        DAMAGED = "damaged", "Damaged: to the rejected store"

    sales_return = models.ForeignKey(SalesReturn, on_delete=models.CASCADE, related_name="lines")
    delivery_line = models.ForeignKey(DeliveryLine, null=True, blank=True, on_delete=models.PROTECT,
                                      related_name="return_lines")
    item = models.ForeignKey("inventory.Item", on_delete=models.PROTECT, related_name="+")
    batch = models.ForeignKey("inventory.Batch", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    condition = models.CharField(max_length=16, choices=Condition.choices, default=Condition.GOOD)
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, related_name="+")
    qty = models.DecimalField("Quantity", **QTY, validators=[MinValueValidator(FOUR)])
    uom = models.ForeignKey("inventory.Uom", on_delete=models.PROTECT, related_name="+")
    factor = _factor()
    unit_cost_base = models.DecimalField(**QTY, null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "sales_return_line"
        ordering = ("sales_return", "pk")

    @property
    def qty_base(self):
        return (self.qty * self.factor).quantize(FOUR)
