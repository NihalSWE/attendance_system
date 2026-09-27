"""Leave day shapes (Phase E1, 2026-09-27; docs/LEAVE_FULL_DESIGN.md §1):
morning / afternoon halves, hours, and part-paid leave - and every half day
recorded before them still read exactly as it was.

Rahim works Day, 09:00-18:00, at Head Office. Monday 10 August 2026.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.urls import reverse

from attendance.services import recalculate
from common.tenant import use_company
from leaves import services, workflow
from leaves.models import LeaveDay, LeaveRequest, LeaveRequestSegment
from leaves.tests_branch_access import MONDAY, TwoBranchCase
from payroll.services import summarise

T = datetime.time


class ShapeCase(TwoBranchCase):
    def record(self, employee=None, **values):
        return services.record_leave(actor=self.admin, company_id=self.company.pk, values={
            "employee": employee or self.employee, "leave_type": self.leave_type,
            "start_date": MONDAY, "end_date": MONDAY, "pay_type": "paid", "reason": "",
            **values})

    def day(self, employee=None):
        with use_company(self.company):
            return LeaveDay.objects.get(employee=employee or self.employee, work_date=MONDAY)

    def came(self, start, end):
        self.punch(MONDAY, *start)
        self.punch(MONDAY, *end)

    def worked_out(self):
        recalculate(self.company.pk, employee_ids=[self.employee.pk], start=MONDAY, end=MONDAY)
        return self.record_day()

    def record_day(self):
        return self.record_(MONDAY)

    def record_(self, day):
        from attendance.models import AttendanceRecord

        with use_company(self.company):
            return AttendanceRecord.objects.get(employee=self.employee, work_date=day)


class HalfDayPartTests(ShapeCase):
    def test_a_morning_half_covers_the_first_half_and_excuses_arriving_at_midday(self):
        self.came((13, 35), (18, 0))
        self.record(duration="half_day", half_day_part="morning")
        leave = self.day()
        self.assertEqual(leave.balance_units, Decimal("0.5"))
        self.assertEqual(leave.leave_minutes, self.shift.scheduled_minutes // 2)
        self.assertEqual(leave.covered_end_at - leave.covered_start_at,
                         datetime.timedelta(hours=4, minutes=30))
        day = self.worked_out()
        self.assertEqual(day.attendance_status, "present")
        self.assertEqual(day.late_minutes, 0)
        self.assertEqual(day.payable_fraction, Decimal("1"))
        self.assertIn("Morning half-day", day.note)

    def test_a_morning_half_still_counts_a_late_arrival_after_it(self):
        self.came((15, 0), (18, 0))
        self.record(duration="half_day", half_day_part="morning")
        self.assertGreater(self.worked_out().late_minutes, 0)

    def test_an_afternoon_half_excuses_leaving_early_but_not_arriving_late(self):
        self.came((9, 40), (13, 30))
        self.record(duration="half_day", half_day_part="afternoon")
        day = self.worked_out()
        self.assertEqual(day.early_out_minutes, 0)
        self.assertGreater(day.late_minutes, 0)

    def test_a_half_day_with_no_part_reads_as_it_always_did(self):
        # Every half day before Phase E: both excused; paid 1 / 0.5, unpaid 0.5 / 0.
        self.came((9, 40), (13, 30))
        self.record(duration="half_day", pay_type="unpaid")
        day = self.worked_out()
        self.assertEqual((day.late_minutes, day.early_out_minutes), (0, 0))
        self.assertEqual(day.payable_fraction, Decimal("0.5"))
        self.assertEqual(self.day().approved_pay_percentage, Decimal("0"))

    def test_half_days_not_worked_pay_as_before(self):
        self.record(duration="half_day")
        self.assertEqual(self.worked_out().payable_fraction, Decimal("0.5"))
        far = self.record(employee=self.clerk, duration="half_day", pay_type="unpaid")
        self.assertTrue(far.pk)
        from attendance.models import AttendanceRecord

        recalculate(self.company.pk, employee_ids=[self.clerk.pk], start=MONDAY, end=MONDAY)
        with use_company(self.company):
            self.assertEqual(AttendanceRecord.objects.get(
                employee=self.clerk, work_date=MONDAY).payable_fraction, Decimal("0"))


class HourlyTests(ShapeCase):
    def test_hours_at_the_start_excuse_arriving_after_them(self):
        self.came((11, 5), (18, 0))
        self.record(duration="hourly", start_time=T(9), end_time=T(11))
        leave = self.day()
        self.assertEqual(leave.leave_minutes, 120)
        self.assertEqual(leave.balance_units, (Decimal(120) / Decimal(
            self.shift.scheduled_minutes)).quantize(Decimal("0.01")))
        with use_company(self.company):
            segment = LeaveRequestSegment.objects.get(leave_request__employee=self.employee)
        self.assertEqual((segment.duration_type, segment.start_time, segment.end_time),
                         ("hourly", T(9), T(11)))
        day = self.worked_out()
        self.assertEqual(day.attendance_status, "present")
        self.assertEqual(day.late_minutes, 0)
        self.assertEqual(day.payable_fraction, Decimal("1"))

    def test_unpaid_hours_take_their_share_of_the_day(self):
        self.came((9, 0), (16, 0))
        self.record(duration="hourly", start_time=T(16), end_time=T(18), pay_type="unpaid")
        day = self.worked_out()
        f = self.day().balance_units
        self.assertEqual(day.payable_fraction, Decimal("1") - f)
        self.assertEqual(day.early_out_minutes, 0)

    def test_hours_and_no_scans(self):
        self.record(duration="hourly", start_time=T(9), end_time=T(11))
        day = self.worked_out()
        self.assertEqual(day.attendance_status, "leave")
        self.assertEqual(day.payable_fraction, self.day().balance_units)

    def test_what_hours_cannot_be(self):
        for values, said in (
            ({"start_time": T(7), "end_time": T(9, 30)}, "inside the shift"),
            ({"start_time": T(9), "end_time": T(18)}, "whole shift"),
            ({"start_time": T(9), "end_time": T(9, 10)}, "at least 15 minutes"),
            ({"start_time": None, "end_time": None}, "Give the time"),
            ({"start_time": T(9), "end_time": T(11),
              "end_date": MONDAY + datetime.timedelta(days=1)}, "one date"),
        ):
            with self.subTest(values=values), self.assertRaisesMessage(ValidationError, said):
                self.record(duration="hourly", **values)
        self.assertFalse(LeaveRequest.all_objects.exists())

    def test_hours_count_against_the_allowance_by_their_share(self):
        self.leave_type.days_per_year = Decimal("1")
        self.leave_type.save(update_fields=["days_per_year"])
        self.record(duration="hourly", start_time=T(9), end_time=T(13))
        with use_company(self.company):
            left = services.allowance_left(self.employee, self.leave_type, 2026)
        self.assertEqual(left, Decimal("1") - self.day().balance_units)


class PartPaidTests(ShapeCase):
    def test_a_part_paid_day(self):
        self.record(pay_type="partial", pay_percentage=Decimal("50"))
        leave = self.day()
        self.assertEqual((leave.approved_pay_type, leave.approved_pay_percentage),
                         ("partial", Decimal("50.00")))
        day = self.worked_out()
        self.assertEqual(day.payable_fraction, Decimal("0.5"))
        with use_company(self.company):
            from attendance.models import AttendanceRecord

            counts = summarise(AttendanceRecord.objects.select_related("leave_day")
                               .filter(pk=day.pk))
        self.assertEqual(counts["paid_leave"], 1)
        self.assertEqual(counts["paid_leave_minutes"], self.shift.scheduled_minutes // 2)

    def test_a_part_paid_half_day_not_worked(self):
        self.record(duration="half_day", pay_type="partial", pay_percentage=Decimal("50"))
        self.assertEqual(self.worked_out().payable_fraction, Decimal("0.25"))

    def test_a_share_is_needed(self):
        for percent in (None, Decimal("0"), Decimal("100")):
            with self.subTest(percent=percent), self.assertRaisesMessage(ValidationError, "1 to 99"):
                self.record(pay_type="partial", pay_percentage=percent)

    def test_the_approver_decides_part_pay(self):
        request = workflow.submit_request(
            actor=self.clerk_user, company_id=self.company.pk,
            values={"leave_type": self.leave_type, "start_date": MONDAY, "end_date": MONDAY,
                    "pay_type": "paid", "reason": "Unwell", "duration": "half_day",
                    "half_day_part": "afternoon"})
        workflow.decide_request(actor=self.manager, company_id=self.company.pk,
                                request_id=request.pk, approve=True, pay_type="partial",
                                pay_percentage=Decimal("60"))
        leave = self.day(self.clerk)
        self.assertEqual((leave.approved_pay_type, leave.approved_pay_percentage,
                          leave.balance_units), ("partial", Decimal("60.00"), Decimal("0.5")))
        with use_company(self.company):
            self.assertEqual(leave.request_segment.half_day_part, "afternoon")


class PageTests(ShapeCase):
    def test_record_leave_page_takes_hours_and_part_pay(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("leaves:leave_record"), {
            "employee": self.employee.pk, "leave_type": self.leave_type.pk,
            "start_date": MONDAY.isoformat(), "end_date": MONDAY.isoformat(),
            "duration": "hourly", "start_time": "09:00", "end_time": "11:30",
            "half_day_part": "morning",                     # does not apply: dropped
            "pay_type": "partial", "pay_percentage": "40", "reason": "Dentist"})
        self.assertEqual(response.status_code, 302)
        leave = self.day()
        self.assertEqual((leave.leave_minutes, leave.approved_pay_percentage),
                         (150, Decimal("40.00")))
        page = self.client.get(reverse("leaves:leave_list"), {"month": "8", "year": "2026"})
        self.assertContains(page, "Part paid")

    def test_the_employee_asks_for_a_morning_and_the_approver_gives_part_pay(self):
        self.client.force_login(self.clerk_user)
        response = self.client.post(reverse("me:leave_request"), {
            "leave_type": self.leave_type.pk, "start_date": MONDAY.isoformat(),
            "end_date": MONDAY.isoformat(), "duration": "half_day", "half_day_part": "morning",
            "pay_type": "paid", "reason": "Doctor"})
        self.assertEqual(response.status_code, 302)
        request = LeaveRequest.all_objects.get(employee=self.clerk)
        self.client.force_login(self.manager)
        refused = self.client.post(reverse("me:leave_decide", args=[request.pk]), {
            "decision": "approve", "pay_type": "partial", "pay_percentage": "", "reason": ""})
        self.assertContains(refused, "1 to 99")
        self.client.post(reverse("me:leave_decide", args=[request.pk]), {
            "decision": "approve", "pay_type": "partial", "pay_percentage": "50", "reason": ""})
        leave = self.day(self.clerk)
        self.assertEqual(leave.approved_pay_percentage, Decimal("50.00"))
        with use_company(self.company):
            self.assertEqual(leave.request_segment.half_day_part, "morning")

    def test_the_forms_show_the_new_choices(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("leaves:leave_record"))
        for text in ("Some hours", "Which half", "Part paid", "Share of pay kept",
                     'data-show-when="duration:hourly"'):
            self.assertContains(page, text)
