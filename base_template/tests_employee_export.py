"""Employees list downloads (Excel, PDF) - the page's rules, in a file.

The two that matter most: the file never shows pay the page would not (the
Salary columns exist only for a viewer who may see pay somewhere, and a row's
rate is withheld where the screen withholds it), and the file is what is on
screen (scope, search, filters, table search and sort) - not the whole table.
"""

from unittest import mock

from django.urls import reverse
from django.utils import timezone

from auditlog.models import AuditLog
from common import exports
from common.tenant import use_company
from common.tests_exports import pdf_text, xlsx_table
from leaves.tests_branch_access import TwoBranchCase


class EmployeeExportCase(TwoBranchCase):
    def download(self, fmt="xlsx", user=None, **params):
        self.client.force_login(user or self.admin)
        response = self.client.get(reverse("employee_list"), {**params, "format": fmt})
        return response

    def table(self, **kwargs):
        response = self.download("xlsx", **kwargs)
        self.assertEqual(response.status_code, 200, getattr(response, "url", ""))
        lines, headers, rows = xlsx_table(response.content)
        return lines, headers, [dict(zip(headers, row)) for row in rows]

    def names(self, rows):
        return [row["Name"] for row in rows]


class WhatIsInTheFileTests(EmployeeExportCase):
    def test_the_company_gets_everyone_with_department_designation_and_salary(self):
        lines, headers, rows = self.table()
        for column in ("Employee ID", "Name", "Branch", "Department", "Designation",
                       "Salary", "Pay basis"):
            self.assertIn(column, headers)
        self.assertEqual(sorted(self.names(rows)), ["Clerk", "Karim", "Rahim"])
        rahim = next(row for row in rows if row["Name"] == "Rahim")
        self.assertEqual(rahim["Salary"], 30000)          # a number, so it sums
        self.assertEqual(rahim["Department"], "Software")

    def test_the_file_says_what_it_is(self):
        lines, _headers, _rows = self.table()
        self.assertIn("Employees", lines[0])
        self.assertIn("Scope: All branches", lines[1])
        self.assertIn("3 employees", lines[2])

    def test_the_pdf_holds_the_same_people(self):
        response = self.download("pdf")
        self.assertEqual(response["Content-Type"], "application/pdf")
        text = pdf_text(response.content)
        for name in ("Rahim", "Karim", "Clerk", "Salary"):
            self.assertIn(name, text)

    def test_the_filename_carries_the_scope_and_the_date(self):
        today = timezone.localdate().isoformat()
        response = self.download("xlsx")
        self.assertIn(f'filename="employees-liveltd-{today}.xlsx"',
                      response["Content-Disposition"])
        response = self.download("pdf", user=self.manager)
        self.assertIn(f"employees-{exports.slug(self.branch.name)}-{today}.pdf",
                      response["Content-Disposition"])


class PayTests(EmployeeExportCase):
    def test_a_department_head_gets_no_salary_columns(self):
        with use_company(self.company):
            self.hq_department.head = self.clerk
            self.hq_department.save(update_fields=["head"])
        _lines, headers, rows = self.table(user=self.clerk_user)
        self.assertNotIn("Salary", headers)
        self.assertNotIn("Pay basis", headers)
        self.assertEqual(sorted(self.names(rows)), ["Clerk", "Rahim"])
        text = pdf_text(self.download("pdf", user=self.clerk_user).content)
        self.assertNotIn("Salary", text)
        self.assertNotIn("30,000", text)

    def test_a_viewer_granted_employees_view_only_gets_no_salary_columns(self):
        self.grant("employees.view", self.branch)
        _lines, headers, _rows = self.table(user=self.clerk_user)
        self.assertNotIn("Salary", headers)

    def test_a_rate_is_withheld_per_row_as_on_screen(self):
        """Pay seen in Chittagong only: Karim's rate is in, Rahim's is not."""
        self.grant("employees.view", self.branch, self.unit)
        self.grant("salary.view", self.unit)
        _lines, headers, rows = self.table(user=self.clerk_user)
        self.assertIn("Salary", headers)
        by_name = {row["Name"]: row for row in rows}
        self.assertEqual(by_name["Karim"]["Salary"], 30000)
        self.assertIsNone(by_name["Rahim"]["Salary"])
        self.assertIn(by_name["Rahim"]["Pay basis"], (None, ""))

    def test_a_branch_manager_gets_their_branch_with_its_pay(self):
        _lines, headers, rows = self.table(user=self.manager)
        self.assertEqual(sorted(self.names(rows)), ["Clerk", "Rahim"])
        self.assertIn("Salary", headers)


