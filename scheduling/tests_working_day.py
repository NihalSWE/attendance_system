"""A weekly-off date worked as a normal day (2026-10-17).

A Friday weekly off the company works stayed "Weekly off": everybody paid for
it as a day off, every minute in the office overtime at the day-off rate, and
nobody absent. A working day makes that one date an ordinary working day.
Holidays and weekly offs added later also reach days already worked out.
"""

import datetime
from unittest import mock

from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from accounts.models import CompanyMembership, User
from attendance.models import AttendanceRecord
from attendance.services import refresh
from attendance.tests_live import LiveTestCase
from auditlog.models import AuditLog
from common.tenant import use_company
from employees.models import EmployeeAssignment
from leaves.models import LeaveRequest, LeaveRequestSegment, LeaveType, PayType
from organization.models import Branch
from payroll.models import OvertimeDecision
from scheduling import services as schedule
from scheduling.calendar import HOLIDAY, WEEKLY_OFF, WORKING, WorkCalendar
from scheduling.models import WorkingDay

S = AttendanceRecord.AttendanceStatus
FRIDAY = datetime.date(2026, 9, 11)          # a past Friday


class WorkingDayCase(LiveTestCase):
    def setUp(self):
        super().setUp()
        schedule.add_weekly_offs(actor=self.admin, company_id=self.company.pk, values={
            "weekdays": [FRIDAY.weekday()], "effective_from": datetime.date(2026, 1, 1)})

    def read(self, day=FRIDAY):
        refresh(self.company.pk, start=day, end=day)
        return self.record(day)

    def add(self, day=FRIDAY, **values):
        return schedule.add_working_day(actor=self.admin, company_id=self.company.pk,
                                        values={"work_date": day, "reason": "Factory works",
                                                **values})

    def kind(self, day=FRIDAY, branch_id=None):
        return WorkCalendar(self.company.pk, day, day).day(branch_id or self.branch.pk, day).kind


class CalendarTests(WorkingDayCase):
    def test_a_working_day_beats_the_weekly_off(self):
        self.assertEqual(self.kind(), WEEKLY_OFF)
        self.add()
        self.assertEqual(self.kind(), WORKING)
        self.assertEqual(self.kind(FRIDAY + datetime.timedelta(days=7)), WEEKLY_OFF)

    def test_a_holiday_still_wins(self):
        self.add()
        schedule.create_holiday(actor=self.admin, company_id=self.company.pk, values={
            "holiday_date": FRIDAY, "name": "Special"})
        self.assertEqual(self.kind(), HOLIDAY)

    def test_a_branch_working_day_only_moves_that_branch(self):
        with use_company(self.company):
            other = Branch.objects.create(company=self.company, code="B2", name="Shed 2")
        self.add(branch=other)
        self.assertEqual(self.kind(branch_id=other.pk), WORKING)
        self.assertEqual(self.kind(), WEEKLY_OFF)


class AttendanceTests(WorkingDayCase):
    def test_scans_on_the_friday_are_a_normal_day(self):
        self.punch(FRIDAY, 9)
        self.punch(FRIDAY, 20)
        before = self.read()
        self.assertEqual(before.attendance_status, S.WEEKLY_OFF)
        self.assertEqual(before.approved_overtime_minutes, 660)   # every minute, day off
        self.add()
        after = self.read()
        self.assertEqual(after.attendance_status, S.PRESENT)
        self.assertEqual(after.approved_overtime_minutes, 120)    # only after 18:00

    def test_coming_late_is_late(self):
        self.punch(FRIDAY, 9, 40)
        self.punch(FRIDAY, 18)
        self.add()
        self.assertGreater(self.read().late_minutes, 0)

    def test_nobody_came_is_absent(self):
        self.assertEqual(self.read().attendance_status, S.WEEKLY_OFF)
        self.add()
        self.assertEqual(self.read().attendance_status, S.ABSENT)

    def test_cancelling_makes_it_a_weekly_off_again(self):
        working = self.add()
        self.assertEqual(self.read().attendance_status, S.ABSENT)
        schedule.cancel_working_day(actor=self.admin, company_id=self.company.pk,
                                    working_day_id=working.pk)
        self.assertEqual(self.read().attendance_status, S.WEEKLY_OFF)
        with use_company(self.company):
            self.assertEqual(WorkingDay.objects.get(pk=working.pk).status, "cancelled")


