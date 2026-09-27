"""The employee profile (2026-09-26): photo, personal information, the
month's summaries, leave, and the actions - for whoever may edit the person."""

import io
import shutil
import tempfile
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from attendance.services import recalculate
from auditlog.models import AuditLog
from common.tenant import use_company
from leaves.tests_branch_access import MONDAY, TwoBranchCase

MEDIA = tempfile.mkdtemp(prefix="profile-photos-")
AUGUST = {"year": "2026", "month": "8"}


def picture(name="me.png", kind="PNG"):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "teal").save(buffer, kind)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=f"image/{kind.lower()}")


@override_settings(MEDIA_ROOT=MEDIA)
class ProfileCase(TwoBranchCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        super().setUp()
        self.punch(MONDAY, 9, 40)      # 30 minutes late
        self.punch(MONDAY, 18)
        recalculate(self.company.pk, start=MONDAY, end=MONDAY)
        self.url = reverse("organization:employee_detail", args=[self.employee.pk])

    def page(self, user=None, **params):
        self.client.force_login(user or self.admin)
        return self.client.get(self.url, params)

    def fresh(self):
        self.employee.refresh_from_db()
        return self.employee


class PageTests(ProfileCase):
    def test_the_profile_has_its_tabs_and_actions(self):
        page = self.page()
        # Ajay's tabs and sections (2026-09-27).
        for text in ('data-tab="profile"', 'data-tab="attendance"', 'data-tab="leave"',
                     'data-tab="roster"', "General information", "Personal &amp; contact info",
                     "Address", "Employment history", "Remove from reports",
                     reverse("organization:employee_edit", args=[self.employee.pk])):
            self.assertContains(page, text)
        self.assertContains(page, 'class="profile-photo__initials"')    # no photo yet

    def test_the_employee_login_is_kept_out(self):
        self.assertNotEqual(self.page(user=self.clerk_user).status_code, 200)


class PhotoTests(ProfileCase):
    def upload(self, file, user=None):
        self.client.force_login(user or self.admin)
        return self.client.post(
            reverse("organization:employee_photo_change", args=[self.employee.pk]),
            {"photo": file}, follow=True)

    def test_a_photo_is_stored_under_a_random_name_and_served_privately(self):
        self.assertContains(self.upload(picture("rahim-passport.png")), "Photo saved.")
        employee = self.fresh()
        self.assertNotIn("rahim", employee.photo.name)
        self.assertRegex(employee.photo.name, r"employee_photos/[0-9a-f]{32}\.png$")
        photo_url = reverse("organization:employee_photo", args=[self.employee.pk])
        self.assertContains(self.page(), photo_url)
        served = self.client.get(photo_url)
        self.assertEqual((served.status_code, served["Content-Type"]), (200, "image/png"))
        self.assertIn("private", served["Cache-Control"])
        self.assertTrue(AuditLog.objects.filter(action="employee.photo_changed").exists())

    def test_someone_who_may_not_see_them_gets_no_photo(self):
        self.upload(picture())
        self.client.force_login(self.manager)          # Head Office's manager: may
        url = reverse("organization:employee_photo", args=[self.far.pk])
        self.assertNotEqual(self.client.get(url).status_code, 200)   # Karim: Chittagong

    def test_a_file_that_is_not_a_picture_is_refused_and_the_modal_opens_again(self):
        fake = SimpleUploadedFile("me.png", b"not really a png", content_type="image/png")
        page = self.upload(fake)
        self.assertContains(page, "not a picture we can read")
        self.assertContains(page, 'data-open-on-load="photo-dialog"')
        self.assertFalse(self.fresh().photo)

    def test_an_svg_or_other_kind_is_refused(self):
        page = self.upload(SimpleUploadedFile("me.svg", b"<svg/>", content_type="image/svg+xml"))
        self.assertContains(page, "Use a PNG, JPG or WEBP picture.")

    def test_replacing_deletes_the_old_file_and_remove_clears_it(self):
        self.upload(picture())
        first = self.fresh().photo.name
        storage = self.fresh().photo.storage
        self.upload(picture("again.jpg", "JPEG"))
        self.assertFalse(storage.exists(first))
        self.client.post(reverse("organization:employee_photo_change", args=[self.employee.pk]),
                         {"remove": "on"})
        self.assertFalse(self.fresh().photo)

    def test_a_branch_manager_elsewhere_cannot_change_it(self):
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse("organization:employee_photo_change", args=[self.far.pk]), {"photo": picture()})
        self.assertNotEqual(response.status_code, 200)


