"""A11 part 4: a monthly salary that changes inside the month."""

import datetime
from decimal import Decimal

from django.utils import timezone

from common.tenant import use_company
from employees.services import revise_compensation
from payroll.services import generate_payroll, money
from payroll.tests_overtime import OvertimeBase


class SalaryChangeTests(OvertimeBase):
    def raise_from(self, day, rate):
        revise_compensation(
            employee=self.employee, actor=self.admin, base_rate=Decimal(rate),
            effective_at=timezone.make_aware(datetime.datetime.combine(day, datetime.time.min)),
        )

    def basics(self):
        run = generate_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        with use_company(self.company):
            record = run.records.get(employee=self.employee)
            return record, list(record.lines.filter(code="BASIC").order_by("sequence"))

    def test_raise_mid_month_pays_each_rate_for_its_days(self):
        self.work(datetime.date(2026, 8, 5), (9, 0), (18, 0))
        self.raise_from(datetime.date(2026, 8, 16), "31000")
        record, basics = self.basics()
        self.assertEqual([line.amount for line in basics],
                         [money(Decimal("30000") * 15 / 31), money(Decimal("31000") * 16 / 31)])
        self.assertIn("01 Aug–15 Aug, 15 of 31 days", basics[0].description)
        self.assertIn("16 Aug–31 Aug, 16 of 31 days", basics[1].description)
        self.assertEqual(len(record.calculation_snapshot["basic_segments"]), 2)

    def test_raise_and_joining_in_the_same_month(self):
        self.work(datetime.date(2026, 8, 11), (9, 0), (18, 0))
        with use_company(self.company):
            self.employee.joining_date = datetime.date(2026, 8, 10)
            self.employee.save(update_fields=["joining_date"])
        self.raise_from(datetime.date(2026, 8, 16), "31000")
        _, basics = self.basics()
        self.assertEqual([line.amount for line in basics],
                         [money(Decimal("30000") * 6 / 31), money(Decimal("31000") * 16 / 31)])

    def test_no_change_keeps_a_single_basic_line(self):
        self.work(datetime.date(2026, 8, 5), (9, 0), (18, 0))
        record, basics = self.basics()
        self.assertEqual([(line.description, line.amount) for line in basics],
                         [("Basic salary", Decimal("30000.00"))])
        self.assertEqual(record.calculation_snapshot["basic_segments"], [])