class RulesTests(WorkingDayCase):
    def assert_refused(self, words, **values):
        with self.assertRaises(ValidationError) as caught:
            self.add(**values)
        self.assertIn(words, " ".join(caught.exception.messages))

    def test_only_a_weekly_off_date(self):
        self.assert_refused("is not a weekly off", work_date=FRIDAY - datetime.timedelta(days=1))

    def test_not_a_holiday(self):
        schedule.create_holiday(actor=self.admin, company_id=self.company.pk, values={
            "holiday_date": FRIDAY, "name": "Special"})
        self.assert_refused("Cancel the holiday instead")

    def test_not_twice(self):
        self.add()
        self.assert_refused("already a working day")

    def test_not_in_a_posted_month(self):
        posted = [(datetime.date(2026, 9, 1), datetime.date(2026, 9, 30))]
        with mock.patch("attendance.services.locked_ranges", return_value=posted):
            self.assert_refused("is posted")

    def test_overtime_already_decided_is_dropped(self):
        with use_company(self.company):
            OvertimeDecision.objects.create(
                company=self.company, employee=self.employee, work_date=FRIDAY,
                status="approved", calculated_minutes=480, approved_minutes=480,
                decided_by=self.admin, decided_at=timezone.now())
        working = self.add()
        self.assertEqual(len(working.cleared_overtime), 1)
        with use_company(self.company):
            self.assertFalse(OvertimeDecision.objects.filter(work_date=FRIDAY).exists())
            event = AuditLog.objects.filter(action="working_day.added").latest("pk")
        self.assertEqual(event.after_data["overtime_decisions_cleared"][0]["approved_minutes"], 480)

    def test_leave_around_the_date_is_named(self):
        with use_company(self.company):
            leave_type = LeaveType.objects.create(company=self.company, code="CL", name="Casual")
            request = LeaveRequest.objects.create(
                company=self.company, employee=self.employee, status="approved",
                submission_assignment=EmployeeAssignment.objects.get(employee=self.employee))
            LeaveRequestSegment.objects.create(
                company=self.company, leave_request=request, leave_type=leave_type,
                start_date=FRIDAY - datetime.timedelta(days=1),
                end_date=FRIDAY + datetime.timedelta(days=1),
                requested_pay_type=PayType.values[0])
        working = self.add()
        self.assertEqual(working.leave_around, ["Rahim (10 Sep to 12 Sep)"])

    def test_only_structure_managers(self):
        hr = User.objects.create_user(email="hr@liv.test", password="pw")
        CompanyMembership.all_objects.create(
            company=self.company, user=hr, role=CompanyMembership.Role.HR,
            status=CompanyMembership.Status.ACTIVE)
        with self.assertRaises(Exception):
            schedule.add_working_day(actor=hr, company_id=self.company.pk,
                                     values={"work_date": FRIDAY, "reason": "x"})


class CalendarChangesRebuildTests(WorkingDayCase):
    """Days already worked out follow a holiday or weekly off added later."""

    def setUp(self):
        super().setUp()
        self.thursday = FRIDAY - datetime.timedelta(days=1)

    def test_a_holiday_added_later(self):
        self.assertEqual(self.read(self.thursday).attendance_status, S.ABSENT)
        schedule.create_holiday(actor=self.admin, company_id=self.company.pk, values={
            "holiday_date": self.thursday, "name": "Swap day"})
        self.assertEqual(self.read(self.thursday).attendance_status, S.HOLIDAY)

    def test_a_cancelled_holiday(self):
        holiday = schedule.create_holiday(actor=self.admin, company_id=self.company.pk, values={
            "holiday_date": self.thursday, "name": "Swap day"})
        self.assertEqual(self.read(self.thursday).attendance_status, S.HOLIDAY)
        schedule.cancel_holiday(actor=self.admin, company_id=self.company.pk,
                                holiday_id=holiday.pk)
        self.assertEqual(self.read(self.thursday).attendance_status, S.ABSENT)

    def test_a_weekly_off_stopped_later(self):
        self.assertEqual(self.read().attendance_status, S.WEEKLY_OFF)
        with use_company(self.company):
            from scheduling.models import WeeklyOffRule
            rule = WeeklyOffRule.objects.get(weekday=FRIDAY.weekday())
        schedule.end_weekly_off(actor=self.admin, company_id=self.company.pk,
                                rule_id=rule.pk, effective_to=FRIDAY)
        self.assertEqual(self.read().attendance_status, S.ABSENT)


class ScreenTests(WorkingDayCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        self.url = reverse("scheduling:working_day_list")

    def test_the_list_and_its_links(self):
        self.add()
        page = self.client.get(self.url, {"year": 2026})
        self.assertContains(page, "Working days in 2026")
        self.assertContains(page, "Factory works")
        self.assertContains(page, "1 of 1")
        holidays = self.client.get(reverse("scheduling:holiday_list"))
        self.assertContains(holidays, self.url)

    def test_adding_by_the_form(self):
        response = self.client.post(self.url, {
            "work_date": FRIDAY.isoformat(), "branch": "", "reason": "Order rush"})
        self.assertRedirects(response, f"{self.url}?year=2026", fetch_redirect_response=False)
        with use_company(self.company):
            self.assertTrue(WorkingDay.objects.filter(work_date=FRIDAY, reason="Order rush")
                            .exists())
        page = self.client.get(response["Location"])
        self.assertContains(page, "is now a working day")

    def test_a_refused_form_reopens_the_modal(self):
        thursday = FRIDAY - datetime.timedelta(days=1)
        page = self.client.post(self.url, {
            "work_date": thursday.isoformat(), "branch": "", "reason": "Order rush"})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'data-open-on-load="working-day-dialog"')
        self.assertContains(page, "is not a weekly off")

    def test_cancelling_by_the_form(self):
        working = self.add()
        response = self.client.post(reverse("scheduling:working_day_cancel", args=[working.pk]))
        self.assertEqual(response.status_code, 302)
        with use_company(self.company):
            self.assertEqual(WorkingDay.objects.get(pk=working.pk).status, "cancelled")

    def test_hr_sees_the_list_but_cannot_add(self):
        hr = User.objects.create_user(email="hr2@liv.test", password="pw")
        CompanyMembership.all_objects.create(
            company=self.company, user=hr, role=CompanyMembership.Role.HR,
            status=CompanyMembership.Status.ACTIVE)
        self.client.force_login(hr)
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'id="working-day-dialog"')
        refused = self.client.post(self.url, {
            "work_date": FRIDAY.isoformat(), "branch": "", "reason": "x"})
        self.assertEqual(refused.status_code, 403)
