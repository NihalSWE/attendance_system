"""Salary months from the settings: e.g. 26th to 25th (2026-10-24).

docs/payroll-periods/00-PLAN.md item 3. A salary month is named after the
month it ends in; each starts the day after the previous one ended, so a
change of start day makes one short changeover month and never a gap or an
overlap.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.urls import reverse

from common.tenant import use_company
from payroll import periods
from payroll.models import PayrollPeriod
from payroll.services import generate_payroll, money
from payroll.tests_overtime import OvertimeBase

D = datetime.date


class PeriodCase(OvertimeBase):
    def set_day(self, day, year=2026, month=9):
        return periods.set_start_day(actor=self.admin, company_id=self.company.pk,
                                     start_day=day, year=year, month=month)


class PeriodDateTests(PeriodCase):
    def test_no_setting_is_the_calendar_month(self):
        self.assertEqual(periods.bounds(self.company.pk, 2026, 2), (D(2026, 2, 1), D(2026, 2, 28)))
        self.assertEqual(periods.full_days(self.company.pk, 2026, 2), 28)

    def test_the_26th_with_a_changeover_month(self):
        self.set_day(26)
        cid = self.company.pk
        self.assertEqual(periods.bounds(cid, 2026, 8), (D(2026, 8, 1), D(2026, 8, 31)))
        self.assertEqual(periods.bounds(cid, 2026, 9), (D(2026, 9, 1), D(2026, 9, 25)))
        self.assertEqual(periods.bounds(cid, 2026, 10), (D(2026, 9, 26), D(2026, 10, 25)))
        self.assertEqual(periods.bounds(cid, 2027, 1), (D(2026, 12, 26), D(2027, 1, 25)))
        self.assertTrue(periods.is_changeover(cid, 2026, 9))
        self.assertFalse(periods.is_changeover(cid, 2026, 10))
        # Changeover: its calendar month's days; otherwise the days it covers.
        self.assertEqual((periods.full_days(cid, 2026, 9), periods.full_days(cid, 2026, 10)),
                         (30, 30))
        self.assertEqual(periods.month_of(cid, D(2026, 9, 26)), (2026, 10))
        self.assertEqual(periods.month_of(cid, D(2026, 9, 25)), (2026, 9))

    def test_every_day_is_in_exactly_one_salary_month(self):
        self.set_day(26)
        self.set_day(10, 2027, 3)
        cid, seen, day = self.company.pk, set(), D(2026, 6, 1)
        months = [(2026, m) for m in range(6, 13)] + [(2027, m) for m in range(1, 7)]
        for year, month in months:
            first, last = periods.bounds(cid, year, month)
            self.assertEqual(first, day, (year, month))
            while day <= last:
                self.assertNotIn(day, seen)
                seen.add(day)
                day += datetime.timedelta(days=1)

    def test_a_month_with_a_salary_cannot_move(self):
        generate_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        with self.assertRaises(ValidationError) as caught:
            self.set_day(26, 2026, 8)
        self.assertIn("August 2026 already has a salary", " ".join(caught.exception.messages))
        first, last = self.set_day(26)                     # September has none: fine
        self.assertEqual((first, last), (D(2026, 9, 1), D(2026, 9, 25)))

    def test_days_29_to_31_and_the_same_day_are_refused(self):
        for day in (0, 29, 31):
            with self.subTest(day=day), self.assertRaises(ValidationError):
                self.set_day(day)
        with self.assertRaises(ValidationError):
            self.set_day(1)


class PeriodPayTests(PeriodCase):
    def basic(self, year, month):
        run = generate_payroll(actor=self.admin, company_id=self.company.pk, year=year, month=month)
        with use_company(self.company):
            record = run.records.get(employee=self.employee)
            return run, record, record.lines.get(code="BASIC")

    def test_the_changeover_month_pays_its_days(self):
        self.set_day(26)
        self.work(D(2026, 9, 7), (9, 0), (18, 0))
        run, record, basic = self.basic(2026, 9)
        self.assertEqual((run.payroll_period.start_date, run.payroll_period.end_date),
                         (D(2026, 9, 1), D(2026, 9, 25)))
        self.assertEqual(run.payroll_period.name, "September 2026")
        self.assertEqual(basic.amount, money(Decimal("30000") * 25 / 30))
        self.assertIn("25 of 30 days", basic.description)

    def test_a_full_26th_to_25th_month_pays_the_whole_salary(self):
        self.set_day(26)
        self.work(D(2026, 9, 28), (9, 0), (18, 0))
        run, record, basic = self.basic(2026, 10)
        self.assertEqual((run.payroll_period.start_date, run.payroll_period.end_date),
                         (D(2026, 9, 26), D(2026, 10, 25)))
        self.assertEqual(run.payroll_period.name, "October 2026")
        self.assertEqual(basic.amount, Decimal("30000.00"))

    def test_generating_again_replaces_the_same_month(self):
        self.set_day(26)
        self.work(D(2026, 9, 28), (9, 0), (18, 0))
        self.basic(2026, 10)
        self.basic(2026, 10)
        with use_company(self.company):
            self.assertEqual(PayrollPeriod.objects.filter(end_date=D(2026, 10, 25)).count(), 1)


class PeriodPageTests(PeriodCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def test_saving_it_on_salary_settings(self):
        # One box (2026-10-26): it applies from the first month with no salary.
        url = reverse("payroll:salary_settings")
        page = self.client.get(url)
        self.assertContains(page, "Salary months are calendar months")
        response = self.client.post(url, {"section": "salary_month", "start_day": "26"})
        self.assertRedirects(response, f"{url}#salary-month", fetch_redirect_response=False)
        page = self.client.get(url)
        self.assertContains(page, "alert alert--success\">Saved. From ")
        self.assertContains(page, "Salary months run from the 26th to the 25th of the next month")
        self.assertContains(page, '<option value="26" selected>26th - from the 26th to the 25th')
        # Saving the same again changes nothing, and says so.
        self.client.post(url, {"section": "salary_month", "start_day": "26"})
        self.assertContains(self.client.get(url), "Nothing changed: salary months already start on day 26.")

    def test_a_wrong_day_is_said_at_the_top(self):
        url = reverse("payroll:salary_settings")
        self.client.post(url, {"section": "salary_month", "start_day": "31"})
        self.assertContains(self.client.get(url), "alert alert--danger\">Select a valid choice.")

    def test_the_salary_page_says_what_the_month_covers(self):
        self.set_day(26)
        page = self.client.get(reverse("payroll:payroll_home"), {"month": 10, "year": 2026})
        self.assertContains(page, "October 2026 covers 26 Sep 2026 - 25 Oct 2026")
