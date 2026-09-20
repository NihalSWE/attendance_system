"""Attendance push 2.x (SenseFace 3A) tests — A16, 2026-09-15.

Written from traffic captured from the physical ZKTeco SenseFace 3A
(ZAM70-NF28VA-3.3.12, PushVersion 3.1.2S, announcing ``pushver=2.4.1``,
``DeviceType=att``), serial VGU6262600120. The payloads below are real device
output; the command forms were each answered ``Return=0`` on that device, and
the 3.x table form ``Return=-1004``.
"""

import datetime

from django.test import TestCase
from django.utils import timezone

from common.tenant import use_company
from devices.models import (
    BiometricDevice,
    DeviceModel,
    DeviceSyncState,
    DeviceVendor,
    PunchEvent,
)
from devices.services import protocol
from devices.services.commands import (
    SAFE_COMMANDS,
    catch_up_after_gap,
    pending_summary,
    queue_command,
    queue_user_delete,
    queue_user_push,
)
from devices.services.device_roster import build_roster
from organization.models import Branch
from tenants.models import Company

SN = "VGU6262600120"
# Answer to DATA QUERY USERINFO (every user), posted as table=OPERLOG.
USERS = (
    "USER PIN=1\tName=NIHAL\tPri=14\tPasswd=\tCard=196793\tGrp=1\tTZ=0000000100000000"
    "\tVerify=-1\tViceCard=\tExpires=0\tStartDatetime=0\tEndDatetime=0\n"
    "USER PIN=2\tName=RYHAN\tPri=0\tPasswd=\tCard=\tGrp=1\tTZ=0000000100000000"
    "\tVerify=-1\tViceCard=\tExpires=0\tStartDatetime=0\tEndDatetime=0\n"
)
# The operation log as the device sends it on its own (an enrolment).
OPLOG = "OPLOG 70\t0\t2026-09-14 19:05:35\t2\t0\t0\t0\n"
BIODATA = (
    "BIODATA Pin=1\tNo=6\tIndex=0\tValid=1\tDuress=0\tType=1\tMajorVer=13\tMinorVer=0"
    "\tFormat=0\tTmp=apUBEBgEJAsBAA0AAaR\n"
    "BIODATA Pin=2\tNo=6\tIndex=0\tValid=1\tDuress=0\tType=1\tMajorVer=13\tMinorVer=0"
    "\tFormat=0\tTmp=apUBEBgEBz8BAA0AAS8b\n"
)
ATTLOG = "3\t2026-09-15 15:35:43\t255\t1\t0\t0\t0\t0\t0\t0\t\n"


class Att2Case(TestCase):
    def setUp(self):
        vendor = DeviceVendor.objects.get_or_create(
            code="zkteco", defaults={"name": "ZKTeco", "adapter_key": "zkteco_adms_push"},
        )[0]
        model = DeviceModel.objects.get_or_create(
            vendor=vendor, model_code="senseface-3a",
            defaults={"name": "SenseFace 3A", "protocol": DeviceModel.Protocol.ADMS_PUSH},
        )[0]
        self.company = Company.objects.create(code="A", slug="a", name="Company A")
        with use_company(self.company):
            branch = Branch.objects.create(code="HQ", name="HQ", is_default=True)
            self.device = BiometricDevice.objects.create(
                branch=branch, device_model=model, name="Main Entrance",
                serial_number=SN, timezone="Asia/Dhaka",
                status=BiometricDevice.Status.ACTIVE,
            )

    def post(self, table, body, stamp="OpStamp"):
        return self.client.post(
            f"/iclock/cdata?SN={SN}&table={table}&{stamp}=9999",
            data=body, content_type="text/plain",
        )

    def handshake(self, pushver="2.4.1", device_type="att"):
        return self.client.get(
            f"/iclock/cdata?SN={SN}&options=all&pushver={pushver}&DeviceType={device_type}"
        )

    def reload(self):
        self.device.refresh_from_db()
        return self.device


