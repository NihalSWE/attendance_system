"""Attendance downloads: the Daily list (Excel, PDF) and the calendar (PDF).

Head Office: Rahim (and Clerk); Chittagong: Karim. Rahim scans 09:00-18:00 and
Karim 09:00-17:00 on Monday 10 Aug 2026. A download holds what that viewer's
page holds - same scope, window, filters, table search and sort.
"""

from unittest import mock

from django.urls import reverse

from attendance.services import recalculate
from auditlog.models import AuditLog
from common import exports
from common.tests_exports import pdf_text, xlsx_table
from leaves.tests_branch_access import MONDAY, TwoBranchCase

AUGUST = {"month": "8", "year": "2026"}


class ExportCase(TwoBranchCase):
    def setUp(self):
        super().setUp()
        self.punch(MONDAY, 9)
        self.punch(MONDAY, 18)
        self.far_punch(MONDAY, 9)
        self.far_punch(MONDAY, 17)
        recalculate(self.company.pk, start=MONDAY, end=MONDAY)

    def download(self, fmt="xlsx", user=None, **params):
        self.client.force_login(user or self.admin)
        return self.client.get(reverse("attendance:attendance_list"),
                               {**AUGUST, **params, "format": fmt})

    def table(self, **kwargs):
        response = self.download("xlsx", **kwargs)
        self.assertEqual(response.status_code, 200, getattr(response, "url", ""))
        lines, headers, rows = xlsx_table(response.content)
        return lines, [dict(zip(headers, row)) for row in rows], response

    def day(self, **kwargs):
        return self.table(on=MONDAY.isoformat(), **kwargs)


class DailyListTests(ExportCase):
    def test_one_day_for_the_company(self):
        lines, rows, response = self.day()
        by_name = {row["Employee"]: row for row in rows}
        self.assertEqual(set(by_name), {"Rahim", "Clerk", "Karim"})
        rahim = by_name["Rahim"]
        self.assertEqual((rahim["In"], rahim["Out"], rahim["Status"]), ("09:00", "18:00", "Present"))
        self.assertEqual(rahim["Employee ID"], "E1")
        self.assertGreater(rahim["Worked (min)"], 0)
        self.assertIn("Period: 10 Aug 2026", lines[1])
        self.assertIn("3 rows", lines[2])
        self.assertIn('filename="attendance-liveltd-2026-08-10.xlsx"',
                      response["Content-Disposition"])

    def test_the_month_is_what_the_page_counts(self):
        _lines, rows, response = self.table()
        self.client.force_login(self.admin)
        page = self.client.get(reverse("attendance:attendance_list"), AUGUST)
        self.assertEqual(len(rows), page.context["month_total"])
        self.assertIn("attendance-liveltd-2026-08.xlsx", response["Content-Disposition"])

    def test_a_date_range_names_both_ends(self):
        _lines, _rows, response = self.table(date_from="2026-08-10", date_to="2026-08-12")
        self.assertIn("attendance-liveltd-2026-08-10-to-2026-08-12.xlsx",
                      response["Content-Disposition"])

    def test_the_branch_filter_and_its_name(self):
        lines, rows, response = self.day(branch=str(self.unit.pk))
        self.assertEqual([row["Employee"] for row in rows], ["Karim"])
        self.assertIn("Branch: Chittagong", lines[1])
        self.assertIn("attendance-chittagong-2026-08-10.xlsx", response["Content-Disposition"])

    def test_a_branch_manager_gets_their_branch_only(self):
        _lines, rows, response = self.day(user=self.manager)
        self.assertNotIn("Karim", {row["Employee"] for row in rows})
        self.assertIn(f"attendance-{exports.slug(self.branch.name)}-2026-08-10",
                      response["Content-Disposition"])
        # Asking for the other branch returns nothing, as the page would.
        _lines, rows, _response = self.day(user=self.manager, branch=str(self.unit.pk))
        self.assertEqual(rows, [])

    def test_the_status_filter(self):
        _lines, rows, _response = self.day(status="present")
        self.assertEqual({row["Employee"] for row in rows}, {"Rahim", "Karim"})

    def test_the_table_search_and_sort(self):
        lines, rows, _response = self.day(**{"order[0][column]": "1", "order[0][dir]": "desc"})
        self.assertEqual([row["Employee"] for row in rows], ["Rahim", "Karim", "Clerk"])
        self.assertIn("Sorted by: Employee descending", lines[1])
        _lines, rows, _response = self.day(**{"search[value]": "Karim"})
        self.assertEqual([row["Employee"] for row in rows], ["Karim"])

    def test_the_pdf(self):
        response = self.download("pdf", on=MONDAY.isoformat())
        self.assertEqual(response["Content-Type"], "application/pdf")
        text = pdf_text(response.content)
        for expected in ("Rahim", "Karim", "09:00", "18:00", "Present"):
            self.assertIn(expected, text)

    def test_over_the_ceiling_is_refused(self):
        with mock.patch.dict(exports.MAX_ROWS, {exports.PDF: 2}):
            response = self.download("pdf", on=MONDAY.isoformat())
        self.assertEqual(response.status_code, 302)
        page = self.client.get(response.url)
        self.assertContains(page, "at most 2")
        self.assertFalse(AuditLog.objects.filter(action="export.downloaded").exists())

    def test_each_download_is_recorded(self):
        self.day(status="present")
        entry = AuditLog.objects.get(action="export.downloaded")
        self.assertEqual(entry.after_data["page"], "attendance")
        self.assertEqual(entry.after_data["rows"], 2)
        self.assertEqual(entry.after_data["filters"]["status"], "present")
        self.assertEqual(entry.after_data["filters"]["period"], ["2026-08-10", "2026-08-10"])

    def test_the_page_offers_the_downloads(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("attendance:attendance_list"), AUGUST)
        self.assertContains(page, "Download Excel")
        self.assertContains(page, "Download PDF")


class CalendarTests(ExportCase):
    def calendar(self, user=None, **params):
        self.client.force_login(user or self.admin)
        return self.client.get(reverse("attendance:attendance_calendar"),
                               {**AUGUST, **params})

    def test_one_persons_month_as_a_pdf(self):
        response = self.calendar(employee=str(self.employee.pk), format="pdf")
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attendance-calendar-rahim-2026-08.pdf", response["Content-Disposition"])
        text = pdf_text(response.content)
        for expected in ("Rahim", "August 2026", "Present", "09:00", "Mon"):
            self.assertIn(expected, text)
        self.assertNotIn("Karim", text)
        entry = AuditLog.objects.get(action="export.downloaded")
        self.assertEqual(entry.after_data["page"], "attendance_calendar")
        self.assertEqual(entry.after_data["filters"]["month"], "2026-08")

    def test_someone_outside_the_viewers_branches_is_not_printed(self):
        """The page falls back to someone it may show; so does the PDF."""
        response = self.calendar(user=self.manager, employee=str(self.far.pk), format="pdf")
        self.assertNotIn("Karim", pdf_text(response.content))

    def test_there_is_no_spreadsheet_of_a_calendar(self):
        response = self.calendar(employee=str(self.employee.pk), format="xlsx")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response["Content-Type"])

    def test_the_page_offers_the_pdf_for_the_person_shown(self):
        page = self.calendar(employee=str(self.employee.pk))
        self.assertContains(page, f"employee={self.employee.pk}&amp;year=2026&amp;month=8&amp;format=pdf")
