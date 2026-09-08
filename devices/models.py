"""Devices: biometric hardware, enrollment and raw punch evidence.

Ten models (MODEL_FIELD_DICTIONARY.md sections 22-31). ``DeviceVendor`` and
``DeviceModel`` are global reference data shared across tenants; everything
else is company-scoped via ``TenantOwned``.

``DeviceMessage`` and ``PunchEvent`` are append-only source evidence
(DEVICE_ATTENDANCE_POLICY.md, TEAM_LEAD_PLAYBOOK.md "raw evidence vs.
calculation"): resolution/authorization fields may advance through an audited
revision, but original device-reported values are never rewritten, and rows
are never deleted to make a calculated result look right.
"""

import uuid

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import RangeBoundary, RangeOperators
from django.core.exceptions import ValidationError
from django.db import models

from common.db import TstzRange
from common.models import ActorTracked, TenantOwned

# '[)' bounds: inclusive start, exclusive end — the project-wide effective-dated
# convention (MODEL_FIELD_DICTIONARY.md Conventions).
_PERIOD = TstzRange("effective_from", "effective_to", RangeBoundary())


class DeviceVendor(models.Model):
    """Global reference row identifying a supported hardware vendor.

    Not tenant-owned: every company chooses from the same vendor catalogue.
    """

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=150, unique=True)
    # Selects a reviewed adapter class in devices/adapters/; never executed as
    # user input, and never accepts an arbitrary import path from the database.
    adapter_key = models.CharField(max_length=64)
    description = models.TextField(blank=True)
    support_url = models.URLField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "devices_devicevendor"

    def __str__(self):
        return self.name


class DeviceModel(models.Model):
    """Global reference row for a specific hardware model of a vendor."""

    class Protocol(models.TextChoices):
        ADMS_PUSH = "adms_push", "ADMS push"
        WEBHOOK = "webhook", "Webhook"
        VENDOR_CLOUD_API = "vendor_cloud_api", "Vendor cloud API"
        FILE_IMPORT = "file_import", "File import"
        OTHER = "other", "Other"

    vendor = models.ForeignKey(
        DeviceVendor, on_delete=models.PROTECT, related_name="models"
    )
    model_code = models.CharField(max_length=64)
    name = models.CharField(max_length=150)
    protocol = models.CharField(max_length=32, choices=Protocol.choices)
    # Flags such as {"push": true, "face": true, "fingerprint": true,
    # "card": false, "commands": false, "template_export": false,
    # "template_import": false}. Validated against a fixed key set in clean().
    capabilities = models.JSONField(default=dict, blank=True)
    supported_template_formats = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    _CAPABILITY_KEYS = {
        "push", "face", "fingerprint", "card", "commands",
        "template_export", "template_import",
    }

    class Meta:
        db_table = "devices_devicemodel"
        constraints = [
            models.UniqueConstraint(
                fields=["vendor", "model_code"], name="uniq_devicemodel_vendor_code"
            ),
        ]

    def __str__(self):
        return f"{self.vendor.name} {self.name}"

    def clean(self):
        super().clean()
        if not isinstance(self.capabilities, dict):
            raise ValidationError({"capabilities": "Must be a JSON object."})
        unknown = set(self.capabilities) - self._CAPABILITY_KEYS
        if unknown:
            raise ValidationError(
                {"capabilities": f"Unknown capability keys: {sorted(unknown)}"}
            )
        if not isinstance(self.supported_template_formats, list):
            raise ValidationError(
                {"supported_template_formats": "Must be a JSON array."}
            )


