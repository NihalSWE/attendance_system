"""The profile's other sections (Ajay, 2026-09-27): Approver information,
Education history, Employee documents, Enrol employee, Device permissions,
Roster and Pending approvals.

Head Office: Rahim (on the Front device), Clerk; Manny manages it.
Chittagong: Karim, no branch manager.
"""

from unittest.mock import patch
import datetime
import shutil
import tempfile
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from attendance.tests_live import UTC
from auditlog.models import AuditLog
from common.tenant import use_company
from devices.models import DeviceEnrollment
from employees.models import EmployeeDocument, EmployeeEducation
from employees.services import create_employee
from leaves import workflow
from leaves.tests_branch_access import TwoBranchCase
from organization import employee_records

MEDIA = tempfile.mkdtemp(prefix="profile-sections-")
TODAY = datetime.date.today()


def pdf(name="nid.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4\n%%EOF", "application/pdf")


@override_settings(MEDIA_ROOT=MEDIA)
class SectionCase(TwoBranchCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def url(self, name, *args, employee=None):
        return reverse(f"organization:{name}", args=[(employee or self.employee).pk, *args])

    def profile(self, employee=None, user=None):
        if user is not None:
            self.client.force_login(user)
        return self.client.get(self.url("employee_detail", employee=employee))

    def assertModalOpen(self, response, dialog):
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'data-open-on-load="{dialog}"')


class ApproverTests(SectionCase):
    def test_their_branch_manager_decides_their_leave(self):
        page = self.profile()
        approvers = page.context["approvers"]
        self.assertEqual(approvers["branch_managers"], ["manny@liv.test"])
        self.assertEqual(approvers["leave"], ["manny@liv.test"])
        self.assertFalse(approvers["leave_by_company"])
        self.assertIn("hr@liv.test", approvers["attendance"])
        self.assertContains(page, "Approver information")

    def test_a_branch_without_a_manager_goes_to_the_company(self):
        approvers = self.profile(employee=self.far).context["approvers"]
        self.assertTrue(approvers["leave_by_company"])
        self.assertEqual(approvers["leave"], ["admin@liv.test"])

    @patch("access_control.branch_access.HEAD_ACCESS", True)
    def test_someone_given_approve_leave_and_the_department_head(self):
        self.grant("leave.approve", self.branch)
        with use_company(self.company):
            department = self.employee.assignments.get().department
            department.head = self.far
            department.save(update_fields=["head"])
        approvers = self.profile().context["approvers"]
        self.assertIn("Clerk", approvers["leave"])
        self.assertIn("Karim", approvers["leave"])
        self.assertEqual(approvers["department_head"], self.far)


class EducationTests(SectionCase):
    def test_add_change_and_remove(self):
        response = self.client.post(self.url("employee_education_add"), {
            "qualification": "BSc", "institution": "BUET", "subject": "CSE",
            "result": "3.8", "passing_year": "2019", "note": ""})
        self.assertRedirects(response, self.url("employee_detail") + "#profile",
                             fetch_redirect_response=False)
        with use_company(self.company):
            row = EmployeeEducation.objects.get(employee=self.employee)
        page = self.profile()
        self.assertContains(page, "BUET")
        self.assertContains(page, f'id="education_{row.pk}-dialog"')
        self.client.post(self.url("employee_education_edit", row.pk), {
            "qualification": "BSc", "institution": "BUET", "subject": "EEE",
            "result": "3.9", "passing_year": "2019", "note": ""})
        row.refresh_from_db()
        self.assertEqual(row.subject, "EEE")
        self.client.post(self.url("employee_education_remove", row.pk))
        self.assertFalse(EmployeeEducation.all_objects.filter(pk=row.pk).exists())
        self.assertEqual(
            list(AuditLog.objects.filter(action__startswith="employee.education")
                 .order_by("pk").values_list("action", flat=True)),
            ["employee.education_added", "employee.education_changed",
             "employee.education_removed"])

    def test_a_refused_one_reopens_its_modal(self):
        response = self.client.post(self.url("employee_education_add"), {
            "qualification": "", "passing_year": "1800"})
        self.assertModalOpen(response, "education_new-dialog")
        self.assertContains(response, "Give the year it was passed")

    def test_view_only_sees_but_cannot_add(self):
        self.grant("employees.view", self.branch)
        page = self.profile(user=self.clerk_user)
        self.assertNotContains(page, 'id="education_new-dialog"')
        response = self.client.post(self.url("employee_education_add"), {"qualification": "X"})
        self.assertEqual(response.status_code, 403)


class DocumentTests(SectionCase):
    def add(self, upload=None, **extra):
        return self.client.post(self.url("employee_document_add"), {
            "kind": "national_id", "title": "National ID card", "note": "",
            "file": upload or pdf("rahim-nid.pdf"), **extra})

    def test_added_under_a_random_name_and_served_privately(self):
        self.assertRedirects(self.add(), self.url("employee_detail") + "#profile",
                             fetch_redirect_response=False)
        with use_company(self.company):
            document = EmployeeDocument.objects.get(employee=self.employee)
        self.assertRegex(document.file.name, r"employee_documents/[0-9a-f]{32}\.pdf$")
        self.assertEqual(document.file_name, "rahim-nid.pdf")
        link = self.url("employee_document", document.pk)
        self.assertContains(self.profile(), link)
        reply = self.client.get(link)
        self.assertEqual(reply["Content-Type"], "application/pdf")
        self.assertIn("private", reply["Cache-Control"])
        # A branch manager elsewhere cannot open it.
        far_manager = self.member("far@liv.test", "manager", branches=[self.unit])
        self.client.force_login(far_manager)
        self.assertEqual(self.client.get(link).status_code, 403)

    def test_a_bad_file_reopens_the_modal(self):
        response = self.add(SimpleUploadedFile("x.pdf", b"not a pdf"))
        self.assertModalOpen(response, "document-dialog")
        self.assertContains(response, "not a PDF we can read")

    def test_removed_with_its_file(self):
        self.add()
        with use_company(self.company):
            document = EmployeeDocument.objects.get(employee=self.employee)
        storage, name = document.file.storage, document.file.name
        self.client.post(self.url("employee_document_remove", document.pk))
        self.assertFalse(EmployeeDocument.all_objects.filter(pk=document.pk).exists())
        self.assertFalse(storage.exists(name))
        self.assertTrue(AuditLog.objects.filter(action="employee.document_removed").exists())


