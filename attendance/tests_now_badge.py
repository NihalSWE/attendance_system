"""The "Now" badge: where each employee is, from today's scans.

Derived on read, so nothing here asserts about stored rows. What matters is
that the seven states come out right, that the badge uses the same pairing the
calendar does, and that one company cannot read another's.
"""

import datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import CompanyMembership, User
from attendance import live_status
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
from leaves import services as leave_services
from organization.catalogue import adopt_department, adopt_designation
from organization.models import Branch
from scheduling import services as schedule
from tenants.services import onboard_company

UTC = datetime.timezone.utc
DHAKA = datetime.timezone(datetime.timedelta(hours=6))
TODAY = datetime.date(2026, 8, 10)  # a Monday


def local(hour, minute=0, day=TODAY):
    return datetime.datetime(day.year, day.month, day.day, hour, minute, tzinfo=DHAKA)


class NowBadgeTestCase(TestCase):
    def setUp(self):
        self.company = onboard_company(code="NOW", slug="now", name="Now Ltd")
        self.admin = User.objects.create_user(email="admin@now.test", password="pw")
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
                "spans_next_day": False, "grace_in_minutes": 15,
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
                serial_number="SN-NOW", timezone="Asia/Dhaka",
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
                raw_payload_text="x", payload_hash="ph-now",
            )
        self._index = 0

    def punch(self, hour, minute=0):
        moment = local(hour, minute)
        self._index += 1
        with use_company(self.company):
            return PunchEvent.objects.create(
                device_message=self.message, device=self.device,
                branch=self.branch, device_enrollment=self.enrollment,
                employee=self.employee, device_user_id="1",
                source_record_index=self._index,
                punched_at_device_raw=moment.strftime("%Y-%m-%d %H:%M:%S"),
                punched_at_device=moment, punched_at_utc=moment.astimezone(UTC),
                received_at=moment.astimezone(UTC), raw_record={},
                authorization_status=PunchEvent.AuthorizationStatus.AUTHORIZED,
            )

    def status(self, at_hour, at_minute=0):
        result = live_status.statuses_for(
            self.company.pk, employee_ids=[self.employee.pk],
            now=local(at_hour, at_minute),
        )
        return result[self.employee.pk]


class BadgeStateTests(NowBadgeTestCase):
    def test_before_the_shift_starts_nobody_is_missing(self):
        self.assertEqual(self.status(8, 30).key, "not_in_yet")

    def test_inside_the_grace_is_still_not_in_yet(self):
        """Fifteen minutes of grace: 09:10 is not yet a problem."""
        self.assertEqual(self.status(9, 10).key, "not_in_yet")

    def test_past_the_grace_with_no_scan_is_absent(self):
        self.assertEqual(self.status(9, 30).key, "absent")

    def test_after_checking_in_they_are_in_office(self):
        self.punch(9)
        status = self.status(10)
        self.assertEqual(status.key, "in_office")
        self.assertEqual(status.since_text, "09:00")

    def test_after_a_break_out_they_are_on_break(self):
        self.punch(9)
        self.punch(13)
        status = self.status(13, 30)
        self.assertEqual(status.key, "on_break")
        self.assertEqual(status.since_text, "13:00")

    def test_after_coming_back_they_are_in_office_again(self):
        for hour in (9, 13, 14):
            self.punch(hour)
        self.assertEqual(self.status(15).key, "in_office")

    def test_a_trailing_out_is_a_break_while_the_day_is_open(self):
        """The same rule the calendar uses: lunch is not going home."""
        self.punch(9)
        self.punch(13)
        self.assertEqual(self.status(17).key, "on_break")

    def test_leaving_after_the_shift_ends_reads_as_left(self):
        """Not "on break" all evening because somebody went home at six."""
        self.punch(9)
        self.punch(18)
        status = self.status(19)
        self.assertEqual(status.key, "left")
        self.assertEqual(status.since_text, "18:00")

    def test_stepping_out_during_the_shift_is_still_a_break(self):
        self.punch(9)
        self.punch(13)
        self.assertEqual(self.status(13, 30).key, "on_break")

    def test_the_badge_resets_for_the_next_day(self):
        """Yesterday's check-out says nothing about this morning."""
        self.punch(9)
        self.punch(18)
        tomorrow = live_status.statuses_for(
            self.company.pk, employee_ids=[self.employee.pk],
            now=local(6, 0, day=datetime.date(2026, 8, 11)),
        )[self.employee.pk]
        self.assertEqual(tomorrow.key, "not_in_yet")

    def test_leave_wins_over_everything(self):
        casual = leave_services.create_leave_type(
            actor=self.admin, company_id=self.company.pk,
            values={"code": "CL", "name": "Casual", "description": ""},
        )
        leave_services.record_leave(
            actor=self.admin, company_id=self.company.pk, values={
                "employee": self.employee, "leave_type": casual,
                "start_date": TODAY, "end_date": TODAY,
                "pay_type": "paid", "reason": "",
            },
        )
        self.assertEqual(self.status(11).key, "on_leave")

    def test_a_weekly_off_reads_as_off_today(self):
        schedule.add_weekly_offs(
            actor=self.admin, company_id=self.company.pk, values={
                "weekdays": [TODAY.weekday()], "branch": None, "is_paid": True,
                "effective_from": datetime.date(2026, 1, 1),
            },
        )
        self.assertEqual(self.status(11).key, "off_today")

    def test_working_on_a_day_off_still_shows_them_in_the_office(self):
        """"Off today" is about the calendar, not about where they are."""
        schedule.add_weekly_offs(
            actor=self.admin, company_id=self.company.pk, values={
                "weekdays": [TODAY.weekday()], "branch": None, "is_paid": True,
                "effective_from": datetime.date(2026, 1, 1),
            },
        )
        self.punch(10)
        self.assertEqual(self.status(11).key, "in_office")


