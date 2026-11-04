"""Attendance bonus (2026-11-04, payroll/attendance_bonus.py).

Monthly staff: August 2026, Rahim on 30,000 a month. Weekly workers: weeks run
Saturday to Friday; 1 Aug 2026 is a Saturday, and the weeks ending in August
are 1-7, 8-14, 15-21 and 22-28 (payroll/tests_weekly.py).
"""

import datetime
from decimal import Decimal
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from django.urls import reverse

from attendance.models import AttendanceRecord
from common.tenant import use_company
from payroll import attendance_bonus, overrides, paygroups
from payroll.attendance_bonus import Bonus
from payroll.policy import change_salary_rules
from payroll.services import period_records
from payroll.tests_weekly import WeeklyCase

D = datetime.date
Status = AttendanceRecord.AttendanceStatus
AUGUST = D(2026, 8, 1)


def days(*statuses):
    return [SimpleNamespace(attendance_status=status) for status in statuses]


class BonusRuleTests(SimpleTestCase):
    """The rule on its own: who gets it, and how much."""

    def test_every_working_day(self):
        bonus = Bonus(kind="monthly", min_days=None, percent=Decimal("10"))
        full = days(Status.PRESENT, Status.PRESENT, Status.INCOMPLETE, Status.WEEKLY_OFF, Status.HOLIDAY)
        line, detail = bonus.line(Decimal("30000"), full)
        self.assertEqual(line[1:2] + line[-1:], ("ATTENDANCE_BONUS", Decimal("3000.00")))
        self.assertEqual((detail["present"], detail["working"], detail["source"]), (3, 3, "company"))
        # A half day, any leave or an absence breaks it; days off are not working days.
        for status in (Status.HALF_DAY, Status.LEAVE, Status.ABSENT):
            line, detail = bonus.line(Decimal("30000"), full + days(status))
            self.assertIsNone(line)
            self.assertEqual(detail["why_not"], "present 3 of 4 working days")

    def test_at_least_some_days(self):
        bonus = Bonus(kind="monthly", min_days=3, amount=Decimal("500"), source="department")
        self.assertEqual(bonus.line(Decimal("0"), days(Status.PRESENT, Status.PRESENT, Status.PRESENT,
                                                       Status.ABSENT))[0][-1], Decimal("500.00"))
        self.assertIsNone(bonus.line(Decimal("0"), days(Status.PRESENT, Status.PRESENT, Status.ABSENT,
                                                        Status.ABSENT))[0])
        # A short period with fewer working days than asked needs all of them.
        self.assertEqual(bonus.line(Decimal("0"), days(Status.PRESENT, Status.PRESENT))[1]["needed"], 2)

    def test_blocked_whatever_the_days(self):
        bonus = Bonus(kind="monthly", min_days=None, percent=Decimal("10"), blocked="joined on 05 Aug 2026")
        line, detail = bonus.line(Decimal("30000"), days(Status.PRESENT))
        self.assertIsNone(line)
        self.assertEqual(detail["why_not"], "joined on 05 Aug 2026")

    def test_who_wins(self):
        rules = SimpleNamespace(bonus_percent=Decimal("10"))
        employee = SimpleNamespace(employee_id=1, department_id=None, percent=None, amount=Decimal("700"))
        department = SimpleNamespace(employee_id=None, department_id=9, percent=Decimal("5"), amount=None)
        self.assertEqual(attendance_bonus.pick(rules, ({1: employee}, {9: department}), 1, 9),
                         ("employee", None, Decimal("700")))
        self.assertEqual(attendance_bonus.pick(rules, ({}, {9: department}), 1, 9), ("department", Decimal("5"), None))
        self.assertEqual(attendance_bonus.pick(rules, ({}, {}), 1, 9), ("company", Decimal("10"), None))