class BiometricDevice(TenantOwned, ActorTracked):
    """A physical biometric terminal registered to a company/branch."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACTIVE = "active", "Active"
        OFFLINE = "offline", "Offline"
        SUSPENDED = "suspended", "Suspended"
        RETIRED = "retired", "Retired"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    branch = models.ForeignKey(
        "organization.Branch", on_delete=models.PROTECT, related_name="biometric_devices"
    )
    device_model = models.ForeignKey(
        DeviceModel, on_delete=models.PROTECT, related_name="devices"
    )
    name = models.CharField(max_length=150)
    serial_number = models.CharField(max_length=100)
    # Vendor/cloud-side identifier; distinct from our own public_id.
    external_device_id = models.CharField(max_length=100, blank=True)
    timezone = models.CharField(max_length=64)
    # Identifies which server-managed secret authenticates this device's
    # inbound pushes; the secret itself is never stored or returned in the
    # clear — see authentication_secret_hash.
    authentication_key_id = models.CharField(max_length=100, blank=True)
    authentication_secret_hash = models.CharField(max_length=255, blank=True)
    firmware_version = models.CharField(max_length=64, blank=True)
    ip_address_last_seen = models.GenericIPAddressField(null=True, blank=True)

    installed_at = models.DateTimeField(null=True, blank=True)
    decommissioned_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    last_message_at = models.DateTimeField(null=True, blank=True)
    clock_offset_seconds = models.IntegerField(null=True, blank=True)

    # Vendor-specific configuration (push interval, verification mode, log
    # capacity, comm key label, etc). Validated in clean() against a per-adapter
    # schema hook; kept permissive here since the adapter owns the real schema.
    settings = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )

    class Meta:
        db_table = "devices_biometricdevice"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "serial_number"],
                name="uniq_biometricdevice_company_serial",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "status"]),
            models.Index(fields=["company", "branch"]),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if not isinstance(self.settings, dict):
            raise ValidationError({"settings": "Must be a JSON object."})


class DeviceDepartment(TenantOwned, ActorTracked):
    """Dated mapping restricting a device to one or more departments.

    Only meaningful in ``department_devices`` scope mode
    (DEVICE_ATTENDANCE_POLICY.md): a device with no active mapping is treated
    as serving its whole branch in that mode, and mappings never restrict
    branch_devices/company_devices or an explicit assigned-device grant.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ENDED = "ended", "Ended"

    device = models.ForeignKey(
        BiometricDevice, on_delete=models.PROTECT, related_name="department_links"
    )
    department = models.ForeignKey(
        "organization.Department", on_delete=models.PROTECT, related_name="device_links"
    )
    effective_from = models.DateTimeField()
    effective_to = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE
    )

    class Meta:
        db_table = "devices_devicedepartment"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(effective_to__isnull=True)
                | models.Q(effective_to__gt=models.F("effective_from")),
                name="devicedepartment_period_end_after_start",
            ),
            ExclusionConstraint(
                name="excl_devicedepartment_overlap",
                expressions=[
                    ("company", RangeOperators.EQUAL),
                    ("device", RangeOperators.EQUAL),
                    ("department", RangeOperators.EQUAL),
                    (_PERIOD, RangeOperators.OVERLAPS),
                ],
                condition=~models.Q(status="ended"),
            ),
        ]
        indexes = [
            models.Index(fields=["company", "device", "effective_from"]),
            models.Index(fields=["company", "department"]),
        ]

    def __str__(self):
        return f"{self.device_id} -> {self.department_id}"

    def clean(self):
        super().clean()
        errors = {}
        if self.effective_to and self.effective_to <= self.effective_from:
            errors["effective_to"] = "End must be after start."
        if self.device_id and self.department_id:
            if self.device.branch_id != self.department.branch_id:
                errors["department"] = (
                    "Department must belong to the device's branch."
                )
        if errors:
            raise ValidationError(errors)


