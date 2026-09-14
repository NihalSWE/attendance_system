"""Which devices count, and "Re-check punches" (plan step N4).

A punch is judged by the rules in force when it was made. These tests hold
that line — a late punch is still judged by yesterday's rule — and show the
one deliberate way past it: an administrator re-checking a date range, which
judges excluded punches by today's rules, touches nothing that already counts,
leaves finalised salary months alone, and is audited.
"""

import datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import CompanyMembership, User
from attendance import tests_live
from attendance.models import AttendanceRecord
from auditlog.models import AuditLog
from common.choices import DeviceAttendanceScope
from common.tenant import use_company
from devices.models import DeviceEnrollment, DeviceMessage, PunchEvent
from devices.services import attendance_rules
from devices.services.processing import resolve_and_authorize
from scheduling.models import CompanyAttendanceSettings

UTC = datetime.timezone.utc
DHAKA = datetime.timezone(datetime.timedelta(hours=6))
Status = PunchEvent.AuthorizationStatus
AUG_10 = datetime.date(2026, 8, 10)  # a Monday
AUG_11 = datetime.date(2026, 8, 11)


class RulesTestCase(TestCase):
    # The attendance live-test company: onboarded, an admin, a 09:00-18:00
    # company shift and one device with Rahim enrolled as user "1". Borrowed
    # through the module so the runner does not collect LiveTestCase twice.
    setUp_company = tests_live.LiveTestCase.setUp

    def setUp(self):
        from employees.models import EmployeeAssignment
        from organization.models import Branch

        self.setUp_company()
        # Recognition only: the device knows Rahim, but has not been given to
        # him. update() rather than save(), so this reads as how the enrollment
        # was first set up rather than as a later, unaudited edit.
        DeviceEnrollment.all_objects.filter(pk=self.enrollment.pk).update(
            assigned_device_authorized=False
        )
        # The company was set up in January, long before August's scans.
        # Without this every row is seconds old, and a change made a moment
        # after creation reads as the original setup (policy_history's
        # tolerance) — so a re-check test would pass whether or not it judged
        # by today's rules, and prove nothing.
        january = datetime.datetime(2026, 1, 1, tzinfo=UTC)
        for rows in (
            DeviceEnrollment.all_objects.filter(pk=self.enrollment.pk),
            CompanyAttendanceSettings.all_objects.filter(company=self.company),
            Branch.all_objects.filter(company=self.company),
            EmployeeAssignment.all_objects.filter(company=self.company),
        ):
            rows.update(created_at=january, updated_at=january)
        self.enrollment.refresh_from_db()
        self._raw_index = 1000

    def scan(self, day, hour, minute=0, *, device_user_id="1", decide=True):
        """A punch as ingestion stores it, then judged by the real engine."""
        local = datetime.datetime(
            day.year, day.month, day.day, hour, minute, tzinfo=DHAKA
        )
        return self.scan_at(local.astimezone(UTC), device_user_id=device_user_id,
                            decide=decide)

    def scan_at(self, at, *, device_user_id="1", decide=True):
        self._raw_index += 1
        with use_company(self.company):
            punch = PunchEvent.objects.create(
                device_message=self.message, device=self.device,
                branch=self.branch, device_user_id=device_user_id,
                source_record_index=self._raw_index,
                punched_at_device_raw=at.strftime("%Y-%m-%d %H:%M:%S"),
                punched_at_device=at, punched_at_utc=at, received_at=at,
                raw_record={},
            )
            if decide:
                resolve_and_authorize(punch)
                punch.refresh_from_db()
        return punch

    def grant_the_device(self):
        """Tick "Authorised for assigned-devices mode" the way the form does."""
        with use_company(self.company):
            self.enrollment.assigned_device_authorized = True
            self.enrollment.save()
            AuditLog.objects.create(
                company=self.company, actor_user=self.admin,
                actor_type=AuditLog.ActorType.USER,
                action="device_enrollment.updated",
                object_app="devices", object_model="deviceenrollment",
                object_id=str(self.enrollment.pk),
                before_data={"assigned_device_authorized": False},
                after_data={"assigned_device_authorized": True},
            )

    def recheck(self, start=AUG_10, end=AUG_11, **kwargs):
        return attendance_rules.recheck_punches(
            actor=self.admin, company_id=self.company.pk, start=start, end=end,
            **kwargs,
        )

    def reload(self, punch):
        return PunchEvent.all_objects.get(pk=punch.pk)


