"""Reports (2026-09-26): every report renders and downloads, reads what the
system already records, and follows the rules of the page it reads from.

Monday 10 August 2026 (shift 9:00-18:00, 10 grace minutes): Rahim (Head
Office, ID 100) in at 9:40 - 30 minutes late - out at 18:00; Karim
(Chittagong, ID 9) in at 9:05, out at 17:00 - on time, an hour short; Clerk
(Head Office, ID 20) no scans - absent.
"""

import datetime

from django.test import override_settings
from django.urls import reverse

from attendance.services import recalculate
from auditlog.models import AuditLog
from common.tenant import use_company
from common.tests_exports import pdf_text, xlsx_table
from employees.models import EmployeeAssignment
from leaves.tests_branch_access import MONDAY, TwoBranchCase
from reports.catalogue import BY_SLUG, REPORTS
from reports.filters import _week_start


class ReportCase(TwoBranchCase):
    def setUp(self):
        super().setUp()
        self.punch(MONDAY, 9, 40)
        self.punch(MONDAY, 18)
        self.far_punch(MONDAY, 9, 5)
        self.far_punch(MONDAY, 17)
        recalculate(self.company.pk, start=MONDAY, end=MONDAY)
        with use_company(self.company):
            for employee, code in ((self.employee, "100"), (self.far, "9"), (self.clerk, "20")):
                EmployeeAssignment.objects.filter(employee=employee).update(employee_code=code)

    def open(self, slug, user=None, **params):
        self.client.force_login(user or self.admin)
        return self.client.get(reverse(BY_SLUG[slug].url_name), params)

    def rows(self, slug, **params):
        page = self.open(slug, **params)
        self.assertEqual(page.status_code, 200)
        return page.context["result"].rows

    def names(self, rows, at=1):
        return [row[at] for row in rows]

    def summary(self, slug, **params):
        return dict(self.open(slug, **params).context["result"].summary)


DAY = {"on": MONDAY.isoformat()}
AUGUST = {"month": "8", "year": "2026"}
RANGE = {"date_from": "2026-08-10", "date_to": "2026-08-10"}


class EveryReportTests(ReportCase):
    def test_every_report_opens_and_downloads(self):
        for report in REPORTS:
            with self.subTest(report=report.slug):
                page = self.open(report.slug, **{**DAY, **AUGUST, **RANGE,
                                                 "week_start": MONDAY.isoformat()})
                self.assertEqual(page.status_code, 200)
                self.assertContains(page, report.title)
                for fmt, kind in (("xlsx", "spreadsheetml"), ("pdf", "application/pdf")):
                    download = self.open(report.slug, format=fmt, **RANGE, **AUGUST, **DAY)
                    self.assertEqual(download.status_code, 200, fmt)
                    self.assertIn(kind, download["Content-Type"])
                    self.assertIn(report.slug, download["Content-Disposition"])

    def test_the_index_lists_them_by_group(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("reports:index"))
        for report in REPORTS:
            self.assertContains(page, report.title)
        self.assertContains(page, "Attendance Report")

    def test_the_menu_has_the_reports_under_their_headings(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("reports:index"))
        menu = next(m for m in page.context["company_menus"] if m["key"] == "reports")
        labels = [(link["group"], link["label"]) for link in menu["links"]]
        self.assertEqual(labels[:5], [("", "All reports"),
                                      ("Attendance Report", "Daily"),
                                      ("Attendance Report", "Weekly"),
                                      ("Attendance Report", "Monthly"),
                                      ("Attendance Report", "Customize")])
        self.assertIn(("", "Entry Logs Report"), labels)
        self.assertContains(page, 'class="sidebar__subhead"', count=3)

    def test_a_download_is_audited(self):
        self.open("daily-attendance", format="xlsx", **DAY)
        entry = AuditLog.objects.get(action="export.downloaded")
        self.assertEqual(entry.after_data["page"], "report:daily-attendance")


