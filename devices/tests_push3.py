"""PushSDK 3.x / access-control firmware tests.

Written from captures taken against the physical ZKTeco SenseFace 2A
(firmware ZAM70-NF24HA-Ver3.0.15, PushVersion 3.0.4S-20240809,
``DeviceType=acc``). The payload strings below are real device output with
identifiers left as captured, so a future firmware change that breaks these
assumptions fails loudly here.

These are still *automated* tests running against stored captures. They are
not a substitute for re-verifying on hardware; see the real-device notes in
docs/ for what was confirmed live.
"""

from django.test import Client, TestCase

from common.tenant import use_company
from devices.adapters.zkteco_adms import (
    ZKTecoAdmsAdapter,
    parse_device_info,
    parse_kv_row,
    uses_push3,
)
from devices.models import (
    BiometricDevice,
    DeviceModel,
    DeviceSyncState,
    DeviceVendor,
    PunchEvent,
)
from devices.services.commands import (
    SAFE_COMMANDS,
    pending_summary,
    queue_command,
    take_pending_commands,
)
from organization.models import Branch
from tenants.models import Company

# One real rtlog row plus the door-state row the same table carries.
RTLOG_PUNCH = (
    "time=2026-09-09 12:09:10\tpin=445966\tcardno=0\teventaddr=1\tevent=3"
    "\tinoutstatus=0\tverifytype=1\tindex=40\tsitecode=0\tlinkid=0"
    "\tmaskflag=0\ttemperature=0\tconvtemperature=0\r\n"
)
RTLOG_DOOR_EVENT = (
    "time=2026-09-09 12:09:11\tsensor=02\trelay=00"
    "\talarm=0100000000000000\tdoor=01\r\n"
)
REGISTRY_BODY = (
    "DeviceType=acc,~DeviceName=SenseFace 2A,"
    "FirmVer=ZAM70-NF24HA-Ver3.0.15,PushVersion=Ver 3.0.4S-20240809,"
    "~SerialNumber=NYU7251601501,FaceFunOn=1,FingerFunOn=1"
)


class PushVersionTests(TestCase):
    def test_three_point_x_is_detected(self):
        self.assertTrue(uses_push3("3.1.2"))
        self.assertTrue(uses_push3("3.0.4"))

    def test_older_and_unreadable_versions_fall_back_to_2x(self):
        # An unreadable version must not silently switch protocol dialect.
        self.assertFalse(uses_push3("2.4.1"))
        self.assertFalse(uses_push3(""))
        self.assertFalse(uses_push3("not-a-version"))


class PayloadParsingTests(TestCase):
    def test_kv_row_ignores_malformed_pairs(self):
        fields = parse_kv_row("pin=1\tgarbage\tevent=3")
        self.assertEqual(fields, {"pin": "1", "event": "3"})

    def test_device_info_block_is_parsed(self):
        info = parse_device_info(REGISTRY_BODY)
        self.assertEqual(info["FirmVer"], "ZAM70-NF24HA-Ver3.0.15")
        self.assertEqual(info["~SerialNumber"], "NYU7251601501")
        self.assertEqual(info["DeviceType"], "acc")


class RtlogParsingTests(TestCase):
    def setUp(self):
        self.adapter = ZKTecoAdmsAdapter()

    def _parse(self, body):
        return self.adapter.parse(
            path_name="cdata_upload",
            query={"table": "rtlog"},
            body_text=body,
            serial_number="NYU7251601501",
        )

    def test_user_event_becomes_a_punch(self):
        parsed = self._parse(RTLOG_PUNCH)
        self.assertEqual(len(parsed.punches), 1)
        punch = parsed.punches[0]
        self.assertEqual(punch.device_user_id, "445966")
        self.assertEqual(punch.verification_method, PunchEvent.VerificationMethod.FINGERPRINT)
        self.assertEqual(punch.punched_at_device_raw, "2026-09-09 12:09:10")

    def test_door_event_is_not_turned_into_a_punch(self):
        # Fabricating attendance from a door sensor would invent evidence.
        parsed = self._parse(RTLOG_DOOR_EVENT)
        self.assertEqual(parsed.punches, [])

    def test_vendor_punch_id_includes_timestamp_so_index_reset_cannot_collide(self):
        first = self._parse(RTLOG_PUNCH).punches[0]
        # Same event index, different day — must not look like the same punch.
        later = self._parse(
            RTLOG_PUNCH.replace("2026-09-09 12:09:10", "2026-09-10 12:09:10")
        ).punches[0]
        self.assertNotEqual(first.vendor_punch_id, later.vendor_punch_id)

    def test_unparsable_timestamp_is_kept_and_flagged(self):
        # The pre-NTP clock produces rows like this; they are still evidence.
        broken = RTLOG_PUNCH.replace(
            "2026-09-09 12:09:10", "1971--10--29 -23:-27:-54"
        )
        parsed = self._parse(broken)
        self.assertEqual(len(parsed.punches), 1)
        self.assertIsNone(parsed.punches[0].punched_at_device)
        self.assertEqual(
            parsed.punches[0].punched_at_device_raw, "1971--10--29 -23:-27:-54"
        )
        self.assertIn("unparsable timestamp", parsed.parse_error)


