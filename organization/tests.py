"""Organization structure: company-owned departments and designations, tenant safety."""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from common.tenant import use_company
from organization.models import Branch, Department, Designation
from tenants.models import Company


class OrganizationTests(TestCase):
    def setUp(self):
        self.company_a = Company.objects.create(code="A", slug="a", name="Company A")
        self.company_b = Company.objects.create(code="B", slug="b", name="Company B")
        with use_company(self.company_a):
            self.branch_a = Branch.objects.create(
                code="HQ", name="Head Office", is_default=True
            )
            self.dept_a = Department.objects.create(
                branch=self.branch_a, code="SW", name="Software"
            )
            self.dev_a = Designation.objects.create(
                department=self.dept_a, code="DEV", name="Developer"
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
                    Branch.objects.create(code="B2", name="Second HQ", is_default=True)

    # --- departments are company-owned ----------------------------------

    def test_departments_are_scoped_to_their_company(self):
        with use_company(self.company_a):
            self.assertEqual(list(Department.objects.all()), [self.dept_a])
        with use_company(self.company_b):
            self.assertEqual(list(Department.objects.all()), [])

    def test_same_department_code_allowed_in_different_companies(self):
        with use_company(self.company_b):
            Department.objects.create(branch=self.branch_b, code="SW", name="Software")
        self.assertEqual(Department.all_objects.filter(code="SW").count(), 2)

    def test_duplicate_department_code_in_one_branch_rejected(self):
        with use_company(self.company_a):
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    Department.objects.create(branch=self.branch_a, code="SW", name="Other")

    def test_duplicate_department_name_in_one_branch_rejected(self):
        with use_company(self.company_a):
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    Department.objects.create(branch=self.branch_a, code="SW2", name="Software")

    # --- designations belong to a department ----------------------------

    def test_designations_are_scoped_to_their_company(self):
        with use_company(self.company_a):
            self.assertEqual(list(Designation.objects.all()), [self.dev_a])
        with use_company(self.company_b):
            self.assertEqual(list(Designation.objects.all()), [])

    def test_duplicate_designation_code_in_one_department_rejected(self):
        with use_company(self.company_a):
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    Designation.objects.create(
                        department=self.dept_a, code="DEV", name="Another"
                    )

    def test_same_designation_code_allowed_in_different_departments(self):
        with use_company(self.company_a):
            hr = Department.objects.create(branch=self.branch_a, code="HR", name="Human Resources")
            Designation.objects.create(department=hr, code="DEV", name="Developer")
            self.assertEqual(Designation.objects.filter(code="DEV").count(), 2)

    def test_a_designation_belongs_to_a_department(self):
        field_names = {f.name for f in Designation._meta.get_fields()}
        self.assertIn("department", field_names)
        self.assertIn("parent", field_names)

    def test_parent_must_be_in_the_same_department(self):
        with use_company(self.company_a):
            hr = Department.objects.create(branch=self.branch_a, code="HR", name="Human Resources")
            hr_mgr = Designation.objects.create(department=hr, code="HRM", name="HR Manager")
            child = Designation(
                department=self.dept_a, code="SDV", name="Senior Developer", parent=hr_mgr
            )
            child.company = self.company_a
            with self.assertRaises(ValidationError):
                child.full_clean()

    def test_a_designation_cannot_be_its_own_parent(self):
        with use_company(self.company_a):
            self.dev_a.parent = self.dev_a
            with self.assertRaises(ValidationError):
                self.dev_a.full_clean()

    def test_hierarchy_level_follows_the_parent_chain(self):
        with use_company(self.company_a):
            senior = Designation.objects.create(
                department=self.dept_a, code="SDV", name="Senior Developer",
                parent=self.dev_a,
            )
            self.assertEqual(self.dev_a.hierarchy_level, 0)
            self.assertEqual(senior.hierarchy_level, 1)

