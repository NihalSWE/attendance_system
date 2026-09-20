"""Date and date-range filters on the Daily list, and the calendar date jump (N12)."""

import datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from attendance import tests_live
from attendance.models import AttendanceRecord
from attendance.services import recalculate
from common.tenant import use_company

UTC = datetime.timezone.utc


class DateFilterTests(TestCase):
    setUp_company = tests_live.LiveTestCase.setUp
    punch = tests_live.LiveTestCase.punch

    def setUp(self):
        self.setUp_company()
        recalculate(self.company.pk, start=datetime.date(2026, 8, 1),
                    end=datetime.date(2026, 8, 31))
        self.client.force_login(self.admin)
        self.url = reverse("attendance:attendance_list")

    def on_date(self, day):
        with use_company(self.company):
            return AttendanceRecord.objects.filter(work_date=day).count()

    def in_range(self, start, end):
        with use_company(self.company):
            return AttendanceRecord.objects.filter(
                work_date__gte=start, work_date__lte=end
            ).count()

    def draw(self, **params):
        response = self.client.get(self.url, {
            "table": "1", "draw": "2", "start": "0", "length": "10",
            "year": "2026", "month": "8", **params,
        })
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_a_single_date_shows_only_that_day(self):
        day = datetime.date(2026, 8, 10)
        data = self.draw(on=day.isoformat())
        self.assertEqual(data["recordsTotal"], self.on_date(day))
        self.assertGreater(data["recordsTotal"], 0)
        # It is fewer than the whole month.
        self.assertLess(data["recordsTotal"], self.in_range(
            datetime.date(2026, 8, 1), datetime.date(2026, 8, 31)))

    def test_a_from_to_range_shows_only_that_range(self):
        start, end = datetime.date(2026, 8, 10), datetime.date(2026, 8, 12)
        data = self.draw(**{"date_from": start.isoformat(), "date_to": end.isoformat()})
        self.assertEqual(data["recordsTotal"], self.in_range(start, end))

    def test_one_sided_range_is_a_single_day(self):
        day = datetime.date(2026, 8, 14)
        data = self.draw(**{"date_from": day.isoformat()})
        self.assertEqual(data["recordsTotal"], self.on_date(day))

    def test_a_backwards_range_falls_back_to_the_month(self):
        # to < from is invalid; the view ignores it and shows the month.
        data = self.draw(**{"date_from": "2026-08-20", "date_to": "2026-08-10"})
        self.assertEqual(data["recordsTotal"], self.in_range(
            datetime.date(2026, 8, 1), datetime.date(2026, 8, 31)))

    def test_the_single_date_overrides_a_range(self):
        day = datetime.date(2026, 8, 10)
        data = self.draw(on=day.isoformat(),
                         **{"date_from": "2026-08-01", "date_to": "2026-08-31"})
        self.assertEqual(data["recordsTotal"], self.on_date(day))

    def test_the_page_shows_the_date_inputs(self):
        page = self.client.get(self.url, {"year": "2026", "month": "8"})
        self.assertContains(page, 'data-daterange="att-range"')
        self.assertContains(page, 'name="on"')

    def test_calendar_jump_to_date_sets_the_month(self):
        page = self.client.get(reverse("attendance:attendance_calendar"), {
            "employee": self.employee.pk, "date": "2026-07-15",
        })
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "July")
        self.assertContains(page, 'name="date"')
