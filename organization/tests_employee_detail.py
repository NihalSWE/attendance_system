"""An employee's history page, and ending their employment (plan step N6)."""

import datetime
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts.models import CompanyMembership, User
from attendance import tests_live
from attendance.models import AttendanceRecord
from attendance.services import recalculate
from auditlog.models import AuditLog
from common.tenant import use_company
from devices.models import DeviceEnrollment
from employees.models import Employee, EmployeeAssignment, EmployeeCompensation
from employees.services import transfer_employee
from organization import employee_detail_services as services

DHAKA = datetime.timezone(datetime.timedelta(hours=6))
AUG_10 = datetime.date(2026, 8, 10)
AUG_11 = datetime.date(2026, 8, 11)
AUG_12 = datetime.date(2026, 8, 12)


def midnight(day):
    return datetime.datetime(day.year, day.month, day.day, tzinfo=DHAKA)


class DetailTestCase(TestCase):
    # The attendance live-test company: an admin, a 09:00-18:00 company shift,
    # and Rahim (placed and paid 30,000 from 1 Jan 2026) enrolled on a device.
    # Borrowed through the module so LiveTestCase is not collected twice.
    setUp_company = tests_live.LiveTestCase.setUp
    punch = tests_live.LiveTestCase.punch

    def setUp(self):
        self.setUp_company()

    def end(self, last_day=AUG_11, **kwargs):
        values = {
            "actor": self.admin, "company_id": self.company.pk,
            "employee_id": self.employee.pk, "last_day": last_day,
            "status": "resigned", "reason": "Moved abroad",
        }
        values.update(kwargs)
        return services.end_employment(**values)

    def give_login(self):
        user = User.objects.create_user(email="rahim@liv.test", password="pw")
        CompanyMembership.all_objects.create(
            company=self.company, user=user, role=CompanyMembership.Role.EMPLOYEE,
            status=CompanyMembership.Status.ACTIVE,
        )
        Employee.all_objects.filter(pk=self.employee.pk).update(user=user)
        self.employee.refresh_from_db()
        return user

    def rows(self, model):
        with use_company(self.company):
            return list(model.objects.filter(employee=self.employee).order_by("effective_from"))


class EndEmploymentTests(DetailTestCase):
    def test_placement_and_salary_end_after_the_last_working_day(self):
        employee, _summary = self.end(AUG_11)
        self.assertEqual(employee.employment_status, "resigned")
        self.assertEqual(employee.leaving_date, AUG_11)
        [placement] = self.rows(EmployeeAssignment)
        [salary] = self.rows(EmployeeCompensation)
        self.assertEqual(placement.effective_to, midnight(AUG_12))
        self.assertEqual(salary.effective_to, midnight(AUG_12))
        self.assertEqual(placement.status, "ended")
        audit = AuditLog.objects.get(action="employee.employment_ended")
        self.assertEqual(audit.before_data["employment_status"], "active")
        self.assertEqual(audit.after_data["leaving_date"], "2026-08-11")
        self.assertEqual(audit.after_data["reason"], "Moved abroad")

    def test_the_last_day_still_counts_and_nothing_after_it(self):
        for day in (AUG_10, AUG_11, AUG_12):
            self.punch(day, 9)
            self.punch(day, 18)
        recalculate(self.company.pk, start=AUG_10, end=datetime.date(2026, 8, 20))
        self.end(AUG_11)
        with use_company(self.company):
            days = list(
                AttendanceRecord.objects.filter(employee=self.employee)
                .order_by("work_date").values_list("work_date", flat=True)
            )
        self.assertEqual(days, [AUG_10, AUG_11])

    def test_device_enrollments_end_with_an_audit_the_device_history_can_read(self):
        _employee, summary = self.end(AUG_11)
        self.assertEqual(summary["enrollments_ended"], 1)
        [enrollment] = self.rows(DeviceEnrollment)
        self.assertEqual(enrollment.effective_to, midnight(AUG_12))
        audit = AuditLog.objects.get(action="device_enrollment.ended_with_employment")
        self.assertIn("effective_to", audit.before_data)
        self.assertIsNone(audit.before_data["effective_to"])

    def test_enrollments_can_be_left_alone(self):
        _employee, summary = self.end(AUG_11, end_device_enrollments=False)
        self.assertEqual(summary["enrollments_ended"], 0)
        [enrollment] = self.rows(DeviceEnrollment)
        self.assertIsNone(enrollment.effective_to)

    def test_their_login_is_disabled_unless_asked_not_to(self):
        user = self.give_login()
        _employee, summary = self.end(AUG_11)
        self.assertTrue(summary["login_disabled"])
        self.assertEqual(
            CompanyMembership.all_objects.get(user=user, company=self.company).status,
            "suspended",
        )

    def test_the_login_can_be_kept(self):
        user = self.give_login()
        _employee, summary = self.end(AUG_11, disable_login=False)
        self.assertFalse(summary["login_disabled"])
        self.assertEqual(
            CompanyMembership.all_objects.get(user=user, company=self.company).status,
            "active",
        )

    def test_refusals(self):
        future = datetime.date(2099, 1, 1)
        cases = {
            "a future last day": {"last_day": future},
            "a day before the placement began": {"last_day": datetime.date(2025, 12, 31)},
            "no reason": {"reason": "  "},
            "a status that is not leaving": {"status": "suspended"},
        }
        for name, values in cases.items():
            with self.subTest(name):
                with self.assertRaises(ValidationError):
                    self.end(**values)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.employment_status, "active")

    def test_somebody_who_has_left_cannot_leave_again(self):
        self.end(AUG_11)
        with self.assertRaises(ValidationError):
            self.end(AUG_12)

    def test_a_finalised_salary_month_cannot_be_reopened(self):
        from payroll.models import PayrollPeriod, PayrollRun

        with use_company(self.company):
            period = PayrollPeriod.objects.create(
                company=self.company, name="Aug 2026",
                start_date=datetime.date(2026, 8, 1), end_date=datetime.date(2026, 8, 31),
            )
            PayrollRun.objects.create(
                company=self.company, payroll_period=period, status=PayrollRun.Status.POSTED,
            )
        with self.assertRaises(ValidationError) as caught:
            self.end(AUG_11)
        self.assertIn("finalised", str(caught.exception))
        self.end(datetime.date(2026, 8, 31))  # the month's last day is fine

    def test_a_salary_starting_after_the_last_day_is_refused_not_broken(self):
        from organization import employee_edit_services

        employee_edit_services.change_salary(
            actor=self.admin, company_id=self.company.pk, employee_id=self.employee.pk,
            values={"pay_basis": "monthly", "base_rate": Decimal("35000"),
                    "effective_at": midnight(datetime.date(2026, 9, 1)), "reason": "Raise"},
        )
        with self.assertRaises(ValidationError) as caught:
            self.end(AUG_11)
        self.assertIn("salary starts", str(caught.exception))

    def test_only_an_owner_or_company_admin_may_end_it(self):
        hr = User.objects.create_user(email="hr@liv.test", password="pw")
        CompanyMembership.all_objects.create(
            company=self.company, user=hr, role=CompanyMembership.Role.HR,
            status=CompanyMembership.Status.ACTIVE,
        )
        with self.assertRaises(PermissionDenied):
            self.end(AUG_11, actor=hr)

    def test_another_companys_employee_is_not_found(self):
        from tenants.services import onboard_company

        other = onboard_company(code="OTH", slug="oth", name="Other Ltd")
        outsider = User.objects.create_user(email="admin@oth.test", password="pw")
        CompanyMembership.all_objects.create(
            company=other, user=outsider, role=CompanyMembership.Role.COMPANY_ADMIN,
            status=CompanyMembership.Status.ACTIVE,
        )
        with self.assertRaises(PermissionDenied):
            self.end(AUG_11, actor=outsider, company_id=other.pk)


