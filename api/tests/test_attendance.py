"""Phase 7: attendance - the daily list, a month, a day, fixing it, missed
scans (docs/api/70-attendance.md)."""

import datetime
from decimal import Decimal

from django.utils import timezone

from api.tests.test_devices_setup import DeviceApiTestCase
from attendance.models import AttendanceRecord
from attendance.services import recalculate
from common.tenant import use_company
from devices.models import BiometricDevice, DeviceEnrollment, DeviceMessage, PunchEvent
from employees.services import create_employee
from organization.catalogue import adopt_department, adopt_designation
from scheduling import services as schedule

UTC = datetime.timezone.utc
DHAKA = datetime.timezone(datetime.timedelta(hours=6))


def a_weekday(days_back):
    """A Monday-Thursday at least ``days_back`` days ago (never a weekly off)."""
    day = timezone.localdate() - datetime.timedelta(days=days_back)
    while day.weekday() > 3:
        day -= datetime.timedelta(days=1)
    return day


class AttendanceApiTestCase(DeviceApiTestCase):
    def setUp(self):
        super().setUp()
        with use_company(self.company):
            department = adopt_department(self.hq, "SW", "Software")
            designation = adopt_designation(department, "DEV", "Developer")
        self.rahim = create_employee(
            company=self.company, first_name="Rahim", employee_code="E1", branch=self.hq,
            department=department, designation=designation,
            effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC),
            pay_basis="monthly", base_rate=Decimal("30000"))["employee"]
        shift = schedule.create_shift(actor=self.admin_user, company_id=self.company.pk, values={
            "code": "DAY", "name": "Day", "start_time": datetime.time(9),
            "end_time": datetime.time(18), "spans_next_day": False, "grace_in_minutes": 10,
            "minimum_full_day_minutes": 400, "minimum_half_day_minutes": 200})
        schedule.update_attendance_settings(
            actor=self.admin_user, company_id=self.company.pk,
            values={"company_shift": shift, "missing_punch_policy": "review_required"})
        with use_company(self.company):
            self.device = BiometricDevice.objects.create(
                branch=self.hq, device_model=self.model, name="Front", serial_number="SN-ATT",
                timezone="Asia/Dhaka", status="active")
            self.enrollment = DeviceEnrollment.objects.create(
                device=self.device, employee=self.rahim, device_user_id="1",
                attendance_enabled=True, assigned_device_authorized=True,
                effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC))
            self.message = DeviceMessage.objects.create(
                device=self.device, branch=self.hq, message_type="punch_batch",
                received_at=timezone.now(), raw_payload_text="x", payload_hash="ph-att")
        self._index = 0
        self.late_day, self.open_day = a_weekday(3), a_weekday(10)
        self.punch(self.late_day, 9, 25)
        self.punch(self.late_day, 18, 5)
        self.punch(self.open_day, 9)

    def punch(self, day, hour, minute=0):
        local = datetime.datetime(day.year, day.month, day.day, hour, minute, tzinfo=DHAKA)
        self._index += 1
        with use_company(self.company):
            PunchEvent.objects.create(
                device_message=self.message, device=self.device, branch=self.hq,
                device_enrollment=self.enrollment, employee=self.rahim, device_user_id="1",
                source_record_index=self._index,
                punched_at_device_raw=local.strftime("%Y-%m-%d %H:%M:%S"),
                punched_at_device=local, punched_at_utc=local.astimezone(UTC),
                received_at=local.astimezone(UTC), raw_record={}, authorization_status="authorized")
        recalculate(self.company.pk, employee_ids=[self.rahim.pk], start=day, end=day)

    def day_path(self, day, action=""):
        return f"/api/v1/attendance/days/{self.rahim.pk}/{day.isoformat()}" + (
            f"/{action}" if action else "")


