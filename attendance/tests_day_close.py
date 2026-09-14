"""The day-close rule: when a day ends, and what its minutes are worth.

DEVICE_ATTENDANCE_POLICY.md step 7, "When a day closes, and the check-out"
(Ajay, 2026-09-14). Both modules under test are free of the database, so the
rule is checked directly rather than through a month of fixtures.
"""

import datetime
from types import SimpleNamespace

from django.test import TestCase

from attendance import day_window, pairing

TZ = datetime.timezone.utc


def at(hour, minute=0, day=10):
    return datetime.datetime(2026, 8, day, hour, minute, tzinfo=TZ)


def shift(start=9, end=18, spans=False, grace=0, break_minutes=0, paid=False):
    return SimpleNamespace(
        start_time=datetime.time(start), end_time=datetime.time(end),
        spans_next_day=spans, grace_in_minutes=grace,
        grace_out_minutes=0, overtime_after_minutes=0,
        default_break_minutes=break_minutes, break_is_paid=paid,
    )


def labels(day):
    return [scan.label for scan in day.kept]


class OpenDayTests(TestCase):
    """While a day is open, nobody has gone home yet."""

    def test_a_trailing_out_is_a_break_not_a_check_out(self):
        """The bug this rule was written for: lunch read as going home."""
        day = pairing.build_day([at(9), at(13)], is_closed=False)
        self.assertEqual(labels(day), ["check_in", "break_out"])
        self.assertFalse(day.has_check_out)
        self.assertIsNone(day.last_out_at)
        self.assertEqual(day.total_minutes, 0)

    def test_a_trailing_in_means_still_in(self):
        day = pairing.build_day([at(9), at(13), at(14)], is_closed=False)
        self.assertEqual(labels(day), ["check_in", "break_out", "break_in"])
        self.assertTrue(day.is_still_in)

    def test_an_open_day_is_never_flagged_for_review(self):
        """Nothing is wrong yet — the day simply has not finished."""
        day = pairing.build_day([at(9), at(13)], is_closed=False)
        self.assertFalse(day.needs_review)
        self.assertEqual(day.review_reason, "")


class ClosedDayTests(TestCase):
    """When the day closes the trailing scan is finally decided."""

    def test_a_trailing_out_becomes_the_check_out(self):
        day = pairing.build_day(
            [at(9), at(13)], is_closed=True,
            scheduled_start=at(9), scheduled_end=at(18),
        )
        self.assertEqual(labels(day), ["check_in", "check_out"])
        self.assertEqual(day.last_out_at, at(13))
        self.assertEqual(day.total_minutes, 240)
        self.assertFalse(day.needs_review)

    def test_a_day_that_ends_on_an_in_is_closed_at_the_shift_end(self):
        day = pairing.build_day(
            [at(9), at(13), at(14)], is_closed=True,
            scheduled_start=at(9), scheduled_end=at(18),
        )
        self.assertTrue(day.check_out_by_rule)
        self.assertEqual(day.last_out_at, at(18))
        self.assertEqual(day.review_reason, "check-out by rule, no scan")
        # 09–13 and 14–18, so the shift end closed the second session.
        self.assertEqual(day.worked_minutes, 480)

    def test_a_rule_check_out_is_not_treated_as_leaving_early(self):
        """Nobody left early: the software chose the time, not the employee."""
        day = pairing.build_day(
            [at(9), at(13), at(14)], is_closed=True,
            scheduled_start=at(9), scheduled_end=at(18),
        )
        self.assertEqual(day.early_out_minutes, 0)

    def test_leaving_before_the_shift_end_is_measured(self):
        day = pairing.build_day(
            [at(9), at(16)], is_closed=True,
            scheduled_start=at(9), scheduled_end=at(18),
        )
        self.assertEqual(day.early_out_minutes, 120)


class EarlyArrivalTests(TestCase):
    """Arriving early is normal. It is not overtime and it is not paid."""

    def test_time_before_the_shift_start_is_not_paid(self):
        day = pairing.build_day(
            [at(8, 30), at(18)], is_closed=True,
            scheduled_start=at(9), scheduled_end=at(18),
        )
        self.assertEqual(day.worked_minutes, 540)   # 09:00–18:00, not 08:30
        self.assertEqual(day.overtime_minutes, 0)

    def test_the_real_check_in_time_is_still_shown(self):
        day = pairing.build_day(
            [at(8, 30), at(18)], is_closed=True,
            scheduled_start=at(9), scheduled_end=at(18),
        )
        self.assertEqual(day.first_in_at, at(8, 30))
        self.assertEqual(day.total_minutes, 570)

    def test_arriving_early_is_never_late(self):
        day = pairing.build_day(
            [at(8, 30), at(18)], is_closed=True,
            scheduled_start=at(9), scheduled_end=at(18), grace_in_minutes=10,
        )
        self.assertEqual(day.late_minutes, 0)

    def test_lateness_is_measured_from_the_start_after_grace(self):
        day = pairing.build_day(
            [at(9, 25), at(18)], is_closed=True,
            scheduled_start=at(9), scheduled_end=at(18), grace_in_minutes=10,
        )
        self.assertEqual(day.late_minutes, 15)


