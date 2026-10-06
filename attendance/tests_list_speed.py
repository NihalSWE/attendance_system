"""Attendance is read from what is saved, not worked out again on every load
(2026-10-06).

Measured before: every load of the current month rebuilt every employee's every
day so far - 677 queries for 21 people on day 6, the second load no cheaper
than the first. Now a load rebuilds only what time alone has changed since the
last build (attendance.services.refresh), and a device's check-in keeps today
and yesterday built in the background (settle_soon).
"""

import datetime
from decimal import Decimal

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from attendance import tests_live
from attendance.models import AttendanceDayBuild, AttendanceRecord
from attendance.services import final_from, refresh, settle_recent
from common.tenant import use_company
from employees.services import create_employee
from organization.catalogue import adopt_department, adopt_designation

UTC = datetime.timezone.utc
DHAKA = datetime.timezone(datetime.timedelta(hours=6))


class SavedAttendanceCase(tests_live.LiveTestCase):
    PEOPLE = 20

    def setUp(self):
        super().setUp()
        with use_company(self.company):
            self.department = adopt_department(self.branch, "OPS", "Operations")
            self.designation = adopt_designation(self.department, "OPR", "Operator")
        for number in range(self.PEOPLE):
            self.hire(f"Person {number}", f"P{number:03d}")
        self.client.force_login(self.admin)

    def hire(self, name, code, since=datetime.datetime(2026, 1, 1, tzinfo=UTC)):
        return create_employee(
            company=self.company, first_name=name, employee_code=code, branch=self.branch,
            department=self.department, designation=self.designation, effective_from=since,
            pay_basis="monthly", base_rate=Decimal("20000"))["employee"]

    def load(self, **query):
        with CaptureQueriesContext(connection) as queries:
            page = self.client.get(reverse("attendance:attendance_list"), query)
        self.assertEqual(page.status_code, 200)
        return len(queries)


class PageLoadTests(SavedAttendanceCase):
    def test_a_second_load_reads_what_is_saved(self):
        first = self.load()
        second = self.load()
        # The first builds the month once; the second only reads it - a handful
        # of queries however many people and days there are.
        self.assertGreater(first, 200)
        self.assertLess(second, 60)

    def test_a_day_not_final_is_built_again_once_due(self):
        self.load()
        today = timezone.localdate()
        now = timezone.now()
        # Within a few minutes: nothing to do.
        self.assertTrue(refresh(self.company.pk, start=today, end=today, now=now)["unchanged"])
        # Later on: today can still change with time (a shift ends), so it is
        # built again.
        later = now + datetime.timedelta(minutes=6)
        self.assertNotIn("unchanged", refresh(self.company.pk, start=today, end=today,
                                              now=later))
        self.assertEqual(AttendanceDayBuild.objects.get(company=self.company,
                                                        work_date=today).built_at, later)

    def test_a_final_day_is_never_built_again(self):
        today = timezone.localdate()
        old = today - datetime.timedelta(days=3)
        refresh(self.company.pk, start=old, end=today)
        built = AttendanceDayBuild.objects.get(company=self.company, work_date=old).built_at
        self.assertGreaterEqual(built, final_from(old, DHAKA))
        much_later = timezone.now() + datetime.timedelta(days=1)
        refresh(self.company.pk, start=old, end=old, now=much_later)
        self.assertEqual(AttendanceDayBuild.objects.get(company=self.company,
                                                        work_date=old).built_at, built)

    def test_someone_hired_afterwards_gets_their_days(self):
        today = timezone.localdate()
        start = today.replace(day=1)
        self.load()
        newcomer = self.hire("Newcomer", "N001",
                             since=datetime.datetime.combine(start, datetime.time(),
                                                             tzinfo=DHAKA))
        self.load()
        with use_company(self.company):
            days = AttendanceRecord.objects.filter(employee=newcomer,
                                                   work_date__lt=today).count()
        # Every finished working day of theirs is there (absent, or a day off).
        self.assertGreaterEqual(days, max(0, today.day - 2))

    def test_a_scan_shows_at_once(self):
        """A scan arriving rewrites its day straight away (ingestion recalculates
        the days it touched) - the saved answer is never behind a real change."""
        from attendance.services import recalculate_for_punches

        self.load()
        today = timezone.localdate()
        yesterday = today - datetime.timedelta(days=1)
        self.punch(yesterday, 9, 5)
        recalculate_for_punches(self.company.pk, [(self.employee.pk, yesterday)])
        self.load()
        with use_company(self.company):
            day = AttendanceRecord.objects.get(employee=self.employee, work_date=yesterday)
        self.assertIsNotNone(day.first_in_at)


class BackgroundTests(SavedAttendanceCase):
    def test_the_background_pass_builds_today_and_yesterday(self):
        settle_recent(self.company.pk)
        today = timezone.localdate()
        self.assertEqual(
            set(AttendanceDayBuild.objects.filter(company=self.company)
                .values_list("work_date", flat=True)),
            {today - datetime.timedelta(days=1), today})

    def test_after_it_a_page_only_reads(self):
        today = timezone.localdate()
        refresh(self.company.pk, start=today.replace(day=1), end=today)
        settle_recent(self.company.pk)
        self.assertLess(self.load(), 60)

    @override_settings(ATTENDANCE_SETTLE_IN_BACKGROUND=False)
    def test_a_device_check_in_starts_it(self):
        from unittest import mock

        with mock.patch("attendance.services.settle_soon") as settle:
            self.client.get("/iclock/getrequest", {"SN": "SN-LIVE"})
        settle.assert_called_once_with(self.company.pk)


class CommandTests(SavedAttendanceCase):
    def test_the_scheduled_command_builds_and_then_rests(self):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("settle_attendance", stdout=out)
        self.assertIn("Live Ltd: ", out.getvalue())
        self.assertNotIn("up to date", out.getvalue())
        again = StringIO()
        call_command("settle_attendance", stdout=again)
        self.assertIn("Live Ltd: up to date.", again.getvalue())
