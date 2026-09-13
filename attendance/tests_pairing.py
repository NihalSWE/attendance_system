"""The pairing rule: DEVICE_ATTENDANCE_POLICY.md processing step 7.

The rule reads simply — scans alternate IN, OUT across the whole day, the
first IN is check-in, the last OUT is check-out, and everything between is a
break — but almost every interesting case is a day that does not look like
that: an odd number of scans, two devices, a scan repeated because the first
beep was missed. Those are what this file is mostly about.

``pairing`` is deliberately free of the database, so the rule can be checked
directly instead of through a month of fixtures.
"""

import datetime
from decimal import Decimal

from django.test import TestCase

from attendance import pairing

TZ = datetime.timezone.utc


def at(hour, minute=0, second=0):
    return datetime.datetime(2026, 8, 10, hour, minute, second, tzinfo=TZ)


def labels(day):
    return [scan.label for scan in day.kept]


class AlternationTests(TestCase):
    """Which scan is which, before any minutes are counted."""

    def test_two_scans_are_a_check_in_and_a_check_out(self):
        day = pairing.build_day([at(9), at(18)])
        self.assertEqual(labels(day), ["check_in", "check_out"])
        self.assertTrue(day.has_check_out)

    def test_four_scans_are_a_day_with_one_break(self):
        day = pairing.build_day([at(9), at(13), at(14), at(18)])
        self.assertEqual(
            labels(day), ["check_in", "break_out", "break_in", "check_out"]
        )
        self.assertEqual(day.break_count, 1)

    def test_six_scans_are_a_day_with_two_breaks(self):
        day = pairing.build_day([at(9), at(11), at(11, 15), at(13), at(14), at(18)])
        self.assertEqual(
            labels(day),
            ["check_in", "break_out", "break_in", "break_out", "break_in",
             "check_out"],
        )
        self.assertEqual(day.break_count, 2)

    def test_a_single_scan_is_a_check_in_with_no_check_out(self):
        day = pairing.build_day([at(9)])
        self.assertEqual(labels(day), ["check_in"])
        self.assertFalse(day.has_check_out)
        self.assertIsNone(day.last_out_at)

    def test_an_odd_day_ends_on_a_break_in_and_has_no_check_out(self):
        """Out for lunch, came back, never left: the day is unfinished."""
        day = pairing.build_day([at(9), at(13), at(14)])
        self.assertEqual(labels(day), ["check_in", "break_out", "break_in"])
        self.assertFalse(day.has_check_out)
        self.assertEqual(day.break_count, 1)

    def test_no_scans_is_an_empty_day(self):
        day = pairing.build_day([])
        self.assertEqual(labels(day), [])
        self.assertEqual(day.worked_minutes, 0)
        self.assertIsNone(day.first_in_at)

    def test_scans_are_ordered_by_time_not_by_arrival(self):
        """Batches can reach us out of order; the day is still chronological."""
        day = pairing.build_day([at(18), at(9), at(14), at(13)])
        self.assertEqual(
            labels(day), ["check_in", "break_out", "break_in", "check_out"]
        )
        self.assertEqual(day.first_in_at, at(9))
        self.assertEqual(day.last_out_at, at(18))

    def test_the_device_is_never_asked_which_direction_a_scan_was(self):
        """Alternation is the only source of a label.

        The scans carry punch ids from two different terminals and the result
        is one clean day — no per-device counter, no restart.
        """
        day = pairing.build_day(
            [(at(9), 101), (at(13), 202), (at(14), 101), (at(18), 202)]
        )
        self.assertEqual(
            labels(day), ["check_in", "break_out", "break_in", "check_out"]
        )
        self.assertEqual([s.punch_event_id for s in day.kept], [101, 202, 101, 202])


class RepeatScanTests(TestCase):
    """A second scan inside the window is the same arrival, not a break."""

    def test_a_repeat_inside_the_window_is_dropped(self):
        day = pairing.build_day(
            [at(9), at(9, 0, 20), at(18)], window_seconds=30
        )
        self.assertEqual(labels(day), ["check_in", "check_out"])
        self.assertEqual(len(day.dropped), 1)
        self.assertEqual(day.dropped[0][0], at(9, 0, 20))

    def test_a_scan_outside_the_window_is_kept(self):
        day = pairing.build_day([at(9), at(9, 1), at(18)], window_seconds=30)
        self.assertEqual(len(day.kept), 3)
        self.assertEqual(day.dropped, [])

    def test_a_burst_of_repeats_is_one_arrival(self):
        """Each kept scan restarts the window, so a stutter never pairs up."""
        day = pairing.build_day(
            [at(9), at(9, 0, 4), at(9, 0, 8), at(9, 0, 12), at(18)],
            window_seconds=5,
        )
        self.assertEqual(labels(day), ["check_in", "check_out"])
        self.assertEqual(len(day.dropped), 3)

    def test_a_zero_window_keeps_everything(self):
        day = pairing.build_day([at(9), at(9, 0, 1), at(18)], window_seconds=0)
        self.assertEqual(len(day.kept), 3)

    def test_repeats_would_otherwise_invent_a_break(self):
        """Why the window exists at all: without it the day reads wrong."""
        moments = [at(9), at(9, 0, 10), at(18)]
        unfiltered = pairing.build_day(moments, window_seconds=0)
        filtered = pairing.build_day(moments, window_seconds=30)
        self.assertFalse(unfiltered.has_check_out)
        self.assertTrue(filtered.has_check_out)


