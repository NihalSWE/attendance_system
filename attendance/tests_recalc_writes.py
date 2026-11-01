"""Recalculating writes only the days whose answer changed (2026-11-01).

Salary generation recalculates a whole month for everyone; before, every day
was deleted and written again (52 s for 100 people). Now a day that comes out
as stored is left alone, and days with nothing stored are written in batches.
"""

import datetime

from django.db import connection
from django.test.utils import CaptureQueriesContext

from attendance.models import AttendanceRecord, AttendanceSession, PunchAllocation
from attendance.services import recalculate
from attendance.tests_live import DHAKA, LiveTestCase
from common.tenant import use_company

MONDAY = datetime.date(2026, 8, 10)
LATER = datetime.datetime(2026, 9, 1, 12, tzinfo=DHAKA)


class RecalculateWritesTests(LiveTestCase):
    def setUp(self):
        super().setUp()
        for day in range(3, 15):
            on = datetime.date(2026, 8, day)
            self.punch(on, 9)
            self.punch(on, 18)

    def snapshot(self):
        with use_company(self.company):
            return (
                sorted(AttendanceRecord.objects.values_list("pk", "work_date", "attendance_status",
                                                            "worked_minutes", "updated_at")),
                sorted(PunchAllocation.objects.values_list("pk", "attendance_record_id", "event_at")),
                sorted(AttendanceSession.objects.values_list("pk", "attendance_record_id", "worked_minutes")),
            )

    def run_month(self):
        with CaptureQueriesContext(connection) as queries:
            recalculate(self.company.pk, start=datetime.date(2026, 8, 1), end=datetime.date(2026, 8, 31), now=LATER)
        return len(queries)

    def test_unchanged_days_are_left_alone_and_a_change_is_written(self):
        first = self.run_month()
        built = self.snapshot()
        self.assertEqual(len(built[0]), 31)
        self.assertEqual(self.record(MONDAY).attendance_status, "present")
        again = self.run_month()
        self.assertEqual(self.snapshot(), built)          # same rows, same ids, not even touched
        self.assertLess(again, first)
        # A scan in the middle of Monday: that day alone is written again.
        self.punch(MONDAY, 13)
        self.punch(MONDAY, 14)
        self.run_month()
        after = self.snapshot()
        changed = [row for row in after[0] if row not in built[0]]
        self.assertEqual([row[1] for row in changed], [MONDAY])
        self.assertEqual(self.record(MONDAY).break_count, 1)
