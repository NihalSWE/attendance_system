"""Leave Fare Assistance (Nihal, 2026-09-28): the company's own rules, a claim,
its decision, and payment on the payslip or separately.

Rahim (Head Office, 30,000 a month) joined 1 January 2024; Clerk has an
Employee login; Manny manages Head Office; Karim is at Chittagong.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError

from common.tenant import use_company
from employees.models import Employee
from leaves import services as leave_services
from leaves.tests_branch_access import TwoBranchCase
from payroll import lfa
from payroll.models import LfaClaim, PayrollAdjustment, PayrollLine
from payroll.services import generate_payroll, remove_adjustment

D = datetime.date
TODAY = D(2026, 8, 20)
AUGUST = D(2026, 8, 1)


class LfaCase(TwoBranchCase):
    def setUp(self):
        super().setUp()
        with use_company(self.company):
            Employee.objects.filter(pk__in=[self.employee.pk, self.clerk.pk, self.far.pk]).update(
                joining_date=D(2024, 1, 1))
        self.employee.refresh_from_db()
        self.clerk.refresh_from_db()

    def rules(self, leave_types=(), **values):
        data = {"enabled": True, "name": "LFA", "description": "",
                "amount_method": "basic_months", "fixed_amount": None,
                "months": Decimal("1"), "max_amount": None, "min_service_months": 12,
                "probation_eligible": False, "cycle": "calendar_year", "claims_per_cycle": 1,
                "requires_leave": False, "min_leave_days": None, "requires_document": False,
                "prorate_first_cycle": False, "payment": "with_salary", **values}
        return lfa.save_settings(actor=self.admin, company_id=self.company.pk, values=data,
                                 leave_types=leave_types)

    def claim(self, employee=None, actor=None, **values):
        return lfa.submit(actor=actor or self.admin, company_id=self.company.pk,
                          employee=employee or self.employee, values=values, today=TODAY)

    def check(self, employee=None):
        with use_company(self.company):
            return lfa.eligibility(employee or self.employee, lfa.settings_for(self.company.pk),
                                   TODAY)


class EligibilityTests(LfaCase):
    def test_off_until_the_company_switches_it_on(self):
        found = self.check()
        self.assertFalse(found.ok)
        self.assertIn("not switched on", found.reasons[0])

    def test_one_months_basic_once_a_year(self):
        self.rules()
        found = self.check()
        self.assertTrue(found.ok, found.reasons)
        self.assertEqual(found.amount, Decimal("30000.00"))
        self.assertEqual(found.cycle, (D(2026, 1, 1), D(2026, 12, 31)))
        self.claim()
        again = self.check()
        self.assertFalse(again.ok)
        self.assertIn("Already claimed 1 of 1", " ".join(again.reasons))

    def test_months_of_basic_or_of_gross(self):
        from payroll import component_services

        rent = component_services.create_component(
            actor=self.admin, company_id=self.company.pk,
            values={"code": "HR", "name": "House rent", "kind": "earning",
                    "method": "percent_of_basic", "default_amount": None,
                    "default_percent": Decimal("50"), "description": ""})
        travel = component_services.create_component(
            actor=self.admin, company_id=self.company.pk,
            values={"code": "TR", "name": "Transport", "kind": "earning", "method": "fixed",
                    "default_amount": Decimal("2000"), "default_percent": None,
                    "description": ""})
        for component, amount, percent in ((rent, None, Decimal("50")),
                                           (travel, Decimal("2000"), None)):
            component_services.give_component(
                actor=self.admin, company_id=self.company.pk, employee_id=self.employee.pk,
                values={"component": component, "amount": amount, "percent": percent,
                        "effective_from": D(2026, 1, 1), "reason": ""})
        self.rules(months=Decimal("1.5"))
        found = self.check()
        self.assertEqual(found.amount, Decimal("45000.00"))                # 1.5 x 30,000
        self.assertIn("1.5 months of basic", found.how)
        self.rules(amount_method="gross_months", months=Decimal("1"))
        self.assertEqual(self.check().amount, Decimal("47000.00"))          # 30,000 + 15,000 + 2,000

    def test_no_trip_no_leave_needed_by_default(self):
        self.rules()
        claim = self.claim()
        self.assertIsNone(claim.leave_request)
        self.assertEqual(claim.status, LfaClaim.Status.PENDING)

    def test_minimum_service(self):
        self.rules(min_service_months=36)
        found = self.check()
        self.assertFalse(found.ok)
        self.assertIn("36 months of service; they have 31", " ".join(found.reasons))

    def test_probation_only_if_allowed(self):
        self.rules()
        with use_company(self.company):
            Employee.objects.filter(pk=self.employee.pk).update(employment_status="probation")
        self.employee.refresh_from_db()
        self.assertFalse(self.check().ok)
        self.rules(probation_eligible=True)
        self.assertTrue(self.check().ok)

    def test_fixed_amount_capped_and_prorated(self):
        self.rules(amount_method="fixed", fixed_amount=Decimal("24000"),
                   max_amount=Decimal("20000"))
        self.assertEqual(self.check().amount, Decimal("20000.00"))
        self.rules(amount_method="fixed", fixed_amount=Decimal("24000"), min_service_months=0,
                   prorate_first_cycle=True)
        with use_company(self.company):
            Employee.objects.filter(pk=self.employee.pk).update(joining_date=D(2026, 4, 1))
        self.employee.refresh_from_db()
        self.assertEqual(self.check().amount, Decimal("18000.00"))     # 9 of 12 months

    def test_service_year_cycle(self):
        self.rules(cycle="service_year")
        with use_company(self.company):
            Employee.objects.filter(pk=self.employee.pk).update(joining_date=D(2023, 10, 5))
        self.employee.refresh_from_db()
        self.assertEqual(self.check().cycle, (D(2025, 10, 5), D(2026, 10, 4)))


class LeaveAndProofTests(LfaCase):
    def test_leave_required_when_the_company_says_so(self):
        self.rules(requires_leave=True, min_leave_days=Decimal("3"),
                   leave_types=[self.leave_type])
        self.assertIn("approved leave", " ".join(self.check().reasons))
        leave = leave_services.record_leave(actor=self.admin, company_id=self.company.pk, values={
            "employee": self.employee, "leave_type": self.leave_type,
            "start_date": D(2026, 8, 10), "end_date": D(2026, 8, 12), "pay_type": "paid",
            "reason": ""})
        found = self.check()
        self.assertTrue(found.ok, found.reasons)
        with self.assertRaisesMessage(ValidationError, "Choose the approved leave"):
            self.claim()
        claim = self.claim(leave_request=found.leave_requests[0])
        self.assertEqual(claim.leave_request_id, leave.pk)

    def test_proof_required_when_the_company_says_so(self):
        self.rules(requires_document=True)
        with self.assertRaisesMessage(ValidationError, "Attach the proof"):
            self.claim()


class DecisionTests(LfaCase):
    def test_paid_on_the_payslip_of_the_month_chosen(self):
        self.rules()
        claim = lfa.submit(actor=self.clerk_user, company_id=self.company.pk, values={},
                           today=TODAY)
        self.assertEqual(claim.employee_id, self.clerk.pk)
        lfa.decide(actor=self.manager, company_id=self.company.pk, claim_id=claim.pk,
                   approve=True, pay_month=AUGUST)
        claim.refresh_from_db()
        self.assertEqual((claim.status, claim.approved_amount),
                         (LfaClaim.Status.APPROVED, Decimal("30000.00")))
        self.assertEqual(claim.payroll_adjustment.code, "LFA")
        generate_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        with use_company(self.company):
            line = PayrollLine.objects.get(payroll_record__employee=self.clerk, code="LFA")
        self.assertEqual((line.line_type, line.amount), ("earning", Decimal("30000.00")))
        # Not removable as a bonus: the claim is cancelled instead.
        with self.assertRaisesMessage(ValidationError, "Cancel the LFA claim"):
            remove_adjustment(actor=self.admin, company_id=self.company.pk,
                              adjustment_id=claim.payroll_adjustment_id)
        lfa.cancel(actor=self.admin, company_id=self.company.pk, claim_id=claim.pk,
                   note="Wrong month")
        with use_company(self.company):
            self.assertFalse(PayrollLine.objects.filter(code="LFA").exists())
        self.assertEqual(PayrollAdjustment.all_objects.get(pk=claim.payroll_adjustment_id).status,
                         "cancelled")

    def test_paid_once_the_month_is_finalised(self):
        from payroll.models import PayrollRun

        self.rules()
        claim = self.claim()
        lfa.decide(actor=self.admin, company_id=self.company.pk, claim_id=claim.pk,
                   approve=True, pay_month=AUGUST)
        run = generate_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        PayrollRun.all_objects.filter(pk=run.pk).update(status="posted")
        with use_company(self.company):
            claim = LfaClaim.objects.select_related(
                "payroll_adjustment__target_payroll_period").get(pk=claim.pk)
            lfa.sync_paid(self.company.pk, [claim])
        self.assertEqual((claim.status, claim.paid_on), (LfaClaim.Status.PAID, D(2026, 8, 31)))
        with self.assertRaisesMessage(ValidationError, "not yet paid"):
            lfa.cancel(actor=self.admin, company_id=self.company.pk, claim_id=claim.pk, note="x")

    def test_a_finalised_month_moves_it_to_the_next_open_one(self):
        from payroll.models import PayrollPeriod, PayrollRun

        self.rules()
        run = generate_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        PayrollRun.all_objects.filter(pk=run.pk).update(status="posted")
        claim = self.claim()
        lfa.decide(actor=self.admin, company_id=self.company.pk, claim_id=claim.pk,
                   approve=True, pay_month=AUGUST)
        claim.refresh_from_db()
        self.assertEqual(PayrollPeriod.all_objects.get(
            pk=claim.payroll_adjustment.target_payroll_period_id).start_date, D(2026, 9, 1))

    def test_paid_separately(self):
        self.rules(payment="separately")
        claim = self.claim()
        lfa.decide(actor=self.admin, company_id=self.company.pk, claim_id=claim.pk,
                   approve=True)
        claim.refresh_from_db()
        self.assertIsNone(claim.payroll_adjustment)
        lfa.mark_paid(actor=self.admin, company_id=self.company.pk, claim_id=claim.pk,
                      paid_on=D(2026, 8, 25), reference="Cheque 1234")
        claim.refresh_from_db()
        self.assertEqual((claim.status, claim.payment_reference), ("paid", "Cheque 1234"))

    def test_the_approver_decides_the_amount_up_to_the_cap(self):
        self.rules(amount_method="approver", months=None, max_amount=Decimal("25000"))
        claim = self.claim()
        self.assertIsNone(claim.calculated_amount)
        with self.assertRaisesMessage(ValidationError, "Give the amount"):
            lfa.decide(actor=self.admin, company_id=self.company.pk, claim_id=claim.pk,
                       approve=True, pay_month=AUGUST)
        with self.assertRaisesMessage(ValidationError, "most that can be approved"):
            lfa.decide(actor=self.admin, company_id=self.company.pk, claim_id=claim.pk,
                       approve=True, amount=Decimal("26000"), pay_month=AUGUST)
        lfa.decide(actor=self.admin, company_id=self.company.pk, claim_id=claim.pk,
                   approve=True, amount=Decimal("22000"), pay_month=AUGUST)
        claim.refresh_from_db()
        self.assertEqual(claim.approved_amount, Decimal("22000.00"))

    def test_reject_needs_a_reason_and_frees_the_year(self):
        self.rules()
        claim = self.claim()
        with self.assertRaisesMessage(ValidationError, "Say why"):
            lfa.decide(actor=self.admin, company_id=self.company.pk, claim_id=claim.pk,
                       approve=False)
        lfa.decide(actor=self.admin, company_id=self.company.pk, claim_id=claim.pk,
                   approve=False, note="Not this year")
        self.assertTrue(self.check().ok)

    def test_who_decides(self):
        self.rules()
        claim = lfa.submit(actor=self.clerk_user, company_id=self.company.pk, values={},
                           today=TODAY)
        far_manager = self.member("far@liv.test", "manager", branches=[self.unit])
        for actor in (self.clerk_user, far_manager, self.hr):
            with self.subTest(actor=actor.email), self.assertRaises(PermissionDenied):
                lfa.decide(actor=actor, company_id=self.company.pk, claim_id=claim.pk,
                           approve=True, pay_month=AUGUST)

    def test_the_rules_changing_later_do_not_change_a_claim(self):
        self.rules()
        claim = self.claim()
        self.rules(amount_method="fixed", fixed_amount=Decimal("5000"))
        lfa.decide(actor=self.admin, company_id=self.company.pk, claim_id=claim.pk,
                   approve=True, pay_month=AUGUST)
        claim.refresh_from_db()
        self.assertEqual(claim.approved_amount, Decimal("30000.00"))
        self.assertEqual(claim.settings_snapshot["amount_method"], "basic_months")

    def test_an_employee_withdraws_their_own_pending_claim(self):
        self.rules()
        claim = lfa.submit(actor=self.clerk_user, company_id=self.company.pk, values={},
                           today=TODAY)
        with self.assertRaises(PermissionDenied):
            lfa.withdraw(actor=self.hr, company_id=self.company.pk, claim_id=claim.pk)
        lfa.withdraw(actor=self.clerk_user, company_id=self.company.pk, claim_id=claim.pk)
        claim.refresh_from_db()
        self.assertEqual(claim.status, "withdrawn")


class SettingsTests(LfaCase):
    def test_only_the_owner_or_admin_sets_the_rules(self):
        with self.assertRaises(PermissionDenied):
            lfa.save_settings(actor=self.hr, company_id=self.company.pk, values={})

    def test_each_amount_method_needs_its_number(self):
        for method, field, said in (("fixed", "fixed_amount", "Give the amount"),
                                    ("basic_months", "months", "how many months"),
                                    ("approver", "max_amount", "most they may approve")):
            with self.subTest(method=method), self.assertRaisesMessage(ValidationError, said):
                self.rules(amount_method=method, **{field: None, "months": None})


class MigrationTests(LfaCase):
    def test_a_saved_percentage_becomes_months(self):
        import importlib

        from django.apps import apps

        from payroll.models import LfaSettings

        self.rules()
        LfaSettings.all_objects.filter(company=self.company).update(
            amount_method="basic_percent", months=Decimal("50"))     # 50 % of basic
        migration = importlib.import_module("payroll.migrations.0011_lfa_months")
        migration.percent_to_months(apps, None)
        saved = LfaSettings.all_objects.get(company=self.company)
        self.assertEqual((saved.amount_method, saved.months), ("basic_months", Decimal("0.50")))
