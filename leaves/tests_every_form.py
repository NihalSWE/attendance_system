"""Every leave form, submitted through its page by the person who uses it
(2026-09-27). Opening a form is not enough: "Unsupported field:
requires_attachment_by_default" reached Nihal because the Leave type form
showed a field the save did not accept, and only the page was ever opened."""

import datetime
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from common.tenant import use_company
from leaves.models import LeaveDay, LeaveRequest, LeaveType
from leaves.tests_branch_access import MONDAY, TwoBranchCase

MEDIA = tempfile.mkdtemp(prefix="leave-forms-")
TUESDAY = MONDAY + datetime.timedelta(days=1)
WEDNESDAY = MONDAY + datetime.timedelta(days=2)
AUGUST = {"month": "8", "year": "2026"}


def pdf():
    return SimpleUploadedFile("letter.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")


@override_settings(MEDIA_ROOT=MEDIA)
class FormCase(TwoBranchCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def post(self, user, name, data, *args, follow=True):
        self.client.force_login(user)
        response = self.client.post(reverse(name, args=args), data, follow=follow)
        self.assertEqual(response.status_code, 200, name)
        return response

    def refused(self, response):
        """The form came back with an error instead of saving."""
        form = response.context.get("form") if response.context else None
        return bool(form is not None and form.errors)

    def leaves(self, employee=None):
        return LeaveRequest.all_objects.filter(employee=employee or self.employee)


class LeaveTypeFormTests(FormCase):
    def test_add_a_leave_type_that_needs_a_document(self):
        page = self.post(self.admin, "leaves:leave_type_create", {
            "code": "SL2", "name": "Sick (certified)", "days_per_year": "14",
            "requires_attachment_by_default": "on", "description": "With a certificate"})
        self.assertFalse(self.refused(page))
        self.assertNotContains(page, "Unsupported field")
        with use_company(self.company):
            added = LeaveType.objects.get(code="SL2")
        self.assertTrue(added.requires_attachment_by_default)

    def test_add_one_that_does_not(self):
        page = self.post(self.admin, "leaves:leave_type_create",
                         {"code": "PL", "name": "Paternity", "days_per_year": "",
                          "description": ""})
        self.assertFalse(self.refused(page))
        with use_company(self.company):
            self.assertFalse(LeaveType.objects.get(code="PL").requires_attachment_by_default)

    def test_edit_turns_it_on_and_off(self):
        url = "leaves:leave_type_edit"
        base = {"code": self.leave_type.code, "name": self.leave_type.name,
                "days_per_year": "", "description": ""}
        self.post(self.admin, url, {**base, "requires_attachment_by_default": "on"},
                  self.leave_type.pk)
        self.leave_type.refresh_from_db()
        self.assertTrue(self.leave_type.requires_attachment_by_default)
        self.post(self.admin, url, base, self.leave_type.pk)
        self.leave_type.refresh_from_db()
        self.assertFalse(self.leave_type.requires_attachment_by_default)

    def test_turn_off_and_on_again(self):
        for status in ("inactive", "active"):
            with self.subTest(status=status):
                page = self.post(self.admin, "leaves:leave_type_status", {"status": status},
                                 self.leave_type.pk)
                self.assertFalse(self.refused(page))
                self.leave_type.refresh_from_db()
                self.assertEqual(self.leave_type.status, status)

    def test_add_the_default_types(self):
        self.post(self.admin, "leaves:leave_type_defaults", {})
        with use_company(self.company):
            self.assertTrue({"CL", "SL", "EL", "ML"} <= set(
                LeaveType.objects.values_list("code", flat=True)))


class RecordChangeCancelFormTests(FormCase):
    def record(self, user=None, **extra):
        return self.post(user or self.admin, "leaves:leave_record", {
            "employee": self.employee.pk, "leave_type": self.leave_type.pk,
            "start_date": MONDAY.isoformat(), "end_date": WEDNESDAY.isoformat(),
            "duration": "full_day", "pay_type": "paid", "reason": "Family", **extra})

    def test_record_full_days_with_a_document(self):
        page = self.record(document=pdf())
        self.assertFalse(self.refused(page))
        leave = self.leaves().get()
        self.assertEqual(leave.attachment_name, "letter.pdf")
        with use_company(self.company):
            self.assertEqual(LeaveDay.objects.filter(request_segment__leave_request=leave)
                             .count(), 3)

    def test_record_a_half_day(self):
        page = self.record(end_date=MONDAY.isoformat(), duration="half_day")
        self.assertFalse(self.refused(page))
        self.assertEqual(self.leaves().count(), 1)

    def test_a_branch_manager_records_for_their_branch(self):
        page = self.record(user=self.manager)
        self.assertFalse(self.refused(page))

    def test_change_then_cancel_some_days_then_all(self):
        self.record()
        leave = self.leaves().get()
        page = self.post(self.admin, "leaves:leave_amend", {
            "leave_type": self.leave_type.pk, "start_date": MONDAY.isoformat(),
            "end_date": TUESDAY.isoformat(), "duration": "full_day", "pay_type": "unpaid",
            "reason": "Shorter", "document": pdf()}, leave.pk)
        self.assertFalse(self.refused(page))
        page = self.post(self.admin, "leaves:leave_cancel",
                         {"what": "some", "work_dates": [TUESDAY.isoformat()],
                          "reason": "Came back"}, leave.pk)
        self.assertFalse(self.refused(page))
        self.assertEqual(self.leaves().get().status, "partially_cancelled")
        page = self.post(self.admin, "leaves:leave_cancel", {"reason": "All of it"}, leave.pk)
        self.assertFalse(self.refused(page))
        self.assertEqual(self.leaves().get().status, "cancelled")


class RequestDecideFormTests(FormCase):
    def ask(self, **extra):
        return self.post(self.clerk_user, "me:leave_request", {
            "leave_type": self.leave_type.pk, "start_date": MONDAY.isoformat(),
            "end_date": MONDAY.isoformat(), "duration": "full_day", "pay_type": "paid",
            "reason": "Unwell", **extra})

    def test_request_and_approve(self):
        page = self.ask(document=pdf())
        self.assertFalse(self.refused(page))
        leave = self.leaves(self.clerk).get()
        page = self.post(self.manager, "me:leave_decide",
                         {"decision": "approve", "pay_type": "paid", "reason": ""}, leave.pk)
        self.assertFalse(self.refused(page))
        self.assertEqual(self.leaves(self.clerk).get().status, "approved")

    def test_request_and_reject(self):
        self.ask()
        leave = self.leaves(self.clerk).get()
        page = self.post(self.manager, "me:leave_decide",
                         {"decision": "reject", "pay_type": "", "reason": "Busy week"},
                         leave.pk)
        self.assertFalse(self.refused(page))
        self.assertEqual(self.leaves(self.clerk).get().status, "rejected")

    def test_request_and_withdraw(self):
        self.ask()
        leave = self.leaves(self.clerk).get()
        self.post(self.clerk_user, "me:leave_withdraw", {}, leave.pk)
        self.assertEqual(self.leaves(self.clerk).get().status, "withdrawn")

    def test_a_type_that_needs_a_document_says_so_on_the_form(self):
        self.leave_type.requires_attachment_by_default = True
        self.leave_type.save(update_fields=["requires_attachment_by_default"])
        page = self.ask()
        self.assertContains(page, "needs a document")
        self.assertFalse(self.leaves(self.clerk).exists())


class ListPageTests(FormCase):
    def test_every_leave_page_opens_for_the_people_who_use_it(self):
        pages = (
            (self.admin, "leaves:leave_list", AUGUST),
            (self.admin, "leaves:leave_list", {**AUGUST, "status": "approved"}),
            (self.admin, "leaves:leave_type_list", {}),
            (self.admin, "leaves:leave_type_create", {}),
            (self.admin, "leaves:leave_record", {}),
            (self.hr, "leaves:leave_list", AUGUST),
            (self.hr, "leaves:leave_record", {}),
            (self.manager, "leaves:leave_list", AUGUST),
            (self.manager, "me:leave_inbox", {}),
            (self.manager, "me:branch_attendance", {"date": MONDAY.isoformat()}),
            (self.clerk_user, "me:leave", {}),
            (self.clerk_user, "me:leave_request", {}),
        )
        for user, name, params in pages:
            with self.subTest(user=user.email, page=name, params=params):
                self.client.force_login(user)
                self.assertEqual(self.client.get(reverse(name), params).status_code, 200)
