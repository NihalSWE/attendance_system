"""Putting right a month that is already finalised (A11).

The finalised month never changes - what was paid stays paid, and an
employee's payslip does not move under them. The money is a line on the next
month still open, marked as a correction for the month it is for.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from auditlog.models import AuditLog
from common.tenant import use_company
from payroll.models import PayrollAdjustment, PayrollPeriod, PayrollRecord, PayrollRun
from payroll.services import (
    add_adjustment,
    correct_finalised_month,
    generate_payroll,
    open_period_after,
)
from payroll.tests_approval import finalise_payroll
from payroll.tests_branch_salary import BranchSalaryBase

AUGUST = {"month": 8, "year": 2026}


class CorrectionCase(BranchSalaryBase):
    def setUp(self):
        super().setUp()
        self.generate()
        self.august = self.records()["Rahim"]

    def finalise_august(self):
        finalise_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        self.august.refresh_from_db()

    def correct(self, actor=None, kind="earning", amount="750",
                reason="August overtime was missed"):
        return correct_finalised_month(
            actor=actor or self.admin, company_id=self.company.pk,
            record_id=self.august.pk, adjustment_type=kind,
            amount=Decimal(amount), reason=reason,
        )

    def september(self):
        run = generate_payroll(actor=self.admin, company_id=self.company.pk,
                               year=2026, month=9)
        with use_company(self.company):
            return PayrollRecord.objects.filter(
                payroll_run=run, employee=self.employee).first()


class CorrectionTests(CorrectionCase):
    def test_a_correction_is_paid_with_the_next_open_month(self):
        self.finalise_august()
        correction = self.correct()
        self.assertEqual(correction.source_payroll_period.name, "August 2026")
        self.assertEqual(correction.target_payroll_period.name, "September 2026")

        record = self.september()
        with use_company(self.company):
            line = record.lines.get(code="CORRECTION")
        self.assertEqual(line.amount, Decimal("750.00"))
        self.assertEqual(line.line_type, "earning")
        self.assertIn("correction for August 2026", line.description)
        self.assertIn("August overtime was missed", line.description)

    def test_the_finalised_month_is_not_touched(self):
        self.finalise_august()
        before = (self.august.gross_earnings, self.august.net_pay)
        self.correct()
        self.august.refresh_from_db()
        self.assertEqual((self.august.gross_earnings, self.august.net_pay), before)
        self.assertEqual(self.august.payroll_run.status, PayrollRun.Status.POSTED)
        with use_company(self.company):
            self.assertFalse(self.august.lines.filter(code="CORRECTION").exists())

    def test_a_deduction_correction_takes_money_back(self):
        self.finalise_august()
        self.correct(kind="deduction", amount="500", reason="Paid twice in August")
        record = self.september()
        with use_company(self.company):
            line = record.lines.get(code="CORRECTION")
        self.assertEqual(line.line_type, "deduction")

    def test_a_draft_month_is_corrected_by_a_normal_line_instead(self):
        with self.assertRaisesMessage(ValidationError, "not finalised"):
            self.correct()
        # Which is what the ordinary bonus line is for.
        add_adjustment(actor=self.admin, company_id=self.company.pk,
                       record_id=self.august.pk, adjustment_type="earning",
                       amount=Decimal("750"), reason="Missed overtime")

    def test_it_skips_a_month_that_is_finalised_too(self):
        self.finalise_august()
        generate_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=9)
        finalise_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=9)
        self.assertEqual(self.correct().target_payroll_period.name, "October 2026")

    def test_the_month_is_made_if_nobody_has_generated_it(self):
        self.finalise_august()
        with use_company(self.company):
            self.assertFalse(PayrollPeriod.objects.filter(name="September 2026").exists())
        self.assertEqual(self.correct().target_payroll_period.name, "September 2026")

    def test_it_shows_at_once_on_a_month_already_drafted(self):
        self.finalise_august()
        generate_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=9)
        self.correct()
        with use_company(self.company):
            record = PayrollRecord.objects.filter(
                payroll_run__payroll_period__name="September 2026",
                employee=self.employee).first()
            self.assertTrue(record.lines.filter(code="CORRECTION").exists())

    def test_amount_and_reason_are_required(self):
        self.finalise_august()
        for kwargs in ({"amount": "0"}, {"reason": "  "}):
            with self.subTest(**kwargs):
                with self.assertRaises(ValidationError):
                    self.correct(**kwargs)

    def test_it_is_audited_with_both_months(self):
        self.finalise_august()
        self.correct()
        entry = AuditLog.objects.get(action="payroll.correction_added")
        self.assertEqual(entry.after_data["for_month"], "August 2026")
        self.assertEqual(entry.after_data["paid_in"], "September 2026")


class WhoMayCorrectTests(CorrectionCase):
    def test_a_branch_manager_corrects_their_own_branch_only(self):
        self.finalise_august()
        self.correct(actor=self.manager)
        far = self.records()["Karim"]
        with self.assertRaises(PermissionDenied):
            correct_finalised_month(
                actor=self.manager, company_id=self.company.pk, record_id=far.pk,
                adjustment_type="earning", amount=Decimal("100"), reason="No")

    def test_someone_without_salary_access_cannot(self):
        self.finalise_august()
        with self.assertRaises(PermissionDenied):
            self.correct(actor=self.clerk_user)

    def test_the_page_offers_it_only_once_the_month_is_finalised(self):
        self.client.force_login(self.admin)
        url = reverse("payroll:payslip", args=[self.august.pk])
        self.assertNotContains(self.client.get(url), "Put this month right")

        self.finalise_august()
        page = self.client.get(url)
        self.assertContains(page, "Put this month right")
        response = self.client.post(
            reverse("payroll:payslip_correction_add", args=[self.august.pk]),
            {"adjustment_type": "earning", "amount": "750", "reason": "Missed overtime"},
            follow=True)
        self.assertContains(response, "paid with September 2026")
        self.assertContains(response, "August 2026 stays as it was paid")
        with use_company(self.company):
            self.assertTrue(PayrollAdjustment.objects.filter(
                source_payroll_period__name="August 2026").exists())

    def test_the_employee_never_sees_the_form(self):
        self.finalise_august()
        self.client.force_login(self.clerk_user)
        clerk_slip = self.records()["Clerk"]
        page = self.client.get(reverse("me:payslip", args=[clerk_slip.pk]))
        self.assertNotContains(page, "Put this month right")


class OpenPeriodTests(CorrectionCase):
    def test_it_stops_rather_than_searching_for_ever(self):
        self.finalise_august()
        with use_company(self.company):
            period = PayrollPeriod.objects.get(name="August 2026")
        with self.assertRaises(ValidationError):
            open_period_after(self.company.pk, period, months=0)
