"""Device screen tests: access control, tenant isolation and the write paths.

The playbook requires read and write to ship together, so these exercise the
forms that create and change data, not only the pages that display it.
"""

from datetime import datetime, time, timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import CompanyMembership
from auditlog.models import AuditLog
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
from employees.models import Employee, EmployeeAssignment
from organization.catalogue import adopt_department, adopt_designation
from organization.models import Branch
from scheduling.models import CompanyAttendanceSettings, Shift
from tenants.models import Company


def dt(year, month, day):
    return datetime(year, month, day, tzinfo=dt_timezone.utc)


class DeviceScreenTests(TestCase):
    def setUp(self):
        self.client = Client()
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
        self.company, self.user, self.branch = self._company("A", "admin-a@example.test")
        self.other_company, self.other_user, self.other_branch = self._company(
            "B", "admin-b@example.test"
        )

        with use_company(self.company):
            self.department = adopt_department(self.branch, "SW", "Software")
            self.designation = adopt_designation(self.department, "DEV", "Developer")
            self.employee = Employee.objects.create(
                first_name="Ayesha", last_name="Rahman"
            )
            EmployeeAssignment.objects.create(
                employee=self.employee,
                employee_code="E-1",
                branch=self.branch,
                department=self.department,
                designation=self.designation,
                effective_from=dt(2026, 1, 1),
            )
            self.device = BiometricDevice.objects.create(
                branch=self.branch,
                device_model=self.device_model,
                name="Front Door",
                serial_number="SN-A",
                timezone="Asia/Dhaka",
                status=BiometricDevice.Status.ACTIVE,
            )

        with use_company(self.other_company):
            self.other_device = BiometricDevice.objects.create(
                branch=self.other_branch,
                device_model=self.device_model,
                name="B Front Door",
                serial_number="SN-B",
                timezone="Asia/Dhaka",
                status=BiometricDevice.Status.ACTIVE,
            )

        self.client.force_login(self.user)

    def _company(self, code, email):
        company = Company.objects.create(
            code=code, slug=code.lower(), name=f"Company {code}"
        )
        user = get_user_model().objects.create_user(email=email, password="pw-12345678")
        CompanyMembership.all_objects.create(
            company=company, user=user, role="company_admin", status="active"
        )
        with use_company(company):
            branch = Branch.objects.create(
                code="HQ", name=f"{code} HQ", is_default=True
            )
            shift = Shift.objects.create(
                code="GEN", name="General", start_time=time(9, 0),
                end_time=time(18, 0), scheduled_minutes=540,
            )
            CompanyAttendanceSettings.objects.create(
                company_shift=shift, effective_from=dt(2026, 1, 1)
            )
        return company, user, branch

    # --- access control ---------------------------------------------------

    def test_anonymous_visitor_is_sent_to_login(self):
        self.client.logout()
        response = self.client.get(reverse("devices:device_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_device_list_shows_only_this_companys_devices(self):
        response = self.client.get(reverse("devices:device_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Front Door")
        self.assertNotContains(response, "B Front Door")

    def test_another_companys_device_is_not_reachable_by_url(self):
        response = self.client.get(
            reverse("devices:device_detail", args=[self.other_device.public_id])
        )
        self.assertEqual(response.status_code, 404)

    # --- registering a device --------------------------------------------

    def test_register_device_creates_it_and_issues_a_key(self):
        response = self.client.post(reverse("devices:device_register"), {
            "name": "Side Door",
            "serial_number": "SN-NEW",
            "branch": self.branch.pk,
            "device_model": self.device_model.pk,
            "external_device_id": "",
            "timezone": "Asia/Dhaka",
            "installed_at": "",
            "status": BiometricDevice.Status.ACTIVE,
            "comm_key": "",
            "push_interval_seconds": 10,
            "error_delay_seconds": 30,
            "realtime": "on",
        }, follow=True)
        self.assertEqual(response.status_code, 200)

        device = BiometricDevice.all_objects.get(serial_number="SN-NEW")
        self.assertEqual(device.company_id, self.company.pk)
        # A key was generated and stored only as a hash.
        self.assertTrue(device.authentication_secret_hash)
        self.assertNotIn(device.authentication_secret_hash, response.content.decode())
        # The settings JSON is written from named fields, not hand-typed JSON.
        self.assertEqual(device.settings["push_interval_seconds"], 10)
        self.assertTrue(
            AuditLog.objects.filter(
                object_model="biometricdevice", action="device.registered"
            ).exists()
        )

    def test_duplicate_serial_is_rejected_with_a_readable_message(self):
        response = self.client.post(reverse("devices:device_register"), {
            "name": "Clash",
            "serial_number": "SN-A",
            "branch": self.branch.pk,
            "device_model": self.device_model.pk,
            "timezone": "Asia/Dhaka",
            "status": BiometricDevice.Status.ACTIVE,
            "push_interval_seconds": 10,
            "error_delay_seconds": 30,
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already uses that serial number")

    def test_register_form_cannot_select_another_companys_branch(self):
        response = self.client.post(reverse("devices:device_register"), {
            "name": "Cross tenant",
            "serial_number": "SN-X",
            "branch": self.other_branch.pk,
            "device_model": self.device_model.pk,
            "timezone": "Asia/Dhaka",
            "status": BiometricDevice.Status.ACTIVE,
            "push_interval_seconds": 10,
            "error_delay_seconds": 30,
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            BiometricDevice.all_objects.filter(serial_number="SN-X").exists()
        )

    def test_retiring_a_device_keeps_its_punch_history(self):
        with use_company(self.company):
            message = DeviceMessage.objects.create(
                device=self.device,
                branch=self.branch,
                message_type=DeviceMessage.MessageType.PUNCH_BATCH,
                received_at=dt(2026, 9, 1),
                raw_payload_text="raw",
                payload_hash="h",
            )
            PunchEvent.objects.create(
                device_message=message,
                device=self.device,
                branch=self.branch,
                device_user_id="1",
                source_record_index=0,
                punched_at_device_raw="2026-09-01 09:00:00",
                punched_at_device=dt(2026, 9, 1),
                punched_at_utc=dt(2026, 9, 1),
                received_at=dt(2026, 9, 1),
                raw_record={},
            )

        self.client.post(
            reverse("devices:device_retire", args=[self.device.public_id])
        )
        self.device.refresh_from_db()
        self.assertEqual(self.device.status, BiometricDevice.Status.RETIRED)
        self.assertIsNotNone(self.device.decommissioned_at)
        # The evidence is untouched.
        self.assertEqual(PunchEvent.all_objects.filter(device=self.device).count(), 1)

    # --- enrollment -------------------------------------------------------

    def test_creating_an_enrollment_records_both_policy_flags_in_the_audit(self):
        response = self.client.post(reverse("devices:enrollment_create"), {
            "device": self.device.pk,
            "employee": self.employee.pk,
            "device_user_id": "7",
            "device_privilege": DeviceEnrollment.Privilege.NORMAL_USER,
            "attendance_enabled": "on",
            "effective_from": "2026-09-01T09:00",
            "effective_to": "",
        }, follow=True)
        self.assertEqual(response.status_code, 200)

        enrollment = DeviceEnrollment.all_objects.get(device_user_id="7")
        self.assertTrue(enrollment.attendance_enabled)
        # Recognition only unless explicitly granted.
        self.assertFalse(enrollment.assigned_device_authorized)

        entry = AuditLog.objects.get(action="device_enrollment.created")
        self.assertIn("attendance_enabled", entry.after_data)
        self.assertIn("assigned_device_authorized", entry.after_data)

    def test_editing_an_enrollment_audits_the_previous_values(self):
        # Historical authorization depends on before_data carrying every field
        # the change touched, so this is a correctness test, not bookkeeping.
        with use_company(self.company):
            enrollment = DeviceEnrollment.objects.create(
                device=self.device,
                employee=self.employee,
                device_user_id="7",
                effective_from=dt(2026, 9, 1),
            )

        self.client.post(
            reverse("devices:enrollment_edit", args=[enrollment.pk]),
            {
                "device": self.device.pk,
                "employee": self.employee.pk,
                "device_user_id": "7",
                "device_privilege": DeviceEnrollment.Privilege.NORMAL_USER,
                "attendance_enabled": "on",
                "assigned_device_authorized": "on",
                "effective_from": "2026-09-01T00:00",
                "effective_to": "",
            },
        )
        enrollment.refresh_from_db()
        self.assertTrue(enrollment.assigned_device_authorized)

        entry = AuditLog.objects.get(action="device_enrollment.updated")
        self.assertIs(entry.before_data["assigned_device_authorized"], False)
        self.assertIs(entry.after_data["assigned_device_authorized"], True)

    def test_overlapping_user_number_is_refused_with_an_explanation(self):
        other_employee = None
        with use_company(self.company):
            other_employee = Employee.objects.create(
                first_name="Bob", last_name="Barua"
            )
            DeviceEnrollment.objects.create(
                device=self.device,
                employee=self.employee,
                device_user_id="7",
                effective_from=dt(2026, 9, 1),
            )

        response = self.client.post(reverse("devices:enrollment_create"), {
            "device": self.device.pk,
            "employee": other_employee.pk,
            "device_user_id": "7",
            "device_privilege": DeviceEnrollment.Privilege.NORMAL_USER,
            "attendance_enabled": "on",
            "effective_from": "2026-09-15T00:00",
            "effective_to": "",
        })
        # A readable message, not an IntegrityError page.
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already mapped to")

    def test_enrolling_the_same_employee_twice_on_a_device_is_a_readable_error(self):
        with use_company(self.company):
            DeviceEnrollment.objects.create(
                device=self.device,
                employee=self.employee,
                device_user_id="7",
                effective_from=dt(2026, 9, 1),
            )
        # A different user number, but the same person on the same device.
        response = self.client.post(reverse("devices:enrollment_create"), {
            "device": self.device.pk,
            "employee": self.employee.pk,
            "device_user_id": "8",
            "device_privilege": DeviceEnrollment.Privilege.NORMAL_USER,
            "attendance_enabled": "on",
            "effective_from": "2026-09-15T00:00",
            "effective_to": "",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "is already enrolled on")

    # --- department mapping ----------------------------------------------

    def test_department_mapping_can_be_added_and_ended(self):
        response = self.client.post(
            reverse("devices:device_department_add", args=[self.device.public_id]),
            {
                "department": self.department.pk,
                "effective_from": "2026-09-01T00:00",
                "effective_to": "",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        link = DeviceDepartment.all_objects.get(device=self.device)

        self.client.post(
            reverse("devices:device_department_end", args=[link.pk])
        )
        link.refresh_from_db()
        self.assertEqual(link.status, DeviceDepartment.Status.ENDED)
        self.assertIsNotNone(link.effective_to)

    def test_mapping_a_device_to_one_department_twice_is_a_readable_error(self):
        """The same shape as the double-enrollment bug, one model over.

        ``excl_devicedepartment_overlap`` stops a device being mapped to the
        same department for overlapping dates. Without a matching check in the
        form the administrator got an IntegrityError page instead of being
        told what was already there.
        """
        add = reverse("devices:device_department_add", args=[self.device.public_id])
        self.client.post(add, {
            "department": self.department.pk,
            "effective_from": "2026-09-01T00:00",
            "effective_to": "",
        }, follow=True)

        response = self.client.post(add, {
            "department": self.department.pk,
            "effective_from": "2026-09-15T00:00",
            "effective_to": "",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already serves")
        self.assertEqual(DeviceDepartment.all_objects.count(), 1)

    def test_a_mapping_may_be_re_added_once_the_previous_one_has_ended(self):
        """Ending is not deleting: the same pair must be mappable again."""
        add = reverse("devices:device_department_add", args=[self.device.public_id])
        self.client.post(add, {
            "department": self.department.pk,
            "effective_from": "2026-09-01T00:00",
            "effective_to": "2026-09-10T00:00",
        }, follow=True)

        response = self.client.post(add, {
            "department": self.department.pk,
            "effective_from": "2026-09-10T00:00",
            "effective_to": "",
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(DeviceDepartment.all_objects.count(), 2)

    def test_two_departments_may_share_one_device_at_the_same_time(self):
        """The constraint is per department pair, not per device."""
        with use_company(self.company):
            other = adopt_department(self.branch, "OPS", "Operations")
        add = reverse("devices:device_department_add", args=[self.device.public_id])
        self.client.post(add, {
            "department": self.department.pk,
            "effective_from": "2026-09-01T00:00",
            "effective_to": "",
        }, follow=True)
        response = self.client.post(add, {
            "department": other.pk,
            "effective_from": "2026-09-01T00:00",
            "effective_to": "",
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(DeviceDepartment.all_objects.count(), 2)

    # --- troubleshooting screens -----------------------------------------

    def test_troubleshooting_screens_render(self):
        for name in (
            "devices:message_list",
            "devices:punch_list",
            "devices:unresolved_queue",
            "devices:enrollment_list",
        ):
            with self.subTest(view=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)

    def test_device_detail_states_what_to_type_into_the_device(self):
        response = self.client.get(
            reverse("devices:device_detail", args=[self.device.public_id])
        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # The administrator is told the address, port and path rather than
        # being left to work them out.
        self.assertIn("Server address", body)
        self.assertIn("Server port", body)
        self.assertIn("/iclock/cdata", body)

    def test_punch_detail_explains_the_exclusion(self):
        with use_company(self.company):
            message = DeviceMessage.objects.create(
                device=self.device,
                branch=self.branch,
                message_type=DeviceMessage.MessageType.PUNCH_BATCH,
                received_at=dt(2026, 9, 1),
                raw_payload_text="raw",
                payload_hash="h",
            )
            punch = PunchEvent.objects.create(
                device_message=message,
                device=self.device,
                branch=self.branch,
                device_user_id="404",
                source_record_index=0,
                punched_at_device_raw="2026-09-01 09:00:00",
                punched_at_device=dt(2026, 9, 1),
                punched_at_utc=dt(2026, 9, 1),
                received_at=dt(2026, 9, 1),
                raw_record={},
                authorization_status=PunchEvent.AuthorizationStatus.UNKNOWN_EMPLOYEE,
                authorization_snapshot={"decision_reason": "never enrolled here"},
                processing_status=PunchEvent.ProcessingStatus.EXCLUDED,
            )

        response = self.client.get(reverse("devices:punch_detail", args=[punch.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "never enrolled here")


class SetupInstructionTests(TestCase):
    """The values an administrator types into the device must be connectable."""

    def setUp(self):
        from devices.services import setup_instructions

        self.setup_instructions = setup_instructions
        self.factory_path = "/devices/"

    def _address(self, **extra):
        from django.test import RequestFactory

        request = RequestFactory().get(self.factory_path, **extra)
        return self.setup_instructions.server_address(request)

    def test_plain_http_reports_port_80(self):
        scheme, _, port = self._address()
        self.assertEqual(scheme, "http")
        self.assertEqual(port, 80)

    def test_tls_terminating_proxy_reports_https_on_443(self):
        # A tunnel forwards plain HTTP, so the request looks insecure. Telling
        # the administrator "port 80, HTTPS no" hands them a device setting
        # that cannot connect.
        scheme, _, port = self._address(HTTP_X_FORWARDED_PROTO="https")
        self.assertEqual(scheme, "https")
        self.assertEqual(port, 443)

    def test_proxy_chain_uses_the_clients_scheme(self):
        scheme, _, port = self._address(HTTP_X_FORWARDED_PROTO="https, http")
        self.assertEqual(scheme, "https")
        self.assertEqual(port, 443)

    def test_localhost_is_reported_as_unreachable_by_a_device(self):
        self.assertTrue(self.setup_instructions.is_unreachable_host("127.0.0.1"))
        self.assertFalse(
            self.setup_instructions.is_unreachable_host("tunnel.ngrok-free.dev")
        )
