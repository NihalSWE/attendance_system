"""Daily threshold blocks and audited salary-cycle overtime totals."""

import datetime
from decimal import Decimal
from types import SimpleNamespace

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import SimpleTestCase
from django.urls import reverse

from attendance.models import AttendanceRecord
from common.exceptions import NotInThisCompany
from common.tenant import use_company
from payroll import overtime_overrides
from payroll.models import OvertimeOverride, PayrollRun
from payroll.policy import change_salary_rules
from payroll.services import calculate_pay, generate_payroll, submit_payroll
from payroll.tests_overtime import OvertimeBase, day, line, rules

D = datetime.date


class ThresholdTests(SimpleTestCase):
    def test_each_hour_remainder_uses_the_threshold(self):
        policy = rules(minimum_overtime_minutes=40, overtime_rounding_minutes=60)
        for actual, paid in [(0, 0), (1, 0), (39, 0), (40, 60), (50, 60), (59, 60),
                             (60, 60), (99, 60), (100, 120), (119, 120), (120, 120),
                             (159, 120), (160, 180), (180, 180)]:
            with self.subTest(minutes=actual):
                self.assertEqual(policy.payable_overtime(actual), paid)

    def test_users_monthly_example_is_30500(self):
        policy = rules(minimum_overtime_minutes=40, overtime_rounding_minutes=60,
                       overtime_multiplier=Decimal(1))
        result = calculate_pay("monthly", "30000", [day(50), day(160, on=D(2026, 8, 11))], rules=policy)
        self.assertEqual(line(result, "OVERTIME")[3:], (Decimal("4.00"), Decimal("125"), Decimal("500.00")))
        self.assertEqual(result["net"], Decimal("30500.00"))
        self.assertEqual([d["paid_minutes"] for d in result["overtime"]], [60, 180])

    def test_small_days_are_not_added_to_reach_the_threshold(self):
        result = calculate_pay("monthly", "30000", [day(20), day(20, on=D(2026, 8, 11))],
                               rules=rules(minimum_overtime_minutes=40, overtime_rounding_minutes=60))
        self.assertIsNone(line(result, "OVERTIME"))

    def test_exact_minutes_zero_threshold_and_small_blocks(self):
        self.assertEqual(rules(minimum_overtime_minutes=40).payable_overtime(50), 50)
        self.assertEqual(rules(overtime_rounding_minutes=60).payable_overtime(50), 0)
        self.assertEqual(rules(minimum_overtime_minutes=10, overtime_rounding_minutes=15).payable_overtime(40), 45)
        self.assertEqual(rules(minimum_overtime_minutes=30, overtime_rounding_minutes=15).payable_overtime(95), 90)

    def test_manual_totals_replace_scans_with_separate_day_off_rate(self):
        result = calculate_pay("monthly", "30000", [day(600)],
                               rules=rules(overtime_multiplier=Decimal(1), holiday_overtime_multiplier=Decimal(2)),
                               overtime_override=SimpleNamespace(hours=Decimal(4), day_off_hours=Decimal(2)))
        self.assertEqual(line(result, "OVERTIME")[5], Decimal(500))
        self.assertEqual(line(result, "DAY_OFF_WORK")[5], Decimal(500))
        self.assertEqual(result["net"], Decimal(31000))


