"""Fingerprint/face templates: kept encrypted, copied through push_to_device.

The row shapes are the SenseFace 2A's (ZAM70-NF24HA-Ver3.0.15) ``biodata``
upload as captured 2026-09-19 (Type 1 fingerprint v13, Type 9 face v40.1);
the ``tmp`` values are made up. Real templates are biometric data and never go
in the repository.
"""

from cryptography.fernet import Fernet
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import CompanyMembership
from common.tenant import use_company
from devices.models import (
    BiometricDevice,
    DeviceModel,
    DeviceOutboxCommand,
    DeviceSyncState,
    DeviceUserTemplate,
    DeviceVendor,
)
from devices.services import commands, templates
from devices.services.commands import (
    COMMANDS_PER_POLL,
    TEST_USER_ID,
    build_template_update,
    note_results,
    parse_results,
    push_to_device,
    queue_command,
    take_pending_commands,
)
from organization.models import Branch
from tenants.models import Company

KEY = Fernet.generate_key().decode()
FINGER_TMP = "apUBEBgEFAKEFINGERTEMPLATE0000AAAA=="
FACE_TMP = "apUBFjYCFAKEFACETEMPLATE11110000=="
BIODATA = (
    f"biodata pin=445900\tno=6\tindex=0\tvalid=1\tduress=0\ttype=1\tmajorver=13"
    f"\tminorver=0\tformat=0\ttmp={FINGER_TMP}\n"
    f"biodata pin=445900\tno=0\tindex=0\tvalid=1\tduress=0\ttype=9\tmajorver=40"
    f"\tminorver=1\tformat=0\ttmp={FACE_TMP}\n"
)
SN = "NYU0000000001"


@override_settings(BIOMETRIC_TEMPLATE_KEY=KEY)
class TemplateCase(TestCase):
    def setUp(self):
        vendor = DeviceVendor.objects.get_or_create(
            code="zkteco", defaults={"name": "ZKTeco", "adapter_key": "zkteco_adms_push"},
        )[0]
        self.model = DeviceModel.objects.get_or_create(
            vendor=vendor, model_code="senseface-2a",
            defaults={"name": "SenseFace 2A", "protocol": DeviceModel.Protocol.ADMS_PUSH},
        )[0]
        self.other_model = DeviceModel.objects.get_or_create(
            vendor=vendor, model_code="senseface-3a",
            defaults={"name": "SenseFace 3A", "protocol": DeviceModel.Protocol.ADMS_PUSH},
        )[0]
        self.company = Company.objects.create(code="A", slug="a", name="Company A")
        with use_company(self.company):
            self.branch = Branch.objects.create(code="HQ", name="HQ", is_default=True)
            self.device = self._device(SN, self.model)
            self.second = self._device("NYU0000000002", self.model)

    def _device(self, serial, model):
        return BiometricDevice.objects.create(
            branch=self.branch, device_model=model, name=serial, serial_number=serial,
            timezone="Asia/Dhaka", status=BiometricDevice.Status.ACTIVE,
            settings={"announced": {"pushver": "3.1.2", "device_type": "acc"}},
        )

    def upload(self, body=BIODATA, serial=SN):
        return self.client.post(
            f"/iclock/cdata?SN={serial}&table=tabledata&tablename=biodata&count=2",
            data=body, content_type="text/plain",
        )

    def pending(self, device=None):
        return commands.pending_summary(device or self.device, limit=100)


