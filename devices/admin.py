"""Root-only Django admin registrations for developer inspection.

These are a debugging aid, not the product surface: the administrator-facing
device workflows are real pages in this app (DEVICE_INTEGRATION_HANDOFF.md
section 2a requires them to work with no shell and no Django admin).

DeviceMessage and PunchEvent are append-only evidence, so they are registered
read-only — the admin must not offer an edit or delete path for them.
"""

from django.contrib import admin

from common.admin import TenantOwnedAdmin
from devices.models import (
    BiometricDevice,
    BiometricTemplate,
    DeviceDepartment,
    DeviceEnrollment,
    DeviceEnrollmentTemplate,
    DeviceMessage,
    DeviceModel,
    DeviceSyncState,
    DeviceVendor,
    PunchEvent,
)


class AppendOnlyAdmin(TenantOwnedAdmin):
    """Evidence tables: visible to root for diagnosis, never editable."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DeviceVendor)
class DeviceVendorAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "adapter_key", "is_active")
    search_fields = ("name", "code")
    list_filter = ("is_active",)


@admin.register(DeviceModel)
class DeviceModelAdmin(admin.ModelAdmin):
    list_display = ("name", "model_code", "vendor", "protocol", "is_active")
    search_fields = ("name", "model_code")
    list_filter = ("protocol", "is_active", "vendor")


@admin.register(BiometricDevice)
class BiometricDeviceAdmin(TenantOwnedAdmin):
    list_display = (
        "name", "serial_number", "company", "branch", "status", "last_seen_at",
    )
    search_fields = ("name", "serial_number", "external_device_id")
    list_filter = ("status", "device_model")


@admin.register(DeviceDepartment)
class DeviceDepartmentAdmin(TenantOwnedAdmin):
    list_display = ("device", "department", "effective_from", "effective_to", "status")
    list_filter = ("status",)


@admin.register(DeviceEnrollment)
class DeviceEnrollmentAdmin(TenantOwnedAdmin):
    list_display = (
        "employee", "device", "device_user_id", "attendance_enabled",
        "assigned_device_authorized", "effective_from", "effective_to",
        "enrollment_status",
    )
    search_fields = ("device_user_id",)
    list_filter = ("attendance_enabled", "assigned_device_authorized", "enrollment_status")


@admin.register(BiometricTemplate)
class BiometricTemplateAdmin(TenantOwnedAdmin):
    list_display = ("employee", "biometric_type", "template_format", "status", "captured_at")
    list_filter = ("biometric_type", "status")
    # Never surface the encrypted payload in a list or form.
    exclude = ("encrypted_template_data",)


@admin.register(DeviceEnrollmentTemplate)
class DeviceEnrollmentTemplateAdmin(TenantOwnedAdmin):
    list_display = (
        "device_enrollment", "biometric_template", "deployment_status", "deployed_at",
    )
    list_filter = ("deployment_status",)


@admin.register(DeviceSyncState)
class DeviceSyncStateAdmin(TenantOwnedAdmin):
    list_display = (
        "device", "last_punch_received_at", "consecutive_error_count",
        "estimated_backlog_count", "clock_offset_seconds",
    )


@admin.register(DeviceMessage)
class DeviceMessageAdmin(AppendOnlyAdmin):
    list_display = (
        "public_id", "device", "message_type", "received_at", "record_count",
        "processing_status",
    )
    list_filter = ("message_type", "processing_status")
    search_fields = ("vendor_message_id", "idempotency_key")


@admin.register(PunchEvent)
class PunchEventAdmin(AppendOnlyAdmin):
    list_display = (
        "device_user_id", "employee", "device", "punched_at_utc",
        "authorization_status", "dedupe_status", "processing_status",
    )
    list_filter = ("authorization_status", "dedupe_status", "processing_status")
    search_fields = ("device_user_id", "vendor_punch_id")
