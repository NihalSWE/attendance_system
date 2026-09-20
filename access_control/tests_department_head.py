"""The head of a department manages that department's people (Ajay, 2026-09-20).

Head Office holds two departments: the one Rahim and Clerk are in, and a second
one (Support) with Sumon. Clerk is made the head of the first. Chittagong holds
Karim. So the three boundaries are all testable: another department in the same
branch, another branch, and the head's own record.

A head is deliberately narrow: they see and fix their department's attendance,
see and approve its leave, see its overtime — and never edit employees, never
see pay, never decide overtime, and never decide their own anything.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.urls import reverse

from access_control.branch_access import can, headed_departments, scope_for
from attendance import access, correction_services as fixes, scan_requests
from attendance.models import AttendanceRecord
from attendance.services import recalculate
from common.tenant import use_company
from employees.services import create_employee
from leaves import workflow
from leaves.tests_branch_access import AUGUST, MONDAY, TwoBranchCase
from organization.catalogue import adopt_department, adopt_designation
from organization.models import Department

UTC = datetime.timezone.utc


class DepartmentHeadCase(TwoBranchCase):
    def setUp(self):
        super().setUp()
        # A second department in the SAME branch, so "not another department"
        # is a real boundary and not just "not another branch".
        with use_company(self.company):
            self.support = adopt_department(self.branch, "SUP", "Support")
            support_title = adopt_designation(self.support, "AGT", "Agent")
        self.sumon = create_employee(
            company=self.company, first_name="Sumon", employee_code="E5",
            branch=self.branch, department=self.support, designation=support_title,
            effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC),
            pay_basis="monthly", base_rate=Decimal("30000"),
        )["employee"]
        # Clerk heads the department Rahim and Clerk are in.
        with use_company(self.company):
            self.hq_department.refresh_from_db()
            self.hq_department.head = self.clerk
            self.hq_department.save(update_fields=["head"])
        self.head = self.clerk_user

    def day(self, employee, *hours, on=MONDAY):
        for hour in hours:
            if employee is self.far:
                self.far_punch(on, hour)
            else:
                self.punch_for(employee, on, hour)
        recalculate(self.company.pk, start=on, end=on)

    def punch_for(self, employee, day, hour):
        """A punch for an HQ employee on the HQ device."""
        import datetime as dt

        from devices.models import PunchEvent

        local = dt.datetime(day.year, day.month, day.day, hour, tzinfo=self.DHAKA)
        self._index += 1
        with use_company(self.company):
            enrollment, _ = self._enrollment_for(employee)
            return PunchEvent.objects.create(
                device_message=self.message, device=self.device, branch=self.branch,
                device_enrollment=enrollment, employee=employee,
                device_user_id=str(employee.pk), source_record_index=self._index,
                punched_at_device_raw=local.strftime("%Y-%m-%d %H:%M:%S"),
                punched_at_device=local, punched_at_utc=local.astimezone(UTC),
                received_at=local.astimezone(UTC), raw_record={},
                authorization_status=PunchEvent.AuthorizationStatus.AUTHORIZED,
            )

    def _enrollment_for(self, employee):
        from devices.models import DeviceEnrollment

        return DeviceEnrollment.objects.get_or_create(
            device=self.device, employee=employee,
            defaults={"device_user_id": str(employee.pk), "attendance_enabled": True,
                      "assigned_device_authorized": True,
                      "effective_from": datetime.datetime(2026, 1, 1, tzinfo=UTC)},
        )

    DHAKA = datetime.timezone(datetime.timedelta(hours=6))


class WhatAHeadReachesTests(DepartmentHeadCase):
    def test_the_head_is_resolved_from_the_department(self):
        self.assertEqual(
            headed_departments(self.head, self.company.pk), {self.hq_department.pk}
        )
        # Nobody else heads anything.
        self.assertEqual(headed_departments(self.manager, self.company.pk), set())

    def test_the_head_holds_only_the_head_codes_and_only_in_their_department(self):
        company = self.company.pk
        for code in ("employees.view", "attendance.view", "attendance.fix",
                     "leave.view", "leave.approve", "overtime.view"):
            with self.subTest(code=code):
                self.assertTrue(can(self.head, company, code))
                self.assertTrue(can(self.head, company, code, None, self.hq_department.pk))
                # Not the other department in the same branch.
                self.assertFalse(can(self.head, company, code, None, self.support.pk))
                # Heading a department never opens the whole branch.
                self.assertFalse(can(self.head, company, code, self.branch.pk))
        for code in ("employees.edit", "employees.logins", "salary.view",
                     "salary.prepare", "overtime.decide", "access.grant"):
            with self.subTest(code=code):
                self.assertFalse(can(self.head, company, code))
                self.assertFalse(
                    can(self.head, company, code, None, self.hq_department.pk))

    def test_losing_the_head_field_loses_the_access(self):
        with use_company(self.company):
            self.hq_department.head = None
            self.hq_department.save(update_fields=["head"])
        self.assertEqual(headed_departments(self.head, self.company.pk), set())
        self.assertFalse(can(self.head, self.company.pk, "attendance.view"))

    def test_a_deactivated_department_stops_granting(self):
        with use_company(self.company):
            self.hq_department.status = "inactive"
            self.hq_department.save(update_fields=["status"])
        self.assertEqual(headed_departments(self.head, self.company.pk), set())


class HeadSeesTheirDepartmentTests(DepartmentHeadCase):
    def test_the_employees_list_is_their_department_only(self):
        self.client.force_login(self.head)
        page = self.client.get(reverse("employee_list"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Rahim")        # same department
        self.assertNotContains(page, "Sumon")     # other department, same branch
        self.assertNotContains(page, "Karim")     # other branch

    def test_the_employees_list_offers_no_editing_no_pay_and_no_device_controls(self):
        self.client.force_login(self.head)
        page = self.client.get(reverse("employee_list"))
        self.assertNotContains(page, "Create employee")
        self.assertNotContains(page, "Bulk map")
        self.assertNotContains(page, "Send to devices")
        self.assertNotContains(page, "30,000")    # pay needs salary.view

    def test_the_employee_page_opens_for_their_department_only(self):
        self.client.force_login(self.head)
        mine = reverse("organization:employee_detail", args=[self.employee.pk])
        self.assertEqual(self.client.get(mine).status_code, 200)
        for outsider in (self.sumon, self.far):
            with self.subTest(employee=outsider.first_name):
                page = self.client.get(
                    reverse("organization:employee_detail", args=[outsider.pk]))
                self.assertEqual(page.status_code, 403)

    def test_a_head_cannot_edit_an_employee_or_their_login(self):
        self.client.force_login(self.head)
        for name in ("organization:employee_edit", "organization:employee_end"):
            with self.subTest(page=name):
                page = self.client.get(reverse(name, args=[self.employee.pk]))
                self.assertRedirects(page, reverse("me:home"))

    def test_attendance_is_their_department_only(self):
        self.day(self.employee, 9, 18)
        self.day(self.far, 9, 18)
        self.client.force_login(self.head)
        page = self.client.get(reverse("attendance:attendance_list"), AUGUST)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Rahim")
        self.assertNotContains(page, "Karim")
        self.assertNotContains(page, "Sumon")

    def test_leave_is_their_department_only(self):
        near = self.record_leave(self.employee)
        self.record_leave(self.far)
        self.client.force_login(self.head)
        page = self.client.get(reverse("leaves:leave_list"), AUGUST)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Rahim")
        self.assertNotContains(page, "Karim")
        self.assertIsNotNone(near)

    def test_overtime_of_another_department_in_the_same_branch_is_refused(self):
        """The head's branch is offered as a filter, but only their rows are theirs."""
        from payroll import overtime

        self.day(self.sumon, 9, 20)  # Support, same branch as the head
        with use_company(self.company):
            outside = AttendanceRecord.objects.get(employee=self.sumon, work_date=MONDAY)
        with self.assertRaises(PermissionDenied):
            overtime.overtime_day(actor=self.head, company_id=self.company.pk,
                                  record_id=outside.pk)

    def test_overtime_of_their_own_department_opens(self):
        from payroll import overtime

        self.day(self.employee, 9, 20)
        with use_company(self.company):
            mine = AttendanceRecord.objects.get(employee=self.employee, work_date=MONDAY)
        page = overtime.overtime_day(actor=self.head, company_id=self.company.pk,
                                     record_id=mine.pk)
        self.assertFalse(page["may_decide"])

    def test_overtime_is_visible_but_never_decidable(self):
        self.client.force_login(self.head)
        page = self.client.get(reverse("payroll:overtime_list"), AUGUST)
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "Karim")
        scope = scope_for(self.head, self.company.pk, "overtime.decide")
        self.assertFalse(scope)