class SavingTests(TemplateCase):
    def test_upload_saves_templates_encrypted(self):
        self.assertEqual(self.upload().status_code, 200)
        rows = DeviceUserTemplate.all_objects.filter(device=self.device).order_by("vendor_type")
        self.assertEqual(
            [(r.device_user_id, r.bio_type, r.vendor_type, r.major_version) for r in rows],
            [("445900", "fingerprint", "1", "13"), ("445900", "face", "9", "40")],
        )
        finger = rows[0]
        self.assertNotIn(FINGER_TMP.encode(), bytes(finger.template_encrypted))
        self.assertEqual(templates.decrypt(finger.template_encrypted), FINGER_TMP)
        self.assertEqual(finger.device_model, self.model)

    def test_same_upload_twice_changes_nothing(self):
        self.upload()
        self.assertEqual(templates.save_from_payload(self.device, BIODATA), (0, 0, 2))

    def test_newer_template_replaces_the_saved_one(self):
        self.upload()
        self.upload(BIODATA.replace(FACE_TMP, "apUBFjYCNEWFACE=="))
        self.assertEqual(templates.save_from_messages(self.device), (0, 0, 2))
        _, face = templates.templates_for(self.device, "445900")
        self.assertEqual(face["template"], "apUBFjYCNEWFACE==")

    def test_row_without_user_number_is_skipped(self):
        added, _, _ = templates.save_from_payload(
            self.device, BIODATA.replace("pin=445900", "pin=", 1)
        )
        self.assertEqual(added, 1)

    def test_saving_from_stored_messages(self):
        with override_settings(BIOMETRIC_TEMPLATE_KEY=""):
            self.upload()  # stored, but no key: nothing kept
        self.assertFalse(DeviceUserTemplate.all_objects.exists())
        self.assertEqual(templates.save_from_messages(self.device), (2, 0, 0))

    @override_settings(BIOMETRIC_TEMPLATE_KEY="")
    def test_no_key_refuses_and_upload_still_acknowledged(self):
        self.assertEqual(self.upload().status_code, 200)
        self.assertFalse(DeviceUserTemplate.all_objects.exists())
        with self.assertRaises(templates.TemplateKeyMissing):
            templates.save_from_payload(self.device, BIODATA)
        self.assertIn("BIOMETRIC_TEMPLATE_KEY", templates.key_problem())

    @override_settings(BIOMETRIC_TEMPLATE_KEY="not-a-key")
    def test_bad_key_refuses(self):
        with self.assertRaises(templates.TemplateKeyMissing):
            templates.save_from_payload(self.device, BIODATA)

    def test_other_key_cannot_read(self):
        self.upload()
        with override_settings(BIOMETRIC_TEMPLATE_KEY=Fernet.generate_key().decode()):
            with self.assertRaises(templates.TemplateKeyMissing):
                templates.templates_for(self.device, "445900")


