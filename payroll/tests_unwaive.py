"""Undoing a waived penalty (A11).

A waiver given by mistake puts the penalty back to proposed and regenerates
the month, so it is charged again like any other. Only while the month is
still a draft: once it is waiting for approval or finalised, the month is put
right with a correction instead.
"""

from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse

from auditlog.models import AuditLog
from common.tenant import use_company
from payroll.models import PenaltyAssessment
from payroll.services import (
    generate_payroll,
    submit_payroll,
    unwaive_penalty,
    waive_penalty,
)
from payroll.tests_approval import finalise_payroll
from payroll import tests_payslip


class UnwaiveTests(TestCase):
    # The payslip tests' month: a late rule, and one penalty to waive.
    setUp = tests_payslip.PayslipPageTests.setUp
    add_late_rule = tests_payslip.PayslipPageTests.add_late_rule
    payslip = tests_payslip.PayslipPageTests.payslip

    def waived(self):
        self.add_late_rule()
        self.payslip()
        with use_company(self.company):
            penalty = PenaltyAssessment.objects.filter(employee=self.monthly).first()
        waive_penalty(actor=self.admin, company_id=self.company.pk, assessment_id=penalty.pk)
        penalty.refresh_from_db()
        return penalty

    def penalty_lines(self, record):
        """How many penalties this payslip charges. The month can hold several:
        one waiver is one of them, not all."""
        with use_company(self.company):
            return record.lines.filter(code="PENALTY").count()

    def waived_count(self):
        with use_company(self.company):
            return PenaltyAssessment.objects.filter(
                employee=self.monthly, status=PenaltyAssessment.Status.WAIVED).count()

    def proposed(self):
        """A proposed penalty of this employee's. Regenerating rebuilds these
        rows, so it is looked up again rather than kept across a call."""
        with use_company(self.company):
            return PenaltyAssessment.objects.filter(
                employee=self.monthly, status=PenaltyAssessment.Status.PROPOSED).first()

    def test_undoing_charges_the_penalty_again(self):
        penalty = self.waived()
        record, _response = self.payslip()
        charged = self.penalty_lines(record)
        self.assertEqual(self.waived_count(), 1)

        back = unwaive_penalty(actor=self.admin, company_id=self.company.pk,
                               assessment_id=penalty.pk)
        self.assertEqual(self.waived_count(), 0)
        self.assertEqual(self.penalty_lines(back), charged + 1)

    def test_it_can_be_waived_again_afterwards(self):
        penalty = self.waived()
        unwaive_penalty(actor=self.admin, company_id=self.company.pk, assessment_id=penalty.pk)
        # Regenerating rebuilds proposed penalties, so the row is a new one.
        again = self.proposed()
        waive_penalty(actor=self.admin, company_id=self.company.pk, assessment_id=again.pk)
        self.assertEqual(self.waived_count(), 1)

    def test_a_penalty_that_is_not_waived_says_so(self):
        self.add_late_rule()
        self.payslip()
        with use_company(self.company):
            penalty = PenaltyAssessment.objects.filter(employee=self.monthly).first()
        with self.assertRaisesMessage(ValidationError, "not waived"):
            unwaive_penalty(actor=self.admin, company_id=self.company.pk,
                            assessment_id=penalty.pk)

    def test_not_once_the_month_is_waiting_for_approval(self):
        penalty = self.waived()
        submit_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        with self.assertRaisesMessage(ValidationError, "no longer a draft"):
            unwaive_penalty(actor=self.admin, company_id=self.company.pk,
                            assessment_id=penalty.pk)

    def test_not_once_the_month_is_finalised(self):
        penalty = self.waived()
        finalise_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        with self.assertRaisesMessage(ValidationError, "correction"):
            unwaive_penalty(actor=self.admin, company_id=self.company.pk,
                            assessment_id=penalty.pk)

    def test_it_stays_with_the_company(self):
        from accounts.models import CompanyMembership, User

        penalty = self.waived()
        hr = User.objects.create_user(email="hr@unwaive.test")
        CompanyMembership.all_objects.create(company=self.company, user=hr, role="hr",
                                             status="active")
        with self.assertRaises(PermissionDenied):
            unwaive_penalty(actor=hr, company_id=self.company.pk, assessment_id=penalty.pk)

    def test_it_is_audited(self):
        penalty = self.waived()
        unwaive_penalty(actor=self.admin, company_id=self.company.pk, assessment_id=penalty.pk)
        entry = AuditLog.objects.get(action="penalty.waiver_undone")
        self.assertEqual(entry.before_data["status"], "waived")
        self.assertEqual(entry.after_data["status"], "proposed")

    def test_the_payslip_offers_it_beside_the_waived_line(self):
        penalty = self.waived()
        record, response = self.payslip()
        self.assertContains(response, "Waived, not deducted: Late 10+")
        undo = reverse("payroll:penalty_unwaive", args=[penalty.pk])
        self.assertContains(response, undo)
        response = self.client.post(undo, follow=True)
        self.assertContains(response, "Waiver undone")
        self.assertEqual(self.waived_count(), 0)
