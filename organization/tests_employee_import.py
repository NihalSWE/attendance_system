"""Bulk employee import: the file, the preview, and the all-or-nothing write.

TwoBranchCase is the right fixture for this on purpose: Head Office and
Chittagong each hold a department called "Software" with a designation called
"Developer", which is what a company gets by copying one branch's structure
into another. The import has to resolve those per branch, not give up on the
word — so that case is tested first.
"""

import csv
import datetime
import io
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from auditlog.models import AuditLog
from common.tenant import use_company
from employees.models import Employee, EmployeeAssignment, EmployeeCompensation
from leaves.tests_branch_access import TwoBranchCase
from organization import import_services
from organization.models import Department

IMPORT_URL = "/organization/employees/import/"


class ImportCase(TwoBranchCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("organization:employee_import")
        self.confirm_url = reverse("organization:employee_import_confirm")

    # --- building files ---------------------------------------------------

    def row(self, code, name, *, branch=None, department="Software",
            designation="Developer", joining="2026-03-01", basis="monthly",
            rate="25000", phone="", email=""):
        return [code, name, branch or self.branch.name, department, designation,
                joining, basis, rate, phone, email]

    def csv_file(self, rows, *, name="people.csv", headings=None):
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(import_services.HEADINGS if headings is None else headings)
        for row in rows:
            writer.writerow(row)
        return SimpleUploadedFile(
            name, buffer.getvalue().encode("utf-8"), content_type="text/csv")

    def upload(self, rows, *, user=None, follow=False, **extra):
        self.client.force_login(user or self.admin)
        payload = {"upload": self.csv_file(rows)}
        payload.update(extra)
        return self.client.post(self.url, payload, follow=follow)

    def confirm(self, user=None):
        return self.client.post(self.confirm_url, follow=True)

    def employee_count(self):
        with use_company(self.company):
            return Employee.objects.count()

    def placement(self, code):
        with use_company(self.company):
            return EmployeeAssignment.objects.select_related(
                "branch", "department", "designation", "employee"
            ).get(employee_code=code)


class TemplateTests(ImportCase):
    def test_the_excel_template_downloads_and_reads_back(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("organization:employee_import_template"))
        self.assertEqual(page.status_code, 200)
        self.assertIn("spreadsheetml", page["Content-Type"])
        self.assertIn("employee-import-template.xlsx", page["Content-Disposition"])
        # What it writes is what it accepts.
        rows = import_services.read_file(
            SimpleUploadedFile("t.xlsx", page.content))
        self.assertEqual(rows[0]["employee_id"], "445962")
        self.assertEqual(rows[0]["name"], "Ajay Kumar")

    def test_the_csv_template_downloads_with_the_same_headings(self):
        self.client.force_login(self.admin)
        page = self.client.get(
            reverse("organization:employee_import_template") + "?format=csv")
        self.assertEqual(page.status_code, 200)
        first_line = page.content.decode("utf-8-sig").splitlines()[0]
        self.assertEqual(first_line.split(","), import_services.HEADINGS)


class ThePageTests(ImportCase):
    def test_it_opens_with_the_headings_and_the_notes(self):
        self.client.force_login(self.admin)
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        for heading in import_services.HEADINGS:
            self.assertContains(page, heading)
        self.assertContains(page, "Download template (Excel)")
        self.assertIsNone(page.context["preview"])

    def test_the_employees_list_offers_it_to_whoever_may_create(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("employee_list")), self.url)


