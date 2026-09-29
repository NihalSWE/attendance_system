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


class InactiveDaysTests(ProrationTests):
    """No salary for inactive days (Nihal, 2026-09-29): a monthly salary loses
    each inactive calendar day, a fixed allowance too, and the days are not
    counted absent on top."""

    def inactive(self, start, end):
        from employees.models import EmployeeInactivePeriod

        with use_company(self.company):
            period = EmployeeInactivePeriod(employee=self.employee, start_date=start,
                                            end_date=end, reason="Suspended pending inquiry")
            period.company_id = self.company.pk
            period.save()

    def test_three_inactive_days_are_not_paid(self):
        from payroll import component_services

        component = component_services.create_component(
            actor=self.admin, company_id=self.company.pk,
            values={"code": "HR", "name": "House rent", "kind": "earning", "method": "fixed",
                    "default_amount": Decimal("3100"), "default_percent": None,
                    "description": ""})
        component_services.give_component(
            actor=self.admin, company_id=self.company.pk, employee_id=self.employee.pk,
            values={"component": component, "amount": Decimal("3100"), "percent": None,
                    "effective_from": datetime.date(2026, 8, 1), "reason": ""})
        self.work(datetime.date(2026, 8, 5), (9, 0), (18, 0))
        self.work(datetime.date(2026, 8, 11), (9, 0), (18, 0))
        self.inactive(datetime.date(2026, 8, 10), datetime.date(2026, 8, 12))
        record, basic = self.basic()
        from attendance.models import AttendanceRecord

        with use_company(self.company):
            lines = {line.code: line for line in record.lines.all()}
            statuses = set(AttendanceRecord.objects.filter(
                employee=self.employee, work_date__range=("2026-08-10", "2026-08-12"))
                .values_list("attendance_status", flat=True))
        self.assertEqual(statuses, {"inactive"})
        self.assertEqual(basic.amount, Decimal("30000.00"))
        self.assertEqual(lines["INACTIVE"].amount, money(Decimal("30000") * 3 / 31))
        self.assertIn("Inactive days (3 of 31 days)", lines["INACTIVE"].description)
        self.assertEqual(lines["HR"].amount, money(Decimal("3100") * 28 / 31))
        # Inactive days are not counted absent on top.
        self.assertEqual(record.calculation_snapshot["counts"].get("inactive"), 3)
