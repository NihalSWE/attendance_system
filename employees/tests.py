"""Employee identity, reusable-code history and dated compensation tests."""

from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from common.tenant import use_company
from employees.models import Employee, EmployeeAssignment, EmployeeCompensation
from organization.models import (
    Branch,
    CompanyDepartment,
    CompanyDesignation,
    Department,
    Designation,
)
from tenants.models import Company


def dt(year, month, day):
    return datetime(year, month, day, tzinfo=dt_timezone.utc)


class EmployeeStructureTests(TestCase):
    def setUp(self):
        # Root-owned catalogue.
        self.software = Department.objects.create(code="SW", name="Software")
        self.dev_entry = Designation.objects.create(
            department=self.software, code="DEV", name="Developer"
        )

        self.company = Company.objects.create(code="A", slug="a", name="Company A")
        with use_company(self.company):
            self.branch = Branch.objects.create(
                code="HQ", name="Head Office", is_default=True
            )
            self.department = CompanyDepartment.objects.create(
                branch=self.branch, department=self.software
            )
            self.designation = CompanyDesignation.objects.create(
                company_department=self.department, designation=self.dev_entry
            )
            self.alice = Employee.objects.create(first_name="Alice", last_name="Ahmed")
            self.bob = Employee.objects.create(first_name="Bob", last_name="Barua")

    def _assign(self, employee, code, start, end=None, status="active"):
        return EmployeeAssignment.objects.create(
            employee=employee,
            employee_code=code,
            branch=self.branch,
            department=self.department,
            designation=self.designation,
            effective_from=start,
            effective_to=end,
            status=status,
        )

    # --- reusable employee code (the core invariant) ---------------------

    def test_employee_code_reusable_after_previous_interval_ends(self):
        with use_company(self.company):
            self._assign(self.alice, "E100", dt(2024, 1, 1), dt(2024, 6, 1),
                         status="ended")
            # Bob may take the same code once Alice's interval has closed.
            bobs = self._assign(self.bob, "E100", dt(2024, 6, 1))
            self.assertEqual(bobs.employee_code, "E100")

    def test_overlapping_employee_code_rejected(self):
        with use_company(self.company):
            self._assign(self.alice, "E100", dt(2024, 1, 1), dt(2024, 6, 1),
                         status="ended")
            # Bob starting before Alice's interval closed must be rejected.
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    self._assign(self.bob, "E100", dt(2024, 3, 1))

    def test_history_does_not_transfer_with_a_reused_code(self):
        with use_company(self.company):
            self._assign(self.alice, "E100", dt(2024, 1, 1), dt(2024, 6, 1),
                         status="ended")
            self._assign(self.bob, "E100", dt(2024, 6, 1))
            # The code is shared, but each person's history stays their own.
            self.assertEqual(self.alice.assignments.count(), 1)
            self.assertEqual(self.bob.assignments.count(), 1)
            self.assertNotEqual(self.alice.pk, self.bob.pk)

    def test_employee_cannot_hold_two_overlapping_assignments(self):
        with use_company(self.company):
            self._assign(self.alice, "E100", dt(2024, 1, 1))
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    self._assign(self.alice, "E200", dt(2024, 3, 1))

    def test_cancelled_assignment_does_not_reserve_the_code(self):
        with use_company(self.company):
            self._assign(self.alice, "E100", dt(2024, 1, 1), status="cancelled")
            # A void row must not block a real assignment on the same code.
            bobs = self._assign(self.bob, "E100", dt(2024, 1, 1))
            self.assertEqual(bobs.employee_code, "E100")

    # --- assignment validation -------------------------------------------

    def test_manager_cannot_be_the_employee(self):
        with use_company(self.company):
            a = EmployeeAssignment(
                employee=self.alice, employee_code="E1", branch=self.branch,
                department=self.department, designation=self.designation,
                manager=self.alice, effective_from=dt(2024, 1, 1),
            )
            a.company = self.company
            with self.assertRaises(ValidationError):
                a.full_clean()

    def test_designation_must_belong_to_department(self):
        hr = Department.objects.create(code="HR", name="Human Resources")
        with use_company(self.company):
            other_dept = CompanyDepartment.objects.create(
                branch=self.branch, department=hr
            )
            a = EmployeeAssignment(
                employee=self.alice, employee_code="E1", branch=self.branch,
                department=other_dept, designation=self.designation,
                effective_from=dt(2024, 1, 1),
            )
            a.company = self.company
            with self.assertRaises(ValidationError):
                a.full_clean()

    def test_period_end_must_follow_start(self):
        with use_company(self.company):
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    self._assign(self.alice, "E1", dt(2024, 6, 1), dt(2024, 1, 1))

    # --- employee identity ------------------------------------------------

    def test_employee_may_exist_without_a_user_account(self):
        with use_company(self.company):
            self.assertIsNone(self.alice.user)
            self.assertEqual(Employee.objects.count(), 2)

    def test_employees_are_scoped_to_their_company(self):
        other = Company.objects.create(code="B", slug="b", name="Company B")
        with use_company(other):
            self.assertEqual(Employee.objects.count(), 0)
        with use_company(self.company):
            self.assertEqual(Employee.objects.count(), 2)

    # --- compensation history ---------------------------------------------

    def _compensate(self, employee, rate, start, end=None, status="active"):
        return EmployeeCompensation.objects.create(
            employee=employee, pay_basis="monthly", base_rate=Decimal(rate),
            currency="USD", effective_from=start, effective_to=end, status=status,
        )

    def test_successive_compensation_periods_allowed(self):
        with use_company(self.company):
            self._compensate(self.alice, "30000", dt(2024, 1, 1), dt(2024, 7, 1),
                             status="ended")
            raise_row = self._compensate(self.alice, "35000", dt(2024, 7, 1))
            self.assertEqual(raise_row.base_rate, Decimal("35000"))

    def test_overlapping_compensation_rejected(self):
        with use_company(self.company):
            self._compensate(self.alice, "30000", dt(2024, 1, 1))
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    self._compensate(self.alice, "35000", dt(2024, 6, 1))

    def test_negative_base_rate_rejected(self):
        with use_company(self.company):
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    self._compensate(self.alice, "-100", dt(2024, 1, 1))
