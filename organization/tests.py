"""Organization structure: root list ownership, company relations, tenant safety."""

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
        # Designations are a flat root list: they belong to no department.
        self.developer = Designation.objects.create(code="DEV", name="Developer")
        self.manager = Designation.objects.create(code="MGR", name="Manager")

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

    def test_root_rows_are_not_tenant_scoped(self):
        """Root owns these lists: they read the same from inside any tenant."""
        with use_company(self.company_a):
            self.assertEqual(Department.objects.count(), 2)
        with use_company(self.company_b):
            self.assertEqual(Department.objects.count(), 2)
        # And with no tenant context at all, which a TenantOwned model refuses.
        self.assertEqual(Department.objects.count(), 2)

    def test_two_companies_share_one_root_department(self):
        with use_company(self.company_b):
            adopted = CompanyDepartment.objects.create(
                branch=self.branch_b, department=self.software
            )
        self.assertEqual(adopted.department_id, self.dept_a.department_id)
        self.assertEqual(Department.objects.filter(name="Software").count(), 1)

    def test_duplicate_root_department_name_rejected(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Department.objects.create(code="SW2", name="Software")

    def test_designation_name_is_unique_platform_wide(self):
        """One flat list, so a second "Developer" is a duplicate, not a variant."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Designation.objects.create(code="DEV2", name="Developer")

    def test_designation_code_is_unique_platform_wide(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Designation.objects.create(code="DEV", name="Another Developer")

    def test_a_designation_belongs_to_no_department(self):
        field_names = {f.name for f in Designation._meta.get_fields()}
        self.assertNotIn("department", field_names)

    # --- adoption rows are company-specific -----------------------------

    def test_company_department_rows_are_scoped_to_their_company(self):
        with use_company(self.company_b):
            other = CompanyDepartment.objects.create(
                branch=self.branch_b, department=self.software
            )
            self.assertEqual(list(CompanyDepartment.objects.all()), [other])
        with use_company(self.company_a):
            self.assertEqual(list(CompanyDepartment.objects.all()), [self.dept_a])

    def test_branch_cannot_add_the_same_department_twice(self):
        with use_company(self.company_a):
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    CompanyDepartment.objects.create(
                        branch=self.branch_a, department=self.software
                    )

    def test_company_department_cannot_reference_another_companys_branch(self):
        with use_company(self.company_a):
            adoption = CompanyDepartment(
                branch=self.branch_b, department=self.hr
            )
            adoption.company = self.company_a
            with self.assertRaises(ValidationError):
                adoption.full_clean()

    def test_any_designation_may_be_assigned_to_any_of_the_companys_departments(self):
        """The relation is the company's to decide, not root's."""
        with use_company(self.company_a):
            hr_dept = CompanyDepartment.objects.create(
                branch=self.branch_a, department=self.hr
            )
            for company_department in (self.dept_a, hr_dept):
                link = CompanyDesignation(
                    company_department=company_department, designation=self.manager
                )
                link.company = self.company_a
                link.full_clean()
                link.save()
            # The same root Manager now sits under both Software and HR.
            self.assertEqual(
                CompanyDesignation.objects.filter(designation=self.manager).count(), 2
            )

    def test_two_companies_may_place_one_designation_differently(self):
        with use_company(self.company_a):
            link_a = CompanyDesignation(
                company_department=self.dept_a, designation=self.manager
            )
            link_a.company = self.company_a
            link_a.full_clean()
            link_a.save()
        with use_company(self.company_b):
            hr_dept_b = CompanyDepartment.objects.create(
                branch=self.branch_b, department=self.hr
            )
            link_b = CompanyDesignation(
                company_department=hr_dept_b, designation=self.manager
            )
            link_b.company = self.company_b
            link_b.full_clean()
            link_b.save()
        # Company A files Manager under Software, Company B under HR.
        self.assertEqual(link_a.company_department.department_id, self.software.pk)
        self.assertEqual(link_b.company_department.department_id, self.hr.pk)

    def test_company_department_reads_its_name_and_code_from_root(self):
        """No local copy of the name exists, so it cannot drift from root's."""
        self.assertEqual(self.dept_a.name, "Software")
        self.assertEqual(self.dept_a.code, "SW")
        self.software.name = "Software Engineering"
        self.software.save()
        self.dept_a.refresh_from_db()
        self.assertEqual(self.dept_a.name, "Software Engineering")
