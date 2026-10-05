"""Phase 5: devices - setting them up (docs/api/50-devices-setup.md)."""

import datetime

from accounts.models import CompanyMembership
from api.tests.test_company import CompanyApiTestCase
from auditlog.models import AuditLog
from common.tenant import use_company
from devices.models import BiometricDevice, DeviceEnrollment, DeviceModel, DeviceVendor
from employees.models import Employee
from organization.catalogue import adopt_department


class DeviceApiTestCase(CompanyApiTestCase):
    def setUp(self):
        super().setUp()
        vendor = DeviceVendor.objects.get_or_create(
            code="zkteco", defaults={"name": "ZKTeco", "adapter_key": "zkteco_adms_push"})[0]
        self.model = DeviceModel.objects.get_or_create(
            vendor=vendor, model_code="senseface-2a",
            defaults={"name": "SenseFace 2A", "protocol": DeviceModel.Protocol.ADMS_PUSH})[0]

    def register(self, **values):
        body = {"branch_id": self.hq.pk, "name": "Main gate", "device_model_id": self.model.pk,
                "serial_number": "CQZ1", "timezone": "Asia/Dhaka", **values}
        response = self.api("POST", "/api/v1/devices", body)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def fields(self, response):
        return response.json()["error"].get("fields", {})


class DeviceTests(DeviceApiTestCase):
    def test_register_change_and_retire(self):
        device = self.register()
        self.assertEqual((device["status"], device["comm_key"], device["has_comm_key"],
                          device["connection"]["key"]),
                         ("pending", None, False, "not_connected"))
        self.assertTrue(AuditLog.objects.filter(action="device.registered").exists())
        changed = self.api("PATCH", f"/api/v1/devices/{device['id']}",
                           {"name": "Main gate (north)", "push_interval_seconds": 15})
        self.assertEqual(changed.status_code, 200, changed.content)
        self.assertEqual((changed.json()["name"], changed.json()["push_interval_seconds"],
                          changed.json()["serial_number"]), ("Main gate (north)", 15, "CQZ1"))
        entry = AuditLog.objects.get(action="device.updated")
        self.assertEqual(entry.before_data["name"], "Main gate")
        retired = self.api("POST", f"/api/v1/devices/{device['id']}/retire")
        self.assertEqual(retired.json()["status"], "retired")
        again = self.api("POST", f"/api/v1/devices/{device['id']}/retire")
        self.assertEqual(again.status_code, 422)

    def test_a_typed_key_is_shown_once(self):
        device = self.register(comm_key="s3cret")
        self.assertEqual((device["comm_key"], device["has_comm_key"]), ("s3cret", True))
        shown = self.api("GET", f"/api/v1/devices/{device['id']}").json()
        self.assertNotIn("comm_key", shown)
        removed = self.api("PATCH", f"/api/v1/devices/{device['id']}", {"remove_comm_key": True})
        self.assertFalse(removed.json()["has_comm_key"])

    def test_one_serial_one_company(self):
        self.register()
        twice = self.api("POST", "/api/v1/devices", {
            "branch_id": self.hq.pk, "name": "Copy", "serial_number": "CQZ1",
            "timezone": "Asia/Dhaka"})
        self.assertIn("serial_number", self.fields(twice))
        with use_company(self.other):
            BiometricDevice.objects.create(company=self.other, branch=self.other_branch,
                                           name="Theirs", serial_number="OTHER1", device_model=self.model,
                                           timezone="Asia/Dhaka", status="active")
        check = self.api("GET", "/api/v1/devices/serial-check", query="serial=OTHER1").json()
        self.assertFalse(check["ok"])
        self.assertIn("Other Ltd", check["message"])
        free = self.api("GET", "/api/v1/devices/serial-check", query="serial=NEW9").json()
        self.assertTrue(free["ok"])
        elsewhere = self.api("POST", "/api/v1/devices", {
            "branch_id": self.hq.pk, "name": "X", "serial_number": "OTHER1",
            "timezone": "Asia/Dhaka"})
        self.assertIn("serial_number", self.fields(elsewhere))

    def test_setup_lines_and_models(self):
        device = self.register()
        setup = self.api("GET", f"/api/v1/devices/{device['id']}/setup").json()
        self.assertEqual(setup["lines"][0]["setting"], "Server address / ADMS server")
        self.assertTrue(setup["endpoint_url"].endswith("/iclock/cdata"))
        models = self.api("GET", "/api/v1/device-models").json()["results"]
        self.assertIn("SenseFace 2A", [m["name"] for m in models])

    def test_connection_test(self):
        device = self.register()
        started = self.api("POST", f"/api/v1/devices/{device['id']}/test-connection")
        self.assertEqual(started.status_code, 201, started.content)
        body = started.json()
        status = self.api("GET", f"/api/v1/devices/{device['id']}/test-connection",
                          query=f"since={body['started_at'].replace('+', '%2B')}"
                                f"&command_id={body['command_id'] or ''}").json()
        self.assertFalse(status["checked_in"])
        board = self.api("GET", "/api/v1/devices/connections").json()["results"]
        self.assertEqual(board[0]["device"]["id"], device["id"])

    def test_only_the_owner_or_an_unrestricted_admin(self):
        self.person("hr@example.test", role="hr")
        hr = self.logged_in("hr@example.test")
        self.assertEqual(self.code_of(self.api("GET", "/api/v1/devices", session=hr)),
                         "permission_denied")
        CompanyMembership.all_objects.filter(user=self.admin_user).first().allowed_branches.add(
            self.hq)
        self.assertEqual(self.api("GET", "/api/v1/devices").status_code, 403)

    def test_another_companys_device_is_not_found(self):
        with use_company(self.other):
            theirs = BiometricDevice.objects.create(
                company=self.other, branch=self.other_branch, name="Theirs",
                serial_number="T1", timezone="Asia/Dhaka", device_model=self.model)
        self.assertEqual(self.api("GET", f"/api/v1/devices/{theirs.public_id}").status_code, 404)

    def test_api_keys_need_the_scope(self):
        key = self.key("devices:read")
        self.assertEqual(self.as_key(key, "GET", "/api/v1/devices").status_code, 200)
        refused = self.as_key(key, "POST", "/api/v1/devices", {"name": "x"})
        self.assertEqual(self.code_of(refused), "scope_missing")


