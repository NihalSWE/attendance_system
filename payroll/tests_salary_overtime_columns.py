"""The Salary page's overtime columns (2026-11-09): OT hours and OT pay on each
payslip row, by department, in the totals and in the download."""

import datetime
import re
from decimal import Decimal

from django.urls import reverse

from common.tenant import use_company
from common.tests_exports import xlsx_table
from payroll.services import generate_payroll
from payroll.tests_overtime import OvertimeBase

D = datetime.date
AUGUST = {"year": "2026", "month": "8"}


class OvertimeColumnTests(OvertimeBase):
    def setUp(self):
        super().setUp()
        self.work(D(2026, 8, 10), (9, 0), (20, 0))          # 2 hours after the shift
        run = generate_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        with use_company(self.company):
            record = run.records.get(employee=self.employee)
            line = record.lines.get(code="OVERTIME")
        self.hours, self.pay = line.quantity, line.amount
        self.client.force_login(self.admin)
        self.url = reverse("payroll:payroll_home")

    def test_the_people_table(self):
        self.assertEqual(self.hours, Decimal("2.00"))
        page = self.client.get(self.url, AUGUST)
        self.assertContains(page, ">OT hours</th>")
        self.assertContains(page, ">OT pay</th>")
        [record] = page.context["records"]
        self.assertEqual((record.table_ot_hours, record.table_ot_pay), (self.hours, self.pay))
        self.assertEqual((page.context["totals"]["ot_hours"], page.context["totals"]["ot_pay"]),
                         (self.hours, self.pay))
        self.assertContains(page, f"{self.pay:,.2f}")
        # Sortable by either.
        for column in ("8", "9"):
            reply = self.client.get(self.url, {**AUGUST, "table": "1", "draw": "2", "start": "0", "length": "25",
                                               "order[0][column]": column, "order[0][dir]": "desc"})
            self.assertEqual(reply.status_code, 200)
            cell = re.sub(r"<[^>]+>", "", reply.json()["data"][0][column]).strip()
            self.assertEqual(cell, "2" if column == "8" else f"{self.pay:,.2f}")

    def test_by_department_and_the_download(self):
        page = self.client.get(self.url, {**AUGUST, "view": "departments"})
        [group] = page.context["by_department"]
        self.assertEqual((group["ot_hours"], group["ot_pay"]), (self.hours, self.pay))
        reply = self.client.get(self.url, {**AUGUST, "format": "xlsx"})
        _lines, headers, rows = xlsx_table(reply.content)
        self.assertEqual(headers[7:9], ["OT hours", "OT pay"])
        person = dict(zip(headers, rows[0]))
        self.assertEqual((Decimal(str(person["OT hours"])), Decimal(str(person["OT pay"]))), (self.hours, self.pay))