class CycleOverrideTests(OvertimeBase):
    def setUp(self):
        super().setUp()
        self.shift.default_break_minutes = 60
        self.shift.minimum_full_day_minutes = 480
        self.shift.save()
        change_salary_rules(actor=self.admin, company_id=self.company.pk, values={
            "effective_from": D(2026, 8, 1), "minimum_overtime_minutes": 40,
            "overtime_rounding_minutes": 60, "overtime_multiplier": Decimal(1)})
        self.work(D(2026, 8, 10), (9, 0), (18, 50))
        self.work(D(2026, 8, 11), (9, 0), (20, 40))
        self.generate()
        self.client.force_login(self.admin)

    def generate(self):
        self.run = generate_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        with use_company(self.company):
            self.slip = self.run.records.get(employee=self.employee)
        return self.slip

    def save_hours(self, **values):
        return overtime_overrides.save(actor=self.admin, company_id=self.company.pk,
                                        record_id=self.slip.pk, hours=values.pop("hours", "6"),
                                        reason=values.pop("reason", "Reviewed the cycle"), **values)

    def test_generation_rounds_daily_before_summing(self):
        with use_company(self.company):
            ot = self.slip.lines.get(code="OVERTIME")
        self.assertEqual((ot.quantity, ot.amount), (Decimal(4), Decimal(500)))

    def test_override_survives_regeneration_and_leaves_attendance_intact(self):
        with use_company(self.company):
            before = list(AttendanceRecord.objects.values_list("pk", "approved_overtime_minutes"))
        row = self.save_hours()
        self.assertEqual(overtime_overrides.changed_since(self.company.pk, self.run), 1)
        with self.assertRaisesMessage(ValidationError, "Regenerate"):
            submit_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        self.generate()
        self.generate()
        with use_company(self.company):
            ot = self.slip.lines.get(code="OVERTIME")
            after = list(AttendanceRecord.objects.values_list("pk", "approved_overtime_minutes"))
        self.assertEqual(before, after)
        self.assertEqual((ot.quantity, ot.amount, ot.is_manual), (Decimal(6), Decimal(750), True))
        self.assertEqual(self.slip.calculation_snapshot["overtime_override"]["id"], row.pk)
        self.assertEqual(overtime_overrides.changed_since(self.company.pk, self.run), 0)

    def test_zero_replacement_and_revert(self):
        first = self.save_hours()
        second = self.save_hours(hours="0")
        first.refresh_from_db()
        self.assertEqual(first.status, OvertimeOverride.Status.REPLACED)
        self.generate()
        with use_company(self.company):
            self.assertFalse(self.slip.lines.filter(code__in=["OVERTIME", "DAY_OFF_WORK"]).exists())
        self.save_hours(revert=True, reason="Return to scans")
        second.refresh_from_db()
        self.assertEqual(second.status, OvertimeOverride.Status.REVERTED)
        self.assertEqual(overtime_overrides.changed_since(self.company.pk, self.run), 1)
        self.generate()
        with use_company(self.company):
            self.assertEqual(self.slip.lines.get(code="OVERTIME").quantity, Decimal(4))

    def test_invalid_totals_and_missing_reason_are_rejected(self):
        for hours in ["NaN", "Infinity", "-1", "1.5", "900", "0.001", "text"]:
            with self.subTest(hours=hours), self.assertRaises(ValidationError):
                self.save_hours(hours=hours)
        with self.assertRaises(ValidationError):
            self.save_hours(reason="")

    def test_submitted_and_posted_cycles_refuse_set_and_revert(self):
        self.save_hours()
        for status in (PayrollRun.Status.SUBMITTED, PayrollRun.Status.POSTED):
            PayrollRun.all_objects.filter(pk=self.run.pk).update(status=status)
            with self.assertRaises(ValidationError):
                self.save_hours()
            with self.assertRaises(ValidationError):
                self.save_hours(revert=True)

    def test_settings_changed_after_generation_require_regeneration(self):
        change_salary_rules(actor=self.admin, company_id=self.company.pk, values={
            "effective_from": D(2026, 8, 1), "minimum_overtime_minutes": 40,
            "overtime_rounding_minutes": 60, "overtime_multiplier": Decimal(2)})
        self.assertEqual(overtime_overrides.rules_changed_since(self.company.pk, self.run), 1)
        with self.assertRaisesMessage(ValidationError, "Regenerate"):
            submit_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)

    def test_salary_page_and_form_save_and_revert(self):
        home = self.client.get(reverse("payroll:payroll_home"), {"year": 2026, "month": 8})
        self.assertContains(home, "Set overtime hours")
        self.assertContains(home, "a remainder of 40 minutes earns the next block")
        url = reverse("payroll:cycle_overtime", args=[self.slip.pk])
        self.assertContains(self.client.get(url), "125.00 BDT/hour")
        response = self.client.post(url, {"hours": "7", "day_off_hours": "0", "reason": "Reviewed"})
        self.assertEqual(response.status_code, 302)
        self.assertContains(self.client.get(reverse("payroll:payroll_home"), {"year": 2026, "month": 8}),
                            "Overtime totals or rules changed")
        self.assertEqual(self.client.post(url, {"action": "revert", "reason": "Use scans"}).status_code, 302)

    def test_foreign_company_payslip_is_not_found(self):
        from tenants.services import onboard_company
        other = onboard_company(code="OTOTHER", slug="otother", name="Other")
        with self.assertRaises(NotInThisCompany):
            overtime_overrides.record_for(self.admin, other.pk, self.slip.pk)

    def test_employee_without_salary_permission_cannot_set_hours(self):
        from accounts.models import CompanyMembership, User
        user = User.objects.create_user(email="otviewer@example.test", password="pw")
        CompanyMembership.all_objects.create(company=self.company, user=user, role="employee", status="active")
        with self.assertRaises(PermissionDenied):
            overtime_overrides.save(actor=user, company_id=self.company.pk, record_id=self.slip.pk,
                                     hours=1, reason="No access")

    def test_changed_blocks_do_not_delete_the_existing_draft(self):
        change_salary_rules(actor=self.admin, company_id=self.company.pk, values={
            "effective_from": D(2026, 8, 1), "overtime_rounding_minutes": 30})
        self.save_hours(hours="1.5")
        self.generate()
        previous_id = self.slip.pk
        change_salary_rules(actor=self.admin, company_id=self.company.pk, values={
            "effective_from": D(2026, 8, 1), "overtime_rounding_minutes": 60})
        with self.assertRaisesMessage(ValidationError, "blocks"):
            self.generate()
        from payroll.models import PayrollRecord
        self.assertTrue(PayrollRecord.all_objects.filter(pk=previous_id).exists())


