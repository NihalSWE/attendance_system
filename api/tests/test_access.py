"""Phase 2: Organisation → Access through the API (who has which permission,
in which branch)."""

import datetime
from decimal import Decimal

import pyotp

from access_control.branch_access import can
from accounts.models import CompanyMembership
from api.tests.test_auth import ApiTestCase
from auditlog.models import AuditLog
from common.tenant import use_company
from employees.services import create_employee
from organization.catalogue import adopt_department, adopt_designation
from organization.models import Branch
from tenants.services import onboard_company

START = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)


class AccessApiTests(ApiTestCase):
    """Head Office: clerk (Employee login), Manny (branch manager). Chattogram: remote."""

    def setUp(self):
        super().setUp()
        with use_company(self.company):
            self.hq = Branch.objects.get(is_default=True)
            self.ctg = Branch.objects.create(company=self.company, code="CTG", name="Chattogram",
                                             timezone="Asia/Dhaka", country_code="BD")
            self.placement = {}
            for branch in (self.hq, self.ctg):
                department = adopt_department(branch, "SW", "Software")
                self.placement[branch.pk] = (department,
                                             adopt_designation(department, "DEV", "Developer"))
        admin_user = self.person("admin@example.test", role="company_admin")
        secret = self.turn_on_two_step(admin_user)
        challenge = self.login("admin@example.test").json()["challenge"]
        self.admin = self.call("POST", "/api/v1/auth/login/two-step",
                               {"challenge": challenge, "code": pyotp.TOTP(secret).now()}).json()
        self.clerk_user = self.person("clerk@example.test", role="employee")
        self.clerk = self.employee("Clerk", "E1", self.hq, user=self.clerk_user)
        self.remote = self.employee("Remote", "C1", self.ctg)
        manager_user = self.person("manny@example.test", role="manager")
        with use_company(self.company):
            CompanyMembership.all_objects.get(user=manager_user).allowed_branches.set([self.hq])
        self.manny = self.employee("Manny", "M1", self.hq, user=manager_user)
        self.manager = self.logged_in("manny@example.test")

    def employee(self, name, code, branch, user=None):
        department, designation = self.placement[branch.pk]
        employee = create_employee(
            company=self.company, first_name=name, employee_code=code, branch=branch,
            department=department, designation=designation, effective_from=START,
            pay_basis="monthly", base_rate=Decimal("20000"))["employee"]
        if user is not None:
            with use_company(self.company):
                employee.user = user
                employee.save(update_fields=["user"])
        return employee

    def test_the_permissions_list(self):
        rows = self.call("GET", "/api/v1/access/permissions", session=self.admin,
                         query="page_size=100").json()["results"]
        self.assertIn("employees.view", [row["code"] for row in rows])

    def test_the_admin_sees_everyone_and_gives_access(self):
        people = self.call("GET", "/api/v1/access/people", session=self.admin).json()
        self.assertEqual({row["employee"]["name"] for row in people["results"]},
                         {"Clerk", "Remote", "Manny"})
        path = f"/api/v1/access/people/{self.clerk.pk}"
        grid = self.call("GET", path, session=self.admin).json()
        self.assertEqual([b["name"] for b in grid["branches"]], ["Chattogram", self.hq.name])
        saved = self.call("PUT", path, {"grants": [
            {"permission": "employees.view", "branch_id": self.hq.pk}], "reason": "Covers HR"},
            session=self.admin)
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertEqual(saved.json()["added"],
                         [{"permission": "employees.view", "branch_id": self.hq.pk}])
        self.assertTrue(can(self.clerk_user, self.company.pk, "employees.view", self.hq.pk))
        self.assertTrue(AuditLog.objects.filter(action="access.granted").exists())
        removed = self.call("PUT", path, {"grants": []}, session=self.admin).json()
        self.assertEqual(removed["removed"],
                         [{"permission": "employees.view", "branch_id": self.hq.pk}])
        filtered = self.call("GET", "/api/v1/access/people", session=self.admin,
                             query=f"branch_id={self.ctg.pk}").json()
        self.assertEqual([r["employee"]["name"] for r in filtered["results"]], ["Remote"])

    def test_a_branch_manager_works_in_their_branch_only(self):
        people = self.call("GET", "/api/v1/access/people", session=self.manager).json()
        self.assertEqual({row["employee"]["name"] for row in people["results"]}, {"Clerk", "Manny"})
        outside = self.call("GET", f"/api/v1/access/people/{self.remote.pk}", session=self.manager)
        self.assertEqual(outside.status_code, 403)
        crafted = self.call("PUT", f"/api/v1/access/people/{self.clerk.pk}", {"grants": [
            {"permission": "employees.view", "branch_id": self.ctg.pk}]}, session=self.manager)
        self.assertEqual(crafted.status_code, 200, crafted.content)
        self.assertEqual(crafted.json()["added"], [])       # a cell they may not change
        self.assertFalse(can(self.clerk_user, self.company.pk, "employees.view", self.ctg.pk))

    def test_nobody_changes_their_own_access(self):
        response = self.call("PUT", f"/api/v1/access/people/{self.manny.pk}", {"grants": []},
                             session=self.manager)
        self.assertEqual(self.code_of(response), "permission_denied")

    def test_an_employee_login_cannot_open_access(self):
        clerk = self.logged_in("clerk@example.test")
        response = self.call("GET", "/api/v1/access/people", session=clerk)
        self.assertEqual(self.code_of(response), "permission_denied")

    def test_someone_from_another_company_is_not_found(self):
        other = onboard_company(code="OTH", slug="oth", name="Other Ltd")
        from employees.models import Employee

        with use_company(other):
            stranger = Employee.objects.create(company=other, first_name="Stranger")
        response = self.call("GET", f"/api/v1/access/people/{stranger.pk}", session=self.admin)
        self.assertEqual(response.status_code, 404)

    def test_api_keys_cannot_change_access(self):
        key = self.call("POST", "/api/v1/api-keys", {"name": "ERP", "scopes": ["company:read"]},
                        session=self.admin).json()
        from api.tests.test_auth import signature_headers

        headers = signature_headers(key["secret"], key["id"], "GET", "/api/v1/access/people")
        response = self.client.get("/api/v1/access/people", **headers)
        self.assertEqual(self.code_of(response), "permission_denied")