class BonusCase(WeeklyCase):
    def rules(self, **bonus):
        change_salary_rules(actor=self.admin, company_id=self.company.pk,
                            values={"effective_from": AUGUST, **bonus})

    def everyday(self, first, last, pay_group=None, missing=0):
        """Present by hand on every working day of the period (less ``missing``)."""
        with use_company(self.company):
            from attendance.services import calculate_attendance

            calculate_attendance(actor=self.admin, company_id=self.company.pk, year=last.year,
                                 month=last.month, first=first, last=last)
        records, _ = period_records(self.company.pk, first, last, pay_group, employee_ids=[self.employee.pk])
        _present, working = attendance_bonus.count(records.get(self.employee, []))
        overrides.set_override(actor=self.admin, company_id=self.company.pk, employee_id=self.employee.pk,
                               first=first, last=last, pay_group=pay_group, present_days=working - missing,
                               reason="test")
        return working

    def line_of(self, run, code="ATTENDANCE_BONUS"):
        record = self.pay_record(run)
        with use_company(self.company):
            return record, record.lines.filter(code=code).first()


class MonthlyStaffTests(BonusCase):
    def test_off_changes_nothing_and_on_adds_one_line(self):
        self.everyday(AUGUST, D(2026, 8, 31))
        before = self.pay_record(self.month())
        self.assertIsNone(self.line_of(self.month())[1])
        self.rules(bonus_monthly=True, bonus_percent=Decimal("10"))
        record, line = self.line_of(self.month())
        self.assertEqual(line.amount, Decimal("3000.00"))            # 10 % of 30,000
        self.assertEqual(record.gross_earnings, before.gross_earnings + Decimal("3000.00"))
        self.assertEqual(record.total_deductions, before.total_deductions)
        self.assertTrue(record.calculation_snapshot["attendance_bonus"]["paid"])

    def test_one_day_missing(self):
        self.everyday(AUGUST, D(2026, 8, 31), missing=1)
        self.rules(bonus_monthly=True, bonus_percent=Decimal("10"))
        record, line = self.line_of(self.month())
        self.assertIsNone(line)
        self.assertFalse(record.calculation_snapshot["attendance_bonus"]["paid"])
        # At least 20 days: one missing day is fine.
        self.rules(bonus_monthly=True, bonus_monthly_days=20, bonus_percent=Decimal("10"))
        self.assertEqual(self.line_of(self.month())[1].amount, Decimal("3000.00"))

    def test_department_then_employee_win(self):
        self.everyday(AUGUST, D(2026, 8, 31))
        self.rules(bonus_monthly=True, bonus_percent=Decimal("10"))
        with use_company(self.company):
            department = self.employee.assignments.first().department
        attendance_bonus.set_department_rate(actor=self.admin, company_id=self.company.pk,
                                             department=department, percent="5")
        self.assertEqual(self.line_of(self.month())[1].amount, Decimal("1500.00"))
        attendance_bonus.set_employee_rate(actor=self.admin, company_id=self.company.pk,
                                           employee_id=self.employee.pk, amount="2000")
        self.assertEqual(self.line_of(self.month())[1].amount, Decimal("2000.00"))
        # Both typed: the percent wins.
        row = attendance_bonus.set_employee_rate(actor=self.admin, company_id=self.company.pk,
                                                 employee_id=self.employee.pk, percent="1", amount="2000")
        self.assertEqual((row.percent, row.amount), (Decimal("1"), None))
        self.assertEqual(self.line_of(self.month())[1].amount, Decimal("300.00"))
        # Emptied: back to the department's.
        attendance_bonus.set_employee_rate(actor=self.admin, company_id=self.company.pk,
                                           employee_id=self.employee.pk)
        self.assertEqual(self.line_of(self.month())[1].amount, Decimal("1500.00"))

    def test_joined_in_the_month_gets_none(self):
        with use_company(self.company):
            type(self.employee).objects.filter(pk=self.employee.pk).update(joining_date=D(2026, 8, 5))
        self.employee.refresh_from_db()
        self.everyday(D(2026, 8, 5), D(2026, 8, 31))
        self.rules(bonus_monthly=True, bonus_percent=Decimal("10"))
        record, line = self.line_of(self.month())
        self.assertIsNone(line)
        self.assertEqual(record.calculation_snapshot["attendance_bonus"]["why_not"], "joined on 05 Aug 2026")


