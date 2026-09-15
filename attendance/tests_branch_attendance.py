"""A12 part 7 (N10): attendance and the employee page limited to the viewer's branches.

Head Office has Rahim and Clerk; Chittagong has Karim (leaves/tests_branch_access.py).
Both Rahim and Karim scanned in on Monday and never out, so each has a closed
day waiting for review. Every page checks: a branch manager sees their own
branch only; access given in the other branch shows that branch only; without
the code the gate sends an Employee login to My account; owner and HR are
unchanged; and a crafted request the page would never offer is refused by the
service too.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from accounts.models import CompanyMembership
from attendance import correction_services as fixes
from attendance import month_view
from attendance.models import AttendanceCorrection, AttendanceRecord
from attendance.services import recalculate
from auditlog.services import record_company_event
from common.tenant import use_company
from employees.models import Employee
from employees.services import create_employee
from leaves.tests_branch_access import AUGUST, MONDAY, TwoBranchCase
from organization import employee_detail_services as detail

DHAKA = datetime.timezone(datetime.timedelta(hours=6))


def at(day, hour):
    return datetime.datetime(day.year, day.month, day.day, hour, tzinfo=DHAKA)


class BranchAttendanceCase(TwoBranchCase):
    def setUp(self):
        super().setUp()
        self.punch(MONDAY, 9)
        self.far_punch(MONDAY, 9)
        recalculate(self.company.pk, start=MONDAY, end=MONDAY)
        with use_company(self.company):
            self.near_day = AttendanceRecord.objects.get(employee=self.employee, work_date=MONDAY)
            self.far_day = AttendanceRecord.objects.get(employee=self.far, work_date=MONDAY)
        self.auditor = self.member("auditor@liv.test", "auditor")

    def fix_url(self, employee):
        return reverse("attendance:attendance_day_fix", args=[employee.pk, MONDAY.isoformat()])

    def day_url(self, employee):
        return reverse("attendance:attendance_day", args=[employee.pk, MONDAY.isoformat()])

    def accept(self, employee, actor):
        return fixes.accept_review(
            actor=actor, company_id=self.company.pk, employee_id=employee.pk,
            work_date=MONDAY, reason="Left at shift end",
        )


class DailyListTests(BranchAttendanceCase):
    url = reverse("attendance:attendance_list")

    def test_a_branch_manager_sees_their_branch_only(self):
        self.client.force_login(self.manager)
        page = self.client.get(self.url, AUGUST)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Rahim")
        self.assertNotContains(page, "Karim")
        self.assertNotContains(page, "Chittagong")
        # Asking for the other branch by hand finds nothing.
        page = self.client.get(self.url, {**AUGUST, "branch": self.unit.pk})
        self.assertNotContains(page, "Karim")

    def test_access_in_the_other_branch_shows_that_branch_only(self):
        self.grant("attendance.view", self.unit)
        self.client.force_login(self.clerk_user)
        page = self.client.get(self.url, AUGUST)
        self.assertContains(page, "Karim")
        self.assertNotContains(page, "Rahim")
        self.assertNotContains(page, "Head Office")

    def test_without_access_the_gate_keeps_an_employee_out(self):
        self.client.force_login(self.clerk_user)
        self.assertRedirects(self.client.get(self.url, AUGUST), reverse("me:home"))

    def test_owner_hr_and_other_company_roles_are_unchanged(self):
        for user in (self.admin, self.hr, self.auditor):
            self.client.force_login(user)
            page = self.client.get(self.url, AUGUST)
            self.assertContains(page, "Rahim")
            self.assertContains(page, "Karim")


class CalendarTests(BranchAttendanceCase):
    url = reverse("attendance:attendance_calendar")

    def test_a_branch_manager_cannot_open_another_branchs_person(self):
        self.client.force_login(self.manager)
        page = self.client.get(self.url, {**AUGUST, "employee": self.far.pk})
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "Karim")
        self.assertContains(page, "Rahim")

    def test_access_in_the_other_branch_opens_its_people(self):
        self.grant("attendance.view", self.unit)
        self.client.force_login(self.clerk_user)
        page = self.client.get(self.url, {**AUGUST, "employee": self.far.pk})
        self.assertContains(page, "Karim")
        self.assertNotContains(page, "Rahim")

    def test_the_month_leaves_out_days_worked_in_other_branches(self):
        with use_company(self.company):
            mine = month_view.build_month(
                employee=self.far, year=2026, month=8, company_timezone="Asia/Dhaka",
                branches={self.branch.pk},
            )
            everything = month_view.build_month(
                employee=self.far, year=2026, month=8, company_timezone="Asia/Dhaka",
            )
        self.assertEqual([d for d in mine["days"] if d.record is not None], [])
        self.assertTrue([d for d in everything["days"] if d.record is not None])

    def test_owner_and_hr_are_unchanged(self):
        for user in (self.admin, self.hr):
            self.client.force_login(user)
            page = self.client.get(self.url, {**AUGUST, "employee": self.far.pk})
            self.assertContains(page, "Karim")


class DayPanelTests(BranchAttendanceCase):
    def test_a_branch_manager_opens_and_fixes_their_branch_only(self):
        self.client.force_login(self.manager)
        near = self.client.get(self.day_url(self.employee))
        self.assertEqual(near.status_code, 200)
        self.assertContains(near, self.fix_url(self.employee))
        self.assertEqual(self.client.get(self.day_url(self.far)).status_code, 403)

    def test_view_only_access_shows_the_day_without_fix(self):
        self.grant("attendance.view", self.unit)
        self.client.force_login(self.clerk_user)
        page = self.client.get(self.day_url(self.far))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, self.fix_url(self.far))
        self.assertEqual(self.client.get(self.day_url(self.employee)).status_code, 403)

    def test_owner_and_hr_are_unchanged(self):
        for user in (self.admin, self.hr):
            self.client.force_login(user)
            self.assertContains(self.client.get(self.day_url(self.far)), self.fix_url(self.far))


class NowTests(BranchAttendanceCase):
    def ask(self, *employees):
        ids = ",".join(str(e.pk) for e in employees)
        response = self.client.get(reverse("attendance:attendance_now"), {"employees": ids})
        self.assertEqual(response.status_code, 200)
        return set(response.json()["employees"])

    def test_a_branch_manager_gets_only_their_people(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.ask(self.employee, self.far), {str(self.employee.pk)})
        self.assertNotIn(str(self.far.pk), self.ask())

    def test_employee_or_attendance_access_elsewhere_answers_there_only(self):
        self.grant("employees.view", self.unit)
        self.client.force_login(self.clerk_user)
        self.assertEqual(self.ask(self.employee, self.far), {str(self.far.pk)})

    def test_asking_only_for_other_branches_answers_nothing(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.ask(self.far), set())

    def test_without_access_the_gate_keeps_an_employee_out(self):
        self.client.force_login(self.clerk_user)
        self.assertRedirects(self.client.get(reverse("attendance:attendance_now")), reverse("me:home"))

    def test_owner_is_unchanged(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.ask(self.employee, self.far), {str(self.employee.pk), str(self.far.pk)})


class ReviewAndFixTests(BranchAttendanceCase):
    url = reverse("attendance:attendance_review")

    def test_a_branch_manager_reviews_and_fixes_their_branch_only(self):
        self.client.force_login(self.manager)
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Rahim")
        self.assertNotContains(page, "Karim")
        self.assertEqual(self.client.get(self.fix_url(self.employee)).status_code, 200)
        self.assertEqual(self.client.get(self.fix_url(self.far)).status_code, 403)
        crafted = {"action": "accept_review", "accept-reason": "Left at shift end"}
        self.assertEqual(self.client.post(self.fix_url(self.far), crafted).status_code, 403)
        self.assertRedirects(self.client.post(self.fix_url(self.employee), crafted),
                             self.fix_url(self.employee))
        with use_company(self.company):
            self.assertFalse(AttendanceCorrection.objects.filter(employee=self.far).exists())
            self.assertTrue(AttendanceCorrection.objects.filter(employee=self.employee).exists())

    def test_fix_access_in_the_other_branch_stays_there(self):
        self.grant("attendance.fix", self.unit)
        self.client.force_login(self.clerk_user)
        page = self.client.get(self.url)
        self.assertContains(page, "Karim")
        self.assertNotContains(page, "Rahim")
        self.accept(self.far, self.clerk_user)
        with self.assertRaises(PermissionDenied):
            self.accept(self.employee, self.clerk_user)
        with self.assertRaises(PermissionDenied):
            fixes.add_scan(actor=self.clerk_user, company_id=self.company.pk,
                           employee_id=self.employee.pk, work_date=MONDAY,
                           at=at(MONDAY, 18), reason="Device was offline")
        with self.assertRaises(PermissionDenied):
            fixes.change_status(actor=self.clerk_user, company_id=self.company.pk,
                                employee_id=self.employee.pk, work_date=MONDAY,
                                status="present", reason="On site")

    def test_view_access_alone_does_not_open_fixing(self):
        self.grant("attendance.view", self.branch)
        self.client.force_login(self.clerk_user)
        self.assertRedirects(self.client.get(self.url), reverse("me:home"))
        self.assertEqual(self.client.post(self.fix_url(self.employee), {}).status_code, 403)

    def test_the_review_list_is_limited_in_the_service(self):
        rows = fixes.review_queue(self.company.pk, {self.unit.pk})
        self.assertEqual([row.employee_id for row in rows], [self.far.pk])
        self.assertEqual(len(fixes.review_queue(self.company.pk)), 2)

    def test_owner_and_hr_are_unchanged_and_other_company_roles_still_cannot_fix(self):
        for user in (self.admin, self.hr):
            self.client.force_login(user)
            page = self.client.get(self.url)
            self.assertContains(page, "Rahim")
            self.assertContains(page, "Karim")
        self.accept(self.far, self.hr)
        self.client.force_login(self.auditor)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        with self.assertRaises(PermissionDenied):
            self.accept(self.employee, self.auditor)


class WithdrawTests(BranchAttendanceCase):
    def setUp(self):
        super().setUp()
        self.far_fix = self.accept(self.far, self.admin)
        self.near_fix = self.accept(self.employee, self.admin)

    def url(self, correction):
        return reverse("attendance:attendance_correction_withdraw", args=[correction.pk])

    def test_a_branch_manager_withdraws_in_their_branch_only(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.post(self.url(self.far_fix), {"note": ""}).status_code, 403)
        self.assertRedirects(self.client.post(self.url(self.near_fix), {"note": ""}),
                             self.fix_url(self.employee))
        self.far_fix.refresh_from_db()
        self.near_fix.refresh_from_db()
        self.assertEqual(self.far_fix.status, AttendanceCorrection.Status.APPLIED)
        self.assertEqual(self.near_fix.status, AttendanceCorrection.Status.WITHDRAWN)

    def test_the_service_refuses_another_branchs_correction(self):
        with self.assertRaises(PermissionDenied):
            fixes.withdraw(actor=self.manager, company_id=self.company.pk,
                           correction_id=self.far_fix.pk, note="")
        self.far_fix.refresh_from_db()
        self.assertEqual(self.far_fix.status, AttendanceCorrection.Status.APPLIED)


class DevicesStayWithTheCompanyTests(BranchAttendanceCase):
    def test_a_branch_manager_still_cannot_open_devices(self):
        self.client.force_login(self.manager)
        self.assertRedirects(self.client.get(reverse("devices:device_list")), reverse("me:home"))


class EmployeePageTests(BranchAttendanceCase):
    def setUp(self):
        super().setUp()
        with use_company(self.company):
            membership = CompanyMembership.all_objects.get(company=self.company, user=self.admin)
            record_company_event(
                actor=self.admin, membership=membership, company=self.company,
                action="employee.salary_changed", obj=self.employee,
            )

    def url(self, employee, name="organization:employee_detail"):
        return reverse(name, args=[employee.pk])

    def test_a_branch_manager_opens_their_branchs_people_only(self):
        self.client.force_login(self.manager)
        page = self.client.get(self.url(self.employee))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Salary history")
        self.assertContains(page, self.url(self.employee, "organization:employee_end"))
        self.assertContains(page, reverse("attendance:attendance_calendar"))
        self.assertEqual(self.client.get(self.url(self.far)).status_code, 403)

    def test_view_only_access_hides_pay_and_the_edit_actions(self):
        self.grant("employees.view", self.branch)
        self.client.force_login(self.clerk_user)
        page = self.client.get(self.url(self.employee))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "Salary history")
        self.assertNotContains(page, "30,000")
        self.assertNotContains(page, "Salary changed")
        self.assertNotContains(page, self.url(self.employee, "organization:employee_edit"))
        self.assertNotContains(page, self.url(self.employee, "organization:employee_end"))
        # No attendance access, so no Calendar link either.
        self.assertNotContains(page, reverse("attendance:attendance_calendar"))
        self.assertRedirects(self.client.get(self.url(self.employee, "organization:employee_end")),
                             reverse("me:home"))

    def test_salary_shows_with_view_salaries_in_the_branch(self):
        self.grant("employees.view", self.branch)
        self.grant("salary.view", self.branch)
        self.client.force_login(self.clerk_user)
        page = self.client.get(self.url(self.employee))
        self.assertContains(page, "Salary history")
        self.assertContains(page, "Salary changed")

    def test_without_access_the_gate_keeps_an_employee_out(self):
        self.client.force_login(self.clerk_user)
        self.assertRedirects(self.client.get(self.url(self.employee)), reverse("me:home"))

    def test_the_owner_is_unchanged(self):
        self.client.force_login(self.admin)
        page = self.client.get(self.url(self.far))
        self.assertContains(page, "Salary history")
        self.assertContains(page, self.url(self.far, "organization:employee_end"))


class EndEmploymentTests(BranchAttendanceCase):
    def setUp(self):
        super().setUp()
        self.dina_user = self.member("dina@liv.test", "employee")
        self.dina = self.hq_person("Dina", "E3", self.dina_user)

    def hq_person(self, name, code, user):
        employee = create_employee(
            company=self.company, first_name=name, employee_code=code,
            branch=self.branch, department=self.hq_department, designation=self.hq_designation,
            effective_from=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
            pay_basis="monthly", base_rate=Decimal("30000"),
        )["employee"]
        with use_company(self.company):
            employee.user = user
            employee.save(update_fields=["user"])
        return employee

    def end(self, employee, actor, disable_login=True):
        return detail.end_employment(
            actor=actor, company_id=self.company.pk, employee_id=employee.pk,
            last_day=MONDAY, status="resigned", reason="Moved away",
            disable_login=disable_login,
        )

    def status(self, employee):
        with use_company(self.company):
            return Employee.objects.get(pk=employee.pk).employment_status

    def test_a_branch_manager_ends_employment_in_their_branch_only(self):
        self.client.force_login(self.manager)
        url = reverse("organization:employee_end", args=[self.far.pk])
        self.assertEqual(self.client.get(url).status_code, 403)
        with self.assertRaises(PermissionDenied):
            self.end(self.far, self.manager)
        self.assertEqual(self.status(self.far), "active")
        response = self.client.post(reverse("organization:employee_end", args=[self.dina.pk]), {
            "last_day": MONDAY.isoformat(), "status": "resigned", "reason": "Moved away",
            "disable_login": "on", "end_device_enrollments": "on",
        })
        # Ended, Dina is placed nowhere, so the manager lands on the Employees list.
        self.assertRedirects(response, reverse("employee_list"))
        self.assertEqual(self.status(self.dina), "resigned")

    def test_a_branch_login_cannot_end_a_branch_managers_or_its_own_employment(self):
        manny = self.hq_person("Manny", "E4", self.manager)
        self.grant("employees.edit", self.branch)
        with self.assertRaises(PermissionDenied):
            self.end(manny, self.clerk_user, disable_login=False)
        with self.assertRaises(PermissionDenied):
            self.end(manny, self.manager, disable_login=False)
        with self.assertRaises(PermissionDenied):
            self.end(self.clerk, self.clerk_user, disable_login=False)
        self.assertEqual(self.status(manny), "active")
        self.assertEqual(self.status(self.clerk), "active")
        # The company still can.
        self.end(manny, self.admin)
        self.assertEqual(self.status(manny), "resigned")

    def test_disabling_the_login_needs_login_access_and_is_checked_first(self):
        self.grant("employees.edit", self.branch)
        with self.assertRaises(ValidationError) as caught:
            self.end(self.dina, self.clerk_user)
        self.assertIn("disable_login", caught.exception.message_dict)
        self.assertEqual(self.status(self.dina), "active")
        self.end(self.dina, self.clerk_user, disable_login=False)
        self.assertEqual(self.status(self.dina), "resigned")
        with use_company(self.company):
            login = CompanyMembership.all_objects.get(company=self.company, user=self.dina_user)
        self.assertEqual(login.status, CompanyMembership.Status.ACTIVE)
