"""Shared abstract model bases (project-wide conventions).

These are *abstract* models: they create no database table and are not part of
the 83-model count. Concrete domain models mix them in to inherit consistent
timestamp and actor-tracking columns. ``common`` is a plain Python package, not
a Django app — abstract models do not need to belong to an installed app.

Conventions encoded here (see MODEL_FIELD_DICTIONARY.md "Conventions"):
- Operational timestamps are timezone-aware UTC (USE_TZ = True).
- Actor references use settings.AUTH_USER_MODEL and SET_NULL, so deactivating
  a user never destroys the historical record that references them.

TenantOwned (company FK + tenant scoping) is defined here now that
tenants.Company exists. Its default manager auto-filters by the active company
from common.tenant; see Lesson 4 in docs/ENGINEERING_LEARNING_LOG.md.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from common.tenant import get_current_company_id


class TimeStamped(models.Model):
    """Adds created/updated timestamps to a model."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class ActorTracked(models.Model):
    """Tracks which user created/last-updated a row.

    Both FKs are nullable and SET_NULL: system/automated actions may leave them
    null, and deactivating a user must not cascade-delete business history.
    """

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        abstract = True


class TenantManager(models.Manager):
    """Default manager for TenantOwned models: auto-scopes to the current company.

    Reads the active company from common.tenant. If none is set it **fails loud**
    (RuntimeError) instead of silently returning cross-tenant rows — this catches
    "forgot to set the tenant context" bugs at the point of the query. Platform /
    root code that must span tenants uses the unscoped ``all_objects`` manager, or
    wraps the query in ``common.tenant.use_company(...)``.
    """

    def get_queryset(self):
        company_id = get_current_company_id()
        if company_id is None:
            raise RuntimeError(
                "No active tenant context for a tenant-scoped query. Wrap it in "
                "common.tenant.use_company(company), or use `all_objects` for "
                "explicit platform-level access."
            )
        return super().get_queryset().filter(company_id=company_id)


class TenantOwned(TimeStamped):
    """Base for every row that belongs to exactly one Company (the tenant).

    Adds the mandatory direct ``company`` FK (the query boundary, index prefix and
    audit anchor per DATABASE_SCHEMA.md §1). ``objects`` is tenant-scoped;
    ``all_objects`` is the unscoped escape hatch. ``base_manager_name`` points at
    the unscoped manager so Django's internal/related-object fetches are never
    blocked by a missing context (the FK is already tenant-consistent).
    """

    company = models.ForeignKey(
        "tenants.Company",
        on_delete=models.PROTECT,
        related_name="%(app_label)s_%(class)s_set",
    )

    objects = TenantManager()          # tenant-scoped default manager
    all_objects = models.Manager()     # unscoped: platform/root/bootstrap only

    class Meta:
        abstract = True
        base_manager_name = "all_objects"

    def save(self, *args, **kwargs):
        # Convenience: stamp the company from the active context when not set
        # explicitly, so code inside use_company()/middleware need not repeat it.
        if self.company_id is None:
            company_id = get_current_company_id()
            if company_id is not None:
                self.company_id = company_id
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        self.validate_tenant_consistency()

    def validate_tenant_consistency(self):
        """Every FK to another tenant-owned row must belong to the same company.

        This is the application-layer half of the "same-company foreign key"
        rule (DATABASE_SCHEMA.md §10). It runs on full_clean(), so services and
        forms must call full_clean() before save(). The database-level backstop
        — parent UNIQUE (id, company_id) + a composite FK — is a separate
        hardening step for high-risk relations.
        """
        if self.company_id is None:
            return
        errors = {}
        for field in self._meta.concrete_fields:
            if not isinstance(field, models.ForeignKey):
                continue
            related_model = field.related_model
            if not issubclass(related_model, TenantOwned):
                continue
            related_id = getattr(self, field.attname)
            if related_id is None:
                continue
            related_company_id = (
                related_model.all_objects.filter(pk=related_id)
                .values_list("company_id", flat=True)
                .first()
            )
            if related_company_id is not None and related_company_id != self.company_id:
                errors[field.name] = (
                    f"{field.name} belongs to a different company; "
                    "cross-company references are not allowed."
                )
        if errors:
            raise ValidationError(errors)
