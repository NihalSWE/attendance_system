"""Device screen tests: access control, tenant isolation and the write paths.

The playbook requires read and write to ship together, so these exercise the
forms that create and change data, not only the pages that display it.
"""

from datetime import date, datetime, time, timezone as dt_timezone

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
        # No key is invented: a ZKTeco push device could never send it.
        self.assertEqual(device.authentication_secret_hash, "")
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
            "effective_from_0": "2026-09-01",
            "effective_from_1": "09:00",
            "effective_to_0": "",
            "effective_to_1": "",
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
                "effective_from_0": "2026-09-01",
                "effective_from_1": "00:00",
                "effective_to_0": "",
                "effective_to_1": "",
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
            "effective_from_0": "2026-09-15",
            "effective_from_1": "00:00",
            "effective_to_0": "",
            "effective_to_1": "",
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
            "effective_from_0": "2026-09-15",
            "effective_from_1": "00:00",
            "effective_to_0": "",
            "effective_to_1": "",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "is already enrolled on")

    # --- department mapping ----------------------------------------------

    def test_department_mapping_can_be_added_and_ended(self):
        response = self.client.post(
            reverse("devices:device_department_add", args=[self.device.public_id]),
            {
                "department": self.department.pk,
                "effective_from_0": "2026-09-01",
                "effective_from_1": "00:00",
                "effective_to_0": "",
                "effective_to_1": "",
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

    # --- form controls ----------------------------------------------------

    def test_a_checkbox_is_not_given_the_text_input_class(self):
        """class="input" carries width:100%, which collapsed it to nothing.

        Inside the flex row that renders a checkbox beside its label, a
        100%-wide control in a `flex: none` slot measured zero pixels across —
        the box was not faint, it was absent. Checkboxes get the checkbox
        component class instead.
        """
        response = self.client.get(reverse("devices:enrollment_create"))
        form = response.context["form"]
        for name in ("attendance_enabled", "assigned_device_authorized"):
            with self.subTest(field=name):
                classes = form.fields[name].widget.attrs["class"].split()
                self.assertIn("check__input", classes)
                self.assertNotIn("input", classes)

    def test_text_fields_still_get_the_input_class(self):
        """The narrowing must not have taken the class off everything else."""
        response = self.client.get(reverse("devices:enrollment_create"))
        form = response.context["form"]
        self.assertIn("input", form.fields["device_user_id"].widget.attrs["class"])

    def test_a_checkbox_is_wrapped_in_the_shared_check_component(self):
        response = self.client.get(reverse("devices:enrollment_create"))
        self.assertContains(response, 'class="check"')

    def test_both_enrollment_switches_start_on_for_a_new_enrollment(self):
        """Recognised and counted, unless somebody says otherwise.

        The company default scope is assigned-devices, so an enrollment saved
        with assigned_device_authorized off has every punch filed as
        unauthorized_device and the person reads as absent in attendance and
        unpaid in salary. Withholding it is the deliberate act.
        """
        response = self.client.get(reverse("devices:enrollment_create"))
        form = response.context["form"]
        self.assertTrue(form.fields["attendance_enabled"].initial)
        self.assertTrue(form.fields["assigned_device_authorized"].initial)
        self.assertContains(response, "checked", count=2)

    def test_editing_an_enrollment_keeps_whatever_was_saved(self):
        """Defaults are for new rows; an edit must never silently re-enable."""
        with use_company(self.company):
            enrollment = DeviceEnrollment.objects.create(
                device=self.device,
                employee=self.employee,
                device_user_id="9100",
                attendance_enabled=False,
                assigned_device_authorized=False,
                effective_from=dt(2026, 1, 1),
            )
        response = self.client.get(
            reverse("devices:enrollment_edit", args=[enrollment.pk])
        )
        form = response.context["form"]
        self.assertFalse(form["attendance_enabled"].value())
        self.assertFalse(form["assigned_device_authorized"].value())

    # --- dates and times --------------------------------------------------

    def test_no_device_form_renders_a_browser_drawn_picker(self):
        """The five that used to: install date, and both effective ranges."""
        pages = [
            reverse("devices:device_register"),
            reverse("devices:device_edit", args=[self.device.public_id]),
            reverse("devices:enrollment_create"),
            reverse("devices:device_department_add", args=[self.device.public_id]),
        ]
        for url in pages:
            with self.subTest(url=url):
                body = self.client.get(url).content.decode()
                self.assertNotIn('type="datetime-local"', body)
                self.assertNotIn('type="time"', body)

    def test_each_device_date_field_is_on_the_project_calendar(self):
        response = self.client.get(reverse("devices:enrollment_create"))
        body = response.content.decode()
        # Two halves per datetime: a date input carrying data-datepicker, and
        # an HH:MM text box.
        self.assertEqual(body.count("data-datepicker"), 2)
        self.assertEqual(body.count('placeholder="HH:MM"'), 2)

    def test_a_datetime_is_saved_as_the_company_clock_time(self):
        """09:30 typed in Dhaka is 03:30 UTC in the database."""
        response = self.client.post(reverse("devices:enrollment_create"), {
            "device": self.device.pk,
            "employee": self.employee.pk,
            "device_user_id": "7001",
            "device_privilege": DeviceEnrollment.Privilege.NORMAL_USER,
            "attendance_enabled": "on",
            "assigned_device_authorized": "on",
            "effective_from_0": "2026-10-01",
            "effective_from_1": "09:30",
            "effective_to_0": "",
            "effective_to_1": "",
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        with use_company(self.company):
            enrollment = DeviceEnrollment.objects.get(device_user_id="7001")
        self.assertEqual(
            enrollment.effective_from.astimezone(dt_timezone.utc).strftime(
                "%Y-%m-%d %H:%M"
            ),
            "2026-10-01 03:30",
        )
        self.assertIsNone(enrollment.effective_to)

    def test_editing_shows_back_the_time_that_was_entered(self):
        """The round trip an administrator actually sees."""
        self.client.post(reverse("devices:enrollment_create"), {
            "device": self.device.pk,
            "employee": self.employee.pk,
            "device_user_id": "7002",
            "device_privilege": DeviceEnrollment.Privilege.NORMAL_USER,
            "attendance_enabled": "on",
            "assigned_device_authorized": "on",
            "effective_from_0": "2026-10-01",
            "effective_from_1": "09:30",
            "effective_to_0": "",
            "effective_to_1": "",
        }, follow=True)
        with use_company(self.company):
            enrollment = DeviceEnrollment.objects.get(device_user_id="7002")
        response = self.client.get(
            reverse("devices:enrollment_edit", args=[enrollment.pk])
        )
        # Asserted against the response body, not by re-rendering the field
        # here: the widget reads the company timezone from the active tenant,
        # which only exists during the request. Re-rendering in the test would
        # quietly fall back to UTC and "pass" while showing 03:30.
        self.assertContains(response, 'name="effective_from_0" value="2026-10-01"')
        self.assertContains(response, 'name="effective_from_1" value="09:30"')

    def test_a_blank_time_is_read_as_midnight(self):
        response = self.client.post(reverse("devices:enrollment_create"), {
            "device": self.device.pk,
            "employee": self.employee.pk,
            "device_user_id": "7003",
            "device_privilege": DeviceEnrollment.Privilege.NORMAL_USER,
            "attendance_enabled": "on",
            "assigned_device_authorized": "on",
            "effective_from_0": "2026-10-02",
            "effective_from_1": "",
            "effective_to_0": "",
            "effective_to_1": "",
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        with use_company(self.company):
            enrollment = DeviceEnrollment.objects.get(device_user_id="7003")
        # Midnight in Dhaka is 18:00 UTC the previous day.
        self.assertEqual(
            enrollment.effective_from.astimezone(dt_timezone.utc).strftime(
                "%Y-%m-%d %H:%M"
            ),
            "2026-10-01 18:00",
        )

    def test_a_time_with_no_date_is_a_field_error(self):
        response = self.client.post(reverse("devices:enrollment_create"), {
            "device": self.device.pk,
            "employee": self.employee.pk,
            "device_user_id": "7004",
            "device_privilege": DeviceEnrollment.Privilege.NORMAL_USER,
            "attendance_enabled": "on",
            "assigned_device_authorized": "on",
            "effective_from_0": "",
            "effective_from_1": "09:30",
            "effective_to_0": "",
            "effective_to_1": "",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("effective_from", response.context["form"].errors)

    # --- button classes ---------------------------------------------------

    def test_no_device_page_uses_a_button_class_that_has_no_css(self):
        """btn--secondary is not in components.css, so it rendered unstyled."""
        pages = [
            reverse("devices:device_list"),
            reverse("devices:device_detail", args=[self.device.public_id]),
            reverse("devices:device_users", args=[self.device.public_id]),
            reverse("devices:enrollment_list"),
            reverse("devices:punch_list"),
            reverse("devices:message_list"),
        ]
        for url in pages:
            with self.subTest(url=url):
                self.assertNotContains(self.client.get(url), "btn--secondary")

    # --- action placement -------------------------------------------------

    def test_the_enrollment_actions_are_in_the_device_page_header(self):
        """They used to sit in the footer of the last card, below a table."""
        response = self.client.get(
            reverse("devices:device_detail", args=[self.device.public_id])
        )
        body = response.content.decode()
        header = body.split('class="page__actions"', 1)[1].split("</div>", 1)[0]
        self.assertIn("Enroll an employee", header)
        self.assertIn("Enrollments", header)
        # And not left behind at the bottom as well: the point was to move
        # them, not to add a second copy.
        after_header = body.split('class="page__actions"', 1)[1]
        self.assertNotIn("Enroll an employee", after_header.split("</div>", 1)[1])

    def test_the_punch_page_offers_its_enrollment_at_the_top(self):
        with use_company(self.company):
            enrollment = DeviceEnrollment.objects.create(
                device=self.device,
                employee=self.employee,
                device_user_id="9101",
                effective_from=dt(2026, 1, 1),
            )
            message = DeviceMessage.objects.create(
                device=self.device,
                branch=self.branch,
                message_type=DeviceMessage.MessageType.PUNCH_BATCH,
                received_at=dt(2026, 2, 1),
                raw_payload_text="x",
                payload_hash="ph-actions",
            )
            punch = PunchEvent.objects.create(
                device_message=message,
                device=self.device,
                branch=self.branch,
                device_enrollment=enrollment,
                employee=self.employee,
                device_user_id="9101",
                source_record_index=0,
                punched_at_device_raw="2026-02-01 09:00:00",
                punched_at_device=dt(2026, 2, 1),
                punched_at_utc=dt(2026, 2, 1),
                received_at=dt(2026, 2, 1),
                raw_record={},
            )
        response = self.client.get(reverse("devices:punch_detail", args=[punch.pk]))
        header = response.content.decode().split('class="page__actions"', 1)[1]
        self.assertIn("Open the enrollment used", header.split("</div>", 1)[0])

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
            "effective_from_0": "2026-09-01",
            "effective_from_1": "00:00",
            "effective_to_0": "",
            "effective_to_1": "",
        }, follow=True)

        response = self.client.post(add, {
            "department": self.department.pk,
            "effective_from_0": "2026-09-15",
            "effective_from_1": "00:00",
            "effective_to_0": "",
            "effective_to_1": "",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already serves")
        self.assertEqual(DeviceDepartment.all_objects.count(), 1)

    def test_a_mapping_may_be_re_added_once_the_previous_one_has_ended(self):
        """Ending is not deleting: the same pair must be mappable again."""
        add = reverse("devices:device_department_add", args=[self.device.public_id])
        self.client.post(add, {
            "department": self.department.pk,
            "effective_from_0": "2026-09-01",
            "effective_from_1": "00:00",
            "effective_to_0": "2026-09-10",
            "effective_to_1": "00:00",
        }, follow=True)

        response = self.client.post(add, {
            "department": self.department.pk,
            "effective_from_0": "2026-09-10",
            "effective_from_1": "00:00",
            "effective_to_0": "",
            "effective_to_1": "",
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
            "effective_from_0": "2026-09-01",
            "effective_from_1": "00:00",
            "effective_to_0": "",
            "effective_to_1": "",
        }, follow=True)
        response = self.client.post(add, {
            "department": other.pk,
            "effective_from_0": "2026-09-01",
            "effective_from_1": "00:00",
            "effective_to_0": "",
            "effective_to_1": "",
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