class MinuteTests(TestCase):
    """Total, in-office and outside, all from the same pairing."""

    def test_a_day_with_no_break_is_all_in_office(self):
        day = pairing.build_day([at(9), at(17)])
        self.assertEqual(day.total_minutes, 480)
        self.assertEqual(day.in_office_minutes, 480)
        self.assertEqual(day.outside_minutes, 0)
        self.assertEqual(day.worked_minutes, 480)

    def test_a_break_comes_out_of_in_office_time(self):
        day = pairing.build_day([at(9), at(13), at(14), at(18)])
        self.assertEqual(day.total_minutes, 540)      # 09:00 -> 18:00
        self.assertEqual(day.in_office_minutes, 480)  # 4h + 4h
        self.assertEqual(day.outside_minutes, 60)     # 13:00 -> 14:00
        self.assertEqual(day.worked_minutes, 480)     # break is unpaid

    def test_total_always_equals_in_office_plus_outside(self):
        """The invariant that keeps the three figures from drifting apart."""
        for moments in (
            [at(9), at(18)],
            [at(9), at(13), at(14), at(18)],
            [at(9), at(11), at(11, 20), at(13), at(13, 45), at(18)],
        ):
            with self.subTest(scans=len(moments)):
                day = pairing.build_day(moments)
                self.assertEqual(
                    day.total_minutes,
                    day.in_office_minutes + day.outside_minutes,
                )

    def test_an_unfinished_day_has_no_total(self):
        """Nothing is invented for a day that has not ended."""
        day = pairing.build_day([at(9), at(13), at(14)])
        self.assertEqual(day.total_minutes, 0)
        self.assertEqual(day.in_office_minutes, 240)
        self.assertEqual(day.outside_minutes, 60)

    def test_sessions_are_the_in_office_stretches(self):
        day = pairing.build_day([at(9), at(13), at(14), at(18)])
        self.assertEqual(len(day.sessions), 2)
        self.assertEqual(
            [(s.started_at.hour, s.ended_at.hour, s.minutes) for s in day.sessions],
            [(9, 13, 240), (14, 18, 240)],
        )
        self.assertTrue(all(s.is_complete for s in day.sessions))

    def test_the_last_session_of_an_unfinished_day_is_left_open(self):
        day = pairing.build_day([at(9), at(13), at(14)])
        self.assertFalse(day.sessions[-1].is_complete)
        self.assertIsNone(day.sessions[-1].ended_at)
        self.assertEqual(day.sessions[-1].minutes, 0)


class PaidBreakTests(TestCase):
    """Shift.default_break_minutes and Shift.break_is_paid."""

    def test_an_unpaid_break_is_not_worked_time(self):
        day = pairing.build_day(
            [at(9), at(13), at(14), at(18)],
            break_minutes=60, break_is_paid=False,
        )
        self.assertEqual(day.worked_minutes, 480)

    def test_a_paid_break_is_credited_back(self):
        day = pairing.build_day(
            [at(9), at(13), at(14), at(18)],
            break_minutes=60, break_is_paid=True,
        )
        self.assertEqual(day.worked_minutes, 540)

    def test_a_paid_break_is_capped_at_the_shift_allowance(self):
        """Ninety minutes out on a sixty-minute paid break costs thirty."""
        day = pairing.build_day(
            [at(9), at(13), at(14, 30), at(18)],
            break_minutes=60, break_is_paid=True,
        )
        self.assertEqual(day.outside_minutes, 90)
        self.assertEqual(day.in_office_minutes, 450)
        self.assertEqual(day.worked_minutes, 510)

    def test_a_short_break_is_credited_only_for_what_was_taken(self):
        """Twenty minutes out on a sixty-minute allowance credits twenty."""
        day = pairing.build_day(
            [at(9), at(13), at(13, 20), at(18)],
            break_minutes=60, break_is_paid=True,
        )
        self.assertEqual(day.worked_minutes, day.in_office_minutes + 20)

    def test_a_paid_break_never_credits_a_day_with_no_break(self):
        day = pairing.build_day([at(9), at(17)], break_minutes=60, break_is_paid=True)
        self.assertEqual(day.worked_minutes, 480)