class DeviceEnrollment(TenantOwned, ActorTracked):
    """Maps a device-reported user id to a permanent Employee, on an interval.

    Recognition and authorization are separate concerns
    (DEVICE_ATTENDANCE_POLICY.md): this row proves the device can identify the
    person. ``attendance_enabled`` is the master enable/disable switch (false
    denies the punch under every scope). ``assigned_device_authorized`` is an
    additional explicit grant consulted only in assigned_devices mode.
    """

    class Privilege(models.TextChoices):
        NORMAL_USER = "normal_user", "Normal user"
        DEVICE_ADMIN = "device_admin", "Device admin"
        OTHER = "other", "Other"

    class EnrollmentStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        QUEUED = "queued", "Queued"
        SYNCED = "synced", "Synced"
        FAILED = "failed", "Failed"
        REMOVED = "removed", "Removed"

    device = models.ForeignKey(
        BiometricDevice, on_delete=models.PROTECT, related_name="enrollments"
    )
    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.PROTECT, related_name="device_enrollments"
    )
    device_user_id = models.CharField(max_length=100)
    card_number = models.CharField(max_length=255, blank=True)
    device_privilege = models.CharField(
        max_length=16, choices=Privilege.choices, default=Privilege.NORMAL_USER
    )
    attendance_enabled = models.BooleanField(default=True)
    assigned_device_authorized = models.BooleanField(default=False)

    effective_from = models.DateTimeField()
    effective_to = models.DateTimeField(null=True, blank=True)
    enrollment_status = models.CharField(
        max_length=16, choices=EnrollmentStatus.choices, default=EnrollmentStatus.PENDING
    )
    vendor_enrollment_revision = models.CharField(max_length=100, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    removed_at = models.DateTimeField(null=True, blank=True)
    sync_error_code = models.CharField(max_length=64, blank=True)
    sync_error_message = models.TextField(blank=True)

    class Meta:
        db_table = "devices_deviceenrollment"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(effective_to__isnull=True)
                | models.Q(effective_to__gt=models.F("effective_from")),
                name="deviceenrollment_period_end_after_start",
            ),
            # One device_user_id cannot identify two different employees on the
            # same device at overlapping times.
            ExclusionConstraint(
                name="excl_deviceenrollment_user_id_overlap",
                expressions=[
                    ("company", RangeOperators.EQUAL),
                    ("device", RangeOperators.EQUAL),
                    ("device_user_id", RangeOperators.EQUAL),
                    (_PERIOD, RangeOperators.OVERLAPS),
                ],
                condition=~models.Q(enrollment_status="removed"),
            ),
            # One employee cannot hold two overlapping enrollments on the same
            # device (recognition identity must be unambiguous at punch time).
            ExclusionConstraint(
                name="excl_deviceenrollment_employee_device_overlap",
                expressions=[
                    ("company", RangeOperators.EQUAL),
                    ("device", RangeOperators.EQUAL),
                    ("employee", RangeOperators.EQUAL),
                    (_PERIOD, RangeOperators.OVERLAPS),
                ],
                condition=~models.Q(enrollment_status="removed"),
            ),
        ]
        indexes = [
            models.Index(fields=["company", "device", "device_user_id"]),
            models.Index(fields=["company", "employee"]),
        ]

    def __str__(self):
        return f"{self.employee_id} on {self.device_id} ({self.device_user_id})"

    def clean(self):
        super().clean()
        if self.effective_to and self.effective_to <= self.effective_from:
            raise ValidationError({"effective_to": "End must be after start."})


class BiometricTemplate(TenantOwned, ActorTracked):
    """An employee's encrypted biometric template, independent of any device.

    Broad software permission never uploads templates to devices by itself;
    provisioning onto a specific device is tracked separately by
    ``DeviceEnrollmentTemplate``.
    """

    class BiometricType(models.TextChoices):
        FINGERPRINT = "fingerprint", "Fingerprint"
        FACE = "face", "Face"

    class FingerPosition(models.TextChoices):
        LEFT_THUMB = "left_thumb", "Left thumb"
        LEFT_INDEX = "left_index", "Left index"
        LEFT_MIDDLE = "left_middle", "Left middle"
        LEFT_RING = "left_ring", "Left ring"
        LEFT_LITTLE = "left_little", "Left little"
        RIGHT_THUMB = "right_thumb", "Right thumb"
        RIGHT_INDEX = "right_index", "Right index"
        RIGHT_MIDDLE = "right_middle", "Right middle"
        RIGHT_RING = "right_ring", "Right ring"
        RIGHT_LITTLE = "right_little", "Right little"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        REVOKED = "revoked", "Revoked"
        INCOMPATIBLE = "incompatible", "Incompatible"
        DELETED_PENDING = "deleted_pending", "Deletion pending"

    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.PROTECT, related_name="biometric_templates"
    )
    biometric_type = models.CharField(max_length=16, choices=BiometricType.choices)
    finger_position = models.CharField(
        max_length=16, choices=FingerPosition.choices, blank=True
    )
    template_format = models.CharField(max_length=32)
    template_version = models.CharField(max_length=32)
    vendor = models.ForeignKey(
        DeviceVendor, null=True, blank=True, on_delete=models.PROTECT,
        related_name="biometric_templates",
    )
    compatible_device_model = models.ForeignKey(
        DeviceModel, null=True, blank=True, on_delete=models.PROTECT,
        related_name="compatible_templates",
    )
    captured_from_device = models.ForeignKey(
        BiometricDevice, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="captured_templates",
    )
    # Application-encrypted bytes; the encryption key itself is never stored
    # alongside the data. Protect in backups/logs (MODEL_FIELD_DICTIONARY.md).
    encrypted_template_data = models.BinaryField()
    encryption_key_version = models.CharField(max_length=32)
    template_checksum = models.CharField(max_length=128)
    template_size_bytes = models.PositiveIntegerField(null=True, blank=True)
    quality_score = models.PositiveIntegerField(null=True, blank=True)
    captured_at = models.DateTimeField()
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE
    )
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+",
    )
    revocation_reason = models.TextField(blank=True)

    class Meta:
        db_table = "devices_biometrictemplate"
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(biometric_type="face")
                | models.Q(finger_position=""),
                name="biometrictemplate_face_has_no_finger_position",
            ),
            models.UniqueConstraint(
                fields=[
                    "company", "employee", "biometric_type",
                    "template_format", "template_checksum",
                ],
                condition=models.Q(status="active"),
                name="uniq_biometrictemplate_active_checksum",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "employee", "biometric_type"]),
        ]

    def __str__(self):
        return f"{self.employee_id} {self.biometric_type}"

    def clean(self):
        super().clean()
        if self.biometric_type == self.BiometricType.FACE and self.finger_position:
            raise ValidationError(
                {"finger_position": "Face templates do not have a finger position."}
            )


