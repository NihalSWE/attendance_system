"""The attendance calendar: the month shape, the day panel, and the screens.

``month_view`` is where the arranging happens and it needs only records, so
most of this is about the shape it returns — the Monday-first padding, the
tint each status gets, and the summary agreeing with the days it sits over.
The view tests then check the two things a template can silently get wrong:
that every day with a record is a real button, and that the panel is served
as its own fragment.
"""

import datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import CompanyMembership, User
from attendance import month_view
from attendance.models import AttendanceRecord
from common.tenant import use_company
from employees.services import create_employee
from organization.catalogue import adopt_department, adopt_designation
from organization.models import Branch
from scheduling import services as schedule
from tenants.services import onboard_company

UTC = datetime.timezone.utc
S = AttendanceRecord.AttendanceStatus


class CalendarTestCase(TestCase):
    """One employee with a shift, and records written straight in."""

    def setUp(self):
        self.company = onboard_company(code="CAL", slug="cal", name="Cal Ltd")
        self.admin = User.objects.create_user(email="admin@cal.test", password="pw")
        CompanyMembership.all_objects.create(
            company=self.company, user=self.admin,
            role=CompanyMembership.Role.COMPANY_ADMIN,
            status=CompanyMembership.Status.ACTIVE,
        )
        with use_company(self.company):
            self.branch = Branch.objects.get(is_default=True)
            department = adopt_department(self.branch, "SW", "Software")
            designation = adopt_designation(department, "DEV", "Developer")
        self.employee = create_employee(
            company=self.company, first_name="Rahim", employee_code="E1",
            branch=self.branch, department=department, designation=designation,
            effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC),
            pay_basis="monthly", base_rate=Decimal("30000"),
        )["employee"]
        # A tenant-scoped reverse accessor, so it needs the company context.
        with use_company(self.company):
            self.assignment = self.employee.assignments.first()
        self.shift = schedule.create_shift(
            actor=self.admin, company_id=self.company.pk, values={
                "code": "DAY", "name": "Day",
                "start_time": datetime.time(9), "end_time": datetime.time(18),
                "spans_next_day": False, "grace_in_minutes": 10,
                "minimum_full_day_minutes": 400, "minimum_half_day_minutes": 200,
            },
        )

    def record(self, day, status=S.PRESENT, **kwargs):
        """One stored day. Times are given as UTC instants."""
        values = {
            "employee_assignment": self.assignment,
            "branch": self.branch,
            "shift": self.shift,
            "scheduled_start_at": datetime.datetime(
                day.year, day.month, day.day, 3, tzinfo=UTC
            ),
            "scheduled_end_at": datetime.datetime(
                day.year, day.month, day.day, 12, tzinfo=UTC
            ),
            "attendance_status": status,
            "punch_status": AttendanceRecord.PunchStatus.COMPLETE,
            "payable_fraction": Decimal("1"),
            "calculated_at": datetime.datetime(2026, 9, 30, tzinfo=UTC),
        }
        values.update(kwargs)
        with use_company(self.company):
            return AttendanceRecord.objects.create(
                company=self.company, employee=self.employee, work_date=day,
                **values,
            )

    def month(self, year=2026, month=9, today=None):
        with use_company(self.company):
            return month_view.build_month(
                employee=self.employee, year=year, month=month,
                company_timezone="Asia/Dhaka",
                today=today or datetime.date(2026, 9, 30),
            )


class MonthShapeTests(CalendarTestCase):
    def test_the_month_is_whole_monday_first_weeks(self):
        result = self.month()
        self.assertTrue(all(len(week) == 7 for week in result["weeks"]))
        self.assertEqual(result["weekday_names"][0], "Mon")
        # 1 September 2026 is a Tuesday, so Monday 31 August leads.
        first_cell = result["weeks"][0][0]
        self.assertEqual(first_cell.date, datetime.date(2026, 8, 31))
        self.assertTrue(getattr(first_cell, "is_filler", False))

    def test_neighbouring_months_are_fillers_and_never_clickable(self):
        result = self.month()
        fillers = [
            cell for week in result["weeks"] for cell in week
            if getattr(cell, "is_filler", False)
        ]
        self.assertTrue(fillers)
        self.assertTrue(all(not cell.has_record for cell in fillers))

    def test_every_day_of_the_month_is_present_exactly_once(self):
        result = self.month()
        self.assertEqual(len(result["days"]), 30)
        self.assertEqual(
            [d.date.day for d in result["days"]], list(range(1, 31))
        )

    def test_a_day_with_no_record_is_empty_not_absent(self):
        """Nothing is invented for a day the calculation never wrote."""
        day = self.month()["days"][0]
        self.assertFalse(day.has_record)
        self.assertEqual(day.status_label, "")
        self.assertEqual(day.tone, "")

    def test_today_is_marked_and_future_days_are_known(self):
        result = self.month(today=datetime.date(2026, 9, 15))
        days = {d.date.day: d for d in result["days"]}
        self.assertTrue(days[15].is_today)
        self.assertFalse(days[14].is_today)
        self.assertTrue(days[16].is_future)
        self.assertFalse(days[14].is_future)