class CompanyScopeTests(RulesTestCase):
    def settings(self):
        return CompanyAttendanceSettings.all_objects.get(company=self.company)

    def test_a_new_company_counts_assigned_devices_only(self):
        self.assertEqual(
            attendance_rules.company_scope(self.company.pk),
            DeviceAttendanceScope.ASSIGNED_DEVICES,
        )

    def test_changing_the_scope_is_saved_versioned_and_audited(self):
        version = self.settings().settings_version
        _row, changed = attendance_rules.set_company_scope(
            actor=self.admin, company_id=self.company.pk,
            scope=DeviceAttendanceScope.BRANCH_DEVICES,
        )
        self.assertTrue(changed)
        row = self.settings()
        self.assertEqual(row.device_attendance_scope, "branch_devices")
        self.assertEqual(row.settings_version, version + 1)
        audit = AuditLog.objects.get(action="attendance_settings.device_scope_changed")
        # The previous value is what a late punch will be judged by.
        self.assertEqual(
            audit.before_data["device_attendance_scope"], "assigned_devices"
        )
        self.assertEqual(audit.after_data["device_attendance_scope"], "branch_devices")

    def test_saving_the_same_scope_records_nothing(self):
        _row, changed = attendance_rules.set_company_scope(
            actor=self.admin, company_id=self.company.pk,
            scope=DeviceAttendanceScope.ASSIGNED_DEVICES,
        )
        self.assertFalse(changed)
        self.assertFalse(
            AuditLog.objects.filter(
                action="attendance_settings.device_scope_changed"
            ).exists()
        )

    def test_an_unknown_scope_is_refused(self):
        with self.assertRaises(ValidationError):
            attendance_rules.set_company_scope(
                actor=self.admin, company_id=self.company.pk, scope="everywhere",
            )

    def test_only_someone_who_manages_devices_may_change_it(self):
        hr = User.objects.create_user(email="hr@liv.test", password="pw")
        CompanyMembership.all_objects.create(
            company=self.company, user=hr, role=CompanyMembership.Role.HR,
            status=CompanyMembership.Status.ACTIVE,
        )
        with self.assertRaises(PermissionDenied):
            attendance_rules.set_company_scope(
                actor=hr, company_id=self.company.pk,
                scope=DeviceAttendanceScope.COMPANY_DEVICES,
            )
        with self.assertRaises(PermissionDenied):
            attendance_rules.recheck_punches(
                actor=hr, company_id=self.company.pk, start=AUG_10, end=AUG_11,
            )

    def test_a_late_punch_is_still_judged_by_the_rule_in_force_when_it_was_made(self):
        """The audit trail is what makes this work, so it is tested end to end."""
        punch = self.scan_at(timezone.now(), decide=False)
        attendance_rules.set_company_scope(
            actor=self.admin, company_id=self.company.pk,
            scope=DeviceAttendanceScope.COMPANY_DEVICES,
        )
        with use_company(self.company):
            resolve_and_authorize(punch)
        # Under the rule in force at the scan: assigned devices only, no grant.
        self.assertEqual(self.reload(punch).authorization_status,
                         Status.UNAUTHORIZED_DEVICE)


