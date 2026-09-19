"""Device administration tests: verify codes, settings writes and user sync.

Covers the three administrator-facing behaviours added after commissioning the
physical SenseFace 2A: mapping its rtlog verification codes, writing a bounded
set of settings back to it, and turning its user roster into employee records.
"""

from django.test import TestCase
from django.utils import timezone

from common.tenant import use_company
from devices.adapters.zkteco_adms import ZKTecoAdmsAdapter
from devices.models import (
    BiometricDevice,
    DeviceEnrollment,
    DeviceMessage,
    DeviceModel,
    DeviceVendor,
    PunchEvent,
)
from devices.services.commands import (
    pending_summary,
    queue_set_option,
)
from devices.services.user_sync import SyncNotPossible, sync_device_users
from employees.models import Employee
from organization.catalogue import adopt_department, adopt_designation
from organization.models import Branch, Department, Designation
from tenants.models import Company


def _reference_data():
    vendor = DeviceVendor.objects.get_or_create(
        code="zkteco",
        defaults={"name": "ZKTeco", "adapter_key": "zkteco_adms_push"},
    )[0]
    return DeviceModel.objects.get_or_create(
        vendor=vendor,
        model_code="senseface-2a",
        defaults={
            "name": "SenseFace 2A",
            "protocol": DeviceModel.Protocol.ADMS_PUSH,
            "capabilities": {"push": True},
        },
    )[0]


class VerifyTypeMappingTests(TestCase):
    """rtlog verification codes, mapped from captured device traffic."""

    def setUp(self):
        self.adapter = ZKTecoAdmsAdapter()

    def _method(self, verifytype, cardno="0"):
        row = (
            f"time=2026-09-09 12:44:28\tpin=445966\tcardno={cardno}"
            f"\teventaddr=1\tevent=3\tinoutstatus=0\tverifytype={verifytype}"
            f"\tindex=211\r\n"
        )
        parsed = self.adapter.parse(
            path_name="cdata_upload",
            query={"table": "rtlog"},
            body_text=row,
            serial_number="NYU7251601501",
        )
        return parsed.punches[0].verification_method

    def test_fingerprint_and_face_are_recognised(self):
        self.assertEqual(self._method("1"), PunchEvent.VerificationMethod.FINGERPRINT)
        self.assertEqual(self._method("15"), PunchEvent.VerificationMethod.FACE)

    def test_card_code_four_is_recognised(self):
        # verifytype=4 was the only captured value carrying a real card
        # number, and it matched that user's card in the device's own table.
        self.assertEqual(
            self._method("4", cardno="196793"), PunchEvent.VerificationMethod.CARD
        )

    def test_unmapped_code_stays_unknown_rather_than_guessed(self):
        self.assertEqual(self._method("77"), PunchEvent.VerificationMethod.UNKNOWN)


class SetOptionTests(TestCase):
    def setUp(self):
        model = _reference_data()
        self.company = Company.objects.create(code="A", slug="a", name="Company A")
        with use_company(self.company):
            branch = Branch.objects.create(code="HQ", name="HQ", is_default=True)
            self.device = BiometricDevice.objects.create(
                branch=branch,
                device_model=model,
                name="Main Entrance",
                serial_number="NYU7251601501",
                timezone="Asia/Dhaka",
                status=BiometricDevice.Status.ACTIVE,
            )

    def test_valid_option_is_queued_and_remembered(self):
        entry, error = queue_set_option(
            device=self.device, option_key="push_interval_seconds", value="20"
        )
        self.assertEqual(error, "")
        self.assertEqual(entry["body"], "SET OPTION Delay=20")
        # Stored too: otherwise the next handshake re-announces the old value
        # and quietly undoes the change.
        self.device.refresh_from_db()
        self.assertEqual(self.device.settings["push_interval_seconds"], 20)

    def test_out_of_range_value_is_refused(self):
        entry, error = queue_set_option(
            device=self.device, option_key="push_interval_seconds", value="99999"
        )
        self.assertIsNone(entry)
        self.assertTrue(error)

    def test_non_numeric_value_is_refused(self):
        entry, error = queue_set_option(
            device=self.device, option_key="push_interval_seconds", value="soon"
        )
        self.assertIsNone(entry)
        self.assertTrue(error)

    def test_options_outside_the_allowlist_are_refused(self):
        # Rewriting the server address or comm key remotely could strand the
        # device, so neither is settable from a web page.
        for key in ("server_address", "comm_key", "ServerIP"):
            entry, error = queue_set_option(device=self.device, option_key=key, value="1")
            self.assertIsNone(entry, key)
            self.assertTrue(error, key)

    def test_repeated_set_supersedes_the_earlier_one(self):
        queue_set_option(device=self.device, option_key="push_interval_seconds", value="20")
        queue_set_option(device=self.device, option_key="push_interval_seconds", value="30")
        pending = pending_summary(self.device)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["body"], "SET OPTION Delay=30")


