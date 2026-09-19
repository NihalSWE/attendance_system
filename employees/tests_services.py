"""Employee lifecycle service tests: create, transfer, salary revision."""

from datetime import datetime, time, timezone as dt_timezone
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from common.tenant import use_company
from employees.models import Employee, EmployeeAssignment, EmployeeCompensation
from employees.services import create_employee, revise_compensation, transfer_employee
from organization.models import (
    Branch,
    Department,
    Designation,
)
from scheduling.models import Shift
from tenants.services import onboard_company


def dt(y, m, d):
    return datetime(y, m, d, tzinfo=dt_timezone.utc)


class EmployeeServiceTests(TestCase):
    def setUp(self):
        self.company = onboard_company(code="ACME", slug="acme", name="Acme Ltd")
        with use_company(self.company):
            self.branch = Branch.objects.get()
            self.software = Department.objects.create(
                branch=self.branch, code="SW", name="Software"
            )
            self.sales = Department.objects.create(
                branch=self.branch, code="SL", name="Sales"
            )
            self.dev = Designation.objects.create(
                department=self.software, code="DEV", name="Developer"
            )
            self.rep = Designation.objects.create(
                department=self.sales, code="REP", name="Sales Rep"
            )
            self.shift = Shift.objects.create(
                code="DAY", name="Day", start_time=time(9), end_time=time(18),
                scheduled_minutes=480,
            )

    def _create_employee(self, first_name="Alice", code="E100", start=None, **kw):
        return create_employee(
            company=self.company,
            first_name=first_name,
            employee_code=code,
            branch=self.branch,
            department=self.software,
            designation=self.dev,
            effective_from=start or dt(2024, 1, 1),
            pay_basis="monthly",
            base_rate=Decimal("30000"),
            **kw,
        )

    # --- create employee ----------------------------------------------------

    def test_create_employee_makes_employee_assignment_and_compensation(self):
        result = self._create_employee()
        self.assertIsNotNone(result["employee"].pk)
        self.assertEqual(result["assignment"].employee_code, "E100")
        self.assertEqual(result["compensation"].base_rate, Decimal("30000"))
        # Currency defaults from the company when not supplied.
        self.assertEqual(result["compensation"].currency, self.company.currency)

    def test_create_employee_can_attach_a_shift(self):
        result = self._create_employee(shift=self.shift)
        self.assertIsNotNone(result["shift_assignment"])
        self.assertEqual(result["shift_assignment"].shift, self.shift)

    def test_create_employee_without_a_shift_leaves_none(self):
        self.assertIsNone(self._create_employee()["shift_assignment"])

    def test_employee_may_be_created_without_a_user_account(self):
        self.assertIsNone(self._create_employee()["employee"].user)

    def test_create_employee_rejects_a_designation_from_another_department(self):
        # designation "rep" belongs to Sales, but department passed is Software.
        with self.assertRaises(ValidationError):
            create_employee(
                company=self.company, first_name="Bob", employee_code="E200",
                branch=self.branch, department=self.software, designation=self.rep,
                effective_from=dt(2024, 1, 1),
                pay_basis="monthly", base_rate=Decimal("1000"),
            )
        with use_company(self.company):
            self.assertEqual(Employee.objects.count(), 0)  # rolled back

    def test_a_failed_create_leaves_no_partial_records(self):
        with self.assertRaises(ValidationError):
            create_employee(
                company=self.company, first_name="Bob", employee_code="E200",
                branch=self.branch, department=self.software, designation=self.rep,
                effective_from=dt(2024, 1, 1),
                pay_basis="monthly", base_rate=Decimal("1000"),
            )
        with use_company(self.company):
            self.assertEqual(Employee.objects.count(), 0)
            self.assertEqual(EmployeeAssignment.objects.count(), 0)
            self.assertEqual(EmployeeCompensation.objects.count(), 0)

    # --- transfer ------------------------------------------------------------

    def test_transfer_closes_the_old_assignment_and_opens_a_new_one(self):
        employee = self._create_employee()["employee"]
        new_assignment = transfer_employee(
            employee=employee, effective_at=dt(2024, 6, 1),
            department=self.sales, designation=self.rep, reason="Moved to Sales",
        )
        with use_company(self.company):
            history = list(
                EmployeeAssignment.objects.filter(employee=employee)
                .order_by("effective_from")
            )
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].effective_to, dt(2024, 6, 1))
        self.assertEqual(history[0].status, "ended")
        self.assertIsNone(history[1].effective_to)
        self.assertEqual(new_assignment.department, self.sales)

    def test_transfer_carries_forward_unspecified_attributes(self):
        employee = self._create_employee(code="E100")["employee"]
        new_assignment = transfer_employee(
            employee=employee, effective_at=dt(2024, 6, 1), reason="Same role"
        )
        # Code and branch were not passed, so they must be preserved.
        self.assertEqual(new_assignment.employee_code, "E100")
        self.assertEqual(new_assignment.branch, self.branch)

    def test_transfer_before_the_current_start_is_rejected(self):
        employee = self._create_employee(start=dt(2024, 6, 1))["employee"]
        with self.assertRaises(ValidationError):
            transfer_employee(employee=employee, effective_at=dt(2024, 1, 1))

    def test_transfer_preserves_history_after_the_code_is_reused(self):
        alice = self._create_employee(first_name="Alice", code="E100")["employee"]
        # Alice moves on to a new code; E100 is then free for Bob.
        transfer_employee(
            employee=alice, effective_at=dt(2024, 6, 1), employee_code="E999"
        )
        bob = create_employee(
            company=self.company, first_name="Bob", employee_code="E100",
            branch=self.branch, department=self.software, designation=self.dev,
            effective_from=dt(2024, 6, 1),
            pay_basis="monthly", base_rate=Decimal("25000"),
        )["employee"]

        with use_company(self.company):
            alice_codes = set(
                EmployeeAssignment.objects.filter(employee=alice)
                .values_list("employee_code", flat=True)
            )
            bob_codes = set(
                EmployeeAssignment.objects.filter(employee=bob)
                .values_list("employee_code", flat=True)
            )
        # Bob holds E100 now, but Alice's E100 history still belongs to Alice.
        self.assertEqual(alice_codes, {"E100", "E999"})
        self.assertEqual(bob_codes, {"E100"})
        self.assertNotEqual(alice.pk, bob.pk)

    # --- compensation revision ------------------------------------------------

    def test_revision_closes_the_old_rate_and_opens_the_new_one(self):
        employee = self._create_employee()["employee"]
        revised = revise_compensation(
            employee=employee, effective_at=dt(2024, 7, 1),
            base_rate=Decimal("35000"), reason="Annual raise",
        )
        with use_company(self.company):
            history = list(
                EmployeeCompensation.objects.filter(employee=employee)
                .order_by("effective_from")
            )
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].base_rate, Decimal("30000"))
        self.assertEqual(history[0].effective_to, dt(2024, 7, 1))
        self.assertEqual(history[0].status, "ended")
        self.assertEqual(revised.base_rate, Decimal("35000"))
        self.assertIsNone(revised.effective_to)

    def test_revision_carries_forward_pay_basis_and_currency(self):
        employee = self._create_employee()["employee"]
        revised = revise_compensation(
            employee=employee, effective_at=dt(2024, 7, 1),
            base_rate=Decimal("35000"),
        )
        self.assertEqual(revised.pay_basis, "monthly")
        self.assertEqual(revised.currency, self.company.currency)

    def test_revision_before_the_current_start_is_rejected(self):
        employee = self._create_employee(start=dt(2024, 6, 1))["employee"]
        with self.assertRaises(ValidationError):
            revise_compensation(
                employee=employee, effective_at=dt(2024, 1, 1),
                base_rate=Decimal("1"),
            )

    def test_mid_month_raise_leaves_both_periods_queryable(self):
        # Payroll must be able to split the month at the change instant.
        employee = self._create_employee()["employee"]
        revise_compensation(
            employee=employee, effective_at=dt(2024, 7, 15),
            base_rate=Decimal("36000"),
        )
        with use_company(self.company):
            on_july_1 = EmployeeCompensation.objects.filter(
                employee=employee, effective_from__lte=dt(2024, 7, 1)
            ).exclude(effective_to__lte=dt(2024, 7, 1)).get()
            on_july_20 = EmployeeCompensation.objects.filter(
                employee=employee, effective_from__lte=dt(2024, 7, 20)
            ).exclude(effective_to__lte=dt(2024, 7, 20)).get()
        self.assertEqual(on_july_1.base_rate, Decimal("30000"))
        self.assertEqual(on_july_20.base_rate, Decimal("36000"))
