"""A12 part 5: leave limited to the viewer's branches (overtime: payroll/tests_branch_overtime.py)."""

import datetime
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from access_control.branch_access import grant_access
from accounts.models import CompanyMembership, User
from attendance.tests_live import DHAKA, UTC, LiveTestCase
from common.tenant import use_company
from devices.models import BiometricDevice, DeviceEnrollment, DeviceMessage, PunchEvent
from employees.services import create_employee
from leaves import services, workflow
from leaves.models import LeaveRequest
from organization.catalogue import adopt_department, adopt_designation
from organization.models import Branch

MONDAY = datetime.date(2026, 8, 10)
AUGUST = {"month": 8, "year": 2026}


class TwoBranchCase(LiveTestCase):
    """Head Office: Rahim (plus Clerk and Manny, the branch manager).
    Chittagong: Karim, with a device of its own."""

    def setUp(self):
        super().setUp()
        with use_company(self.company):
            self.unit = Branch.objects.create(
                company=self.company, code="CTG", name="Chittagong",
                timezone="Asia/Dhaka", country_code="BD",
            )
            self.hq_department = self.employee.assignments.get().department
            self.hq_designation = self.employee.assignments.get().designation
            department = adopt_department(self.unit, "SW", "Software")
            designation = adopt_designation(department, "DEV", "Developer")
        self.far = create_employee(
            company=self.company, first_name="Karim", employee_code="C1",
            branch=self.unit, department=department, designation=designation,
            effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC),
            pay_basis="monthly", base_rate=Decimal("30000"),
        )["employee"]
        with use_company(self.company):
            self.far_device = BiometricDevice.objects.create(
                branch=self.unit, device_model=self.device.device_model, name="CTG",
                serial_number="SN-CTG", timezone="Asia/Dhaka",
                status=BiometricDevice.Status.ACTIVE,
            )
            self.far_enrollment = DeviceEnrollment.objects.create(
                device=self.far_device, employee=self.far, device_user_id="9",
                attendance_enabled=True, assigned_device_authorized=True,
                effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC),
            )
            self.far_message = DeviceMessage.objects.create(
                device=self.far_device, branch=self.unit,
                message_type=DeviceMessage.MessageType.PUNCH_BATCH,
                received_at=datetime.datetime(2026, 8, 10, tzinfo=UTC),
                raw_payload_text="x", payload_hash="ph-ctg",
            )
        self.manager = self.member("manny@liv.test", "manager", branches=[self.branch])
        self.hr = self.member("hr@liv.test", "hr")
        self.clerk_user = self.member("clerk@liv.test", "employee")
        self.clerk = create_employee(
            company=self.company, first_name="Clerk", employee_code="E2",
            branch=self.branch, department=self.hq_department, designation=self.hq_designation,
            effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC),
            pay_basis="monthly", base_rate=Decimal("30000"),
        )["employee"]
        with use_company(self.company):
            self.clerk.user = self.clerk_user
            self.clerk.save(update_fields=["user"])
        self.leave_type = services.create_leave_type(
            actor=self.admin, company_id=self.company.pk, values={"code": "CAS", "name": "Casual"})

    def member(self, email, role, branches=()):
        user = User.objects.create_user(email=email)
        membership = CompanyMembership.all_objects.create(
            company=self.company, user=user, role=role, status="active")
        with use_company(self.company):
            membership.allowed_branches.set(branches)
        return user

    def grant(self, code, *branches, to=None):
        grant_access(actor=self.admin, company_id=self.company.pk,
                     employee_id=(to or self.clerk).pk, code=code,
                     branch_ids=[b.pk for b in branches])

    def far_punch(self, day, hour, minute=0):
        local = datetime.datetime(day.year, day.month, day.day, hour, minute, tzinfo=DHAKA)
        self._index += 1
        with use_company(self.company):
            return PunchEvent.objects.create(
                device_message=self.far_message, device=self.far_device,
                branch=self.unit, device_enrollment=self.far_enrollment,
                employee=self.far, device_user_id="9", source_record_index=self._index,
                punched_at_device_raw=local.strftime("%Y-%m-%d %H:%M:%S"),
                punched_at_device=local, punched_at_utc=local.astimezone(UTC),
                received_at=local.astimezone(UTC), raw_record={},
                authorization_status=PunchEvent.AuthorizationStatus.AUTHORIZED,
            )

    def record_leave(self, employee, on=MONDAY, actor=None):
        return services.record_leave(actor=actor or self.admin, company_id=self.company.pk, values={
            "employee": employee, "leave_type": self.leave_type, "start_date": on,
            "end_date": on, "pay_type": "paid", "reason": "",
        })


