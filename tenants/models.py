"""Tenant root: Company.

Company is the tenant itself, so it is NOT TenantOwned — it is the thing every
other tenant-owned row points at. See MODEL_FIELD_DICTIONARY.md §3.
"""

import uuid

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import RangeBoundary, RangeOperators
from django.db import models, transaction

from common.db import TstzRange
from common.models import ActorTracked, TenantOwned, TimeStamped


class Company(TimeStamped, ActorTracked):
    """A customer organization. The unit of tenancy for the whole platform."""

    class Status(models.TextChoices):
        TRIAL = "trial", "Trial"
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        INACTIVE = "inactive", "Inactive"

    # Stable external identifier; never expose the sequential PK publicly.
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    code = models.CharField(max_length=32, unique=True)
    slug = models.SlugField(max_length=64, unique=True)

    name = models.CharField(max_length=255)
    legal_name = models.CharField(max_length=255, blank=True)

    timezone = models.CharField(max_length=64, default="Asia/Dhaka")
    currency = models.CharField(max_length=3, default="BDT")  # ISO 4217
    language = models.CharField(max_length=16, blank=True, default="en-us")
    country_code = models.CharField(max_length=2, blank=True, default="BD")  # ISO 3166-1 alpha-2

    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    address = models.TextField(blank=True)

    registration_number = models.CharField(max_length=64, blank=True)
    tax_identifier = models.CharField(max_length=64, blank=True)

    # Private storage reference (FileField needs no Pillow, unlike ImageField).
    logo = models.FileField(upload_to="company_logos/", null=True, blank=True)

    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.TRIAL
    )
    activated_at = models.DateTimeField(null=True, blank=True)
    suspended_at = models.DateTimeField(null=True, blank=True)
    suspended_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="suspended_companies",
    )
    suspension_reason = models.TextField(blank=True)

    class Meta:
        db_table = "tenants_company"
        verbose_name_plural = "companies"

    def __str__(self):
        return self.name

    @transaction.atomic
    def save(self, *args, **kwargs):
        from common.identifiers import prepare_company_identifiers
        prepare_company_identifiers(self)
        super().save(*args, **kwargs)

    def clean(self):
        from common.forms import normalize_bd_phone
        from django.core.exceptions import ValidationError
        super().clean()
        try:
            self.phone = normalize_bd_phone(self.phone)
        except ValidationError as exc:
            raise ValidationError({"phone": exc.messages})


class Feature(TimeStamped):
    """Global catalogue of sellable/toggleable product capabilities.

    Root-admin data, shared by every tenant, so it is NOT TenantOwned. Company
    access to a feature is expressed by CompanyFeature.
    """

    code = models.CharField(max_length=64, unique=True)  # e.g. "leave", "payroll"
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    # Core features cannot be removed from an eligible package.
    is_core = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        # Client-facing table name. The model stays `Feature` because that is what
        # it is in our code; the client's vocabulary is "module". db_table is
        # cosmetic - no data, relationship or query behaviour changes.
        db_table = "module"
        ordering = ("sort_order", "code")

    def __str__(self):
        return self.name


class CompanyFeature(TenantOwned, ActorTracked):
    """A dated enable/disable of one Feature for one Company.

    Separate from the package snapshot: this is the explicit per-company grant or
    override. Effective access combines the subscription snapshot with these
    dated rows.
    """

    class Effect(models.TextChoices):
        ENABLE = "enable", "Enable"
        DISABLE = "disable", "Disable"

    feature = models.ForeignKey(
        Feature, on_delete=models.PROTECT, related_name="company_grants"
    )
    effect = models.CharField(max_length=16, choices=Effect.choices)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    # Feature-specific scalar limits (e.g. max devices), validated per feature.
    limits = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="granted_company_features",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "tenants_companyfeature"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(ends_at__isnull=True)
                | models.Q(ends_at__gt=models.F("starts_at")),
                name="companyfeature_end_after_start",
            ),
            # No two active overrides of the same effect overlapping in time.
            ExclusionConstraint(
                name="excl_companyfeature_overlap",
                expressions=[
                    ("company", RangeOperators.EQUAL),
                    ("feature", RangeOperators.EQUAL),
                    ("effect", RangeOperators.EQUAL),
                    (
                        TstzRange("starts_at", "ends_at", RangeBoundary()),
                        RangeOperators.OVERLAPS,
                    ),
                ],
                condition=models.Q(is_active=True),
            ),
        ]

    def __str__(self):
        return f"{self.feature_id} {self.effect} for company {self.company_id}"
