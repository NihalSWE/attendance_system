"""Devices model tests: constraints, tenant scoping, evidence integrity.

Covers the slice-1 acceptance points that are testable without an ingestion
endpoint yet: cross-tenant isolation, the overlap/uniqueness constraints from
MODEL_FIELD_DICTIONARY.md sections 22-31, and the append-only shape of
DeviceMessage/PunchEvent (idempotency key, source_record_index uniqueness).
"""

from datetime import datetime, timezone as dt_timezone

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from common.tenant import use_company
from devices.models import (
    BiometricDevice,
    BiometricTemplate,
    DeviceDepartment,
    DeviceEnrollment,
    DeviceMessage,
    DeviceModel,
    DeviceSyncState,
    DeviceVendor,
    PunchEvent,
)
from employees.models import Employee
from organization.models import Branch, Department
from tenants.models import Company


def dt(year, month, day, hour=0, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=dt_timezone.utc)


class DevicesModelTests(TestCase):
    def setUp(self):
        # Global reference data — not tenant-owned.
        self.vendor = DeviceVendor.objects.create(
            code="zkteco", name="ZKTeco", adapter_key="zkteco_adms_push"
        )
        self.device_model = DeviceModel.objects.create(
            vendor=self.vendor,
            model_code="senseface-2a",
            name="SenseFace 2A",
            protocol=DeviceModel.Protocol.ADMS_PUSH,
            capabilities={"push": True, "face": True, "fingerprint": True},
        )

        self.company_a = Company.objects.create(code="A", slug="a", name="Company A")
        self.company_b = Company.objects.create(code="B", slug="b", name="Company B")

        with use_company(self.company_a):
            self.branch_a = Branch.objects.create(
                code="HQ", name="Head Office", is_default=True
            )
            self.dept_a = Department.objects.create(
                branch=self.branch_a, code="SW", name="Software"
            )
            self.device_a = BiometricDevice.objects.create(
                branch=self.branch_a,
                device_model=self.device_model,
                name="Front Door",
                serial_number="SN-001",
                timezone="Asia/Dhaka",
            )
            self.alice = Employee.objects.create(first_name="Alice", last_name="Ahmed")

        with use_company(self.company_b):
            self.branch_b = Branch.objects.create(
                code="HQ", name="B Head Office", is_default=True
            )
            self.device_b = BiometricDevice.objects.create(
                branch=self.branch_b,
                device_model=self.device_model,
                name="B Front Door",
                serial_number="SN-001",  # same serial, different company: allowed
                timezone="Asia/Dhaka",
            )

    # --- tenant scoping ---------------------------------------------------

    def test_devices_are_scoped_to_their_company(self):
        with use_company(self.company_a):
            self.assertEqual(list(BiometricDevice.objects.all()), [self.device_a])
        with use_company(self.company_b):
            self.assertEqual(list(BiometricDevice.objects.all()), [self.device_b])

    def test_same_serial_number_allowed_across_companies(self):
        self.assertEqual(
            BiometricDevice.all_objects.filter(serial_number="SN-001").count(), 2
        )

    def test_duplicate_serial_within_same_company_rejected(self):
        with use_company(self.company_a):
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    BiometricDevice.objects.create(
                        branch=self.branch_a,
                        device_model=self.device_model,
                        name="Second unit",
                        serial_number="SN-001",
                        timezone="Asia/Dhaka",
                    )

    def test_device_cannot_reference_another_companys_branch(self):
        with use_company(self.company_a):
            device = BiometricDevice(
                branch=self.branch_b,
                device_model=self.device_model,
                name="Cross-tenant",
                serial_number="SN-XT",
                timezone="Asia/Dhaka",
            )
            device.company = self.company_a
            with self.assertRaises(ValidationError):
                device.full_clean()

    # --- DeviceEnrollment: recognition identity and overlap -------------

    def test_enrollment_recognises_employee_on_device(self):
        with use_company(self.company_a):
            enrollment = DeviceEnrollment.objects.create(
                device=self.device_a,
                employee=self.alice,
                device_user_id="7",
                effective_from=dt(2026, 1, 1),
            )
            self.assertTrue(enrollment.attendance_enabled)
            self.assertFalse(enrollment.assigned_device_authorized)

    def test_same_device_user_id_cannot_identify_two_employees_when_overlapping(self):
        with use_company(self.company_a):
            bob = Employee.objects.create(first_name="Bob", last_name="Barua")
            DeviceEnrollment.objects.create(
                device=self.device_a,
                employee=self.alice,
                device_user_id="7",
                effective_from=dt(2026, 1, 1),
            )
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    DeviceEnrollment.objects.create(
                        device=self.device_a,
                        employee=bob,
                        device_user_id="7",
                        effective_from=dt(2026, 1, 15),
                    )

    def test_device_user_id_reusable_after_prior_enrollment_ends(self):
        with use_company(self.company_a):
            bob = Employee.objects.create(first_name="Bob", last_name="Barua")
            DeviceEnrollment.objects.create(
                device=self.device_a,
                employee=self.alice,
                device_user_id="7",
                effective_from=dt(2026, 1, 1),
                effective_to=dt(2026, 2, 1),
            )
            # Does not raise: the prior interval already ended.
            DeviceEnrollment.objects.create(
                device=self.device_a,
                employee=bob,
                device_user_id="7",
                effective_from=dt(2026, 2, 1),
            )

    def test_enrollment_disabled_flag_defaults_true_and_is_overridable(self):
        with use_company(self.company_a):
            enrollment = DeviceEnrollment.objects.create(
                device=self.device_a,
                employee=self.alice,
                device_user_id="7",
                effective_from=dt(2026, 1, 1),
                attendance_enabled=False,
            )
            self.assertFalse(enrollment.attendance_enabled)

    # --- DeviceDepartment: department must belong to the device's branch --

    def test_department_link_must_share_devices_branch(self):
        with use_company(self.company_b):
            other_dept = Department.objects.create(
                branch=self.branch_b, code="OPS", name="Operations"
            )
        with use_company(self.company_a):
            link = DeviceDepartment(
                device=self.device_a,
                department=other_dept,
                effective_from=dt(2026, 1, 1),
            )
            with self.assertRaises(ValidationError):
                link.full_clean()

    # --- BiometricTemplate: face has no finger position -------------------

    def test_face_template_with_finger_position_rejected(self):
        with use_company(self.company_a):
            template = BiometricTemplate(
                employee=self.alice,
                biometric_type=BiometricTemplate.BiometricType.FACE,
                finger_position=BiometricTemplate.FingerPosition.LEFT_THUMB,
                template_format="x1",
                template_version="1",
                encrypted_template_data=b"abc",
                encryption_key_version="v1",
                template_checksum="deadbeef",
                captured_at=dt(2026, 1, 1),
            )
            with self.assertRaises(ValidationError):
                template.full_clean()

    # --- DeviceMessage: append-only evidence, idempotency ------------------

    def test_message_requires_a_raw_representation(self):
        with use_company(self.company_a):
            message = DeviceMessage(
                device=self.device_a,
                branch=self.branch_a,
                message_type=DeviceMessage.MessageType.PUNCH_BATCH,
                received_at=dt(2026, 1, 1),
                payload_hash="h1",
            )
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    message.save()

    def test_retransmission_with_same_idempotency_key_rejected(self):
        with use_company(self.company_a):
            DeviceMessage.objects.create(
                device=self.device_a,
                branch=self.branch_a,
                message_type=DeviceMessage.MessageType.PUNCH_BATCH,
                idempotency_key="msg-1",
                received_at=dt(2026, 1, 1),
                raw_payload_text="{}",
                payload_hash="h1",
            )
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    DeviceMessage.objects.create(
                        device=self.device_a,
                        branch=self.branch_a,
                        message_type=DeviceMessage.MessageType.PUNCH_BATCH,
                        idempotency_key="msg-1",
                        received_at=dt(2026, 1, 1, 0, 1),
                        raw_payload_text="{}",
                        payload_hash="h1",
                    )

    def test_blank_idempotency_key_does_not_collide(self):
        # Adapters that cannot derive a trustworthy key leave it blank; two
        # such messages from the same device must not collide with each other.
        with use_company(self.company_a):
            DeviceMessage.objects.create(
                device=self.device_a,
                branch=self.branch_a,
                message_type=DeviceMessage.MessageType.HEARTBEAT,
                received_at=dt(2026, 1, 1),
                raw_payload_text="{}",
                payload_hash="h1",
            )
            # Does not raise.
            DeviceMessage.objects.create(
                device=self.device_a,
                branch=self.branch_a,
                message_type=DeviceMessage.MessageType.HEARTBEAT,
                received_at=dt(2026, 1, 1, 0, 1),
                raw_payload_text="{}",
                payload_hash="h1",
            )

    # --- PunchEvent: evidence uniqueness and preservation ------------------

    def _message(self, idempotency_key):
        return DeviceMessage.objects.create(
            device=self.device_a,
            branch=self.branch_a,
            message_type=DeviceMessage.MessageType.PUNCH_BATCH,
            idempotency_key=idempotency_key,
            received_at=dt(2026, 1, 2, 9, 0),
            raw_payload_text="{}",
            payload_hash="h1",
        )

    def test_duplicate_source_record_index_within_same_message_rejected(self):
        with use_company(self.company_a):
            message = self._message("m1")
            PunchEvent.objects.create(
                device_message=message,
                device=self.device_a,
                branch=self.branch_a,
                device_user_id="7",
                source_record_index=0,
                punched_at_device_raw="2026-01-02 09:00:00",
                punched_at_device=dt(2026, 1, 2, 9, 0),
                punched_at_utc=dt(2026, 1, 2, 3, 0),
                received_at=dt(2026, 1, 2, 9, 0),
                raw_record={"raw": "1"},
            )
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    PunchEvent.objects.create(
                        device_message=message,
                        device=self.device_a,
                        branch=self.branch_a,
                        device_user_id="7",
                        source_record_index=0,
                        punched_at_device_raw="2026-01-02 09:00:00",
                        punched_at_device=dt(2026, 1, 2, 9, 0),
                        punched_at_utc=dt(2026, 1, 2, 3, 0),
                        received_at=dt(2026, 1, 2, 9, 0),
                        raw_record={"raw": "1"},
                    )

    def test_unauthorized_punch_is_preserved_not_dropped(self):
        # An unauthorized/unresolved punch must still be a normal, queryable
        # row — never silently discarded (DEVICE_ATTENDANCE_POLICY.md).
        with use_company(self.company_a):
            message = self._message("m2")
            punch = PunchEvent.objects.create(
                device_message=message,
                device=self.device_a,
                branch=self.branch_a,
                device_user_id="unknown-99",
                source_record_index=0,
                punched_at_device_raw="2026-01-02 09:05:00",
                punched_at_device=dt(2026, 1, 2, 9, 5),
                punched_at_utc=dt(2026, 1, 2, 3, 5),
                received_at=dt(2026, 1, 2, 9, 5),
                raw_record={"raw": "2"},
                authorization_status=PunchEvent.AuthorizationStatus.UNKNOWN_EMPLOYEE,
                processing_status=PunchEvent.ProcessingStatus.EXCLUDED,
            )
            self.assertEqual(PunchEvent.objects.count(), 1)
            self.assertEqual(
                PunchEvent.objects.get(pk=punch.pk).authorization_status,
                PunchEvent.AuthorizationStatus.UNKNOWN_EMPLOYEE,
            )

    def test_two_close_scans_are_not_forced_into_duplicates(self):
        # Closeness in time alone must not be treated as proof of duplication;
        # both rows persist with dedupe_status left at the default 'unique'.
        with use_company(self.company_a):
            message = self._message("m3")
            first = PunchEvent.objects.create(
                device_message=message,
                device=self.device_a,
                branch=self.branch_a,
                device_user_id="7",
                source_record_index=0,
                punched_at_device_raw="2026-01-02 09:00:00",
                punched_at_device=dt(2026, 1, 2, 9, 0),
                punched_at_utc=dt(2026, 1, 2, 3, 0),
                received_at=dt(2026, 1, 2, 9, 0),
                raw_record={"raw": "3"},
            )
            second = PunchEvent.objects.create(
                device_message=message,
                device=self.device_a,
                branch=self.branch_a,
                device_user_id="7",
                source_record_index=1,
                punched_at_device_raw="2026-01-02 09:00:05",
                punched_at_device=dt(2026, 1, 2, 9, 0, 5),
                punched_at_utc=dt(2026, 1, 2, 3, 0, 5),
                received_at=dt(2026, 1, 2, 9, 0, 5),
                raw_record={"raw": "4"},
            )
            self.assertEqual(first.dedupe_status, PunchEvent.DedupeStatus.UNIQUE)
            self.assertEqual(second.dedupe_status, PunchEvent.DedupeStatus.UNIQUE)
            self.assertEqual(PunchEvent.objects.count(), 2)

    # --- DeviceSyncState: one row per device, not evidence -----------------

    def test_sync_state_is_one_per_device(self):
        with use_company(self.company_a):
            DeviceSyncState.objects.create(device=self.device_a)
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    DeviceSyncState.objects.create(device=self.device_a)
