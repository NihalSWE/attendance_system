"""View-level tests for the organisation screens.

These exist because the catalogue refactor shipped a broken branch list that a
fully green suite did not catch: ``Count("departments")`` is a *string*, so it
imports nothing, resolves nothing, and raises only when the query actually
runs. ``manage.py check`` and ``makemigrations --check`` are both blind to it.

The rule these encode: every page gets a logged-in request that asserts 200
**and** reads a real value off the response. A page that renders is not proof —
a stale template accessor renders empty and looks fine.
"""

from datetime import datetime, time, timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import CompanyMembership
from common.tenant import use_company
from organization.catalogue import adopt_department, adopt_designation
from organization.models import Branch, Department
from scheduling.models import CompanyAttendanceSettings, Shift
from tenants.models import Company


def dt(year, month, day):
    return datetime(year, month, day, tzinfo=dt_timezone.utc)


class OrganisationViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.company, self.user, self.branch = self._company("A", "admin-a@example.test")
        self.other_company, self.other_user, self.other_branch = self._company(
            "B", "admin-b@example.test"
        )
        with use_company(self.company):
            self.department = adopt_department(self.branch, "SW", "Software")
            adopt_designation(self.department, "DEV", "Developer")
        self.client.force_login(self.user)

    def _company(self, code, email):
        company = Company.objects.create(
            code=code, slug=code.lower(), name=f"Company {code}"
        )
        user = get_user_model().objects.create_user(email=email, password="pw-12345678")
        CompanyMembership.all_objects.create(
            company=company, user=user, role="company_admin", status="active"
        )
        with use_company(company):
            branch = Branch.objects.create(code="HQ", name=f"{code} HQ", is_default=True)
            shift = Shift.objects.create(
                code="GEN", name="General", start_time=time(9, 0),
                end_time=time(18, 0), scheduled_minutes=540,
            )
            CompanyAttendanceSettings.objects.create(
                company_shift=shift, effective_from=dt(2026, 1, 1)
            )
        return company, user, branch

    # --- branch list ------------------------------------------------------

    def test_branch_list_renders(self):
        response = self.client.get(reverse("organization:branch_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A HQ")

    def test_branch_list_counts_adopted_departments(self):
        """The regression test for the bug this module was written for.

        The count must come from the adoption rows. Asserting the number, not
        just a 200, is the point: the previous accessor raised FieldError, and
        a merely-renaming mistake would render blank instead.
        """
        response = self.client.get(reverse("organization:branch_list"))
        self.assertEqual(response.status_code, 200)
        branch = response.context["page"].object_list[0]
        self.assertEqual(branch.department_count, 1)

        with use_company(self.company):
            adopt_department(self.branch, "HR", "Human Resources")
        response = self.client.get(reverse("organization:branch_list"))
        self.assertEqual(response.context["page"].object_list[0].department_count, 2)

    def test_branch_list_excludes_other_companies(self):
        response = self.client.get(reverse("organization:branch_list"))
        self.assertNotContains(response, "B HQ")

    def test_branch_list_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("organization:branch_list"))
        self.assertEqual(response.status_code, 302)

    # --- branch write paths ----------------------------------------------

    def test_branch_create_form_renders(self):
        response = self.client.get(reverse("organization:branch_create"))
        self.assertEqual(response.status_code, 200)

    def test_branch_edit_form_renders(self):
        response = self.client.get(
            reverse("organization:branch_edit", args=[self.branch.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A HQ")

    def test_branch_edit_rejects_another_companys_branch(self):
        response = self.client.get(
            reverse("organization:branch_edit", args=[self.other_branch.pk])
        )
        self.assertIn(response.status_code, (403, 404))


class CompanyShellViewTests(TestCase):
    """The shared shell pages, which had no view coverage at all."""

    def setUp(self):
        self.client = Client()
        self.company = Company.objects.create(code="A", slug="a", name="Company A")
        self.user = get_user_model().objects.create_user(
            email="admin@example.test", password="pw-12345678"
        )
        CompanyMembership.all_objects.create(
            company=self.company, user=self.user, role="company_admin", status="active"
        )
        with use_company(self.company):
            self.branch = Branch.objects.create(
                code="HQ", name="Head Office", is_default=True
            )
            self.department = adopt_department(self.branch, "SW", "Software")
            adopt_designation(self.department, "DEV", "Developer")
        self.client.force_login(self.user)

    def test_dashboard_renders(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Company A")

    def test_department_list_shows_the_department_name(self):
        response = self.client.get(reverse("department_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Software")
        self.assertContains(response, "Head Office")

    def test_department_list_counts_job_titles(self):
        response = self.client.get(reverse("department_list"))
        # The reverse accessor is tenant-scoped, so it needs a company context
        # here exactly as a view gets one from TenantMiddleware.
        with use_company(self.company):
            adoption = Department.objects.get(pk=self.department.pk)
            self.assertEqual(adoption.designations.count(), 1)
        self.assertContains(response, "Software")

    def test_employee_list_renders(self):
        response = self.client.get(reverse("employee_list"))
        self.assertEqual(response.status_code, 200)

    def test_shell_pages_require_login(self):
        self.client.logout()
        for name in ("dashboard", "department_list", "employee_list"):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 302)
