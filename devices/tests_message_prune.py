"""Old copies of a device's user list are deleted only when nothing changes
(2026-10-18)."""

from io import StringIO
from unittest import mock

from django.core.management import call_command

from common.tenant import use_company
from devices.models import DeviceMessage
from devices.services import message_prune
from devices.services.device_roster import build_roster
from devices.tests_mapping import BIODATA, SN, USERS, MappingCase


class PruneCase(MappingCase):
    def setUp(self):
        super().setUp()
        for _ in range(3):                                   # the device re-sends its table
            self.upload(USERS, cmdid="")
            self.upload(BIODATA, table="biodata", cmdid="")
        self.upload(USERS.replace("name=Moin", "name=Moinul"), cmdid="")

    def uploads(self):
        return DeviceMessage.all_objects.filter(device=self.device).exclude(
            message_type__in=message_prune.NO_USER_ROWS)

    def roster(self):
        with use_company(self.company):
            return {r["pin"]: (r["name"], r["fingerprint_count"], r["face_count"])
                    for r in build_roster(self.device)}


class PruneTests(PruneCase):
    def test_copies_go_and_the_list_stays(self):
        before = self.roster()
        result = message_prune.prune(self.device)
        self.assertEqual(len(result.deletable), 5)           # 7 uploads: 3 user, 3 biodata, 1 user
        self.assertEqual(self.roster(), before)
        self.assertEqual(before["445900"][0], "Moinul")
        self.assertEqual(before["445962"][1:], (1, 1))
        self.assertEqual(self.uploads().count(), 2)          # newest users, newest templates

    def test_a_check_deletes_nothing(self):
        count = self.uploads().count()
        result = message_prune.prune(self.device, apply=False)
        self.assertTrue(result.deletable)
        self.assertEqual(self.uploads().count(), count)

    def test_punches_and_unknown_lines_are_kept(self):
        self.client.post(f"/iclock/cdata?SN={SN}&table=ATTLOG&Stamp=1",
                         data="445962\t2026-10-01 09:00:00\t0\t1\t0\t0\n",
                         content_type="text/plain")
        self.upload("OPLOG 4\t0\t2026-10-01 09:00:00\t0\t0\t0\t0\n", table="oplog", cmdid="")
        punches = DeviceMessage.all_objects.filter(device=self.device,
                                                   message_type__in=message_prune.NO_USER_ROWS)
        punch_count = punches.count()
        message_prune.prune(self.device)
        self.assertEqual(punches.count(), punch_count)
        self.assertTrue(self.uploads().filter(raw_payload_text__startswith="OPLOG").exists())

    def test_anything_that_would_change_the_list_deletes_nothing(self):
        count = self.uploads().count()
        newest = self.uploads().latest("pk").pk                 # the newest user rows
        real = message_prune.plan

        def too_much(device):
            result = real(device)
            result.deletable = sorted({*result.deletable, newest})
            return result

        with mock.patch.object(message_prune, "plan", side_effect=too_much):
            with self.assertRaises(message_prune.PruneMismatch):
                message_prune.prune(self.device)
        self.assertEqual(self.uploads().count(), count)
        self.assertEqual(self.roster()["445900"][0], "Moinul")

    def test_the_command_deletes_only_with_apply(self):
        count = self.uploads().count()
        out = StringIO()
        call_command("prune_device_messages", stdout=out)
        self.assertIn("would go", out.getvalue())
        call_command("prune_device_messages", "--check", stdout=out)
        self.assertEqual(self.uploads().count(), count)
        call_command("prune_device_messages", "--apply", stdout=out)
        self.assertEqual(self.uploads().count(), 2)
        self.assertIn("deleted", out.getvalue())
