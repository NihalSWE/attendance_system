"""Phase 10: My account - the employee's own profile, attendance, missed
scans, leave, payslips and LFA (docs/api/100-employee-app.md)."""

import datetime
import shutil
import tempfile

from django.test import override_settings
from django.utils import timezone

from accounts.models import CompanyMembership
from api.tests.test_attendance import AttendanceApiTestCase
from api.tests.test_employee_records import b64, png_bytes
from api.tests.test_leave import next_weekday
from common.tenant import use_company
from employees.models import Employee
from leaves.services import create_leave_type


class MeApiTestCase(AttendanceApiTestCase):
    def setUp(self):
        super().setUp()
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        worker = self.person("worker@example.test", role="employee")
        with use_company(self.company):
            Employee.objects.filter(pk=self.rahim.pk).update(
                user=worker, joining_date=datetime.date(2025, 1, 1))
        self.me = self.logged_in("worker@example.test")

    def mine(self, method, path, body=None, query=""):
        return self.api(method, f"/api/v1/me{path}", body, session=self.me, query=query)


class ProfileTests(MeApiTestCase):
    def test_home_and_profile(self):
        home = self.mine("GET", "")
        self.assertEqual(home.status_code, 200, home.content)
        self.assertEqual((home.json()["role"], home.json()["employee"]["name"],
                          home.json()["placement"]["employee_code"]), ("employee", "Rahim", "E1"))
        self.assertIsNotNone(home.json()["month_summary"])
        changed = self.mine("PATCH", "/details", {"phone": "01711000001",
                                                  "blood_group": "B+"})
        self.assertEqual(changed.status_code, 200, changed.content)
        self.assertEqual(changed.json()["details"]["phone"], "01711000001")
        name = self.mine("PATCH", "/details", {"first_name": "Someone else"})
        self.assertEqual(self.code_of(name), "unknown_field")

    def test_photo_and_education(self):
        self.assertEqual(self.mine("GET", "/photo").status_code, 404)
        put = self.mine("PUT", "/photo", {"filename": "me.png", "content_base64": b64(png_bytes())})
        self.assertEqual(put.status_code, 200, put.content)
        self.assertTrue(put.json()["employee"]["has_photo"])
        got = self.mine("GET", "/photo")
        self.assertEqual(b"".join(got.streaming_content)[:4], b"\x89PNG")
        self.assertFalse(self.mine("DELETE", "/photo").json()["employee"]["has_photo"])
        added = self.mine("POST", "/education", {"qualification": "BSc", "passing_year": 2019})
        self.assertEqual(added.status_code, 201, added.content)
        changed = self.mine("PATCH", f"/education/{added.json()['id']}", {"result": "3.6"})
        self.assertEqual(changed.json()["result"], "3.6")
        self.assertEqual(self.mine("GET", "/education").json()["count"], 1)
        self.mine("DELETE", f"/education/{added.json()['id']}")
        self.assertEqual(self.mine("GET", "/education").json()["count"], 0)

    def test_a_login_without_an_employee_record(self):
        home = self.api("GET", "/api/v1/me")
        self.assertEqual((home.status_code, home.json()["employee"]), (200, None))
        refused = self.api("GET", "/api/v1/me/profile")
        self.assertEqual(self.code_of(refused), "permission_denied")

    def test_api_keys_cannot_use_it(self):
        key = self.key("employees:read")
        self.assertEqual(self.code_of(self.as_key(key, "GET", "/api/v1/me/profile")),
                         "permission_denied")