class ReadingTheFileTests(ImportCase):
    def upload_error(self, response):
        return " ".join(str(m) for m in response.context["form"].errors["upload"])

    def test_a_missing_heading_is_refused_by_name(self):
        headings = [h for h in import_services.HEADINGS if h != "Branch"]
        self.client.force_login(self.admin)
        page = self.client.post(self.url, {
            "upload": self.csv_file([["1", "A", "x", "y", "2026-01-01", "", "", "", ""]],
                                    headings=headings)})
        self.assertIn("Branch", self.upload_error(page))

    def test_an_empty_file_is_refused(self):
        self.client.force_login(self.admin)
        page = self.client.post(self.url, {
            "upload": SimpleUploadedFile("e.csv", b"", content_type="text/csv")})
        self.assertTrue(page.context["form"].errors)

    def test_headings_but_no_people_is_refused(self):
        page = self.upload([])
        self.assertIn("no people", self.upload_error(page))

    def test_an_old_xls_file_says_what_to_do(self):
        self.client.force_login(self.admin)
        page = self.client.post(self.url, {
            "upload": SimpleUploadedFile("old.xls", b"\xd0\xcf\x11\xe0rubbish")})
        self.assertIn(".xlsx", self.upload_error(page))

    def test_an_unknown_extension_is_refused(self):
        self.client.force_login(self.admin)
        page = self.client.post(self.url, {
            "upload": SimpleUploadedFile("people.pdf", b"%PDF-1.4 ...")})
        self.assertIn(".csv", self.upload_error(page))

    def test_too_many_rows_is_refused_before_any_checking(self):
        rows = [self.row(str(500000 + n), f"P{n}") for n in range(3)]
        original = import_services.MAX_ROWS
        import_services.MAX_ROWS = 2
        try:
            page = self.upload(rows)
        finally:
            import_services.MAX_ROWS = original
        self.assertIn("3 rows", self.upload_error(page))
        self.assertEqual(self.employee_count(), 3)   # nothing read, nothing written

    def test_an_excel_file_is_read_like_a_csv(self):
        from openpyxl import Workbook

        book = Workbook()
        sheet = book.active
        sheet.append(import_services.HEADINGS)
        # Numbers and a real date, as Excel actually stores them.
        sheet.append([445962, "Ajay Kumar", self.branch.name, "Software", "Developer",
                      datetime.date(2026, 3, 1), "monthly", 25000, "", ""])
        buffer = io.BytesIO()
        book.save(buffer)

        rows = import_services.read_file(
            SimpleUploadedFile("people.xlsx", buffer.getvalue()))
        self.assertEqual(rows[0]["employee_id"], "445962")     # not "445962.0"
        self.assertEqual(rows[0]["joining_date"], "2026-03-01")
        self.assertEqual(rows[0]["base_rate"], "25000")


class PreviewTests(ImportCase):
    def rows_of(self, page):
        return page.context["preview"]

    def test_a_good_file_previews_without_writing_anything(self):
        before = self.employee_count()
        page = self.upload([self.row("445962", "Ajay Kumar"),
                            self.row("445963", "Dia Rahman")])
        preview = self.rows_of(page)
        self.assertEqual(preview["counts"], {"total": 2, "bad": 0, "good": 2})
        self.assertEqual(self.employee_count(), before)
        self.assertContains(page, "Import 2 employees")

    def test_the_same_department_name_resolves_inside_each_branch(self):
        """Head Office and Chittagong both have Software / Developer."""
        page = self.upload([
            self.row("445962", "Ajay Kumar", branch=self.branch.name),
            self.row("445963", "Karim Two", branch=self.unit.name),
        ])
        self.assertEqual(self.rows_of(page)["counts"]["bad"], 0)
        self.confirm()
        self.assertEqual(self.placement("445962").branch_id, self.branch.pk)
        self.assertEqual(self.placement("445963").branch_id, self.unit.pk)
        self.assertEqual(self.placement("445963").department.branch_id, self.unit.pk)

    def test_a_department_of_another_branch_is_named_not_guessed(self):
        with use_company(self.company):
            Department.objects.create(
                company=self.company, branch=self.unit, code="SUP", name="Support")
        page = self.upload([self.row("445962", "Ajay Kumar", department="Support")])
        errors = self.rows_of(page)["bad"][0]["errors"]
        self.assertTrue(any("not a department of" in e for e in errors), errors)

    def test_a_designation_of_another_department_is_named(self):
        with use_company(self.company):
            support = Department.objects.create(
                company=self.company, branch=self.branch, code="SUP", name="Support")
            from organization.models import Designation
            Designation.objects.create(
                company=self.company, department=support, code="AGT", name="Agent")
        page = self.upload([self.row("445962", "Ajay Kumar", designation="Agent")])
        errors = self.rows_of(page)["bad"][0]["errors"]
        self.assertTrue(any("does not belong to" in e for e in errors), errors)

    def test_every_bad_row_is_named_with_its_line_number(self):
        page = self.upload([
            self.row("44A962", "Letters In Id"),
            self.row("445963", "Good Person"),
            self.row("445964", "No Branch", branch="Nowhere"),
            self.row("445965", "Bad Date", joining="the first of March"),
            self.row("445966", "Bad Basis", basis="yearly"),
            self.row("445967", "Bad Email", email="not-an-email"),
            self.row("", "No Id At All"),
        ])
        preview = self.rows_of(page)
        self.assertEqual(preview["counts"]["bad"], 6)
        self.assertEqual([row["line"] for row in preview["bad"]], [2, 4, 5, 6, 7, 8])
        joined = " ".join(e for row in preview["bad"] for e in row["errors"])
        for phrase in ("is not digits", "No active branch called", "not a date",
                       "not a pay basis", "not an email address",
                       "Employee ID is missing"):
            self.assertIn(phrase, joined)
        self.assertNotContains(page, "Import 1 employee<")

    def test_an_id_repeated_in_the_file_points_at_the_other_row(self):
        page = self.upload([self.row("445962", "First"), self.row("445962", "Second")])
        errors = self.rows_of(page)["bad"][0]["errors"]
        self.assertIn("also on row 2", errors[0])

    def test_an_id_already_placed_is_refused(self):
        page = self.upload([self.row("E1", "Clash")])   # Rahim already holds E1
        errors = self.rows_of(page)["bad"][0]["errors"]
        # It is not digits either, so the digit rule speaks first; use a real one.
        self.assertTrue(errors)
        with use_company(self.company):
            EmployeeAssignment.objects.filter(employee_code="E1").update(
                employee_code="445962")
        page = self.upload([self.row("445962", "Clash")])
        self.assertIn("already belongs to someone still placed",
                      self.rows_of(page)["bad"][0]["errors"][0])

    def test_a_row_with_no_pay_needs_a_default(self):
        page = self.upload([self.row("445962", "Ajay Kumar", basis="", rate="")])
        errors = " ".join(self.rows_of(page)["bad"][0]["errors"])
        self.assertIn("no default chosen", errors)

    def test_the_defaults_fill_the_blank_pay_columns(self):
        page = self.upload(
            [self.row("445962", "Ajay Kumar", basis="", rate="")],
            default_pay_basis="daily", default_base_rate="900",
        )
        self.assertEqual(self.rows_of(page)["counts"]["bad"], 0)
        self.confirm()
        with use_company(self.company):
            pay = EmployeeCompensation.objects.get(
                employee=self.placement("445962").employee)
        self.assertEqual(pay.pay_basis, "daily")
        self.assertEqual(pay.base_rate, Decimal("900.00"))

    def test_a_default_rate_without_a_basis_is_refused_on_the_form(self):
        page = self.upload([self.row("445962", "A B")], default_base_rate="900")
        self.assertIn("default_pay_basis", page.context["form"].errors)


