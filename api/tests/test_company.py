"""Phase 2: company, branches, departments and designations
(docs/api/20-company-and-branches.md)."""

import base64
import shutil
import tempfile

import pyotp
from django.test import override_settings

from accounts.models import CompanyMembership
from api.tests.test_auth import ApiTestCase, signature_headers
from auditlog.models import AuditLog
from common.tenant import use_company
from employees.models import Employee
from organization.models import Branch, Department, Designation
from tenants.services import onboard_company

PNG = base64.b64encode(
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00"
    b"\x00\x00IEND\xaeB`\x82").decode()


class CompanyApiTestCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.admin_user = self.person("admin@example.test", role="company_admin")
        self.admin = self.admin_session(self.admin_user, "admin@example.test")
        with use_company(self.company):
            self.hq = Branch.objects.get(is_default=True)
        self.other = onboard_company(code="OTH", slug="oth", name="Other Ltd")
        with use_company(self.other):
            self.other_branch = Branch.objects.get(is_default=True)

    def admin_session(self, user, email):
        secret = self.turn_on_two_step(user)
        challenge = self.login(email).json()["challenge"]
        return self.call("POST", "/api/v1/auth/login/two-step",
                         {"challenge": challenge, "code": pyotp.TOTP(secret).now()}).json()

    def api(self, method, path, body=None, session=None, query=""):
        return self.call(method, path, body, session=session or self.admin, query=query)

    def key(self, *scopes):
        response = self.api("POST", "/api/v1/api-keys", {"name": "ERP", "scopes": list(scopes)})
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def as_key(self, key, method, path, body=None):
        import json

        raw = json.dumps(body) if body is not None else ""
        headers = signature_headers(key["secret"], key["id"], method, path, body=raw)
        return self.client.generic(method, path, raw, content_type="application/json", **headers)


class CompanyTests(CompanyApiTestCase):
    def test_the_profile_and_a_change(self):
        data = self.api("GET", "/api/v1/company").json()
        self.assertEqual((data["id"], data["name"], data["logo_url"]),
                         (self.company.pk, "Acme Ltd", None))
        changed = self.api("PATCH", "/api/v1/company",
                           {"contact_person": "Karim Ahmed", "phone": "01811000000"})
        self.assertEqual(changed.status_code, 200, changed.content)
        self.assertEqual(changed.json()["contact_person"], "Karim Ahmed")
        self.assertTrue(AuditLog.objects.filter(action="company.profile_updated").exists())

    def test_the_panels_rules_apply(self):
        self.other.email = "info@other.test"
        self.other.save()
        taken = self.api("PATCH", "/api/v1/company", {"email": "info@other.test"})
        self.assertEqual(taken.status_code, 422)
        self.assertIn("email", taken.json()["error"]["fields"])
        blank = self.api("PATCH", "/api/v1/company", {"name": "  "})
        self.assertIn("name", blank.json()["error"]["fields"])

    def test_hr_and_branch_managers_cannot_open_the_profile(self):
        self.person("hr@example.test", role="hr")
        hr = self.logged_in("hr@example.test")
        self.assertEqual(self.code_of(self.api("GET", "/api/v1/company", session=hr)),
                         "permission_denied")
        self.person("manager@example.test", role="manager")
        manager = self.logged_in("manager@example.test")
        self.assertEqual(self.api("GET", "/api/v1/company", session=manager).status_code, 403)

    def test_logo_upload_and_removal(self):
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        with override_settings(MEDIA_ROOT=media):
            up = self.api("PUT", "/api/v1/company/logo",
                          {"filename": "logo.png", "content_base64": PNG})
            self.assertEqual(up.status_code, 200, up.content)
            self.assertIn("/company_logos/", up.json()["logo_url"])
            wrong = self.api("PUT", "/api/v1/company/logo",
                             {"filename": "logo.exe", "content_base64": PNG})
            self.assertEqual(wrong.status_code, 422)
            broken = self.api("PUT", "/api/v1/company/logo",
                              {"filename": "logo.png", "content_base64": "not base64!"})
            self.assertIn("content_base64", broken.json()["error"]["fields"])
            big = base64.b64encode(b"PNG" + b"0" * (2 * 1024 * 1024 - 100)).decode()
            nearly = self.api("PUT", "/api/v1/company/logo",
                              {"filename": "big.png", "content_base64": big})
            self.assertEqual(nearly.status_code, 200, nearly.content[:300])
            over = base64.b64encode(b"0" * (2 * 1024 * 1024 + 1)).decode()
            too_big = self.api("PUT", "/api/v1/company/logo",
                               {"filename": "big.png", "content_base64": over})
            self.assertIn("2 MB", str(too_big.json()["error"]))
            gone = self.api("DELETE", "/api/v1/company/logo")
            self.assertEqual(gone.status_code, 200, gone.content)
            self.assertIsNone(gone.json()["logo_url"])

    def test_mail_settings_never_show_the_password(self):
        empty = self.api("GET", "/api/v1/company/mail-settings").json()
        self.assertFalse(empty["saved"])
        missing = self.api("PATCH", "/api/v1/company/mail-settings", {"from_name": "Acme"})
        self.assertEqual(missing.status_code, 422)
        saved = self.api("PATCH", "/api/v1/company/mail-settings", {
            "from_email": "hr@acme.test", "host": "smtp.gmail.com", "port": 587,
            "username": "hr@acme.test", "password": "secret-app-password"})
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertTrue(saved.json()["has_password"])
        self.assertNotIn("secret-app-password", saved.content.decode())
        kept = self.api("PATCH", "/api/v1/company/mail-settings", {"from_name": "Acme HR"})
        self.assertEqual((kept.json()["from_name"], kept.json()["has_password"]), ("Acme HR", True))

    def test_mail_settings_are_for_people_not_api_keys(self):
        key = self.key("company:read", "company:write")
        response = self.as_key(key, "GET", "/api/v1/company/mail-settings")
        self.assertEqual(self.code_of(response), "permission_denied")

    def test_a_test_email_needs_saved_settings(self):
        response = self.api("POST", "/api/v1/company/mail-settings/test", {"to": "a@b.test"})
        self.assertEqual(response.status_code, 422)


