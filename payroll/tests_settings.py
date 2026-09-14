"""Company salary settings: dated rule versions, and the calculation reading them."""

import datetime
from decimal import Decimal
from types import SimpleNamespace

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from accounts.models import CompanyMembership, User
from auditlog.models import AuditLog
from attendance.models import AttendanceRecord
from common.tenant import use_company
from payroll.models import PayrollPolicyVersion, PayrollSettings
from payroll.policy import (
    STANDARD_RULES,
    SalaryRules,
    change_salary_rules,
    rules_for,
    update_general_settings,
)
from payroll.services import calculate_pay
from tenants.services import onboard_company

S = AttendanceRecord.AttendanceStatus
SHIFT = SimpleNamespace(scheduled_minutes=540, break_is_paid=False, default_break_minutes=60)
# The rules form always posts its overtime section (A9).
OVERTIME_POST = {
    "overtime_multiplier": "2", "holiday_overtime_multiplier": "2",
    "minimum_overtime_minutes": "0", "overtime_rounding_minutes": "0",
}


def day(status, fraction="1", worked=480, leave_minutes=0, shift=SHIFT):
    leave = SimpleNamespace(leave_minutes=leave_minutes) if status == S.LEAVE else None
    return SimpleNamespace(
        attendance_status=status, payable_fraction=Decimal(fraction),
        worked_minutes=worked, late_minutes=0, leave_day=leave, shift=shift,
    )


def rules(**overrides):
    config = {key: overrides.pop(key) for key in list(overrides) if key in PayrollPolicyVersion.CONFIG_DEFAULTS}
    version = PayrollPolicyVersion(calculation_config=config, **overrides)
    return SalaryRules.from_version(version)


def lines_by_code(result):
    return {code: amount for _, code, _, _, _, amount in result["lines"]}


class CalculationRulesTests(SimpleTestCase):
    """calculate_pay reads the company's rules instead of fixed numbers."""

    def month(self):
        return (
            [day(S.PRESENT)] * 20
            + [day(S.ABSENT, "0", 0)] * 2
            + [day(S.HALF_DAY, "0.5", 240)]
            + [day(S.WEEKLY_OFF, "1", 0)] * 4
            + [day(S.HOLIDAY, "1", 0)]
        )

    def test_standard_rules_are_the_agreed_formula(self):
        result = calculate_pay("monthly", Decimal("30000"), self.month(), STANDARD_RULES)
        self.assertEqual(result["deductions"], Decimal("2500.00"))  # 2.5 days x 1000
        self.assertIsNone(result["rules"]["version_id"])

    def test_one_day_is_monthly_salary_over_days_in_the_month(self):
        result = calculate_pay(
            "monthly", Decimal("31000"), self.month(),
            rules(monthly_proration_method="calendar_days"), days_in_month=31,
        )
        self.assertEqual(result["deductions"], Decimal("2500.00"))  # 2.5 x 1000

    def test_one_day_is_monthly_salary_over_working_days(self):
        # 23 working days (20 present, 2 absent, 1 half day).
        result = calculate_pay(
            "monthly", Decimal("23000"), self.month(),
            rules(monthly_proration_method="scheduled_workdays"),
        )
        self.assertEqual(result["deductions"], Decimal("2500.00"))

    def test_a_fixed_divisor_other_than_30(self):
        result = calculate_pay("monthly", Decimal("26000"), self.month(), rules(monthly_divisor=Decimal("26")))
        self.assertEqual(result["deductions"], Decimal("2500.00"))

    def test_absence_by_minutes_deducts_every_minute_short(self):
        # Shift 540 min less a 60 min unpaid break = 480 expected minutes.
        month = [day(S.PRESENT, worked=480)] * 20 + [day(S.PRESENT, worked=360)] + [day(S.ABSENT, "0", 0)]
        result = calculate_pay(
            "monthly", Decimal("30000"), month,
            rules(absence_deduction_method="scheduled_minutes"),
        )
        lines = lines_by_code(result)
        self.assertEqual(lines["ABSENT"], Decimal("1000.00"))
        self.assertEqual(lines["SHORT_MINUTES"], Decimal("250.00"))  # 120/480 of a day

    def test_absence_only_through_penalty_rules_still_deducts_unpaid_leave(self):
        month = self.month() + [day(S.LEAVE, "0", 0)]
        result = calculate_pay(
            "monthly", Decimal("30000"), month, rules(absence_deduction_method="rule_only"),
        )
        self.assertEqual(lines_by_code(result), {"BASIC": Decimal("30000.00"), "UNPAID_LEAVE": Decimal("1000.00")})

    def test_a_half_day_can_pay_more_than_half(self):
        monthly = calculate_pay("monthly", Decimal("30000"), self.month(), rules(half_day_pay_percent="75"))
        self.assertEqual(lines_by_code(monthly)["HALF_DAY"], Decimal("250.00"))
        daily = calculate_pay("daily", Decimal("1000"), self.month(), rules(half_day_pay_percent="75"))
        self.assertEqual(daily["net"], Decimal("20750.00"))

    def test_a_day_without_check_out(self):
        month = [day(S.PRESENT)] * 20 + [day(S.INCOMPLETE, "1", 0)]
        standard_hourly = calculate_pay("hourly", Decimal("100"), month, STANDARD_RULES)
        # Paid in full until reviewed: the shift's 8 expected hours.
        self.assertEqual(lines_by_code(standard_hourly)["INCOMPLETE"], Decimal("800.00"))
        unpaid = rules(incomplete_day_treatment="unpaid")
        self.assertEqual(lines_by_code(calculate_pay("monthly", Decimal("30000"), month, unpaid))["INCOMPLETE"], Decimal("1000.00"))
        self.assertEqual(calculate_pay("daily", Decimal("1000"), month, unpaid)["net"], Decimal("20000.00"))
        self.assertNotIn("INCOMPLETE", lines_by_code(calculate_pay("hourly", Decimal("100"), month, unpaid)))

    def test_daily_and_hourly_staff_can_be_paid_for_days_off(self):
        daily = calculate_pay("daily", Decimal("1000"), self.month(), rules(daily_paid_days_off=True))
        self.assertEqual(lines_by_code(daily)["PAID_OFF"], Decimal("5000.00"))
        hourly = calculate_pay("hourly", Decimal("100"), self.month(), rules(hourly_paid_days_off=True))
        self.assertEqual(lines_by_code(hourly)["PAID_OFF"], Decimal("4000.00"))  # 5 x 8h
        self.assertNotIn("PAID_OFF", lines_by_code(calculate_pay("daily", Decimal("1000"), self.month())))

    def test_net_salary_is_rounded_with_a_visible_line(self):
        month = [day(S.PRESENT)] * 20 + [day(S.ABSENT, "0", 0)]
        # 30001 - 1000.03 = 29000.97
        up = calculate_pay("monthly", Decimal("30001"), month, rules(money_rounding_increment=Decimal("10")))
        self.assertEqual(up["net"], Decimal("29000.00"))
        self.assertEqual(lines_by_code(up)["ROUNDING"], Decimal("0.97"))
        self.assertEqual(up["gross"] - up["deductions"], up["net"])
        ceiling = calculate_pay(
            "monthly", Decimal("30001"), month,
            rules(money_rounding_increment=Decimal("1"), money_rounding_mode="up"),
        )
        self.assertEqual(ceiling["net"], Decimal("29001.00"))

    def test_salary_below_zero_only_when_allowed(self):
        month = [day(S.ABSENT, "0", 0)] * 20 + [day(S.WEEKLY_OFF, "0", 0)] * 11
        self.assertEqual(calculate_pay("monthly", Decimal("3000"), month)["net"], Decimal("0.00"))
        allowed = calculate_pay("monthly", Decimal("3000"), month, rules(allow_negative_net_pay=True))
        self.assertEqual(allowed["net"], Decimal("-100.00"))


