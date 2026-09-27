"""A leave's document (Ajay's handover, 2026-09-27): one per leave - recorded,
requested or changed with it; required by a leave type that needs one; opened
only by the employee, whoever may see the leave, and whoever decides it."""

import datetime
import io
import shutil
import tempfile

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from auditlog.models import AuditLog
from common.tenant import use_company
from leaves import documents, services, workflow
from leaves.models import LeaveRequest
from leaves.tests_branch_access import MONDAY, TwoBranchCase

MEDIA = tempfile.mkdtemp(prefix="leave-documents-")
TUESDAY = MONDAY + datetime.timedelta(days=1)


def pdf(name="certificate.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4\n1 0 obj<<>>endobj\n%%EOF", "application/pdf")


def picture(name="note.jpg"):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (6, 6), "white").save(buffer, "JPEG")
    return SimpleUploadedFile(name, buffer.getvalue(), "image/jpeg")


def cleaned(upload):
    """What the form hands the service: the checked upload."""
    return documents.DocumentField().clean(upload)


@override_settings(MEDIA_ROOT=MEDIA)
class DocumentCase(TwoBranchCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def record(self, employee=None, document=None, day=MONDAY):
        return services.record_leave(actor=self.admin, company_id=self.company.pk, values={
            "employee": employee or self.employee, "leave_type": self.leave_type,
            "start_date": day, "end_date": day, "pay_type": "paid", "reason": "Unwell",
        }, document=cleaned(document) if document else None)

    def fresh(self, leave):
        return LeaveRequest.all_objects.get(pk=leave.pk)


class StoringTests(DocumentCase):
    def test_recorded_with_its_document_under_a_random_name(self):
        leave = self.fresh(self.record(document=pdf("rahim-medical.pdf")))
        self.assertEqual(leave.attachment_name, "rahim-medical.pdf")
        self.assertRegex(leave.attachment.name, r"leave_documents/[0-9a-f]{32}\.pdf$")
        self.assertEqual(AuditLog.objects.get(action="leave.recorded").after_data["document"],
                         "rahim-medical.pdf")

    def test_a_picture_is_accepted(self):
        leave = self.fresh(self.record(document=picture()))
        self.assertTrue(leave.attachment.name.endswith(".jpg"))

    def test_what_is_not_a_pdf_or_picture_is_refused(self):
        field = documents.DocumentField()
        for upload, said in (
            (SimpleUploadedFile("x.pdf", b"not a pdf"), "not a PDF"),
            (SimpleUploadedFile("x.png", b"not a png"), "not a picture"),
            (SimpleUploadedFile("x.svg", b"<svg/>"), "Use a PDF"),
            (SimpleUploadedFile("x.exe", b"MZ"), "Use a PDF"),
        ):
            with self.subTest(name=upload.name), self.assertRaisesMessage(ValidationError, said):
                field.clean(upload)

    def test_too_big_is_refused(self):
        original = documents.MAX_BYTES
        documents.MAX_BYTES = 10
        try:
            with self.assertRaisesMessage(ValidationError, "over 5 MB"):
                documents.DocumentField().clean(pdf())
        finally:
            documents.MAX_BYTES = original


class NeedsADocumentTests(DocumentCase):
    def setUp(self):
        super().setUp()
        self.leave_type.requires_attachment_by_default = True
        self.leave_type.save(update_fields=["requires_attachment_by_default"])

    def test_a_type_that_needs_one_is_refused_without(self):
        with self.assertRaisesMessage(ValidationError, "needs a document"):
            self.record()
        self.record(document=pdf())

    def test_the_employee_request_too(self):
        values = {"leave_type": self.leave_type, "start_date": MONDAY, "end_date": MONDAY,
                  "pay_type": "paid", "reason": "Unwell"}
        with self.assertRaisesMessage(ValidationError, "needs a document"):
            workflow.submit_request(actor=self.clerk_user, company_id=self.company.pk,
                                    values=values)
        request = workflow.submit_request(actor=self.clerk_user, company_id=self.company.pk,
                                          values=values, document=cleaned(pdf()))
        self.assertTrue(self.fresh(request).attachment)

    def test_a_changed_leave_keeps_the_one_it_has(self):
        leave = self.record(document=pdf())
        services.amend_leave(actor=self.admin, company_id=self.company.pk, request_id=leave.pk,
                             values={"leave_type": self.leave_type, "start_date": MONDAY,
                                     "end_date": TUESDAY, "pay_type": "paid", "reason": ""})
        self.assertTrue(self.fresh(leave).attachment)

    def test_the_leave_type_form_offers_it(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("leaves:leave_type_edit", args=[self.leave_type.pk]))
        self.assertContains(page, "Needs a document")


class ReplacingTests(DocumentCase):
    def test_changing_the_leave_with_a_new_one_replaces_and_removes_the_old(self):
        leave = self.record(document=pdf())
        old = self.fresh(leave).attachment
        storage, old_name = old.storage, old.name
        services.amend_leave(actor=self.admin, company_id=self.company.pk, request_id=leave.pk,
                             values={"leave_type": self.leave_type, "start_date": MONDAY,
                                     "end_date": MONDAY, "pay_type": "paid", "reason": ""},
                             document=cleaned(picture("better.jpg")))
        leave = self.fresh(leave)
        self.assertEqual(leave.attachment_name, "better.jpg")
        self.assertFalse(storage.exists(old_name))


class WhoMayOpenTests(DocumentCase):
    def setUp(self):
        super().setUp()
        # Clerk (Head Office) asks, with a document; Manny manages Head Office.
        self.request = workflow.submit_request(
            actor=self.clerk_user, company_id=self.company.pk,
            values={"leave_type": self.leave_type, "start_date": MONDAY, "end_date": MONDAY,
                    "pay_type": "paid", "reason": "Unwell"}, document=cleaned(pdf()))
        self.url = reverse("me:leave_document", args=[self.request.pk])

    def opens(self, user):
        self.client.force_login(user)
        return self.client.get(self.url).status_code == 200

    def test_the_employee_their_approver_and_the_company(self):
        for user in (self.clerk_user, self.manager, self.admin, self.hr):
            with self.subTest(user=user.email):
                self.assertTrue(self.opens(user))

    def test_nobody_else(self):
        from employees.models import Employee

        outsider = self.member("other@doc.test", "employee")
        with use_company(self.company):
            Employee.objects.filter(pk=self.far.pk).update(user=outsider)
        far_manager = self.member("far@doc.test", "manager", branches=[self.unit])
        for user in (outsider, far_manager):
            with self.subTest(user=user.email):
                self.assertFalse(self.opens(user))

    def test_served_privately(self):
        self.client.force_login(self.clerk_user)
        reply = self.client.get(self.url)
        self.assertEqual(reply["Content-Type"], "application/pdf")
        self.assertIn("private", reply["Cache-Control"])
        self.assertIn("certificate.pdf", reply["Content-Disposition"])


class PageTests(DocumentCase):
    def test_record_leave_takes_a_document(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("leaves:leave_record"), {
            "employee": self.employee.pk, "leave_type": self.leave_type.pk,
            "start_date": MONDAY.isoformat(), "end_date": MONDAY.isoformat(),
            "duration": "full_day", "pay_type": "paid", "reason": "Unwell",
            "document": pdf()})
        self.assertEqual(response.status_code, 302)
        leave = LeaveRequest.all_objects.get(employee=self.employee)
        self.assertEqual(leave.attachment_name, "certificate.pdf")
        page = self.client.get(reverse("leaves:leave_list"), {"month": "8", "year": "2026"})
        self.assertContains(page, reverse("me:leave_document", args=[leave.pk]))

    def test_a_bad_file_is_said_on_the_form(self):
        self.client.force_login(self.admin)
        page = self.client.post(reverse("leaves:leave_record"), {
            "employee": self.employee.pk, "leave_type": self.leave_type.pk,
            "start_date": MONDAY.isoformat(), "end_date": MONDAY.isoformat(),
            "duration": "full_day", "pay_type": "paid", "reason": "Unwell",
            "document": SimpleUploadedFile("x.pdf", b"nope")})
        self.assertContains(page, "not a PDF we can read")

    def test_the_employee_requests_with_one_and_both_sides_see_it(self):
        self.client.force_login(self.clerk_user)
        response = self.client.post(reverse("me:leave_request"), {
            "leave_type": self.leave_type.pk, "start_date": MONDAY.isoformat(),
            "end_date": MONDAY.isoformat(), "duration": "full_day", "pay_type": "paid",
            "reason": "Unwell", "document": picture()})
        self.assertEqual(response.status_code, 302)
        leave = LeaveRequest.all_objects.get(employee=self.clerk)
        link = reverse("me:leave_document", args=[leave.pk])
        self.assertContains(self.client.get(reverse("me:leave")), link)
        self.client.force_login(self.manager)
        self.assertContains(self.client.get(reverse("me:leave_decide", args=[leave.pk])), link)