class DeviceEnrollmentTemplate(TenantOwned):
    """Tracks deployment of one biometric template onto one device enrollment."""

    class DeploymentStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        QUEUED = "queued", "Queued"
        SYNCED = "synced", "Synced"
        FAILED = "failed", "Failed"
        REMOVED = "removed", "Removed"

    device_enrollment = models.ForeignKey(
        DeviceEnrollment, on_delete=models.PROTECT, related_name="template_deployments"
    )
    biometric_template = models.ForeignKey(
        BiometricTemplate, on_delete=models.PROTECT, related_name="device_deployments"
    )
    device_template_id = models.CharField(max_length=100, blank=True)
    deployment_status = models.CharField(
        max_length=16, choices=DeploymentStatus.choices, default=DeploymentStatus.PENDING
    )
    attempt_count = models.PositiveIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    deployed_at = models.DateTimeField(null=True, blank=True)
    removed_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=64, blank=True)
    last_error_message = models.TextField(blank=True)
    # Idempotency key for the vendor deployment command, so a retried command
    # does not queue the same deployment twice.
    source_key = models.CharField(max_length=150)

    class Meta:
        db_table = "devices_deviceenrollmenttemplate"
        constraints = [
            models.UniqueConstraint(
                fields=["device_enrollment", "biometric_template"],
                condition=~models.Q(deployment_status="removed"),
                name="uniq_deviceenrollmenttemplate_active",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "device_enrollment"]),
        ]

    def __str__(self):
        return f"{self.device_enrollment_id} <- {self.biometric_template_id}"

    def clean(self):
        super().clean()
        errors = {}
        if self.device_enrollment_id and self.biometric_template_id:
            if self.device_enrollment.employee_id != self.biometric_template.employee_id:
                errors["biometric_template"] = (
                    "Template must belong to the same employee as the enrollment."
                )
            device_model_id = self.device_enrollment.device.device_model_id
            compatible_id = self.biometric_template.compatible_device_model_id
            if compatible_id and compatible_id != device_model_id:
                errors["biometric_template"] = (
                    "Template is not compatible with the enrollment's device model."
                )
        if errors:
            raise ValidationError(errors)


class DeviceSyncState(TenantOwned):
    """Current, mutable operational state for one device (not historical evidence).

    One row per device. ``version`` supports optimistic concurrency control
    when the ingestion endpoint and a background reconciliation job might
    otherwise race to update the same row.
    """

    device = models.OneToOneField(
        BiometricDevice, on_delete=models.PROTECT, related_name="sync_state"
    )
    last_vendor_sequence = models.CharField(max_length=100, blank=True)
    last_vendor_cursor = models.CharField(max_length=255, blank=True)
    last_device_event_at = models.DateTimeField(null=True, blank=True)
    last_message_received_at = models.DateTimeField(null=True, blank=True)
    last_punch_received_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=64, blank=True)
    last_error_message = models.TextField(blank=True)
    consecutive_error_count = models.PositiveIntegerField(default=0)
    estimated_backlog_count = models.PositiveIntegerField(default=0)
    clock_offset_seconds = models.IntegerField(null=True, blank=True)
    state_data = models.JSONField(default=dict, blank=True)
    version = models.PositiveBigIntegerField(default=0)

    class Meta:
        db_table = "devices_devicesyncstate"

    def __str__(self):
        return f"sync state for {self.device_id}"

    def clean(self):
        super().clean()
        if not isinstance(self.state_data, dict):
            raise ValidationError({"state_data": "Must be a JSON object."})


