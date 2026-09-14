"""Attendance keeps itself up to date, and never touches a paid month.

Nobody presses Calculate (DEVICE_ATTENDANCE_POLICY.md step 7, "Live, not on
request"). Three things have to hold:

* a punch arriving rebuilds the days it belongs to, including a device's
  backlog from a week ago;
* reading a month brings its closed days up to date;
* a day inside a posted payroll run never changes, whatever arrives.
"""

import datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import CompanyMembership, User
from attendance.models import AttendanceRecord
from attendance.services import locked_ranges, recalculate, refresh
from common.tenant import use_company
from devices.models import (
    BiometricDevice,
    DeviceEnrollment,
    DeviceMessage,
    DeviceModel,
    DeviceVendor,
    PunchEvent,
)
from employees.services import create_employee
from organization.catalogue import adopt_department, adopt_designation
from organization.models import Branch
from scheduling import services as schedule
from tenants.services import onboard_company

UTC = datetime.timezone.utc
DHAKA = datetime.timezone(datetime.timedelta(hours=6))


class LiveTestCase(TestCase):
    def setUp(self):
        self.company = onboard_company(code="LIV", slug="liv", name="Live Ltd")
        self.admin = User.objects.create_user(email="admin@liv.test", password="pw")
        CompanyMembership.all_objects.create(
            company=self.company, user=self.admin,
            role=CompanyMembership.Role.COMPANY_ADMIN,
            status=CompanyMembership.Status.ACTIVE,
        )
        with use_company(self.company):
            self.branch = Branch.objects.get(is_default=True)
            department = adopt_department(self.branch, "SW", "Software")
            designation = adopt_designation(department, "DEV", "Developer")
        self.employee = create_employee(
            company=self.company, first_name="Rahim", employee_code="E1",
            branch=self.branch, department=department, designation=designation,
            effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC),
            pay_basis="monthly", base_rate=Decimal("30000"),
        )["employee"]
        self.shift = schedule.create_shift(
            actor=self.admin, company_id=self.company.pk, values={
                "code": "DAY", "name": "Day",
                "start_time": datetime.time(9), "end_time": datetime.time(18),
                "spans_next_day": False, "grace_in_minutes": 10,
                "minimum_full_day_minutes": 400, "minimum_half_day_minutes": 200,
            },
        )
        schedule.update_attendance_settings(
            actor=self.admin, company_id=self.company.pk,
            values={"company_shift": self.shift,
                    "missing_punch_policy": "review_required"},
        )
        vendor = DeviceVendor.objects.get_or_create(
            code="zkteco",
            defaults={"name": "ZKTeco", "adapter_key": "zkteco_adms_push"},
        )[0]
        model = DeviceModel.objects.get_or_create(
            vendor=vendor, model_code="senseface-2a",
            defaults={"name": "SenseFace 2A",
                      "protocol": DeviceModel.Protocol.ADMS_PUSH},
        )[0]
        with use_company(self.company):
            self.device = BiometricDevice.objects.create(
                branch=self.branch, device_model=model, name="Front",
                serial_number="SN-LIVE", timezone="Asia/Dhaka",
                status=BiometricDevice.Status.ACTIVE,
            )
            self.enrollment = DeviceEnrollment.objects.create(
                device=self.device, employee=self.employee, device_user_id="1",
                attendance_enabled=True, assigned_device_authorized=True,
                effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC),
            )
            self.message = DeviceMessage.objects.create(
                device=self.device, branch=self.branch,
                message_type=DeviceMessage.MessageType.PUNCH_BATCH,
                received_at=datetime.datetime(2026, 8, 10, tzinfo=UTC),
                raw_payload_text="x", payload_hash="ph-live",
            )
        self._index = 0

    def punch(self, day, hour, minute=0):
        local = datetime.datetime(
            day.year, day.month, day.day, hour, minute, tzinfo=DHAKA
        )
        self._index += 1
        with use_company(self.company):
            return PunchEvent.objects.create(
                device_message=self.message, device=self.device,
                branch=self.branch, device_enrollment=self.enrollment,
                employee=self.employee, device_user_id="1",
                source_record_index=self._index,
                punched_at_device_raw=local.strftime("%Y-%m-%d %H:%M:%S"),
                punched_at_device=local, punched_at_utc=local.astimezone(UTC),
                received_at=local.astimezone(UTC), raw_record={},
                authorization_status=PunchEvent.AuthorizationStatus.AUTHORIZED,
            )

    def record(self, day):
        with use_company(self.company):
            return AttendanceRecord.objects.filter(
                employee=self.employee, work_date=day
            ).first()


