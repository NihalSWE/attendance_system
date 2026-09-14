"""A11 part 2: one-time bonus and deduction lines on a draft salary."""

import datetime
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from accounts.models import CompanyMembership, User
from common.tenant import use_company
from payroll.services import add_adjustment, finalise_payroll, generate_payroll, remove_adjustment
from payroll.tests_overtime import OvertimeBase


class AdjustmentTests(OvertimeBase):
    def setUp(self):
        super().setUp()
        self.work(datetime.date(2026, 8, 10), (9, 0), (18, 0))
        run = self.generate()
        with use_company(self.company):
            self.record = run.records.get(employee=self.employee)

    def generate(self):
        return generate_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)

    def add(self, record=None, actor=None, **values):
        values = {"adjustment_type": "earning", "amount": Decimal("5000"), "reason": "Eid bonus", **values}
        return add_adjustment(actor=actor or self.admin, company_id=self.company.pk,
                              record_id=(record or self.record).pk, **values)

    def test_bonus_and_deduction_change_net_and_survive_regeneration(self):
        net = self.record.net_pay
        record = self.add()
        record = self.add(record, adjustment_type="deduction", amount=Decimal("2000"), reason="Advance recovery")
        self.assertEqual(record.net_pay, net + 3000)

        run = self.generate()
        with use_company(self.company):
            again = run.records.get(employee=self.employee)
            lines = {line.description: line for line in again.lines.select_related("payroll_adjustment")}
        self.assertEqual(again.net_pay, net + 3000)
        self.assertEqual((lines["Eid bonus"].line_type, lines["Eid bonus"].is_manual), ("earning", True))
        self.assertEqual(lines["Advance recovery"].line_type, "deduction")

        record = remove_adjustment(actor=self.admin, company_id=self.company.pk,
                                   adjustment_id=lines["Eid bonus"].payroll_adjustment_id)
        with use_company(self.company):
            descriptions = set(record.lines.values_list("description", flat=True))
        self.assertNotIn("Eid bonus", descriptions)
        self.assertIn("Advance recovery", descriptions)
        # Net never goes below zero under the standard rules.
        self.assertEqual(record.net_pay, max(net - 2000, 0))

    def test_values_role_and_finalised_month_are_checked(self):
        for bad in ({"amount": Decimal("0")}, {"reason": " "}, {"adjustment_type": "gift"}):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                self.add(**bad)
        hr = User.objects.create_user(email="hr@adjust.test")
        CompanyMembership.all_objects.create(company=self.company, user=hr, role="hr", status="active")
        with self.assertRaises(PermissionDenied):
            self.add(actor=hr)
        finalise_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        with self.assertRaises(ValidationError):
            self.add()

    def test_payslip_page_adds_and_removes_a_line(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("payroll:payslip", args=[self.record.pk]))
        self.assertContains(page, "Bonus and deductions")
        # A11 part 5: printable payslip; the company-only card is not printed.
        self.assertContains(page, "Print or save as PDF")
        self.assertContains(page, 'class="card no-print"')
        response = self.client.post(reverse("payroll:payslip_adjustment_add", args=[self.record.pk]), {
            "adjustment_type": "deduction", "amount": "1500", "reason": "Uniform cost",
        })
        self.assertEqual(response.status_code, 302)
        page = self.client.get(response["Location"])
        self.assertContains(page, "Uniform cost")
        with use_company(self.company):
            adjustment = self.employee.payroll_adjustments.get()
        response = self.client.post(reverse("payroll:payslip_adjustment_remove", args=[adjustment.pk]))
        self.assertNotContains(self.client.get(response["Location"]), "Uniform cost")
