"""Purchasing (docs/inventory/00-PLAN.md): suppliers in Phase 1; purchase
requisitions, orders, goods receipts and quality checks arrive in Phase 3."""

from django.core.validators import MaxValueValidator
from django.db import models

from common.choices import ActiveStatus
from common.models import ActorTracked, TenantOwned


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