class BadgeShapeTests(NowBadgeTestCase):
    def test_every_state_uses_an_existing_token_family(self):
        allowed = {"success", "warning", "danger", "info", "neutral"}
        for key, (_label, tone) in live_status.BADGES.items():
            with self.subTest(key=key):
                self.assertIn(tone, allowed)

    def test_a_status_serialises_for_the_poller(self):
        self.punch(9)
        payload = self.status(10).as_dict()
        self.assertEqual(
            sorted(payload), ["detail", "key", "label", "since", "tone"]
        )
        self.assertEqual(payload["label"], "In office")


class NowScreenTests(NowBadgeTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def test_the_employees_page_renders_the_column_server_side(self):
        """Correct before any script runs."""
        self.punch(9)
        response = self.client.get(reverse("employee_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Now")
        self.assertContains(response, f'data-now-for="{self.employee.pk}"')

    def test_the_page_tells_the_poller_where_to_look(self):
        response = self.client.get(reverse("employee_list"))
        self.assertContains(response, "data-now-board")
        self.assertContains(response, reverse("attendance:attendance_now"))

    def test_the_endpoint_answers_with_one_entry_per_employee(self):
        self.punch(9)
        response = self.client.get(
            reverse("attendance:attendance_now")
            + f"?employees={self.employee.pk}"
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()["employees"][str(self.employee.pk)]
        self.assertIn(payload["key"], live_status.BADGES)
        self.assertIn(payload["tone"], {"success", "warning", "danger", "info",
                                        "neutral"})

    def test_the_endpoint_needs_a_login(self):
        self.client.logout()
        self.assertEqual(
            self.client.get(reverse("attendance:attendance_now")).status_code, 302
        )

    def test_another_company_reads_nothing_of_this_one(self):
        other = onboard_company(code="OTH", slug="oth", name="Other Ltd")
        outsider = User.objects.create_user(email="out@oth.test", password="pw")
        CompanyMembership.all_objects.create(
            company=other, user=outsider,
            role=CompanyMembership.Role.COMPANY_ADMIN,
            status=CompanyMembership.Status.ACTIVE,
        )
        self.client.force_login(outsider)
        response = self.client.get(
            reverse("attendance:attendance_now")
            + f"?employees={self.employee.pk}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["employees"], {})

    def test_the_badge_writes_nothing(self):
        """It is a reading, not a calculation: no records appear."""
        from attendance.models import AttendanceRecord

        self.punch(9)
        self.client.get(reverse("employee_list"))
        with use_company(self.company):
            self.assertEqual(AttendanceRecord.objects.count(), 0)