class DeviceMessage(TenantOwned):
    """Durable, append-only capture of one inbound message from a device.

    Saved before any parsing/attendance logic runs (DEVICE_ATTENDANCE_POLICY.md
    "Processing and history" step 1) so ingestion is durable before
    acknowledgement — a background worker finishing later is never a
    precondition for accepting the data.
    """

    class MessageType(models.TextChoices):
        PUNCH_BATCH = "punch_batch", "Punch batch"
        HEARTBEAT = "heartbeat", "Heartbeat"
        ENROLLMENT_RESULT = "enrollment_result", "Enrollment result"
        COMMAND_RESULT = "command_result", "Command result"
        DEVICE_INFO = "device_info", "Device info"
        UNKNOWN = "unknown", "Unknown"

    class ProcessingStatus(models.TextChoices):
        RECEIVED = "received", "Received"
        PARSING = "parsing", "Parsing"
        PARSED = "parsed", "Parsed"
        PARTIALLY_FAILED = "partially_failed", "Partially failed"
        FAILED = "failed", "Failed"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    device = models.ForeignKey(
        BiometricDevice, on_delete=models.PROTECT, related_name="messages"
    )
    # Snapshot of the device's branch at receipt time; the device's current
    # branch may change later without rewriting historical messages.
    branch = models.ForeignKey(
        "organization.Branch", on_delete=models.PROTECT, related_name="device_messages"
    )
    message_type = models.CharField(max_length=32, choices=MessageType.choices)
    vendor_message_id = models.CharField(max_length=150, blank=True)
    vendor_sequence = models.CharField(max_length=100, blank=True)
    # Set only from a trustworthy adapter identity (never trusted client input
    # verbatim), so it can safely be used to reject a retransmission.
    idempotency_key = models.CharField(max_length=200, blank=True)
    occurred_at_device = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(db_index=True)
    content_type = models.CharField(max_length=100, blank=True)
    encoding = models.CharField(max_length=32, blank=True)
    raw_payload_text = models.TextField(null=True, blank=True)
    raw_payload_binary = models.BinaryField(null=True, blank=True)
    payload_json = models.JSONField(null=True, blank=True)
    payload_hash = models.CharField(max_length=128)
    source_ip = models.GenericIPAddressField(null=True, blank=True)
    request_headers_snapshot = models.JSONField(null=True, blank=True)
    record_count = models.PositiveIntegerField(null=True, blank=True)
    processing_status = models.CharField(
        max_length=20, choices=ProcessingStatus.choices, default=ProcessingStatus.RECEIVED
    )
    processing_started_at = models.DateTimeField(null=True, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    processing_attempts = models.PositiveIntegerField(default=0)
    processing_error = models.TextField(blank=True)

    class Meta:
        db_table = "devices_devicemessage"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(raw_payload_text__isnull=False)
                | models.Q(raw_payload_binary__isnull=False),
                name="devicemessage_has_raw_representation",
            ),
            models.UniqueConstraint(
                fields=["device", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="uniq_devicemessage_device_idempotency_key",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "device", "received_at"]),
            models.Index(fields=["company", "processing_status"]),
        ]

    def __str__(self):
        return f"{self.message_type} from {self.device_id} at {self.received_at}"


