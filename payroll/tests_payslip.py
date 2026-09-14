"""The payslip page (N7): layout and wording only.

Nothing here checks a calculation — tests_basic.py and tests_penalties.py do
that. These check that what the salary run already holds reaches the page
readably: amounts with thousands separators, a reason beside every deduction,
the run's real status, and the month's attendance behind the numbers.
"""

import datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from common.tenant import use_company
from payroll.models import PayrollRecord, PayrollRun, PenaltyAssessment
from payroll.penalties import create_penalty_rule
from payroll.services import generate_payroll, waive_penalty
from payroll import tests_basic


class PayslipPageTests(TestCase):
    # The same month as the end-to-end salary tests: demo punches for August,
    # a company shift, Friday weekly offs, and two unpaid leave days for Rahim.
    # Imported as a module, not by class name: a TestCase named at module level
    # here would be discovered again and its tests run twice.
    setUp = tests_basic.EndToEndTests.setUp

    def payslip(self, employee=None):
        run = generate_payroll(
            actor=self.admin, company_id=self.company.pk, year=2026, month=8
        )
        with use_company(self.company):
            record = PayrollRecord.objects.get(
                payroll_run=run, employee=employee or self.monthly
            )
        self.client.force_login(self.admin)
        return record, self.client.get(reverse("payroll:payslip", args=[record.pk]))

    def add_late_rule(self):
        create_penalty_rule(actor=self.admin, company_id=self.company.pk, values={
            "effective_from": datetime.date(2026, 8, 1), "name": "Late 10+",
            "metric": "late_minutes", "operator": "gte", "threshold_minutes": 10,
            "occurrence_mode": "single_day", "required_occurrences": 1,
            "deduction_method": "fixed_amount", "deduction_value": Decimal("100"),
            "exclusive_group": "", "maximum_deduction": None,
        })

    def test_every_amount_has_thousands_separators(self):
        record, response = self.payslip()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "30,000.00")  # the monthly salary
        self.assertContains(response, f"{record.net_pay:,.2f}")
        self.assertContains(response, f"{record.gross_earnings:,.2f}")

    def test_net_pay_is_the_one_hero_figure(self):
        record, response = self.payslip()
        self.assertContains(response, "metric--hero", count=1)
        self.assertContains(response, "Net pay")

    def test_a_deduction_says_why(self):
        """Two unpaid leave days: the line says two days at a day's pay."""
        _record, response = self.payslip()
        self.assertContains(response, "Unpaid leave days")
        self.assertContains(response, "2 days × 1,000.00 a day")

    def test_a_penalty_names_its_days_and_can_be_waived_on_a_draft(self):
        self.add_late_rule()
        record, response = self.payslip()
        with use_company(self.company):
            penalty = PenaltyAssessment.objects.filter(employee=self.monthly).first()
            late_on = penalty.days.first().attendance_record.work_date
        self.assertContains(response, "Late 10+")
        self.assertContains(response, late_on.strftime("%d %b"))
        self.assertContains(
            response, reverse("payroll:penalty_waive", args=[penalty.pk])
        )

    def test_a_waived_penalty_is_shown_as_not_deducted(self):
        self.add_late_rule()
        self.payslip()
        with use_company(self.company):
            penalty = PenaltyAssessment.objects.filter(employee=self.monthly).first()
        waive_penalty(
            actor=self.admin, company_id=self.company.pk, assessment_id=penalty.pk
        )
        _record, response = self.payslip()
        self.assertContains(response, "Waived, not deducted: Late 10+")

    def test_the_status_is_the_runs_own_not_always_draft(self):
        record, response = self.payslip()
        self.assertContains(response, "Draft")
        PayrollRun.all_objects.filter(pk=record.payroll_run_id).update(
            status=PayrollRun.Status.POSTED
        )
        response = self.client.get(reverse("payroll:payslip", args=[record.pk]))
        self.assertContains(response, "Finalised")
        self.assertNotContains(response, "Waive</button>")

    def test_the_attendance_summary_links_to_that_months_calendar(self):
        _record, response = self.payslip()
        self.assertContains(response, "Attendance in August 2026")
        self.assertContains(
            response,
            reverse("attendance:attendance_calendar")
            + f"?employee={self.monthly.pk}&year=2026&month=8",
        )

    def test_only_project_buttons(self):
        """btn--secondary has no styles; it renders as a bare button."""
        _record, response = self.payslip()
        self.assertNotContains(response, "btn--secondary")
        self.assertContains(response, "btn btn--ghost")