class DeviceUserSyncTests(TestCase):
    USER_TABLE = (
        "user uid=1\tcardno=2796848\tpin=445961\tpassword=\t"
        "group=1\tname=\tprivilege=0\tdisable=0\n"
        "user uid=2\tcardno=196793\tpin=445966\tpassword=\t"
        "group=1\tname=Nihal\tprivilege=14\tdisable=0\n"
    )

    def setUp(self):
        model = _reference_data()
        self.company = Company.objects.create(code="A", slug="a", name="Company A")
        with use_company(self.company):
            self.branch = Branch.objects.create(code="HQ", name="HQ", is_default=True)
            self.department = adopt_department(self.branch, "SW", "Software")
            adopt_designation(self.department, "DEV", "Developer")
            self.device = BiometricDevice.objects.create(
                branch=self.branch,
                device_model=model,
                name="Main Entrance",
                serial_number="NYU7251601501",
                timezone="Asia/Dhaka",
                status=BiometricDevice.Status.ACTIVE,
            )
            DeviceMessage.objects.create(
                device=self.device,
                branch=self.branch,
                message_type=DeviceMessage.MessageType.ENROLLMENT_RESULT,
                received_at=timezone.now(),
                raw_payload_text=self.USER_TABLE,
                payload_hash="h1",
            )

    def _sync(self):
        with use_company(self.company):
            return sync_device_users(device=self.device)

    def test_sync_creates_a_draft_employee_and_mapping(self):
        created, skipped, errors = self._sync()
        self.assertEqual(errors, [])
        self.assertEqual(len(created), 2)
        with use_company(self.company):
            self.assertTrue(
                DeviceEnrollment.objects.filter(device_user_id="445961").exists()
            )

    def test_named_user_keeps_its_name(self):
        self._sync()
        with use_company(self.company):
            names = set(Employee.objects.values_list("first_name", flat=True))
        self.assertIn("Nihal", names)

    def test_blank_device_name_becomes_a_labelled_placeholder(self):
        # A blank name must never become a blank employee, and must never be
        # replaced with an invented one.
        self._sync()
        with use_company(self.company):
            names = set(Employee.objects.values_list("first_name", flat=True))
        self.assertIn("Device user 445961", names)

    def test_synced_enrollments_are_recognition_only(self):
        # Syncing recognises people; it must not grant attendance permission.
        self._sync()
        with use_company(self.company):
            for enrollment in DeviceEnrollment.objects.all():
                self.assertFalse(enrollment.assigned_device_authorized)
                self.assertTrue(enrollment.attendance_enabled)

    def test_drafts_are_marked_for_hr_review(self):
        self._sync()
        with use_company(self.company):
            employee = Employee.objects.get(first_name="Device user 445961")
        self.assertTrue(employee.metadata.get("created_from_device_sync"))
        self.assertTrue(employee.metadata.get("needs_hr_review"))

    def test_running_sync_twice_creates_no_duplicates(self):
        self._sync()
        created, skipped, errors = self._sync()
        self.assertEqual(created, [])
        self.assertEqual(errors, [])
        with use_company(self.company):
            self.assertEqual(DeviceEnrollment.objects.count(), 2)

    def test_already_mapped_users_are_left_alone(self):
        with use_company(self.company):
            employee = Employee.objects.create(first_name="Existing", last_name="Person")
            DeviceEnrollment.objects.create(
                device=self.device,
                employee=employee,
                device_user_id="445966",
                effective_from=timezone.now(),
                assigned_device_authorized=True,
            )
        created, skipped, errors = self._sync()
        self.assertEqual([c["pin"] for c in created], ["445961"])
        with use_company(self.company):
            kept = DeviceEnrollment.objects.get(device_user_id="445966")
        # The pre-existing grant must survive a sync.
        self.assertTrue(kept.assigned_device_authorized)
        self.assertEqual(kept.employee_id, employee.pk)

    def test_sync_refuses_when_there_is_no_organisation_structure(self):
        # Inventing a department to hold new people is HR's decision, not a
        # side-effect of plugging in a terminal.
        with use_company(self.company):
            # Only this company's adoption rows are removed. The root
            # catalogue is shared and is not a tenant's to delete.
            Designation.objects.all().delete()
            Department.objects.all().delete()
            with self.assertRaises(SyncNotPossible):
                sync_device_users(device=self.device)


