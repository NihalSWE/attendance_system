"""Attendance -> Calendar -> Edit this month (2026-10-26).

Monday 10 Aug 2026: Rahim in at 9:40 (30 minutes late), out at 18:00.
Tuesday 11 Aug: no scans, absent.
"""

import datetime

from django.urls import reverse

from attendance.models import AttendanceCorrection, AttendanceRecord
from attendance.tests_corrections import CorrectionTestCase
from common.tenant import use_company

MONDAY = datetime.date(2026, 8, 10)
TUESDAY = datetime.date(2026, 8, 11)


class MonthEditTests(CorrectionTestCase):
    def setUp(self):
        super().setUp()
        self.punch(MONDAY, 9, 40)
        self.punch(MONDAY, 18, 0)
        self.day(MONDAY)
        self.day(TUESDAY)
        self.client.force_login(self.admin)
        self.url = reverse("attendance:attendance_month_edit", args=[self.employee.pk, 2026, 8])

    def record(self, on):
        with use_company(self.company):
            return AttendanceRecord.objects.get(employee=self.employee, work_date=on)

    def test_the_month_opens_with_its_totals_and_the_calendar_links_to_it(self):
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        totals = page.context["totals"]
        self.assertGreaterEqual(totals["present"], 1)
        self.assertGreaterEqual(totals["absent"], 1)
        self.assertEqual(totals["late"], 1)
        self.assertContains(page, f'name="status_{TUESDAY.isoformat()}"')
        self.assertContains(page, f'name="excuse_{MONDAY.isoformat()}"')
        calendar = self.client.get(reverse("attendance:attendance_calendar"),
                                   {"employee": self.employee.pk, "year": 2026, "month": 8})
        self.assertContains(calendar, self.url)

    def test_saving_several_days_with_one_reason(self):
        response = self.client.post(self.url, {
            f"status_{TUESDAY.isoformat()}": "present",
            f"status_{MONDAY.isoformat()}": "present",
            f"excuse_{MONDAY.isoformat()}": "on",
            "reason": "Checked with the register"})
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertEqual(self.record(TUESDAY).attendance_status, "present")
        self.assertEqual(self.record(MONDAY).late_minutes, 0)
        with use_company(self.company):
            reasons = set(AttendanceCorrection.objects.filter(employee=self.employee)
                          .values_list("reason", flat=True))
        self.assertEqual(reasons, {"Checked with the register"})
        self.assertEqual(self.client.get(self.url).context["totals"]["late"], 0)

    def test_no_reason_saves_nothing(self):
        page = self.client.post(self.url, {f"status_{TUESDAY.isoformat()}": "half_day", "reason": ""})
        self.assertContains(page, "Say why: it is kept with every change.")
        self.assertEqual(self.record(TUESDAY).attendance_status, "absent")

    def test_a_finalised_month_cannot_be_changed(self):
        self.post_a_run()
        page = self.client.get(self.url)
        self.assertNotContains(page, f'name="status_{TUESDAY.isoformat()}"')
        self.assertContains(page, "Salary finalised.")