class PushTests(TemplateCase):
    def setUp(self):
        super().setUp()
        self.upload()
        self.finger, self.face = templates.templates_for(self.device, "445900")

    def test_template_body(self):
        self.assertEqual(
            build_template_update(device_user_id="99999", template=self.finger),
            "DATA UPDATE biodata Pin=99999\tNo=6\tIndex=0\tValid=1\tDuress=0\tType=1"
            f"\tMajorVer=13\tMinorVer=0\tFormat=0\tTmp={FINGER_TMP}",
        )

    def test_user_then_templates_queued_together(self):
        entries, error = push_to_device(
            self.device, TEST_USER_ID, name="TEST", card="12345",
            finger_template=self.finger, face_template=self.face,
        )
        self.assertEqual(error, "")
        self.assertEqual(
            [e["key"] for e in entries],
            ["push_user:99999", "push_access:99999", "push_template:99999:1:6:0",
             "push_template:99999:9:0:0"],
        )
        self.assertEqual(
            entries[0]["body"],
            "DATA UPDATE user Pin=99999\tName=TEST\tCardNo=12345\tPrivilege=0\tGrp=1",
        )
        self.assertEqual(
            entries[1]["body"],
            "DATA UPDATE userauthorize Pin=99999\tAuthorizeTimezoneId=1\tAuthorizeDoorId=1",
        )

    def test_queued_template_is_encrypted_until_handed_over(self):
        push_to_device(self.device, TEST_USER_ID, finger_template=self.finger)
        stored = str(DeviceSyncState.all_objects.get(device=self.device).state_data)
        self.assertNotIn(FINGER_TMP, stored)

        body, issued = take_pending_commands(self.device)
        self.assertIn(f"Tmp={FINGER_TMP}", body)
        self.assertTrue(body.startswith(f"C:{issued[0]['id']}:DATA UPDATE user Pin=99999"))
        stored = str(DeviceSyncState.all_objects.get(device=self.device).state_data)
        self.assertNotIn(FINGER_TMP, stored)

    def test_unmeasured_template_type_only_to_the_test_user(self):
        palm = {**self.finger, "type": "8"}
        entries, error = push_to_device(self.device, "445900", finger_template=palm)
        self.assertEqual(entries, [])
        self.assertIn(TEST_USER_ID, error)
        self.assertEqual(self.pending(), [])
        entries, error = push_to_device(self.device, TEST_USER_ID, finger_template=palm)
        self.assertEqual((len(entries), error), (3, ""))

    def test_finger_and_face_go_to_a_real_user(self):
        entries, error = push_to_device(
            self.device, "445900", finger_template=self.finger, face_template=self.face
        )
        self.assertEqual(error, "")
        self.assertEqual([e["key"] for e in entries][-2:],
                         ["push_template:445900:1:6:0", "push_template:445900:9:0:0"])

    def test_fingerprint_goes_to_a_real_user(self):
        entries, error = push_to_device(
            self.device, "445900", name="Moin", card="8868366", finger_template=self.finger
        )
        self.assertEqual(error, "")
        self.assertEqual([e["key"] for e in entries],
                         ["push_user:445900", "push_access:445900", "push_template:445900:1:6:0"])

    def test_user_record_alone_may_go_to_anyone(self):
        entries, error = push_to_device(self.device, "445900", name="Moin", card="8868366")
        self.assertEqual((len(entries), error), (2, ""))

    def test_door_permission_when_the_device_type_is_not_known(self):
        # After a database rebuild the device does not register again, so its
        # type is unknown (seen 2026-09-19): it must still get the permission.
        self.device.settings = {"push_protocol": "auto"}
        self.device.save()
        entries, _ = push_to_device(self.device, "445990", name="Rayhan")
        self.assertEqual([e["key"] for e in entries], ["push_user:445990", "push_access:445990"])

    def test_no_door_permission_for_a_time_attendance_device(self):
        self.device.settings = {"announced": {"pushver": "3.1.2", "device_type": "att"}}
        self.device.save()
        entries, _ = push_to_device(self.device, "445900", name="Moin")
        self.assertEqual([e["key"] for e in entries], ["push_user:445900"])

    def test_templates_copy_to_another_device_of_the_same_model(self):
        entries, error = push_to_device(
            self.second, TEST_USER_ID, finger_template=self.finger, face_template=self.face
        )
        self.assertEqual((len(entries), error), (4, ""))

    def test_other_model_refused(self):
        with use_company(self.company):
            other = self._device("VGU0000000001", self.other_model)
        entries, error = push_to_device(other, TEST_USER_ID, face_template=self.face)
        self.assertEqual(entries, [])
        self.assertIn("same model", error)

    def test_2x_device_takes_only_the_test_user(self):
        # Nothing is measured on the 2.x protocol yet, so a real person is
        # refused there and only the test user goes, to measure it.
        with use_company(self.company):
            att2 = self._device("NYU0000000003", self.model)
        att2.settings = {"announced": {"pushver": "2.4.1", "device_type": "att"}}
        att2.save()
        entries, error = push_to_device(att2, "445900", name="Moin")
        self.assertEqual(entries, [])
        self.assertIn("not measured", error)
        entries, error = push_to_device(att2, TEST_USER_ID, name="TEST")
        self.assertEqual((len(entries), error), (1, ""))
        self.assertTrue(entries[0]["body"].startswith("DATA UPDATE USERINFO PIN=99999"))

    def test_bad_input_refused(self):
        for kwargs in ({"device_user_id": "12a"}, {"device_user_id": ""},
                       {"device_user_id": "1", "role": 5}, {"device_user_id": "1", "card": "x1"}):
            entries, error = push_to_device(self.device, **kwargs)
            self.assertEqual(entries, [], kwargs)
            self.assertTrue(error, kwargs)

    def test_all_or_nothing_when_the_queue_is_full(self):
        with mock.patch.object(commands, "OUTBOX_LIMIT", 5):
            push_to_device(self.device, "1", name="x")
            push_to_device(self.device, "2", name="y")
            entries, error = push_to_device(
                self.device, TEST_USER_ID, finger_template=self.finger, face_template=self.face
            )
        self.assertEqual(entries, [])
        self.assertIn("writes waiting", error)
        self.assertEqual(len(self.pending()), 4)