class AttendanceTests(MeApiTestCase):
    def test_my_month_and_day(self):
        month = self.mine("GET", "/attendance",
                          query=f"year={self.late_day.year}&month={self.late_day.month}")
        self.assertEqual(month.status_code, 200, month.content)
        day = [d for d in month.json()["days"] if d["date"] == self.late_day.isoformat()][0]
        self.assertTrue(day["late"])
        detail = self.mine("GET", f"/attendance/{self.late_day}").json()
        self.assertEqual((len(detail["scans"]), detail["may_fix"]), (2, False))

    def test_report_and_withdraw_a_missed_scan(self):
        sent = self.mine("POST", "/missed-scans",
                         {"kind": "check_out", "at": f"{self.open_day}T18:10",
                          "reason": "Left through the side gate"})
        self.assertEqual(sent.status_code, 201, sent.content)
        self.assertEqual((sent.json()["status"], sent.json()["date"]),
                         ("pending", self.open_day.isoformat()))
        waiting = self.api("GET", "/api/v1/missed-scans").json()
        self.assertEqual(waiting["count"], 1)
        back = self.mine("POST", f"/missed-scans/{sent.json()['id']}/withdraw")
        self.assertEqual(back.json()["status"], "withdrawn")
        self.assertEqual(self.mine("POST", f"/missed-scans/{sent.json()['id']}/withdraw")
                         .status_code, 422)

    def test_a_branch_managers_day(self):
        refused = self.mine("GET", "/branch-attendance")
        self.assertEqual(self.code_of(refused), "permission_denied")
        manager_user = self.person("manny@example.test", role="manager")
        with use_company(self.company):
            CompanyMembership.all_objects.get(user=manager_user).allowed_branches.set([self.hq])
        manager = self.logged_in("manny@example.test")
        rows = self.api("GET", "/api/v1/me/branch-attendance", session=manager,
                        query=f"date={self.late_day}").json()
        self.assertEqual([(r["employee"]["name"], r["status"]) for r in rows["results"]],
                         [("Rahim", "present")])


class LeaveTests(MeApiTestCase):
    def test_ask_see_and_withdraw(self):
        casual = create_leave_type(actor=self.admin_user, company_id=self.company.pk,
                                   values={"code": "CAS", "name": "Casual",
                                           "days_per_year": 10})
        summary = self.mine("GET", "/leave/summary").json()
        self.assertEqual(summary["leave_types"], [{"id": casual.pk, "name": "Casual"}])
        day = next_weekday(7)
        no_reason = self.mine("POST", "/leave", {"leave_type_id": casual.pk,
                                                 "start_date": str(day), "end_date": str(day)})
        self.assertIn("reason", self.fields(no_reason))
        asked = self.mine("POST", "/leave", {"leave_type_id": casual.pk, "start_date": str(day),
                                             "end_date": str(day), "reason": "Doctor"})
        self.assertEqual(asked.status_code, 201, asked.content)
        self.assertEqual((asked.json()["status"], asked.json()["days"], asked.json()["may_withdraw"]),
                         ("pending", "1.00", True))
        inbox = self.api("GET", "/api/v1/leave/requests").json()
        self.assertEqual([r["id"] for r in inbox["results"]], [asked.json()["id"]])
        back = self.mine("POST", f"/leave/{asked.json()['id']}/withdraw")
        self.assertEqual(back.json()["status"], "withdrawn")
        self.assertEqual(self.mine("GET", "/leave").json()["count"], 1)


class SalaryTests(MeApiTestCase):
    def test_only_finalised_payslips_show(self):
        path = f"/api/v1/payroll/months/{self.late_day.year}/{self.late_day.month}"
        self.api("POST", f"{path}/generate")
        self.assertEqual(self.mine("GET", "/payslips").json()["count"], 0)
        self.api("POST", f"{path}/submit")
        self.api("POST", f"{path}/approve")
        listed = self.mine("GET", "/payslips").json()
        self.assertEqual(listed["count"], 1)
        payslip = self.mine("GET", f"/payslips/{listed['results'][0]['id']}")
        self.assertEqual(payslip.status_code, 200, payslip.content)
        self.assertFalse(payslip.json()["may_adjust"])
        pdf = self.mine("GET", f"/payslips/{listed['results'][0]['id']}/pdf")
        self.assertEqual(pdf["Content-Type"], "application/pdf")

    def test_someone_elses_payslip_is_not_found(self):
        self.assertEqual(self.mine("GET", "/payslips/999999").status_code, 404)

    def test_claim_and_withdraw_lfa(self):
        self.assertFalse(self.mine("GET", "/lfa").json()["eligible"])
        self.api("PATCH", "/api/v1/payroll/lfa/settings", {
            "enabled": True, "name": "LFA", "amount_method": "fixed", "fixed_amount": "5000",
            "min_service_months": 0, "cycle": "calendar_year", "claims_per_cycle": 1,
            "payment": "separately"})
        found = self.mine("GET", "/lfa").json()
        self.assertEqual((found["eligible"], found["amount"]), (True, "5000.00"), found)
        claim = self.mine("POST", "/lfa", {"note": "Family trip"})
        self.assertEqual(claim.status_code, 201, claim.content)
        self.assertEqual(self.mine("GET", "/lfa/claims").json()["count"], 1)
        back = self.mine("POST", f"/lfa/claims/{claim.json()['id']}/withdraw")
        self.assertEqual(back.json()["status"], "withdrawn")