class DialectTests(Att2Case):
    def test_the_handshake_is_remembered(self):
        self.handshake()
        device = self.reload()
        self.assertEqual(device.settings["announced"], {"pushver": "2.4.1", "device_type": "att"})
        self.assertEqual(protocol.dialect(device), protocol.ATT2)

    def test_a_3x_announcement_keeps_the_table_commands(self):
        self.handshake(pushver="3.1.2", device_type="acc")
        self.assertEqual(protocol.dialect(self.reload()), protocol.PUSH3)

    def test_without_a_recorded_handshake_the_2x_operation_log_gives_it_away(self):
        # A model the catalogue says nothing about: the 2.x operation log is
        # what identifies it. (A SenseFace 3A does not need this — its model
        # already means 2.x, see DialectWhenNothingWasAnnouncedTests.)
        from devices.models import DeviceModel

        with use_company(self.company):
            self.device.device_model = DeviceModel.objects.get_or_create(
                vendor=self.device.device_model.vendor, model_code="unknown-model",
                defaults={"name": "Unknown", "protocol": DeviceModel.Protocol.ADMS_PUSH})[0]
            self.device.save()
        self.assertEqual(protocol.dialect(self.device), protocol.PUSH3)
        self.post("OPERLOG", OPLOG)
        self.assertEqual(protocol.dialect(self.device), protocol.ATT2)


class RosterTests(Att2Case):
    def roster(self):
        with use_company(self.company):
            return {row["pin"]: row for row in build_roster(self.device)}

    def test_users_and_fingerprints_from_the_2x_upload(self):
        self.post("OPERLOG", OPLOG + USERS)
        self.post("BIODATA", BIODATA)
        roster = self.roster()
        self.assertEqual(roster["1"]["name"], "NIHAL")
        self.assertEqual(roster["1"]["card_number"], "196793")
        self.assertEqual(roster["1"]["privilege_label"], "Super Admin")
        self.assertEqual(roster["2"]["name"], "RYHAN")
        self.assertEqual((roster["1"]["fingerprint_count"], roster["2"]["fingerprint_count"]), (1, 1))
        # Sent twice, still counted once.
        self.post("BIODATA", BIODATA)
        roster = self.roster()
        self.assertEqual(roster["1"]["fingerprint_count"], 1)

    def test_a_number_seen_only_in_scans_is_listed_to_map(self):
        self.post("ATTLOG", ATTLOG, stamp="Stamp")
        roster = self.roster()
        self.assertTrue(roster["3"]["only_in_scans"])
        self.assertFalse(roster["3"]["is_mapped"])

    def test_the_users_page_shows_them(self):
        from django.contrib.auth import get_user_model
        from accounts.models import CompanyMembership

        admin = get_user_model().objects.create_user(email="admin@a.test")
        CompanyMembership.all_objects.create(company=self.company, user=admin,
                                             role="company_admin", status="active")
        self.post("OPERLOG", USERS)
        self.post("ATTLOG", ATTLOG, stamp="Stamp")
        self.client.force_login(admin)
        page = self.client.get(f"/devices/{self.device.public_id}/users/")
        self.assertContains(page, "NIHAL")
        self.assertContains(page, "RYHAN")
        self.assertContains(page, "name not received yet")
        self.assertContains(page, "Refresh user list")
        self.assertNotContains(page, "Push to device")


class CommandTests(Att2Case):
    def setUp(self):
        super().setUp()
        self.handshake()
        self.reload()

    def test_refresh_user_list_uses_the_2x_form(self):
        entry = queue_command(device=self.device, command_key="query_users")
        self.assertEqual(entry["body"], "DATA QUERY USERINFO")
        body = self.client.get(f"/iclock/getrequest?SN={SN}").content.decode()
        self.assertIn("DATA QUERY USERINFO", body)
        self.assertNotIn(SAFE_COMMANDS["query_users"], body)

    def test_attendance_history_is_asked_for_in_device_time(self):
        entry = queue_command(device=self.device, command_key="query_attlog")
        self.assertTrue(entry["body"].startswith("DATA QUERY ATTLOG StartTime="))
        self.assertIn("\tEndTime=", entry["body"])

    def test_requests_it_has_no_form_for_are_refused(self):
        self.assertIsNone(queue_command(device=self.device, command_key="query_biodata"))

    def test_user_writes_wait_until_measured_on_this_protocol(self):
        entry, error = queue_user_push(device=self.device, device_user_id="5", name="X")
        self.assertIsNone(entry)
        self.assertIn("not verified", error)
        entry, error = queue_user_delete(device=self.device, device_user_id="5")
        self.assertIsNone(entry)
        self.assertEqual(pending_summary(self.device), [])

    def test_history_is_replayed_without_counting_twice(self):
        self.post("ATTLOG", ATTLOG, stamp="Stamp")
        self.post("ATTLOG", "1\t2026-09-14 18:24:18\t255\t1\t0\t0\t0\t0\t0\t0\t\n" + ATTLOG, stamp="Stamp")
        with use_company(self.company):
            statuses = sorted(PunchEvent.objects.values_list("dedupe_status", flat=True))
        self.assertEqual(statuses, ["confirmed_duplicate", "unique", "unique"])


