"""Organization structure tests: constraints, hierarchy rules, tenant safety."""

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

    # --- cross-company protection ---------------------------------------

    def test_department_cannot_reference_another_companys_branch(self):
        with use_company(self.company_a):
            dept = Department(branch=self.branch_b, code="X", name="Cross")
            dept.company = self.company_a
            with self.assertRaises(ValidationError):
                dept.full_clean()

    # --- designation hierarchy ------------------------------------------

    def test_hierarchy_level_derived_from_parent(self):
        with use_company(self.company_a):
            lead = Designation.objects.create(
                department=self.dept_a, code="SDL", name="Senior Developer"
            )
            junior = Designation.objects.create(
                department=self.dept_a, code="JDV", name="Junior Developer", parent=lead
            )
            self.assertEqual(lead.hierarchy_level, 0)
            self.assertEqual(junior.hierarchy_level, 1)

    def test_designation_cannot_be_its_own_parent(self):
        with use_company(self.company_a):
            d = Designation.objects.create(
                department=self.dept_a, code="SDL", name="Senior Developer"
            )
            d.parent = d
            with self.assertRaises(ValidationError):
                d.full_clean()

    def test_designation_cycle_rejected(self):
        with use_company(self.company_a):
            top = Designation.objects.create(
                department=self.dept_a, code="MGR", name="Manager"
            )
            mid = Designation.objects.create(
                department=self.dept_a, code="LEAD", name="Lead", parent=top
            )
            # Making the top report to its own descendant would close a cycle.
            top.parent = mid
            with self.assertRaises(ValidationError):
                top.full_clean()

    def test_parent_must_be_in_same_department(self):
        with use_company(self.company_a):
            other_dept = Department.objects.create(
                branch=self.branch_a, code="HR", name="Human Resources"
            )
            hr_head = Designation.objects.create(
                department=other_dept, code="HRM", name="HR Manager"
            )
            dev = Designation(
                department=self.dept_a, code="DEV", name="Developer", parent=hr_head
            )
            dev.company = self.company_a
            with self.assertRaises(ValidationError):
                dev.full_clean()