class OpenAndClosedTests(LiveTestCase):
    """A day in progress is provisional; once it closes it is decided."""

    def test_a_day_still_running_is_open_and_pays_nothing_yet(self):
        day = datetime.date(2026, 8, 10)
        self.punch(day, 9)
        self.punch(day, 13)
        # 14:00 local on the same day: the shift has not ended.
        now = datetime.datetime(2026, 8, 10, 14, tzinfo=DHAKA)
        recalculate(self.company.pk, start=day, end=day, now=now)

        stored = self.record(day)
        self.assertTrue(stored.is_open)
        self.assertEqual(stored.payable_fraction, Decimal("0"))
        with use_company(self.company):
            labels = list(stored.allocations.order_by("sequence_number")
                          .values_list("label", flat=True))
        self.assertEqual(labels, ["check_in", "break_out"])

    def test_the_same_day_settles_once_it_has_closed(self):
        day = datetime.date(2026, 8, 10)
        self.punch(day, 9)
        self.punch(day, 18)
        now = datetime.datetime(2026, 8, 11, 10, tzinfo=DHAKA)
        recalculate(self.company.pk, start=day, end=day, now=now)

        stored = self.record(day)
        self.assertFalse(stored.is_open)
        self.assertEqual(stored.attendance_status, "present")
        with use_company(self.company):
            labels = list(stored.allocations.order_by("sequence_number")
                          .values_list("label", flat=True))
        self.assertEqual(labels, ["check_in", "check_out"])

    def test_a_working_day_with_no_scans_is_not_absent_until_it_ends(self):
        """"Not in yet" is not a record: nothing is claimed about the day."""
        day = datetime.date(2026, 8, 10)
        now = datetime.datetime(2026, 8, 10, 11, tzinfo=DHAKA)
        recalculate(self.company.pk, start=day, end=day, now=now)
        self.assertIsNone(self.record(day))

    def test_a_working_day_with_no_scans_is_absent_once_it_has_closed(self):
        day = datetime.date(2026, 8, 10)
        now = datetime.datetime(2026, 8, 11, 10, tzinfo=DHAKA)
        recalculate(self.company.pk, start=day, end=day, now=now)
        self.assertEqual(self.record(day).attendance_status, "absent")


class ArrivalTriggersTests(LiveTestCase):
    """A punch rebuilds its own days, without anybody asking."""

    def test_a_batch_rebuilds_the_span_its_days_cover(self):
        """A backlog is recalculated end to end, not day by day.

        The days between the ends are real days that also need deciding — the
        employee was absent on them — so the span is the right unit. Days
        outside it are untouched.
        """
        from attendance.services import recalculate_for_punches

        first = datetime.date(2026, 8, 10)
        second = datetime.date(2026, 8, 12)
        for day in (first, second):
            self.punch(day, 9)
            self.punch(day, 18)
        recalculate_for_punches(
            self.company.pk, {(self.employee.pk, first), (self.employee.pk, second)}
        )
        self.assertEqual(self.record(first).attendance_status, "present")
        self.assertEqual(self.record(second).attendance_status, "present")
        self.assertEqual(
            self.record(datetime.date(2026, 8, 11)).attendance_status, "absent"
        )
        # Nothing outside the span.
        self.assertIsNone(self.record(datetime.date(2026, 8, 9)))
        self.assertIsNone(self.record(datetime.date(2026, 8, 13)))

    def test_a_backlog_from_last_week_lands_on_its_own_days(self):
        """A device back from being offline does not rewrite today."""
        from attendance.services import recalculate_for_punches

        old_day = datetime.date(2026, 8, 3)
        self.punch(old_day, 9)
        self.punch(old_day, 17)
        recalculate_for_punches(self.company.pk, {(self.employee.pk, old_day)})

        stored = self.record(old_day)
        self.assertEqual(stored.attendance_status, "present")
        self.assertEqual(stored.worked_minutes, 480)

    def test_an_ingested_punch_recalculates_without_being_asked(self):
        """End to end through the device endpoint, not the service."""
        body = "\r\n".join([
            "1\t2026-08-10 09:00:00\t0\t1",
            "1\t2026-08-10 18:00:00\t0\t1",
        ]) + "\r\n"
        response = self.client.post(
            "/iclock/cdata",
            data=body, content_type="text/plain",
            QUERY_STRING=f"SN={self.device.serial_number}&table=ATTLOG&Stamp=1",
        )
        self.assertEqual(response.status_code, 200)
        stored = self.record(datetime.date(2026, 8, 10))
        self.assertIsNotNone(stored, "the punch should have built its day")
        self.assertIsNotNone(stored.first_in_at)