class AttendanceReportTests(ReportCase):
    def test_daily(self):
        rows = self.rows("daily-attendance", **DAY)
        by_name = {row[1]: row for row in rows}
        self.assertEqual(by_name["Rahim"][:2], ["100", "Rahim"])
        self.assertEqual(by_name["Rahim"][5:7], ["09:40", "18:00"])
        self.assertEqual(by_name["Rahim"][8], 30)                   # late minutes
        self.assertEqual(by_name["Clerk"][4], "Absent")
        summary = self.summary("daily-attendance", **DAY)
        self.assertEqual((summary["Late"], summary["Absent"]), (1, 1))

    def test_weekly_is_a_grid_from_saturday(self):
        page = self.open("weekly-attendance", week_start=MONDAY.isoformat())
        result = page.context["result"]
        self.assertEqual(page.context["f"].first, _week_start(MONDAY))
        self.assertEqual(page.context["f"].first.weekday(), 5)     # Saturday
        self.assertEqual(len(result.columns), 2 + 7 + 7)
        codes = {row[1]: row[2:9] for row in result.rows}
        monday = (MONDAY - page.context["f"].first).days
        self.assertEqual(codes["Rahim"][monday], "LT")
        self.assertEqual(codes["Karim"][monday], "P")
        self.assertEqual(codes["Clerk"][monday], "A")
        self.assertIn("LT present, came late", result.legend)

    def test_monthly_has_every_day_of_the_month(self):
        result = self.open("monthly-attendance", **AUGUST).context["result"]
        self.assertEqual(len(result.columns), 2 + 31 + 7)
        self.assertTrue(result.grid)

    def test_the_monthly_pdf_is_compact_and_says_the_legend(self):
        download = self.open("monthly-attendance", format="pdf", **AUGUST)
        text = pdf_text(download.content)
        self.assertIn("Monthly Attendance Report", text)
        self.assertIn("weekly off", text)

    def test_custom_totals_and_every_day_and_one_status(self):
        rows = self.rows("custom-attendance", **RANGE)
        self.assertEqual(sorted(self.names(rows)), ["Clerk", "Karim", "Rahim"])
        detail = self.rows("custom-attendance", view="detail", **RANGE)
        self.assertEqual(detail[0][0], "Mon 10 Aug")
        absent = self.rows("custom-attendance", view="detail", status="absent", **RANGE)
        self.assertEqual(self.names(absent, at=2), ["Clerk"])

    def test_a_range_too_long_is_shortened_and_said(self):
        page = self.open("custom-attendance", date_from="2026-01-01", date_to="2026-12-31")
        self.assertEqual((page.context["f"].last - page.context["f"].first).days + 1, 92)
        self.assertContains(page, "at most 92 days")


class AbsentAndLateTests(ReportCase):
    def test_daily_absent(self):
        self.assertEqual(self.names(self.rows("daily-absent", **DAY)), ["Clerk"])

    def test_monthly_absent_counts_and_dates(self):
        rows = self.rows("monthly-absent", **AUGUST)
        clerk = next(row for row in rows if row[1] == "Clerk")
        self.assertGreaterEqual(clerk[4], 1)
        self.assertIn("10", clerk[5].split(", "))

    def test_daily_late(self):
        rows = self.rows("daily-late", **DAY)
        self.assertEqual([(row[1], row[5], row[6]) for row in rows], [("Rahim", "09:40", 30)])

    def test_monthly_late(self):
        rows = self.rows("monthly-late", **AUGUST)
        self.assertEqual([(row[1], row[4], row[5]) for row in rows], [("Rahim", 1, 30)])
        self.assertEqual(self.summary("monthly-late", **AUGUST)["Minutes late"], 30)


class HoursTests(ReportCase):
    def test_working_hours_per_person(self):
        rows = {row[1]: row for row in self.rows("working-hours", **RANGE)}
        self.assertEqual(set(rows), {"Rahim", "Karim"})              # Clerk worked no day
        self.assertEqual(rows["Rahim"][4], 1)                       # days worked

    def test_short_hours_lists_the_short_day(self):
        rows = self.rows("short-hours", **RANGE)
        self.assertIn("Karim", self.names(rows, at=2))
        karim = next(row for row in rows if row[2] == "Karim")
        self.assertEqual((karim[5], karim[6]), ("09:05", "17:00"))

    def test_overtime_after_the_shift_as_the_overtime_page_reads_it(self):
        tuesday = MONDAY + datetime.timedelta(days=1)
        self.punch(tuesday, 9)
        self.punch(tuesday, 20)           # two hours after the 18:00 end, scanned out
        recalculate(self.company.pk, start=tuesday, end=tuesday)
        on = {"date_from": tuesday.isoformat(), "date_to": tuesday.isoformat()}
        rows = self.rows("overtime", **on)
        self.assertEqual(self.names(rows, at=2), ["Rahim"])
        self.assertEqual(rows[0][7:], ["2:00", "2:00", "2:00", "Approved automatically"])
        self.assertEqual(self.summary("overtime", **on)["Waiting"], 0)


class LeaveReportTests(ReportCase):
    def test_leave_in_the_period(self):
        self.record_leave(self.clerk)
        rows = self.rows("leave", **RANGE)
        self.assertEqual([(row[1], row[3], row[6]) for row in rows], [("Clerk", "Casual", "1")])
        self.assertEqual(self.summary("leave", **RANGE)["Casual"], "1 day")

    def test_leave_by_type_and_status(self):
        self.record_leave(self.clerk)
        self.assertEqual(self.rows("leave", leave_status="pending", **RANGE), [])
        self.assertEqual(len(self.rows("leave", leave_status="all", **RANGE)), 1)
        self.assertEqual(len(self.rows("leave", leave_type=str(self.leave_type.pk), **RANGE)), 1)


class EntryLogTests(ReportCase):
    def test_every_scan_in_time_order(self):
        rows = self.rows("entry-logs", **RANGE)
        self.assertEqual([row[1][:5] for row in rows], ["09:05", "09:40", "17:00", "18:00"])
        self.assertEqual(self.summary("entry-logs", **RANGE)["Scans"], 4)

    def test_at_most_a_month(self):
        page = self.open("entry-logs", date_from="2026-08-01", date_to="2026-10-31")
        self.assertContains(page, "at most 31 days")


