"""Root department and designation lists: services, authorization and screens.

Both lists are shared by every tenant, so the tests that matter most are the
negative ones — a company administrator must not reach these screens, and a
crafted POST must not reach a column the form does not expose.

Designations are flat: they carry no department, and which departments use
one is each company decision on CompanyDesignation.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import CompanyMembership
from auditlog.models import AuditLog
from common.tenant import use_company
from organization import catalogue_services as services
from organization.catalogue import adopt_department, adopt_designation
from organization.models import Branch, Department, Designation
from tenants.models import Company

User = get_user_model()


class CatalogueServiceTests(TestCase):
    def setUp(self):
        self.root = User.objects.create_superuser(
            email="root@example.test", password="pw-12345678"
        )
        self.company = Company.objects.create(code="A", slug="a", name="Company A")
        self.member = User.objects.create_user(
            email="member@example.test", password="pw-12345678"
        )
        CompanyMembership.all_objects.create(
            company=self.company, user=self.member, role="company_admin", status="active"
        )

    # --- authorization ----------------------------------------------------

    def test_company_administrator_cannot_write_the_catalogue(self):
        for call in (
            lambda: services.create_department(
                actor=self.member, values={"code": "X", "name": "X"}
            ),
            lambda: services.create_designation(
                actor=self.member, values={"code": "X", "name": "X"}
            ),
        ):
            with self.assertRaises(PermissionDenied):
                call()

    def test_anonymous_cannot_write_the_catalogue(self):
        class Anon:
            is_authenticated = False
            is_active = False
            is_superuser = False

        with self.assertRaises(PermissionDenied):
            services.create_department(actor=Anon(), values={"code": "X", "name": "X"})

    # --- departments ------------------------------------------------------

    def test_create_department_is_audited(self):
        department = services.create_department(
            actor=self.root, values={"code": "HR", "name": "Human Resources"}
        )
        self.assertEqual(department.name, "Human Resources")
        self.assertTrue(
            AuditLog.objects.filter(
                action="catalogue.department.created", object_id=str(department.pk)
            ).exists()
        )

    def test_department_name_is_unique_platform_wide(self):
        services.create_department(actor=self.root, values={"code": "HR", "name": "HR Dept"})
        with self.assertRaises(ValidationError):
            services.create_department(
                actor=self.root, values={"code": "HR2", "name": "HR Dept"}
            )

    def test_writable_field_whitelist_ignores_extra_keys(self):
        """A crafted POST must not set a column the form does not expose."""
        department = services.create_department(
            actor=self.root,
            values={"code": "OPS", "name": "Operations", "id": 9999, "created_by_id": 4242},
        )
        self.assertNotEqual(department.pk, 9999)
        self.assertEqual(department.created_by, self.root)

    def test_deactivating_keeps_the_row_and_its_adoptions(self):
        department = services.create_department(
            actor=self.root, values={"code": "SW", "name": "Software"}
        )
        with use_company(self.company):
            branch = Branch.objects.create(code="HQ", name="HQ", is_default=True)
            adoption = adopt_department(branch, "SW", "Software")

        services.set_department_status(
            actor=self.root, department_id=department.pk, status="inactive"
        )
        department.refresh_from_db()
        self.assertEqual(department.status, "inactive")
        # The adoption survives: deactivation stops new use, it is not a delete.
        self.assertTrue(Department.objects.filter(pk=department.pk).exists())
        with use_company(self.company):
            adoption.refresh_from_db()
            self.assertEqual(adoption.department_id, department.pk)

    # --- designations -----------------------------------------------------

    def test_designation_name_is_unique_platform_wide(self):
        """One Manager for the whole platform, not one per department."""
        services.create_designation(
            actor=self.root, values={"code": "MGR", "name": "Manager"}
        )
        with self.assertRaises(ValidationError):
            services.create_designation(
                actor=self.root, values={"code": "MGR2", "name": "Manager"}
            )
        self.assertEqual(Designation.objects.filter(name="Manager").count(), 1)

    def test_creating_a_designation_ignores_a_department_key(self):
        """A crafted POST must not reach a column the model no longer has."""
        designation = services.create_designation(
            actor=self.root,
            values={"code": "MGR", "name": "Manager", "department": 999},
        )
        self.assertFalse(hasattr(designation, "department_id"))

    def test_renaming_a_designation_keeps_every_company_placement(self):
        """Renaming is safe: a company reads the name through this row."""
        designation = services.create_designation(
            actor=self.root, values={"code": "MGR", "name": "Manager"}
        )
        with use_company(self.company):
            branch = Branch.objects.create(code="HQ", name="HQ", is_default=True)
            company_department = adopt_department(branch, "HR", "HR")
            adopt_designation(company_department, "MGR", "Manager")

        services.update_designation(
            actor=self.root, designation_id=designation.pk,
            values={"name": "Department Manager"},
        )
        with use_company(self.company):
            link = company_department.designations.get()
            self.assertEqual(link.name, "Department Manager")

class CatalogueScreenTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.root = User.objects.create_superuser(
            email="root@example.test", password="pw-12345678"
        )
        self.company = Company.objects.create(code="A", slug="a", name="Company A")
        self.member = User.objects.create_user(
            email="member@example.test", password="pw-12345678"
        )
        CompanyMembership.all_objects.create(
            company=self.company, user=self.member, role="company_admin", status="active"
        )
        self.department = services.create_department(
            actor=self.root, values={"code": "SW", "name": "Software"}
        )
        self.designation = services.create_designation(
            actor=self.root, values={"code": "DEV", "name": "Developer"}
        )

    def _urls(self):
        return [
            reverse("catalogue:department_list"),
            reverse("catalogue:department_create"),
            reverse("catalogue:department_edit", args=[self.department.pk]),
            reverse("catalogue:department_status", args=[self.department.pk]),
            reverse("catalogue:designation_list"),
            reverse("catalogue:designation_create"),
            reverse("catalogue:designation_edit", args=[self.designation.pk]),
            reverse("catalogue:designation_status", args=[self.designation.pk]),
        ]

    def test_every_screen_renders_for_root(self):
        self.client.force_login(self.root)
        for url in self._urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_company_administrator_is_refused_everywhere(self):
        self.client.force_login(self.member)
        for url in self._urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_anonymous_is_redirected_to_login(self):
        for url in self._urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 302)

    def test_department_list_shows_real_counts(self):
        self.client.force_login(self.root)
        response = self.client.get(reverse("catalogue:department_list"))
        self.assertContains(response, "Software")
        row = response.context["page"].object_list[0]
        # No designation count: the two lists are independent now.
        self.assertEqual(row.adoption_count, 0)

    def test_create_department_through_the_form(self):
        self.client.force_login(self.root)
        response = self.client.post(
            reverse("catalogue:department_create"),
            {"code": "hr", "name": "Human Resources", "status": "active"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        created = Department.objects.get(name="Human Resources")
        # Codes are normalised so "hr" and "HR" cannot both exist.
        self.assertEqual(created.code, "HR")

    def test_duplicate_name_is_a_form_error_not_a_server_error(self):
        self.client.force_login(self.root)
        response = self.client.post(
            reverse("catalogue:department_create"),
            {"code": "SW2", "name": "Software", "status": "active"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already exists")
        self.assertEqual(Department.objects.filter(name="Software").count(), 1)

    def test_duplicate_designation_name_is_a_form_error(self):
        self.client.force_login(self.root)
        response = self.client.post(
            reverse("catalogue:designation_create"),
            {"code": "DEV2", "name": "Developer", "status": "active"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already exists")
        self.assertEqual(Designation.objects.filter(name="Developer").count(), 1)

    def test_the_create_form_has_no_department_field(self):
        self.client.force_login(self.root)
        response = self.client.get(reverse("catalogue:designation_create"))
        self.assertNotIn("department", response.context["form"].fields)

    def test_a_designation_is_created_without_any_department(self):
        self.client.force_login(self.root)
        response = self.client.post(
            reverse("catalogue:designation_create"),
            {"code": "QA", "name": "Tester", "status": "active"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Designation.objects.filter(name="Tester").exists())

    def test_designation_usage_names_the_department_of_each_placement(self):
        """Company and branch alone would render two identical rows.

        One company may put the same designation under several of its own
        departments, so the department is the only thing telling the rows
        apart. Losing it here is how the screen silently stopped being
        readable when designations went flat.
        """
        self.client.force_login(self.root)
        with use_company(self.company):
            branch = Branch.objects.create(code="HQ", name="HQ", is_default=True)
            sales = adopt_department(branch, "SL", "Sales")
            production = adopt_department(branch, "PRD", "Production")
            adopt_designation(sales, "DEV", "Developer")
            adopt_designation(production, "DEV", "Developer")

        response = self.client.get(
            reverse("catalogue:designation_edit", args=[self.designation.pk])
        )
        self.assertTrue(response.context["usage_shows_department"])
        self.assertContains(response, "Sales")
        self.assertContains(response, "Production")

    def test_department_usage_has_no_department_column(self):
        """The same template serves both; only designations need the column."""
        self.client.force_login(self.root)
        response = self.client.get(
            reverse("catalogue:department_edit", args=[self.department.pk])
        )
        self.assertFalse(response.context["usage_shows_department"])

    def test_status_screen_deactivates_without_deleting(self):
        self.client.force_login(self.root)
        self.client.post(
            reverse("catalogue:department_status", args=[self.department.pk]),
            {"status": "inactive"},
            follow=True,
        )
        self.department.refresh_from_db()
        self.assertEqual(self.department.status, "inactive")
        self.assertTrue(Department.objects.filter(pk=self.department.pk).exists())

    def test_root_sidebar_links_to_departments_and_designations(self):
        self.client.force_login(self.root)
        response = self.client.get(reverse("catalogue:department_list"))
        self.assertContains(response, reverse("catalogue:department_list"))
        self.assertContains(response, reverse("catalogue:designation_list"))
        # Root must see the platform shell, never the company sidebar.
        self.assertTrue(response.context["platform_surface"])
