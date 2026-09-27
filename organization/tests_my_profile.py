"""My profile (Nihal, 2026-09-27): an employee edits their own details, photo
and education from their own login - and nothing of anyone else's.

Clerk (Head Office) has an Employee login; Rahim is someone else.
"""

import io
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from auditlog.models import AuditLog
from common.tenant import use_company
from employees.models import Employee, EmployeeEducation
from leaves.tests_branch_access import TwoBranchCase
from organization import employee_records

MEDIA = tempfile.mkdtemp(prefix="my-profile-")


def picture(name="me.png"):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, "PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), "image/png")


def _release(response):
    """Let go of a served file (Windows will not delete an open one); not
    ``response.close()``, which would also end the test's database connection."""
    for closer in response._resource_closers:
        closer()


@override_settings(MEDIA_ROOT=MEDIA)
class MyProfileCase(TwoBranchCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        super().setUp()
        self.client.force_login(self.clerk_user)

    def fresh(self):
        return Employee.all_objects.get(pk=self.clerk.pk)


class PageTests(MyProfileCase):
    def test_the_page_and_the_sidebar_link(self):
        page = self.client.get(reverse("me:profile"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Clerk")
        self.assertContains(page, "Kept by the company")
        for dialog in ("details-dialog", "photo-dialog", "education_new-dialog"):
            self.assertContains(page, f'id="{dialog}"')
        home = self.client.get(reverse("me:home"))
        self.assertContains(home, reverse("me:profile"))

    def test_a_login_without_an_employee_record(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse("me:profile")).status_code, 403)


class DetailsTests(MyProfileCase):
    def test_saved_and_audited_as_theirs(self):
        response = self.client.post(reverse("me:details"), {
            "preferred_name": "Clo", "phone": "+8801711000000", "personal_email": "clo@mail.test",
            "date_of_birth": "1995-04-02", "gender": "female", "blood_group": "O+",
            "marital_status": "single", "national_id": "1990123", "passport_number": "",
            "address": "House 1, Road 2", "emergency_contact_name": "Mum",
            "emergency_contact_phone": "+8801711000001", "emergency_contact_relation": "Mother"})
        self.assertRedirects(response, reverse("me:profile"), fetch_redirect_response=False)
        me = self.fresh()
        self.assertEqual((me.preferred_name, me.phone, me.address, me.blood_group),
                         ("Clo", "+8801711000000", "House 1, Road 2", "O+"))
        entry = AuditLog.objects.get(action="employee.personal_updated")
        self.assertEqual(entry.actor_user, self.clerk_user)
        self.assertEqual(entry.after_data["by"], "themselves")

    def test_what_the_company_keeps_is_not_theirs_to_change(self):
        self.client.post(reverse("me:details"), {
            "first_name": "Hacked", "work_email": "boss@x.test", "joining_date": "2000-01-01",
            "confirmation_date": "2000-01-01", "preferred_name": "Clo"})
        me = self.fresh()
        self.assertEqual(me.first_name, "Clerk")
        self.assertNotEqual(me.work_email, "boss@x.test")
        self.assertIsNone(me.confirmation_date)

    def test_a_refused_one_reopens_the_modal(self):
        page = self.client.post(reverse("me:details"), {"date_of_birth": "2999-01-01"})
        self.assertContains(page, 'data-open-on-load="details-dialog"')
        self.assertContains(page, "cannot be in the future")


class PhotoTests(MyProfileCase):
    def test_upload_see_and_remove(self):
        response = self.client.post(reverse("me:photo_change"), {"photo": picture()})
        self.assertRedirects(response, reverse("me:profile"), fetch_redirect_response=False)
        me = self.fresh()
        self.assertRegex(me.photo.name, r"employee_photos/[0-9a-f]{32}\.png$")
        reply = self.client.get(reverse("me:photo"))
        self.assertEqual(reply["Content-Type"], "image/png")
        self.assertIn("private", reply["Cache-Control"])
        _release(reply)
        # The same photo shows on the profile HR and managers open.
        self.client.force_login(self.admin)
        theirs = self.client.get(reverse("organization:employee_photo", args=[self.clerk.pk]))
        self.assertEqual(theirs.status_code, 200)
        _release(theirs)
        self.client.force_login(self.clerk_user)
        self.client.post(reverse("me:photo_change"), {"remove": "on"})
        self.assertFalse(self.fresh().photo)

    def test_not_a_picture_reopens_the_modal(self):
        page = self.client.post(reverse("me:photo_change"),
                                {"photo": SimpleUploadedFile("x.png", b"nope")})
        self.assertContains(page, 'data-open-on-load="photo-dialog"')


class EducationTests(MyProfileCase):
    def test_add_change_remove_their_own(self):
        self.client.post(reverse("me:education_add"), {
            "qualification": "BBA", "institution": "DU", "subject": "", "result": "",
            "passing_year": "2018", "note": ""})
        with use_company(self.company):
            row = EmployeeEducation.objects.get(employee=self.clerk)
        self.client.post(reverse("me:education_edit", args=[row.pk]), {
            "qualification": "MBA", "institution": "DU", "subject": "", "result": "",
            "passing_year": "2020", "note": ""})
        row.refresh_from_db()
        self.assertEqual(row.qualification, "MBA")
        self.client.post(reverse("me:education_remove", args=[row.pk]))
        self.assertFalse(EmployeeEducation.all_objects.filter(pk=row.pk).exists())

    def test_never_someone_elses(self):
        other = employee_records.save_education(
            actor=self.admin, company_id=self.company.pk, employee_id=self.employee.pk,
            values={"qualification": "PhD", "institution": "", "subject": "", "result": "",
                    "passing_year": None, "note": ""})
        for response in (
            self.client.post(reverse("me:education_edit", args=[other.pk]),
                             {"qualification": "X"}),
            self.client.post(reverse("me:education_remove", args=[other.pk])),
        ):
            self.assertEqual(response.status_code, 403)
        other.refresh_from_db()
        self.assertEqual(other.qualification, "PhD")


class EmployeeSeesNoCompanyTests(MyProfileCase):
    def test_a_plain_employee_sees_only_their_own_pages(self):
        page = self.client.get(reverse("me:home"))
        self.assertEqual(page.context["branch_menus"], [])
        self.assertNotContains(page, 'data-menu="employees"')
        for name in ("employee_list", "leaves:leave_list", "reports:daily_attendance"):
            response = self.client.get(reverse(name))
            self.assertIn(response.status_code, (302, 403), name)
        self.assertEqual(self.client.get(
            reverse("organization:employee_detail", args=[self.employee.pk])).status_code in
            (302, 403), True)
