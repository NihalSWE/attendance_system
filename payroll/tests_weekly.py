"""Weekly pay groups (2026-10-24, docs/payroll-periods/00-PLAN.md item 4).

Weeks run Saturday to Friday here; 1 Aug 2026 is a Saturday.
"""

import datetime
from decimal import Decimal
from unittest import mock

from django.core.exceptions import ValidationError
from django.urls import reverse

from attendance.services import recalculate
from common.tenant import use_company
from employees.models import EmployeeCompensation
from payroll import paygroups
from payroll.models import PayGroup, PayGroupMember, PayrollRun
from payroll.services import approve_payroll, generate_payroll, money, submit_payroll
from payroll.tests_overtime import LATER, OvertimeBase

D = datetime.date


class WeeklyCase(OvertimeBase):
    def setUp(self):
        super().setUp()
        self.group = paygroups.create_group(actor=self.admin, company_id=self.company.pk,
                                            name="Weekly workers",
                                            week_start=PayGroup.Weekday.SATURDAY)

    def daily(self, rate="1000"):
        with use_company(self.company):
            EmployeeCompensation.objects.filter(employee=self.employee).update(
                pay_basis="daily", base_rate=Decimal(rate))

    def join(self, day, group="weekly"):
        return paygroups.set_member(actor=self.admin, company_id=self.company.pk,
                                    effective_from=day, employee=self.employee,
                                    pay_group=self.group if group == "weekly" else None)

    def week(self, day):
        return generate_payroll(actor=self.admin, company_id=self.company.pk,
                                pay_group=self.group, week=day)

    def month(self, year=2026, month=8):
        return generate_payroll(actor=self.admin, company_id=self.company.pk,
                                year=year, month=month)

    def pay_record(self, run):
        with use_company(self.company):
            return run.records.filter(employee=self.employee).first()


class WeekTests(WeeklyCase):
    def test_weeks_of_a_month(self):
        self.assertEqual(paygroups.week_of(self.group, D(2026, 8, 12)), (D(2026, 8, 8), D(2026, 8, 14)))
        self.assertEqual(paygroups.weeks_in_month(self.group, 2026, 8),
                         [(D(2026, 8, 1), D(2026, 8, 7)), (D(2026, 8, 8), D(2026, 8, 14)),
                          (D(2026, 8, 15), D(2026, 8, 21)), (D(2026, 8, 22), D(2026, 8, 28))])
        self.assertTrue(paygroups.is_last_week_of_month(D(2026, 8, 28)))
        self.assertFalse(paygroups.is_last_week_of_month(D(2026, 8, 21)))

    def test_a_week_pays_the_days_worked_at_the_daily_rate(self):
        self.daily()
        self.join(D(2026, 8, 1))
        self.work(D(2026, 8, 10), (9, 0), (18, 0))
        self.work(D(2026, 8, 11), (9, 0), (18, 0))
        run = self.week(D(2026, 8, 12))
        self.assertEqual((run.payroll_period.start_date, run.payroll_period.end_date),
                         (D(2026, 8, 8), D(2026, 8, 14)))
        self.assertEqual(run.payroll_period.name, "Week 08 Aug - 14 Aug 2026")
        self.assertEqual(run.pay_group, self.group)
        record = self.pay_record(run)
        with use_company(self.company):
            days = record.lines.get(code="DAYS")
        self.assertEqual((days.quantity, days.amount), (Decimal("2.00"), Decimal("2000.00")))
        # Generating it again replaces it, as for a month.
        again = self.week(D(2026, 8, 9))
        self.assertEqual(again.pk, run.pk)
        self.assertEqual(self.pay_record(again).gross_earnings, record.gross_earnings)

    def test_the_monthly_salary_leaves_weekly_people_out(self):
        self.daily()
        self.join(D(2026, 8, 1))
        self.work(D(2026, 8, 10), (9, 0), (18, 0))
        self.assertIsNone(self.pay_record(self.month()))

    def test_moving_mid_month_splits_the_month(self):
        # Monthly staff until 14 Aug, weekly from Saturday 15 Aug: August's
        # salary pays the days up to the 14th; the weeks pay the rest.
        self.daily()
        self.join(D(2026, 8, 15))
        self.work(D(2026, 8, 10), (9, 0), (18, 0))
        self.work(D(2026, 8, 17), (9, 0), (18, 0))
        self.work(D(2026, 8, 18), (9, 0), (18, 0))
        with use_company(self.company):
            days = self.pay_record(self.month()).lines.get(code="DAYS")
        self.assertEqual(days.amount, Decimal("1000.00"))
        with use_company(self.company):
            days = self.pay_record(self.week(D(2026, 8, 17))).lines.get(code="DAYS")
        self.assertEqual(days.amount, Decimal("2000.00"))

    def test_a_monthly_salary_stays_with_the_monthly_staff(self):
        # Rahim's department goes weekly, but his salary is monthly: the month
        # pays all of it and the week has nobody to pay.
        with use_company(self.company):
            department = self.employee.assignments.first().department
        paygroups.set_member(actor=self.admin, company_id=self.company.pk,
                             effective_from=D(2026, 8, 1), department=department,
                             pay_group=self.group)
        self.work(D(2026, 8, 10), (9, 0), (18, 0))
        with use_company(self.company):
            basic = self.pay_record(self.month()).lines.get(code="BASIC")
        self.assertEqual(basic.amount, Decimal("30000.00"))
        self.assertIsNone(self.pay_record(self.week(D(2026, 8, 10))))

    def test_joining_must_start_a_week_outside_a_finalised_salary(self):
        with self.assertRaises(ValidationError) as caught:
            self.join(D(2026, 8, 10))                    # a Monday
        self.assertIn("choose a Saturday", " ".join(caught.exception.messages))
        self.work(D(2026, 8, 10), (9, 0), (18, 0))
        run = self.month()
        submit_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        approve_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        with use_company(self.company):
            self.assertEqual(PayrollRun.objects.get(pk=run.pk).status, "posted")
        with self.assertRaises(ValidationError) as caught:
            self.join(D(2026, 8, 15))
        self.assertIn("is finalised", " ".join(caught.exception.messages))


