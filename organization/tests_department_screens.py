"""The company's own Department and Designation screens.

Departments and designations went back to company level on 2026-09-19 and the
screens were rebuilt (Departments repurposed, Designations given their own
page), but they shipped without tests. These cover the refusals that carry the
rules, not just that the pages render: a duplicate code, a department a
branch already has, removing a title somebody still holds, and the branch /
department that is fixed once a row exists.

Every page assertion reads a real value off the response, following the rule in
tests_views.py: a page that merely renders is not proof.
"""

from datetime import datetime, time, timezone as dt_timezone
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import CompanyMembership
from common.tenant import use_company
from employees.services import create_employee
from organization import adoption_services as services
from organization.catalogue import adopt_department, adopt_designation
from organization.models import Branch, Department, Designation
from scheduling.models import CompanyAttendanceSettings, Shift
from tenants.models import Company


def dt(year, month, day):
    return datetime(year, month, day, tzinfo=dt_timezone.utc)


class DepartmentScreenCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.company = Company.objects.create(code="A", slug="a", name="Company A")
        self.owner = get_user_model().objects.create_user(
            email="owner@screens.test", password="pw-12345678")
        CompanyMembership.all_objects.create(
            company=self.company, user=self.owner, role="company_admin", status="active")
        with use_company(self.company):
            self.branch = Branch.objects.create(code="HQ", name="Head Office", is_default=True)
            self.other_branch = Branch.objects.create(code="BR2", name="Second Branch")
            shift = Shift.objects.create(
                code="GEN", name="General", start_time=time(9), end_time=time(18),
                scheduled_minutes=540)
            CompanyAttendanceSettings.objects.create(
                company_shift=shift, effective_from=dt(2026, 1, 1))
            self.software = adopt_department(self.branch, "SW", "Software")
            self.developer = adopt_designation(self.software, "DEV", "Developer")
        self.client.force_login(self.owner)

    def hire(self, department, designation, code="E1", name="Rahim"):
        return create_employee(
            company=self.company, first_name=name, employee_code=code,
            branch=self.branch, department=department, designation=designation,
            effective_from=dt(2026, 1, 1), pay_basis="monthly",
            base_rate=Decimal("30000"),
        )["employee"]


