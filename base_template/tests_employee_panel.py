"""A7 employee panel: an employee's own attendance, leave and payslips."""

import datetime
from decimal import Decimal

from django.urls import reverse

from accounts.models import CompanyMembership, User
from attendance.services import recalculate
from attendance.tests_live import LiveTestCase
from common.tenant import use_company
from devices.models import PunchEvent
from employees.services import create_employee
from leaves import services as leave_services
from organization import employee_login
from payroll.models import PayrollRun
from payroll.services import generate_payroll

PASSWORD = "Str0ng-pass-2026"
MONDAY = datetime.date(2026, 8, 10)
LATER = datetime.datetime(2026, 9, 1, 12, tzinfo=datetime.timezone.utc)


class PanelBase(LiveTestCase):
    def setUp(self):
        super().setUp()
        with use_company(self.company):
            placement = self.employee.assignments.first()
        self.other = create_employee(
            company=self.company, first_name="Karim", employee_code="E2", branch=self.branch,
            department=placement.department, designation=placement.designation,
            effective_from=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
            pay_basis="monthly", base_rate=Decimal("20000"),
        )["employee"]
        employee_login.give_login(
            actor=self.admin, company_id=self.company.pk, employee_id=self.employee.pk,
            values={"email": "rahim@liv.test", "password": PASSWORD, "password_confirm": PASSWORD,
                    "role": "employee", "branches": []},
        )
        self.assertTrue(self.client.login(email="rahim@liv.test", password=PASSWORD))

    def work(self, day, *times):
        for hour, minute in times:
            self.punch(day, hour, minute)
        recalculate(self.company.pk, start=day, end=day, now=LATER)


class MyAttendanceTests(PanelBase):
    def test_my_month_is_the_calendar_with_my_own_day_panel(self):
        self.work(MONDAY, (9, 0), (18, 0))
        page = self.client.get(reverse("me:attendance"), {"month": 8, "year": 2026})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "My attendance")
        self.assertContains(page, 'data-day-url-template="/me/attendance/0000-00-00/"')
        self.assertContains(page, 'data-day="2026-08-10"')

        panel = self.client.get(reverse("me:attendance_day", args=["2026-08-10"]))
        self.assertEqual(panel.status_code, 200)
        self.assertContains(panel, "09:00")

    def test_the_day_panel_only_ever_shows_my_own_day(self):
        # Karim scanned on Monday; Rahim did not. Rahim's panel shows none of it.
        self.punch(MONDAY, 9)
        self.punch(MONDAY, 18)
        with use_company(self.company):
            PunchEvent.objects.update(employee=self.other)
        recalculate(self.company.pk, start=MONDAY, end=MONDAY, now=LATER)
        panel = self.client.get(reverse("me:attendance_day", args=["2026-08-10"]))
        self.assertEqual(panel.status_code, 200)
        self.assertNotContains(panel, "Karim")
        self.assertContains(panel, "Absent")

    def test_company_pages_stay_closed(self):
        self.assertRedirects(self.client.get(reverse("attendance:attendance_calendar")), reverse("me:home"))


class MyAccountTests(PanelBase):
    def test_home_has_today_and_the_panel(self):
        page = self.client.get(reverse("me:home"))
        self.assertContains(page, "Today")
        for name in ("me:attendance", "me:leave", "me:payslips"):
            self.assertContains(page, f'href="{reverse(name)}"')

    def test_a_login_without_an_employee_is_told_so(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("me:attendance"))
        self.assertContains(page, "not linked to an employee")


class MyLeaveTests(PanelBase):
    def test_my_leave_lists_my_applications_and_days_taken(self):
        casual = leave_services.create_leave_type(
            actor=self.admin, company_id=self.company.pk,
            values={"code": "CL", "name": "Casual", "description": ""},
        )
        for employee, day in ((self.employee, MONDAY), (self.other, datetime.date(2026, 8, 11))):
            leave_services.record_leave(actor=self.admin, company_id=self.company.pk, values={
                "employee": employee, "leave_type": casual, "start_date": day, "end_date": day,
                "pay_type": "paid", "reason": f"Reason of {employee.first_name}",
            })
        page = self.client.get(reverse("me:leave"), {"year": 2026})
        self.assertContains(page, "Casual")
        self.assertContains(page, "Reason of Rahim")
        self.assertNotContains(page, "Reason of Karim")
        self.assertContains(page, "10 Aug 2026")


class MyPayslipTests(PanelBase):
    def setUp(self):
        super().setUp()
        self.work(MONDAY, (9, 0), (18, 0))
        self.run = generate_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        with use_company(self.company):
            self.mine = self.run.records.get(employee=self.employee)
            self.theirs = self.run.records.get(employee=self.other)

    def test_a_draft_is_not_shown(self):
        self.assertContains(self.client.get(reverse("me:payslips")), "No payslips yet")
        self.assertEqual(self.client.get(reverse("me:payslip", args=[self.mine.pk])).status_code, 404)

    def test_a_finalised_payslip_is_mine_to_read_and_nobody_elses(self):
        with use_company(self.company):
            PayrollRun.objects.filter(pk=self.run.pk).update(status=PayrollRun.Status.POSTED)
        listing = self.client.get(reverse("me:payslips"))
        self.assertContains(listing, "August 2026")
        page = self.client.get(reverse("me:payslip", args=[self.mine.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Net pay")
        self.assertNotContains(page, "Waive")
        self.assertEqual(self.client.get(reverse("me:payslip", args=[self.theirs.pk])).status_code, 404)
