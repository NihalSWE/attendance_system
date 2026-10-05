"""Phase 3, part 2: the photo, education, documents, the login, leave policy
and balances, overtime, reports, line management, devices and import."""

import base64
import io
import shutil
import tempfile

from django.test import override_settings
from PIL import Image

from api.tests.test_employees import EmployeeApiTestCase
from common.tenant import use_company
from employees.models import Employee


def b64(data):
    return base64.b64encode(data).decode()


def png_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, "PNG")
    return buffer.getvalue()


class MediaTestCase(EmployeeApiTestCase):
    def setUp(self):
        super().setUp()
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=media)
        override.enable()
        self.addCleanup(override.disable)


class PhotoTests(MediaTestCase):
    def test_upload_fetch_and_remove(self):
        path = f"/api/v1/employees/{self.rahim.pk}/photo"
        self.assertEqual(self.api("GET", path).status_code, 404)
        up = self.api("PUT", path, {"filename": "rahim.png", "content_base64": b64(png_bytes())})
        self.assertEqual(up.status_code, 200, up.content)
        self.assertTrue(up.json()["has_photo"])
        got = self.api("GET", path)
        self.assertEqual((got.status_code, got["Content-Type"]), (200, "image/png"))
        self.assertEqual(got["X-Content-Type-Options"], "nosniff")
        self.assertEqual(b"".join(got.streaming_content)[:4], b"\x89PNG")
        gone = self.api("DELETE", path)
        self.assertFalse(gone.json()["has_photo"])

    def test_a_renamed_file_is_not_a_picture(self):
        response = self.api("PUT", f"/api/v1/employees/{self.rahim.pk}/photo",
                            {"filename": "fake.png", "content_base64": b64(b"not a picture")})
        self.assertIn("content_base64", self.fields(response))

    def test_hr_may_see_but_a_stranger_branch_manager_may_not(self):
        path = f"/api/v1/employees/{self.remote.pk}/photo"
        self.api("PUT", path, {"filename": "r.png", "content_base64": b64(png_bytes())})
        self.assertEqual(self.api("GET", path, session=self.hr).status_code, 200)
        self.assertEqual(self.api("GET", path, session=self.manager).status_code, 403)


class EducationAndDocumentTests(MediaTestCase):
    def test_education(self):
        path = f"/api/v1/employees/{self.rahim.pk}/education"
        made = self.api("POST", path, {"qualification": "BSc", "passing_year": 2019})
        self.assertEqual(made.status_code, 201, made.content)
        row = made.json()["id"]
        changed = self.api("PATCH", f"{path}/{row}", {"result": "CGPA 3.7"})
        self.assertEqual((changed.json()["result"], changed.json()["qualification"]),
                         ("CGPA 3.7", "BSc"))
        self.assertEqual(self.api("GET", path).json()["count"], 1)
        old = self.api("POST", path, {"qualification": "SSC", "passing_year": 1800})
        self.assertIn("passing_year", self.fields(old))
        self.assertEqual(self.api("DELETE", f"{path}/{row}").status_code, 200)
        self.assertEqual(self.api("GET", path).json()["count"], 0)
        self.assertEqual(self.api("DELETE", f"{path}/{row}").status_code, 404)

    def test_documents(self):
        path = f"/api/v1/employees/{self.rahim.pk}/documents"
        pdf = b"%PDF-1.4\n%fake but starts right\n"
        made = self.api("POST", path, {"kind": "national_id", "title": "NID", "filename": "nid.pdf",
                                       "content_base64": b64(pdf)})
        self.assertEqual(made.status_code, 201, made.content)
        doc = made.json()["id"]
        listed = self.api("GET", path).json()["results"]
        self.assertEqual([d["title"] for d in listed], ["NID"])
        got = self.api("GET", f"{path}/{doc}", session=self.hr)
        self.assertEqual(b"".join(got.streaming_content), pdf)
        self.assertIn("nid.pdf", got["Content-Disposition"])
        fake = self.api("POST", path, {"kind": "other", "title": "X", "filename": "x.pdf",
                                       "content_base64": b64(b"hello")})
        self.assertIn("content_base64", self.fields(fake))
        self.assertEqual(self.api("DELETE", f"{path}/{doc}").status_code, 200)
        self.assertEqual(self.api("GET", f"{path}/{doc}").status_code, 404)


