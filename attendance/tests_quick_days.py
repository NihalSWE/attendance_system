"""Today and Yesterday on the Daily list and Late entries (2026-10-24)."""

import datetime
from zoneinfo import ZoneInfo

from django.urls import reverse
from django.utils import timezone

from attendance import tests_live


class QuickDayTests(tests_live.LiveTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        zone = ZoneInfo(self.company.timezone or "UTC")
        self.today = timezone.now().astimezone(zone).date()
        self.yesterday = self.today - datetime.timedelta(days=1)

    def test_both_buttons_point_at_their_day(self):
        page = self.client.get(reverse("attendance:attendance_list"))
        self.assertContains(page, f"on={self.today.isoformat()}")
        self.assertContains(page, f"on={self.yesterday.isoformat()}")
        self.assertContains(page, ">\n                    Today")
        self.assertContains(page, ">\n                    Yesterday")

    def test_yesterday_shows_that_day_and_is_marked(self):
        self.punch(self.yesterday, 9)
        self.punch(self.yesterday, 18)
        page = self.client.get(reverse("attendance:attendance_list"),
                               {"on": self.yesterday.isoformat()})
        self.assertEqual(page.context["date_window"], (self.yesterday, self.yesterday))
        quick = {q["label"]: q for q in page.context["quick_days"]}
        self.assertTrue(quick["Yesterday"]["active"])
        self.assertFalse(quick["Today"]["active"])
        self.assertContains(page, 'aria-current="true"')

    def test_other_filters_are_kept(self):
        page = self.client.get(reverse("attendance:attendance_list"),
                               {"status": "present", "branch": self.branch.pk,
                                "date_from": "2026-01-01", "date_to": "2026-01-31"})
        url = {q["label"]: q["url"] for q in page.context["quick_days"]}["Today"]
        self.assertIn("status=present", url)
        self.assertIn(f"branch={self.branch.pk}", url)
        self.assertNotIn("date_from", url)

    def test_late_entries_has_them_too(self):
        page = self.client.get(reverse("attendance:attendance_late"))
        self.assertEqual([q["label"] for q in page.context["quick_days"]], ["Today", "Yesterday"])
