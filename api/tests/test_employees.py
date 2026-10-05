"""Phase 3, part 1: employees - the list, adding, the profile, edits, status
(docs/api/30-employees.md)."""

import datetime
from decimal import Decimal

import pyotp
from django.utils import timezone

from accounts.models import CompanyMembership
from api.tests.test_auth import ApiTestCase, signature_headers
from auditlog.models import AuditLog
from common.tenant import use_company
from employees.models import Employee
from employees.services import create_employee
from organization.catalogue import adopt_department, adopt_designation
from organization.models import Branch
from tenants.services import onboard_company

START = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)


class EmployeeApiTestCase(ApiTestCase):
    """Head Office (Rahim, Karim) and Chattogram (Remote); an admin, HR and the
    Head Office branch manager."""

    def setUp(self):
        super().setUp()
        with use_company(self.company):
            self.hq = Branch.objects.get(is_default=True)
            self.ctg = Branch.objects.create(company=self.company, code="CTG", name="Chattogram",
                                             timezone="Asia/Dhaka", country_code="BD")
            self.places = {}
            for branch in (self.hq, self.ctg):
                department = adopt_department(branch, "SW", "Software")
                self.places[branch.pk] = (department,
                                          adopt_designation(department, "DEV", "Developer"),
                                          adopt_designation(department, "LEAD", "Team Lead"))
        admin_user = self.person("admin@example.test", role="company_admin")
        secret = self.turn_on_two_step(admin_user)
        challenge = self.login("admin@example.test").json()["challenge"]
        self.admin = self.call("POST", "/api/v1/auth/login/two-step",
                               {"challenge": challenge, "code": pyotp.TOTP(secret).now()}).json()
        self.person("hr@example.test", role="hr")
        self.hr = self.logged_in("hr@example.test")
        manager_user = self.person("manny@example.test", role="manager")
        with use_company(self.company):
            CompanyMembership.all_objects.get(user=manager_user).allowed_branches.set([self.hq])
        self.manager = self.logged_in("manny@example.test")
        self.rahim = self.employee("Rahim", "E1", self.hq)
        self.karim = self.employee("Karim", "E2", self.hq)
        self.remote = self.employee("Remote", "C1", self.ctg)

    def employee(self, name, code, branch):
        department, designation, _lead = self.places[branch.pk]
        return create_employee(
            company=self.company, first_name=name, employee_code=code, branch=branch,
            department=department, designation=designation, effective_from=START,
            pay_basis="monthly", base_rate=Decimal("20000"))["employee"]

    def api(self, method, path, body=None, session=None, query=""):
        return self.call(method, path, body, session=session or self.admin, query=query)

    def fields(self, response):
        return response.json()["error"].get("fields", {})