class OvertimeTests(TestCase):
    """After the scheduled end it is overtime, and overtime is not worked time."""

    def test_time_after_the_shift_end_is_overtime(self):
        day = pairing.build_day(
            [at(9), at(20)], is_closed=True,
            scheduled_start=at(9), scheduled_end=at(18),
        )
        self.assertEqual(day.worked_minutes, 540)     # 09–18
        self.assertEqual(day.overtime_minutes, 120)   # 18–20

    def test_a_separate_evening_session_is_all_overtime(self):
        day = pairing.build_day(
            [at(9), at(18), at(19), at(21)], is_closed=True,
            scheduled_start=at(9), scheduled_end=at(18),
        )
        self.assertEqual(day.worked_minutes, 540)
        self.assertEqual(day.overtime_minutes, 120)

    def test_an_evening_session_with_no_check_out_pays_nothing(self):
        """An overtime claim nobody closed is worth zero until approved."""
        day = pairing.build_day(
            [at(9), at(18), at(19)], is_closed=True,
            scheduled_start=at(9), scheduled_end=at(18),
        )
        self.assertTrue(day.open_overtime)
        self.assertEqual(day.review_reason, "overtime session with no check-out")
        self.assertEqual(day.overtime_minutes, 0)
        self.assertEqual(day.worked_minutes, 540)
        # The regular day still closed properly at 18:00.
        self.assertEqual(day.last_out_at, at(18))
        self.assertFalse(day.check_out_by_rule)

    def test_that_open_overtime_session_has_a_blank_check_out(self):
        day = pairing.build_day(
            [at(9), at(18), at(19)], is_closed=True,
            scheduled_start=at(9), scheduled_end=at(18),
        )
        evening = day.sessions[-1]
        self.assertIsNone(evening.ended_at)
        self.assertEqual(evening.minutes, 0)
        self.assertTrue(evening.needs_review)


class ShiftThresholdTests(TestCase):
    """The two shift settings A5 put on the form."""

    def test_overtime_does_not_start_until_the_shift_says_so(self):
        """"Minutes after the shift's end before overtime counts."""
        day = pairing.build_day(
            [at(9), at(18, 20)], is_closed=True,
            scheduled_start=at(9), scheduled_end=at(18),
            overtime_after_minutes=30,
        )
        self.assertEqual(day.overtime_minutes, 0)
        self.assertEqual(day.worked_minutes, 540)

    def test_overtime_is_measured_from_the_end_plus_the_delay(self):
        """Staying to 19:00 on a 30-minute delay is 30 minutes, not 60."""
        day = pairing.build_day(
            [at(9), at(19)], is_closed=True,
            scheduled_start=at(9), scheduled_end=at(18),
            overtime_after_minutes=30,
        )
        self.assertEqual(day.overtime_minutes, 30)

    def test_a_zero_delay_counts_overtime_straight_away(self):
        day = pairing.build_day(
            [at(9), at(19)], is_closed=True,
            scheduled_start=at(9), scheduled_end=at(18),
            overtime_after_minutes=0,
        )
        self.assertEqual(day.overtime_minutes, 60)

    def test_leaving_inside_the_out_grace_is_not_early(self):
        day = pairing.build_day(
            [at(9), at(17, 50)], is_closed=True,
            scheduled_start=at(9), scheduled_end=at(18),
            grace_out_minutes=15,
        )
        self.assertEqual(day.early_out_minutes, 0)

    def test_leaving_beyond_the_out_grace_is_early(self):
        day = pairing.build_day(
            [at(9), at(17, 30)], is_closed=True,
            scheduled_start=at(9), scheduled_end=at(18),
            grace_out_minutes=15,
        )
        self.assertEqual(day.early_out_minutes, 30)


