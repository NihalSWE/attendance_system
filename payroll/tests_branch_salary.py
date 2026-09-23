"""A12 part 6: salary by branch — each branch prepares its own, the company finalises."""

from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from attendance.models import AttendanceRecord
from attendance.services import recalculate
from common.tenant import use_company
from leaves.tests_branch_access import AUGUST, MONDAY, TwoBranchCase
from payroll import overtime
from payroll.models import PayrollRun
from payroll.services import add_adjustment, generate_payroll
from payroll.tests_approval import finalise_payroll
from payroll.tests_overtime import LATER


class BranchSalaryBase(TwoBranchCase):
    def generate(self, actor=None, branches=None):
        return generate_payroll(
            actor=actor or self.admin, company_id=self.company.pk, year=2026, month=8,
            branch_ids=None if branches is None else [b.pk for b in branches],
        )

    def records(self):
        with use_company(self.company):
            run = PayrollRun.objects.get()
            return {r.employee.first_name: r for r in run.records.select_related("employee")}

    def payslip_url(self, record):
        return reverse("payroll:payslip", args=[record.pk])


class GenerateTests(BranchSalaryBase):
    def test_a_branch_generates_its_own_people_and_leaves_the_rest(self):
        self.generate(self.manager, [self.branch])
        self.assertEqual(set(self.records()), {"Rahim", "Clerk"})

        self.generate()  # the company: everybody
        before = self.records()
        self.assertEqual(set(before), {"Rahim", "Clerk", "Karim"})

        run = self.generate(self.manager, [self.branch])
        after = self.records()
        self.assertEqual(after["Karim"].pk, before["Karim"].pk)   # untouched
        self.assertNotEqual(after["Rahim"].pk, before["Rahim"].pk)  # rebuilt
        # The month's totals still cover every branch.
        self.assertEqual(run.totals_snapshot["employees"], 3)
        self.assertEqual(Decimal(run.totals_snapshot["net"]),
                         sum(r.net_pay for r in after.values()))

    def test_only_branches_where_they_may_prepare(self):
        with self.assertRaises(PermissionDenied):
            self.generate(self.manager, [self.unit])
        self.grant("salary.view", self.branch)
        with self.assertRaises(PermissionDenied):
            self.generate(self.clerk_user, [self.branch])

    def test_finalising_stays_with_the_company(self):
        self.generate()
        with self.assertRaises(PermissionDenied):
            finalise_payroll(actor=self.manager, company_id=self.company.pk, year=2026, month=8)
        self.client.force_login(self.manager)
        self.assertRedirects(self.client.get(reverse("payroll:payroll_finalise"), AUGUST),
                             reverse("me:home"))

    def test_overtime_decided_after_a_payslip_still_blocks_finalising(self):
        # Karim stays late on Monday; the whole month is generated; his
        # overtime is then decided; only Head Office regenerates.
        for hour in (9, 20):
            self.far_punch(MONDAY, hour)
        recalculate(self.company.pk, start=MONDAY, end=MONDAY, now=LATER)
        self.generate()
        with use_company(self.company):
            day = AttendanceRecord.objects.get(employee=self.far, work_date=MONDAY)
        overtime.decide_overtime(actor=self.admin, company_id=self.company.pk,
                                 record_id=day.pk, approve=True, minutes=60)
        self.generate(self.manager, [self.branch])
        with self.assertRaises(ValidationError):
            finalise_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        self.generate()
        finalise_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)


class SalaryPageTests(BranchSalaryBase):
    def test_a_branch_manager_sees_and_generates_their_branch(self):
        self.client.force_login(self.manager)
        response = self.client.post(reverse("payroll:payroll_generate"), AUGUST)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(set(self.records()), {"Rahim", "Clerk"})
        page = self.client.get(reverse("payroll:payroll_home"), AUGUST)
        self.assertContains(page, "Rahim")
        self.assertContains(page, "for your branches")
        self.assertContains(page, "in your branches")
        self.assertNotContains(page, reverse("payroll:payroll_finalise"))
        self.assertNotContains(page, reverse("payroll:salary_settings"))
        self.generate()
        page = self.client.get(reverse("payroll:payroll_home"), AUGUST)
        self.assertNotContains(page, "Karim")
        self.assertEqual(self.client.get(self.payslip_url(self.records()["Karim"])).status_code, 403)

    def test_view_only_sees_payslips_without_changing_them(self):
        self.generate()
        self.grant("salary.view", self.branch)
        self.client.force_login(self.clerk_user)
        page = self.client.get(reverse("payroll:payroll_home"), AUGUST)
        self.assertContains(page, "Rahim")
        self.assertNotContains(page, reverse("payroll:payroll_generate"))
        self.assertEqual(self.client.post(reverse("payroll:payroll_generate"), AUGUST).status_code, 403)
        slip = self.client.get(self.payslip_url(self.records()["Rahim"]))
        self.assertEqual(slip.status_code, 200)
        self.assertContains(slip, "Adding lines needs access to prepare salary")
        self.assertNotContains(slip, reverse("payroll:payslip_adjustment_add", args=[self.records()["Rahim"].pk]))

    def test_prepare_in_another_branch_adds_lines_there_only(self):
        self.generate()
        self.grant("salary.prepare", self.unit)
        before = self.records()
        record = add_adjustment(actor=self.clerk_user, company_id=self.company.pk,
                                record_id=before["Karim"].pk, adjustment_type="earning",
                                amount="500", reason="Eid bonus")
        self.assertEqual(record.gross_earnings, before["Karim"].gross_earnings + 500)
        # Only Chittagong was regenerated.
        self.assertEqual(self.records()["Rahim"].pk, before["Rahim"].pk)
        with self.assertRaises(PermissionDenied):
            add_adjustment(actor=self.clerk_user, company_id=self.company.pk,
                           record_id=before["Rahim"].pk, adjustment_type="earning",
                           amount="500", reason="Eid bonus")

    def test_the_owner_is_unchanged_and_hr_sees_no_salary(self):
        self.generate()
        self.client.force_login(self.admin)
        page = self.client.get(reverse("payroll:payroll_home"), AUGUST)
        for name in ("Rahim", "Karim"):
            self.assertContains(page, name)
        # A draft month offers Submit for approval; Approve comes once submitted.
        self.assertContains(page, reverse("payroll:payroll_submit"))
        self.assertNotContains(page, "for your branches")
        # HR has no salary access at all (Ajay, 2026-09-23); it used to see this
        # page, every figure on it, while being refused the payslips.
        self.client.force_login(self.hr)
        page = self.client.get(reverse("payroll:payroll_home"), AUGUST)
        self.assertEqual(page.status_code, 403)
        self.assertEqual(self.client.get(self.payslip_url(self.records()["Karim"])).status_code, 403)