class BranchTests(CompanyApiTestCase):
    def test_list_create_change_and_retire(self):
        rows = self.api("GET", "/api/v1/branches").json()["results"]
        self.assertEqual([r["code"] for r in rows], [self.hq.code])
        with use_company(self.company):
            Department.objects.create(company=self.company, branch=self.hq, code="HR",
                                      name="Human Resources")
        created = self.api("POST", "/api/v1/branches",
                           {"code": "ctg", "name": "Chattogram", "city": "Chattogram"})
        self.assertEqual(created.status_code, 201, created.content)
        branch = created.json()
        self.assertEqual((branch["code"], branch["status"], branch["timezone"]),
                         ("CTG", "active", "Asia/Dhaka"))
        self.assertEqual(branch["departments_copied"], 1)
        again = self.api("POST", "/api/v1/branches", {"code": "CTG", "name": "Copy"})
        self.assertEqual(again.status_code, 422)
        changed = self.api("PATCH", f"/api/v1/branches/{branch['id']}", {"phone": "01711000009"})
        self.assertEqual((changed.json()["phone"], changed.json()["name"]),
                         ("01711000009", "Chattogram"))
        dates = self.api("PATCH", f"/api/v1/branches/{branch['id']}",
                         {"opened_on": "2024-05-01", "closed_on": "2024-01-01"})
        self.assertIn("closed_on", dates.json()["error"]["fields"])
        retired = self.api("POST", f"/api/v1/branches/{branch['id']}/status",
                           {"status": "inactive", "reason": "Closed"})
        self.assertEqual(retired.json()["status"], "inactive")

    def test_the_default_branch_cannot_be_retired(self):
        response = self.api("POST", f"/api/v1/branches/{self.hq.pk}/status", {"status": "inactive"})
        self.assertIn("status", response.json()["error"]["fields"])

    def test_another_companys_branch_is_not_found(self):
        self.assertEqual(self.api("GET", f"/api/v1/branches/{self.other_branch.pk}").status_code, 404)
        response = self.api("PATCH", f"/api/v1/branches/{self.other_branch.pk}", {"name": "x"})
        self.assertEqual(response.status_code, 404)

    def test_hr_reads_but_does_not_change(self):
        self.person("hr@example.test", role="hr")
        hr = self.logged_in("hr@example.test")
        self.assertEqual(self.api("GET", "/api/v1/branches", session=hr).status_code, 200)
        refused = self.api("POST", "/api/v1/branches", {"code": "X", "name": "X"}, session=hr)
        self.assertEqual(refused.status_code, 403)

    def test_someone_limited_to_branches_sees_only_those(self):
        with use_company(self.company):
            north = Branch.objects.create(company=self.company, code="NTH", name="North")
        hr_user = self.person("hr@example.test", role="hr")
        membership = CompanyMembership.all_objects.get(user=hr_user)
        membership.allowed_branches.add(north)
        hr = self.logged_in("hr@example.test")
        rows = self.api("GET", "/api/v1/branches", session=hr).json()["results"]
        self.assertEqual([r["code"] for r in rows], ["NTH"])
        self.assertEqual(self.api("GET", f"/api/v1/branches/{self.hq.pk}", session=hr).status_code,
                         404)

    def test_branch_managers_and_employees_are_kept_out(self):
        self.person("manager@example.test", role="manager")
        manager = self.logged_in("manager@example.test")
        self.assertEqual(self.code_of(self.api("GET", "/api/v1/branches", session=manager)),
                         "permission_denied")

    def test_api_keys_need_the_scope(self):
        reader = self.key("company:read")
        self.assertEqual(self.as_key(reader, "GET", "/api/v1/branches").status_code, 200)
        refused = self.as_key(reader, "POST", "/api/v1/branches", {"code": "K", "name": "Key"})
        self.assertEqual(self.code_of(refused), "scope_missing")
        writer = self.key("company:read", "company:write")
        made = self.as_key(writer, "POST", "/api/v1/branches", {"code": "K", "name": "Key"})
        self.assertEqual(made.status_code, 201, made.content)

    def test_filters_and_paging(self):
        for number in range(3):
            self.api("POST", "/api/v1/branches", {"code": f"B{number}", "name": f"Branch {number}"})
        page = self.api("GET", "/api/v1/branches", query="page_size=2").json()
        self.assertEqual((page["count"], len(page["results"])), (4, 2))
        found = self.api("GET", "/api/v1/branches", query="q=branch 1").json()
        self.assertEqual([r["code"] for r in found["results"]], ["B1"])


