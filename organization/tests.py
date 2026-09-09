"""Organization structure tests: catalogue ownership, adoption, tenant safety."""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from common.tenant import use_company
from organization.models import (
    Branch,
    CompanyDepartment,
    CompanyDesignation,
    Department,
    Designation,
)
from tenants.models import Company


class OrganizationTests(TestCase):
    def setUp(self):
        # Root-owned catalogue: created once, shared by every company.
        self.software = Department.objects.create(code="SW", name="Software")
        self.hr = Department.objects.create(code="HR", name="Human Resources")
        self.developer = Designation.objects.create(
            department=self.software, code="DEV", name="Developer"
        )
        self.hr_manager = Designation.objects.create(
            department=self.hr, code="HRM", name="HR Manager"
        )

        self.company_a = Company.objects.create(code="A", slug="a", name="Company A")
        self.company_b = Company.objects.create(code="B", slug="b", name="Company B")
        with use_company(self.company_a):
            self.branch_a = Branch.objects.create(
                code="HQ", name="Head Office", is_default=True
            )
            self.dept_a = CompanyDepartment.objects.create(
                branch=self.branch_a, department=self.software
            )
        with use_company(self.company_b):
            self.branch_b = Branch.objects.create(
                code="HQ", name="B Head Office", is_default=True
            )

    # --- tenant scoping -------------------------------------------------

    def test_branches_are_scoped_to_their_company(self):
        with use_company(self.company_a):
            self.assertEqual(list(Branch.objects.all()), [self.branch_a])
        with use_company(self.company_b):
            self.assertEqual(list(Branch.objects.all()), [self.branch_b])

    def test_same_branch_code_allowed_in_different_companies(self):
        # Both companies use code "HQ"; uniqueness is per company, not global.
        self.assertEqual(Branch.all_objects.filter(code="HQ").count(), 2)

    def test_duplicate_branch_code_in_same_company_rejected(self):
        with use_company(self.company_a):
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    Branch.objects.create(code="HQ", name="Duplicate")

    def test_second_active_default_branch_rejected(self):
        with use_company(self.company_a):
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    Branch.objects.create(
                        code="B2", name="Second HQ", is_default=True
                    )

    # --- the catalogue is global ----------------------------------------

    def test_catalogue_rows_are_not_tenant_scoped(self):
        """Root owns the catalogue: it reads the same from inside any tenant."""
        with use_company(self.company_a):
            self.assertEqual(Department.objects.count(), 2)
        with use_company(self.company_b):
            self.assertEqual(Department.objects.count(), 2)
        # And with no tenant context at all, which a TenantOwned model refuses.
        self.assertEqual(Department.objects.count(), 2)

    def test_two_companies_share_one_catalogue_department(self):
        with use_company(self.company_b):
            adopted = CompanyDepartment.objects.create(
                branch=self.branch_b, department=self.software
            )
        self.assertEqual(adopted.department_id, self.dept_a.department_id)
        self.assertEqual(Department.objects.filter(name="Software").count(), 1)

    def test_duplicate_catalogue_department_name_rejected(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Department.objects.create(code="SW2", name="Software")

    def test_catalogue_designation_name_unique_within_department(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Designation.objects.create(
                    department=self.software, code="DEV2", name="Developer"
                )

    def test_same_designation_name_allowed_in_another_department(self):
        # "Manager" is a real title in both places; the department separates them.
        Designation.objects.create(department=self.software, code="SWM", name="Manager")
        Designation.objects.create(department=self.hr, code="HRD", name="Manager")
        self.assertEqual(Designation.objects.filter(name="Manager").count(), 2)

    # --- adoption rows are company-specific -----------------------------

    def test_adoption_rows_are_scoped_to_their_company(self):
        with use_company(self.company_b):
            other = CompanyDepartment.objects.create(
                branch=self.branch_b, department=self.software
            )
            self.assertEqual(list(CompanyDepartment.objects.all()), [other])
        with use_company(self.company_a):
            self.assertEqual(list(CompanyDepartment.objects.all()), [self.dept_a])

    def test_branch_cannot_adopt_the_same_department_twice(self):
        with use_company(self.company_a):
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    CompanyDepartment.objects.create(
                        branch=self.branch_a, department=self.software
                    )

    def test_adoption_cannot_reference_another_companys_branch(self):
        with use_company(self.company_a):
            adoption = CompanyDepartment(
                branch=self.branch_b, department=self.hr
            )
            adoption.company = self.company_a
            with self.assertRaises(ValidationError):
                adoption.full_clean()

    def test_designation_must_belong_to_the_selected_department(self):
        """An HR title cannot be filed under the company's Software department."""
        with use_company(self.company_a):
            adoption = CompanyDesignation(
                company_department=self.dept_a, designation=self.hr_manager
            )
            adoption.company = self.company_a
            with self.assertRaises(ValidationError):
                adoption.full_clean()

    def test_matching_designation_and_department_accepted(self):
        with use_company(self.company_a):
            adoption = CompanyDesignation(
                company_department=self.dept_a, designation=self.developer
            )
            adoption.company = self.company_a
            adoption.full_clean()
            adoption.save()
        self.assertEqual(adoption.name, "Developer")
        self.assertEqual(adoption.branch, self.branch_a)

    def test_adoption_reads_its_name_and_code_from_the_catalogue(self):
        """No local copy of the name exists, so it cannot drift from root's."""
        self.assertEqual(self.dept_a.name, "Software")
        self.assertEqual(self.dept_a.code, "SW")
        self.software.name = "Software Engineering"
        self.software.save()
        self.dept_a.refresh_from_db()
        self.assertEqual(self.dept_a.name, "Software Engineering")