class AccessTests(ReportCase):
    def test_a_branch_manager_sees_their_branch_only(self):
        for slug, at in (("daily-attendance", 1), ("entry-logs", 3)):
            with self.subTest(slug=slug):
                page = self.open(slug, user=self.manager, **DAY, **RANGE)
                self.assertEqual(page.status_code, 200)
                self.assertNotIn("Karim", self.names(page.context["result"].rows, at=at))

    def test_a_branch_outside_their_reach_is_ignored_not_trusted(self):
        page = self.open("daily-attendance", user=self.manager, branch=str(self.unit.pk), **DAY)
        self.assertEqual(page.context["f"].branch, "")
        self.assertNotIn("Karim", self.names(page.context["result"].rows))

    def test_an_employee_login_is_kept_out(self):
        page = self.open("daily-attendance", user=self.clerk_user, **DAY)
        self.assertNotEqual(page.status_code, 200)

    def test_overtime_follows_the_overtime_page(self):
        payroll = self.member("pm@rep.test", "payroll_manager")
        self.assertEqual(self.open("overtime", user=payroll, **RANGE).status_code, 403)
        self.assertEqual(self.open("overtime", user=self.hr, **RANGE).status_code, 200)
        # The attendance reports are open to them, as the Daily list is.
        self.assertEqual(self.open("daily-attendance", user=payroll, **DAY).status_code, 200)

    def test_the_index_leaves_out_what_they_cannot_open(self):
        payroll = self.member("pm2@rep.test", "payroll_manager")
        self.client.force_login(payroll)
        page = self.client.get(reverse("reports:index"))
        self.assertNotContains(page, "Overtime Report")
        self.assertContains(page, "Daily Attendance Report")

    def test_filters_narrow_it(self):
        rows = self.rows("daily-attendance", employee=str(self.far.pk), **DAY)
        self.assertEqual(self.names(rows), ["Karim"])
        rows = self.rows("daily-attendance", branch=str(self.unit.pk), **DAY)
        self.assertEqual(self.names(rows), ["Karim"])


class DataTableTests(ReportCase):
    """The report table is the project's server-side DataTable: entries per
    page, search across every row, any column sorted, numbered pages."""

    def draw(self, slug, **params):
        self.client.force_login(self.admin)
        response = self.client.get(reverse(BY_SLUG[slug].url_name), {
            **params, "table": "1", "draw": "3", "start": "0", "length": "10"})
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_the_page_carries_the_server_table(self):
        page = self.open("daily-attendance", **DAY)
        self.assertContains(page, "data-server-table")
        self.assertEqual(page.context["server_table"]["orderable"],
                         ",".join(str(i) for i in range(11)))

    def test_a_draw_answers_as_datatables_asks(self):
        data = self.draw("daily-attendance", **DAY)
        self.assertEqual((data["draw"], data["recordsTotal"], data["recordsFiltered"]), (3, 3, 3))
        self.assertEqual(len(data["data"]), 3)

    def test_search_runs_across_every_row(self):
        data = self.draw("daily-attendance", **DAY, **{"search[value]": "karim"})
        self.assertEqual((data["recordsTotal"], data["recordsFiltered"]), (3, 1))
        self.assertIn("Karim", data["data"][0]["1"])

    def test_ids_and_hours_sort_as_numbers(self):
        by_id = self.draw("daily-attendance", **DAY, **{"order[0][column]": "0",
                                                         "order[0][dir]": "asc"})
        self.assertEqual([row["0"].strip() for row in by_id["data"]], ["9", "20", "100"])
        by_worked = self.draw("daily-attendance", **DAY, **{"order[0][column]": "7",
                                                             "order[0][dir]": "desc"})
        self.assertIn("Rahim", by_worked["data"][0]["1"])     # 8:20 before 7:55

    def test_dates_sort_as_dates(self):
        tuesday = MONDAY + datetime.timedelta(days=1)
        self.punch(tuesday, 9)
        self.punch(tuesday, 18)
        recalculate(self.company.pk, start=tuesday, end=tuesday)
        rows = self.draw("custom-attendance", view="detail", date_from="2026-08-10",
                         date_to="2026-08-11", **{"order[0][column]": "0",
                                                  "order[0][dir]": "desc"})["data"]
        self.assertIn("Tue 11 Aug", rows[0]["0"])            # not "Mon" first, as text would

    def test_a_download_follows_the_table_search_and_sort(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse(BY_SLUG["daily-attendance"].url_name), {
            **DAY, "format": "xlsx", "search[value]": "office",
            "order[0][column]": "1", "order[0][dir]": "desc"})
        lines, _headers, rows = xlsx_table(response.content)
        # Head Office's two people (Karim is in Chittagong), names Z to A.
        self.assertEqual([row[1] for row in rows], ["Rahim", "Clerk"])
        self.assertIn('Table search: "office"', " ".join(lines))
        self.assertIn("Sorted by: Name descending", " ".join(lines))
