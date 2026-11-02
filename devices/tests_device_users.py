"""Each device's user list kept as rows (2026-10-18).

The Device users page used to work the list out from every upload on every
view; a live device with 4,644 uploads (195 MB) took ten seconds, twice. The
rows must say exactly what the old way said.
"""

from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from common.tenant import use_company
from devices.models import DeviceMessage, DeviceUser, DeviceUserSync
from devices.services import device_users
from devices.services.device_roster import build_roster, roster_from_messages
from devices.tests_mapping import BIODATA, SN, USERS, MappingCase

FIELDS = ("pin", "name", "card_number", "privilege_code", "fingerprint_count", "face_count",
          "has_photo", "only_in_scans", "removed_from_device", "is_mapped")


class DeviceUserCase(MappingCase):
    def roster(self, device=None):
        with use_company(self.company):
            return {r["pin"]: r for r in build_roster(device or self.device)}

    def assert_same_as_before(self, device=None):
        with use_company(self.company):
            new = [tuple(r[k] for k in FIELDS) for r in build_roster(device or self.device)]
            old = [tuple(r[k] for k in FIELDS) for r in roster_from_messages(device or self.device)]
        self.assertEqual(new, old)


class ArrivalTests(DeviceUserCase):
    def test_an_upload_is_applied_when_it_arrives(self):
        self.upload(USERS)
        rows = {r.pin: r for r in DeviceUser.all_objects.filter(device=self.device)}
        self.assertEqual(rows["445962"].name, "Ajay")
        self.assertEqual(rows["445900"].card_number, "8868366")
        newest = DeviceMessage.all_objects.filter(device=self.device).latest("pk").pk
        self.assertEqual(DeviceUserSync.all_objects.get(device=self.device).through_message_id,
                         newest)
        self.assert_same_as_before()

    def test_templates_count_once_however_often_sent(self):
        self.upload(USERS)
        for _ in range(3):
            self.upload(BIODATA, table="biodata", cmdid="")
        ajay = self.roster()["445962"]
        self.assertEqual((ajay["fingerprint_count"], ajay["face_count"]), (1, 1))
        self.assert_same_as_before()

    def test_the_newest_upload_wins(self):
        self.upload(USERS)
        self.upload(USERS.replace("name=Moin", "name=Moinul"), cmdid="")
        self.assertEqual(self.roster()["445900"]["name"], "Moinul")
        self.assert_same_as_before()

    def test_a_number_missing_from_the_latest_list_is_removed(self):
        self.upload(USERS, cmdid="1")
        without_777 = "\n".join(line for line in USERS.splitlines() if "pin=777" not in line)
        self.upload(without_777, cmdid="2")
        roster = self.roster()
        self.assertTrue(roster["777"]["removed_from_device"])
        self.assertFalse(roster["445962"]["removed_from_device"])
        self.assert_same_as_before()
        # Added again on the terminal afterwards: on the device after all.
        self.upload([line for line in USERS.splitlines() if "pin=777" in line][0], cmdid="")
        self.assertFalse(self.roster()["777"]["removed_from_device"])
        self.assert_same_as_before()

    def test_a_scan_from_an_unknown_number_is_listed(self):
        self.upload(USERS)
        self.client.post(f"/iclock/cdata?SN={SN}&table=ATTLOG&Stamp=1",
                         data="999\t2026-10-01 09:00:00\t0\t1\t0\t0\n", content_type="text/plain")
        self.assertTrue(self.roster()["999"]["only_in_scans"])
        self.assert_same_as_before()

    def test_ingestion_never_fails_because_of_the_list(self):
        with mock.patch.object(device_users, "record_message", side_effect=RuntimeError("boom")):
            response = self.upload(USERS)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(DeviceMessage.all_objects.filter(device=self.device).exists())
        # The next reader catches up what was missed.
        self.assertEqual(self.roster()["445962"]["name"], "Ajay")


class CatchUpTests(DeviceUserCase):
    def test_uploads_stored_without_the_list_are_caught_up_on_read(self):
        with mock.patch.object(device_users, "record_message"):
            self.upload(USERS)
            self.upload(BIODATA, table="biodata", cmdid="")
        self.assertFalse(DeviceUser.all_objects.filter(device=self.device).exists())
        ajay = self.roster()["445962"]
        self.assertEqual((ajay["name"], ajay["fingerprint_count"]), ("Ajay", 1))

    def test_reading_again_does_not_read_the_uploads(self):
        self.upload(USERS)
        self.roster()
        with CaptureQueriesContext(connection) as queries:
            self.roster()
        self.assertFalse([q for q in queries.captured_queries
                          if "raw_payload_text" in q["sql"]])

    def test_the_page_reads_the_rows(self):
        self.upload(USERS)
        self.client.force_login(self.admin)
        session = self.client.session
        session["active_company_id"] = self.company.pk
        session.save()
        page = self.client.get(reverse("devices:device_users", args=[self.device.public_id]))
        self.assertContains(page, "Moin")
        self.assertContains(page, "445962")


class CommandTests(DeviceUserCase):
    def test_rebuild_and_check(self):
        self.upload(USERS)
        self.upload(BIODATA, table="biodata", cmdid="")
        DeviceUser.all_objects.filter(device=self.device).update(name="wrong")
        out = StringIO()
        call_command("rebuild_device_users", "--check", stdout=out)
        self.assertIn("same as before", out.getvalue())
        self.assertEqual(self.roster()["445962"]["name"], "Ajay")