class ListAndAddTests(EmployeeApiTestCase):
    def test_the_admin_sees_everyone_with_pay(self):
        rows = self.api("GET", "/api/v1/employees").json()["results"]
        self.assertEqual({r["name"] for r in rows}, {"Rahim", "Karim", "Remote"})
        rahim = next(r for r in rows if r["name"] == "Rahim")
        self.assertEqual((rahim["employee_code"], rahim["branch"]["id"], rahim["pay"]["base_rate"]),
                         ("E1", self.hq.pk, "20000.00"))

    def test_hr_sees_everyone_without_pay(self):
        rows = self.api("GET", "/api/v1/employees", session=self.hr).json()["results"]
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r["pay"] is None for r in rows))

    def test_a_branch_manager_sees_their_branch(self):
        rows = self.api("GET", "/api/v1/employees", session=self.manager).json()["results"]
        self.assertEqual({r["name"] for r in rows}, {"Rahim", "Karim"})

    def test_filters(self):
        ctg = self.api("GET", "/api/v1/employees", query=f"branch_id={self.ctg.pk}").json()
        self.assertEqual([r["name"] for r in ctg["results"]], ["Remote"])
        found = self.api("GET", "/api/v1/employees", query="q=E2").json()
        self.assertEqual([r["name"] for r in found["results"]], ["Karim"])

    def test_an_employee_login_is_kept_out(self):
        self.person("staff@example.test", role="employee")
        staff = self.logged_in("staff@example.test")
        self.assertEqual(self.code_of(self.api("GET", "/api/v1/employees", session=staff)),
                         "permission_denied")

    def test_add_with_pay(self):
        department, designation, _ = self.places[self.hq.pk]
        made = self.api("POST", "/api/v1/employees", {
            "first_name": "Nadia", "employee_code": "E9", "branch_id": self.hq.pk,
            "department_id": department.pk, "designation_id": designation.pk,
            "manager_id": self.rahim.pk, "start_date": "2026-02-01", "base_rate": "30000"})
        self.assertEqual(made.status_code, 201, made.content)
        profile = made.json()
        self.assertEqual((profile["placement"]["employee_code"], profile["pay"]["base_rate"],
                          profile["placement"]["line_manager"]["id"]),
                         ("E9", "30000.00", self.rahim.pk))

    def test_the_panels_rules_apply_when_adding(self):
        department, designation, _ = self.places[self.hq.pk]
        ctg_department, ctg_designation, _ = self.places[self.ctg.pk]
        body = {"first_name": "Dup", "employee_code": "E1", "branch_id": self.hq.pk,
                "department_id": department.pk, "designation_id": designation.pk,
                "start_date": "2026-02-01", "base_rate": "1000"}
        self.assertIn("employee_code", self.fields(self.api("POST", "/api/v1/employees", body)))
        wrong = self.api("POST", "/api/v1/employees",
                         {**body, "employee_code": "E8", "department_id": ctg_department.pk})
        self.assertIn("department_id", self.fields(wrong))
        no_pay = self.api("POST", "/api/v1/employees", {**body, "employee_code": "E8",
                                                         "base_rate": None})
        self.assertIn("base_rate", self.fields(no_pay))

    def test_a_branch_manager_adds_only_in_their_branch(self):
        department, designation, _ = self.places[self.ctg.pk]
        outside = self.api("POST", "/api/v1/employees", {
            "first_name": "X", "employee_code": "X1", "branch_id": self.ctg.pk,
            "department_id": department.pk, "designation_id": designation.pk,
            "start_date": "2026-02-01", "base_rate": "1000"}, session=self.manager)
        self.assertIn("branch_id", self.fields(outside))

    def test_hr_adds_without_pay(self):
        choices = self.api("GET", "/api/v1/employees/choices", session=self.hr).json()
        self.assertEqual(choices["pay"], "none")
        department, designation, _ = self.places[self.hq.pk]
        body = {"first_name": "Nopay", "employee_code": "E7", "branch_id": self.hq.pk,
                "department_id": department.pk, "designation_id": designation.pk,
                "start_date": "2026-02-01"}
        refused = self.api("POST", "/api/v1/employees", {**body, "base_rate": "100"},
                           session=self.hr)
        self.assertIn("base_rate", self.fields(refused))
        made = self.api("POST", "/api/v1/employees", body, session=self.hr)
        self.assertEqual(made.status_code, 201, made.content)
        self.assertIsNone(made.json()["pay"])

    def test_choices_narrow_by_branch_and_department(self):
        department, designation, lead = self.places[self.hq.pk]
        data = self.api("GET", "/api/v1/employees/choices",
                        query=f"branch_id={self.hq.pk}&department_id={department.pk}").json()
        self.assertEqual(data["pay"], "required")
        self.assertEqual([d["id"] for d in data["departments"]], [department.pk])
        self.assertEqual({d["id"] for d in data["designations"]}, {designation.pk, lead.pk})


