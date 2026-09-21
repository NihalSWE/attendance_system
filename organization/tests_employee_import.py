"""Bulk employee import: EMP-ID and Name in the file, the branch on the form.

TwoBranchCase: Head Office (the company's default branch, where Manny is the
branch manager) and Chittagong. The company picks the branch, starting on the
default; a branch manager is locked to their own. Everyone lands in the
branch's Unassigned department with no salary, exactly as a device import
does, and HR fills in the rest on Edit employee.
"""

import csv
import datetime
import io
from zoneinfo import ZoneInfo

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from auditlog.models import AuditLog
from common.tenant import use_company
from devices.services.mapping import UNASSIGNED_CODE
from employees.models import Employee, EmployeeAssignment, EmployeeCompensation
from leaves.tests_branch_access import TwoBranchCase
from organization import import_services
from organization.models import Branch, Department


class ImportCase(TwoBranchCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("organization:employee_import")
        self.confirm_url = reverse("organization:employee_import_confirm")

    def csv_file(self, rows, *, name="people.csv", headings=("EMP-ID", "Name")):
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(headings)
        for row in rows:
            writer.writerow(row)
        return SimpleUploadedFile(name, buffer.getvalue().encode("utf-8"),
                                  content_type="text/csv")

    def upload(self, rows, *, user=None, branch=None, **file_options):
        self.client.force_login(user or self.admin)
        payload = {"upload": self.csv_file(rows, **file_options)}
        if branch is not None:
            payload["branch"] = branch.pk
        elif user is None or user == self.admin:
            payload["branch"] = self.branch.pk      # what the dropdown starts on
        return self.client.post(self.url, payload)

    def confirm(self):
        return self.client.post(self.confirm_url, follow=True)

    def count(self):
        with use_company(self.company):
            return Employee.objects.count()

    def placement(self, code):
        with use_company(self.company):
            return EmployeeAssignment.objects.select_related(
                "branch", "department", "designation", "employee").get(employee_code=code)

    def upload_error(self, page):
        return " ".join(str(m) for m in page.context["form"].errors.get("upload", []))


class DemoFileTests(ImportCase):
    def test_the_demo_file_downloads_in_the_format_it_asks_for(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("organization:employee_import_demo"))
        self.assertEqual(page.status_code, 200)
        self.assertIn("text/csv", page["Content-Type"])
        self.assertIn("employee-import-demo.csv", page["Content-Disposition"])
        text = page.content.decode("utf-8-sig")
        self.assertEqual(text.splitlines()[0], "EMP-ID,Name")
        # What the demo writes is what the import reads.
        rows = import_services.read_file(SimpleUploadedFile("demo.csv", page.content))
        self.assertEqual(len(rows), len(import_services.DEMO_ROWS))
        self.assertEqual((rows[0]["employee_id"], rows[0]["name"]), ("445961", "Sajal Ahmed"))

    def test_a_branch_manager_can_download_it_too(self):
        self.client.force_login(self.manager)
        page = self.client.get(reverse("organization:employee_import_demo"))
        self.assertEqual(page.status_code, 200)


class ReadingTheFileTests(ImportCase):
    def test_a_missing_heading_is_named(self):
        page = self.upload([["445962"]], headings=("EMP-ID",))
        self.assertIn("Missing: Name", self.upload_error(page))

    def test_other_spellings_of_the_headings_are_accepted(self):
        for headings in (("Employee ID", "Name"), ("emp id", "NAME"), ("EMPID", "Full name")):
            with self.subTest(headings=headings):
                rows = import_services.read_file(
                    self.csv_file([["445962", "Ajay Kumar"]], headings=headings))
                self.assertEqual(rows[0]["employee_id"], "445962")

    def test_extra_columns_are_ignored(self):
        rows = import_services.read_file(self.csv_file(
            [["x", "445962", "Ajay Kumar"]], headings=("Notes", "EMP-ID", "Name")))
        self.assertEqual((rows[0]["employee_id"], rows[0]["name"]), ("445962", "Ajay Kumar"))

    def test_an_empty_file_is_refused(self):
        self.client.force_login(self.admin)
        page = self.client.post(self.url, {
            "branch": self.branch.pk,
            "upload": SimpleUploadedFile("e.csv", b"", content_type="text/csv")})
        self.assertTrue(page.context["form"].errors)
        self.assertEqual(self.count(), 3)

    def test_headings_with_no_people_is_refused(self):
        self.assertIn("no people", self.upload_error(self.upload([])))

    def test_another_file_type_is_refused(self):
        self.client.force_login(self.admin)
        page = self.client.post(self.url, {
            "branch": self.branch.pk,
            "upload": SimpleUploadedFile("people.pdf", b"%PDF-1.4")})
        self.assertIn(".csv", self.upload_error(page))

    def test_too_many_rows_is_refused(self):
        original = import_services.MAX_ROWS
        import_services.MAX_ROWS = 2
        try:
            page = self.upload([[str(500000 + n), f"P {n}"] for n in range(3)])
        finally:
            import_services.MAX_ROWS = original
        self.assertIn("3 people", self.upload_error(page))

    def test_an_excel_file_is_read_too(self):
        from openpyxl import Workbook

        book = Workbook()
        book.active.append(["EMP-ID", "Name"])
        book.active.append([445962, "Ajay Kumar"])        # a number, as Excel keeps it
        buffer = io.BytesIO()
        book.save(buffer)
        rows = import_services.read_file(SimpleUploadedFile("p.xlsx", buffer.getvalue()))
        self.assertEqual(rows[0]["employee_id"], "445962")   # not "445962.0"


class PreviewTests(ImportCase):
    def test_a_good_file_previews_and_writes_nothing(self):
        page = self.upload([["445962", "Ajay Kumar"], ["445963", "Dia Rahman"]])
        preview = page.context["preview"]
        self.assertEqual(preview["counts"], {"total": 2, "bad": 0, "good": 2})
        self.assertEqual(preview["branch"], self.branch)
        self.assertEqual(self.count(), 3)
        self.assertContains(page, "Import 2 employees")

    def test_every_bad_row_is_named_with_its_line(self):
        page = self.upload([
            ["44A962", "Letters In Id"],
            ["445963", "Good Person"],
            ["", "No Id"],
            ["445964", ""],
            ["445963", "Same Id Again"],
        ])
        preview = page.context["preview"]
        self.assertEqual([row["line"] for row in preview["bad"]], [2, 4, 5, 6])
        said = " ".join(e for row in preview["bad"] for e in row["errors"])
        for phrase in ("is not a number", "EMP-ID is missing", "Name is missing",
                       "also on row 3"):
            self.assertIn(phrase, said)
        self.assertNotContains(page, "Import 1 employee")

    def test_an_emp_id_already_in_the_company_is_refused(self):
        with use_company(self.company):
            EmployeeAssignment.objects.filter(employee_code="E1").update(employee_code="445962")
        page = self.upload([["445962", "Clash"]])
        self.assertIn("already belongs to an employee",
                      page.context["preview"]["bad"][0]["errors"][0])


class CompanyBranchTests(ImportCase):
    def test_the_dropdown_starts_on_the_default_branch_and_lists_every_branch(self):
        self.client.force_login(self.admin)
        form = self.client.get(self.url).context["form"]
        self.assertEqual(form.fields["branch"].initial, self.branch.pk)
        self.assertFalse(form.fields["branch"].disabled)
        with use_company(self.company):
            self.assertEqual(set(form.fields["branch"].queryset), {self.branch, self.unit})

    def test_leaving_the_default_puts_everyone_in_the_default_branch(self):
        self.upload([["445962", "Ajay Kumar"]])
        self.confirm()
        self.assertEqual(self.placement("445962").branch, self.branch)

    def test_choosing_another_branch_puts_everyone_there(self):
        page = self.upload([["445962", "Ajay Kumar"], ["445963", "Dia Rahman"]],
                           branch=self.unit)
        self.assertContains(page, "joining Chittagong")
        self.confirm()
        self.assertEqual(self.placement("445962").branch, self.unit)
        self.assertEqual(self.placement("445963").branch, self.unit)

    def test_clearing_the_dropdown_means_the_default_branch(self):
        self.client.force_login(self.admin)
        page = self.client.post(self.url, {
            "branch": "", "upload": self.csv_file([["445962", "Ajay Kumar"]])})
        self.assertEqual(page.context["preview"]["branch"], self.branch)
        self.confirm()
        self.assertEqual(self.placement("445962").branch, self.branch)

    def test_no_branch_at_all_means_the_default_in_the_service(self):
        self.assertEqual(import_services.check_branch(self.admin, self.company.pk), self.branch)


class BranchManagerTests(ImportCase):
    def test_the_branch_is_locked_to_their_own(self):
        self.client.force_login(self.manager)
        page = self.client.get(self.url)
        field = page.context["form"].fields["branch"]
        self.assertTrue(field.disabled)
        self.assertEqual(field.initial, self.branch.pk)
        self.assertContains(page, "You add people to your own branch.")

    def test_they_import_into_their_own_branch(self):
        page = self.upload([["445962", "Ajay Kumar"]], user=self.manager)
        self.assertEqual(page.context["preview"]["branch"], self.branch)
        self.confirm()
        self.assertEqual(self.placement("445962").branch, self.branch)

    def test_posting_another_branch_is_ignored(self):
        """The locked field keeps its own value whatever the request says."""
        page = self.upload([["445962", "Ajay Kumar"]], user=self.manager, branch=self.unit)
        self.assertEqual(page.context["preview"]["branch"], self.branch)
        self.confirm()
        self.assertEqual(self.placement("445962").branch, self.branch)

    def test_the_service_refuses_another_branch(self):
        with self.assertRaises(PermissionDenied):
            import_services.check_branch(self.manager, self.company.pk, self.unit.pk)
        with self.assertRaises(PermissionDenied):
            import_services.commit(actor=self.manager, company_id=self.company.pk,
                                   rows=[{"line": 2, "employee_id": "445962",
                                          "name": "Sneaky"}],
                                   branch_id=self.unit.pk)
        self.assertEqual(self.count(), 3)

    def test_a_manager_of_two_branches_chooses_between_those_two_only(self):
        with use_company(self.company):
            Branch.objects.create(company=self.company, code="SYL", name="Sylhet",
                                  timezone="Asia/Dhaka", country_code="BD")
        both = self.member("both@liv.test", "manager", branches=[self.branch, self.unit])
        self.client.force_login(both)
        field = self.client.get(self.url).context["form"].fields["branch"]
        self.assertFalse(field.disabled)
        with use_company(self.company):
            self.assertEqual(set(field.queryset), {self.branch, self.unit})


class ConfirmTests(ImportCase):
    def test_they_are_created_like_a_device_import(self):
        before = self.count()
        self.upload([["445962", "Ajay Kumar"], ["445963", "Md Fazle Rabbi"]])
        page = self.confirm()
        self.assertEqual(self.count(), before + 2)
        self.assertContains(page, "2 employees imported")

        placement = self.placement("445962")
        employee = placement.employee
        self.assertEqual((employee.first_name, employee.last_name), ("Ajay", "Kumar"))
        self.assertEqual(self.placement("445963").employee.full_name, "Md Fazle Rabbi")
        self.assertEqual(employee.employment_status, "active")
        self.assertTrue(employee.metadata["needs_hr_review"])
        # The branch's Unassigned department and designation, as a device import uses.
        self.assertEqual(placement.department.code, UNASSIGNED_CODE)
        self.assertEqual(placement.designation.code, UNASSIGNED_CODE)
        self.assertEqual(placement.department.branch_id, self.branch.pk)
        # No salary: payroll skips them by name until one is set.
        with use_company(self.company):
            self.assertFalse(EmployeeCompensation.objects.filter(employee=employee).exists())

    def test_they_start_at_midnight_today(self):
        self.upload([["445962", "Ajay Kumar"]])
        self.confirm()
        placement = self.placement("445962")
        zone = ZoneInfo(self.company.timezone or "UTC")
        local = placement.effective_from.astimezone(zone)
        self.assertEqual((local.hour, local.minute), (0, 0))
        self.assertEqual(local.date(), datetime.datetime.now(zone).date())
        self.assertEqual(placement.employee.joining_date, local.date())

    def test_setting_the_department_from_today_corrects_the_row(self):
        """The follow-up on Edit employee fixes the placement, adds no history."""
        from organization.employee_edit_services import change_placement

        self.upload([["445962", "Ajay Kumar"]])
        self.confirm()
        placement = self.placement("445962")
        change_placement(actor=self.admin, company_id=self.company.pk,
                         employee_id=placement.employee_id, values={
                             "branch": self.branch, "department": self.hq_department,
                             "designation": self.hq_designation,
                             "employee_code": "445962",
                             "effective_at": placement.effective_from, "reason": "",
                         })
        with use_company(self.company):
            rows = EmployeeAssignment.objects.filter(employee_id=placement.employee_id)
            self.assertEqual(rows.count(), 1)
            self.assertEqual(rows.get().department, self.hq_department)

    def test_a_second_import_reuses_the_same_unassigned_department(self):
        self.upload([["445962", "Ajay Kumar"]])
        self.confirm()
        self.upload([["445963", "Dia Rahman"]])
        self.confirm()
        with use_company(self.company):
            self.assertEqual(Department.objects.filter(
                branch=self.branch, code=UNASSIGNED_CODE).count(), 1)

    def test_one_bad_row_means_nothing_is_written(self):
        self.upload([["445962", "Good"], ["44A963", "Bad"]])
        page = self.confirm()
        self.assertEqual(self.count(), 3)
        self.assertContains(page, "can no longer be imported")

    def test_an_emp_id_taken_after_the_preview_rolls_everything_back(self):
        self.upload([["445962", "Ajay Kumar"], ["445963", "Dia Rahman"]])
        with use_company(self.company):
            EmployeeAssignment.objects.filter(employee_code="E1").update(employee_code="445963")
        page = self.confirm()
        self.assertEqual(self.count(), 3)
        self.assertContains(page, "can no longer be imported")

    def test_confirming_twice_imports_once(self):
        self.upload([["445962", "Ajay Kumar"]])
        self.confirm()
        before = self.count()
        self.assertContains(self.confirm(), "no longer waiting")
        self.assertEqual(self.count(), before)

    def test_the_import_is_audited_on_one_line(self):
        self.upload([["445962", "Ajay Kumar"], ["445963", "Dia Rahman"]])
        self.confirm()
        entry = AuditLog.objects.get(action="employees.imported")
        self.assertEqual(entry.actor_user_id, self.admin.pk)
        self.assertEqual(entry.after_data["count"], 2)
        self.assertEqual(entry.after_data["branch_id"], self.branch.pk)
        self.assertEqual(entry.after_data["employee_codes"], ["445962", "445963"])


class WhoMayImportTests(ImportCase):
    def test_an_employee_login_never_reaches_it(self):
        self.client.force_login(self.clerk_user)
        for url in (self.url, reverse("organization:employee_import_demo")):
            with self.subTest(url=url):
                page = self.client.get(url)
                self.assertEqual(page.status_code, 302)
                self.assertIn("/me/", page["Location"])
        self.assertIn(self.client.post(self.confirm_url).status_code, (302, 403))
        self.assertEqual(self.count(), 3)

    def test_the_services_refuse_them_too(self):
        rows = [{"line": 2, "employee_id": "445962", "name": "Sneaky"}]
        with self.assertRaises(PermissionDenied):
            import_services.check(self.clerk_user, self.company.pk, rows)
        with self.assertRaises(PermissionDenied):
            import_services.commit(actor=self.clerk_user, company_id=self.company.pk,
                                   rows=rows, branch_id=self.branch.pk)
        self.assertEqual(self.count(), 3)

    def test_the_employees_list_links_to_it(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("employee_list")), self.url)

    def test_the_page_shows_the_format_and_the_demo_link(self):
        self.client.force_login(self.admin)
        page = self.client.get(self.url)
        self.assertContains(page, "EMP-ID")
        self.assertContains(page, "Download demo file (CSV)")
        self.assertIsNone(page.context["preview"])
