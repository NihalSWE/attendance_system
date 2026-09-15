"""Attendance → Daily list on the shared server-side table (plan step N9).

The month, branch, employee and status filters narrow the set; the table's
search, order and page then run in SQL on that set. Counts must be the real
counts of the whole filtered month, not of the rows on screen.
"""

import datetime
from decimal import Decimal

from django.urls import reverse

from accounts.models import CompanyMembership, User
from attendance import tests_live
from attendance.models import AttendanceRecord
from attendance.services import recalculate
from common.tenant import use_company
from django.test import TestCase
from employees.services import create_employee
from organization.catalogue import adopt_department, adopt_designation
from organization.models import Branch
from tenants.services import onboard_company

UTC = datetime.timezone.utc
COLUMNS = 10


class DailyListTests(TestCase):
    # The live-test company: an admin, a 09:00-18:00 shift and Rahim on the
    # default branch. Borrowed through the module so LiveTestCase is not
    # collected twice.
    setUp_company = tests_live.LiveTestCase.setUp
    punch = tests_live.LiveTestCase.punch

    def setUp(self):
        self.setUp_company()
        with use_company(self.company):
            self.north = Branch.objects.create(code="NTH", name="North Office")
            department = adopt_department(self.north, "OPS", "Operations")
            designation = adopt_designation(department, "CLK", "Clerk")
        self.karim = create_employee(
            company=self.company, first_name="Karim", employee_code="N1",
            branch=self.north, department=department, designation=designation,
            effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC),
            pay_basis="monthly", base_rate=Decimal("20000"),
        )["employee"]
        # August 2026, both employees: every working day is written (absent
        # where nobody scanned), which gives a month long enough to page.
        recalculate(self.company.pk, start=datetime.date(2026, 8, 1),
                    end=datetime.date(2026, 8, 31))
        self.client.force_login(self.admin)
        self.url = reverse("attendance:attendance_list")

    def records(self, **filters):
        with use_company(self.company):
            return AttendanceRecord.objects.filter(
                work_date__year=2026, work_date__month=8, **filters
            ).count()

    def draw(self, **params):
        response = self.client.get(self.url, {
            "table": "1", "draw": "3", "start": "0", "length": "10",
            "year": "2026", "month": "8", **params,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        data = response.json()
        self.assertEqual(data["draw"], 3)
        return data

    def test_counts_are_the_whole_month_and_rows_are_one_page(self):
        data = self.draw()
        month = self.records()
        self.assertGreater(month, 10)
        self.assertEqual((data["recordsTotal"], data["recordsFiltered"]), (month, month))
        self.assertEqual(len(data["data"]), 10)
        self.assertEqual(len(data["data"][0]) - 2, COLUMNS)

    def test_the_branch_filter_narrows_the_set_before_the_table(self):
        data = self.draw(branch=str(self.north.pk))
        north = self.records(branch=self.north)
        self.assertEqual(data["recordsTotal"], north)
        self.assertTrue(all("North Office" in row["2"] for row in data["data"]))
        self.assertNotIn("Rahim", str(data))

    def test_search_runs_in_the_database_across_every_page(self):
        data = self.draw(**{"search[value]": "Karim", "length": "10", "start": "20"})
        self.assertEqual(data["recordsFiltered"], self.records(employee=self.karim))
        self.assertEqual(data["recordsTotal"], self.records())
        self.assertTrue(all("Karim" in row["1"] for row in data["data"]))

    def test_the_employee_code_is_searchable(self):
        data = self.draw(**{"search[value]": "N1"})
        self.assertEqual(data["recordsFiltered"], self.records(employee=self.karim))

    def test_every_orderable_column_sorts_and_hostile_values_are_ignored(self):
        for column in range(COLUMNS):
            with self.subTest(column=column):
                self.draw(**{"order[0][column]": column, "order[0][dir]": "desc"})
        data = self.draw(**{"order[0][column]": 9999, "length": -1, "start": 10 ** 9,
                            "columns[0][data]": "employee__user__password"})
        self.assertEqual(data["data"], [])
        self.assertEqual(self.draw(**{"search[value]": "<script>"})["recordsFiltered"], 0)

    def test_dates_sort_newest_first_when_asked(self):
        rows = self.draw(**{"order[0][column]": 0, "order[0][dir]": "desc"})["data"]
        self.assertIn("31 Aug", rows[0]["0"])

    def test_another_company_is_never_counted(self):
        other = onboard_company(code="OTH", slug="oth", name="Other Ltd")
        outsider = User.objects.create_user(email="admin@oth.test", password="pw")
        CompanyMembership.all_objects.create(
            company=other, user=outsider, role=CompanyMembership.Role.COMPANY_ADMIN,
            status=CompanyMembership.Status.ACTIVE,
        )
        self.client.force_login(outsider)
        data = self.draw()
        self.assertEqual(data["recordsTotal"], 0)
        self.assertNotIn("Rahim", str(data))

    def test_the_html_page_has_a_counted_numbered_pager_that_keeps_the_filters(self):
        response = self.client.get(self.url, {
            "year": "2026", "month": "8", "branch": str(self.north.pk), "per_page": "10",
        })
        self.assertContains(response, "data-server-table")
        self.assertContains(response, 'aria-label="Result pages"')
        self.assertContains(response, f"branch={self.north.pk}")
        self.assertContains(response, "North Office")
        self.assertContains(response, f"{self.records(branch=self.north)} of {self.records()} days")

    def test_the_headers_stay_when_a_search_finds_nothing(self):
        response = self.client.get(self.url, {
            "year": "2026", "month": "8", "table_q": "NeverMatchesAnything",
        })
        self.assertContains(response, "data-server-table")
        self.assertContains(response, "Worked (min)")
        self.assertContains(response, "No days match")

    def test_names_are_escaped_in_the_json_cells(self):
        with use_company(self.company):
            self.karim.first_name = '<img src=x onerror="alert(1)">'
            self.karim.save(update_fields=["first_name"])
        cells = str(self.draw(branch=str(self.north.pk))["data"])
        self.assertIn("&lt;img", cells)
        self.assertNotIn("<img", cells)