class LinkTests(DeviceApiTestCase):
    def setUp(self):
        super().setUp()
        self.device = self.register()
        with use_company(self.company):
            self.department = adopt_department(self.hq, "SW", "Software")
            self.rahim = Employee.objects.create(company=self.company, first_name="Rahim")

    def test_department_mapping(self):
        path = f"/api/v1/devices/{self.device['id']}/departments"
        made = self.api("POST", path, {"department_id": self.department.pk,
                                       "effective_from": "2026-01-01"})
        self.assertEqual(made.status_code, 201, made.content)
        overlap = self.api("POST", path, {"department_id": self.department.pk,
                                          "effective_from": "2026-02-01T09:00"})
        self.assertIn("department_id", self.fields(overlap))
        ended = self.api("POST", f"/api/v1/device-departments/{made.json()['id']}/end")
        self.assertEqual(ended.json()["status"], "ended")
        self.assertEqual(self.api("GET", path).json()["count"], 1)
        bad = self.api("POST", path, {"department_id": self.department.pk,
                                      "effective_from": "01/02/2026"})
        self.assertIn("effective_from", self.fields(bad))

    def test_enrollments(self):
        made = self.api("POST", "/api/v1/device-enrollments", {
            "device_id": self.device["id"], "employee_id": self.rahim.pk,
            "device_user_id": "41", "effective_from": "2026-01-01"})
        self.assertEqual(made.status_code, 201, made.content)
        row = made.json()
        self.assertEqual((row["device"]["id"], row["attendance_enabled"],
                          row["assigned_device_authorized"]), (self.device["id"], True, True))
        with use_company(self.company):
            other = Employee.objects.create(company=self.company, first_name="Karim")
        taken = self.api("POST", "/api/v1/device-enrollments", {
            "device_id": self.device["id"], "employee_id": other.pk,
            "device_user_id": "41", "effective_from": "2026-03-01"})
        self.assertIn("device_user_id", self.fields(taken))
        changed = self.api("PATCH", f"/api/v1/device-enrollments/{row['id']}",
                           {"assigned_device_authorized": False, "card_number": "12345"})
        self.assertEqual(changed.status_code, 200, changed.content)
        self.assertEqual((changed.json()["assigned_device_authorized"],
                          changed.json()["card_number"], changed.json()["device_user_id"]),
                         (False, "12345", "41"))
        entry = AuditLog.objects.get(action="device_enrollment.updated")
        self.assertIs(entry.before_data["assigned_device_authorized"], True)
        letters = self.api("PATCH", f"/api/v1/device-enrollments/{row['id']}",
                           {"card_number": "12A"})
        self.assertIn("card_number", self.fields(letters))
        listed = self.api("GET", "/api/v1/device-enrollments",
                          query=f"device_id={self.device['id']}").json()
        self.assertEqual(listed["count"], 1)
        with use_company(self.company):
            stored = DeviceEnrollment.objects.get(pk=row["id"])
        self.assertEqual(stored.effective_from.date(), datetime.date(2025, 12, 31))  # UTC of 00:00+06


class RulesTests(DeviceApiTestCase):
    def test_rules_and_recheck(self):
        rules = self.api("GET", "/api/v1/devices/attendance-rules").json()
        self.assertIn(rules["scope"], rules["scope_help"])
        changed = self.api("PATCH", "/api/v1/devices/attendance-rules",
                           {"scope": "company_devices"})
        self.assertEqual(changed.json()["scope"], "company_devices")
        result = self.api("POST", "/api/v1/devices/attendance-rules/recheck",
                          {"start": "2026-09-01", "end": "2026-09-30"})
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(result.json()["checked"], 0)
        too_long = self.api("POST", "/api/v1/devices/attendance-rules/recheck",
                            {"start": "2024-01-01", "end": "2026-09-30"})
        self.assertEqual(too_long.status_code, 422)