class RefreshOnReadTests(LiveTestCase):
    def test_reading_a_month_settles_a_day_that_has_since_closed(self):
        day = datetime.date(2026, 8, 10)
        self.punch(day, 9)
        self.punch(day, 13)
        recalculate(
            self.company.pk, start=day, end=day,
            now=datetime.datetime(2026, 8, 10, 14, tzinfo=DHAKA),
        )
        self.assertTrue(self.record(day).is_open)

        refresh(self.company.pk, start=day, end=day)
        stored = self.record(day)
        self.assertFalse(stored.is_open)
        # It closed on an OUT, so that OUT is the check-out.
        self.assertIsNotNone(stored.last_out_at)

    def test_the_calendar_page_settles_the_month_it_shows(self):
        day = datetime.date(2026, 8, 10)
        self.punch(day, 9)
        self.punch(day, 18)
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("attendance:attendance_calendar")
            + f"?employee={self.employee.pk}&month=8&year=2026"
        )
        self.assertEqual(response.status_code, 200)
        # Nothing had ever been calculated; opening the page did it.
        self.assertEqual(self.record(day).attendance_status, "present")

    def test_there_is_no_calculate_button_any_more(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("attendance:attendance_list") + "?month=8&year=2026"
        )
        self.assertNotContains(response, "Calculate ")


class EmployeeShiftTests(LiveTestCase):
    """An employee's own shift wins, and attendance has to ask for it.

    ``shift_for`` ignores an override unless the employee is named, so an
    attendance run that forgot to pass ``employee_id`` would quietly measure
    everyone against the company shift. This is the test that says it does.
    """

    def _own_shift(self, start, end, from_day):
        evening = schedule.create_shift(
            actor=self.admin, company_id=self.company.pk, values={
                "code": "EVE", "name": "Evening",
                "start_time": datetime.time(start), "end_time": datetime.time(end),
                "spans_next_day": False, "grace_in_minutes": 10,
                "minimum_full_day_minutes": 300, "minimum_half_day_minutes": 120,
            },
        )
        schedule.set_employee_shift(
            actor=self.admin, company_id=self.company.pk, values={
                "employee": self.employee, "shift": evening, "first_day": from_day,
            },
        )
        return evening

    def test_the_employees_own_shift_decides_their_day(self):
        day = datetime.date(2026, 8, 10)
        self._own_shift(14, 22, datetime.date(2026, 8, 1))
        # Worked the evening shift, not the company 09–18 one.
        self.punch(day, 14)
        self.punch(day, 22)
        recalculate(
            self.company.pk, start=day, end=day,
            now=datetime.datetime(2026, 8, 11, 10, tzinfo=DHAKA),
        )
        stored = self.record(day)
        self.assertEqual(stored.shift.code, "EVE")
        self.assertEqual(stored.worked_minutes, 480)
        self.assertEqual(stored.late_minutes, 0)
        self.assertEqual(stored.calculated_overtime_minutes, 0)

    def test_the_company_shift_would_have_read_that_day_wrongly(self):
        """What the override is protecting against, stated plainly."""
        day = datetime.date(2026, 8, 10)
        self.punch(day, 14)
        self.punch(day, 22)
        recalculate(
            self.company.pk, start=day, end=day,
            now=datetime.datetime(2026, 8, 11, 10, tzinfo=DHAKA),
        )
        on_company_shift = self.record(day)
        # 09–18 shift: only 14:00–18:00 is regular, the rest is overtime.
        self.assertEqual(on_company_shift.worked_minutes, 240)
        self.assertGreater(on_company_shift.calculated_overtime_minutes, 0)

    def test_a_temporary_shift_ends_and_the_old_one_returns(self):
        self._own_shift(14, 22, datetime.date(2026, 8, 1))
        schedule.set_employee_shift(
            actor=self.admin, company_id=self.company.pk, values={
                "employee": self.employee, "shift": self.shift,
                "first_day": datetime.date(2026, 8, 12),
            },
        )
        for day, expected in (
            (datetime.date(2026, 8, 10), "EVE"),
            (datetime.date(2026, 8, 12), "DAY"),
        ):
            with self.subTest(day=day):
                self.punch(day, 15)
                self.punch(day, 16)
                recalculate(
                    self.company.pk, start=day, end=day,
                    now=datetime.datetime(2026, 8, 20, 10, tzinfo=DHAKA),
                )
                self.assertEqual(self.record(day).shift.code, expected)


