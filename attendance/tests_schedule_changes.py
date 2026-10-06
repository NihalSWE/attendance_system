"""A schedule change reaches the days already worked out, a person without a
shift is named, and a full day out of reach is pointed out (2026-10-06).

A client's complaint: a woman who scanned every day had an empty calendar
(her department had no shift), and a man who worked almost twelve hours got a
half day (his shift needed all 720 minutes of a 12-hour shift with a one-hour
unpaid break). Setting the shifts right afterwards changed nothing on screen:
days already built were never measured again.
"""

import datetime
import zoneinfo

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from attendance import tests_live
from attendance.models import AttendanceDue, AttendanceRecord
from attendance.services import refresh, schedule_changed, settle_due
from common.tenant import use_company
from employees.models import EmployeeAssignment
from scheduling import services as schedule
from scheduling.models import Shift


class ScheduleChangeTests(tests_live.LiveTestCase):
    def setUp(self):
        super().setUp()
        tz = zoneinfo.ZoneInfo(self.company.timezone or "UTC")
        self.today = timezone.now().astimezone(tz).date()
        if self.today.day == 1:
            self.skipTest("Needs a finished day earlier this month.")
        self.day = self.today - datetime.timedelta(days=1)
        self.punch(self.day, 9)
        self.punch(self.day, 17)          # 480 minutes in a 9-18 shift
        with use_company(self.company):
            self.department = EmployeeAssignment.objects.get(employee=self.employee).department

    def read(self):
        refresh(self.company.pk, start=self.day, end=self.day)
        return self.record(self.day)

    def edit_shift(self, **values):
        fields = {field: getattr(self.shift, field) for field in schedule.SHIFT_FIELDS}
        schedule.update_shift(actor=self.admin, company_id=self.company.pk,
                              shift_id=self.shift.pk, values={**fields, **values})

    def test_a_changed_shift_is_measured_on_the_next_read(self):
        self.assertEqual(self.read().attendance_status, AttendanceRecord.AttendanceStatus.PRESENT)
        self.edit_shift(minimum_full_day_minutes=500)
        self.assertEqual(self.read().attendance_status,
                         AttendanceRecord.AttendanceStatus.HALF_DAY)

    def test_renaming_a_shift_rebuilds_nothing(self):
        settle_due(self.company.pk)       # what setting the company shift up made due
        self.edit_shift(name="Day shift")
        self.assertFalse(AttendanceDue.objects.filter(company=self.company,
                                                      due_at__lte=timezone.now()).exists())
        self.edit_shift(grace_in_minutes=15)
        self.assertTrue(AttendanceDue.objects.filter(company=self.company, work_date=self.day,
                                                     due_at__lte=timezone.now()).exists())

    def test_a_department_given_its_first_shift_gets_its_days(self):
        schedule.update_attendance_settings(
            actor=self.admin, company_id=self.company.pk,
            values={"shift_mode": "department_shifts", "company_shift": None})
        self.assertIsNone(self.read())                       # no shift, no day
        schedule.set_department_shift(
            actor=self.admin, company_id=self.company.pk,
            values={"department": self.department, "shift": self.shift,
                    "effective_from": self.day})
        self.assertEqual(self.read().attendance_status, AttendanceRecord.AttendanceStatus.PRESENT)

    def test_an_employees_own_shift_is_measured_too(self):
        self.read()
        with use_company(self.company):
            strict = Shift.objects.create(
                company=self.company, code="STR", name="Strict",
                start_time=datetime.time(9), end_time=datetime.time(18),
                scheduled_minutes=540, minimum_full_day_minutes=500,
                minimum_half_day_minutes=200)
        schedule.set_employee_shift(
            actor=self.admin, company_id=self.company.pk,
            values={"employee": self.employee, "shift": strict, "first_day": self.day})
        self.assertEqual(self.read().attendance_status,
                         AttendanceRecord.AttendanceStatus.HALF_DAY)

    def test_an_old_date_reaches_back_to_last_month_only(self):
        this_month = self.today.replace(day=1)
        last_month = (this_month - datetime.timedelta(days=1)).replace(day=1)
        self.assertEqual(schedule_changed(self.company.pk, datetime.date(2020, 1, 1)), last_month)
        self.assertEqual(schedule_changed(self.company.pk), this_month)
        self.assertIsNone(schedule_changed(self.company.pk,
                                           self.today + datetime.timedelta(days=3)))

    @override_settings(ATTENDANCE_SETTLE_IN_BACKGROUND=True)
    def test_the_background_starts_after_the_save(self):
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            self.edit_shift(minimum_full_day_minutes=450)
        self.assertEqual(len(callbacks), 1)


class NoShiftNoticeTests(tests_live.LiveTestCase):
    def setUp(self):
        super().setUp()
        schedule.update_attendance_settings(
            actor=self.admin, company_id=self.company.pk,
            values={"shift_mode": "department_shifts", "company_shift": None})
        self.client.force_login(self.admin)

    def test_the_daily_list_names_the_department(self):
        page = self.client.get(reverse("attendance:attendance_list"))
        self.assertContains(page, "1 person has no shift")
        self.assertContains(page, "Software (1)")

    def test_the_calendar_says_it(self):
        page = self.client.get(reverse("attendance:attendance_calendar"),
                               {"employee": self.employee.pk})
        self.assertContains(page, "Rahim")
        self.assertContains(page, "has no shift, so no attendance is worked out")

    def test_nothing_is_said_when_everyone_has_one(self):
        schedule.update_attendance_settings(
            actor=self.admin, company_id=self.company.pk,
            values={"shift_mode": "department_shifts", "company_shift": self.shift})
        page = self.client.get(reverse("attendance:attendance_list"))
        self.assertNotContains(page, "no shift, so no attendance")
        page = self.client.get(reverse("attendance:attendance_calendar"),
                               {"employee": self.employee.pk})
        self.assertNotContains(page, "has no shift")


class FullDayWarningTests(tests_live.LiveTestCase):
    def twelve_hours(self, full, *, paid=False):
        return Shift(start_time=datetime.time(7), end_time=datetime.time(19),
                     scheduled_minutes=720, default_break_minutes=60,
                     break_is_paid=paid, minimum_full_day_minutes=full)

    def test_a_full_day_beyond_the_working_minutes(self):
        warning = self.twelve_hours(720).full_day_warning
        self.assertIn("660 minutes", warning)
        self.assertIn("Lower the full day below 660", warning)

    def test_a_full_day_of_every_minute(self):
        self.assertIn("a minute late", self.twelve_hours(660).full_day_warning)
        self.assertIn("a minute late", self.twelve_hours(720, paid=True).full_day_warning)

    def test_room_to_spare_says_nothing(self):
        self.assertEqual(self.twelve_hours(600).full_day_warning, "")
        self.assertEqual(self.twelve_hours(0).full_day_warning, "")

    def test_saving_such_a_shift_warns_and_the_list_marks_it(self):
        self.client.force_login(self.admin)
        page = self.client.post(reverse("scheduling:shift_edit", args=[self.shift.pk]), {
            "code": "DAY", "name": "Day", "start_time": "09:00", "end_time": "18:00",
            "grace_in_minutes": 10, "grace_out_minutes": 0,
            "minimum_full_day_minutes": 540, "minimum_half_day_minutes": 200,
            "default_break_minutes": 0, "overtime_after_minutes": 0,
        }, follow=True)
        self.assertContains(page, "Day: A full day needs all 540 working minutes")
        self.assertContains(page, "Too high")
