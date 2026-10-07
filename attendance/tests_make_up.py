"""Late time made up after the shift, and absent only when nobody came
(Nihal's senior, 2026-10-07; two company settings, on by default).

The senior's example: 9:00-18:00 with a paid lunch hour, full day 8 hours.
Four hours late, four hours after the shift: 5 + 3 made up = 8, a full day,
and 1 hour of overtime - not 4. On time, 9:00-18:00: a full day, no overtime.
"""

import datetime

from django.urls import reverse

from attendance import tests_live
from attendance.models import AttendanceRecord
from attendance.services import recalculate
from scheduling import services as schedule
from scheduling.models import CompanyAttendanceSettings

DHAKA = tests_live.DHAKA
DAY = datetime.date(2026, 8, 10)
NEXT_MORNING = datetime.datetime(2026, 8, 11, 10, tzinfo=DHAKA)
Status = AttendanceRecord.AttendanceStatus


class MakeUpTests(tests_live.LiveTestCase):
    def setUp(self):
        super().setUp()
        self.office = self.company_shift(overtime_after=0)

    def company_shift(self, *, overtime_after, code="OFF"):
        shift = schedule.create_shift(
            actor=self.admin, company_id=self.company.pk, values={
                "code": code, "name": f"Office {code}",
                "start_time": datetime.time(9), "end_time": datetime.time(18),
                "spans_next_day": False, "grace_in_minutes": 10,
                "minimum_full_day_minutes": 480, "minimum_half_day_minutes": 240,
                "default_break_minutes": 60, "break_is_paid": True,
                "overtime_after_minutes": overtime_after,
            })
        self.settings(company_shift=shift)
        return shift

    def settings(self, **values):
        schedule.update_attendance_settings(actor=self.admin, company_id=self.company.pk,
                                            values=values)

    def day(self, *scans):
        for hour, minute in scans:
            self.punch(DAY, hour, minute)
        recalculate(self.company.pk, start=DAY, end=DAY, now=NEXT_MORNING)
        return self.record(DAY)

    def test_the_seniors_example(self):
        record = self.day((13, 0), (22, 0))
        self.assertEqual(record.attendance_status, Status.PRESENT)
        self.assertEqual(record.worked_minutes, 480)               # 300 in the shift + 180
        self.assertEqual(record.calculated_overtime_minutes, 60)   # not 240
        self.assertEqual(record.approved_overtime_minutes, 60)
        self.assertEqual(record.late_minutes, 230)                 # still late: 240 - 10 grace
        self.assertIn("180 min after the shift made up", record.note)

    def test_not_enough_even_with_the_evening(self):
        record = self.day((13, 0), (19, 0))
        self.assertEqual(record.worked_minutes, 360)
        self.assertEqual(record.attendance_status, Status.HALF_DAY)
        self.assertEqual(record.calculated_overtime_minutes, 0)

    def test_on_time_is_a_full_day_and_no_overtime(self):
        record = self.day((9, 0), (18, 0))
        self.assertEqual(record.attendance_status, Status.PRESENT)
        self.assertEqual(record.worked_minutes, 540)
        self.assertEqual(record.calculated_overtime_minutes, 0)

    def test_an_hour_late_and_an_hour_after_is_an_hour_over(self):
        # 8 hours in the shift is already the full day: the evening is overtime.
        record = self.day((10, 0), (19, 0))
        self.assertEqual(record.attendance_status, Status.PRESENT)
        self.assertEqual(record.calculated_overtime_minutes, 60)
        self.assertNotIn("made up", record.note)

    def test_a_full_day_and_more_is_all_overtime(self):
        record = self.day((9, 0), (20, 0))
        self.assertEqual(record.attendance_status, Status.PRESENT)
        self.assertEqual(record.calculated_overtime_minutes, 120)
        self.assertNotIn("made up", record.note)

    def test_overtime_waits_its_minutes_after_the_make_up(self):
        self.company_shift(overtime_after=60, code="LATE")
        record = self.day((13, 0), (22, 0))
        self.assertEqual(record.worked_minutes, 480)
        self.assertEqual(record.calculated_overtime_minutes, 0)    # 60 left, 60 to wait

    def test_first_and_last_takes_an_unpaid_break_off_first(self):
        self.office.break_is_paid = False
        self.office.save()
        self.settings(punch_pairing_strategy="first_last")
        record = self.day((13, 0), (22, 0))
        # 300 in the shift less the unpaid hour = 240; 240 made up.
        self.assertEqual(record.worked_minutes, 480)
        self.assertEqual(record.calculated_overtime_minutes, 0)

    def test_the_evening_fills_up_to_a_full_day_above_the_work(self):
        # 9-18 with an unpaid hour is 480 of work, but this shift asks 500.
        self.office.minimum_full_day_minutes = 500
        self.office.break_is_paid = False
        self.office.save()
        self.settings(punch_pairing_strategy="first_last")
        record = self.day((9, 0), (18, 40))     # 540 - 60 = 480 in the shift, 40 after
        self.assertEqual(record.worked_minutes, 500)
        self.assertEqual(record.attendance_status, Status.PRESENT)
        self.assertEqual(record.calculated_overtime_minutes, 20)

    def test_switched_off_it_is_the_old_rule(self):
        self.settings(late_made_up_after_shift=False)
        record = self.day((13, 0), (22, 0))
        self.assertEqual(record.worked_minutes, 300)
        self.assertEqual(record.attendance_status, Status.HALF_DAY)
        self.assertEqual(record.calculated_overtime_minutes, 240)


class CameInTests(tests_live.LiveTestCase):
    def day(self, *scans):
        for hour, minute in scans:
            self.punch(DAY, hour, minute)
        recalculate(self.company.pk, start=DAY, end=DAY, now=NEXT_MORNING)
        return self.record(DAY)

    def test_a_short_day_is_a_half_day(self):
        record = self.day((9, 0), (10, 0))          # 60 min; half day needs 200
        self.assertEqual(record.attendance_status, Status.HALF_DAY)
        self.assertEqual(record.payable_fraction, AttendanceRecord._meta.get_field(
            "payable_fraction").to_python("0.5"))

    def test_nobody_came_is_still_absent(self):
        self.assertEqual(self.day().attendance_status, Status.ABSENT)

    def test_switched_off_a_short_day_is_absent(self):
        schedule.update_attendance_settings(actor=self.admin, company_id=self.company.pk,
                                            values={"came_in_is_half_day": False})
        self.assertEqual(self.day((9, 0), (10, 0)).attendance_status, Status.ABSENT)


class SettingsTests(tests_live.LiveTestCase):
    def test_both_are_on_for_a_new_company(self):
        settings = schedule.get_attendance_settings(self.company.pk)
        self.assertTrue(settings.late_made_up_after_shift)
        self.assertTrue(settings.came_in_is_half_day)

    def test_the_settings_page_turns_them_off(self):
        self.client.force_login(self.admin)
        page = self.client.post(reverse("scheduling:attendance_settings_edit"), {
            "shift_mode": "company_single_shift", "company_shift": self.shift.pk,
            "missing_punch_policy": "review_required",
            "punch_pairing_strategy": "alternating",
        })
        self.assertEqual(page.status_code, 302)
        settings = CompanyAttendanceSettings.all_objects.get(company=self.company)
        self.assertFalse(settings.late_made_up_after_shift)
        self.assertFalse(settings.came_in_is_half_day)
        page = self.client.get(reverse("scheduling:attendance_settings_edit"))
        self.assertContains(page, "Staying after the shift makes up for coming late")
        self.assertContains(page, "Anyone who came in gets at least a half day")