from payroll.tests_weekly import WeeklyCase


class WeeklyOverrideTests(WeeklyCase):
    def test_weekly_override_stays_in_its_own_cycle(self):
        self.daily()
        self.join(D(2026, 8, 1))
        self.work(D(2026, 8, 10), (9, 0), (20, 0))
        run = self.week(D(2026, 8, 10))
        slip = self.pay_record(run)
        overtime_overrides.save(actor=self.admin, company_id=self.company.pk, record_id=slip.pk,
                                 hours=5, reason="Weekly total")
        run = self.week(D(2026, 8, 10))
        slip = self.pay_record(run)
        with use_company(self.company):
            self.assertEqual(slip.lines.get(code="OVERTIME").quantity, Decimal(5))
        self.assertIsNone(self.pay_record(self.month()))
        submit_payroll(actor=self.admin, company_id=self.company.pk,
                       pay_group=self.group, week=D(2026, 8, 10))
        with self.assertRaises(ValidationError):
            overtime_overrides.save(actor=self.admin, company_id=self.company.pk, record_id=slip.pk,
                                     hours=6, reason="Locked week")


from payroll.tests_branch_salary import BranchSalaryBase


class BranchOverrideTests(BranchSalaryBase):
    def test_branch_preparer_cannot_change_other_branch(self):
        self.generate()
        slips = self.records()
        overtime_overrides.save(actor=self.manager, company_id=self.company.pk,
                                 record_id=slips["Rahim"].pk, hours=2, reason="Own branch")
        with self.assertRaises(PermissionDenied):
            overtime_overrides.save(actor=self.manager, company_id=self.company.pk,
                                     record_id=slips["Karim"].pk, hours=2, reason="Outside scope")