class PersonalTests(ProfileCase):
    def save(self, **values):
        self.client.force_login(self.admin)
        return self.client.post(
            reverse("organization:employee_personal", args=[self.employee.pk]),
            {"blood_group": "O+", "gender": "male", "date_of_birth": "1990-05-04",
             "emergency_contact_name": "Karima", **values}, follow=True)

    def test_it_is_saved_shown_and_audited(self):
        page = self.save()
        self.assertContains(page, "Personal information saved.")
        employee = self.fresh()
        self.assertEqual((employee.blood_group, employee.gender), ("O+", "male"))
        self.assertContains(page, "Male")                 # the word, not the stored value
        entry = AuditLog.objects.get(action="employee.personal_updated")
        self.assertEqual(entry.after_data["blood_group"], "O+")
        self.assertNotIn("national_id", entry.after_data)  # only what changed

    def test_a_birthday_in_the_future_is_refused_in_the_modal(self):
        page = self.save(date_of_birth="2999-01-01")
        self.assertContains(page, "cannot be in the future")
        self.assertContains(page, 'data-open-on-load="personal-dialog"')
        self.assertIsNone(self.fresh().date_of_birth)

    def test_the_branch_manager_of_their_branch_may(self):
        self.client.force_login(self.manager)
        self.client.post(reverse("organization:employee_personal", args=[self.employee.pk]),
                         {"blood_group": "A+"})
        self.assertEqual(self.fresh().blood_group, "A+")


class SummaryTests(ProfileCase):
    def test_the_month_is_the_reports_own_figures_for_them(self):
        summary = self.page(**AUGUST).context["summary"]
        self.assertEqual((summary["totals"]["Present"], summary["totals"]["Late"]), (1, 1))
        self.assertEqual(summary["late_minutes"], 30)
        self.assertIn("10", summary["late_dates"])
        monday = dict((day.day, code) for day, code in summary["days"])[10]
        self.assertEqual(monday, "LT")
        # The same builder the Monthly Late report uses, so they agree.
        self.client.force_login(self.admin)
        report = self.client.get(reverse("reports:monthly_late"),
                                 {**AUGUST, "employee": str(self.employee.pk)})
        self.assertEqual(report.context["result"].rows[0][5], summary["late_minutes"])

    def test_earlier_and_later_months(self):
        page = self.page(**AUGUST)
        self.assertContains(page, "?year=2026&month=7#attendance")
        self.assertContains(page, "?year=2026&month=9#attendance")

    def test_leave_tab_lists_their_leave(self):
        self.record_leave(self.employee, on=MONDAY.replace(day=12))
        leave = self.page(**AUGUST).context["leave"]
        self.assertEqual([row[3] for row in leave.rows], ["Casual"])


class ReportVisibilityTests(ProfileCase):
    def hide(self, hidden=True):
        self.client.force_login(self.admin)
        return self.client.post(
            reverse("organization:employee_report_visibility", args=[self.employee.pk]),
            {"hidden": "1" if hidden else "0"}, follow=True)

    def test_hidden_from_reports_but_not_from_their_own_profile(self):
        self.assertContains(self.hide(), "left out of the reports")
        self.assertTrue(self.fresh().hide_from_reports)
        report = self.client.get(reverse("reports:daily_attendance"), {"on": MONDAY.isoformat()})
        self.assertNotIn("Rahim", [row[1] for row in report.context["result"].rows])
        # Their attendance itself still counts, and their profile still shows it.
        self.assertEqual(self.page(**AUGUST).context["summary"]["totals"]["Present"], 1)
        self.client.force_login(self.admin)
        daily = self.client.get(reverse("attendance:attendance_list"), AUGUST)
        self.assertIn("Rahim", {r.employee.first_name for r in daily.context["page"].object_list})

    def test_shown_again_and_audited(self):
        self.hide()
        self.hide(False)
        self.assertFalse(self.fresh().hide_from_reports)
        self.assertEqual(AuditLog.objects.filter(action="employee.report_visibility").count(), 2)


class RecordLeaveFromProfileTests(ProfileCase):
    def test_record_leave_opens_with_them_chosen(self):
        self.client.force_login(self.admin)
        form = self.client.get(reverse("leaves:leave_record"),
                               {"employee": str(self.employee.pk)}).context["form"]
        self.assertEqual(form.initial["employee"], self.employee.pk)

    def test_someone_outside_their_reach_is_not_chosen(self):
        self.client.force_login(self.manager)
        form = self.client.get(reverse("leaves:leave_record"),
                               {"employee": str(self.far.pk)}).context["form"]
        self.assertNotIn("employee", form.initial)


class ImportRenameTests(ProfileCase):
    def test_a_name_changed_by_an_import_is_sent_to_the_terminals(self):
        from employees.models import EmployeeAssignment
        from organization import import_services

        with use_company(self.company):
            EmployeeAssignment.objects.filter(employee=self.employee).update(employee_code="770015")
        rows = import_services.check(self.admin, self.company.pk,
                                     [{"line": 2, "employee_id": "770015", "name": "Rahim Ahmed"}])
        with mock.patch("devices.services.mapping.resend_identity") as resend:
            import_services.commit(actor=self.admin, company_id=self.company.pk, rows=rows,
                                   branch_id=self.branch.pk)
        resend.assert_called_once()
        self.assertEqual(resend.call_args.kwargs["employee"].full_name, "Rahim Ahmed")
