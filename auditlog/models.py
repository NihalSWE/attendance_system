"""Append-only security evidence; domain history remains in domain models."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class AuditQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Audit records cannot be changed.")

    def delete(self):
        raise ValidationError("Audit records cannot be deleted.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError("Audit records cannot be changed.")


class AuditLog(models.Model):
    class ActorType(models.TextChoices):
        USER = "user", "User"
        SYSTEM = "system", "System"
        DEVICE = "device", "Device"
        BACKGROUND_JOB = "background_job", "Background job"
        ROOT_ADMIN = "root_admin", "Platform owner"

    company = models.ForeignKey("tenants.Company", null=True, blank=True, on_delete=models.PROTECT)
    actor_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    actor_membership = models.ForeignKey("accounts.CompanyMembership", null=True, blank=True, on_delete=models.SET_NULL)
    actor_type = models.CharField(max_length=20, choices=ActorType.choices)
    action = models.CharField(max_length=100)
    object_app = models.CharField(max_length=64)
    object_model = models.CharField(max_length=64)
    object_id = models.CharField(max_length=64)
    object_public_id = models.CharField(max_length=64, blank=True)
    object_display = models.CharField(max_length=255)
    request_id = models.CharField(max_length=100, blank=True, db_index=True)
    correlation_id = models.CharField(max_length=100, blank=True, db_index=True)
    idempotency_key = models.CharField(max_length=100, blank=True, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    before_data = models.JSONField(default=dict, blank=True)
    after_data = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    integrity_hash = models.CharField(max_length=128, blank=True)
    previous_hash = models.CharField(max_length=128, blank=True)
    objects = AuditQuerySet.as_manager()

    @property
    def action_label(self):
        return {
            "administrator.updated": "Administrator updated",
            "company.created": "Company created", "company.updated": "Company details updated",
            "company.status_changed": "Company status changed", "company.feature_changed": "Feature access changed",
            "user.created": "Account created", "membership.granted": "Membership granted",
            "membership.updated": "Membership updated",
        }.get(self.action, self.action)

    class Meta:
        ordering = ("-occurred_at", "-pk")
        indexes = [
            models.Index(fields=["company", "occurred_at"], name="audit_company_time"),
            models.Index(fields=["company", "object_model", "object_id", "occurred_at"], name="audit_company_object_time"),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Audit records cannot be changed.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Audit records cannot be deleted.")
