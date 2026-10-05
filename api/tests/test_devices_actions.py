"""Phase 6, part b: telling the devices - commands, users, settings, the server
address, and putting people on devices (docs/api/60-devices-data-flow.md)."""

import datetime
from decimal import Decimal

from django.test import Client

from accounts.models import CompanyMembership
from api.tests.test_devices_setup import DeviceApiTestCase
from auditlog.models import AuditLog
from common.tenant import use_company
from devices.models import BiometricDevice, DeviceEnrollment, DeviceOutboxCommand
from employees.services import create_employee
from organization.catalogue import adopt_department, adopt_designation

USERS = ("USER PIN=1\tName=BOSS\tPri=14\tPasswd=\tCard=\tGrp=1\tTZ=0000000100000000\n"
         "USER PIN=41\tName=RAHIM\tPri=0\tPasswd=\tCard=\tGrp=1\tTZ=0000000100000000\n"
         "USER PIN=77\tName=STRANGER\tPri=0\tPasswd=\tCard=\tGrp=1\tTZ=0000000100000000\n")


class ActionApiTestCase(DeviceApiTestCase):
    def setUp(self):
        super().setUp()
        self.device = self.register(serial_number="SF3A-001")
        with use_company(self.company):
            BiometricDevice.objects.filter(public_id=self.device["id"]).update(
                status="active",
                settings={"announced": {"pushver": "3.1.2", "device_type": "acc"},
                          "push_interval_seconds": 10})
            department = adopt_department(self.hq, "SW", "Software")
            designation = adopt_designation(department, "DEV", "Developer")
        self.rahim = create_employee(
            company=self.company, first_name="Rahim", employee_code="41", branch=self.hq,
            department=department, designation=designation,
            effective_from=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
            pay_basis="monthly", base_rate=Decimal("20000"))["employee"]
        Client().post("/iclock/cdata?SN=SF3A-001&table=OPERLOG&Stamp=1", data=USERS,
                      content_type="text/plain")
        self.path = f"/api/v1/devices/{self.device['id']}"

    def waiting(self):
        with use_company(self.company):
            return DeviceOutboxCommand.objects.filter(
                device__public_id=self.device["id"], status="queued").count()


class CommandTests(ActionApiTestCase):
    def test_ask_for_data(self):
        first = self.api("POST", f"{self.path}/commands", {"command": "query_options"})
        self.assertEqual(first.status_code, 201, first.content)
        self.assertTrue(first.json()["queued"])
        again = self.api("POST", f"{self.path}/commands", {"command": "query_options"})
        self.assertEqual((again.status_code, again.json()["queued"]), (200, False))
        queue = self.api("GET", f"{self.path}/commands").json()
        self.assertGreaterEqual(queue["waiting"], 1)
        unknown = self.api("POST", f"{self.path}/commands", {"command": "reboot"})
        self.assertEqual(unknown.status_code, 422)

    def test_ask_about_one_user(self):
        # A Push 3.x device only answers its whole list: the panel's rule, said plainly.
        asked = self.api("POST", f"{self.path}/users/41/ask")
        self.assertEqual(asked.status_code, 422)
        self.assertIn("Refresh user list", asked.json()["error"]["message"])
        with use_company(self.company):
            BiometricDevice.objects.filter(public_id=self.device["id"]).update(
                settings={"announced": {"pushver": "2.4.1", "device_type": "att"}})
        asked = self.api("POST", f"{self.path}/users/41/ask")
        self.assertEqual(asked.status_code, 201, asked.content)

    def test_settings(self):
        set_ = self.api("POST", f"{self.path}/options/set",
                        {"key": "push_interval_seconds", "value": "15"})
        self.assertEqual(set_.status_code, 201, set_.content)
        self.assertTrue(AuditLog.objects.filter(action="device.option_queued").exists())
        bad = self.api("POST", f"{self.path}/options/set",
                       {"key": "push_interval_seconds", "value": "99999"})
        self.assertEqual(bad.status_code, 422)

    def test_the_server_address_is_checked_first(self):
        address = self.api("GET", f"{self.path}/server-address").json()
        self.assertFalse(address["active"])
        malformed = self.api("POST", f"{self.path}/server-address", {"address": "not a host!"})
        self.assertIn("address", malformed.json()["error"].get("fields", {}))
        nothing = self.api("POST", f"{self.path}/server-address/cancel")
        self.assertEqual(nothing.status_code, 422)

    def test_a_device_that_never_connected_cannot_be_repointed(self):
        with use_company(self.company):
            BiometricDevice.objects.filter(public_id=self.device["id"]).update(last_seen_at=None)
        refused = self.api("POST", f"{self.path}/server-address",
                           {"address": "https://attendance.example.com"})
        self.assertEqual(refused.status_code, 422)
        self.assertIn("never connected", refused.json()["error"]["message"])