class DaySquareTests(CalendarTestCase):
    def test_each_status_takes_an_existing_token_family(self):
        expected = {
            S.PRESENT: "success",
            S.HALF_DAY: "warning",
            S.INCOMPLETE: "warning",
            S.ABSENT: "danger",
            S.LEAVE: "info",
            S.HOLIDAY: "info",
            S.WEEKLY_OFF: "neutral",
        }
        for index, (status, tone) in enumerate(expected.items(), start=1):
            with self.subTest(status=status):
                self.record(datetime.date(2026, 9, index), status=status)
        days = {d.date.day: d for d in self.month()["days"]}
        for index, (status, tone) in enumerate(expected.items(), start=1):
            with self.subTest(status=status):
                self.assertEqual(days[index].tone, tone)

    def test_a_late_present_day_is_tinted_as_a_warning(self):
        """Late is not its own status, but it reads as its own thing."""
        self.record(datetime.date(2026, 9, 1), status=S.PRESENT, late_minutes=25)
        day = {d.date.day: d for d in self.month()["days"]}[1]
        self.assertTrue(day.is_late)
        self.assertEqual(day.tone, "warning")
        self.assertEqual(day.status_label, "Present")

    def test_leave_says_whether_it_is_paid(self):
        self.record(
            datetime.date(2026, 9, 1), status=S.LEAVE,
            payable_fraction=Decimal("1"),
        )
        self.record(
            datetime.date(2026, 9, 2), status=S.LEAVE,
            payable_fraction=Decimal("0"),
        )
        days = {d.date.day: d for d in self.month()["days"]}
        self.assertEqual(days[1].status_label, "Leave (paid)")
        self.assertEqual(days[2].status_label, "Leave (unpaid)")

    def test_times_are_shown_in_company_time(self):
        """Stored 03:02 UTC is 09:02 in Dhaka, which is what the square says."""
        self.record(
            datetime.date(2026, 9, 1),
            first_in_at=datetime.datetime(2026, 9, 1, 3, 2, tzinfo=UTC),
            last_out_at=datetime.datetime(2026, 9, 1, 12, 10, tzinfo=UTC),
            worked_minutes=512, total_minutes=548, break_count=2,
        )
        day = {d.date.day: d for d in self.month()["days"]}[1]
        self.assertEqual(day.check_in, "09:02")
        self.assertEqual(day.check_out, "18:10")
        self.assertEqual(day.in_office, "8h 32m")
        self.assertEqual(day.break_label, "2 breaks")

    def test_one_break_is_singular(self):
        self.record(datetime.date(2026, 9, 1), break_count=1)
        self.assertEqual(self.month()["days"][0].break_label, "1 break")

    def test_a_day_with_no_check_out_shows_a_dash_not_a_guess(self):
        self.record(
            datetime.date(2026, 9, 1), status=S.INCOMPLETE,
            first_in_at=datetime.datetime(2026, 9, 1, 3, 2, tzinfo=UTC),
        )
        day = self.month()["days"][0]
        self.assertEqual(day.check_in, "09:02")
        self.assertEqual(day.check_out, "")


class SummaryTests(CalendarTestCase):
    def test_the_strip_counts_the_days_it_sits_over(self):
        self.record(datetime.date(2026, 9, 1), status=S.PRESENT, worked_minutes=480)
        self.record(datetime.date(2026, 9, 2), status=S.PRESENT, worked_minutes=450,
                    late_minutes=12)
        self.record(datetime.date(2026, 9, 3), status=S.ABSENT, worked_minutes=0)
        self.record(datetime.date(2026, 9, 4), status=S.LEAVE, worked_minutes=0)
        self.record(datetime.date(2026, 9, 5), status=S.HOLIDAY, worked_minutes=0)

        summary = self.month()["summary"]
        self.assertEqual(summary["present"], 2)
        self.assertEqual(summary["late"], 1)
        self.assertEqual(summary["absent"], 1)
        self.assertEqual(summary["leave"], 1)
        self.assertEqual(summary["holiday"], 1)
        self.assertEqual(summary["in_office"], "15h 30m")

    def test_an_empty_month_summarises_to_zero(self):
        summary = self.month()["summary"]
        self.assertEqual(summary["present"], 0)
        self.assertEqual(summary["in_office"], "0h 0m")