class DeviceTests(SectionCase):
    def test_permissions_are_changed_and_audited_for_history(self):
        page = self.profile()
        self.assertContains(page, f'id="device_{self.enrollment.pk}-dialog"')
        response = self.client.post(self.url("employee_device_permission", self.enrollment.pk), {
            "attendance_enabled": "", "assigned_device_authorized": "on",
            "card_number": "", "device_privilege": self.enrollment.device_privilege})
        self.assertRedirects(response, self.url("employee_detail") + "#device-permissions",
                             fetch_redirect_response=False)
        self.enrollment.refresh_from_db()
        self.assertFalse(self.enrollment.attendance_enabled)
        entry = AuditLog.objects.get(action="device_enrollment.updated",
                                     object_id=str(self.enrollment.pk))
        # Historical punches are judged by before_data: every field it touched.
        self.assertEqual(set(entry.before_data), {"attendance_enabled",
                                                  "assigned_device_authorized",
                                                  "card_number", "device_privilege"})
        self.assertTrue(entry.before_data["attendance_enabled"])

    def test_only_whoever_manages_devices(self):
        self.client.force_login(self.hr)
        page = self.profile()
        self.assertNotContains(page, f'id="device_{self.enrollment.pk}-dialog"')
        response = self.client.post(self.url("employee_device_permission", self.enrollment.pk), {
            "attendance_enabled": "", "assigned_device_authorized": "on",
            "card_number": "", "device_privilege": self.enrollment.device_privilege})
        self.assertEqual(response.status_code, 403)

    def test_enrol_on_a_branch_device(self):
        newcomer = create_employee(
            company=self.company, first_name="Nadia", employee_code="105",
            branch=self.branch, department=self.hq_department,
            designation=self.hq_designation,
            effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC),
            pay_basis="monthly", base_rate=Decimal("20000"),
        )["employee"]
        page = self.profile(employee=newcomer)
        self.assertContains(page, f'id="enrol-{self.device.pk}-dialog"')
        back = self.url("employee_detail", employee=newcomer) + "#enrol"
        response = self.client.post(reverse("devices:employee_map"), {
            "employee": newcomer.pk, "device": self.device.pk,
            "start_day": TODAY.isoformat(), "attendance_enabled": "on", "assigned": "on",
            "next": back})
        self.assertRedirects(response, back, fetch_redirect_response=False)
        self.assertTrue(DeviceEnrollment.all_objects.filter(
            employee=newcomer, device=self.device, device_user_id="105").exists())

    def test_an_id_that_cannot_go_on_a_device_is_said(self):
        page = self.profile(employee=self.clerk)                       # E2
        self.assertContains(page, "must be digits only")
        self.assertNotContains(page, f'id="enrol-{self.device.pk}-dialog"')


class RosterTests(SectionCase):
    def test_the_next_two_weeks_with_leave(self):
        tomorrow = TODAY + datetime.timedelta(days=1)
        self.record_leave(self.employee, on=tomorrow)
        rows = self.profile().context["roster"]
        self.assertEqual(len(rows), employee_records.ROSTER_DAYS)
        self.assertEqual(rows[0]["day"], TODAY)
        day = next(r for r in rows if r["day"] == tomorrow)
        self.assertEqual(day["leave"], "Casual")
        self.assertTrue(all(r["shift"] == self.shift for r in rows if not r["off"]))


class PendingTests(SectionCase):
    def test_a_waiting_leave_request_and_missed_scan(self):
        request = workflow.submit_request(
            actor=self.clerk_user, company_id=self.company.pk,
            values={"leave_type": self.leave_type, "start_date": TODAY, "end_date": TODAY,
                    "pay_type": "paid", "reason": "Unwell"})
        page = self.profile(employee=self.clerk)
        pending = page.context["pending"]
        self.assertEqual([r.pk for r in pending["leave"]], [request.pk])
        self.assertEqual(pending["leave"][0].type_name, "Casual")
        self.assertContains(page, reverse("me:leave_decide", args=[request.pk]))
        self.assertContains(page, 'data-tab="pending"')

    def test_nothing_waiting(self):
        page = self.profile()
        self.assertEqual(page.context["pending"]["total"], 0)
        self.assertContains(page, "Nothing is waiting for a decision")

    def test_leave_hidden_from_someone_who_may_not_see_leave(self):
        workflow.submit_request(
            actor=self.clerk_user, company_id=self.company.pk,
            values={"leave_type": self.leave_type, "start_date": TODAY, "end_date": TODAY,
                    "pay_type": "paid", "reason": "Unwell"})
        self.grant("employees.view", self.branch, to=self.employee)
        with use_company(self.company):
            self.employee.user = self.member("rahim@liv.test", "employee")
            self.employee.save(update_fields=["user"])
        page = self.profile(employee=self.clerk, user=self.employee.user)
        self.assertEqual(page.context["pending"]["leave"], [])