class LeaveListTests(TwoBranchCase):
    def setUp(self):
        super().setUp()
        self.near_leave = self.record_leave(self.employee)
        self.far_leave = self.record_leave(self.far)

    def cancel_url(self, leave):
        return reverse("leaves:leave_cancel", args=[leave.pk])

    def test_a_branch_manager_sees_and_cancels_their_branch_only(self):
        self.client.force_login(self.manager)
        page = self.client.get(reverse("leaves:leave_list"), AUGUST)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Rahim")
        self.assertNotContains(page, "Karim")
        self.assertContains(page, self.cancel_url(self.near_leave))
        self.assertNotContains(page, reverse("leaves:leave_type_list"))
        self.assertContains(page, 'data-menu="leave"')
        self.assertEqual(self.client.get(self.cancel_url(self.far_leave)).status_code, 403)
        response = self.client.post(self.cancel_url(self.near_leave), {"reason": "Came in"})
        self.assertRedirects(response, reverse("leaves:leave_list"))
        self.near_leave.refresh_from_db()
        self.assertEqual(self.near_leave.status, LeaveRequest.Status.CANCELLED)

    def test_view_only_access_shows_the_list_without_cancel_or_record(self):
        self.grant("leave.view", self.branch)
        self.client.force_login(self.clerk_user)
        page = self.client.get(reverse("leaves:leave_list"), AUGUST)
        self.assertContains(page, "Rahim")
        self.assertNotContains(page, "Karim")
        self.assertNotContains(page, self.cancel_url(self.near_leave))
        self.assertNotContains(page, 'href="' + reverse("leaves:leave_record"))
        self.assertRedirects(self.client.get(reverse("leaves:leave_record")), reverse("me:home"))

    def test_without_access_the_gate_keeps_an_employee_out(self):
        self.client.force_login(self.clerk_user)
        self.assertRedirects(self.client.get(reverse("leaves:leave_list")), reverse("me:home"))

    def test_owner_and_hr_are_unchanged(self):
        for user in (self.admin, self.hr):
            self.client.force_login(user)
            page = self.client.get(reverse("leaves:leave_list"), AUGUST)
            self.assertContains(page, "Rahim")
            self.assertContains(page, "Karim")
            self.assertContains(page, self.cancel_url(self.far_leave))
            self.assertContains(page, reverse("leaves:leave_type_list"))


class RecordLeaveTests(TwoBranchCase):
    def test_access_in_another_branch_records_leave_there_only(self):
        self.grant("leave.record", self.unit)
        self.client.force_login(self.clerk_user)
        form = self.client.get(reverse("leaves:leave_record"))
        self.assertEqual(form.status_code, 200)
        self.assertContains(form, "Karim")
        self.assertNotContains(form, "Rahim")
        leave = self.record_leave(self.far, actor=self.clerk_user)
        self.assertEqual(leave.status, LeaveRequest.Status.APPROVED)
        # A crafted post for someone in another branch is refused by the service.
        with self.assertRaises(ValidationError):
            self.record_leave(self.employee, on=MONDAY + datetime.timedelta(days=1),
                              actor=self.clerk_user)
        with self.assertRaises(PermissionDenied):
            services.cancel_leave(actor=self.clerk_user, company_id=self.company.pk,
                                  request_id=self.record_leave(self.employee).pk)

    def test_a_manager_cannot_record_their_own_leave(self):
        manny = create_employee(
            company=self.company, first_name="Manny", employee_code="M1",
            branch=self.branch, department=self.hq_department, designation=self.hq_designation,
            effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC),
            pay_basis="monthly", base_rate=Decimal("30000"),
        )["employee"]
        with use_company(self.company):
            manny.user = self.manager
            manny.save(update_fields=["user"])
        with self.assertRaises(PermissionDenied):
            self.record_leave(manny, actor=self.manager)


class ApprovalInboxTests(TwoBranchCase):
    def setUp(self):
        super().setUp()
        with use_company(self.company):
            self.employee.user = User.objects.create_user(email="rahim@liv.test")
            self.employee.save(update_fields=["user"])
        CompanyMembership.all_objects.create(
            company=self.company, user=self.employee.user, role="employee", status="active")
        self.request = workflow.submit_request(
            actor=self.employee.user, company_id=self.company.pk, values={
                "leave_type": self.leave_type, "start_date": MONDAY, "end_date": MONDAY,
                "pay_type": "paid", "reason": "Family",
            })

    def test_someone_given_approve_leave_gets_the_inbox_and_decides(self):
        self.client.force_login(self.clerk_user)
        self.assertEqual(self.client.get(reverse("me:leave_inbox")).status_code, 403)
        self.assertNotContains(self.client.get(reverse("me:home")), reverse("me:leave_inbox"))

        self.grant("leave.approve", self.branch)
        home = self.client.get(reverse("me:home"))
        self.assertContains(home, reverse("me:leave_inbox"))
        self.assertContains(home, "Leave waiting for your approval")
        self.assertContains(self.client.get(reverse("me:leave_inbox")), "Rahim")
        workflow.decide_request(actor=self.clerk_user, company_id=self.company.pk,
                                request_id=self.request.pk, approve=True)
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, "approved")

    def test_approve_access_in_another_branch_does_not_reach_this_one(self):
        self.grant("leave.approve", self.unit)
        self.client.force_login(self.clerk_user)
        self.assertNotContains(self.client.get(reverse("me:leave_inbox")), "Rahim")
        with self.assertRaises(PermissionDenied):
            workflow.decide_request(actor=self.clerk_user, company_id=self.company.pk,
                                    request_id=self.request.pk, approve=True)

    def test_the_branch_manager_still_decides(self):
        workflow.decide_request(actor=self.manager, company_id=self.company.pk,
                                request_id=self.request.pk, approve=False, reason="Busy week")
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, "rejected")