class ConfirmTests(ImportCase):
    def test_confirming_creates_everyone_with_placement_and_pay(self):
        before = self.employee_count()
        self.upload([
            self.row("445962", "Ajay Kumar", phone="01700000000",
                     email="ajay@example.com"),
            self.row("445963", "Dia Rahman", rate="31000.50"),
        ])
        page = self.confirm()
        self.assertEqual(self.employee_count(), before + 2)
        self.assertContains(page, "2 employees imported")

        placement = self.placement("445962")
        self.assertEqual(placement.employee.first_name, "Ajay")
        self.assertEqual(placement.employee.last_name, "Kumar")
        self.assertEqual(placement.employee.work_email, "ajay@example.com")
        self.assertEqual(placement.employee.phone, "01700000000")
        self.assertEqual(placement.employee.joining_date, datetime.date(2026, 3, 1))
        self.assertEqual(placement.branch_id, self.branch.pk)
        self.assertEqual(placement.department.name, "Software")
        self.assertEqual(placement.designation.name, "Developer")
        with use_company(self.company):
            pay = EmployeeCompensation.objects.get(employee=placement.employee)
        self.assertEqual(pay.base_rate, Decimal("25000.00"))
        self.assertEqual(pay.currency, self.company.currency)

    def test_a_one_word_name_is_kept_as_the_first_name(self):
        self.upload([self.row("445962", "Dia")])
        self.confirm()
        employee = self.placement("445962").employee
        self.assertEqual((employee.first_name, employee.last_name), ("Dia", ""))
        self.assertEqual(employee.full_name, "Dia")

    def test_a_three_word_name_splits_on_the_last_space(self):
        self.upload([self.row("445962", "Md Fazle Rabbi")])
        self.confirm()
        employee = self.placement("445962").employee
        self.assertEqual((employee.first_name, employee.last_name), ("Md Fazle", "Rabbi"))

    def test_one_bad_row_means_nothing_is_written(self):
        before = self.employee_count()
        page = self.upload([self.row("445962", "Good One"),
                            self.row("44A963", "Bad One")])
        self.assertNotContains(page, "Import 1 employee")
        # The button is not offered; a crafted POST is refused all the same.
        page = self.confirm()
        self.assertEqual(self.employee_count(), before)
        self.assertContains(page, "can no longer be imported")

    def test_confirming_twice_does_not_import_twice(self):
        self.upload([self.row("445962", "Ajay Kumar")])
        self.confirm()
        before = self.employee_count()
        page = self.confirm()
        self.assertEqual(self.employee_count(), before)
        self.assertContains(page, "no longer waiting")

    def test_a_confirm_with_nothing_waiting_is_sent_back(self):
        self.client.force_login(self.admin)
        page = self.client.post(self.confirm_url, follow=True)
        self.assertContains(page, "no longer waiting")

    def test_the_import_is_audited_on_one_line(self):
        self.upload([self.row("445962", "Ajay Kumar"), self.row("445963", "Dia Rahman")])
        self.confirm()
        entry = AuditLog.objects.filter(action="employees.imported").get()
        self.assertEqual(entry.actor_user_id, self.admin.pk)
        self.assertEqual(entry.after_data["count"], 2)
        self.assertEqual(entry.after_data["employee_codes"], ["445962", "445963"])

    def test_a_row_that_stops_being_importable_rolls_the_whole_import_back(self):
        """The department is deactivated between the preview and the confirm."""
        before = self.employee_count()
        self.upload([self.row("445962", "Ajay Kumar"), self.row("445963", "Dia Rahman")])
        with use_company(self.company):
            Department.objects.filter(pk=self.hq_department.pk).update(status="inactive")
        page = self.confirm()
        self.assertEqual(self.employee_count(), before)
        self.assertContains(page, "can no longer be imported")


