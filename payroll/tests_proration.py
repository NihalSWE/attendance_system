"""A11 part 3: joining or leaving inside the month (monthly salary, calendar days)."""

import datetime
from decimal import Decimal

from common.tenant import use_company
from payroll.services import generate_payroll, money
from payroll.tests_overtime import OvertimeBase


class ProrationTests(OvertimeBase):
    def basic(self):
        run = generate_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        with use_company(self.company):
            record = run.records.get(employee=self.employee)
            return record, record.lines.get(code="BASIC")

    def set_dates(self, **dates):
        with use_company(self.company):
            for field, value in dates.items():
                setattr(self.employee, field, value)
            self.employee.save(update_fields=list(dates))

    def test_joining_mid_month_pays_the_employed_days_only(self):
        self.work(datetime.date(2026, 8, 17), (9, 0), (18, 0))
        self.set_dates(joining_date=datetime.date(2026, 8, 16))
        record, basic = self.basic()
        self.assertEqual(basic.amount, money(Decimal("30000") * 16 / 31))
        self.assertIn("16 of 31 days employed", basic.description)
        self.assertEqual(record.calculation_snapshot["employed_days"], 16)

    def test_a_full_month_is_unchanged_and_leaving_mid_month_is_prorated(self):
        self.work(datetime.date(2026, 8, 5), (9, 0), (18, 0))
        _, basic = self.basic()
        self.assertEqual((basic.amount, basic.description), (Decimal("30000.00"), "Basic salary"))
        self.set_dates(leaving_date=datetime.date(2026, 8, 10))
        _, basic = self.basic()
        self.assertEqual(basic.amount, money(Decimal("30000") * 10 / 31))