class DepartmentTests(CompanyApiTestCase):
    def setUp(self):
        super().setUp()
        with use_company(self.company):
            self.rahim = Employee.objects.create(company=self.company, first_name="Rahim")
        with use_company(self.other):
            self.outsider = Employee.objects.create(company=self.other, first_name="Out")

    def create(self, **values):
        body = {"branch_id": self.hq.pk, "code": "SW", "name": "Software", **values}
        response = self.api("POST", "/api/v1/departments", body)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def test_create_with_designations_and_change_the_head(self):
        department = self.create(designations=[{"code": "SE", "name": "Software Engineer"}])
        self.assertEqual(department["branch"], {"id": self.hq.pk, "name": self.hq.name})
        self.assertEqual([d["code"] for d in department["designations"]], ["SE"])
        headed = self.api("PATCH", f"/api/v1/departments/{department['id']}",
                          {"head_employee_id": self.rahim.pk})
        self.assertEqual(headed.json()["head"], {"id": self.rahim.pk, "name": "Rahim"})
        self.assertTrue(AuditLog.objects.filter(action="department.head_changed").exists())

    def test_the_panels_rules_apply(self):
        self.create()
        twice = self.api("POST", "/api/v1/departments",
                         {"branch_id": self.hq.pk, "code": "SW", "name": "Other"})
        self.assertIn("code", twice.json()["error"]["fields"])
        foreign_head = self.api("POST", "/api/v1/departments",
                                {"branch_id": self.hq.pk, "code": "QA", "name": "QA",
                                 "head_employee_id": self.outsider.pk})
        self.assertIn("head_employee_id", foreign_head.json()["error"]["fields"])
        foreign_branch = self.api("POST", "/api/v1/departments",
                                  {"branch_id": self.other_branch.pk, "code": "QA", "name": "QA"})
        self.assertIn("branch_id", foreign_branch.json()["error"]["fields"])

    def test_the_branch_cannot_be_changed(self):
        department = self.create()
        with use_company(self.company):
            north = Branch.objects.create(company=self.company, code="NTH", name="North")
        moved = self.api("PATCH", f"/api/v1/departments/{department['id']}",
                         {"branch_id": north.pk, "name": "Software Dev"})
        self.assertEqual(moved.status_code, 200, moved.content)
        self.assertEqual((moved.json()["branch"]["id"], moved.json()["name"]),
                         (self.hq.pk, "Software Dev"))

    def test_status_list_filter_and_not_found(self):
        department = self.create()
        off = self.api("POST", f"/api/v1/departments/{department['id']}/status",
                       {"status": "inactive"})
        self.assertEqual(off.json()["status"], "inactive")
        rows = self.api("GET", "/api/v1/departments", query=f"branch_id={self.hq.pk}").json()
        self.assertEqual(rows["count"], 1)
        bad = self.api("GET", "/api/v1/departments", query="branch_id=abc")
        self.assertEqual(bad.status_code, 422)
        with use_company(self.other):
            theirs = Department.objects.create(company=self.other, branch=self.other_branch,
                                               code="X", name="X")
        self.assertEqual(self.api("GET", f"/api/v1/departments/{theirs.pk}").status_code, 404)

    def test_copy_between_branches(self):
        self.create(designations=[{"code": "SE", "name": "Software Engineer"}])
        with use_company(self.company):
            north = Branch.objects.create(company=self.company, code="NTH", name="North")
        body = {"source_branch_id": self.hq.pk, "target_branch_id": north.pk}
        first = self.api("POST", "/api/v1/departments/copy", body)
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual([d["code"] for d in first.json()["created"]], ["SW"])
        second = self.api("POST", "/api/v1/departments/copy", body).json()
        self.assertEqual((second["created"], second["skipped"]), ([], ["Software"]))
        same = self.api("POST", "/api/v1/departments/copy",
                        {"source_branch_id": north.pk, "target_branch_id": north.pk})
        self.assertIn("target_branch_id", same.json()["error"]["fields"])


