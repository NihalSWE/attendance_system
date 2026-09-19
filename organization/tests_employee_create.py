"""Creating an employee through the browser.

The domain write is ``employees.services.create_employee``, already tested in
employees/. These cover the screen: that the dependent selects narrow
correctly, that a crafted combination is refused, and that the database's own
constraint on employee-code reuse reaches the user as a readable field error
rather than a 500.
"""

from datetime import datetime, time, timezone as dt_timezone
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import CompanyMembership
from common.tenant import use_company
from employees.models import Employee, EmployeeAssignment
from organization import adoption_services
from organization.models import Branch, Designation
from scheduling.models import CompanyAttendanceSettings, Shift
from tenants.models import Company

User = get_user_model()


def dt(year, month, day):
    return datetime(year, month, day, tzinfo=dt_timezone.utc)


class EmployeeCreateScreenTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.company = Company.objects.create(code="A", slug="a", name="Company A")
        self.owner = User.objects.create_user(
            email="owner@example.test", password="pw-12345678"
        )
        CompanyMembership.all_objects.create(
            company=self.company, user=self.owner, role="company_admin", status="active"
        )
        with use_company(self.company):
            self.branch = Branch.objects.create(
                code="HQ", name="Head Office", is_default=True
            )
            self.other_branch = Branch.objects.create(code="BR2", name="Second Branch")
            shift = Shift.objects.create(
                code="GEN", name="General", start_time=time(9, 0),
                end_time=time(18, 0), scheduled_minutes=540,
            )
            CompanyAttendanceSettings.objects.create(
                company_shift=shift, effective_from=dt(2026, 1, 1)
            )

        self.adoption = adoption_services.adopt_department(
            actor=self.owner, company_id=self.company.pk,
            values={"branch": self.branch, "code": "SW", "name": "Software",
                    "status": "active",
                    "designations": [{"code": "DEV", "name": "Developer"}]},
        )
        # A department in the *other* branch, to prove the chain is enforced.
        self.other_adoption = adoption_services.adopt_department(
            actor=self.owner, company_id=self.company.pk,
            values={"branch": self.other_branch, "code": "HR",
                    "name": "Human Resources", "status": "active",
                    "designations": [{"code": "HRM", "name": "HR Manager"}]},
        )
        with use_company(self.company):
            self.title = Designation.objects.get(department=self.adoption)
            self.other_title = Designation.objects.get(department=self.other_adoption)
        self.client.force_login(self.owner)

    def _payload(self, **overrides):
        payload = {
            "first_name": "Ayesha",
            "last_name": "Rahman",
            "employee_code": "E-1",
            "branch": self.branch.pk,
            "department": self.adoption.pk,
            "designation": self.title.pk,
            "effective_from": "2026-03-01",
            "pay_basis": "monthly",
            "base_rate": "42000",
        }
        payload.update(overrides)
        return payload

    # --- rendering --------------------------------------------------------

    def test_form_renders(self):
        response = self.client.get(reverse("organization:employee_create"))
        self.assertEqual(response.status_code, 200)

    def test_anonymous_is_redirected(self):
        self.client.logout()
        self.assertEqual(
            self.client.get(reverse("organization:employee_create")).status_code, 302
        )

    def test_dependent_endpoints_narrow_by_parent(self):
        response = self.client.get(
            reverse("organization:employee_branch_departments"), {"branch": self.branch.pk}
        )
        self.assertEqual(
            {row["text"] for row in response.json()["results"]}, {"Software"}
        )
        response = self.client.get(
            reverse("organization:employee_department_designations"),
            {"department": self.adoption.pk},
        )
        self.assertEqual(
            {row["text"] for row in response.json()["results"]}, {"Developer"}
        )

    # --- the happy path ---------------------------------------------------

    def test_creating_makes_employee_assignment_and_compensation(self):
        response = self.client.post(
            reverse("organization:employee_create"), self._payload(), follow=True
        )
        self.assertEqual(response.status_code, 200)
        with use_company(self.company):
            employee = Employee.objects.get(first_name="Ayesha")
            assignment = employee.assignments.get()
            compensation = employee.compensations.get()
        self.assertEqual(assignment.employee_code, "E-1")
        self.assertEqual(assignment.department_id, self.adoption.pk)
        self.assertEqual(assignment.designation_id, self.title.pk)
        self.assertEqual(compensation.base_rate, Decimal("42000.00"))
        # The company's currency is applied rather than asked for.
        self.assertEqual(compensation.currency, self.company.currency)

    def test_start_date_is_stored_timezone_aware(self):
        self.client.post(reverse("organization:employee_create"), self._payload())
        with use_company(self.company):
            assignment = EmployeeAssignment.objects.get(employee_code="E-1")
        self.assertIsNotNone(assignment.effective_from.tzinfo)

    # --- refusals ---------------------------------------------------------

    def test_a_department_from_another_branch_is_refused(self):
        response = self.client.post(
            reverse("organization:employee_create"),
            self._payload(department=self.other_adoption.pk, designation=self.other_title.pk),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("department", response.context["form"].errors)
        with use_company(self.company):
            self.assertFalse(Employee.objects.exists())

    def test_a_designation_from_another_department_is_refused(self):
        response = self.client.post(
            reverse("organization:employee_create"),
            self._payload(designation=self.other_title.pk),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        with use_company(self.company):
            self.assertFalse(Employee.objects.exists())

    def test_zero_pay_is_refused(self):
        response = self.client.post(
            reverse("organization:employee_create"), self._payload(base_rate="0")
        )
        self.assertIn("base_rate", response.context["form"].errors)

    def test_reusing_an_open_employee_code_is_a_readable_field_error(self):
        self.client.post(reverse("organization:employee_create"), self._payload())
        response = self.client.post(
            reverse("organization:employee_create"),
            self._payload(first_name="Karim", employee_code="E-1"),
        )
        self.assertEqual(response.status_code, 200)
        errors = response.context["form"].errors
        self.assertIn("employee_code", errors)
        # The reader must not be shown a raw constraint name.
        self.assertNotIn("excl_", str(errors))
        with use_company(self.company):
            self.assertEqual(Employee.objects.count(), 1)

    def test_a_code_is_reusable_once_the_previous_placement_ends(self):
        """The central identity rule: the code is reusable, the person is not."""
        self.client.post(reverse("organization:employee_create"), self._payload())
        with use_company(self.company):
            assignment = EmployeeAssignment.objects.get(employee_code="E-1")
            assignment.effective_to = dt(2026, 6, 1)
            assignment.status = "ended"
            assignment.save()

        response = self.client.post(
            reverse("organization:employee_create"),
            self._payload(
                first_name="Karim", employee_code="E-1", effective_from="2026-07-01"
            ),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        with use_company(self.company):
            holders = EmployeeAssignment.objects.filter(employee_code="E-1")
            self.assertEqual(holders.count(), 2)
            # Two different people held the same code at different times.
            self.assertEqual(
                len({a.employee_id for a in holders}), 2
            )

    def test_employee_list_links_to_the_create_form(self):
        response = self.client.get(reverse("employee_list"))
        self.assertContains(response, reverse("organization:employee_create"))
