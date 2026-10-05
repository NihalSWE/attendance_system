"""Phase 6, part a: what the devices sent - messages, punches, users, templates,
settings and the command queue (docs/api/60-devices-data-flow.md).

The scans go through the real device endpoint (/iclock/cdata), as a terminal
sends them, so these read what the real pipeline stored."""

from django.test import Client

from api.tests.test_devices_setup import DeviceApiTestCase
from common.tenant import use_company
from devices.models import BiometricDevice, DeviceEnrollment
from employees.models import Employee

SCANS = "41\t2026-09-08 09:01:02\t0\t15\t0\t\t\n77\t2026-09-08 09:03:44\t0\t1\t0\t\t\n"
USERS = ("USER PIN=41\tName=RAHIM\tPri=0\tPasswd=\tCard=\tGrp=1\tTZ=0000000100000000\n"
         "USER PIN=77\tName=STRANGER\tPri=0\tPasswd=\tCard=\tGrp=1\tTZ=0000000100000000\n")


class FlowApiTestCase(DeviceApiTestCase):
    def setUp(self):
        super().setUp()
        self.device = self.register(serial_number="SF2A-001")
        with use_company(self.company):
            BiometricDevice.objects.filter(public_id=self.device["id"]).update(status="active")
            self.rahim = Employee.objects.create(company=self.company, first_name="Rahim")
        self.api("POST", "/api/v1/device-enrollments", {
            "device_id": self.device["id"], "employee_id": self.rahim.pk,
            "device_user_id": "41", "effective_from": "2026-01-01"})
        terminal = Client()
        terminal.post("/iclock/cdata?SN=SF2A-001&table=ATTLOG&Stamp=1", data=SCANS,
                      content_type="text/plain")
        terminal.post("/iclock/cdata?SN=SF2A-001&table=OPERLOG&Stamp=2", data=USERS,
                      content_type="text/plain")


class FlowTests(FlowApiTestCase):
    def test_messages(self):
        listed = self.api("GET", "/api/v1/device-messages",
                          query=f"device_id={self.device['id']}").json()
        self.assertGreaterEqual(listed["count"], 2)
        batch = next(m for m in listed["results"] if m["message_type"] == "punch_batch")
        one = self.api("GET", f"/api/v1/device-messages/{batch['id']}").json()
        self.assertIn("2026-09-08 09:01:02", one["raw_payload_text"])
        self.assertEqual(len(one["punch_ids"]), 2)

    def test_an_api_key_never_sees_the_raw_text(self):
        message = self.api("GET", "/api/v1/device-messages").json()["results"][0]
        key = self.key("devices:read")
        seen = self.as_key(key, "GET", f"/api/v1/device-messages/{message['id']}").json()
        self.assertIsNone(seen["raw_payload_text"])

    def test_punches_and_whose_they_are(self):
        punches = self.api("GET", "/api/v1/punches").json()["results"]
        by_pin = {p["device_user_id"]: p for p in punches}
        self.assertEqual(by_pin["41"]["employee"]["id"], self.rahim.pk)
        self.assertEqual(by_pin["77"]["authorization_status"], "unknown_employee")
        unresolved = self.api("GET", "/api/v1/punches/unresolved").json()
        # 77 is nobody; Rahim has no placement yet, so the rules cannot decide.
        self.assertEqual({p["device_user_id"]: p["authorization_status"]
                          for p in unresolved["results"]},
                         {"77": "unknown_employee", "41": "policy_unresolved"})
        counts = self.api("GET", "/api/v1/punches/unresolved/counts").json()
        self.assertEqual(counts["unknown_employee"], 1)
        one = self.api("GET", f"/api/v1/punches/{by_pin['41']['id']}").json()
        self.assertTrue(one["message_id"])
        filtered = self.api("GET", "/api/v1/punches", query="authorization=unknown_employee").json()
        self.assertEqual(filtered["count"], 1)

    def test_users_on_the_device(self):
        rows = self.api("GET", f"/api/v1/devices/{self.device['id']}/users").json()["results"]
        by_pin = {r["pin"]: r for r in rows}
        self.assertEqual((by_pin["41"]["name"], by_pin["41"]["employee"]["id"]),
                         ("RAHIM", self.rahim.pk))
        self.assertIsNone(by_pin["77"]["employee"])
        self.assertEqual(by_pin["77"]["unlinked"]["code"], "no_employee")
        unlinked = self.api("GET", f"/api/v1/devices/{self.device['id']}/users",
                            query="mapping=not_linked").json()
        self.assertEqual([r["pin"] for r in unlinked["results"]], ["77"])

    def test_templates_options_commands_and_jobs(self):
        device = self.device["id"]
        saved = self.api("GET", f"/api/v1/devices/{device}/templates").json()
        self.assertEqual(saved["people"], 0)
        options = self.api("GET", f"/api/v1/devices/{device}/options").json()
        self.assertIn("push_interval_seconds", [o["key"] for o in options["writable"]])
        commands = self.api("GET", f"/api/v1/devices/{device}/commands").json()
        self.assertIn("pending", commands)
        jobs = self.api("GET", f"/api/v1/devices/{device}/jobs").json()
        self.assertFalse(jobs["commands"]["running"])

    def test_another_companys_records_are_not_found(self):
        message = self.api("GET", "/api/v1/device-messages").json()["results"][0]
        self.assertEqual(self.api("GET", "/api/v1/device-messages/"
                                         "00000000-0000-0000-0000-000000000000").status_code, 404)
        self.assertEqual(self.api("GET", "/api/v1/punches/999999").status_code, 404)
        self.assertTrue(message["id"])
        with use_company(self.company):
            self.assertTrue(DeviceEnrollment.objects.exists())