class Push3HandshakeTests(TestCase):
    def setUp(self):
        self.client = Client()
        vendor = DeviceVendor.objects.get_or_create(
            code="zkteco",
            defaults={"name": "ZKTeco", "adapter_key": "zkteco_adms_push"},
        )[0]
        model = DeviceModel.objects.get_or_create(
            vendor=vendor,
            model_code="senseface-2a",
            defaults={
                "name": "SenseFace 2A",
                "protocol": DeviceModel.Protocol.ADMS_PUSH,
                "capabilities": {"push": True, "face": True},
            },
        )[0]
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

    def test_registry_returns_a_registry_code(self):
        # Without this the firmware re-registers every 15s and never sends data.
        response = self.client.post(
            "/iclock/registry?SN=NYU7251601501",
            data=REGISTRY_BODY,
            content_type="text/plain",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("RegistryCode=", response.content.decode())

    def test_registry_records_firmware_version(self):
        self.client.post(
            "/iclock/registry?SN=NYU7251601501",
            data=REGISTRY_BODY,
            content_type="text/plain",
        )
        self.device.refresh_from_db()
        self.assertEqual(self.device.firmware_version, "ZAM70-NF24HA-Ver3.0.15")

    def test_push3_handshake_uses_numeric_transflag(self):
        response = self.client.get(
            "/iclock/cdata?SN=NYU7251601501&options=all&pushver=3.1.2"
        )
        body = response.content.decode()
        self.assertIn("TransFlag=1111000000", body)
        self.assertIn("ServerVer=", body)

    def test_push2_handshake_keeps_the_table_name_list(self):
        response = self.client.get(
            "/iclock/cdata?SN=NYU7251601501&options=all&pushver=2.4.1"
        )
        body = response.content.decode()
        self.assertIn("TransFlag=TransData AttLog", body)
        self.assertNotIn("ServerVer=", body)

    def test_rtlog_upload_creates_a_punch_event(self):
        response = self.client.post(
            "/iclock/cdata?SN=NYU7251601501&table=rtlog",
            data=RTLOG_PUNCH,
            content_type="text/plain",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PunchEvent.all_objects.count(), 1)
        self.assertEqual(
            PunchEvent.all_objects.first().device_user_id, "445966"
        )

    def test_replayed_rtlog_does_not_double_count(self):
        for _ in range(2):
            self.client.post(
                "/iclock/cdata?SN=NYU7251601501&table=rtlog",
                data=RTLOG_PUNCH,
                content_type="text/plain",
            )
        counted = PunchEvent.all_objects.exclude(
            dedupe_status=PunchEvent.DedupeStatus.CONFIRMED_DUPLICATE
        ).count()
        self.assertEqual(counted, 1)


class DeviceCommandTests(TestCase):
    def setUp(self):
        self.client = Client()
        vendor = DeviceVendor.objects.get_or_create(
            code="zkteco",
            defaults={"name": "ZKTeco", "adapter_key": "zkteco_adms_push"},
        )[0]
        model = DeviceModel.objects.get_or_create(
            vendor=vendor,
            model_code="senseface-2a",
            defaults={
                "name": "SenseFace 2A",
                "protocol": DeviceModel.Protocol.ADMS_PUSH,
                "capabilities": {"push": True},
            },
        )[0]
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

    def test_only_allowlisted_commands_can_be_queued(self):
        self.assertIsNone(queue_command(device=self.device, command_key="REBOOT"))
        self.assertIsNone(
            queue_command(device=self.device, command_key="DATA DELETE USERINFO")
        )
        self.assertEqual(pending_summary(self.device), [])

    def test_queued_command_is_delivered_on_the_next_poll(self):
        queue_command(device=self.device, command_key="query_users")
        response = self.client.get("/iclock/getrequest?SN=NYU7251601501")
        body = response.content.decode()
        self.assertIn(SAFE_COMMANDS["query_users"], body)
        self.assertTrue(body.startswith("C:"))

    def test_command_is_not_reissued_after_delivery(self):
        # A command left queued would be re-sent four times a minute.
        queue_command(device=self.device, command_key="query_users")
        self.client.get("/iclock/getrequest?SN=NYU7251601501")
        second = self.client.get("/iclock/getrequest?SN=NYU7251601501")
        self.assertEqual(second.content.decode().strip(), "OK")

    def test_duplicate_queueing_is_a_no_op(self):
        self.assertIsNotNone(queue_command(device=self.device, command_key="query_users"))
        self.assertIsNone(queue_command(device=self.device, command_key="query_users"))
        self.assertEqual(len(pending_summary(self.device)), 1)

    def test_poll_with_nothing_queued_returns_ok(self):
        response = self.client.get("/iclock/getrequest?SN=NYU7251601501")
        self.assertEqual(response.content.decode().strip(), "OK")

    def test_access_control_query_syntax_is_used(self):
        # The att-style 'DATA QUERY USERINFO' is rejected by acc firmware
        # with Return=-629; the table form returns a row count.
        self.assertIn("tablename=user", SAFE_COMMANDS["query_users"])
        self.assertNotIn("USERINFO", SAFE_COMMANDS["query_users"])