class WhoMayImportTests(ImportCase):
    def test_a_branch_manager_imports_into_their_own_branch(self):
        before = self.employee_count()
        page = self.upload([self.row("445962", "Ajay Kumar")], user=self.manager)
        self.assertEqual(page.context["preview"]["counts"]["bad"], 0)
        self.confirm()
        self.assertEqual(self.employee_count(), before + 1)
        self.assertEqual(self.placement("445962").branch_id, self.branch.pk)

    def test_a_branch_manager_is_refused_another_branch_row_by_row(self):
        before = self.employee_count()
        page = self.upload([
            self.row("445962", "Mine", branch=self.branch.name),
            self.row("445963", "Not Mine", branch=self.unit.name),
        ], user=self.manager)
        preview = page.context["preview"]
        self.assertEqual(preview["counts"]["bad"], 1)
        self.assertIn("You cannot add people to", preview["bad"][0]["errors"][0])
        # All or nothing: their own good row is not written either.
        self.confirm()
        self.assertEqual(self.employee_count(), before)

    def test_an_employee_login_never_reaches_the_page(self):
        self.client.force_login(self.clerk_user)
        for url in (self.url, reverse("organization:employee_import_template")):
            with self.subTest(url=url):
                page = self.client.get(url)
                self.assertEqual(page.status_code, 302)
                self.assertIn("/me/", page["Location"])
        page = self.client.post(self.confirm_url)
        self.assertIn(page.status_code, (302, 403))
        self.assertEqual(self.employee_count(), 3)

    def test_the_services_refuse_a_crafted_call_too(self):
        """The page is one door; the service is checked on its own."""
        rows = [dict(zip([key for key, *_ in import_services.COLUMNS],
                         self.row("445962", "Ajay Kumar")), line=2)]
        with self.assertRaises(PermissionDenied):
            import_services.check(self.clerk_user, self.company.pk, rows)
        with self.assertRaises(PermissionDenied):
            import_services.commit(actor=self.clerk_user, company_id=self.company.pk,
                                   rows=rows)
        self.assertEqual(self.employee_count(), 3)

    def test_the_service_refuses_a_row_the_actor_may_not_write(self):
        """A session edited to name another branch is caught at commit."""
        keys = [key for key, *_ in import_services.COLUMNS]
        rows = [dict(zip(keys, self.row("445962", "Sneaky", branch=self.unit.name)),
                     line=2)]
        with self.assertRaises(ValidationError):
            import_services.commit(actor=self.manager, company_id=self.company.pk,
                                   rows=rows)
        self.assertEqual(self.employee_count(), 3)