class CatchUpTests(Att2Case):
    def setUp(self):
        super().setUp()
        self.handshake()
        self.reload()

    def state(self):
        return DeviceSyncState.all_objects.get(device=self.device).state_data

    def test_a_reconnecting_2x_device_is_asked_for_what_it_missed(self):
        # First poll ever: asked.
        body = self.client.get(f"/iclock/getrequest?SN={SN}").content.decode()
        self.assertIn("DATA QUERY ATTLOG", body)
        self.assertIn("last_poll_at", self.state())
        self.assertIn("last_catch_up_at", self.state())
        # Polling on: nothing more.
        self.assertEqual(self.client.get(f"/iclock/getrequest?SN={SN}").content.decode().strip(), "OK")
        now = timezone.now()
        minutes = lambda n: now - datetime.timedelta(minutes=n)  # noqa: E731
        # A poll after a three-minute silence: asked.
        self.assertIsNotNone(catch_up_after_gap(self.device, (minutes(3), minutes(10)), now))
        # Polling steadily, asked recently: not asked.
        self.assertIsNone(catch_up_after_gap(self.device, (minutes(0.2), minutes(30)), now))
        # Polling steadily, but an hour since it was last asked: asked.
        entry = catch_up_after_gap(self.device, (minutes(0.2), minutes(61)), now)
        self.assertTrue(entry["body"].startswith("DATA QUERY ATTLOG StartTime="))

    def test_a_3x_device_is_left_alone(self):
        self.handshake(pushver="3.1.2", device_type="acc")
        self.reload()
        self.assertEqual(self.client.get(f"/iclock/getrequest?SN={SN}").content.decode().strip(), "OK")


class DialectWhenNothingWasAnnouncedTests(Att2Case):
    """A rebuilt database loses what the device announced (Ajay, 2026-09-20).

    The live 3A stayed registered, so it never announced again; the server
    fell back to the 3.x dialect and "Refresh user list" was refused with
    Return=-1004. The model catalogue and the administrator's override now
    answer that.
    """

    def test_the_3a_model_speaks_2x_by_default(self):
        with use_company(self.company):
            self.device.settings = {}       # nothing announced, nothing sent
            self.device.save()
            self.assertEqual(protocol.dialect(self.device), protocol.ATT2)
            entry = queue_command(device=self.device, command_key="query_users")
        self.assertEqual(entry["body"], "DATA QUERY USERINFO")

    def test_the_administrator_can_force_either_dialect(self):
        with use_company(self.company):
            self.device.settings = {"push_protocol": "3",
                                    "announced": {"pushver": "2.4.1", "device_type": "att"}}
            self.device.save()
            self.assertEqual(protocol.dialect(self.device), protocol.PUSH3)
            self.device.settings = {"push_protocol": "2",
                                    "announced": {"pushver": "3.1.2", "device_type": "acc"}}
            self.device.save()
            self.assertEqual(protocol.dialect(self.device), protocol.ATT2)

    def test_a_2a_with_nothing_announced_stays_on_3x(self):
        from devices.models import DeviceModel

        with use_company(self.company):
            self.device.device_model = DeviceModel.objects.get_or_create(
                vendor=self.device.device_model.vendor, model_code="senseface-2a",
                defaults={"name": "SenseFace 2A",
                          "protocol": DeviceModel.Protocol.ADMS_PUSH})[0]
            self.device.settings = {}
            self.device.save()
            self.assertEqual(protocol.dialect(self.device), protocol.PUSH3)