class UserWriteCommandTests(TestCase):
    """Write-side syntax, established by probing the physical SenseFace 2A."""

    def setUp(self):
        model = _reference_data()
        self.company = Company.objects.create(code="A", slug="a", name="Company A")
        with use_company(self.company):
            branch = Branch.objects.create(code="HQ", name="HQ", is_default=True)
            self.device = BiometricDevice.objects.create(
                branch=branch,
                device_model=model,
                name="Main Entrance",
                serial_number="NYU7251601501",
                timezone="Asia/Dhaka",
                status=BiometricDevice.Status.ACTIVE,
            )

    def test_write_uses_the_capitalised_field_names(self):
        from devices.services.commands import build_user_update

        body = build_user_update(
            device_user_id="445966", name="Nihal", card_number="196793", privilege=14
        )
        # The lowercase spellings the device uses when *uploading* are silently
        # ignored on write and produce a row with an empty id.
        self.assertIn("Pin=445966", body)
        self.assertIn("Name=Nihal", body)
        self.assertIn("CardNo=196793", body)
        self.assertNotIn("pin=", body)
        self.assertNotIn("cardno=", body)

    def test_delete_is_keyed_on_pin_never_uid(self):
        from devices.services.commands import build_user_delete

        body = build_user_delete(device_user_id="445966")
        self.assertEqual(body, "DATA DELETE user Pin=445966")
        # A uid-keyed delete wipes every user on the device, faces included.
        self.assertNotIn("uid", body.lower())

    def test_delete_refuses_a_uid_keyed_body(self):
        from devices.services import commands

        original = commands.build_user_delete
        try:
            commands.build_user_delete = lambda **kw: "DATA DELETE user uid=5"
            entry, error = commands.queue_user_delete(
                device=self.device, device_user_id="5"
            )
        finally:
            commands.build_user_delete = original
        self.assertIsNone(entry)
        self.assertIn("could clear the device", error)

    def test_non_numeric_user_id_is_refused(self):
        from devices.services.commands import queue_user_delete, queue_user_push

        for bad in ("", "abc", "44;DELETE"):
            entry, error = queue_user_push(device=self.device, device_user_id=bad)
            self.assertIsNone(entry, bad)
            entry, error = queue_user_delete(device=self.device, device_user_id=bad)
            self.assertIsNone(entry, bad)

    def test_unknown_privilege_is_refused(self):
        from devices.services.commands import queue_user_push

        entry, error = queue_user_push(
            device=self.device, device_user_id="445966", privilege=99
        )
        self.assertIsNone(entry)
        self.assertIn("privilege", error)

    def test_push_is_delivered_on_the_next_poll(self):
        from devices.services.commands import queue_user_push, take_pending_commands

        queue_user_push(
            device=self.device, device_user_id="445966", name="Nihal"
        )
        body, issued = take_pending_commands(self.device)
        self.assertEqual(len(issued), 1)
        self.assertIn("DATA UPDATE user Pin=445966", body)
