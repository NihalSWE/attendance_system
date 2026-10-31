"""Attendance -> Summary: the page and its side panel (2026-10-31, payroll/summary_views.py)."""

import datetime

from django.urls import reverse

from accounts.models import CompanyMembership, User
from attendance.services import recalculate
from payroll.models import DaysOverride
from payroll.services import generate_payroll, submit_payroll
from payroll.tests_overtime import LATER, OvertimeBase

D = datetime.date
FETCH = {"HTTP_X_REQUESTED_WITH": "fetch"}
AUGUST = {"year": "2026", "month": "8"}


class SummaryPageTests(OvertimeBase):
    def setUp(self):
        super().setUp()
        self.work(D(2026, 8, 10), (9, 0), (18, 0))
        recalculate(self.company.pk, start=D(2026, 8, 1), end=D(2026, 8, 31), now=LATER)
        self.client.force_login(self.admin)
        self.page = reverse("payroll:attendance_summary")

    def test_the_list_and_the_panel(self):
        page = self.client.get(self.page, {**AUGUST, "show": "all"})
        self.assertContains(page, "Attendance summary")
        self.assertContains(page, "Rahim")
        self.assertContains(page, '<span class="ui-chip">Scans</span>', html=True)
        panel = self.client.get(reverse("payroll:attendance_summary_person", args=[self.employee.pk]), AUGUST)
        self.assertContains(panel, "From the scans")
        self.assertContains(panel, 'name="present_days"')
        self.assertContains(panel, "No changes: the salary follows the scans.")
        # The menu has it.
        self.assertContains(page, f'href="{self.page}"')

    def test_save_mark_and_back_to_scans(self):
        back = f"{self.page}?year=2026&month=8&show=all"
        save = reverse("payroll:attendance_summary_save", args=[self.employee.pk])
        reply = self.client.post(save, {**AUGUST, "present_days": "99", "reason": "", "back": back}, **FETCH).json()
        self.assertFalse(reply["ok"])
        self.assertEqual(set(reply["errors"]), {"present_days", "reason"})
        reply = self.client.post(save, {**AUGUST, "present_days": "20", "reason": "Register checked",
                                        "back": back}, **FETCH).json()
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["redirect"], f"{back}&changed={self.employee.pk}")
        page = self.client.get(reply["redirect"])
        self.assertContains(page, ">By hand</span>")
        self.assertContains(page, "summary__row is-hand is-changed")
        panel = self.client.get(reverse("payroll:attendance_summary_person", args=[self.employee.pk]), AUGUST)
        self.assertContains(panel, "Back to scans")
        self.assertContains(panel, "→ 20 present days")
        reply = self.client.post(reverse("payroll:attendance_summary_revert", args=[self.employee.pk]),
                                 {**AUGUST, "back": "https://elsewhere.example/"}, **FETCH).json()
        self.assertTrue(reply["ok"], reply)
        self.assertTrue(reply["redirect"].startswith(self.page))
        self.assertEqual(DaysOverride.all_objects.get().status, "reverted")

    def test_locked_and_who_may(self):
        generate_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        submit_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        page = self.client.get(self.page, {**AUGUST, "show": "all"})
        self.assertContains(page, "the days can be seen, not changed")
        panel = self.client.get(reverse("payroll:attendance_summary_person", args=[self.employee.pk]), AUGUST)
        self.assertNotContains(panel, 'name="present_days"')
        nobody = User.objects.create_user(email="emp@liv.test", password="pw")
        CompanyMembership.all_objects.create(company=self.company, user=nobody,
                                             role=CompanyMembership.Role.EMPLOYEE,
                                             status=CompanyMembership.Status.ACTIVE)
        self.client.force_login(nobody)
        self.assertIn(self.client.get(self.page, AUGUST).status_code, (302, 403))
