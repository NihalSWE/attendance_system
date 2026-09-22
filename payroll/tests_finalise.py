"""A11 part 1: finalise a month's salary, and undo it for a mistake."""

import datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from accounts.models import CompanyMembership, User
from attendance.services import locked_ranges
from payroll.models import PayrollRun
from payroll.services import generate_payroll, reopen_payroll
from payroll.tests_approval import finalise_payroll
from payroll.tests_overtime import OvertimeBase

AUGUST = (datetime.date(2026, 8, 1), datetime.date(2026, 8, 31))


class FinaliseTests(OvertimeBase):
    def month(self, service, **extra):
        return service(actor=extra.pop("actor", self.admin), company_id=self.company.pk,
                       year=2026, month=8, **extra)

    def test_finalise_locks_the_month_and_undo_needs_a_reason(self):
        self.work(datetime.date(2026, 8, 10), (9, 0), (18, 0))
        with self.assertRaises(ValidationError):
            self.month(finalise_payroll)
        self.month(generate_payroll)
        hr = User.objects.create_user(email="hr@finalise.test")
        CompanyMembership.all_objects.create(company=self.company, user=hr, role="hr", status="active")
        with self.assertRaises(PermissionDenied):
            self.month(finalise_payroll, actor=hr)

        run = self.month(finalise_payroll)
        self.assertEqual((run.status, run.posted_by), (PayrollRun.Status.POSTED, self.admin))
        self.assertIn(AUGUST, locked_ranges(self.company.pk))
        with self.assertRaises(ValidationError):
            self.month(generate_payroll)
        with self.assertRaises(ValidationError):
            self.month(finalise_payroll)

        with self.assertRaises(ValidationError):
            self.month(reopen_payroll, reason=" ")
        run = self.month(reopen_payroll, reason="Wrong salary for Rahim")
        self.assertEqual((run.status, run.posted_at), (PayrollRun.Status.DRAFT, None))
        self.assertNotIn(AUGUST, locked_ranges(self.company.pk))
        self.month(generate_payroll)

    def test_a_draft_older_than_an_overtime_decision_cannot_be_finalised(self):
        record = self.work(datetime.date(2026, 8, 12), (9, 0), (20, 0))
        self.month(generate_payroll)
        self.decide(record, approve=False, note="Not approved")
        with self.assertRaises(ValidationError):
            self.month(finalise_payroll)
        self.month(generate_payroll)
        self.assertEqual(self.month(finalise_payroll).status, PayrollRun.Status.POSTED)

    def test_salary_page_buttons_and_confirmation_pages(self):
        self.work(datetime.date(2026, 8, 10), (9, 0), (18, 0))
        self.month(generate_payroll)
        self.client.force_login(self.admin)
        home = reverse("payroll:payroll_home") + "?month=8&year=2026"
        # Submit, then approve (a single owner/admin approves their own).
        submit = reverse("payroll:payroll_submit") + "?month=8&year=2026"
        self.assertContains(self.client.get(home), submit)
        self.assertRedirects(self.client.post(reverse("payroll:payroll_submit"), {"month": 8, "year": 2026}), home)
        finalise = reverse("payroll:payroll_finalise") + "?month=8&year=2026"
        self.assertContains(self.client.get(home), finalise)
        self.assertEqual(self.client.get(finalise).status_code, 200)
        self.assertRedirects(self.client.post(reverse("payroll:payroll_finalise"), {"month": 8, "year": 2026}), home)
        page = self.client.get(home)
        self.assertContains(page, "Finalised")
        self.assertContains(page, "Undo finalise")
        self.assertNotContains(page, "Regenerate August")
        undo = reverse("payroll:payroll_reopen")
        self.assertContains(self.client.post(undo, {"month": 8, "year": 2026, "reason": ""}), "This field is required")
        self.assertRedirects(self.client.post(undo, {"month": 8, "year": 2026, "reason": "Mistake"}), home)
        self.assertContains(self.client.get(home), "Regenerate August")
