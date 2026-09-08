"""Authorization tests: the worked scenarios from DEVICE_ATTENDANCE_POLICY.md.

The policy document lists scenarios "the next implementation must verify".
Each one below names the scenario it covers. All of this is simulator/unit
evidence — no physical device is involved.
"""

from datetime import datetime, time, timezone as dt_timezone

from django.test import TestCase
from django.utils import timezone

from auditlog.models import AuditLog
from common.choices import DeviceAttendanceScope
from common.tenant import use_company
from devices.models import (
    BiometricDevice,
    DeviceDepartment,
    DeviceEnrollment,
    DeviceMessage,
    DeviceModel,
    DeviceVendor,
    PunchEvent,
)
from devices.services.processing import resolve_and_authorize
from employees.models import Employee, EmployeeAssignment
from organization.models import Branch, Department, Designation
from scheduling.models import CompanyAttendanceSettings, Shift
from tenants.models import Company


def dt(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=dt_timezone.utc)


class AuthorizationTests(TestCase):
    def setUp(self):
        self.vendor = DeviceVendor.objects.get_or_create(
            code="zkteco",
            defaults={"name": "ZKTeco", "adapter_key": "zkteco_adms_push"},
        )[0]
        self.device_model = DeviceModel.objects.get_or_create(
            vendor=self.vendor,
            model_code="senseface-2a",
            defaults={
                "name": "SenseFace 2A",
                "protocol": DeviceModel.Protocol.ADMS_PUSH,
            },
        )[0]
        self.company = Company.objects.create(code="A", slug="a", name="Company A")

        with use_company(self.company):
            self.hq = Branch.objects.create(code="HQ", name="HQ", is_default=True)
            self.other_branch = Branch.objects.create(code="BR2", name="Branch Two")
            self.software = Department.objects.create(
                branch=self.hq, code="SW", name="Software"
            )
            self.sales = Department.objects.create(
                branch=self.hq, code="SL", name="Sales"
            )
            self.designation = Designation.objects.create(
                department=self.software, code="DEV", name="Developer"
            )
            self.shift = Shift.objects.create(
                code="GEN",
                name="General",
                start_time=time(9, 0),
                end_time=time(18, 0),
                scheduled_minutes=540,
            )
            self.settings_row = CompanyAttendanceSettings.objects.create(
                company_shift=self.shift,
                device_attendance_scope=DeviceAttendanceScope.ASSIGNED_DEVICES,
                effective_from=dt(2026, 1, 1),
            )

            self.hq_device = self._device("HQ Door", "SN-HQ", self.hq)
            self.other_device = self._device("BR2 Door", "SN-BR2", self.other_branch)

            self.alice = Employee.objects.create(first_name="Alice", last_name="Ahmed")
            self.assignment = EmployeeAssignment.objects.create(
                employee=self.alice,
                employee_code="E-1",
                branch=self.hq,
                department=self.software,
                designation=self.designation,
                effective_from=dt(2026, 1, 1),
            )

    def _device(self, name, serial, branch):
        return BiometricDevice.objects.create(
            branch=branch,
            device_model=self.device_model,
            name=name,
            serial_number=serial,
            timezone="Asia/Dhaka",
            status=BiometricDevice.Status.ACTIVE,
        )

    def _enroll(self, device, *, granted=False, enabled=True, employee=None):
        return DeviceEnrollment.objects.create(
            device=device,
            employee=employee or self.alice,
            device_user_id="7",
            effective_from=dt(2026, 1, 1),
            attendance_enabled=enabled,
            assigned_device_authorized=granted,
        )

    def _punch(self, device, at=None, device_user_id="7"):
        # Punches happen "now" by default, i.e. after the fixture's policy rows
        # were written. A test that wants a *historical* punch takes this
        # instant first and mutates policy afterwards.
        at = at or timezone.now()
        message = DeviceMessage.objects.create(
            device=device,
            branch=device.branch,
            message_type=DeviceMessage.MessageType.PUNCH_BATCH,
            received_at=at,
            raw_payload_text="raw",
            payload_hash="h",
        )
        return PunchEvent.objects.create(
            device_message=message,
            device=device,
            branch=device.branch,
            device_user_id=device_user_id,
            source_record_index=0,
            punched_at_device_raw="2026-09-08 09:00:00",
            punched_at_device=at,
            punched_at_utc=at,
            received_at=at,
            raw_record={},
        )

    def _set_scope(self, scope):
        # save(), not queryset.update(), so auto_now advances updated_at the way
        # a real edit through a form would. The historical-policy logic depends
        # on that timestamp.
        self.settings_row.device_attendance_scope = scope
        self.settings_row.save()

    # --- recognition ------------------------------------------------------

    def test_unknown_device_user_id_is_preserved_and_marked(self):
        with use_company(self.company):
            punch = self._punch(self.hq_device, device_user_id="999")
            resolve_and_authorize(punch)
            punch.refresh_from_db()
            self.assertEqual(
                punch.authorization_status,
                PunchEvent.AuthorizationStatus.UNKNOWN_EMPLOYEE,
            )
            self.assertIsNone(punch.employee_id)
            # Preserved, not dropped.
            self.assertEqual(PunchEvent.objects.count(), 1)

    def test_punch_outside_enrollment_interval_is_expired_not_unknown(self):
        with use_company(self.company):
            DeviceEnrollment.objects.create(
                device=self.hq_device,
                employee=self.alice,
                device_user_id="7",
                effective_from=dt(2026, 1, 1),
                effective_to=dt(2026, 6, 1),
            )
            punch = self._punch(self.hq_device)
            resolve_and_authorize(punch)
            punch.refresh_from_db()
            self.assertEqual(
                punch.authorization_status,
                PunchEvent.AuthorizationStatus.EXPIRED_ENROLLMENT,
            )

    def test_reused_device_user_id_resolves_to_the_holder_at_punch_time(self):
        with use_company(self.company):
            bob = Employee.objects.create(first_name="Bob", last_name="Barua")
            DeviceEnrollment.objects.create(
                device=self.hq_device, employee=self.alice, device_user_id="7",
                effective_from=dt(2026, 1, 1), effective_to=dt(2026, 6, 1),
                assigned_device_authorized=True,
            )
            DeviceEnrollment.objects.create(
                device=self.hq_device, employee=bob, device_user_id="7",
                effective_from=dt(2026, 6, 1), assigned_device_authorized=True,
            )
            # A punch in September belongs to Bob, not Alice.
            punch = self._punch(self.hq_device)
            resolve_and_authorize(punch)
            punch.refresh_from_db()
            self.assertEqual(punch.employee_id, bob.pk)

    # --- the hard denial --------------------------------------------------

    def test_attendance_disabled_excludes_the_punch_under_every_scope(self):
        # Each scope gets its own employee and device user id: enrollments
        # cannot be cleared between iterations because a stored punch protects
        # them, which is the append-only guarantee doing its job.
        for index, scope in enumerate(DeviceAttendanceScope.values):
            with self.subTest(scope=scope):
                with use_company(self.company):
                    self._set_scope(scope)
                    employee = Employee.objects.create(
                        first_name=f"Test{index}", last_name="Person"
                    )
                    device_user_id = f"90{index}"
                    DeviceEnrollment.objects.create(
                        device=self.hq_device,
                        employee=employee,
                        device_user_id=device_user_id,
                        effective_from=dt(2026, 1, 1),
                        attendance_enabled=False,
                        assigned_device_authorized=True,
                    )
                    punch = self._punch(
                        self.hq_device, device_user_id=device_user_id
                    )
                    resolve_and_authorize(punch)
                    punch.refresh_from_db()
                    self.assertEqual(
                        punch.authorization_status,
                        PunchEvent.AuthorizationStatus.ENROLLMENT_DISABLED,
                    )
                    self.assertEqual(
                        punch.processing_status,
                        PunchEvent.ProcessingStatus.EXCLUDED,
                    )

    # --- assigned_devices -------------------------------------------------

    def test_recognition_only_enrollment_is_unauthorized_in_assigned_mode(self):
        # Policy scenario: assigned_devices employee scans a recognition-only
        # enrollment with assigned_device_authorized=false.
        with use_company(self.company):
            self._set_scope(DeviceAttendanceScope.ASSIGNED_DEVICES)
            self._enroll(self.hq_device, granted=False)
            punch = self._punch(self.hq_device)
            resolve_and_authorize(punch)
            punch.refresh_from_db()
            self.assertEqual(
                punch.authorization_status,
                PunchEvent.AuthorizationStatus.UNAUTHORIZED_DEVICE,
            )
            self.assertEqual(
                punch.processing_status, PunchEvent.ProcessingStatus.EXCLUDED
            )
            # Preserved with an explanation the UI can show.
            self.assertIn("recognition only", punch.authorization_snapshot["decision_reason"])

    def test_granted_device_is_authorized_in_assigned_mode(self):
        with use_company(self.company):
            self._set_scope(DeviceAttendanceScope.ASSIGNED_DEVICES)
            self._enroll(self.hq_device, granted=True)
            punch = self._punch(self.hq_device)
            resolve_and_authorize(punch)
            punch.refresh_from_db()
            self.assertEqual(
                punch.authorization_status, PunchEvent.AuthorizationStatus.AUTHORIZED
            )
            self.assertEqual(
                punch.processing_status, PunchEvent.ProcessingStatus.PENDING
            )

    # --- department_devices -----------------------------------------------

    def test_shared_device_with_no_mapping_counts_in_department_mode(self):
        # Policy scenario: department_devices employee scans a shared device
        # with no department links.
        with use_company(self.company):
            self._set_scope(DeviceAttendanceScope.DEPARTMENT_DEVICES)
            self._enroll(self.hq_device)
            punch = self._punch(self.hq_device)
            resolve_and_authorize(punch)
            punch.refresh_from_db()
            self.assertEqual(
                punch.authorization_status, PunchEvent.AuthorizationStatus.AUTHORIZED
            )

    def test_device_mapped_only_to_another_department_is_mismatched(self):
        # Policy scenario: department_devices employee scans a device mapped
        # only to another department.
        with use_company(self.company):
            self._set_scope(DeviceAttendanceScope.DEPARTMENT_DEVICES)
            DeviceDepartment.objects.create(
                device=self.hq_device,
                department=self.sales,
                effective_from=dt(2026, 1, 1),
            )
            self._enroll(self.hq_device)
            punch = self._punch(self.hq_device)
            resolve_and_authorize(punch)
            punch.refresh_from_db()
            self.assertEqual(
                punch.authorization_status,
                PunchEvent.AuthorizationStatus.DEPARTMENT_MISMATCH,
            )
            self.assertEqual(
                punch.processing_status, PunchEvent.ProcessingStatus.EXCLUDED
            )

    def test_department_mappings_do_not_restrict_branch_mode(self):
        # Policy scenario: branch_devices employee scans same-branch devices
        # with different department mappings; both can count.
        with use_company(self.company):
            self._set_scope(DeviceAttendanceScope.BRANCH_DEVICES)
            DeviceDepartment.objects.create(
                device=self.hq_device,
                department=self.sales,
                effective_from=dt(2026, 1, 1),
            )
            self._enroll(self.hq_device)
            punch = self._punch(self.hq_device)
            resolve_and_authorize(punch)
            punch.refresh_from_db()
            self.assertEqual(
                punch.authorization_status, PunchEvent.AuthorizationStatus.AUTHORIZED
            )

    # --- branch and company scopes ---------------------------------------

    def test_other_branch_device_is_branch_mismatch_in_branch_mode(self):
        # Policy scenario: branch_devices employee scans another branch's device.
        with use_company(self.company):
            self._set_scope(DeviceAttendanceScope.BRANCH_DEVICES)
            self._enroll(self.other_device)
            punch = self._punch(self.other_device)
            resolve_and_authorize(punch)
            punch.refresh_from_db()
            self.assertEqual(
                punch.authorization_status,
                PunchEvent.AuthorizationStatus.BRANCH_MISMATCH,
            )

    def test_other_branch_device_counts_in_company_mode(self):
        # Policy scenario: company_devices employee scans another branch's
        # device; it counts, and the source branch stays recorded separately.
        with use_company(self.company):
            self._set_scope(DeviceAttendanceScope.COMPANY_DEVICES)
            self._enroll(self.other_device)
            punch = self._punch(self.other_device)
            resolve_and_authorize(punch)
            punch.refresh_from_db()
            self.assertEqual(
                punch.authorization_status, PunchEvent.AuthorizationStatus.AUTHORIZED
            )
            # The employee's home branch is not overwritten by the device's.
            self.assertEqual(punch.branch_id, self.other_branch.pk)
            self.assertEqual(
                punch.authorization_snapshot["employee_branch_id"], self.hq.pk
            )

    # --- precedence chain -------------------------------------------------

    def test_employee_override_narrows_a_broader_company_scope(self):
        # Policy scenario: company uses branch_devices but one employee has an
        # assigned_devices override; that employee stays restricted.
        with use_company(self.company):
            self._set_scope(DeviceAttendanceScope.BRANCH_DEVICES)
            self.assignment.device_attendance_scope_override = (
                DeviceAttendanceScope.ASSIGNED_DEVICES
            )
            self.assignment.save()
            self._enroll(self.hq_device, granted=False)
            punch = self._punch(self.hq_device)
            resolve_and_authorize(punch)
            punch.refresh_from_db()
            self.assertEqual(
                punch.authorization_status,
                PunchEvent.AuthorizationStatus.UNAUTHORIZED_DEVICE,
            )
            self.assertEqual(
                punch.authorization_snapshot["winning_policy_level"],
                "employee_assignment",
            )

    def test_branch_override_wins_over_company_default(self):
        with use_company(self.company):
            self._set_scope(DeviceAttendanceScope.ASSIGNED_DEVICES)
            self.hq.device_attendance_scope_override = (
                DeviceAttendanceScope.BRANCH_DEVICES
            )
            self.hq.save()
            self._enroll(self.hq_device, granted=False)
            punch = self._punch(self.hq_device)
            resolve_and_authorize(punch)
            punch.refresh_from_db()
            # branch_devices does not require the explicit grant.
            self.assertEqual(
                punch.authorization_status, PunchEvent.AuthorizationStatus.AUTHORIZED
            )
            self.assertEqual(
                punch.authorization_snapshot["winning_policy_level"], "branch"
            )

    # --- historical policy ------------------------------------------------

    def test_policy_changed_after_the_punch_without_audit_is_unresolved(self):
        # Policy scenario: company changes scope after an offline punch
        # occurred; missing history must produce policy_unresolved, never
        # today's broader permission.
        with use_company(self.company):
            self._set_scope(DeviceAttendanceScope.ASSIGNED_DEVICES)
            self._enroll(self.hq_device, granted=True)
            punch = self._punch(self.hq_device)

            # The settings row is edited after the punch happened, with no
            # audit record explaining what it held before.
            self._set_scope(DeviceAttendanceScope.COMPANY_DEVICES)

            resolve_and_authorize(punch)
            punch.refresh_from_db()
            self.assertEqual(
                punch.authorization_status,
                PunchEvent.AuthorizationStatus.POLICY_UNRESOLVED,
            )
            self.assertEqual(
                punch.processing_status, PunchEvent.ProcessingStatus.NEEDS_REVIEW
            )

    def test_audited_change_lets_the_historical_value_be_reconstructed(self):
        with use_company(self.company):
            self._set_scope(DeviceAttendanceScope.ASSIGNED_DEVICES)
            self._enroll(self.hq_device, granted=False)
            punch = self._punch(self.hq_device)

            # The scope was widened after the punch, and the change is audited
            # with both sides recorded.
            self._set_scope(DeviceAttendanceScope.COMPANY_DEVICES)
            AuditLog.objects.create(
                company=self.company,
                actor_type=AuditLog.ActorType.USER,
                action="company_attendance_settings.updated",
                object_app="scheduling",
                object_model="companyattendancesettings",
                object_id=str(self.settings_row.pk),
                object_display="settings",
                before_data={
                    "device_attendance_scope": DeviceAttendanceScope.ASSIGNED_DEVICES
                },
                after_data={
                    "device_attendance_scope": DeviceAttendanceScope.COMPANY_DEVICES
                },
                occurred_at=timezone.now(),
            )

            resolve_and_authorize(punch)
            punch.refresh_from_db()
            # Judged by the narrower policy that applied when it happened.
            self.assertEqual(
                punch.authorization_status,
                PunchEvent.AuthorizationStatus.UNAUTHORIZED_DEVICE,
            )
            self.assertEqual(
                punch.authorization_snapshot["effective_scope"],
                DeviceAttendanceScope.ASSIGNED_DEVICES,
            )

    def test_enrollment_disabled_after_the_punch_does_not_retroactively_deny(self):
        with use_company(self.company):
            self._set_scope(DeviceAttendanceScope.ASSIGNED_DEVICES)
            enrollment = self._enroll(self.hq_device, granted=True)
            punch = self._punch(self.hq_device)

            enrollment.attendance_enabled = False
            enrollment.save()
            AuditLog.objects.create(
                company=self.company,
                actor_type=AuditLog.ActorType.USER,
                action="device_enrollment.updated",
                object_app="devices",
                object_model="deviceenrollment",
                object_id=str(enrollment.pk),
                object_display="enrollment",
                before_data={"attendance_enabled": True},
                after_data={"attendance_enabled": False},
                occurred_at=timezone.now(),
            )

            resolve_and_authorize(punch)
            punch.refresh_from_db()
            # It was enabled when the punch happened.
            self.assertEqual(
                punch.authorization_status, PunchEvent.AuthorizationStatus.AUTHORIZED
            )

    def test_no_assignment_at_event_time_is_unresolved(self):
        with use_company(self.company):
            EmployeeAssignment.all_objects.filter(pk=self.assignment.pk).update(
                effective_to=dt(2026, 3, 1), status=EmployeeAssignment.Status.ENDED
            )
            self._enroll(self.hq_device, granted=True)
            punch = self._punch(self.hq_device)
            resolve_and_authorize(punch)
            punch.refresh_from_db()
            self.assertEqual(
                punch.authorization_status,
                PunchEvent.AuthorizationStatus.POLICY_UNRESOLVED,
            )