class DepartmentPageTests(DepartmentScreenCase):
    def test_the_list_shows_the_department_and_its_branch(self):
        page = self.client.get(reverse("organization:adoption_list"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Software")
        self.assertContains(page, "Head Office")

    def test_adding_a_department_through_the_page(self):
        response = self.client.post(reverse("organization:adoption_create"), {
            "branch": self.branch.pk, "code": "HR", "name": "Human Resources",
            "description": "", "status": "active",
        })
        self.assertRedirects(response, reverse("organization:adoption_list"))
        with use_company(self.company):
            self.assertTrue(Department.objects.filter(code="HR").exists())

    def test_a_branch_cannot_hold_the_same_code_twice(self):
        page = self.client.post(reverse("organization:adoption_create"), {
            "branch": self.branch.pk, "code": "SW", "name": "Different name",
            "description": "", "status": "active",
        })
        self.assertEqual(page.status_code, 200)   # redisplayed, not saved
        with use_company(self.company):
            self.assertEqual(Department.objects.filter(code="SW").count(), 1)

    def test_the_same_code_is_free_in_another_branch(self):
        response = self.client.post(reverse("organization:adoption_create"), {
            "branch": self.other_branch.pk, "code": "SW", "name": "Software",
            "description": "", "status": "active",
        })
        self.assertRedirects(response, reverse("organization:adoption_list"))
        with use_company(self.company):
            self.assertEqual(Department.objects.filter(code="SW").count(), 2)

    def test_editing_renames_it_and_the_branch_is_fixed(self):
        response = self.client.post(
            reverse("organization:adoption_edit", args=[self.software.pk]), {
                "branch": self.other_branch.pk,   # ignored: fixed once created
                "code": "SW", "name": "Software Engineering",
                "description": "", "status": "active",
            })
        self.assertRedirects(response, reverse("organization:adoption_list"))
        with use_company(self.company):
            self.software.refresh_from_db()
        self.assertEqual(self.software.name, "Software Engineering")
        self.assertEqual(self.software.branch_id, self.branch.pk)

    def test_a_department_with_employees_cannot_be_deactivated(self):
        self.hire(self.software, self.developer)
        with self.assertRaises(ValidationError):
            services.set_adoption_status(
                actor=self.owner, company_id=self.company.pk,
                adoption_id=self.software.pk, status="inactive")
        with use_company(self.company):
            self.software.refresh_from_db()
        self.assertEqual(self.software.status, "active")

    def test_an_empty_department_deactivates(self):
        services.set_adoption_status(
            actor=self.owner, company_id=self.company.pk,
            adoption_id=self.software.pk, status="inactive")
        with use_company(self.company):
            self.software.refresh_from_db()
        self.assertEqual(self.software.status, "inactive")

    def test_copying_to_another_branch_brings_the_designations(self):
        created, skipped = services.copy_adoptions_between_branches(
            actor=self.owner, company_id=self.company.pk,
            source_branch=self.branch, target_branch=self.other_branch)
        self.assertEqual([d.code for d in created], ["SW"])
        self.assertEqual(skipped, [])
        with use_company(self.company):
            copy = Department.objects.get(branch=self.other_branch, code="SW")
            self.assertEqual(
                list(copy.designations.values_list("code", flat=True)), ["DEV"])
            # The head is deliberately not copied.
            self.assertIsNone(copy.head_id)

    def test_copying_twice_skips_what_is_already_there(self):
        services.copy_adoptions_between_branches(
            actor=self.owner, company_id=self.company.pk,
            source_branch=self.branch, target_branch=self.other_branch)
        created, skipped = services.copy_adoptions_between_branches(
            actor=self.owner, company_id=self.company.pk,
            source_branch=self.branch, target_branch=self.other_branch)
        self.assertEqual(created, [])
        self.assertEqual(skipped, ["Software"])


class DesignationPageTests(DepartmentScreenCase):
    def test_the_list_shows_the_title_its_department_and_branch(self):
        page = self.client.get(reverse("organization:designation_list"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Developer")
        self.assertContains(page, "Software")
        self.assertContains(page, "Head Office")

    def test_adding_a_designation_through_the_page(self):
        response = self.client.post(reverse("organization:designation_create"), {
            "department": self.software.pk, "code": "SDV",
            "name": "Senior Developer", "status": "active",
        })
        self.assertRedirects(response, reverse("organization:designation_list"))
        with use_company(self.company):
            self.assertTrue(Designation.objects.filter(code="SDV").exists())

    def test_a_department_cannot_hold_the_same_code_twice(self):
        page = self.client.post(reverse("organization:designation_create"), {
            "department": self.software.pk, "code": "DEV",
            "name": "Another Developer", "status": "active",
        })
        self.assertEqual(page.status_code, 200)
        with use_company(self.company):
            self.assertEqual(Designation.objects.filter(code="DEV").count(), 1)

    def test_the_same_code_is_free_in_another_department(self):
        with use_company(self.company):
            hr = adopt_department(self.branch, "HR", "Human Resources")
        response = self.client.post(reverse("organization:designation_create"), {
            "department": hr.pk, "code": "DEV", "name": "Developer",
            "status": "active",
        })
        self.assertRedirects(response, reverse("organization:designation_list"))
        with use_company(self.company):
            self.assertEqual(Designation.objects.filter(code="DEV").count(), 2)

    def test_editing_renames_it_and_the_department_is_fixed(self):
        with use_company(self.company):
            hr = adopt_department(self.branch, "HR", "Human Resources")
        response = self.client.post(
            reverse("organization:designation_edit", args=[self.developer.pk]), {
                "department": hr.pk,          # ignored: fixed once created
                "code": "DEV", "name": "Software Developer", "status": "active",
            })
        self.assertRedirects(response, reverse("organization:designation_list"))
        with use_company(self.company):
            self.developer.refresh_from_db()
        self.assertEqual(self.developer.name, "Software Developer")
        self.assertEqual(self.developer.department_id, self.software.pk)

    def test_a_parent_must_be_in_the_same_department(self):
        with use_company(self.company):
            hr = adopt_department(self.branch, "HR", "Human Resources")
            hr_manager = adopt_designation(hr, "HRM", "HR Manager")
        page = self.client.post(reverse("organization:designation_create"), {
            "department": self.software.pk, "code": "SDV",
            "name": "Senior Developer", "parent": hr_manager.pk, "status": "active",
        })
        self.assertEqual(page.status_code, 200)
        with use_company(self.company):
            self.assertFalse(Designation.objects.filter(code="SDV").exists())

    def test_a_designation_somebody_holds_cannot_be_deactivated(self):
        self.hire(self.software, self.developer)
        with self.assertRaises(ValidationError):
            services.set_designation_status(
                actor=self.owner, company_id=self.company.pk,
                designation_id=self.developer.pk, status="inactive")
        with use_company(self.company):
            self.developer.refresh_from_db()
        self.assertEqual(self.developer.status, "active")

    def test_an_unused_designation_deactivates_from_the_list(self):
        response = self.client.post(
            reverse("organization:designation_status", args=[self.developer.pk]),
            {"status": "inactive"})
        self.assertRedirects(response, reverse("organization:designation_list"))
        with use_company(self.company):
            self.developer.refresh_from_db()
        self.assertEqual(self.developer.status, "inactive")


class WhoMayManageStructureTests(DepartmentScreenCase):
    """Structure stays with the owner and company administrator."""

    def setUp(self):
        super().setUp()
        self.clerk = get_user_model().objects.create_user(
            email="clerk@screens.test", password="pw-12345678")
        CompanyMembership.all_objects.create(
            company=self.company, user=self.clerk, role="employee", status="active")

    def test_an_employee_login_is_sent_to_my_account(self):
        self.client.force_login(self.clerk)
        for name in ("organization:adoption_list", "organization:designation_list",
                     "organization:adoption_create", "organization:designation_create"):
            with self.subTest(page=name):
                self.assertRedirects(self.client.get(reverse(name)), reverse("me:home"))

    def test_the_services_refuse_them_too(self):
        with self.assertRaises(PermissionDenied):
            services.adopt_department(
                actor=self.clerk, company_id=self.company.pk,
                values={"branch": self.branch, "code": "X", "name": "Crafted"})
        with self.assertRaises(PermissionDenied):
            services.create_designation(
                actor=self.clerk, company_id=self.company.pk,
                values={"department": self.software, "code": "X", "name": "Crafted"})