class HistoryTests(DetailTestCase):
    def test_the_history_reads_a_login_outside_a_request(self):
        """Its branches are tenant-scoped; a service call has no middleware."""
        user = self.give_login()
        page = services.employee_history(
            actor=self.admin, company_id=self.company.pk, employee_id=self.employee.pk,
        )
        self.assertEqual(page["login"].user, user)

    def test_the_history_holds_every_placement_salary_and_device(self):
        transfer_employee(
            employee=self.employee, effective_at=midnight(datetime.date(2026, 6, 1)),
            employee_code="E1-B", reason="Promoted", actor=self.admin,
        )
        page = services.employee_history(
            actor=self.admin, company_id=self.company.pk, employee_id=self.employee.pk,
        )
        placements = page["placements"]
        self.assertEqual([p.row.employee_code for p in placements], ["E1-B", "E1"])
        self.assertTrue(placements[0].is_current)
        # The old placement's last day is the day before the new one began.
        self.assertEqual(placements[1].last_day, datetime.date(2026, 5, 31))
        self.assertEqual(len(page["salaries"]), 1)
        self.assertEqual(len(page["devices"]), 1)
        self.assertFalse(page["is_ended"])


class PageTests(DetailTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        self.detail = reverse("organization:employee_detail", args=[self.employee.pk])
        self.end_url = reverse("organization:employee_end", args=[self.employee.pk])

    def test_the_history_page(self):
        response = self.client.get(self.detail)
        self.assertEqual(response.status_code, 200)
        for text in ("Placement history", "Salary history", "30,000.00", "Devices",
                     "Attendance this month", self.end_url,
                     reverse("organization:employee_edit", args=[self.employee.pk])):
            with self.subTest(text=text):
                self.assertContains(response, text)

    def test_the_employee_list_links_to_it(self):
        self.assertContains(self.client.get(reverse("employee_list")), self.detail)

    def test_ending_employment_from_the_screen(self):
        self.give_login()
        page = self.client.get(self.end_url)
        self.assertContains(page, "Disable their login")
        self.assertContains(page, "End their device enrollments")
        response = self.client.post(self.end_url, {
            "last_day": "2026-08-11", "status": "resigned", "reason": "Moved abroad",
            "disable_login": "on", "end_device_enrollments": "on",
        })
        self.assertRedirects(response, self.detail)
        after = self.client.get(self.detail)
        self.assertContains(after, "last working day 11 Aug 2026")
        self.assertNotContains(after, self.end_url)

    def test_a_refused_date_shows_next_to_the_field(self):
        response = self.client.post(self.end_url, {
            "last_day": "2099-01-01", "status": "resigned", "reason": "x",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "on or after their last working day")
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.employment_status, "active")

    def test_a_leaver_cannot_be_ended_twice_from_the_screen(self):
        self.end(AUG_11)
        response = self.client.get(self.end_url)
        self.assertRedirects(response, self.detail)

    def test_hr_is_turned_away(self):
        hr = User.objects.create_user(email="hr@liv.test", password="pw")
        CompanyMembership.all_objects.create(
            company=self.company, user=hr, role=CompanyMembership.Role.HR,
            status=CompanyMembership.Status.ACTIVE,
        )
        self.client.force_login(hr)
        self.assertIn(self.client.get(self.detail).status_code, (302, 403))
        self.assertIn(self.client.post(self.end_url, {
            "last_day": "2026-08-11", "status": "resigned", "reason": "x",
        }).status_code, (302, 403))
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.employment_status, "active")
