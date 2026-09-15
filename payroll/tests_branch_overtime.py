"""A12 part 5: overtime limited to the viewer's branches."""

from django.core.exceptions import PermissionDenied
from django.urls import reverse

from attendance.models import AttendanceRecord
from attendance.services import recalculate
from common.tenant import use_company
from leaves.tests_branch_access import AUGUST, MONDAY, TwoBranchCase
from payroll import overtime
from payroll.tests_overtime import LATER


class BranchOvertimeTests(TwoBranchCase):
    def setUp(self):
        super().setUp()
        # Both stay two hours after an 18:00 shift end.
        for hour in (9, 20):
            self.punch(MONDAY, hour)
            self.far_punch(MONDAY, hour)
        recalculate(self.company.pk, start=MONDAY, end=MONDAY, now=LATER)
        self.near = self.record(MONDAY)
        with use_company(self.company):
            self.far_day = AttendanceRecord.objects.get(employee=self.far, work_date=MONDAY)

    def day_url(self, record):
        return reverse("payroll:overtime_decide", args=[record.pk])

    def test_a_branch_manager_sees_and_decides_their_branch_only(self):
        self.client.force_login(self.manager)
        page = self.client.get(reverse("payroll:overtime_list"), AUGUST)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Rahim")
        self.assertNotContains(page, "Karim")
        self.assertContains(page, self.day_url(self.near))
        self.assertContains(page, 'data-menu="salary"')
        self.assertEqual(self.client.get(self.day_url(self.far_day)).status_code, 403)
        response = self.client.post(self.day_url(self.near),
                                    {"decision": "approve", "minutes": "60", "note": ""})
        self.assertRedirects(response, reverse("payroll:overtime_list") + "?month=8&year=2026&show=all")

    def test_view_only_access_sees_the_day_but_cannot_decide(self):
        self.grant("overtime.view", self.branch)
        self.client.force_login(self.clerk_user)
        page = self.client.get(reverse("payroll:overtime_list"), AUGUST)
        self.assertContains(page, "Rahim")
        self.assertContains(page, "View")
        day = self.client.get(self.day_url(self.near))
        self.assertEqual(day.status_code, 200)
        self.assertContains(day, "Deciding it needs")
        self.assertNotContains(day, 'value="approve"')
        post = {"decision": "reject", "note": ""}
        self.assertEqual(self.client.post(self.day_url(self.near), post).status_code, 403)
        self.assertEqual(self.client.post(
            reverse("payroll:overtime_undo", args=[self.near.pk])).status_code, 403)

    def test_decide_access_in_another_branch_stays_there(self):
        self.grant("overtime.decide", self.unit)
        overtime.decide_overtime(actor=self.clerk_user, company_id=self.company.pk,
                                 record_id=self.far_day.pk, approve=False)
        with self.assertRaises(PermissionDenied):
            overtime.decide_overtime(actor=self.clerk_user, company_id=self.company.pk,
                                     record_id=self.near.pk, approve=False)

    def test_without_access_the_gate_keeps_an_employee_out(self):
        self.client.force_login(self.clerk_user)
        self.assertRedirects(self.client.get(reverse("payroll:overtime_list")), reverse("me:home"))

    def test_owner_and_hr_are_unchanged(self):
        for user in (self.admin, self.hr):
            self.client.force_login(user)
            page = self.client.get(reverse("payroll:overtime_list"), AUGUST)
            self.assertContains(page, "Rahim")
            self.assertContains(page, "Karim")
            self.assertContains(page, reverse("payroll:payroll_home"))
