"""Four rules made fair (2026-10-07, approved by Nihal's senior):

1. Within the late or leaving-early grace is on time for worked minutes too.
2. "First and last scan" takes an unpaid break only from time beyond 5 hours.
3. "Came in" (at least a half day) needs a scan before the shift ended.
4. A grace is at most 60 minutes.
"""

import datetime

from django.core.exceptions import ValidationError

from attendance import tests_live
from attendance.models import AttendanceRecord
from attendance.pairing import BREAK_AFTER_MINUTES, unpaid_break_taken
from attendance.services import recalculate
from common.tenant import use_company
from scheduling import services as schedule
from scheduling.models import Shift

DHAKA = tests_live.DHAKA
DAY = datetime.date(2026, 8, 10)
NEXT_MORNING = datetime.datetime(2026, 8, 11, 10, tzinfo=DHAKA)
Status = AttendanceRecord.AttendanceStatus


class FairRulesTestCase(tests_live.LiveTestCase):
    """09:00-18:00, a 60-minute unpaid break, 10 minutes' grace each way, a
    full day needs every working minute (480) - the strictest setting."""

    def setUp(self):
        super().setUp()
        self.office = schedule.create_shift(
            actor=self.admin, company_id=self.company.pk, values={
                "code": "STR", "name": "Strict",
                "start_time": datetime.time(9), "end_time": datetime.time(18),
                "spans_next_day": False, "grace_in_minutes": 10, "grace_out_minutes": 10,
                "minimum_full_day_minutes": 480, "minimum_half_day_minutes": 240,
                "default_break_minutes": 60, "break_is_paid": False,
                "overtime_after_minutes": 0,
            })
        self.settings(company_shift=self.office, punch_pairing_strategy="first_last",
                      late_made_up_after_shift=False)

    def settings(self, **values):
        schedule.update_attendance_settings(actor=self.admin, company_id=self.company.pk,
                                            values=values)

    def day(self, *scans):
        for hour, minute in scans:
            self.punch(DAY, hour, minute)
        recalculate(self.company.pk, start=DAY, end=DAY, now=NEXT_MORNING)
        return self.record(DAY)


class GraceTests(FairRulesTestCase):
    def test_arriving_inside_the_grace_is_on_time(self):
        record = self.day((9, 4), (18, 0))
        self.assertEqual(record.late_minutes, 0)
        self.assertEqual(record.worked_minutes, 480)       # 540 - 60, not 536 - 60
        self.assertEqual(record.attendance_status, Status.PRESENT)

    def test_leaving_inside_the_grace_is_on_time(self):
        record = self.day((9, 0), (17, 52))
        self.assertEqual(record.early_out_minutes, 0)
        self.assertEqual(record.worked_minutes, 480)
        self.assertEqual(record.attendance_status, Status.PRESENT)

    def test_beyond_the_grace_the_minutes_are_missing(self):
        record = self.day((9, 15), (18, 0))
        self.assertEqual(record.late_minutes, 5)
        self.assertEqual(record.worked_minutes, 465)
        self.assertEqual(record.attendance_status, Status.HALF_DAY)

    def test_an_old_shift_with_a_huge_grace_credits_an_hour_at_most(self):
        with use_company(self.company):
            Shift.objects.filter(pk=self.office.pk).update(grace_out_minutes=480)
        record = self.day((9, 0), (14, 0))                 # left 4 hours early
        self.assertEqual(record.worked_minutes, 300)       # 5 hours, no break, no credit

    def test_alternating_pairing_gets_it_too(self):
        self.settings(punch_pairing_strategy="alternating")
        record = self.day((9, 8), (18, 0))
        self.assertEqual(record.worked_minutes, 540)       # no lunch scanned


class BreakTests(FairRulesTestCase):
    def test_the_rule(self):
        self.assertEqual(BREAK_AFTER_MINUTES, 300)
        self.assertEqual(unpaid_break_taken(240, 60), 0)
        self.assertEqual(unpaid_break_taken(300, 60), 0)
        self.assertEqual(unpaid_break_taken(330, 60), 30)
        self.assertEqual(unpaid_break_taken(540, 60), 60)

    def test_a_short_day_keeps_its_hours(self):
        self.assertEqual(self.day((9, 0), (13, 0)).worked_minutes, 240)

    def test_a_day_just_over_five_hours_loses_only_the_excess(self):
        self.assertEqual(self.day((9, 0), (14, 30)).worked_minutes, 300)

    def test_a_whole_day_loses_the_whole_break(self):
        self.assertEqual(self.day((9, 0), (18, 0)).worked_minutes, 480)

    def test_a_paid_break_is_never_taken(self):
        with use_company(self.company):
            Shift.objects.filter(pk=self.office.pk).update(break_is_paid=True)
        self.assertEqual(self.day((9, 0), (18, 0)).worked_minutes, 540)


class CameInTests(FairRulesTestCase):
    def test_a_lone_scan_after_the_shift_is_not_coming_in(self):
        record = self.day((20, 15))
        self.assertEqual(record.attendance_status, Status.ABSENT)

    def test_a_short_visit_in_the_shift_is_a_half_day(self):
        record = self.day((9, 0), (10, 0))
        self.assertEqual(record.attendance_status, Status.HALF_DAY)

    def test_a_scan_before_the_start_counts(self):
        record = self.day((8, 40), (9, 30))
        self.assertEqual(record.attendance_status, Status.HALF_DAY)


class GraceLimitTests(FairRulesTestCase):
    def values(self, **changes):
        fields = {field: getattr(self.office, field) for field in schedule.SHIFT_FIELDS}
        return {**fields, **changes}

    def test_more_than_an_hour_is_refused(self):
        for field in ("grace_in_minutes", "grace_out_minutes"):
            with self.subTest(field=field), self.assertRaises(ValidationError) as caught:
                schedule.update_shift(actor=self.admin, company_id=self.company.pk,
                                      shift_id=self.office.pk, values=self.values(**{field: 61}))
            self.assertIn(field, caught.exception.message_dict)

    def test_an_hour_is_allowed(self):
        shift = schedule.update_shift(
            actor=self.admin, company_id=self.company.pk, shift_id=self.office.pk,
            values=self.values(grace_in_minutes=60, grace_out_minutes=60))
        self.assertEqual((shift.grace_in_minutes, shift.grace_out_minutes), (60, 60))

    def test_an_old_long_grace_is_pointed_out(self):
        with use_company(self.company):
            Shift.objects.filter(pk=self.office.pk).update(grace_out_minutes=660)
            shift = Shift.objects.get(pk=self.office.pk)
        self.assertIn("leaving early grace is over 60", shift.grace_warning)
        self.client.force_login(self.admin)
        from django.urls import reverse

        page = self.client.get(reverse("scheduling:schedule_overview"))
        self.assertContains(page, "Grace too long")
