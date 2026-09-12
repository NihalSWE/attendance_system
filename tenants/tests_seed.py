"""Tests for the synthetic demo seed and employee termination."""

from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from access_control.services import has_permission
from common.tenant import use_company
from employees.models import Employee, EmployeeAssignment, EmployeeCompensation
from employees.services import create_employee, terminate_employee
from organization.models import (
    Branch,
    CompanyDepartment,
    CompanyDesignation,
    Department,
    Designation,
)
from tenants.models import Company
from tenants.services import onboard_company


def dt(y, m, d):
    return datetime(y, m, d, tzinfo=dt_timezone.utc)


class TerminationTests(TestCase):
    def setUp(self):
        software_entry = Department.objects.create(code="SW", name="Software")
        dev_entry = Designation.objects.create(
            code="DEV", name="Developer"
        )
        self.company = onboard_company(code="ACME", slug="acme", name="Acme Ltd")
        with use_company(self.company):
            self.branch = Branch.objects.get()
            self.dept = CompanyDepartment.objects.create(
                branch=self.branch, department=software_entry
            )
            self.title = CompanyDesignation.objects.create(
                company_department=self.dept, designation=dev_entry
            )

    def _create_employee(self, name, code, start=dt(2023, 1, 1)):
        return create_employee(
            company=self.company, first_name=name, employee_code=code,
            branch=self.branch, department=self.dept, designation=self.title,
            effective_from=start, pay_basis="monthly", base_rate=Decimal("30000"),
        )["employee"]

    def test_termination_closes_assignment_and_compensation(self):
        employee = self._create_employee("Karim", "E014")
        terminate_employee(employee=employee, effective_at=dt(2024, 2, 1))
        with use_company(self.company):
            assignment = EmployeeAssignment.objects.get(employee=employee)
            compensation = EmployeeCompensation.objects.get(employee=employee)
            employee.refresh_from_db()
        self.assertEqual(assignment.effective_to, dt(2024, 2, 1))
        self.assertEqual(assignment.status, "ended")
        self.assertEqual(compensation.effective_to, dt(2024, 2, 1))
        self.assertEqual(employee.employment_status, "resigned")
        self.assertEqual(employee.leaving_date, dt(2024, 2, 1).date())

    def test_termination_deletes_nothing(self):
        employee = self._create_employee("Karim", "E014")
        terminate_employee(employee=employee, effective_at=dt(2024, 2, 1))
        with use_company(self.company):
            # The person and their history remain fully queryable.
            self.assertEqual(Employee.objects.filter(pk=employee.pk).count(), 1)
            self.assertEqual(
                EmployeeAssignment.objects.filter(employee=employee).count(), 1
            )

    def test_code_is_reusable_after_termination(self):
        karim = self._create_employee("Karim", "E014")
        terminate_employee(employee=karim, effective_at=dt(2024, 2, 1))
        sadia = self._create_employee("Sadia", "E014", start=dt(2024, 5, 1))
        with use_company(self.company):
            holders = list(
                EmployeeAssignment.objects.filter(employee_code="E014")
                .order_by("effective_from")
                .values_list("employee_id", flat=True)
            )
        self.assertEqual(holders, [karim.pk, sadia.pk])
        self.assertNotEqual(karim.pk, sadia.pk)


class SeedDemoTests(TestCase):
    def test_seeding_is_refused_without_force_when_debug_is_off(self):
        # Django forces DEBUG=False under test, so this exercises the real guard:
        # synthetic data must never land in a non-development environment.
        with self.assertRaises(CommandError):
            call_command("seed_demo", verbosity=0)
        self.assertEqual(Company.objects.filter(code="DEMO-NWT").count(), 0)

    def test_seed_creates_two_isolated_companies(self):
        call_command("seed_demo", "--force", verbosity=0)
        northwind = Company.objects.get(code="DEMO-NWT")
        sunrise = Company.objects.get(code="DEMO-SNR")
        with use_company(northwind):
            self.assertEqual(Employee.objects.count(), 7)
        with use_company(sunrise):
            self.assertEqual(Employee.objects.count(), 1)

    def test_seed_is_idempotent(self):
        call_command("seed_demo", "--force", verbosity=0)
        call_command("seed_demo", "--force", verbosity=0)
        self.assertEqual(Company.objects.filter(code="DEMO-NWT").count(), 1)
        northwind = Company.objects.get(code="DEMO-NWT")
        with use_company(northwind):
            self.assertEqual(Employee.objects.count(), 7)

    def test_seeded_employee_code_is_reused_by_a_different_person(self):
        call_command("seed_demo", "--force", verbosity=0)
        northwind = Company.objects.get(code="DEMO-NWT")
        with use_company(northwind):
            holders = list(
                EmployeeAssignment.objects.filter(employee_code="NWT-014")
                .order_by("effective_from")
                .values_list("employee_id", flat=True)
            )
        self.assertEqual(len(holders), 2)
        self.assertNotEqual(holders[0], holders[1])

    def test_same_code_may_exist_in_both_companies_at_once(self):
        # NWT-001 is live in Northwind AND in Sunrise: codes are per company.
        call_command("seed_demo", "--force", verbosity=0)
        self.assertEqual(
            EmployeeAssignment.all_objects.filter(employee_code="NWT-001").count(), 2
        )

    def test_seeded_transfer_leaves_two_assignment_rows(self):
        call_command("seed_demo", "--force", verbosity=0)
        northwind = Company.objects.get(code="DEMO-NWT")
        with use_company(northwind):
            nusrat = Employee.objects.get(first_name="Nusrat")
            self.assertEqual(nusrat.assignments.count(), 2)

    def test_seeded_employees_are_marked_synthetic(self):
        call_command("seed_demo", "--force", verbosity=0)
        northwind = Company.objects.get(code="DEMO-NWT")
        with use_company(northwind):
            self.assertTrue(all(e.metadata.get("demo") for e in Employee.objects.all()))

    def test_only_granted_designations_hold_permissions(self):
        # Proves "enabling a feature does not grant every employee its actions".
        call_command("seed_demo", "--force", verbosity=0)
        northwind = Company.objects.get(code="DEMO-NWT")
        with use_company(northwind):
            hr_manager = Employee.objects.get(first_name="Ayesha")
            junior = Employee.objects.get(first_name="Sadia")
        self.assertTrue(has_permission(hr_manager, "leave.approve"))
        self.assertFalse(has_permission(junior, "leave.approve"))

    def test_feature_not_enabled_denies_permission_in_the_other_company(self):
        call_command("seed_demo", "--force", verbosity=0)
        sunrise = Company.objects.get(code="DEMO-SNR")
        with use_company(sunrise):
            supervisor = Employee.objects.get(first_name="Imran")
        # Supervisor is granted leave.approve and Sunrise has leave enabled...
        self.assertTrue(has_permission(supervisor, "leave.approve"))
        # ...but payroll was never enabled for Sunrise.
        self.assertFalse(has_permission(supervisor, "payroll.finalize"))