class ReadingTests(AttendanceApiTestCase):
    def test_the_daily_list_and_late_entries(self):
        listed = self.api("GET", "/api/v1/attendance", query=f"on={self.late_day}")
        self.assertEqual(listed.status_code, 200, listed.content)
        row = listed.json()["results"][0]
        self.assertEqual((row["employee"]["name"], row["status"], row["employee_code"]),
                         ("Rahim", "present", "E1"))
        self.assertGreater(row["late_minutes"], 0)
        late = self.api("GET", "/api/v1/attendance/late",
                        query=f"from={self.open_day}&to={self.late_day}").json()
        self.assertEqual([r["date"] for r in late["results"]], [self.late_day.isoformat()])
        bad = self.api("GET", "/api/v1/attendance", query="from=2026-10-05&to=2026-01-01")
        self.assertEqual(bad.status_code, 422)

    def test_the_list_by_department(self):
        from organization.models import Department

        with use_company(self.company):
            software = Department.objects.get(code="SW")
            other = Department.objects.exclude(pk=software.pk).first()
        mine = self.api("GET", "/api/v1/attendance",
                        query=f"on={self.late_day}&department_id={software.pk}").json()
        self.assertEqual(mine["count"], 1)
        if other is not None:
            none = self.api("GET", "/api/v1/attendance",
                            query=f"on={self.late_day}&department_id={other.pk}").json()
            self.assertEqual(none["count"], 0)

    def test_a_month_and_a_day(self):
        month = self.api("GET", "/api/v1/attendance/calendar",
                         query=f"employee_id={self.rahim.pk}&year={self.late_day.year}"
                               f"&month={self.late_day.month}")
        self.assertEqual(month.status_code, 200, month.content)
        on = [d for d in month.json()["days"] if d["date"] == self.late_day.isoformat()][0]
        self.assertEqual((on["status"], on["late"], on["check_in"]), ("present", True, "09:25"))
        day = self.api("GET", self.day_path(self.late_day)).json()
        self.assertEqual([scan["label"] for scan in day["scans"]], ["Check-in", "Check-out"])
        self.assertTrue(day["may_fix"])
        self.assertFalse(day["locked"])
        nobody = self.api("GET", "/api/v1/attendance/days/999999/2026-10-01")
        self.assertEqual(nobody.status_code, 404)

    def test_who_is_in_now(self):
        now = self.api("GET", "/api/v1/attendance/now", query=f"employee_ids={self.rahim.pk}")
        self.assertEqual(now.status_code, 200, now.content)
        self.assertEqual(now.json()["results"][0]["employee_id"], self.rahim.pk)

    def test_download(self):
        got = self.api("GET", "/api/v1/attendance/export",
                       query=f"on={self.late_day}&file_type=xlsx")
        self.assertEqual(got.status_code, 200)
        self.assertIn("spreadsheet", got["Content-Type"])
        wrong = self.api("GET", "/api/v1/attendance/export", query="file_type=csv")
        self.assertEqual(wrong.status_code, 422)

    def test_an_employee_without_a_grant_sees_nothing(self):
        self.person("staff@example.test", role="employee")
        staff = self.logged_in("staff@example.test")
        refused = self.api("GET", "/api/v1/attendance", session=staff)
        self.assertEqual(self.code_of(refused), "permission_denied")

    def test_api_keys_need_the_scope(self):
        reader = self.key("attendance:read")
        self.assertEqual(self.as_key(reader, "GET", "/api/v1/attendance").status_code, 200)
        refused = self.as_key(reader, "POST", self.day_path(self.late_day, "excuse-late"),
                              {"reason": "x"})
        self.assertEqual(self.code_of(refused), "scope_missing")


