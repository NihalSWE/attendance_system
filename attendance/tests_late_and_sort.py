"""Late entries, and sorting the Daily list and Employees list (Nihal, 2026-09-24).

Late entries is the Daily list with one more condition: only the days someone
came in late. Both lists sort by name A-Z / Z-A and by Employee ID as a number
(9, 20, 100 - not the text order 100, 20, 9), from the headers or the
"Sort by" box.
"""

import re

from django.urls import reverse

from attendance.services import recalculate
from base_template.navigation import COMPANY_MENUS
from common.tenant import use_company
from common.tests_exports import xlsx_table
from employees.models import EmployeeAssignment
from leaves.tests_branch_access import MONDAY, TwoBranchCase

AUGUST = {"month": 8, "year": 2026}


def text(cell):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", cell)).strip()


class LateCase(TwoBranchCase):
    """Monday: Rahim scans in at 9:40 (30 minutes late after the 10-minute
    grace), Karim at 9:05 (inside the grace, so on time). Employee IDs are
    numbers whose text order differs from their number order."""

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

    def draw(self, url, column, direction, **params):
        """The rows a table draw returns, as the page's DataTables asks."""
        self.client.force_login(params.pop("user", self.admin))
        response = self.client.get(url, {
            **AUGUST, **params, "table": "1", "draw": "1", "start": "0", "length": "25",
            "order[0][column]": str(column), "order[0][dir]": direction,
        })
        self.assertEqual(response.status_code, 200)
        return response.json()["data"]


class LateEntriesTests(LateCase):
    url = reverse("attendance:attendance_late")

    def test_only_the_late_days(self):
        self.client.force_login(self.admin)
        page = self.client.get(self.url, AUGUST)
        records = list(page.context["page"].object_list)
        self.assertEqual([(r.employee.first_name, r.late_minutes) for r in records],
                         [("Rahim", 30)])
        self.assertContains(page, "Late entries")
        self.assertContains(page, "1 late day")

    def test_the_daily_list_still_shows_every_day(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("attendance:attendance_list"), AUGUST)
        names = {r.employee.first_name for r in page.context["page"].object_list}
        self.assertTrue({"Rahim", "Karim"} <= names)
        self.assertNotContains(page, "Late entries only")

    def test_a_day_inside_the_grace_is_not_late(self):
        rows = self.draw(self.url, 1, "asc")
        self.assertNotIn("Karim", " ".join(text(row["1"]) for row in rows))

    def test_filters_and_links_stay_on_the_late_page(self):
        self.client.force_login(self.admin)
        page = self.client.get(self.url, {**AUGUST, "branch": str(self.branch.pk)})
        self.assertContains(page, f'{self.url}?month=8&year=2026')          # Clear
        self.assertContains(page, f'{self.url}?')                            # downloads
        self.assertNotContains(page, reverse("attendance:attendance_list") + "?month=8&amp;year=2026&amp;format")

    def test_the_download_holds_only_late_days_and_says_so(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.url, {**AUGUST, "format": "xlsx"})
        self.assertIn("late-entries", response["Content-Disposition"])
        lines, _headers, rows = xlsx_table(response.content)
        self.assertIn("Late entries", lines[0])                   # the title
        self.assertIn("Late entries only", " ".join(lines))
        self.assertEqual([row[2] for row in rows], ["Rahim"])

    def test_a_branch_manager_sees_late_days_in_their_branch_only(self):
        # Karim (Chittagong) is late too now; Manny runs Head Office.
        self.far_punch(MONDAY, 9, 50)
        recalculate(self.company.pk, start=MONDAY, end=MONDAY)
        self.client.force_login(self.manager)
        page = self.client.get(self.url, AUGUST)
        self.assertEqual(page.status_code, 200)
        self.assertEqual({r.employee.first_name for r in page.context["page"].object_list},
                         {"Rahim"})

    def test_an_employee_login_is_kept_out(self):
        self.client.force_login(self.clerk_user)
        self.assertNotEqual(self.client.get(self.url, AUGUST).status_code, 200)

    def test_it_is_in_the_attendance_menu_after_the_daily_list(self):
        attendance = next(entries for key, _label, entries in COMPANY_MENUS if key == "attendance")
        views = [entry["view"] for entry in attendance]
        self.assertEqual(views[views.index("attendance:attendance_list") + 1],
                         "attendance:attendance_late")

    def test_the_most_late_can_be_put_first(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(self.url, AUGUST), "Sort: Most minutes late first")


class DailyListSortTests(LateCase):
    url = reverse("attendance:attendance_list")

    def codes(self, rows):
        return [text(row["10"]) for row in rows]

    def test_employee_id_sorts_as_a_number(self):
        # Clerk (20) has the day too, marked absent. As text it would be 100, 20, 9.
        self.assertEqual(self.codes(self.draw(self.url, 10, "asc")), ["9", "20", "100"])
        self.assertEqual(self.codes(self.draw(self.url, 10, "desc")), ["100", "20", "9"])

    def test_name_sorts_both_ways(self):
        names = lambda rows: [text(row["1"]).split()[0] for row in rows]  # noqa: E731
        self.assertEqual(names(self.draw(self.url, 1, "asc")), ["Clerk", "Karim", "Rahim"])
        self.assertEqual(names(self.draw(self.url, 1, "desc")), ["Rahim", "Karim", "Clerk"])

    def test_the_page_has_the_sort_box_and_the_hidden_id_column(self):
        self.client.force_login(self.admin)
        page = self.client.get(self.url, AUGUST)
        self.assertContains(page, 'data-table-sort=""')
        self.assertContains(page, "Sort: Employee ID, lowest first")
        self.assertContains(page, "<th data-hidden hidden>")
        # Column 10 may be sorted; the table says so to the script.
        self.assertIn("10", page.context["server_table"]["orderable"].split(","))

    def test_a_download_sorted_by_employee_id_follows_it(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.url, {**AUGUST, "format": "xlsx",
                                              "order[0][column]": "10", "order[0][dir]": "desc"})
        lines, _headers, rows = xlsx_table(response.content)
        self.assertEqual([row[1] for row in rows], ["100", "20", "9"])
        self.assertIn("Sorted by: Employee ID descending", " ".join(lines))


class EmployeeListSortTests(LateCase):
    url = reverse("employee_list")

    def test_employee_id_sorts_as_a_number(self):
        codes = lambda rows: [text(row["1"]) for row in rows]  # noqa: E731
        self.assertEqual(codes(self.draw(self.url, 1, "asc"))[:3], ["9", "20", "100"])
        self.assertEqual(codes(self.draw(self.url, 1, "desc"))[-3:], ["100", "20", "9"])

    def test_name_sorts_both_ways(self):
        names = lambda rows: [text(row["2"]) for row in rows]  # noqa: E731
        ascending = names(self.draw(self.url, 2, "asc"))
        self.assertEqual(ascending, sorted(ascending, key=str.casefold))
        self.assertEqual(names(self.draw(self.url, 2, "desc")), ascending[::-1])

    def test_an_id_with_letters_sorts_after_the_numbers(self):
        with use_company(self.company):
            EmployeeAssignment.objects.filter(employee=self.clerk).update(employee_code="E2")
        codes = [text(row["1"]) for row in self.draw(self.url, 1, "asc")]
        self.assertEqual(codes[:2], ["9", "100"])
        self.assertIn("E2", codes[2:])

    def test_the_page_has_the_sort_box(self):
        self.client.force_login(self.admin)
        page = self.client.get(self.url)
        self.assertContains(page, "Sort: Name A–Z")
        self.assertContains(page, "Sort: Employee ID, highest first")