class RecheckTests(RulesTestCase):
    def test_a_forgotten_device_grant_counts_after_a_re_check(self):
        scans = [self.scan(AUG_10, 9), self.scan(AUG_10, 18)]
        self.assertTrue(all(s.authorization_status == Status.UNAUTHORIZED_DEVICE
                            for s in scans))
        self.grant_the_device()

        result = self.recheck()

        self.assertEqual((result.checked, result.now_count), (2, 2))
        for scan in scans:
            punch = self.reload(scan)
            self.assertEqual(punch.authorization_status, Status.AUTHORIZED)
            self.assertEqual(
                punch.authorization_snapshot["recheck"]["previous_status"],
                Status.UNAUTHORIZED_DEVICE,
            )
        # And the day it belongs to is rebuilt from them.
        with use_company(self.company):
            record = AttendanceRecord.objects.get(
                employee=self.employee, work_date=AUG_10
            )
        self.assertEqual(record.attendance_status, "present")

    def test_ticking_the_grant_alone_leaves_stored_punches_as_they_were(self):
        """Nothing is re-judged silently: that is what the button is for."""
        scan = self.scan(AUG_10, 9)
        self.grant_the_device()
        self.assertEqual(self.reload(scan).authorization_status,
                         Status.UNAUTHORIZED_DEVICE)

    def test_a_wider_scope_brings_punches_back_without_a_grant(self):
        scan = self.scan(AUG_10, 9)
        attendance_rules.set_company_scope(
            actor=self.admin, company_id=self.company.pk,
            scope=DeviceAttendanceScope.BRANCH_DEVICES,
        )
        self.recheck()
        self.assertEqual(self.reload(scan).authorization_status, Status.AUTHORIZED)

    def test_attendance_switched_off_still_wins(self):
        scan = self.scan(AUG_10, 9)
        attendance_rules.set_company_scope(
            actor=self.admin, company_id=self.company.pk,
            scope=DeviceAttendanceScope.COMPANY_DEVICES,
        )
        DeviceEnrollment.all_objects.filter(pk=self.enrollment.pk).update(
            attendance_enabled=False, updated_at=timezone.now(),
        )
        result = self.recheck()
        self.assertEqual(self.reload(scan).authorization_status,
                         Status.ENROLLMENT_DISABLED)
        self.assertEqual(result.still_excluded[Status.ENROLLMENT_DISABLED], 1)

    def test_a_punch_that_already_counts_is_never_re_judged(self):
        self.grant_the_device()
        counted = self.scan_at(timezone.now())
        self.assertEqual(counted.authorization_status, Status.AUTHORIZED)
        before = counted.authorization_snapshot
        # Tighten the rule: a re-check must not take the punch away.
        DeviceEnrollment.all_objects.filter(pk=self.enrollment.pk).update(
            attendance_enabled=False, updated_at=timezone.now(),
        )
        today = timezone.now().astimezone(DHAKA).date()
        result = self.recheck(start=today, end=today)
        self.assertEqual(result.checked, 0)
        punch = self.reload(counted)
        self.assertEqual(punch.authorization_status, Status.AUTHORIZED)
        self.assertEqual(punch.authorization_snapshot, before)

    def test_punches_outside_the_range_are_not_touched(self):
        inside = self.scan(AUG_10, 9)
        outside = self.scan(datetime.date(2026, 8, 20), 9)
        self.grant_the_device()
        self.recheck(start=AUG_10, end=AUG_10)
        self.assertEqual(self.reload(inside).authorization_status, Status.AUTHORIZED)
        self.assertEqual(self.reload(outside).authorization_status,
                         Status.UNAUTHORIZED_DEVICE)

    def test_the_range_is_whole_days_in_company_time(self):
        """00:30 on 11 Aug in Dhaka is 18:30 on 10 Aug in UTC — it is the 11th."""
        early = self.scan(AUG_11, 0, 30)
        self.grant_the_device()
        self.recheck(start=AUG_10, end=AUG_10)
        self.assertEqual(self.reload(early).authorization_status,
                         Status.UNAUTHORIZED_DEVICE)
        self.recheck(start=AUG_11, end=AUG_11)
        self.assertEqual(self.reload(early).authorization_status, Status.AUTHORIZED)

    def test_a_finalised_salary_month_is_left_alone(self):
        from payroll.models import PayrollPeriod, PayrollRun

        scan = self.scan(AUG_10, 9)
        with use_company(self.company):
            period = PayrollPeriod.objects.create(
                company=self.company, name="Aug 2026",
                start_date=datetime.date(2026, 8, 1),
                end_date=datetime.date(2026, 8, 31),
            )
            PayrollRun.objects.create(
                company=self.company, payroll_period=period,
                status=PayrollRun.Status.POSTED,
            )
        self.grant_the_device()
        result = self.recheck()
        self.assertEqual((result.checked, result.skipped_locked), (0, 1))
        self.assertEqual(self.reload(scan).authorization_status,
                         Status.UNAUTHORIZED_DEVICE)

    def test_who_a_punch_belongs_to_is_still_decided_at_its_own_time(self):
        """Scans from before an enrollment began are not attached to anyone."""
        early = self.scan(AUG_10, 9, device_user_id="2")
        with use_company(self.company):
            # One enrollment per employee per device at a time: end the old
            # one before the new one starts.
            DeviceEnrollment.all_objects.filter(pk=self.enrollment.pk).update(
                effective_to=datetime.datetime(2026, 1, 2, tzinfo=UTC)
            )
            enrollment = DeviceEnrollment.objects.create(
                device=self.device, employee=self.employee, device_user_id="2",
                attendance_enabled=True, assigned_device_authorized=True,
                effective_from=datetime.datetime(2026, 9, 1, tzinfo=UTC),
            )
        self.recheck()
        self.assertEqual(self.reload(early).authorization_status,
                         Status.EXPIRED_ENROLLMENT)

        # Moving the enrollment's start earlier is the honest fix.
        DeviceEnrollment.all_objects.filter(pk=enrollment.pk).update(
            effective_from=datetime.datetime(2026, 8, 1, tzinfo=UTC)
        )
        self.recheck()
        self.assertEqual(self.reload(early).authorization_status, Status.AUTHORIZED)

    def test_the_whole_re_check_is_audited(self):
        scan = self.scan(AUG_10, 9)
        self.grant_the_device()
        self.recheck()
        audit = AuditLog.objects.get(action="punches.rechecked")
        self.assertEqual(audit.actor_user, self.admin)
        self.assertEqual(audit.before_data["range"], ["2026-08-10", "2026-08-11"])
        self.assertEqual(audit.before_data["punch_status"][str(scan.pk)],
                         Status.UNAUTHORIZED_DEVICE)
        self.assertEqual(audit.after_data["punch_status"][str(scan.pk)],
                         Status.AUTHORIZED)
        self.assertEqual(audit.after_data["now_count"], 1)

    def test_a_backwards_or_over_long_range_is_refused(self):
        with self.assertRaises(ValidationError):
            self.recheck(start=AUG_11, end=AUG_10)
        with self.assertRaises(ValidationError):
            self.recheck(start=datetime.date(2025, 1, 1), end=AUG_10)

    def test_another_companys_punches_are_not_touched(self):
        from devices.models import BiometricDevice
        from organization.models import Branch
        from tenants.services import onboard_company

        other = onboard_company(code="OTH", slug="oth", name="Other Ltd")
        at = datetime.datetime(2026, 8, 10, 3, tzinfo=UTC)
        with use_company(other):
            device = BiometricDevice.objects.create(
                branch=Branch.objects.get(is_default=True),
                device_model=self.device.device_model, name="Other door",
                serial_number="SN-OTHER", timezone="Asia/Dhaka",
                status=BiometricDevice.Status.ACTIVE,
            )
            message = DeviceMessage.objects.create(
                device=device, branch=device.branch,
                message_type=DeviceMessage.MessageType.PUNCH_BATCH,
                received_at=at, raw_payload_text="x", payload_hash="ph-other",
            )
            foreign = PunchEvent.objects.create(
                device_message=message, device=device, branch=device.branch,
                device_user_id="1", source_record_index=1,
                punched_at_device_raw="2026-08-10 09:00:00",
                punched_at_device=at, punched_at_utc=at, received_at=at,
                raw_record={}, authorization_status=Status.UNKNOWN_EMPLOYEE,
            )
        self.scan(AUG_10, 9)
        self.grant_the_device()
        result = self.recheck()
        self.assertEqual(result.checked, 1)
        untouched = self.reload(foreign)
        self.assertEqual(untouched.authorization_status, Status.UNKNOWN_EMPLOYEE)
        self.assertEqual(untouched.authorization_snapshot, {})