class UserTests(ActionApiTestCase):
    def test_link_by_employee_id_then_send_and_remove(self):
        linked = self.api("POST", f"{self.path}/users/link-by-employee-id", {"all": True})
        self.assertEqual(linked.status_code, 200, linked.content)
        self.assertEqual([row["pin"] for row in linked.json()["linked"]], ["41"])
        before = self.waiting()
        sent = self.api("POST", f"{self.path}/users/41")
        self.assertEqual(sent.status_code, 201, sent.content)
        self.assertGreater(self.waiting(), before)
        removed = self.api("DELETE", f"{self.path}/users/77")
        self.assertEqual(removed.json()["removed"], ["77"])
        nobody = self.api("POST", f"{self.path}/users/77")
        self.assertEqual(nobody.status_code, 404)

    def test_the_last_super_admin_is_kept(self):
        result = self.api("POST", f"{self.path}/users/remove", {"pins": ["1"]}).json()
        self.assertEqual(result["removed"], [])
        self.assertEqual(result["kept"][0]["pin"], "1")

    def test_import_device_users_as_employees(self):
        result = self.api("POST", f"{self.path}/users/import", {"pins": ["77"]})
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual([e["name"] for e in result.json()["created"]], ["STRANGER"])

    def test_load_starts_once(self):
        first = self.api("POST", f"{self.path}/load")
        self.assertIn(first.status_code, (200, 201), first.content)
        jobs = self.api("GET", f"{self.path}/jobs").json()
        self.assertIn("commands", jobs)


class PeopleTests(ActionApiTestCase):
    def test_map_one_employee(self):
        mapped = self.api("POST", f"/api/v1/employees/{self.rahim.pk}/map",
                          {"device_id": self.device["id"]})
        self.assertEqual(mapped.status_code, 201, mapped.content)
        self.assertEqual((mapped.json()["user_number"], mapped.json()["sent_to_device"]),
                         ("41", True))
        with use_company(self.company):
            self.assertTrue(DeviceEnrollment.objects.filter(employee=self.rahim).exists())

    def test_bulk_map_and_send(self):
        bulk = self.api("POST", "/api/v1/employees/bulk-map", {"branch_id": self.hq.pk})
        self.assertEqual(bulk.status_code, 200, bulk.content)
        self.assertEqual(len(bulk.json()["mapped"]), 1)
        again = self.api("POST", "/api/v1/employees/bulk-map", {"branch_id": self.hq.pk}).json()
        self.assertEqual((again["mapped"], again["already"]), ([], 1))
        sent = self.api("POST", "/api/v1/employees/send-to-devices",
                        {"employee_ids": [self.rahim.pk]})
        self.assertEqual(len(sent.json()["sent"]), 1)

    def test_a_branch_manager_maps_in_their_branch_but_cannot_run_devices(self):
        manager_user = self.person("manny@example.test", role="manager")
        with use_company(self.company):
            CompanyMembership.all_objects.get(user=manager_user).allowed_branches.set([self.hq])
        manager = self.logged_in("manny@example.test")
        mapped = self.api("POST", f"/api/v1/employees/{self.rahim.pk}/map",
                          {"device_id": self.device["id"]}, session=manager)
        self.assertEqual(mapped.status_code, 201, mapped.content)
        refused = self.api("POST", f"{self.path}/commands", {"command": "query_users"},
                           session=manager)
        self.assertEqual(self.code_of(refused), "permission_denied")


class IngestTests(ActionApiTestCase):
    def body(self, **extra):
        return {"device_id": self.device["id"], "batch_id": "b1",
                "scans": [{"user_number": "41", "time": "2026-09-08 09:01:02", "method": "card"},
                          {"user_number": "77", "time": "2026-09-08T09:05:00"}], **extra}

    def test_a_machine_pushes_scans_once(self):
        key = self.key("punches:write")
        first = self.as_key(key, "POST", "/api/v1/ingest/punches", self.body())
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual((first.json()["replay"], first.json()["punches"]), (False, 2))
        again = self.as_key(key, "POST", "/api/v1/ingest/punches", self.body())
        self.assertEqual((again.status_code, again.json()["replay"]), (200, True))
        punches = self.api("GET", "/api/v1/punches", query=f"device_id={self.device['id']}")
        self.assertEqual(punches.json()["count"], 2)

    def test_bad_times_and_people_are_refused(self):
        key = self.key("punches:write")
        bad = self.as_key(key, "POST", "/api/v1/ingest/punches",
                          self.body(scans=[{"user_number": "41", "time": "08/09/2026 9:01"}]))
        self.assertIn("scans[0].time", bad.json()["error"]["fields"])
        person = self.api("POST", "/api/v1/ingest/punches", self.body())
        self.assertEqual(self.code_of(person), "permission_denied")
        reader = self.key("devices:read")
        self.assertEqual(self.code_of(self.as_key(reader, "POST", "/api/v1/ingest/punches",
                                                  self.body())), "scope_missing")
