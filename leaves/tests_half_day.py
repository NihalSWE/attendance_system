"""Half-day leave through attendance and salary (kept simple on Ajay's request).

Came in on a half-day leave: the day counts in full, no late mark; an unpaid
half is still deducted. Did not come in: only the leave half counts.
"""

import datetime
from decimal import Decimal

from attendance.models import AttendanceRecord
from attendance.services import recalculate
from common.tenant import use_company
from leaves.services import create_leave_type, record_leave
from payroll.services import calculate_pay, summarise
from payroll.tests_overtime import LATER, OvertimeBase


class HalfDayLeaveTests(OvertimeBase):
    def setUp(self):
        super().setUp()
        self.casual = create_leave_type(actor=self.admin, company_id=self.company.pk,
                                        values={"code": "CAS", "name": "Casual"})

    def half_day(self, on, pay, *times):
        record_leave(actor=self.admin, company_id=self.company.pk, values={
            "employee": self.employee, "leave_type": self.casual, "start_date": on,
            "end_date": on, "duration": "half_day", "pay_type": pay, "reason": ""})
        if times:
            record = self.work(on, *times)
        else:
            recalculate(self.company.pk, start=on, end=on, now=LATER)
            record = self.record(on)
        with use_company(self.company):
            return AttendanceRecord.objects.select_related("shift", "leave_day").get(pk=record.pk)

    def test_paid_half_day_and_came_in_is_a_full_day_with_no_deduction(self):
        record = self.half_day(datetime.date(2026, 8, 10), "paid", (13, 30), (18, 0))
        self.assertEqual((record.attendance_status, record.payable_fraction, record.late_minutes),
                         ("present", Decimal("1"), 0))
        self.assertEqual(calculate_pay("monthly", "30000", [record])["net"], Decimal("30000.00"))
        self.assertEqual(summarise([record])["paid_leave_minutes"], record.leave_day.leave_minutes)

    def test_unpaid_half_day_and_came_in_deducts_half_a_day(self):
        record = self.half_day(datetime.date(2026, 8, 11), "unpaid", (13, 30), (18, 0))
        self.assertEqual((record.attendance_status, record.payable_fraction), ("present", Decimal("0.5")))
        self.assertEqual(calculate_pay("monthly", "30000", [record])["net"], Decimal("29500.00"))
        self.assertEqual(calculate_pay("daily", "1000", [record])["net"], Decimal("500.00"))

    def test_half_day_without_coming_in_counts_only_the_leave_half(self):
        paid = self.half_day(datetime.date(2026, 8, 12), "paid")
        unpaid = self.half_day(datetime.date(2026, 8, 13), "unpaid")
        self.assertEqual((paid.attendance_status, paid.payable_fraction), ("leave", Decimal("0.5")))
        self.assertEqual(unpaid.payable_fraction, Decimal("0"))
        self.assertEqual(calculate_pay("monthly", "30000", [paid])["net"], Decimal("29500.00"))
        self.assertEqual(calculate_pay("monthly", "30000", [unpaid])["net"], Decimal("29000.00"))