class PageTests(RulesTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        self.url = reverse("devices:attendance_rules")

    def test_the_page_shows_the_rule_and_what_does_not_count(self):
        self.scan(AUG_10, 9)
        response = self.client.get(self.url + "?start=2026-08-10&end=2026-08-11")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Which devices count")
        self.assertContains(response, 'value="assigned_devices" checked')
        self.assertContains(response, "Not one of the employee")
        self.assertContains(response, "Re-check 1 punch")

    def test_nothing_excluded_disables_the_button(self):
        response = self.client.get(self.url + "?start=2026-08-10&end=2026-08-11")
        self.assertContains(response, "Every punch counts")
        self.assertContains(response, "disabled")

    def test_saving_the_scope(self):
        response = self.client.post(self.url, {"scope": "company_devices"})
        self.assertRedirects(response, self.url)
        self.assertEqual(attendance_rules.company_scope(self.company.pk),
                         "company_devices")

    def test_re_checking_from_the_page_keeps_the_range(self):
        scan = self.scan(AUG_10, 9)
        self.grant_the_device()
        response = self.client.post(
            reverse("devices:attendance_recheck"),
            {"start": "2026-08-10", "end": "2026-08-11"},
            follow=True,
        )
        self.assertRedirects(response, self.url + "?start=2026-08-10&end=2026-08-11")
        self.assertContains(response, "1 now count")
        self.assertEqual(self.reload(scan).authorization_status, Status.AUTHORIZED)

    def test_re_check_only_answers_a_post(self):
        response = self.client.get(reverse("devices:attendance_recheck"))
        self.assertEqual(response.status_code, 405)

    def test_someone_who_cannot_manage_devices_is_turned_away(self):
        hr = User.objects.create_user(email="hr2@liv.test", password="pw")
        CompanyMembership.all_objects.create(
            company=self.company, user=hr, role=CompanyMembership.Role.HR,
            status=CompanyMembership.Status.ACTIVE,
        )
        self.client.force_login(hr)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        scan = self.scan(AUG_10, 9)
        response = self.client.post(
            reverse("devices:attendance_recheck"),
            {"start": "2026-08-10", "end": "2026-08-11"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.reload(scan).authorization_status,
                         Status.UNAUTHORIZED_DEVICE)

    def test_the_device_and_punch_lists_link_here(self):
        for name in ("devices:device_list", "devices:punch_list"):
            with self.subTest(page=name):
                self.assertContains(self.client.get(reverse(name)), self.url)