class PunchEvent(TenantOwned):
    """One raw punch record extracted from a DeviceMessage.

    Append-only source fact (TEAM_LEAD_PLAYBOOK.md invariant): mapping/
    processing fields (``device_enrollment``, ``employee``,
    ``authorization_status``, ``authorization_snapshot``, ``dedupe_status``,
    ``duplicate_of``, ``processing_status``) may advance through an audited
    revision, but ``punched_at_device_raw`` and ``raw_record`` never change.
    """

    class VerificationMethod(models.TextChoices):
        FINGERPRINT = "fingerprint", "Fingerprint"
        FACE = "face", "Face"
        CARD = "card", "Card"
        PIN = "pin", "PIN"
        UNKNOWN = "unknown", "Unknown"

    class AuthorizationStatus(models.TextChoices):
        AUTHORIZED = "authorized", "Authorized"
        UNAUTHORIZED_DEVICE = "unauthorized_device", "Unauthorized device"
        UNKNOWN_EMPLOYEE = "unknown_employee", "Unknown employee"
        EXPIRED_ENROLLMENT = "expired_enrollment", "Expired enrollment"
        DEPARTMENT_MISMATCH = "department_mismatch", "Department mismatch"
        BRANCH_MISMATCH = "branch_mismatch", "Branch mismatch"
        ENROLLMENT_DISABLED = "enrollment_disabled", "Enrollment disabled"
        POLICY_UNRESOLVED = "policy_unresolved", "Policy unresolved"

    class DedupeStatus(models.TextChoices):
        UNIQUE = "unique", "Unique"
        PROBABLE_DUPLICATE = "probable_duplicate", "Probable duplicate"
        CONFIRMED_DUPLICATE = "confirmed_duplicate", "Confirmed duplicate"

    class ProcessingStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        ALLOCATED = "allocated", "Allocated"
        EXCLUDED = "excluded", "Excluded"
        NEEDS_REVIEW = "needs_review", "Needs review"
        FAILED = "failed", "Failed"

    device_message = models.ForeignKey(
        DeviceMessage, on_delete=models.PROTECT, related_name="punch_events"
    )
    device = models.ForeignKey(
        BiometricDevice, on_delete=models.PROTECT, related_name="punch_events"
    )
    # Snapshot of the *device's* branch at receipt time — not the employee's
    # assignment/home branch, which authorization must resolve separately.
    branch = models.ForeignKey(
        "organization.Branch", on_delete=models.PROTECT, related_name="punch_events"
    )
    device_enrollment = models.ForeignKey(
        DeviceEnrollment, null=True, blank=True, on_delete=models.PROTECT,
        related_name="punch_events",
    )
    employee = models.ForeignKey(
        "employees.Employee", null=True, blank=True, on_delete=models.PROTECT,
        related_name="punch_events",
    )
    device_user_id = models.CharField(max_length=100)
    vendor_punch_id = models.CharField(max_length=150, blank=True)
    vendor_sequence = models.CharField(max_length=100, blank=True)
    source_record_index = models.PositiveIntegerField()

    punched_at_device_raw = models.CharField(max_length=100)
    punched_at_device = models.DateTimeField()
    punched_at_utc = models.DateTimeField()
    device_timezone = models.CharField(max_length=64, blank=True)
    utc_offset_minutes = models.IntegerField(null=True, blank=True)
    received_at = models.DateTimeField()

    verification_method = models.CharField(
        max_length=16, choices=VerificationMethod.choices, default=VerificationMethod.UNKNOWN
    )
    # Vendor-reported IN/OUT flag; informational only, never trusted to decide
    # pairing (DEVICE_ATTENDANCE_POLICY.md "Duplicate and clock handling").
    reported_direction = models.CharField(max_length=16, blank=True)
    reported_status_code = models.CharField(max_length=32, blank=True)
    raw_record = models.JSONField()

    authorization_status = models.CharField(
        max_length=32, choices=AuthorizationStatus.choices,
        default=AuthorizationStatus.POLICY_UNRESOLVED,
    )
    authorization_snapshot = models.JSONField(default=dict, blank=True)
    dedupe_status = models.CharField(
        max_length=20, choices=DedupeStatus.choices, default=DedupeStatus.UNIQUE
    )
    duplicate_of = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="duplicates"
    )
    processing_status = models.CharField(
        max_length=16, choices=ProcessingStatus.choices, default=ProcessingStatus.PENDING
    )
    processing_error = models.TextField(blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "devices_punchevent"
        constraints = [
            models.UniqueConstraint(
                fields=["device_message", "source_record_index"],
                name="uniq_punchevent_message_record_index",
            ),
            models.UniqueConstraint(
                fields=["device", "vendor_punch_id"],
                condition=~models.Q(vendor_punch_id=""),
                name="uniq_punchevent_device_vendor_punch_id",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "device", "punched_at_utc"]),
            models.Index(fields=["company", "employee", "punched_at_utc"]),
            models.Index(fields=["company", "authorization_status"]),
            models.Index(fields=["company", "dedupe_status"]),
            models.Index(fields=["company", "processing_status"]),
        ]

    def __str__(self):
        return f"punch {self.device_user_id} @ {self.punched_at_utc}"

    @property
    def display_timezone(self):
        """Timezone to render device-local times in, or None for the default.

        ``{% timezone %}`` raises on an empty string, and device_timezone can
        legitimately be blank for a punch captured before the device had one.
        """
        return self.device_timezone or None
