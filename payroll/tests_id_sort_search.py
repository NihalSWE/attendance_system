"""Sort by Employee ID, full-name search and the employee picker (2026-11-04).

The Attendance summary and Salary pages have an ID column sorted as a number
(9, 10, 100 - numbers before IDs with letters); every table search matches
each word typed, so a full name finds the person; the summary has an
employee picker.
"""

import datetime
import re
from decimal import Decimal

from django.urls import reverse

from attendance.services import recalculate
from base_template.tests_employee_export import EmployeeExportCase
from common.tenant import use_company
from employees.models import Employee
from employees.services import create_employee
from payroll.services import generate_payroll
from payroll.tests_overtime import LATER, OvertimeBase

D = datetime.date
AUGUST = {"year": "2026", "month": "8"}
TABLE = {"table": "1", "draw": "2", "start": "0", "length": "25"}


class TwoPeople(OvertimeBase):
    """Rahim (ID E1, from the fixture) and Karim Uddin (ID 10)."""

    def setUp(self):
        super().setUp()
        with use_company(self.company):
            assignment = self.employee.assignments.first()
        self.karim = create_employee(
            company=self.company, first_name="Karim", last_name="Uddin", employee_code="10",
            branch=assignment.branch, department=assignment.department, designation=assignment.designation,
            effective_from=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
            pay_basis="monthly", base_rate=Decimal("20000"))["employee"]
        self.work(D(2026, 8, 10), (9, 0), (18, 0))
        recalculate(self.company.pk, start=D(2026, 8, 1), end=D(2026, 8, 31), now=LATER)
        self.client.force_login(self.admin)

    @staticmethod
    def first_cells(reply):
        return [re.sub(r"<[^>]+>", "", row["0"]).strip() for row in reply.json()["data"]]


class SummaryTests(TwoPeople):
    def test_id_column_sorts_as_a_number(self):
        url = reverse("payroll:attendance_summary")
        page = self.client.get(url, {**AUGUST, "show": "all"})
        self.assertContains(page, "<th>ID</th>", html=True)
        self.assertContains(page, 'data-orderable="0,1,2,3,4,5,6,7,8,9,10"')
        up = self.client.get(url, {**AUGUST, "show": "all", **TABLE, "order[0][column]": "0", "order[0][dir]": "asc"})
        self.assertEqual(self.first_cells(up), ["10", "E1"])
        down = self.client.get(url, {**AUGUST, "show": "all", **TABLE, "order[0][column]": "0",
                                     "order[0][dir]": "desc"})
        self.assertEqual(self.first_cells(down), ["E1", "10"])

    def test_full_name_search_and_the_employee_picker(self):
        url = reverse("payroll:attendance_summary")
        found = self.client.get(url, {**AUGUST, "show": "all", **TABLE, "search[value]": "Karim Uddin"})
        self.assertEqual(self.first_cells(found), ["10"])
        page = self.client.get(url, {**AUGUST, "show": "all"})
        self.assertContains(page, 'id="sum-employee"')
        self.assertContains(page, "10 · Karim Uddin")
        only = self.client.get(url, {**AUGUST, "show": "all", "employee": self.karim.pk})
        self.assertEqual(only.context["counts"]["all"], 1)
        self.assertContains(only, f'value="{self.karim.pk}" selected')
        rows = self.client.get(url, {**AUGUST, "show": "all", "employee": self.karim.pk, **TABLE})
        self.assertEqual(self.first_cells(rows), ["10"])


class SalaryPageTests(TwoPeople):
    def test_id_column_sorts_as_a_number(self):
        generate_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        url = reverse("payroll:payroll_home")
        page = self.client.get(url, AUGUST)
        self.assertContains(page, "<th>ID</th>", html=True)
        up = self.client.get(url, {**AUGUST, **TABLE, "order[0][column]": "0", "order[0][dir]": "asc"})
        self.assertEqual([cell.strip() for cell in self.first_cells(up)], ["10", "E1"])
        found = self.client.get(url, {**AUGUST, **TABLE, "search[value]": "karim uddin"})
        self.assertEqual([cell.strip() for cell in self.first_cells(found)], ["10"])


class EmployeeListTests(EmployeeExportCase):
    def setUp(self):
        super().setUp()
        Employee.all_objects.filter(first_name="Karim").update(last_name="Uddin")

    def test_full_name_search(self):
        for words in ("Karim Uddin", "uddin karim", "  Karim   Uddin "):
            _lines, _headers, rows = self.table(q=words)
            self.assertEqual(self.names(rows), ["Karim Uddin"], words)
            _lines, _headers, rows = self.table(**{"search[value]": words})
            self.assertEqual(self.names(rows), ["Karim Uddin"], words)
        _lines, _headers, rows = self.table(q="Karim Nobody")
        self.assertEqual(rows, [])