class FixingTests(AttendanceApiTestCase):
    def test_a_day_to_review_is_fixed_with_a_scan_and_withdrawn(self):
        review = self.api("GET", "/api/v1/attendance/review").json()
        self.assertIn(self.open_day.isoformat(), [r["day"]["date"] for r in review["results"]])
        fixed = self.api("POST", self.day_path(self.open_day, "add-scan"),
                         {"at": f"{self.open_day}T18:30", "reason": "Device was offline"})
        self.assertEqual(fixed.status_code, 200, fixed.content)
        self.assertFalse(fixed.json()["day"]["needs_review"])
        self.assertIn("Added by hand", [scan["device"] for scan in fixed.json()["scans"]])
        correction = fixed.json()["corrections"][0]
        self.assertEqual((correction["type"], correction["by"]), ("add_scan", "admin@example.test"))
        review = self.api("GET", "/api/v1/attendance/review").json()
        self.assertNotIn(self.open_day.isoformat(), [r["day"]["date"] for r in review["results"]])
        back = self.api("POST", f"/api/v1/attendance/corrections/{correction['id']}/withdraw",
                        {"note": "Wrong day"})
        self.assertEqual(back.status_code, 200, back.content)
        self.assertTrue(back.json()["day"]["needs_review"])

    def test_a_scan_needs_a_time_and_a_reason(self):
        no_time = self.api("POST", self.day_path(self.open_day, "add-scan"),
                           {"at": str(self.open_day), "reason": "x"})
        self.assertEqual(no_time.status_code, 422)
        no_reason = self.api("POST", self.day_path(self.open_day, "add-scan"),
                             {"at": f"{self.open_day}T18:30"})
        self.assertIn("reason", self.fields(no_reason))

    def test_change_status_and_accept_review(self):
        changed = self.api("POST", self.day_path(self.open_day, "change-status"),
                           {"status": "half_day", "reason": "Left for the client"})
        self.assertEqual(changed.status_code, 200, changed.content)
        self.assertEqual(changed.json()["day"]["status"], "half_day")
        wrong = self.api("POST", self.day_path(self.open_day, "change-status"),
                         {"status": "leave", "reason": "x"})
        self.assertIn("status", self.fields(wrong))

    def test_accept_a_day_as_it_is(self):
        accepted = self.api("POST", self.day_path(self.open_day, "accept-review"),
                            {"reason": "Checked with the manager"})
        self.assertEqual(accepted.status_code, 200, accepted.content)
        self.assertFalse(accepted.json()["day"]["needs_review"])
        self.assertEqual(accepted.json()["corrections"][0]["type"], "accept_review")

    def test_a_late_arrival_is_approved_once(self):
        excused = self.api("POST", self.day_path(self.late_day, "excuse-late"),
                           {"reason": "Doctor's appointment"})
        self.assertEqual(excused.status_code, 200, excused.content)
        self.assertEqual(excused.json()["day"]["late_minutes"], 0)
        again = self.api("POST", self.day_path(self.late_day, "excuse-late"),
                         {"reason": "Doctor's appointment"})
        self.assertEqual(again.status_code, 422)
        not_late = self.api("POST", self.day_path(self.open_day, "excuse-late"),
                            {"reason": "x"})
        self.assertEqual(not_late.status_code, 422)


class MissedScanTests(AttendanceApiTestCase):
    def enter(self, **extra):
        return self.api("POST", f"/api/v1/employees/{self.rahim.pk}/missing-attendance", {
            "kind": "check_out", "at": f"{self.open_day}T18:10",
            "reason": "Left through the side gate", **extra})

    def test_entered_then_decided(self):
        entered = self.enter()
        self.assertEqual(entered.status_code, 201, entered.content)
        self.assertEqual((entered.json()["status"], entered.json()["date"]),
                         ("pending", self.open_day.isoformat()))
        waiting = self.api("GET", "/api/v1/missed-scans").json()
        self.assertEqual([r["id"] for r in waiting["results"]], [entered.json()["id"]])
        refused = self.api("POST", f"/api/v1/missed-scans/{entered.json()['id']}/decide",
                           {"decision": "reject"})
        self.assertEqual(refused.status_code, 422)
        approved = self.api("POST", f"/api/v1/missed-scans/{entered.json()['id']}/decide",
                            {"decision": "approve"})
        self.assertEqual(approved.status_code, 200, approved.content)
        self.assertEqual(approved.json()["status"], "approved")
        with use_company(self.company):
            record = AttendanceRecord.objects.get(employee=self.rahim, work_date=self.open_day)
        self.assertIsNotNone(record.last_out_at)
        decided = self.api("GET", "/api/v1/missed-scans", query="status=approved").json()
        self.assertEqual(decided["count"], 1)

    def test_an_administrator_may_approve_at_once(self):
        entered = self.enter(approve_now=True)
        self.assertEqual(entered.status_code, 201, entered.content)
        self.assertEqual(entered.json()["status"], "approved")

    def test_a_whole_day_needs_its_date(self):
        missing = self.api("POST", f"/api/v1/employees/{self.rahim.pk}/missing-attendance",
                           {"kind": "whole_day", "reason": "Forgot to scan all day"})
        self.assertEqual(missing.status_code, 422)