class HeadNeverDecidesTheirOwnTests(DepartmentHeadCase):
    def test_a_head_fixes_their_department_but_not_their_own_day(self):
        self.day(self.employee, 9)
        self.day(self.clerk, 9)
        company = self.company.pk
        # Someone else in their department: allowed.
        self.assertTrue(access.may_fix_day(self.head, company, self.employee.pk, MONDAY))
        # Their own day: never.
        self.assertFalse(access.may_fix_day(self.head, company, self.clerk.pk, MONDAY))
        with self.assertRaises(PermissionDenied):
            fixes.accept_review(actor=self.head, company_id=company,
                                employee_id=self.clerk.pk, work_date=MONDAY,
                                reason="my own day")

    def test_a_head_cannot_fix_another_department_or_branch(self):
        self.day(self.far, 9)
        self.day(self.sumon, 9)
        company = self.company.pk
        self.assertFalse(access.may_fix_day(self.head, company, self.far.pk, MONDAY))
        self.assertFalse(access.may_fix_day(self.head, company, self.sumon.pk, MONDAY))

    def test_a_head_never_decides_their_own_missed_scan(self):
        company = self.company.pk
        _membership, queryset = scan_requests.reviewable(self.head, company)
        mine = scan_requests.submit(
            actor=self.head, company_id=company, work_date=MONDAY,
            at=datetime.datetime(MONDAY.year, MONDAY.month, MONDAY.day, 9, 5,
                                 tzinfo=self.DHAKA),
            reason="my own missed scan")
        with use_company(self.company):
            self.assertFalse(queryset.filter(pk=mine.pk).exists())
        with self.assertRaises(PermissionDenied):
            scan_requests.decide(actor=self.head, company_id=company,
                                 request_id=mine.pk, approve=True)

    def test_a_head_never_decides_their_own_leave(self):
        own = self.record_leave(self.clerk)
        member = workflow.reviewer(self.head, self.company.pk)
        with use_company(self.company):
            self.assertFalse(
                workflow.reviewable(member).filter(pk=own.pk).exists())

    def test_a_head_may_decide_their_departments_leave(self):
        theirs = self.record_leave(self.employee)
        member = workflow.reviewer(self.head, self.company.pk)
        with use_company(self.company):
            self.assertTrue(
                workflow.reviewable(member).filter(pk=theirs.pk).exists())

    def test_a_head_cannot_reach_another_departments_leave(self):
        outside = self.record_leave(self.far)
        member = workflow.reviewer(self.head, self.company.pk)
        with use_company(self.company):
            self.assertFalse(
                workflow.reviewable(member).filter(pk=outside.pk).exists())


class HeadChangeIsAuditedTests(DepartmentHeadCase):
    def test_changing_the_head_writes_its_own_audit_line(self):
        from auditlog.models import AuditLog
        from organization import adoption_services

        adoption_services.update_adoption(
            actor=self.admin, company_id=self.company.pk,
            adoption_id=self.hq_department.pk,
            values={"code": self.hq_department.code, "name": self.hq_department.name,
                    "head": self.employee, "status": "active"},
        )
        with use_company(self.company):
            entry = AuditLog.objects.filter(action="department.head_changed").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.before_data["head_id"], self.clerk.pk)
        self.assertEqual(entry.after_data["head_id"], self.employee.pk)
        # And the access moves with it.
        self.assertEqual(headed_departments(self.head, self.company.pk), set())
        self.assertEqual(
            headed_departments(self.employee.user, self.company.pk),
            {self.hq_department.pk},
        ) if self.employee.user_id else None
