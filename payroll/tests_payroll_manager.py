"""The payroll manager and HR on the salary pages (A12, Ajay 2026-09-23).

The payroll manager views salary, prepares it and submits it for approval,
across every branch. Not approve: there is exactly one owner-or-admin per
company, and approving, finalising, waiving penalties and the salary settings
stay theirs. HR and the auditor have no salary access at all.
"""

from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, TestCase
from django.urls import reverse

from access_control.branch_access import ALL_BRANCHES, branches_for
from accounts.models import CompanyMembership, User
from attendance.services import calculate_attendance
from base_template.context_processors import shell
from common.tenant import use_company
from payroll.models import PayrollRun, PenaltyAssessment
from payroll.services import (
    add_adjustment,
    approval_blocker,
    approve_payroll,
    correct_finalised_month,
    reopen_payroll,
    return_payroll,
    submit_payroll,
    waive_penalty,
)
from payroll.tests_approval import finalise_payroll
from payroll.tests_branch_salary import BranchSalaryBase
from payroll import tests_payslip

AUGUST = "?year=2026&month=8"


class PayrollManagerCase(BranchSalaryBase):
    def setUp(self):
        super().setUp()
        self.payroll = self.member("payroll@liv.test", "payroll_manager")
        self.auditor = self.member("audit@liv.test", "auditor")

    def home(self, user):
        self.client.force_login(user)
        return self.client.get(reverse("payroll:payroll_home") + AUGUST)

    def sidebar_labels(self, user):
        request = RequestFactory().get("/")
        request.user = user
        request.company_id = self.company.pk
        request.self_service = False
        return [link["label"] for menu in shell(request)["company_menus"]
                for link in menu["links"]]


class CodesTests(PayrollManagerCase):
    def test_the_payroll_manager_holds_salary_everywhere_and_nothing_else(self):
        for code in ("salary.view", "salary.prepare"):
            self.assertIs(branches_for(self.payroll, self.company.pk, code), ALL_BRANCHES)
        for code in ("employees.edit", "leave.approve", "overtime.decide", "access.grant"):
            self.assertEqual(branches_for(self.payroll, self.company.pk, code), set())

    def test_hr_holds_no_salary_code(self):
        for code in ("salary.view", "salary.prepare"):
            self.assertEqual(branches_for(self.hr, self.company.pk, code), set())


class PayrollManagerTests(PayrollManagerCase):
    def test_generates_the_whole_month(self):
        self.generate(self.payroll)
        self.assertEqual(set(self.records()), {"Rahim", "Clerk", "Karim"})

    def test_the_month_page_offers_generate_and_submit_but_not_approve(self):
        page = self.home(self.payroll)
        self.assertContains(page, "Generate August 2026")
        self.assertNotContains(page, "for your branches")

        self.generate(self.payroll)
        page = self.home(self.payroll)
        self.assertContains(page, "Karim")  # every branch
        self.assertContains(page, "Submit August for approval")
        self.assertNotContains(page, "Salary settings")

        submit_payroll(actor=self.payroll, company_id=self.company.pk, year=2026, month=8)
        page = self.home(self.payroll)
        self.assertNotContains(page, "Approve August")
        self.assertContains(page, "Take back")
        self.assertContains(page, "The owner or company administrator approves it.")

    def test_generating_by_the_page_covers_everybody(self):
        self.client.force_login(self.payroll)
        response = self.client.post(reverse("payroll:payroll_generate"),
                                    {"year": 2026, "month": 8}, follow=True)
        self.assertContains(response, "Salary generated for 3 employee(s)")

    def test_submits_but_the_owner_or_admin_approves(self):
        self.generate(self.payroll)
        submit_payroll(actor=self.payroll, company_id=self.company.pk, year=2026, month=8)
        with use_company(self.company):
            run = PayrollRun.objects.get()
        self.assertTrue(approval_blocker(self.payroll, self.company.pk, run))
        with self.assertRaises(PermissionDenied):
            approve_payroll(actor=self.payroll, company_id=self.company.pk, year=2026, month=8)
        approve_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)

    def test_can_take_back_their_own_submission(self):
        self.generate(self.payroll)
        submit_payroll(actor=self.payroll, company_id=self.company.pk, year=2026, month=8)
        run = return_payroll(actor=self.payroll, company_id=self.company.pk,
                             year=2026, month=8, reason="Missed a bonus")
        self.assertEqual(run.status, PayrollRun.Status.DRAFT)

    def test_cannot_undo_a_finalised_month(self):
        self.generate()
        finalise_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        with self.assertRaises(PermissionDenied):
            reopen_payroll(actor=self.payroll, company_id=self.company.pk,
                           year=2026, month=8, reason="No")

    def test_adds_lines_and_corrections_in_any_branch(self):
        self.generate(self.payroll)
        far = self.records()["Karim"]
        add_adjustment(actor=self.payroll, company_id=self.company.pk, record_id=far.pk,
                       adjustment_type="earning", amount=Decimal("500"), reason="Bonus")
        finalise_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        far = self.records()["Karim"]  # the line rebuilt the payslip
        correction = correct_finalised_month(
            actor=self.payroll, company_id=self.company.pk, record_id=far.pk,
            adjustment_type="earning", amount=Decimal("100"), reason="Missed")
        self.assertEqual(correction.target_payroll_period.name, "September 2026")

    def test_opens_any_payslip(self):
        self.generate()
        self.client.force_login(self.payroll)
        for name in ("Rahim", "Karim"):
            page = self.client.get(self.payslip_url(self.records()[name]))
            self.assertEqual(page.status_code, 200, name)

    def test_salary_settings_and_allowances_stay_with_the_owner_or_admin(self):
        self.client.force_login(self.payroll)
        for url in ("payroll:salary_settings", "payroll:component_list"):
            self.assertEqual(self.client.get(reverse(url)).status_code, 403, url)

    def test_recalculates_the_month_it_generates(self):
        calculate_attendance(actor=self.payroll, company_id=self.company.pk, year=2026, month=8)

    def test_the_sidebar_shows_salary(self):
        self.assertIn("Salary by month", self.sidebar_labels(self.payroll))