class SettingsTests(BonusCase):
    def test_weekly_workers_one_or_the_other(self):
        with self.assertRaises(ValidationError) as caught:
            self.rules(bonus_weekly=True, bonus_weekly_by_month=True, bonus_percent=Decimal("10"))
        self.assertIn("bonus_weekly_by_month", caught.exception.message_dict)

    def test_the_percent_is_needed_when_on(self):
        with self.assertRaises(ValidationError) as caught:
            self.rules(bonus_monthly=True)
        self.assertIn("bonus_percent", caught.exception.message_dict)

    def test_the_page(self):
        self.client.force_login(self.admin)
        url = reverse("payroll:salary_settings")
        page = self.client.get(url)
        self.assertContains(page, "Attendance bonus by department")
        self.assertContains(page, 'data-only-one="bonus_weekly_by_month"')
        post = {"section": "rules", "applies_month": "8", "applies_year": "2026",
                "monthly_proration_method": "fixed_30", "monthly_divisor": "30",
                "absence_deduction_method": "day_fraction", "half_day_pay_percent": "50",
                "incomplete_day_treatment": "pay_full", "money_rounding_increment": "0.01",
                "money_rounding_mode": "half_up", "overtime_multiplier": "2",
                "holiday_overtime_multiplier": "2", "minimum_overtime_minutes": "0",
                "overtime_rounding_minutes": "0", "bonus_weekly": "on", "bonus_weekly_by_month": "on",
                "bonus_monthly_rule": "all", "bonus_weekly_rule": "all", "bonus_percent": "10"}
        refused = self.client.post(url, post)
        self.assertContains(refused, "not both")
        post.pop("bonus_weekly_by_month")
        post.update(bonus_weekly_rule="min", bonus_weekly_days="5")
        self.assertRedirects(self.client.post(url, post), url)
        from payroll.policy import rules_for

        rules = rules_for(self.company.pk, AUGUST)
        self.assertEqual((rules.bonus_weekly, rules.bonus_weekly_days, rules.bonus_percent),
                         (True, 5, Decimal("10")))
        with use_company(self.company):
            department = self.employee.assignments.first().department
        self.client.post(url, {"section": "bonus_department", "department": department.pk, "bonus_amount": "800"})
        self.assertEqual(attendance_bonus.rates(self.company.pk)[1][department.pk].amount, Decimal("800"))
        page = self.client.get(reverse("organization:employee_edit", args=[self.employee.pk]))
        self.assertContains(page, "the department&#x27;s: 800.00 flat")
        self.client.post(reverse("organization:employee_edit", args=[self.employee.pk]),
                         {"section": "attendance_bonus", "bonus_percent": "7"})
        self.assertEqual(attendance_bonus.rates(self.company.pk)[0][self.employee.pk].percent, Decimal("7"))


class WeeklyWorkerTests(BonusCase):
    def setUp(self):
        super().setUp()
        self.daily("1000")
        self.join(AUGUST)

    def test_a_week_of_full_attendance(self):
        working = self.everyday(D(2026, 8, 8), D(2026, 8, 14), pay_group=self.group)
        self.rules(bonus_weekly=True, bonus_percent=Decimal("10"))
        record, line = self.line_of(self.week(D(2026, 8, 10)))
        self.assertEqual(line.amount, Decimal(working * 1000) / 10)   # 10 % of the week's days
        self.assertTrue(line.description.startswith("Weekly attendance bonus"))

    def test_once_a_month_in_the_last_week_only(self):
        working = sum(self.everyday(first, last, pay_group=self.group)
                      for first, last in paygroups.weeks_in_month(self.group, 2026, 8))
        self.rules(bonus_weekly_by_month=True, bonus_percent=Decimal("10"))
        self.assertIsNone(self.line_of(self.week(D(2026, 8, 10)))[1])
        record, line = self.line_of(self.week(D(2026, 8, 25)))
        self.assertTrue(line.description.startswith("Monthly attendance bonus"))
        self.assertEqual(record.calculation_snapshot["attendance_bonus"]["kind"], "weekly_by_month")
        # 10 % of the four weeks' pay (every working day at 1,000), paid once, in the last week.
        self.assertEqual(line.amount, Decimal(working * 1000) / 10)
        self.assertEqual(record.calculation_snapshot["attendance_bonus"]["working"], working)
        # A week by hand one day short: no monthly bonus.
        self.everyday(D(2026, 8, 8), D(2026, 8, 14), pay_group=self.group, missing=1)
        self.assertIsNone(self.line_of(self.week(D(2026, 8, 25)))[1])
