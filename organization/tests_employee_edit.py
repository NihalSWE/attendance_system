"""Edit employee: details, placement and salary, as corrections or dated changes."""

import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts.models import CompanyMembership, User
from auditlog.models import AuditLog
from common.tenant import use_company
from employees.models import EmployeeAssignment, EmployeeCompensation
from employees.services import create_employee
from organization import employee_edit_services as services
from organization.catalogue import adopt_department, adopt_designation
from organization.models import Branch
from tenants.services import onboard_company

DHAKA = ZoneInfo("Asia/Dhaka")


def local(y, m, d):
    return datetime.datetime(y, m, d, tzinfo=DHAKA)


class EmployeeEditTests(TestCase):
    def setUp(self):
        self.company = onboard_company(code="ACME", slug="acme", name="Acme Ltd")
        self.admin = User.objects.create_user(email="admin@acme.test", password="pw")
        self.hr = User.objects.create_user(email="hr@acme.test", password="pw")
        CompanyMembership.all_objects.create(
            company=self.company, user=self.admin,
            role=CompanyMembership.Role.COMPANY_ADMIN,
            status=CompanyMembership.Status.ACTIVE,
        )
        CompanyMembership.all_objects.create(
            company=self.company, user=self.hr, role=CompanyMembership.Role.HR,
            status=CompanyMembership.Status.ACTIVE,
        )
        with use_company(self.company):
            self.hq = Branch.objects.get(is_default=True)
            self.software = adopt_department(self.hq, "SW", "Software")
            self.sales = adopt_department(self.hq, "SL", "Sales")
            self.dev = adopt_designation(self.software, "DEV", "Developer")
            self.rep = adopt_designation(self.sales, "REP", "Sales Representative")
        self.employee = create_employee(
            company=self.company, first_name="Rahim", employee_code="E1",
            branch=self.hq, department=self.software, designation=self.dev,
            effective_from=local(2026, 1, 1), pay_basis="monthly",
            base_rate=Decimal("30000"), joining_date=datetime.date(2026, 1, 1),
        )["employee"]

    def _rows(self, model):
        with use_company(self.company):
            return list(model.objects.filter(employee=self.employee).order_by("effective_from"))

    def test_details_are_saved(self):
        services.update_employee_details(
            actor=self.admin, company_id=self.company.pk, employee_id=self.employee.pk,
            values={"first_name": "Rahim", "last_name": "Uddin", "work_email": "",
                    "phone": "", "joining_date": datetime.date(2026, 1, 5)},
        )
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.last_name, "Uddin")
        self.assertEqual(self.employee.joining_date, datetime.date(2026, 1, 5))

    def test_salary_from_a_later_date_keeps_history(self):
        services.change_salary(
            actor=self.admin, company_id=self.company.pk, employee_id=self.employee.pk,
            values={"pay_basis": "monthly", "base_rate": Decimal("35000"),
                    "effective_at": local(2026, 9, 1), "reason": "Raise"},
        )
        rows = self._rows(EmployeeCompensation)
        self.assertEqual([r.base_rate for r in rows], [Decimal("30000"), Decimal("35000")])
        self.assertIsNotNone(rows[0].effective_to)

    def test_salary_from_the_same_start_date_corrects_it(self):
        services.change_salary(
            actor=self.admin, company_id=self.company.pk, employee_id=self.employee.pk,
            values={"pay_basis": "daily", "base_rate": Decimal("1200"),
                    "effective_at": local(2026, 1, 1), "reason": "Typo"},
        )
        [row] = self._rows(EmployeeCompensation)
        self.assertEqual((row.pay_basis, row.base_rate), ("daily", Decimal("1200")))

    def test_salary_before_the_current_start_is_refused(self):
        with self.assertRaises(ValidationError):
            services.change_salary(
                actor=self.admin, company_id=self.company.pk, employee_id=self.employee.pk,
                values={"pay_basis": "monthly", "base_rate": Decimal("1"),
                        "effective_at": local(2025, 12, 1), "reason": ""},
            )

    def _without_salary(self):
        """As an employee created from a device's user list: placed, no salary."""
        with use_company(self.company):
            EmployeeCompensation.objects.filter(employee=self.employee).delete()

    def test_an_employee_without_a_salary_gets_a_first_one(self):
        self._without_salary()
        services.change_salary(
            actor=self.admin, company_id=self.company.pk, employee_id=self.employee.pk,
            values={"pay_basis": "monthly", "base_rate": Decimal("25000"),
                    "effective_at": local(2026, 1, 1), "reason": ""},
        )
        [row] = self._rows(EmployeeCompensation)
        self.assertEqual((row.pay_basis, row.base_rate, row.effective_to), ("monthly", Decimal("25000"), None))
        self.assertEqual(row.reason, "First salary")
        with use_company(self.company):
            self.assertTrue(AuditLog.objects.filter(action="employee.salary_set").exists())

    def test_a_first_salary_cannot_start_before_the_placement(self):
        self._without_salary()
        with self.assertRaises(ValidationError) as caught:
            services.change_salary(
                actor=self.admin, company_id=self.company.pk, employee_id=self.employee.pk,
                values={"pay_basis": "monthly", "base_rate": Decimal("25000"),
                        "effective_at": local(2025, 12, 1), "reason": ""},
            )
        self.assertIn("salary_from", caught.exception.error_dict)

    def test_an_ended_salary_is_not_restarted_here(self):
        with use_company(self.company):
            EmployeeCompensation.objects.filter(employee=self.employee).update(
                effective_to=local(2026, 6, 1), status="ended"
            )
        with self.assertRaises(ValidationError):
            services.change_salary(
                actor=self.admin, company_id=self.company.pk, employee_id=self.employee.pk,
                values={"pay_basis": "monthly", "base_rate": Decimal("25000"),
                        "effective_at": local(2026, 7, 1), "reason": ""},
            )

    def test_the_page_sets_a_first_salary_from_the_placement_date(self):
        self._without_salary()
        self.client.force_login(self.admin)
        edit_url = reverse("organization:employee_edit", args=[self.employee.pk])
        page = self.client.get(edit_url)
        self.assertContains(page, "No salary yet")
        self.assertContains(page, "Set salary")
        self.assertEqual(page.context["salary"].initial["salary_from"], datetime.date(2026, 1, 1))
        response = self.client.post(edit_url, {
            "section": "salary", "pay_basis": "daily", "base_rate": "1200",
            "salary_from": "2026-01-01", "salary_reason": "",
        })
        self.assertRedirects(response, edit_url)
        [row] = self._rows(EmployeeCompensation)
        self.assertEqual((row.pay_basis, row.base_rate), ("daily", Decimal("1200")))

    def test_placement_change_from_a_later_date_keeps_history(self):
        services.change_placement(
            actor=self.admin, company_id=self.company.pk, employee_id=self.employee.pk,
            values={"branch": self.hq, "department": self.sales, "designation": self.rep,
                    "employee_code": "E1", "effective_at": local(2026, 9, 1), "reason": ""},
        )
        rows = self._rows(EmployeeAssignment)
        self.assertEqual([r.department_id for r in rows], [self.software.pk, self.sales.pk])

    def test_placement_correction_on_the_same_start_date(self):
        services.change_placement(
            actor=self.admin, company_id=self.company.pk, employee_id=self.employee.pk,
            values={"branch": self.hq, "department": self.sales, "designation": self.rep,
                    "employee_code": "E1-X", "effective_at": local(2026, 1, 1), "reason": ""},
        )
        [row] = self._rows(EmployeeAssignment)
        self.assertEqual((row.department_id, row.employee_code), (self.sales.pk, "E1-X"))

    def test_hr_cannot_edit(self):
        with self.assertRaises(PermissionDenied):
            services.update_employee_details(
                actor=self.hr, company_id=self.company.pk, employee_id=self.employee.pk,
                values={"first_name": "X"},
            )

    def test_list_has_an_edit_action_and_the_page_saves(self):
        self.client.force_login(self.admin)
        listing = self.client.get(reverse("employee_list"))
        edit_url = reverse("organization:employee_edit", args=[self.employee.pk])
        self.assertContains(listing, edit_url)
        self.assertEqual(self.client.get(edit_url).status_code, 200)
        response = self.client.post(edit_url, {
            "section": "salary", "pay_basis": "monthly", "base_rate": "32000",
            "salary_from": "2026-09-01", "salary_reason": "",
        })
        self.assertRedirects(response, edit_url)
        self.assertEqual(self._rows(EmployeeCompensation)[-1].base_rate, Decimal("32000"))