class LoginTests(EmployeeApiTestCase):
    def test_give_change_reset_disable_enable(self):
        path = f"/api/v1/employees/{self.rahim.pk}/login"
        self.assertEqual(self.api("GET", path).status_code, 404)
        made = self.api("POST", path, {"email": "rahim@example.test", "password": "Start-2026-pass",
                                       "password_confirm": "Start-2026-pass"})
        self.assertEqual(made.status_code, 201, made.content)
        self.assertEqual((made.json()["role"], made.json()["active"]), ("employee", True))
        self.assertEqual(self.login("rahim@example.test", "Start-2026-pass").status_code, 200)
        role = self.api("PATCH", path, {"role": "manager", "branch_ids": [self.hq.pk]})
        self.assertEqual(role.json()["branches"], [{"id": self.hq.pk, "name": self.hq.name}])
        mismatch = self.api("POST", f"{path}/password",
                            {"password": "New-2026-pass", "password_confirm": "Other-2026"})
        self.assertIn("password_confirm", self.fields(mismatch))
        reset = self.api("POST", f"{path}/password",
                         {"password": "New-2026-pass", "password_confirm": "New-2026-pass"})
        self.assertEqual(reset.status_code, 200, reset.content)
        self.assertFalse(self.api("POST", f"{path}/disable").json()["active"])
        self.assertEqual(self.login("rahim@example.test", "New-2026-pass").status_code, 403)
        self.assertTrue(self.api("POST", f"{path}/enable").json()["active"])

    def test_an_email_with_a_login_is_refused(self):
        response = self.api("POST", f"/api/v1/employees/{self.rahim.pk}/login", {
            "email": "hr@example.test", "password": "Start-2026-pass",
            "password_confirm": "Start-2026-pass"})
        self.assertIn("email", self.fields(response))

    def test_a_branch_manager_gives_employee_logins_only(self):
        path = f"/api/v1/employees/{self.karim.pk}/login"
        body = {"email": "karim@example.test", "password": "Start-2026-pass",
                "password_confirm": "Start-2026-pass"}
        hr_role = self.api("POST", path, {**body, "role": "hr"}, session=self.manager)
        self.assertEqual(self.code_of(hr_role), "permission_denied")
        made = self.api("POST", path, body, session=self.manager)
        self.assertEqual(made.status_code, 201, made.content)
        role = self.api("PATCH", path, {"role": "manager", "branch_ids": [self.hq.pk]},
                        session=self.manager)
        self.assertEqual(self.code_of(role), "permission_denied")


