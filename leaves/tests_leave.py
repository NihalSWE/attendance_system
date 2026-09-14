"""Leave thin slice: types, recording approved leave, and cancelling it.

The rule that matters most for salary: only working days become leave days.
Weekly offs and holidays inside a leave range are skipped, so an unpaid leave
never deducts a day the employee would not have worked anyway.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts.models import CompanyMembership, User
from common.tenant import use_company
from employees.services import create_employee
from leaves import services
from leaves.models import LeaveDay, LeaveRequest, LeaveType
from organization.catalogue import adopt_department, adopt_designation
from organization.models import Branch
from scheduling import services as schedule
from tenants.services import onboard_company


def dt(y, m, d):
    return datetime.datetime(y, m, d, tzinfo=datetime.timezone.utc)


# 2026-09-17 is a Thursday, 18 a Friday, 19 a Saturday.
THU, FRI, SAT = datetime.date(2026, 9, 17), datetime.date(2026, 9, 18), datetime.date(2026, 9, 19)


class LeaveBase(TestCase):
    def setUp(self):
        self.company = onboard_company(code="ACME", slug="acme", name="Acme Ltd")
        self.other = onboard_company(code="OTHER", slug="other", name="Other Ltd")
        self.admin = User.objects.create_user(email="admin@acme.test", password="pw")
        self.hr = User.objects.create_user(email="hr@acme.test", password="pw")
        CompanyMembership.all_objects.create(
            company=self.company, user=self.admin,
            role=CompanyMembership.Role.COMPANY_ADMIN,
            status=CompanyMembership.Status.ACTIVE,
        )
        CompanyMembership.all_objects.create(
            company=self.company, user=self.hr, role=CompanyMembership.Role.HR,
            status=CompanyMembership.Status.ACTIVE,
        )
        with use_company(self.company):
            self.hq = Branch.objects.get(is_default=True)
            department = adopt_department(self.hq, "SW", "Software")
            designation = adopt_designation(department, "DEV", "Developer")
        self.employee = create_employee(
            company=self.company, first_name="Rahim", employee_code="E1",
            branch=self.hq, department=department, designation=designation,
            effective_from=dt(2026, 1, 1), pay_basis="monthly",
            base_rate=Decimal("30000"), joining_date=datetime.date(2026, 1, 1),
        )["employee"]
        shift = schedule.create_shift(
            actor=self.admin, company_id=self.company.pk, values={
                "code": "DAY", "name": "Day", "start_time": datetime.time(9),
                "end_time": datetime.time(17), "spans_next_day": False,
                "grace_in_minutes": 10, "minimum_full_day_minutes": 420,
                "minimum_half_day_minutes": 210,
            },
        )
        schedule.update_attendance_settings(
            actor=self.admin, company_id=self.company.pk,
            values={"company_shift": shift, "missing_punch_policy": "review_required"},
        )
        schedule.add_weekly_offs(
            actor=self.admin, company_id=self.company.pk,
            values={"weekdays": [4], "branch": None, "is_paid": True,
                    "effective_from": datetime.date(2026, 1, 1)},
        )
        self.casual = services.create_leave_type(
            actor=self.admin, company_id=self.company.pk,
            values={"code": "CL", "name": "Casual leave", "description": ""},
        )

    def _record(self, start=THU, end=SAT, pay="unpaid", **extra):
        values = {"employee": self.employee, "leave_type": self.casual,
                  "start_date": start, "end_date": end, "pay_type": pay, "reason": ""}
        values.update(extra)
        return services.record_leave(
            actor=self.admin, company_id=self.company.pk, values=values
        )

    def _days(self, leave=None):
        with use_company(self.company):
            days = LeaveDay.objects.filter(employee=self.employee)
            if leave:
                days = days.filter(request_segment__leave_request=leave)
            return list(days.order_by("work_date"))


class RecordLeaveTests(LeaveBase):
    def test_weekly_off_inside_the_range_is_not_a_leave_day(self):
        leave = self._record()
        days = self._days(leave)
        self.assertEqual([d.work_date for d in days], [THU, SAT])
        with use_company(self.company):
            self.assertEqual(leave.segments.get().requested_units, Decimal("2"))

    def test_holiday_inside_the_range_is_not_a_leave_day(self):
        schedule.create_holiday(
            actor=self.admin, company_id=self.company.pk,
            values={"holiday_date": SAT, "name": "Test holiday", "branch": None,
                    "is_paid": True, "description": ""},
        )
        leave = self._record()
        self.assertEqual([d.work_date for d in self._days(leave)], [THU])

    def test_unpaid_leave_days_carry_zero_percent(self):
        leave = self._record(pay="unpaid")
        for day in self._days(leave):
            self.assertEqual(day.approved_pay_type, "unpaid")
            self.assertEqual(day.approved_pay_percentage, Decimal("0"))

    def test_paid_leave_days_carry_full_pay_and_the_shift_window(self):
        leave = self._record(start=THU, end=THU, pay="paid")
        [day] = self._days(leave)
        self.assertEqual(day.approved_pay_percentage, Decimal("100"))
        self.assertEqual(day.leave_minutes, 480)
        self.assertEqual(day.shift_snapshot["code"], "DAY")
        # 09:00 in Asia/Dhaka is 03:00 UTC.
        self.assertEqual(day.covered_start_at.astimezone(datetime.timezone.utc).hour, 3)

    def test_leave_is_recorded_as_approved(self):
        leave = self._record()
        self.assertEqual(leave.status, LeaveRequest.Status.APPROVED)
        self.assertEqual(leave.submitted_by, self.admin)

    def test_a_range_of_only_days_off_is_refused(self):
        with self.assertRaises(ValidationError):
            self._record(start=FRI, end=FRI)

    def test_overlapping_leave_is_refused_and_names_the_dates(self):
        self._record(start=THU, end=THU)
        with self.assertRaises(ValidationError) as caught:
            self._record(start=THU, end=SAT)
        self.assertIn("17 Sep", str(caught.exception))

    def test_leave_before_joining_is_refused(self):
        with self.assertRaises(ValidationError):
            self._record(start=datetime.date(2025, 12, 30), end=datetime.date(2025, 12, 31))

    def test_no_company_shift_means_no_leave(self):
        other_admin = User.objects.create_user(email="admin@other.test", password="pw")
        CompanyMembership.all_objects.create(
            company=self.other, user=other_admin,
            role=CompanyMembership.Role.COMPANY_ADMIN,
            status=CompanyMembership.Status.ACTIVE,
        )
        with self.assertRaises(ValidationError):
            services.plan_leave_days(
                company_id=self.other.pk, employee=self.employee,
                start_date=THU, end_date=THU,
            )

    def test_hr_records_and_cancels_leave_but_never_their_own(self):
        values = {"employee": self.employee, "leave_type": self.casual,
                  "start_date": THU, "end_date": THU, "pay_type": "paid", "reason": ""}
        leave = services.record_leave(actor=self.hr, company_id=self.company.pk, values=values)
        self.assertEqual(leave.status, LeaveRequest.Status.APPROVED)
        services.cancel_leave(actor=self.hr, company_id=self.company.pk, request_id=leave.pk)
        with use_company(self.company):
            self.employee.user = self.hr
            self.employee.save(update_fields=["user"])
        with self.assertRaises(PermissionDenied):
            services.record_leave(actor=self.hr, company_id=self.company.pk, values=values)
        # An administrator's record of it cannot be cancelled by HR either.
        leave = self._record(start=THU, end=THU)
        with self.assertRaises(PermissionDenied):
            services.cancel_leave(actor=self.hr, company_id=self.company.pk, request_id=leave.pk)

    def test_default_leave_types_for_new_and_existing_companies(self):
        fresh = onboard_company(code="NEW", slug="new", name="New Ltd", default_leave_types=True)
        with use_company(fresh):
            self.assertEqual(sorted(LeaveType.objects.values_list("code", flat=True)),
                             ["CL", "EL", "ML", "SL"])
        # Acme already has its own CL, which is kept.
        added = services.add_default_leave_types(actor=self.admin, company_id=self.company.pk)
        self.assertEqual(sorted(t.code for t in added), ["EL", "ML", "SL"])
        self.assertEqual(services.add_default_leave_types(actor=self.admin, company_id=self.company.pk), [])
        with use_company(self.company):
            self.assertEqual(LeaveType.objects.get(code="CL").name, "Casual leave")
        with self.assertRaises(PermissionDenied):
            services.add_default_leave_types(actor=self.hr, company_id=self.company.pk)

    def test_inactive_leave_type_is_refused(self):
        services.set_leave_type_status(
            actor=self.admin, company_id=self.company.pk,
            leave_type_id=self.casual.pk, status="inactive",
        )
        self.casual.refresh_from_db()
        with self.assertRaises(ValidationError):
            self._record()


class CancelLeaveTests(LeaveBase):
    def test_cancel_keeps_the_record_and_releases_the_days(self):
        leave = self._record()
        services.cancel_leave(
            actor=self.admin, company_id=self.company.pk, request_id=leave.pk,
            reason="Entered twice",
        )
        leave.refresh_from_db()
        self.assertEqual(leave.status, LeaveRequest.Status.CANCELLED)
        self.assertTrue(all(d.status == "cancelled" for d in self._days(leave)))

    def test_the_same_dates_can_be_recorded_again_after_cancelling(self):
        leave = self._record()
        services.cancel_leave(
            actor=self.admin, company_id=self.company.pk, request_id=leave.pk
        )
        again = self._record(pay="paid")
        self.assertEqual(again.status, LeaveRequest.Status.APPROVED)


class LeaveScreenTests(LeaveBase):
    def test_every_page_renders(self):
        leave = self._record()
        self.client.force_login(self.admin)
        for url in (
            reverse("leaves:leave_list") + "?month=9&year=2026",
            reverse("leaves:leave_record"),
            reverse("leaves:leave_cancel", args=[leave.pk]),
            reverse("leaves:leave_type_list"),
            reverse("leaves:leave_type_create"),
            reverse("leaves:leave_type_edit", args=[self.casual.pk]),
            reverse("leaves:leave_type_status", args=[self.casual.pk]),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_list_shows_the_month_leave_with_working_days(self):
        self._record()
        self.client.force_login(self.admin)
        response = self.client.get(reverse("leaves:leave_list") + "?month=9&year=2026")
        self.assertContains(response, "Rahim")
        self.assertContains(response, "Casual leave")
        self.assertContains(response, "Unpaid")

    def test_recording_through_the_form(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("leaves:leave_record"), {
            "employee": self.employee.pk, "leave_type": self.casual.pk,
            "start_date": "2026-09-17", "end_date": "2026-09-19",
            "pay_type": "paid", "reason": "Family event",
        })
        self.assertRedirects(response, reverse("leaves:leave_list"))
        self.assertEqual(len(self._days()), 2)

    def test_overlap_shows_on_the_form(self):
        self._record(start=THU, end=THU)
        self.client.force_login(self.admin)
        response = self.client.post(reverse("leaves:leave_record"), {
            "employee": self.employee.pk, "leave_type": self.casual.pk,
            "start_date": "2026-09-17", "end_date": "2026-09-17", "pay_type": "paid",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already on leave")

    def test_hr_records_leave_and_the_picker_shows_employee_codes(self):
        self.client.force_login(self.hr)
        self.assertContains(self.client.get(reverse("leaves:leave_list")), reverse("leaves:leave_record"))
        self.assertContains(self.client.get(reverse("leaves:leave_record")), "E1 · Rahim")
        self.assertEqual(self.client.post(reverse("leaves:leave_type_defaults")).status_code, 403)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("leaves:leave_type_list")), "Add default leave types")
        response = self.client.post(reverse("leaves:leave_type_defaults"))
        self.assertRedirects(response, reverse("leaves:leave_type_list"))
        self.assertNotContains(self.client.get(reverse("leaves:leave_type_list")), "Add default leave types")