class HandOverAndResultTests(TemplateCase):
    def test_a_few_commands_per_check_in(self):
        for n in range(COMMANDS_PER_POLL + 2):
            commands._queue_raw(device=self.device, key=f"k{n}", body=f"DATA QUERY {n}")
        _, first = take_pending_commands(self.device)
        self.assertEqual([e["key"] for e in first], [f"k{n}" for n in range(COMMANDS_PER_POLL)])
        _, second = take_pending_commands(self.device)
        self.assertEqual(len(second), 2)
        self.assertEqual(take_pending_commands(self.device), ("", []))

    def test_server_address_pair_is_never_split(self):
        for n in range(COMMANDS_PER_POLL):
            commands._queue_raw(device=self.device, key=f"k{n}", body=f"DATA QUERY {n}")
        commands._queue_raw(device=self.device, key="set_server_address:port",
                            body="SET OPTION IclockSvrPort=443")
        commands._queue_raw(device=self.device, key="set_server_address",
                            body="SET OPTION IclockSvrIP=example.test")
        _, issued = take_pending_commands(self.device)
        self.assertEqual(len(issued), COMMANDS_PER_POLL + 2)

    def test_answers_are_remembered_against_their_commands(self):
        queue_command(device=self.device, command_key="query_biodata")
        push_to_device(self.device, TEST_USER_ID, name="TEST")
        _, issued = take_pending_commands(self.device)
        first, second = issued[0]["id"], issued[1]["id"]
        response = self.client.post(
            f"/iclock/devicecmd?SN={SN}",
            data=f"ID={first}&Return=8&CMD=DATA QUERY\nID={second}&Return=0&CMD=DATA UPDATE\n",
            content_type="text/plain",
        )
        self.assertEqual(response.status_code, 200)
        results = commands.recent_results(self.device)
        self.assertEqual([(r["id"], r["return"]) for r in results], [(second, "0"), (first, "8")])
        self.assertTrue(results[0]["command"].startswith("DATA UPDATE user Pin=99999"))

    def test_template_answer_does_not_keep_the_template(self):
        self.upload()
        finger, _ = templates.templates_for(self.device, "445900")
        push_to_device(self.device, TEST_USER_ID, finger_template=finger)
        _, issued = take_pending_commands(self.device)
        note_results(self.device, f"ID={issued[2]['id']}&Return=0&CMD=DATA UPDATE")
        stored = str(DeviceSyncState.all_objects.get(device=self.device).state_data)
        self.assertNotIn(FINGER_TMP, stored)
        row = DeviceOutboxCommand.all_objects.get(device=self.device, command_id=issued[2]["id"])
        self.assertEqual((row.status, row.body, row.body_encrypted), ("done", "", None))
        self.assertTrue(row.description.endswith("Tmp=…"))

    def test_missing_key_drops_only_the_template(self):
        self.upload()
        finger, _ = templates.templates_for(self.device, "445900")
        push_to_device(self.device, TEST_USER_ID, name="TEST", finger_template=finger)
        with override_settings(BIOMETRIC_TEMPLATE_KEY=""):
            body, issued = take_pending_commands(self.device)
        self.assertEqual([e["key"] for e in issued], ["push_user:99999", "push_access:99999"])
        self.assertNotIn("biodata", body)
        result = commands.recent_results(self.device)[0]
        self.assertEqual(result["key"], "push_template:99999:1:6:0")
        self.assertFalse(result["ok"])

    def test_parse_results_skips_junk(self):
        self.assertEqual(parse_results("ID=3&Return=-1004&CMD=DATA\nnonsense\n"),
                         [(3, "-1004", "DATA")])


class ScreenTests(TemplateCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(
            email="admin@example.test", password="pw-12345678"
        )
        CompanyMembership.all_objects.create(
            company=self.company, user=self.user, role="company_admin", status="active"
        )
        self.client.force_login(self.user)
        self.url = reverse("devices:device_users", args=[self.device.public_id])
        self.client.post(
            f"/iclock/cdata?SN={SN}&table=tabledata&tablename=user&count=1",
            data="user uid=14\tcardno=8868366\tpin=445900\tpassword=\tgroup=1\tstarttime=0"
                 "\tendtime=0\tname=Moin\tprivilege=0\tdisable=0\tverify=0\n",
            content_type="text/plain",
        )

    def test_page_shows_saved_templates_and_trial(self):
        self.upload()
        page = self.client.get(self.url).content.decode()
        self.assertIn("1 finger · face", page)
        # Faces are measured on this model, so the face trial is not offered.
        self.assertNotIn("Write test user 99999 to the device", page)

    def test_save_button_saves_and_asks_the_device(self):
        with override_settings(BIOMETRIC_TEMPLATE_KEY=""):
            self.upload()
        response = self.client.post(
            reverse("devices:device_templates_save", args=[self.device.public_id]), follow=True
        )
        self.assertContains(response, "Saved 2 new")
        self.assertEqual([e["key"] for e in self.pending()], ["query_biodata"])

    @override_settings(BIOMETRIC_TEMPLATE_KEY="")
    def test_missing_key_is_shown(self):
        page = self.client.get(self.url).content.decode()
        self.assertIn("Fingerprints and faces are not being saved", page)

    def test_trial_queues_the_test_user(self):
        self.upload()
        response = self.client.post(
            reverse("devices:device_template_trial", args=[self.device.public_id]),
            {"source": "445900", "name": "TEST 99999", "finger": "on", "face": "on"},
            follow=True,
        )
        self.assertContains(response, "Queued test user 99999")
        self.assertEqual(
            [e["key"] for e in self.pending()],
            ["push_user:99999", "push_access:99999", "push_template:99999:1:6:0",
             "push_template:99999:9:0:0"],
        )

    def test_trial_without_saved_templates(self):
        response = self.client.post(
            reverse("devices:device_template_trial", args=[self.device.public_id]),
            {"source": "445900", "finger": "on"}, follow=True,
        )
        self.assertContains(response, "has no saved fingerprint or face")
        self.assertEqual(self.pending(), [])