class PaidBreakTests(TestCase):
    def test_a_paid_break_is_credited_back_capped_at_the_allowance(self):
        day = pairing.build_day(
            [at(9), at(13), at(14, 30), at(18)], is_closed=True,
            scheduled_start=at(9), scheduled_end=at(18),
            break_minutes=60, break_is_paid=True,
        )
        self.assertEqual(day.outside_minutes, 90)
        self.assertEqual(day.worked_minutes, 450 + 60)

    def test_an_unpaid_break_is_simply_missing_time(self):
        day = pairing.build_day(
            [at(9), at(13), at(14), at(18)], is_closed=True,
            scheduled_start=at(9), scheduled_end=at(18),
            break_minutes=60, break_is_paid=False,
        )
        self.assertEqual(day.worked_minutes, 480)


class DayWindowTests(TestCase):
    """When one day stops taking scans and the next starts."""

    def windows(self, shifts, *, window_before=120):
        days = sorted(shifts)
        return day_window.build_windows(
            days=days, shift_for=lambda d: shifts[d], tz=TZ,
            window_before_minutes=window_before,
        )

    def test_a_day_closes_at_the_next_shift_start(self):
        days = {
            datetime.date(2026, 8, 10): shift(),
            datetime.date(2026, 8, 11): shift(),
        }
        windows = self.windows(days)
        monday = windows[datetime.date(2026, 8, 10)]
        # Tuesday's window opens 07:00 (09:00 less two hours), so Monday ends.
        self.assertEqual(monday.closes_at, at(7, day=11))

    def test_the_cap_is_twenty_four_hours_after_the_shift_started(self):
        """Nothing after that belongs to the day, however long the gap."""
        days = {
            datetime.date(2026, 8, 10): shift(),
            datetime.date(2026, 8, 11): None,
            datetime.date(2026, 8, 12): None,
            datetime.date(2026, 8, 13): shift(),
        }
        windows = self.windows(days)
        monday = windows[datetime.date(2026, 8, 10)]
        self.assertEqual(monday.closes_at, at(9, day=11))

    def test_a_day_off_owns_the_rest_of_the_gap(self):
        """Coming in on a day off is working that day, not the day before."""
        days = {
            datetime.date(2026, 8, 10): shift(),
            datetime.date(2026, 8, 11): None,
            datetime.date(2026, 8, 12): shift(),
        }
        windows = self.windows(days)
        off = windows[datetime.date(2026, 8, 11)]
        self.assertEqual(off.opens_at, at(9, day=11))    # the previous cap
        self.assertEqual(off.closes_at, at(7, day=12))   # the next shift window
        self.assertIsNone(off.shift)

    def test_a_night_shift_keeps_its_morning_check_out(self):
        days = {
            datetime.date(2026, 8, 10): shift(start=22, end=6, spans=True),
            datetime.date(2026, 8, 11): shift(start=22, end=6, spans=True),
        }
        windows = self.windows(days)
        night = windows[datetime.date(2026, 8, 10)]
        self.assertEqual(night.scheduled_end, at(6, day=11))
        # Closes when the next night shift's window opens, at 20:00.
        self.assertEqual(night.closes_at, at(20, day=11))
        self.assertTrue(night.contains(at(6, day=11)))

    def test_two_shifts_in_one_day_do_not_share_scans(self):
        """A second shift ends the first day as soon as its window opens."""
        days = {
            datetime.date(2026, 8, 10): shift(start=6, end=14),
            datetime.date(2026, 8, 11): shift(start=6, end=14),
        }
        windows = self.windows(days, window_before=60)
        first = windows[datetime.date(2026, 8, 10)]
        self.assertEqual(first.closes_at, at(5, day=11))

    def test_the_windows_are_contiguous_so_no_scan_is_lost(self):
        days = {
            datetime.date(2026, 8, 10): shift(),
            datetime.date(2026, 8, 11): None,
            datetime.date(2026, 8, 12): shift(),
            datetime.date(2026, 8, 13): shift(),
        }
        windows = self.windows(days)
        ordered = [windows[d] for d in sorted(days)]
        for earlier, later in zip(ordered, ordered[1:]):
            self.assertEqual(earlier.closes_at, later.opens_at)

    def test_a_window_knows_whether_it_has_closed(self):
        days = {
            datetime.date(2026, 8, 10): shift(),
            datetime.date(2026, 8, 11): shift(),
        }
        monday = self.windows(days)[datetime.date(2026, 8, 10)]
        self.assertFalse(monday.is_closed(at(20)))
        self.assertTrue(monday.is_closed(at(8, day=11)))