class WeekApprovalTests(WeeklyCase):
    def test_a_finalised_week_locks_its_people_only(self):
        self.daily()
        self.join(D(2026, 8, 1))
        record = self.work(D(2026, 8, 10), (9, 0), (18, 0))
        self.week(D(2026, 8, 10))
        submit_payroll(actor=self.admin, company_id=self.company.pk,
                       pay_group=self.group, week=D(2026, 8, 10))
        run = approve_payroll(actor=self.admin, company_id=self.company.pk,
                              pay_group=self.group, week=D(2026, 8, 10))
        self.assertEqual(run.status, "posted")
        # A late scan on that finalised week no longer changes the day.
        self.punch(D(2026, 8, 10), 20, 0)
        recalculate(self.company.pk, start=D(2026, 8, 10), end=D(2026, 8, 10), now=LATER)
        with use_company(self.company):
            again = type(record).objects.get(employee=self.employee, work_date=D(2026, 8, 10))
        self.assertEqual(again.worked_minutes, record.worked_minutes)
        # The month of the Monthly staff is still open.
        self.assertEqual(self.month().status, "draft")


class WeekPageTests(WeeklyCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        self.url = reverse("payroll:pay_groups")
        # "Today" is Wednesday 12 Aug 2026: this week is Sat 08 - Fri 14 Aug.
        today = mock.patch("django.utils.timezone.localdate", return_value=D(2026, 8, 12))
        today.start()
        self.addCleanup(today.stop)
        with use_company(self.company):
            self.department = self.employee.assignments.first().department

    def post(self, **data):
        return self.client.post(self.url, data)

    def test_the_page_lists_each_group_and_says_when_it_is_empty(self):
        page = self.client.get(self.url)
        self.assertContains(page, "Weekly, Saturday to Friday")
        self.assertContains(page, "This week: Sat 08 Aug - Fri 14 Aug 2026.")
        self.assertContains(page, "Nobody is in Weekly workers yet. Press Add people.")
        # The start day is chosen from a list, this week's already chosen.
        self.assertContains(page, '<option value="2026-08-08" selected>Sat 08 Aug 2026 (this week)</option>',
                            html=True)
        self.assertContains(self.client.get(reverse("payroll:salary_settings")), "Open Pay groups")

    def test_adding_a_group(self):
        response = self.post(section="group_add", name="Night shift", week_start="0")
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        page = self.client.get(self.url)
        self.assertContains(page, "Pay group Night shift added: weeks start on Monday.")
        self.assertContains(page, "Weekly, Monday to Sunday")

    def test_adding_a_whole_department_ignores_the_hidden_person_picker(self):
        response = self.post(section="member_add", row=self.group.pk, who="department",
                             department=self.department.pk, employee=self.employee.pk,
                             effective_from="2026-08-08")
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        with use_company(self.company):
            row = PayGroupMember.objects.get()
        self.assertEqual((row.department, row.employee, row.pay_group), (self.department, None, self.group))
        page = self.client.get(self.url)
        self.assertContains(page, f"{self.department.name}: paid weekly in Weekly workers from Sat 08 Aug 2026.")
        self.assertContains(page, "(whole department)")

    def test_a_refused_form_opens_again_with_the_reason(self):
        page = self.post(section="member_add", row=self.group.pk, who="employee",
                         effective_from="2026-08-08")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, f'data-open-on-load="add-{self.group.pk}-dialog"')
        self.assertContains(page, "Choose the person.")
        # A day that is not one of the group's start days cannot be chosen.
        page = self.post(section="member_add", row=self.group.pk, who="employee",
                         employee=self.employee.pk, effective_from="2026-08-10")
        self.assertContains(page, "Select a valid choice")
        with use_company(self.company):
            self.assertFalse(PayGroupMember.objects.exists())

    def test_renaming_and_the_week_start_that_cannot_move_once_used(self):
        response = self.post(section="group_edit", row=self.group.pk, name="Weekly staff",
                             week_start=str(PayGroup.Weekday.SATURDAY))
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.join(D(2026, 8, 8))
        page = self.post(section="group_edit", row=self.group.pk, name="Weekly staff",
                         week_start=str(PayGroup.Weekday.MONDAY))
        self.assertContains(page, "already has people or a salary, so its weeks cannot move")
        with use_company(self.company):
            self.group.refresh_from_db()
        self.assertEqual((self.group.name, self.group.week_start), ("Weekly staff", PayGroup.Weekday.SATURDAY))

    def test_back_to_monthly_and_removing_a_mistake(self):
        row = self.join(D(2026, 8, 8))
        response = self.post(section="member_end", row=row.pk, effective_to="2026-08-15")
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        with use_company(self.company):
            row.refresh_from_db()
        self.assertEqual(row.effective_to, D(2026, 8, 15))
        self.post(section="member_end", row=row.pk, effective_to="2026-08-08")
        with use_company(self.company):
            row.refresh_from_db()
        self.assertEqual(row.status, "cancelled")
        self.assertContains(self.client.get(self.url), "Nobody is in Weekly workers yet.")

    def test_a_finalised_week_is_not_offered(self):
        self.work(D(2026, 8, 10), (9, 0), (18, 0))
        self.month()
        submit_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        approve_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        days = [day for day, _ in paygroups.start_days(self.group, self.company.pk)]
        self.assertNotIn(D(2026, 8, 8), days)
        self.assertEqual(days[0], D(2026, 9, 5))

    def test_generating_and_submitting_a_week_from_the_salary_page(self):
        self.daily()
        self.join(D(2026, 8, 1))
        self.work(D(2026, 8, 10), (9, 0), (18, 0))
        home = reverse("payroll:payroll_home")
        page = self.client.get(home, {"month": 8, "year": 2026, "group": self.group.pk,
                                      "week": "2026-08-08"})
        self.assertContains(page, "Weekly workers: Week 08 Aug - 14 Aug 2026")
        self.assertContains(page, "Generate the week")
        query = f"?month=8&year=2026&group={self.group.pk}&week=2026-08-08"
        response = self.client.post(reverse("payroll:payroll_generate"), {
            "month": 8, "year": 2026, "group": self.group.pk, "week": "2026-08-08"})
        self.assertRedirects(response, home + query, fetch_redirect_response=False)
        page = self.client.get(home + query)
        self.assertContains(page, "Submit the week for approval")
        self.client.post(reverse("payroll:payroll_submit"), {
            "month": 8, "year": 2026, "group": self.group.pk, "week": "2026-08-08"})
        with use_company(self.company):
            run = PayrollRun.objects.get(pay_group=self.group)
        self.assertEqual(run.status, "submitted")
        # The Monthly staff's August is a different salary, still not generated.
        self.assertContains(self.client.get(home, {"month": 8, "year": 2026}),
                            "No salary generated for August 2026")
