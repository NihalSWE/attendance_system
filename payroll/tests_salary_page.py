"""Salary by month: by department, the filters and the download (2026-10-31)."""

import datetime
from decimal import Decimal

from django.urls import reverse

from auditlog.models import AuditLog
from common.tenant import use_company
from common.tests_exports import pdf_text
from employees.services import create_employee
from organization.catalogue import adopt_department, adopt_designation
from payroll.services import generate_payroll
from payroll.tests_overtime import OvertimeBase

AUGUST = {"year": "2026", "month": "8"}


class SalaryPageTests(OvertimeBase):
    def setUp(self):
        super().setUp()
        with use_company(self.company):
            self.sewing = adopt_department(self.branch, "SEW", "Sewing")
            operator = adopt_designation(self.sewing, "OP", "Operator")
        create_employee(company=self.company, first_name="Karim", employee_code="E2", branch=self.branch,
                        department=self.sewing, designation=operator,
                        effective_from=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
                        pay_basis="monthly", base_rate=Decimal("20000"))
        generate_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        self.client.force_login(self.admin)
        self.page = reverse("payroll:payroll_home")

    def test_by_department_and_the_department_filter(self):
        page = self.client.get(self.page, {**AUGUST, "view": "departments"})
        self.assertEqual([group["name"] for group in page.context["by_department"]], ["Sewing", "Software"])
        self.assertEqual([group["people"] for group in page.context["by_department"]], [1, 1])
        self.assertContains(page, "By department")
        self.assertContains(page, "Karim")
        both = page.context["totals"]["gross"]
        page = self.client.get(self.page, {**AUGUST, "department": self.sewing.pk})
        self.assertEqual(page.context["totals"]["employees"], 1)
        self.assertLess(page.context["totals"]["gross"], both)
        self.assertNotContains(page, "Rahim")
        # Several departments at once (2026-10-31).
        software = page.context["departments"][1] if page.context["departments"][0].pk == self.sewing.pk             else page.context["departments"][0]
        page = self.client.get(self.page, {**AUGUST, "department": [self.sewing.pk, software.pk]})
        self.assertEqual(page.context["totals"]["employees"], 2)
        self.assertContains(page, f"department={self.sewing.pk}&amp;department={software.pk}")
        page = self.client.get(self.page, {**AUGUST, "department": self.sewing.pk})
        # The next step is the one main button; the rest is under More.
        self.assertContains(page, "Submit August for approval")
        self.assertContains(page, "Salary settings")

    def test_the_download_has_a_subtotal_per_department(self):
        reply = self.client.get(self.page, {**AUGUST, "format": "pdf"})
        self.assertEqual(reply["Content-Type"], "application/pdf")
        text = pdf_text(reply.content)
        for words in ("Salary by department", "Sewing: subtotal", "Software: subtotal", "Total"):
            self.assertIn(words, text)
        reply = self.client.get(self.page, {**AUGUST, "format": "xlsx"})
        self.assertIn("spreadsheet", reply["Content-Type"])
        self.assertTrue(AuditLog.objects.filter(action="export.downloaded",
                                                after_data__page="salary-by-department").exists())


class WeeklyTableTests(SalaryPageTests.__mro__[1]):
    """The People table of a weekly group's week answers its own requests."""

    def test_search_and_pages_of_a_week(self):
        from employees.models import EmployeeCompensation
        from payroll import paygroups
        from payroll.models import PayGroup

        group = paygroups.create_group(actor=self.admin, company_id=self.company.pk, name="Weekly workers",
                                       week_start=PayGroup.Weekday.SATURDAY)
        with use_company(self.company):
            EmployeeCompensation.objects.filter(employee=self.employee).update(pay_basis="daily",
                                                                               base_rate=Decimal("1000"))
        paygroups.set_member(actor=self.admin, company_id=self.company.pk, effective_from=datetime.date(2026, 8, 1),
                             employee=self.employee, pay_group=group)
        generate_payroll(actor=self.admin, company_id=self.company.pk, pay_group=group,
                         week=datetime.date(2026, 8, 8))
        self.client.force_login(self.admin)
        week = {**AUGUST, "group": group.pk, "week": "2026-08-08"}
        page = self.client.get(reverse("payroll:payroll_home"), week)
        self.assertContains(page, "Rahim")
        for extra in ({"search[value]": "Rahim"}, {"order[0][column]": "9", "order[0][dir]": "desc"},
                      {"start": "25"}):
            reply = self.client.get(reverse("payroll:payroll_home"), {
                **week, "table": "1", "draw": "2", "start": "0", "length": "25", **extra})
            self.assertEqual(reply.status_code, 200, extra)
            self.assertIn("data", reply.json())