class SalarySettingsBase(TestCase):
    def setUp(self):
        self.company = onboard_company(code="ACME", slug="acme", name="Acme Ltd")
        self.admin = User.objects.create_user(email="admin@acme.test", password="pw")
        self.hr = User.objects.create_user(email="hr@acme.test", password="pw")
        for user, role in ((self.admin, "company_admin"), (self.hr, "hr")):
            CompanyMembership.all_objects.create(
                company=self.company, user=user, role=role,
                status=CompanyMembership.Status.ACTIVE,
            )

    def change(self, month, actor=None, **values):
        return change_salary_rules(
            actor=actor or self.admin, company_id=self.company.pk,
            values={"effective_from": month, **values},
        )

    def versions(self):
        with use_company(self.company):
            return list(PayrollPolicyVersion.objects.order_by("version_number"))


class SalaryRulesServiceTests(SalarySettingsBase):
    def test_a_company_starts_on_the_standard_rules(self):
        self.assertIsNone(rules_for(self.company.pk, datetime.date(2026, 9, 1)).version)

    def test_saving_rules_makes_version_one_from_the_month(self):
        version = self.change(datetime.date(2026, 9, 1), monthly_divisor=Decimal("26"))
        self.assertEqual((version.version_number, version.status), (1, "active"))
        self.assertEqual(rules_for(self.company.pk, datetime.date(2026, 9, 1)).divisor, Decimal("26"))
        self.assertIsNone(rules_for(self.company.pk, datetime.date(2026, 8, 1)).version)
        with use_company(self.company):
            settings = PayrollSettings.objects.get()
        self.assertEqual(settings.default_policy_version, version)
        self.assertEqual(settings.monthly_divisor, Decimal("26"))
        self.assertTrue(AuditLog.objects.filter(action="payroll.rules_changed").exists())

    def test_a_later_change_closes_the_earlier_version(self):
        first = self.change(datetime.date(2026, 9, 1))
        second = self.change(datetime.date(2026, 11, 1), monthly_divisor=Decimal("26"))
        first.refresh_from_db()
        self.assertEqual(first.effective_to, datetime.date(2026, 11, 1))
        self.assertEqual(rules_for(self.company.pk, datetime.date(2026, 10, 1)).version, first)
        self.assertEqual(rules_for(self.company.pk, datetime.date(2026, 11, 1)).version, second)

    def test_saving_twice_for_one_month_replaces_the_first(self):
        first = self.change(datetime.date(2026, 9, 1))
        second = self.change(datetime.date(2026, 9, 1), monthly_divisor=Decimal("26"))
        first.refresh_from_db()
        self.assertEqual(first.status, "retired")
        self.assertEqual(second.version_number, 2)
        self.assertEqual(rules_for(self.company.pk, datetime.date(2026, 9, 1)).version, second)

    def test_a_change_before_a_saved_later_change_is_refused(self):
        self.change(datetime.date(2026, 11, 1))
        with self.assertRaises(ValidationError) as caught:
            self.change(datetime.date(2026, 9, 1))
        self.assertIn("November 2026", str(caught.exception))

    def test_rules_start_on_the_first_of_a_month(self):
        with self.assertRaises(ValidationError):
            self.change(datetime.date(2026, 9, 15))

    def test_bad_values_are_refused_by_the_model(self):
        with self.assertRaises(ValidationError):
            self.change(datetime.date(2026, 9, 1), monthly_divisor=Decimal("45"))
        with self.assertRaises(ValidationError):
            self.change(datetime.date(2026, 9, 1), half_day_pay_percent=Decimal("120"))
        self.assertEqual(self.versions(), [])

    def test_hr_cannot_change_salary_rules(self):
        with self.assertRaises(PermissionDenied):
            self.change(datetime.date(2026, 9, 1), actor=self.hr)

    def test_general_settings_are_saved_without_a_version(self):
        update_general_settings(
            actor=self.admin, company_id=self.company.pk,
            values={"currency": "usd", "default_pay_day": 5},
        )
        with use_company(self.company):
            settings = PayrollSettings.objects.get()
        self.assertEqual((settings.currency, settings.default_pay_day), ("USD", 5))
        self.assertEqual(self.versions(), [])


