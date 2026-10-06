"""Attendance is read from what is saved, and what time alone changes is
waited for one employee-day at a time (2026-10-06).

Measured before: every load of the current month rebuilt every employee's every
day so far - 677 queries for 21 people on day 6, the second load no cheaper
than the first. Now a load reads what is saved; each day that is not final
waits for its next change (its shift's end, then its close) in
``AttendanceDue``, and only the employee-days whose time has come are rebuilt -
so the work grows with what changed, not with how many people there are.
"""

import datetime
from decimal import Decimal

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from attendance import tests_live
from attendance.models import AttendanceDayBuild, AttendanceDue, AttendanceRecord
from attendance.services import refresh, settle_due, settle_recent
from common.tenant import use_company
from employees.services import create_employee
from organization.catalogue import adopt_department, adopt_designation

UTC = datetime.timezone.utc
DHAKA = datetime.timezone(datetime.timedelta(hours=6))
MONDAY = datetime.date(2026, 8, 10)


def at(day, hour, minute=0):
    return datetime.datetime(day.year, day.month, day.day, hour, minute, tzinfo=DHAKA)


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

    def due(self, day=MONDAY):
        return dict(AttendanceDue.objects.filter(company=self.company, work_date=day)
                    .values_list("employee_id", "due_at"))


class PageLoadTests(SavedAttendanceCase):
    def test_a_second_load_reads_what_is_saved(self):
        first = self.load()
        second = self.load()
        self.assertGreater(first, 200)
        self.assertLess(second, 60)

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
        self.assertGreaterEqual(days, max(0, today.day - 2))

    def test_a_scan_shows_at_once(self):
        """A scan rewrites its day straight away (ingestion recalculates the
        days it touched) - the saved answer is never behind a real change."""
        from attendance.services import recalculate_for_punches

        self.load()
        yesterday = timezone.localdate() - datetime.timedelta(days=1)
        self.punch(yesterday, 9, 5)
        recalculate_for_punches(self.company.pk, [(self.employee.pk, yesterday)])
        self.load()
        with use_company(self.company):
            day = AttendanceRecord.objects.get(employee=self.employee, work_date=yesterday)
        self.assertIsNotNone(day.first_in_at)


class DueTimeTests(SavedAttendanceCase):
    """One working Monday, 09:00-18:00, the day closing at Tuesday's shift start."""

    def setUp(self):
        super().setUp()
        self.punch(MONDAY, 9)                    # Rahim came in; nobody else has yet
        refresh(self.company.pk, start=MONDAY, end=MONDAY, now=at(MONDAY, 12))

    def test_each_day_waits_for_its_own_next_change(self):
        due = self.due()
        # Rahim's open day changes at his shift's end; everybody else's when the
        # day closes (they become absent then, not before).
        self.assertEqual(due[self.employee.pk], at(MONDAY, 18))
        others = {when for person, when in due.items() if person != self.employee.pk}
        self.assertEqual(len(due), self.PEOPLE + 1)
        self.assertEqual(len(others), 1)
        self.assertGreater(others.pop(), at(MONDAY, 18))

    def test_nothing_is_rebuilt_before_its_time(self):
        self.assertEqual(settle_due(self.company.pk, now=at(MONDAY, 17, 59))["days"], 0)

    def test_at_a_shift_end_only_that_person_is_rebuilt(self):
        result = settle_due(self.company.pk, now=at(MONDAY, 18, 30))
        # One employee-day, not twenty-one: the work follows what changed.
        self.assertEqual(result["days"], 1)
        self.assertGreater(self.due()[self.employee.pk], at(MONDAY, 18, 30))

    def test_after_the_close_the_day_is_final_and_waits_for_nothing(self):
        tuesday_morning = at(MONDAY + datetime.timedelta(days=1), 10)
        result = settle_due(self.company.pk, now=tuesday_morning)
        self.assertEqual(result["days"], self.PEOPLE + 1)
        self.assertEqual(self.due(), {})
        with use_company(self.company):
            statuses = list(AttendanceRecord.objects.filter(work_date=MONDAY)
                            .values_list("attendance_status", flat=True))
        self.assertEqual(statuses.count("absent"), self.PEOPLE)
        self.assertEqual(settle_due(self.company.pk, now=tuesday_morning)["days"], 0)

    def test_a_page_load_does_only_what_is_due(self):
        self.assertTrue(refresh(self.company.pk, start=MONDAY, end=MONDAY,
                                now=at(MONDAY, 13))["unchanged"])
        result = refresh(self.company.pk, start=MONDAY, end=MONDAY, now=at(MONDAY, 18, 5))
        self.assertEqual(result["days"], 1)

    def test_a_change_rewrites_what_it_waits_for(self):
        """A scan out after the shift ends: the day is rebuilt by the scan, and
        its next change is the close."""
        from attendance.services import recalculate

        self.punch(MONDAY, 18, 10)
        recalculate(self.company.pk, employee_ids=[self.employee.pk], start=MONDAY,
                    end=MONDAY, now=at(MONDAY, 18, 15))
        self.assertGreater(self.due()[self.employee.pk], at(MONDAY, 18, 15))


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
