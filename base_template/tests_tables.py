"""A15 protocol, tenant boundaries, real database paging and table adapters."""

import datetime

from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.models import CompanyMembership, User
from base_template.tables import paginate
from common.tenant import use_company
from employees.models import Employee
from leaves import services as leave_services
from payroll import overtime
from payroll.models import PayrollRun
from payroll.services import generate_payroll
from payroll.table_views import overtime_queryset
from payroll.tests_overtime import OvertimeBase, rules
from payroll.tests_penalties import rule
from scheduling.models import Holiday
from tenants.services import onboard_company


class TableTests(OvertimeBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def draw(self, name, **params):
        response = self.client.get(reverse(name), {
            "table": "1", "draw": "7", "start": "0", "length": "10",
            "year": "2026", "month": "8", **params,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        data = response.json()
        self.assertEqual(data["draw"], 7)
        self.assertLessEqual(len(data["data"]), 10)
        return data

    def test_database_slice_search_sort_counts_and_hostile_parameters(self):
        other = onboard_company(code="OTHER", slug="other", name="Other")
        with use_company(other):
            Employee.objects.create(company=other, first_name="Secret employee")
        with use_company(self.company):
            Employee.objects.bulk_create([
                Employee(company=self.company, first_name=f"Person {i:03}") for i in range(36)])
            request = RequestFactory().get("/", {"table": "1", "start": 10, "length": 10,
                "order[0][column]": 0, "order[0][dir]": "desc", "columns[0][data]": "company__password"})
            with CaptureQueriesContext(connection) as queries:
                page = paginate(request, Employee.objects.all(), search=("first_name",), order=("first_name",))
                names = [employee.first_name for employee in page]
            self.assertEqual(page.paginator.count, 37)
            self.assertEqual(names, [f"Person {i:03}" for i in range(26, 16, -1)])
            self.assertTrue(any("LIMIT 10 OFFSET 10" in query["sql"] for query in queries))
        data = self.draw("employee_list", **{"search[value]": "Person 035", "start": 0})
        self.assertEqual((data["recordsTotal"], data["recordsFiltered"], len(data["data"])), (37, 1, 1))
        self.assertIn("Person 035", data["data"][0]["1"])
        self.assertNotIn("Secret employee", str(data))
        data = self.draw("employee_list", **{"length": -1, "start": 1000000, "order[0][column]": 9999})
        self.assertEqual(data["data"], [])
        data = self.draw("employee_list", **{"length": 1000000, "search[value]": "<script>"})
        self.assertEqual(data["recordsFiltered"], 0)
        html = self.client.get(reverse("employee_list"), {"per_page": 10})
        self.assertContains(html, 'aria-label="Result pages"')
        self.assertContains(html, 'page=4')
        self.assertContains(html, 'name="page" type="number"')

    def test_every_company_adapter_renders_json_and_all_whitelisted_orders(self):
        leave_type = leave_services.create_leave_type(actor=self.admin, company_id=self.company.pk,
            values={"code": "CAS", "name": "Casual"})
        leave_services.record_leave(actor=self.admin, company_id=self.company.pk, values={
            "employee": self.employee, "leave_type": leave_type, "start_date": datetime.date(2026, 8, 4),
            "end_date": datetime.date(2026, 8, 4), "pay_type": "paid", "reason": "Family"})
        with use_company(self.company):
            Holiday.objects.create(company=self.company, name="Festival", holiday_date=datetime.date(2026, 8, 15))
            penalty = rule(status="active", effective_from=datetime.date(2026, 1, 1))
            penalty.company = self.company
            penalty.save()
        self.work(datetime.date(2026, 8, 10), (9, 0), (20, 0))
        generate_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        screens = {"employee_list": 9, "organization:branch_list": 7, "organization:adoption_list": 7,
            "leaves:leave_list": 8, "leaves:leave_type_list": 5, "scheduling:holiday_list": 5,
            "payroll:payroll_home": 11, "payroll:salary_settings": 5, "payroll:overtime_list": 7}
        for name, columns in screens.items():
            with self.subTest(screen=name):
                data = self.draw(name)
                self.assertGreater(data["recordsTotal"], 0)
                self.assertEqual(len(data["data"][0]) - 2, columns)
                for column in range(columns):
                    self.draw(name, **{"order[0][column]": column, "order[0][dir]": "desc"})
                empty = self.draw(name, **{"search[value]": "NeverMatchesAnything"})
                self.assertEqual(empty["recordsFiltered"], 0)
                self.assertEqual(empty["data"], [])
        # Markup still comes from autoescaped Django templates.
        with use_company(self.company):
            self.employee.first_name = '<img src=x onerror="alert(1)">'
            self.employee.save(update_fields=["first_name"])
        cell = self.draw("employee_list")["data"][0]["1"]
        self.assertIn("&lt;img", cell)
        self.assertNotIn("<img", cell)

    def test_employee_and_manager_tables_keep_access_boundaries(self):
        worker = User.objects.create_user(email="worker@tables.test")
        manager = User.objects.create_user(email="manager@tables.test")
        CompanyMembership.all_objects.create(company=self.company, user=worker, role="employee", status="active")
        membership = CompanyMembership.all_objects.create(company=self.company, user=manager, role="manager", status="active")
        with use_company(self.company):
            membership.allowed_branches.add(self.branch)
            self.employee.user = worker
            self.employee.save(update_fields=["user"])
        self.client.force_login(worker)
        self.assertEqual(self.draw("me:leave")["data"], [])
        self.assertEqual(self.draw("me:payslips")["data"], [])
        self.assertEqual(self.client.get(reverse("payroll:overtime_list"), {"table": 1}).status_code, 302)
        self.client.force_login(manager)
        self.assertEqual(self.draw("me:leave_inbox")["data"], [])
        data = self.draw("me:branch_attendance", date="2026-08-10")
        self.assertEqual(data["recordsTotal"], 1)
        self.assertIn("Rahim", data["data"][0]["0"])
        self.client.logout()
        self.assertEqual(self.client.get(reverse("employee_list"), {"table": 1}).status_code, 302)

    def test_my_payslips_excludes_drafts_even_with_forged_employee(self):
        worker = User.objects.create_user(email="payslips@tables.test")
        CompanyMembership.all_objects.create(company=self.company, user=worker, role="employee", status="active")
        with use_company(self.company):
            self.employee.user = worker
            self.employee.save(update_fields=["user"])
        run = generate_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        self.client.force_login(worker)
        self.assertEqual(self.draw("me:payslips", employee=999)["recordsTotal"], 0)
        with use_company(self.company):
            PayrollRun.objects.filter(pk=run.pk).update(status="posted")
        data = self.draw("me:payslips", employee=999, **{"order[0][column]": 3})
        self.assertEqual(data["recordsTotal"], 1)
        self.assertEqual(len(data["data"][0]) - 2, 5)

    def test_overtime_sql_states_match_existing_claim_rules_and_filters(self):
        self.work(datetime.date(2026, 8, 10), (9, 0), (20, 0))
        self.work(datetime.date(2026, 8, 11), (9, 0), (18, 15))
        self.work(datetime.date(2026, 8, 12), (9, 0), (18, 0), (19, 0))
        rejected = self.work(datetime.date(2026, 8, 13), (9, 0), (20, 0))
        self.decide(rejected, approve=False, note="Not approved")
        with use_company(self.company):
            Holiday.objects.create(company=self.company, name="Day off", holiday_date=datetime.date(2026, 8, 14))
        self.work(datetime.date(2026, 8, 14), (9, 0), (12, 0))
        month = overtime.overtime_month(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        for salary_rules in (rules(), rules(minimum_overtime_minutes=60, overtime_rounding_minutes=60), rules(overtime_method="none")):
            with use_company(self.company):
                annotated = {r.pk: r for r in overtime_queryset(month["membership"], datetime.date(2026, 8, 1), datetime.date(2026, 8, 31), salary_rules)}
            self.assertEqual(set(annotated), {row.record.pk for row in month["rows"]})
            for row in month["rows"]:
                state = overtime.state_of(row.claim, row.decision, salary_rules)
                computed = annotated[row.record.pk]
                self.assertEqual(computed.table_state, state)
                row.state = state
                self.assertEqual(computed.table_paid, salary_rules.payable_overtime(row.approved_minutes))
        self.assertEqual(self.draw("payroll:overtime_list", show="waiting")["recordsFiltered"], 1)
        self.assertEqual(self.draw("payroll:overtime_list", show="rejected")["recordsFiltered"], 1)
        self.assertEqual(self.draw("payroll:overtime_list", employee=self.employee.pk, branch=self.branch.pk)["recordsFiltered"], 5)
        self.assertEqual(self.draw("payroll:overtime_list", employee=999999)["recordsFiltered"], 0)