class DayPanelTests(CalendarTestCase):
    def test_the_panel_lists_every_scan_with_its_label_and_device(self):
        from attendance.models import PunchAllocation

        record = self.record(
            datetime.date(2026, 9, 1),
            first_in_at=datetime.datetime(2026, 9, 1, 3, tzinfo=UTC),
            last_out_at=datetime.datetime(2026, 9, 1, 12, tzinfo=UTC),
            worked_minutes=480, total_minutes=540, outside_minutes=60,
            break_count=1,
        )
        with use_company(self.company):
            for index, (hour, label) in enumerate(
                [(3, "check_in"), (7, "break_out"), (8, "break_in"),
                 (12, "check_out")],
                start=1,
            ):
                PunchAllocation.objects.create(
                    company=self.company, attendance_record=record,
                    sequence_number=index,
                    event_at=datetime.datetime(2026, 9, 1, hour, tzinfo=UTC),
                    label=label,
                    interpreted_direction="in" if index % 2 else "out",
                )
            detail = month_view.build_day_detail(
                record=record, company_timezone="Asia/Dhaka"
            )

        self.assertEqual(
            [s["label"] for s in detail["scans"]],
            ["Check-in", "Break-out", "Break-in", "Check-out"],
        )
        # 03:00 UTC is 09:00 in Dhaka.
        self.assertEqual(detail["scans"][0]["time"], "09:00:00")
        self.assertEqual(detail["total"], "9h 0m")
        self.assertEqual(detail["in_office"], "8h 0m")
        self.assertEqual(detail["outside"], "1h 0m")
        self.assertEqual(detail["break_count"], 1)
        self.assertEqual(detail["shift_name"], "Day")

    def test_ignored_repeats_are_shown_and_counted_apart(self):
        from attendance.models import PunchAllocation

        record = self.record(datetime.date(2026, 9, 1))
        with use_company(self.company):
            PunchAllocation.objects.create(
                company=self.company, attendance_record=record, sequence_number=1,
                event_at=datetime.datetime(2026, 9, 1, 3, tzinfo=UTC),
                label="check_in", interpreted_direction="in",
            )
            PunchAllocation.objects.create(
                company=self.company, attendance_record=record, sequence_number=2,
                event_at=datetime.datetime(2026, 9, 1, 3, 0, 10, tzinfo=UTC),
                label="ignored", interpreted_direction="ignored",
                is_included=False, exclusion_reason="duplicate",
            )
            detail = month_view.build_day_detail(
                record=record, company_timezone="Asia/Dhaka"
            )
        self.assertEqual(detail["ignored_count"], 1)
        self.assertEqual(len(detail["scans"]), 2)


class CalendarScreenTests(CalendarTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        self.record(
            datetime.date(2026, 9, 1), status=S.PRESENT,
            first_in_at=datetime.datetime(2026, 9, 1, 3, tzinfo=UTC),
            last_out_at=datetime.datetime(2026, 9, 1, 12, tzinfo=UTC),
            worked_minutes=480, total_minutes=540,
        )

    def url(self, **params):
        base = reverse("attendance:attendance_calendar")
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{base}?{query}" if query else base

    def test_the_page_renders_the_month(self):
        response = self.client.get(
            self.url(employee=self.employee.pk, month=9, year=2026)
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Attendance calendar")
        self.assertContains(response, "09:00 → 18:00")

    def test_every_day_with_a_record_is_a_real_button(self):
        """Keyboard reachable: a div with a click handler is not."""
        response = self.client.get(
            self.url(employee=self.employee.pk, month=9, year=2026)
        )
        body = response.content.decode()
        self.assertIn('<button type="button" role="gridcell"', body)
        self.assertIn('data-day="2026-09-01"', body)

    def test_the_grid_and_the_phone_list_show_the_same_days(self):
        """One month, rendered twice; CSS picks which is on screen."""
        response = self.client.get(
            self.url(employee=self.employee.pk, month=9, year=2026)
        )
        body = response.content.decode()
        self.assertIn("cal__grid", body)
        self.assertIn("cal__list", body)
        # The day appears in both renderings.
        self.assertEqual(body.count('data-day="2026-09-01"'), 2)

    def test_choosing_no_employee_falls_back_to_one(self):
        """An empty calendar tells nobody anything."""
        response = self.client.get(self.url(month=9, year=2026))
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.context["employee"])

    def test_the_day_panel_is_served_as_its_own_fragment(self):
        response = self.client.get(
            reverse(
                "attendance:attendance_day",
                args=[self.employee.pk, "2026-09-01"],
            )
        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Total time", body)
        # A fragment, not a whole page.
        self.assertNotIn("<html", body)

    def test_a_day_with_nothing_calculated_says_so(self):
        response = self.client.get(
            reverse(
                "attendance:attendance_day",
                args=[self.employee.pk, "2026-09-20"],
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nothing calculated for this day")

    def test_another_company_cannot_read_a_day(self):
        other = onboard_company(code="OTH", slug="oth", name="Other Ltd")
        outsider = User.objects.create_user(email="out@oth.test", password="pw")
        CompanyMembership.all_objects.create(
            company=other, user=outsider,
            role=CompanyMembership.Role.COMPANY_ADMIN,
            status=CompanyMembership.Status.ACTIVE,
        )
        self.client.force_login(outsider)
        response = self.client.get(
            reverse(
                "attendance:attendance_day",
                args=[self.employee.pk, "2026-09-01"],
            )
        )
        # Scoped away: the other company's day is simply not there.
        self.assertContains(response, "Nothing calculated for this day")

    def test_the_calendar_needs_a_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url()).status_code, 302)

    def test_the_table_page_links_to_the_calendar(self):
        """The complaint that started it: the calendar has to be findable."""
        response = self.client.get(
            reverse("attendance:attendance_list") + "?month=9&year=2026"
        )
        self.assertContains(response, reverse("attendance:attendance_calendar"))