class SameAsTheScreenTests(EmployeeExportCase):
    def test_the_page_search_and_status_filter(self):
        _lines, _headers, rows = self.table(q="Karim")
        self.assertEqual(self.names(rows), ["Karim"])
        _lines, _headers, rows = self.table(status="resigned")
        self.assertEqual(rows, [])

    def test_the_setup_filter(self):
        _lines, _headers, rows = self.table(setup="complete")
        self.assertEqual(len(rows), 3)
        lines, _headers, rows = self.table(setup="department")
        self.assertEqual(rows, [])
        self.assertIn("Setup: Needs department", lines[1])

    def test_the_table_search_box_and_sort(self):
        lines, _headers, rows = self.table(**{"order[0][column]": "2", "order[0][dir]": "desc"})
        self.assertEqual(self.names(rows), ["Rahim", "Karim", "Clerk"])
        self.assertIn("Sorted by: Name descending", lines[1])
        lines, _headers, rows = self.table(**{"search[value]": "Rah"})
        self.assertEqual(self.names(rows), ["Rahim"])
        self.assertIn('Table search: "Rah"', lines[1])

    def test_the_no_script_table_search(self):
        _lines, _headers, rows = self.table(table_q="Karim")
        self.assertEqual(self.names(rows), ["Karim"])

    def test_the_page_offers_the_downloads_with_its_filters(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("employee_list"), {"q": "Karim", "setup": "complete"})
        self.assertContains(page, "Download Excel")
        self.assertContains(page, "Download PDF")
        self.assertContains(page, "q=Karim&amp;setup=complete&amp;format=xlsx")


class CeilingAndAuditTests(EmployeeExportCase):
    def test_over_the_ceiling_is_refused_and_says_why(self):
        with mock.patch.dict(exports.MAX_ROWS, {exports.XLSX: 2}):
            response = self.download("xlsx")
        self.assertEqual(response.status_code, 302)
        page = self.client.get(response.url)
        self.assertContains(page, "at most 2")
        self.assertContains(page, "Narrow the filter")
        self.assertFalse(AuditLog.objects.filter(action="export.downloaded").exists())

    def test_each_download_is_recorded(self):
        self.download("xlsx", q="Karim")
        entry = AuditLog.objects.get(action="export.downloaded")
        self.assertEqual(entry.actor_user_id, self.admin.pk)
        self.assertEqual(entry.after_data["page"], "employees")
        self.assertEqual(entry.after_data["format"], "xlsx")
        self.assertEqual(entry.after_data["rows"], 1)
        self.assertEqual(entry.after_data["filters"]["search"], "Karim")

    def test_an_employee_login_cannot_download_what_it_cannot_open(self):
        response = self.download("xlsx", user=self.clerk_user)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/me/", response.url)
        self.assertFalse(AuditLog.objects.filter(action="export.downloaded").exists())


class BanglaNameTests(EmployeeExportCase):
    """A real Bangla name through the real download (Ajay, 2026-09-21)."""

    def setUp(self):
        super().setUp()
        from common.tests_exports import RAHIM_AHMED

        self.bangla = RAHIM_AHMED
        first, last = RAHIM_AHMED.split(" ")
        with use_company(self.company):
            self.employee.first_name, self.employee.last_name = first, last
            self.employee.save(update_fields=["first_name", "last_name"])

    def test_excel_keeps_the_name_exactly(self):
        _lines, _headers, rows = self.table()
        self.assertIn(self.bangla, self.names(rows))

    def test_the_pdf_draws_it_in_the_bundled_font_only(self):
        from common.tests_exports import glyph_ids

        response = self.download("pdf")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"HindSiliguri", response.content)
        self.assertNotIn(b"Helvetica", response.content)
        self.assertNotIn(0, glyph_ids(exports.FONT, self.bangla))