class ProfileAndEditTests(EmployeeApiTestCase):
    def test_the_profile(self):
        profile = self.api("GET", f"/api/v1/employees/{self.rahim.pk}").json()
        self.assertEqual((profile["name"], profile["placement"]["branch"]["id"]),
                         ("Rahim", self.hq.pk))
        self.assertTrue(all(profile["may"].values()))
        history = self.api("GET", f"/api/v1/employees/{self.rahim.pk}/history").json()
        self.assertEqual(len(history["placements"]), 1)
        self.assertEqual(history["salaries"][0]["base_rate"], "20000.00")

    def test_outside_your_branch_and_another_company(self):
        response = self.api("GET", f"/api/v1/employees/{self.remote.pk}", session=self.manager)
        self.assertEqual(response.status_code, 403)
        other = onboard_company(code="OTH", slug="oth", name="Other Ltd")
        with use_company(other):
            stranger = Employee.objects.create(company=other, first_name="Stranger")
        self.assertEqual(self.api("GET", f"/api/v1/employees/{stranger.pk}").status_code, 404)

    def test_details_and_personal(self):
        changed = self.api("PATCH", f"/api/v1/employees/{self.rahim.pk}", {"phone": "01711000099"})
        self.assertEqual(changed.json()["phone"], "01711000099")
        self.assertEqual(changed.json()["first_name"], "Rahim")
        self.assertTrue(AuditLog.objects.filter(action="employee.details_updated").exists())
        personal = self.api("PATCH", f"/api/v1/employees/{self.rahim.pk}/personal",
                            {"blood_group": "B+", "emergency_contact_name": "Fatema"})
        self.assertEqual(personal.status_code, 200, personal.content)
        self.assertEqual(personal.json()["personal"]["blood_group"], "B+")
        future = (timezone.localdate() + datetime.timedelta(days=5)).isoformat()
        bad = self.api("PATCH", f"/api/v1/employees/{self.rahim.pk}/personal",
                       {"date_of_birth": future})
        self.assertIn("date_of_birth", self.fields(bad))

    def test_a_placement_from_a_later_date_keeps_history(self):
        department, designation, lead = self.places[self.hq.pk]
        moved = self.api("POST", f"/api/v1/employees/{self.rahim.pk}/placement", {
            "branch_id": self.hq.pk, "department_id": department.pk, "designation_id": lead.pk,
            "employee_code": "E1", "from_date": "2026-03-01", "reason": "Promoted"})
        self.assertEqual(moved.status_code, 200, moved.content)
        self.assertEqual(moved.json()["placement"]["designation"]["id"], lead.pk)
        history = self.api("GET", f"/api/v1/employees/{self.rahim.pk}/history").json()
        self.assertEqual(len(history["placements"]), 2)

    def test_a_branch_manager_cannot_move_someone_out_of_their_branch(self):
        department, designation, _ = self.places[self.ctg.pk]
        response = self.api("POST", f"/api/v1/employees/{self.rahim.pk}/placement", {
            "branch_id": self.ctg.pk, "department_id": department.pk,
            "designation_id": designation.pk, "employee_code": "E1",
            "from_date": "2026-03-01"}, session=self.manager)
        self.assertIn("branch_id", self.fields(response))

    def test_pay_changes_follow_salary_access(self):
        raised = self.api("POST", f"/api/v1/employees/{self.rahim.pk}/salary", {
            "pay_basis": "monthly", "base_rate": "25000", "from_date": "2026-04-01",
            "reason": "Raise"})
        self.assertEqual(raised.status_code, 200, raised.content)
        self.assertEqual(raised.json()["pay"]["base_rate"], "25000.00")
        hr = self.api("POST", f"/api/v1/employees/{self.rahim.pk}/salary", {
            "pay_basis": "monthly", "base_rate": "1", "from_date": "2026-04-01"}, session=self.hr)
        self.assertEqual(self.code_of(hr), "permission_denied")

    def test_api_keys_need_payroll_scopes_for_pay(self):
        key = self.api("POST", "/api/v1/api-keys",
                       {"name": "ERP", "scopes": ["employees:read"]}).json()
        headers = signature_headers(key["secret"], key["id"], "GET",
                                    f"/api/v1/employees/{self.rahim.pk}")
        seen = self.client.get(f"/api/v1/employees/{self.rahim.pk}", **headers).json()
        self.assertIsNone(seen["pay"])
        body = '{"pay_basis":"monthly","base_rate":"1","from_date":"2026-04-01"}'
        path = f"/api/v1/employees/{self.rahim.pk}/salary"
        headers = signature_headers(key["secret"], key["id"], "POST", path, body=body)
        refused = self.client.generic("POST", path, body, content_type="application/json",
                                      **headers)
        self.assertEqual(self.code_of(refused), "scope_missing")
        payroll = self.api("POST", "/api/v1/api-keys",
                           {"name": "Payroll", "scopes": ["employees:read", "payroll:read"]}).json()
        headers = signature_headers(payroll["secret"], payroll["id"], "GET",
                                    f"/api/v1/employees/{self.rahim.pk}")
        self.assertEqual(self.client.get(f"/api/v1/employees/{self.rahim.pk}",
                                         **headers).json()["pay"]["base_rate"], "20000.00")

    def test_line_manager(self):
        set_ = self.api("PUT", f"/api/v1/employees/{self.rahim.pk}/line-manager",
                        {"manager_id": self.karim.pk})
        self.assertEqual(set_.json()["placement"]["line_manager"]["id"], self.karim.pk)
        own = self.api("PUT", f"/api/v1/employees/{self.rahim.pk}/line-manager",
                       {"manager_id": self.rahim.pk})
        self.assertIn("manager_id", self.fields(own))
        cleared = self.api("PUT", f"/api/v1/employees/{self.rahim.pk}/line-manager",
                           {"manager_id": None})
        self.assertIsNone(cleared.json()["placement"]["line_manager"])


class StatusTests(EmployeeApiTestCase):
    def test_end_employment(self):
        today = timezone.localdate().isoformat()
        ended = self.api("POST", f"/api/v1/employees/{self.karim.pk}/end-employment",
                         {"last_day": today, "status": "resigned", "reason": "Moved abroad"})
        self.assertEqual(ended.status_code, 200, ended.content)
        self.assertEqual(ended.json()["employee"]["employment_status"], "resigned")
        self.assertTrue(ended.json()["employee"]["has_left"])
        again = self.api("POST", f"/api/v1/employees/{self.karim.pk}/end-employment",
                         {"last_day": today, "status": "resigned", "reason": "Again"})
        self.assertIn("already left", again.json()["error"]["message"])
        future = (timezone.localdate() + datetime.timedelta(days=3)).isoformat()
        early = self.api("POST", f"/api/v1/employees/{self.rahim.pk}/end-employment",
                         {"last_day": future, "status": "resigned", "reason": "Soon"})
        self.assertIn("last_day", self.fields(early))

    def test_inactive_then_active(self):
        today = timezone.localdate().isoformat()
        off = self.api("POST", f"/api/v1/employees/{self.rahim.pk}/inactive",
                       {"start_date": today, "reason": "Leave of absence"})
        self.assertEqual(off.status_code, 200, off.content)
        self.assertEqual(off.json()["employment_status"], "suspended")
        back = self.api("POST", f"/api/v1/employees/{self.rahim.pk}/active")
        self.assertEqual(back.json()["employment_status"], "active")
        again = self.api("POST", f"/api/v1/employees/{self.rahim.pk}/active")
        self.assertEqual(again.status_code, 422)
