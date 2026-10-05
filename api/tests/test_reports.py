"""Phase 11: reports and the dashboard (docs/api/110-reports.md)."""

from api.tests.test_attendance import AttendanceApiTestCase
from reports.catalogue import REPORTS


class ReportTests(AttendanceApiTestCase):
    def test_the_list_follows_each_reports_rule(self):
        listed = self.api("GET", "/api/v1/reports")
        self.assertEqual(listed.status_code, 200, listed.content)
        self.assertEqual([r["slug"] for r in listed.json()], [r.slug for r in REPORTS])
        self.person("staff@example.test", role="employee")
        staff = self.logged_in("staff@example.test")
        self.assertEqual(self.api("GET", "/api/v1/reports", session=staff).json(), [])
        refused = self.api("GET", "/api/v1/reports/daily-attendance", session=staff)
        self.assertEqual(self.code_of(refused), "permission_denied")

    def test_a_day_a_month_and_a_range(self):
        day = self.api("GET", "/api/v1/reports/daily-attendance", query=f"on={self.late_day}")
        self.assertEqual(day.status_code, 200, day.content)
        body = day.json()
        self.assertEqual((body["first"], body["count"]), (self.late_day.isoformat(), 1))
        self.assertEqual(len(body["results"][0]), len(body["columns"]))
        self.assertIn("Rahim", " ".join(str(v) for v in body["results"][0]))
        late = self.api("GET", "/api/v1/reports/monthly-late",
                        query=f"year={self.late_day.year}&month={self.late_day.month}").json()
        self.assertEqual(late["count"], 1)
        absent = self.api("GET", "/api/v1/reports/custom-attendance",
                          query=f"from={self.open_day}&to={self.late_day}&view=daily"
                                f"&status=present").json()
        self.assertGreaterEqual(absent["count"], 1)
        too_long = self.api("GET", "/api/v1/reports/entry-logs",
                            query="from=2026-01-01&to=2026-06-30").json()
        self.assertTrue(too_long["warnings"])

    def test_filters_outside_reach_are_refused(self):
        refused = self.api("GET", "/api/v1/reports/daily-attendance",
                           query=f"branch_id={self.other_branch.pk}")
        self.assertEqual(self.code_of(refused), "permission_denied")
        self.assertEqual(self.api("GET", "/api/v1/reports/nothing").status_code, 404)

    def test_download(self):
        pdf = self.api("GET", "/api/v1/reports/daily-attendance/export",
                       query=f"on={self.late_day}&file_type=pdf")
        self.assertEqual((pdf.status_code, pdf["Content-Type"]), (200, "application/pdf"))
        xlsx = self.api("GET", "/api/v1/reports/monthly-attendance/export")
        self.assertIn("spreadsheet", xlsx["Content-Type"])
        wrong = self.api("GET", "/api/v1/reports/monthly-attendance/export",
                         query="file_type=doc")
        self.assertEqual(wrong.status_code, 422)

    def test_api_keys(self):
        reader = self.key("reports:read")
        self.assertEqual(self.as_key(reader, "GET", "/api/v1/reports").status_code, 200)
        other = self.key("attendance:read")
        self.assertEqual(self.code_of(self.as_key(other, "GET", "/api/v1/reports")),
                         "scope_missing")


class DashboardTests(AttendanceApiTestCase):
    def test_the_dashboard(self):
        shown = self.api("GET", "/api/v1/dashboard")
        self.assertEqual(shown.status_code, 200, shown.content)
        self.assertEqual((shown.json()["employees"], shown.json()["active"]), (1, 1))
        self.assertEqual(shown.json()["recent"][0]["name"], "Rahim")

    def test_hr_and_staff_do_not_see_it(self):
        self.person("hr@example.test", role="hr")
        hr = self.logged_in("hr@example.test")
        self.assertEqual(self.code_of(self.api("GET", "/api/v1/dashboard", session=hr)),
                         "permission_denied")
