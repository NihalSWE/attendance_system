"""Sales (docs/inventory/00-PLAN.md): customers in Phase 1; price lists,
sales orders, deliveries and invoices arrive in Phase 6."""

from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from common.choices import ActiveStatus
from common.models import ActorTracked, TenantOwned


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