class SettingsTests(EmployeeApiTestCase):
    def test_leave_policy_and_adjustment(self):
        from leaves.models import LeavePolicy, LeaveType
        from leaves.services import create_default_leave_types

        create_default_leave_types(self.company, actor=None)
        with use_company(self.company):
            policy = LeavePolicy.objects.create(company=self.company, code="STD", name="Standard")
            casual = LeaveType.objects.filter(status="active").first()
        given = self.api("PUT", f"/api/v1/employees/{self.rahim.pk}/leave-policy",
                         {"policy_id": policy.pk, "from_date": "2026-01-01"})
        self.assertEqual(given.status_code, 200, given.content)
        adjusted = self.api("POST", f"/api/v1/employees/{self.rahim.pk}/leave-adjustments",
                            {"leave_type_id": casual.pk, "year": 2026, "days": "1.5",
                             "note": "Carried over"})
        self.assertEqual(adjusted.status_code, 201, adjusted.content)
        no_note = self.api("POST", f"/api/v1/employees/{self.rahim.pk}/leave-adjustments",
                           {"leave_type_id": casual.pk, "year": 2026, "days": "1", "note": " "})
        self.assertEqual(no_note.status_code, 422)
        manager = self.api("PUT", f"/api/v1/employees/{self.rahim.pk}/leave-policy",
                           {"policy_id": None, "from_date": "2026-02-01"}, session=self.manager)
        self.assertEqual(manager.status_code, 403)

    def test_overtime_visibility_and_reports(self):
        off = self.api("PUT", f"/api/v1/employees/{self.rahim.pk}/overtime",
                       {"no_overtime_from": "2026-11-01"})
        self.assertEqual(off.status_code, 200, off.content)
        with use_company(self.company):
            self.assertEqual(str(Employee.objects.get(pk=self.rahim.pk).no_overtime_from),
                             "2026-11-01")
        self.assertEqual(self.api("PUT", f"/api/v1/employees/{self.rahim.pk}/overtime",
                                  {"no_overtime_from": None}).status_code, 200)
        hidden = self.api("PUT", f"/api/v1/employees/{self.rahim.pk}/report-visibility",
                          {"hidden": True})
        self.assertEqual(hidden.status_code, 200, hidden.content)
        reports = self.api("PUT", f"/api/v1/employees/{self.rahim.pk}/reports",
                           {"people_ids": [self.karim.pk]})
        self.assertEqual(reports.status_code, 200, reports.content)
        karim = self.api("GET", f"/api/v1/employees/{self.karim.pk}").json()
        self.assertEqual(karim["placement"]["line_manager"]["id"], self.rahim.pk)

    def test_device_links(self):
        listed = self.api("GET", f"/api/v1/employees/{self.rahim.pk}/devices").json()
        self.assertEqual(listed["count"], 0)
        missing = self.api("PATCH", f"/api/v1/employees/{self.rahim.pk}/devices/999",
                           {"card_number": "1"})
        self.assertEqual(missing.status_code, 404)


class ImportTests(EmployeeApiTestCase):
    FILE = "Employee ID,Name\r\n9001,Nadia Islam\r\nA12,Bad Code\r\n9002,Tanvir Hasan\r\n"

    def body(self, **extra):
        return {"filename": "people.csv", "content_base64": b64(self.FILE.encode()), **extra}

    def test_check_then_import(self):
        before = Employee.all_objects.filter(company=self.company).count()
        checked = self.api("POST", "/api/v1/employees/import", self.body())
        self.assertEqual(checked.status_code, 200, checked.content)
        data = checked.json()
        self.assertEqual((data["imported"], data["rows"], data["new"]), (False, 3, 2))
        self.assertEqual([row["employee_code"] for row in data["bad"]], ["A12"])
        self.assertEqual(Employee.all_objects.filter(company=self.company).count(), before)
        refused = self.api("POST", "/api/v1/employees/import", self.body(confirm=True))
        self.assertIn("Nothing was imported", refused.json()["error"]["message"])
        self.FILE = "Employee ID,Name\n9001,Nadia Islam\n9002,Tanvir Hasan\n"
        done = self.api("POST", "/api/v1/employees/import", self.body(confirm=True))
        self.assertEqual(done.status_code, 200, done.content)
        self.assertTrue(done.json()["imported"])
        self.assertEqual(Employee.all_objects.filter(company=self.company).count(), before + 2)

    def test_a_branch_manager_imports_into_their_branch_only(self):
        response = self.api("POST", "/api/v1/employees/import",
                            self.body(branch_id=self.ctg.pk), session=self.manager)
        self.assertEqual(self.code_of(response), "permission_denied")

    def test_the_demo_file(self):
        csv = self.api("GET", "/api/v1/employees/import/demo-file")
        self.assertIn(b"Employee ID", csv.content)
        xlsx = self.api("GET", "/api/v1/employees/import/demo-file", query="file_type=xlsx")
        self.assertEqual(xlsx.content[:2], b"PK", xlsx.content[:300])