class HrHasNoSalaryTests(PayrollManagerCase):
    def test_hr_is_refused_salary_by_month(self):
        self.generate()
        self.assertEqual(self.home(self.hr).status_code, 403)

    def test_hr_is_refused_a_payslip(self):
        self.generate()
        self.client.force_login(self.hr)
        page = self.client.get(self.payslip_url(self.records()["Rahim"]))
        self.assertEqual(page.status_code, 403)

    def test_hr_cannot_generate_or_submit(self):
        with self.assertRaises(PermissionDenied):
            self.generate(self.hr)
        with self.assertRaises(PermissionDenied):
            calculate_attendance(actor=self.hr, company_id=self.company.pk, year=2026, month=8)
        self.generate()
        with self.assertRaises(PermissionDenied):
            submit_payroll(actor=self.hr, company_id=self.company.pk, year=2026, month=8)

    def test_the_sidebar_leaves_salary_out_and_keeps_the_rest(self):
        labels = self.sidebar_labels(self.hr)
        self.assertNotIn("Salary by month", labels)
        # What HR already had stays.
        self.assertIn("Record leave", labels)
        self.assertIn("Overtime", labels)


class AuditorHasNoSalaryTests(PayrollManagerCase):
    # It used to see Salary by month - every total - but no payslip: the
    # aggregate leaked while the detail was withheld. Now nothing (Ajay,
    # 2026-09-23); an auditor is designed when one is actually needed.
    def test_the_auditor_sees_no_salary(self):
        self.generate()
        self.assertEqual(self.home(self.auditor).status_code, 403)
        self.client.force_login(self.auditor)
        page = self.client.get(self.payslip_url(self.records()["Rahim"]))
        self.assertEqual(page.status_code, 403)
        self.assertNotIn("Salary by month", self.sidebar_labels(self.auditor))
        for code in ("salary.view", "salary.prepare"):
            self.assertEqual(branches_for(self.auditor, self.company.pk, code), set())


class WaivingStaysWithTheAdminTests(TestCase):
    setUp = tests_payslip.PayslipPageTests.setUp
    add_late_rule = tests_payslip.PayslipPageTests.add_late_rule
    payslip = tests_payslip.PayslipPageTests.payslip

    def test_the_payroll_manager_sees_no_waive_button_and_cannot_waive(self):
        payroll = User.objects.create_user(email="payroll@slip.test")
        CompanyMembership.all_objects.create(
            company=self.company, user=payroll, role="payroll_manager", status="active")
        self.add_late_rule()
        record, page = self.payslip()
        self.assertContains(page, reverse("payroll:penalty_waive",
                                          args=[self.penalty().pk]))  # the admin's view

        self.client.force_login(payroll)
        page = self.client.get(reverse("payroll:payslip", args=[record.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, reverse("payroll:penalty_waive", args=[self.penalty().pk]))
        with self.assertRaises(PermissionDenied):
            waive_penalty(actor=payroll, company_id=self.company.pk,
                          assessment_id=self.penalty().pk)

    def penalty(self):
        with use_company(self.company):
            return PenaltyAssessment.objects.filter(employee=self.monthly).first()