class DesignationTests(CompanyApiTestCase):
    def setUp(self):
        super().setUp()
        with use_company(self.company):
            self.software = Department.objects.create(company=self.company, branch=self.hq,
                                                      code="SW", name="Software")
            self.sales = Department.objects.create(company=self.company, branch=self.hq,
                                                   code="SL", name="Sales")
            self.manager = Designation.objects.create(company=self.company,
                                                      department=self.software,
                                                      code="EM", name="Engineering Manager")

    def test_create_change_and_status(self):
        made = self.api("POST", "/api/v1/designations",
                        {"department_id": self.software.pk, "code": "SE",
                         "name": "Software Engineer", "parent_id": self.manager.pk})
        self.assertEqual(made.status_code, 201, made.content)
        title = made.json()
        self.assertEqual((title["parent"]["id"], title["hierarchy_level"]), (self.manager.pk, 1))
        renamed = self.api("PATCH", f"/api/v1/designations/{title['id']}",
                           {"name": "Senior Software Engineer"})
        self.assertEqual(renamed.json()["name"], "Senior Software Engineer")
        off = self.api("POST", f"/api/v1/designations/{title['id']}/status", {"status": "inactive"})
        self.assertEqual(off.json()["status"], "inactive")
        rows = self.api("GET", "/api/v1/designations",
                        query=f"department_id={self.software.pk}").json()
        self.assertEqual(rows["count"], 2)

    def test_a_parent_from_another_department_is_refused(self):
        response = self.api("POST", "/api/v1/designations",
                            {"department_id": self.sales.pk, "code": "SR", "name": "Sales Rep",
                             "parent_id": self.manager.pk})
        self.assertEqual(response.status_code, 422)
        self.assertIn("parent_id", response.json()["error"]["fields"])

    def test_unknown_fields_are_refused(self):
        response = self.api("POST", "/api/v1/designations",
                            {"department_id": self.software.pk, "code": "X", "name": "X",
                             "salary": 100})
        self.assertEqual(self.code_of(response), "unknown_field")