class SalarySettingsPageTests(SalarySettingsBase):
    URL = "payroll:salary_settings"

    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def test_the_page_shows_the_standard_rules(self):
        response = self.client.get(reverse(self.URL))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Standard rules")
        self.assertContains(response, "Monthly salary ÷ 30 days")
        self.assertNotContains(response, 'type="date"')

    def test_saving_rules_through_the_page(self):
        response = self.client.post(reverse(self.URL), {
            "section": "rules", "applies_month": "10", "applies_year": "2026",
            "monthly_proration_method": "fixed_30", "monthly_divisor": "26",
            "absence_deduction_method": "day_fraction", "half_day_pay_percent": "50",
            "incomplete_day_treatment": "pay_full", "money_rounding_increment": "1",
            "money_rounding_mode": "half_up", "daily_paid_days_off": "on",
            **OVERTIME_POST,
        })
        self.assertRedirects(response, reverse(self.URL))
        [version] = self.versions()
        self.assertEqual(version.effective_from, datetime.date(2026, 10, 1))
        self.assertEqual(version.monthly_divisor, Decimal("26"))
        self.assertEqual(version.money_rounding_increment, Decimal("1"))
        self.assertTrue(version.config("daily_paid_days_off"))
        self.assertContains(self.client.get(reverse(self.URL)), "October 2026")

    def test_a_refused_change_shows_on_the_month(self):
        self.change(datetime.date(2026, 11, 1))
        response = self.client.post(reverse(self.URL), {
            "section": "rules", "applies_month": "9", "applies_year": "2026",
            "monthly_proration_method": "fixed_30", "monthly_divisor": "30",
            "absence_deduction_method": "day_fraction", "half_day_pay_percent": "50",
            "incomplete_day_treatment": "pay_full", "money_rounding_increment": "0.01",
            "money_rounding_mode": "half_up", **OVERTIME_POST,
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "are already saved")

    def test_the_form_starts_from_a_later_change_already_saved(self):
        self.change(datetime.date(2030, 3, 1), monthly_divisor=Decimal("26"))
        response = self.client.get(reverse(self.URL))
        initial = response.context["rules_form"].initial
        self.assertEqual((initial["applies_month"], initial["applies_year"]), (3, 2030))
        self.assertIn((2030, 2030), response.context["rules_form"].fields["applies_year"].choices)
        self.assertEqual(initial["monthly_divisor"], "26")
        self.assertContains(response, "Upcoming")

    def test_saving_general_settings_through_the_page(self):
        response = self.client.post(reverse(self.URL), {
            "section": "general", "currency": "bdt", "default_pay_day": "7",
        })
        self.assertRedirects(response, reverse(self.URL))
        with use_company(self.company):
            self.assertEqual(PayrollSettings.objects.get().default_pay_day, 7)

    def test_hr_cannot_open_salary_settings(self):
        self.client.force_login(self.hr)
        self.assertEqual(self.client.get(reverse(self.URL)).status_code, 403)
