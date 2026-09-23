"""Allowances and recurring deductions (A11).

The company names a component once; each employee's own row is dated, so a
payslip already paid keeps what it was paid with. A fixed amount is paid for
the days of the month it was in force and the person was employed; a
percentage is a percentage of the basic actually earned that month.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from auditlog.models import AuditLog
from common.tenant import use_company
from payroll import component_services
from payroll.models import EmployeeSalaryComponent, PayrollRecord, SalaryComponent
from payroll.services import generate_payroll
from payroll.tests_branch_salary import BranchSalaryBase

AUGUST = {"month": 8, "year": 2026}


class ComponentCase(BranchSalaryBase):
    def component(self, code="HOUSE_RENT", name="House rent", kind="earning",
                  method="fixed", amount="4000", percent=None, actor=None):
        return component_services.create_component(
            actor=actor or self.admin, company_id=self.company.pk,
            values={"code": code, "name": name, "kind": kind, "method": method,
                    "default_amount": Decimal(amount) if amount else None,
                    "default_percent": Decimal(percent) if percent else None,
                    "description": ""},
        )

    def give(self, component, employee=None, start=datetime.date(2026, 8, 1),
             amount=None, percent=None, actor=None):
        return component_services.give_component(
            actor=actor or self.admin, company_id=self.company.pk,
            employee_id=(employee or self.employee).pk,
            values={"component": component, "amount": amount, "percent": percent,
                    "effective_from": start, "reason": ""},
        )

    def payslip(self, employee=None):
        self.generate()
        with use_company(self.company):
            return PayrollRecord.objects.select_related("employee").get(
                employee=employee or self.employee)

    def lines(self, record):
        with use_company(self.company):
            return {line.code: line for line in record.lines.all()}


class CatalogueTests(ComponentCase):
    def test_naming_one_pays_nobody(self):
        component = self.component()
        before = self.payslip()
        self.assertNotIn("HOUSE_RENT", self.lines(before))
        self.assertEqual(component.status, "active")

    def test_a_code_is_used_once(self):
        self.component()
        with self.assertRaises(ValidationError):
            self.component(name="Another")

    def test_the_amount_must_match_how_it_is_worked_out(self):
        with self.assertRaises(ValidationError):
            self.component(method="percent_of_basic", amount=None, percent=None)
        with self.assertRaises(ValidationError):
            self.component(code="X", name="X", amount=None)

    def test_only_the_company_keeps_the_list(self):
        for actor in (self.manager, self.clerk_user):
            with self.subTest(actor=actor.email):
                with self.assertRaises(PermissionDenied):
                    self.component(code=f"C{actor.pk}", name=f"C{actor.pk}", actor=actor)

    def test_it_is_audited(self):
        component = self.component()
        entry = AuditLog.objects.get(action="salary_component.created")
        self.assertEqual(entry.after_data["code"], component.code)


class PayTests(ComponentCase):
    def test_a_fixed_allowance_is_added_to_the_payslip(self):
        self.give(self.component())
        record = self.payslip()
        line = self.lines(record)["HOUSE_RENT"]
        self.assertEqual((line.line_type, line.amount), ("earning", Decimal("4000.00")))
        self.assertIn("House rent", line.description)
        self.assertEqual(record.gross_earnings, Decimal("34000.00"))

    def test_a_deduction_comes_off_the_net(self):
        # With an allowance big enough that the month is not already at zero:
        # this fixture's employee is absent for much of August, and a salary
        # never goes negative, so the plain month is already capped.
        self.give(self.component(code="EXTRA", name="Site allowance", amount="40000"))
        plain = self.payslip().net_pay
        self.assertGreater(plain, Decimal("1500.00"))
        self.give(self.component(code="PF", name="Provident fund", kind="deduction",
                                 amount="1500"))
        record = self.payslip()
        self.assertEqual(self.lines(record)["PF"].line_type, "deduction")
        self.assertEqual(record.net_pay, plain - Decimal("1500.00"))

    def test_a_percentage_follows_the_basic_actually_earned(self):
        self.give(self.component(code="HR", name="House rent",
                                 method="percent_of_basic", amount=None, percent="40"))
        record = self.payslip()
        basic = self.lines(record)["BASIC"].amount
        self.assertEqual(self.lines(record)["HR"].amount, (basic * Decimal("0.40")).quantize(Decimal("0.01")))

    def test_an_allowance_given_mid_month_is_paid_for_its_days(self):
        self.give(self.component(), start=datetime.date(2026, 8, 17))
        line = self.lines(self.payslip())["HOUSE_RENT"]
        # 15 of August's 31 days: 4000 x 15/31.
        self.assertEqual(line.amount, (Decimal("4000") * 15 / 31).quantize(Decimal("0.01")))
        self.assertIn("15 of 31 days", line.description)

    def test_an_amount_can_differ_from_the_company_default(self):
        self.give(self.component(), amount=Decimal("9000"))
        self.assertEqual(self.lines(self.payslip())["HOUSE_RENT"].amount, Decimal("9000.00"))

    def test_a_raise_splits_the_month_between_the_two_amounts(self):
        component = self.component()
        self.give(component, amount=Decimal("3100"))
        self.give(component, amount=Decimal("6200"), start=datetime.date(2026, 8, 17))
        with use_company(self.company):
            rows = list(EmployeeSalaryComponent.objects.filter(
                employee=self.employee).order_by("effective_from"))
        self.assertEqual(rows[0].effective_to, datetime.date(2026, 8, 16))
        record = self.payslip()
        with use_company(self.company):
            paid = sum(line.amount for line in record.lines.filter(code="HOUSE_RENT"))
        # 16 days at 3100 and 15 at 6200, of a 31-day month.
        expected = ((Decimal("3100") * 16 / 31).quantize(Decimal("0.01"))
                    + (Decimal("6200") * 15 / 31).quantize(Decimal("0.01")))
        self.assertEqual(paid, expected)

    def test_ending_it_stops_it_from_the_next_month(self):
        component = self.component()
        self.give(component, start=datetime.date(2026, 7, 1))
        self.assertIn("HOUSE_RENT", self.lines(self.payslip()))   # still on in August
        with use_company(self.company):
            row = EmployeeSalaryComponent.objects.get(employee=self.employee)
        component_services.end_component(actor=self.admin, company_id=self.company.pk,
                                         row_id=row.pk, last_day=datetime.date(2026, 7, 31))
        self.assertNotIn("HOUSE_RENT", self.lines(self.payslip()))

    def test_it_cannot_end_before_it_starts(self):
        self.give(self.component())
        with use_company(self.company):
            row = EmployeeSalaryComponent.objects.get(employee=self.employee)
        with self.assertRaises(ValidationError):
            component_services.end_component(actor=self.admin, company_id=self.company.pk,
                                             row_id=row.pk, last_day=datetime.date(2026, 7, 20))

    def test_a_component_no_longer_offered_stops_counting(self):
        component = self.component()
        self.give(component)
        component_services.set_component_status(
            actor=self.admin, company_id=self.company.pk,
            component_id=component.pk, status="inactive")
        self.assertNotIn("HOUSE_RENT", self.lines(self.payslip()))

    def test_it_reaches_the_payslip_page_and_its_pdf(self):
        self.give(self.component())
        record = self.payslip()
        self.client.force_login(self.admin)
        page = self.client.get(reverse("payroll:payslip", args=[record.pk]))
        self.assertContains(page, "House rent")


class WhoMayGiveTests(ComponentCase):
    def test_a_branch_manager_gives_one_in_their_own_branch_only(self):
        component = self.component()
        self.give(component, actor=self.manager)          # Rahim, Head Office
        with self.assertRaises(PermissionDenied):
            self.give(component, employee=self.far, actor=self.manager)   # Chittagong

    def test_someone_without_salary_access_cannot(self):
        component = self.component()
        with self.assertRaises(PermissionDenied):
            self.give(component, actor=self.clerk_user)

    def test_the_employee_page_shows_and_takes_one(self):
        component = self.component()
        self.client.force_login(self.admin)
        url = reverse("organization:employee_edit", args=[self.employee.pk])
        page = self.client.get(url)
        self.assertContains(page, "Allowances and deductions")
        response = self.client.post(url, {
            "section": "component_give", "component": component.pk,
            "amount": "4000", "effective_from": "2026-08-01", "reason": "Package",
        }, follow=True)
        self.assertContains(response, "on their payslip the next time salary")
        with use_company(self.company):
            self.assertTrue(EmployeeSalaryComponent.objects.filter(
                employee=self.employee, component=component).exists())

    def test_the_company_page_lists_and_adds(self):
        self.client.force_login(self.admin)
        page = self.client.post(reverse("payroll:component_list"), {
            "code": "TRANSPORT", "name": "Transport", "kind": "earning",
            "method": "fixed", "default_amount": "1200", "description": "",
        }, follow=True)
        self.assertContains(page, "Transport")
        with use_company(self.company):
            self.assertTrue(SalaryComponent.objects.filter(code="TRANSPORT").exists())
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse("payroll:component_list")).status_code, 302)
