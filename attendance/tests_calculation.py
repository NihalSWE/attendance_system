"""Pairing through the real calculation: punches in, a stored day out.

``tests_pairing`` checks the rule in isolation. This checks that a month run
actually writes the allocations and sessions, that the record's minute fields
agree with them, and that recalculating leaves exactly one answer rather than
two overlapping ones.
"""

import datetime
from decimal import Decimal

from django.test import TestCase

from accounts.models import CompanyMembership, User
from attendance.models import AttendanceRecord, AttendanceSession, PunchAllocation
from attendance.services import calculate_attendance
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
WORK_DAY = datetime.date(2026, 8, 10)  # a Monday


class PairedCalculationTests(TestCase):
    """One employee, one shift, punches placed by hand."""

    def setUp(self):
        self.company = onboard_company(code="PAIR", slug="pair", name="Pair Ltd")
        self.admin = User.objects.create_user(email="admin@pair.test", password="pw")
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
            values={
                "company_shift": self.shift,
                "missing_punch_policy": "review_required",
            },
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
                serial_number="SN-PAIR", timezone="Asia/Dhaka",
                status=BiometricDevice.Status.ACTIVE,
            )
            self.enrollment = DeviceEnrollment.objects.create(
                device=self.device, employee=self.employee, device_user_id="1",
                effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC),
            )
            self.message = DeviceMessage.objects.create(
                device=self.device, branch=self.branch,
                message_type=DeviceMessage.MessageType.PUNCH_BATCH,
                received_at=datetime.datetime(2026, 8, 10, tzinfo=UTC),
                raw_payload_text="x", payload_hash="ph-pair",
            )
        self._index = 0

    def _shift_break(self, minutes, paid):
        """Set the break straight on the shift.

        Not through update_shift: the break fields reach the shift form in
        Ajay's A5, and this step must not touch that service.
        """
        from scheduling.models import Shift

        Shift.all_objects.filter(pk=self.shift.pk).update(
            default_break_minutes=minutes, break_is_paid=paid
        )
        self.shift.refresh_from_db()

    def punch(self, hour, minute=0, second=0, device=None):
        """A punch at a local (Dhaka) wall-clock time on the work day."""
        local = datetime.datetime(
            WORK_DAY.year, WORK_DAY.month, WORK_DAY.day, hour, minute, second,
            tzinfo=datetime.timezone(datetime.timedelta(hours=6)),
        )
        self._index += 1
        with use_company(self.company):
            return PunchEvent.objects.create(
                device_message=self.message,
                device=device or self.device,
                branch=self.branch,
                device_enrollment=self.enrollment,
                employee=self.employee,
                device_user_id="1",
                source_record_index=self._index,
                punched_at_device_raw=local.strftime("%Y-%m-%d %H:%M:%S"),
                punched_at_device=local,
                punched_at_utc=local.astimezone(UTC),
                received_at=local.astimezone(UTC),
                raw_record={},
                authorization_status=PunchEvent.AuthorizationStatus.AUTHORIZED,
            )

    def run_month(self):
        calculate_attendance(
            actor=self.admin, company_id=self.company.pk, year=2026, month=8
        )
        with use_company(self.company):
            return AttendanceRecord.objects.get(
                employee=self.employee, work_date=WORK_DAY
            )

    # ------------------------------------------------------------------ tests

    def test_a_plain_day_is_one_session_and_two_allocations(self):
        self.punch(9)
        self.punch(18)
        record = self.run_month()

        with use_company(self.company):
            allocations = list(record.allocations.order_by("sequence_number"))
            sessions = list(record.sessions.all())
        self.assertEqual([a.label for a in allocations], ["check_in", "check_out"])
        self.assertEqual([a.interpreted_direction for a in allocations], ["in", "out"])
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].worked_minutes, 540)
        self.assertEqual(record.worked_minutes, 540)
        self.assertEqual(record.total_minutes, 540)
        self.assertEqual(record.outside_minutes, 0)
        self.assertEqual(record.break_count, 0)

    def test_a_lunch_break_is_labelled_and_taken_off_worked_time(self):
        for hour in (9, 13, 14, 18):
            self.punch(hour)
        record = self.run_month()

        with use_company(self.company):
            labels = list(
                record.allocations.order_by("sequence_number").values_list(
                    "label", flat=True
                )
            )
            sessions = record.sessions.count()
        self.assertEqual(
            labels, ["check_in", "break_out", "break_in", "check_out"]
        )
        self.assertEqual(sessions, 2)
        self.assertEqual(record.total_minutes, 540)
        self.assertEqual(record.worked_minutes, 480)
        self.assertEqual(record.outside_minutes, 60)
        self.assertEqual(record.break_minutes, 60)
        self.assertEqual(record.break_count, 1)
        self.assertEqual(record.attendance_status, "present")

    def test_the_records_minutes_agree_with_its_sessions(self):
        """The stored figures and the stored sessions cannot disagree."""
        for hour, minute in ((9, 0), (11, 30), (12, 0), (18, 0)):
            self.punch(hour, minute)
        record = self.run_month()
        with use_company(self.company):
            session_total = sum(s.worked_minutes for s in record.sessions.all())
        self.assertEqual(session_total, record.worked_minutes)
        self.assertEqual(
            record.total_minutes, session_total + record.outside_minutes
        )

    def test_a_paid_break_is_credited_on_the_stored_record(self):
        self._shift_break(60, paid=True)
        for hour in (9, 13, 14, 18):
            self.punch(hour)
        record = self.run_month()
        self.assertEqual(record.outside_minutes, 60)
        self.assertEqual(record.worked_minutes, 540)

    def test_a_repeat_scan_is_stored_as_ignored_not_dropped(self):
        """The evidence stays visible; it just does not count."""
        self.punch(9)
        self.punch(9, 0, 10)
        self.punch(18)
        record = self.run_month()

        with use_company(self.company):
            included = record.allocations.filter(is_included=True)
            ignored = record.allocations.filter(is_included=False)
            self.assertEqual(
                [a.label for a in included.order_by("sequence_number")],
                ["check_in", "check_out"],
            )
            self.assertEqual(ignored.count(), 1)
            self.assertEqual(
                ignored.first().exclusion_reason,
                PunchAllocation.ExclusionReason.DUPLICATE,
            )
        self.assertEqual(record.break_count, 0)

    def test_two_devices_make_one_stream_not_two(self):
        """In at the front door, out at the back: one session."""
        with use_company(self.company):
            back = BiometricDevice.objects.create(
                branch=self.branch, device_model=self.device.device_model,
                name="Back", serial_number="SN-BACK", timezone="Asia/Dhaka",
                status=BiometricDevice.Status.ACTIVE,
            )
        self.punch(9)
        self.punch(18, device=back)
        record = self.run_month()

        with use_company(self.company):
            self.assertEqual(record.sessions.count(), 1)
            self.assertEqual(
                list(record.allocations.order_by("sequence_number").values_list(
                    "label", flat=True
                )),
                ["check_in", "check_out"],
            )
        self.assertEqual(record.worked_minutes, 540)

    def test_a_day_with_no_check_out_is_incomplete_and_pays_nothing_extra(self):
        self.punch(9)
        self.punch(13)
        self.punch(14)
        record = self.run_month()

        self.assertEqual(record.attendance_status, "incomplete")
        self.assertEqual(record.punch_status, "missing_out")
        self.assertIsNone(record.last_out_at)
        self.assertEqual(record.total_minutes, 0)
        # Measured so far, kept as evidence.
        self.assertEqual(record.worked_minutes, 240)
        self.assertEqual(record.break_count, 1)

    def test_recalculating_leaves_exactly_one_set_of_rows(self):
        for hour in (9, 13, 14, 18):
            self.punch(hour)
        first = self.run_month()
        with use_company(self.company):
            before = (
                first.allocations.count(),
                first.sessions.count(),
            )
        second = self.run_month()
        with use_company(self.company):
            after = (second.allocations.count(), second.sessions.count())
            self.assertEqual(
                PunchAllocation.objects.filter(attendance_record=second).count(),
                after[0],
            )
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(before, after)

    def test_a_late_arrival_still_counts_its_grace(self):
        self.punch(9, 5)
        self.punch(18)
        record = self.run_month()
        # Five minutes late, ten minutes of grace.
        self.assertEqual(record.late_minutes, 0)

    def test_a_record_can_be_deleted_with_its_derived_rows(self):
        """A session PROTECTs its allocations, so the order matters."""
        for hour in (9, 13, 14, 18):
            self.punch(hour)
        record = self.run_month()
        with use_company(self.company):
            record.delete()
            self.assertEqual(
                PunchAllocation.objects.filter(attendance_record_id=record.pk).count(),
                0,
            )
            self.assertEqual(
                AttendanceSession.objects.filter(
                    attendance_record_id=record.pk
                ).count(),
                0,
            )

    def test_allocations_point_back_at_the_punch_they_came_from(self):
        first = self.punch(9)
        last = self.punch(18)
        record = self.run_month()
        with use_company(self.company):
            ids = list(
                record.allocations.order_by("sequence_number").values_list(
                    "punch_event_id", flat=True
                )
            )
        self.assertEqual(ids, [first.pk, last.pk])

    def test_a_non_working_day_writes_no_allocations(self):
        self.punch(9)
        self.punch(18)
        self.run_month()
        with use_company(self.company):
            weekly_off = AttendanceRecord.objects.filter(
                employee=self.employee,
                attendance_status=AttendanceRecord.AttendanceStatus.WEEKLY_OFF,
            ).first()
            if weekly_off is not None:
                self.assertEqual(weekly_off.allocations.count(), 0)
                self.assertEqual(weekly_off.sessions.count(), 0)