class PayrollLockTests(LiveTestCase):
    """A day inside a posted run never moves again."""

    def _post_a_run(self, start, end):
        from payroll.models import PayrollPeriod, PayrollRun

        with use_company(self.company):
            period = PayrollPeriod.objects.create(
                company=self.company, name="Aug 2026",
                start_date=start, end_date=end,
            )
            return PayrollRun.objects.create(
                company=self.company, payroll_period=period,
                status=PayrollRun.Status.POSTED,
            )

    def test_a_posted_period_is_reported_as_locked(self):
        self._post_a_run(datetime.date(2026, 8, 1), datetime.date(2026, 8, 31))
        self.assertEqual(
            locked_ranges(self.company.pk),
            [(datetime.date(2026, 8, 1), datetime.date(2026, 8, 31))],
        )

    def test_a_late_punch_cannot_change_a_paid_day(self):
        day = datetime.date(2026, 8, 10)
        self.punch(day, 9)
        self.punch(day, 17)
        recalculate(
            self.company.pk, start=day, end=day,
            now=datetime.datetime(2026, 8, 11, 10, tzinfo=DHAKA),
        )
        before = self.record(day).worked_minutes

        self._post_a_run(datetime.date(2026, 8, 1), datetime.date(2026, 8, 31))
        # A punch turns up late, after salary was paid.
        self.punch(day, 19)
        summary = recalculate(
            self.company.pk, start=day, end=day,
            now=datetime.datetime(2026, 8, 20, 10, tzinfo=DHAKA),
        )
        self.assertEqual(self.record(day).worked_minutes, before)
        self.assertEqual(summary.get("skipped_locked"), 1)

    def test_a_day_outside_the_posted_period_still_moves(self):
        self._post_a_run(datetime.date(2026, 8, 1), datetime.date(2026, 8, 31))
        day = datetime.date(2026, 9, 1)
        self.punch(day, 9)
        self.punch(day, 18)
        recalculate(
            self.company.pk, start=day, end=day,
            now=datetime.datetime(2026, 9, 2, 10, tzinfo=DHAKA),
        )
        self.assertEqual(self.record(day).attendance_status, "present")

    def test_a_locked_day_is_not_deleted_by_the_stale_sweep(self):
        """The sweep removes days that no longer apply — but never a paid one."""
        day = datetime.date(2026, 8, 10)
        self.punch(day, 9)
        self.punch(day, 17)
        recalculate(
            self.company.pk, start=day, end=day,
            now=datetime.datetime(2026, 8, 11, 10, tzinfo=DHAKA),
        )
        self._post_a_run(datetime.date(2026, 8, 1), datetime.date(2026, 8, 31))
        with use_company(self.company):
            # Evidence is never deleted; this is how a punch stops counting.
            PunchEvent.objects.filter(employee=self.employee).update(
                authorization_status=PunchEvent.AuthorizationStatus.UNAUTHORIZED_DEVICE
            )
        recalculate(
            self.company.pk, start=day, end=day,
            now=datetime.datetime(2026, 8, 20, 10, tzinfo=DHAKA),
        )
        self.assertIsNotNone(self.record(day))
