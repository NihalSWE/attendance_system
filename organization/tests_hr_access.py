"""HR and employees (Ajay, 2026-09-27): HR views and edits everyone, company-wide,
and sees no pay - on the profile, on Edit employee, on the list - except in a
branch where the Access page granted "View salaries".

Head Office: Rahim, Clerk, and Hana (HR's own employee record). Chittagong: Karim.
"""

import datetime
from decimal import Decimal

from django.urls import reverse

from access_control.branch_access import ALL_BRANCHES, branches_for
from common.tenant import use_company
from employees.models import Employee, EmployeeCompensation
from employees.services import create_employee
from leaves.tests_branch_access import TwoBranchCase
from attendance.tests_live import UTC

RATE = "30,000"


class HrCase(TwoBranchCase):
    def setUp(self):
        super().setUp()
        self.hana = create_employee(
            company=self.company, first_name="Hana", employee_code="H1",
            branch=self.branch, department=self.hq_department,
            designation=self.hq_designation,
            effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC),
            pay_basis="monthly", base_rate=Decimal("45000"),
        )["employee"]
        with use_company(self.company):
            self.hana.user = self.hr
            self.hana.save(update_fields=["user"])
        self.client.force_login(self.hr)

    def profile(self, employee):
        return self.client.get(reverse("organization:employee_detail", args=[employee.pk]))

    def edit(self, employee):
        return self.client.get(reverse("organization:employee_edit", args=[employee.pk]))


class CompanyWideTests(HrCase):
    def test_hr_views_and_edits_employees_in_every_branch(self):
        for code in ("employees.view", "employees.edit"):
            self.assertIs(branches_for(self.hr, self.company.pk, code), ALL_BRANCHES)
        for code in ("salary.view", "salary.prepare", "employees.logins", "access.grant"):
            self.assertEqual(branches_for(self.hr, self.company.pk, code), set())

    def test_the_list_shows_everyone_without_pay(self):
        page = self.client.get(reverse("employee_list"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Karim")
        self.assertContains(page, "Rahim")
        self.assertNotContains(page, RATE)

    def test_a_profile_in_another_branch_opens_without_pay(self):
        page = self.profile(self.far)
        self.assertEqual(page.status_code, 200)
        self.assertFalse(page.context["may"]["salary"])
        self.assertTrue(page.context["may"]["edit"])
        self.assertIsNone(page.context["compensation"])
        self.assertNotContains(page, "Salary history")
        self.assertNotContains(page, RATE)

    def test_edit_employee_opens_without_pay_or_allowances(self):
        page = self.edit(self.far)
        self.assertEqual(page.status_code, 200)
        self.assertFalse(page.context["may"]["salary"])
        self.assertNotContains(page, RATE)
        self.assertNotContains(page, 'id="allowances"')
        self.assertContains(page, "Has a salary")

    def test_hr_saves_details_in_another_branch(self):
        response = self.client.post(reverse("organization:employee_edit", args=[self.far.pk]), {
            "section": "details", "first_name": "Karim", "last_name": "Uddin",
            "work_email": "", "phone": "", "joining_date": "2026-01-01"})
        self.assertEqual(response.status_code, 302)
        self.far.refresh_from_db()
        self.assertEqual(self.far.last_name, "Uddin")

    def test_hr_cannot_change_pay(self):
        response = self.client.post(reverse("organization:employee_edit", args=[self.far.pk]), {
            "section": "salary", "pay_basis": "monthly", "base_rate": "1",
            "salary_from": "2026-09-01"})
        self.assertEqual(response.status_code, 403)
        response = self.client.post(reverse("organization:employee_edit", args=[self.far.pk]), {
            "section": "component_give"})
        self.assertEqual(response.status_code, 403)


class AddingPeopleTests(HrCase):
    def form(self, **extra):
        with use_company(self.company):
            placed = self.far.assignments.get()
        return {"first_name": "Nadia", "last_name": "", "employee_code": "C9",
                "branch": self.unit.pk, "department": placed.department_id,
                "designation": placed.designation_id, "manager": "",
                "effective_from": "2026-09-01", **extra}

    def test_hr_adds_someone_without_pay(self):
        page = self.client.get(reverse("organization:employee_create"))
        self.assertNotIn("base_rate", page.context["form"].fields)
        response = self.client.post(reverse("organization:employee_create"), self.form())
        self.assertRedirects(response, reverse("employee_list"), fetch_redirect_response=False)
        with use_company(self.company):
            nadia = Employee.objects.get(first_name="Nadia")
            self.assertFalse(EmployeeCompensation.objects.filter(employee=nadia).exists())

    def test_pay_sent_anyway_is_not_kept(self):
        self.client.post(reverse("organization:employee_create"),
                         self.form(pay_basis="monthly", base_rate="99999"))
        with use_company(self.company):
            self.assertFalse(EmployeeCompensation.objects.filter(
                employee__first_name="Nadia").exists())

    def test_a_branch_manager_still_sets_pay(self):
        self.client.force_login(self.manager)
        form = self.form(branch=self.branch.pk, department=self.hq_department.pk,
                         designation=self.hq_designation.pk)
        page = self.client.post(reverse("organization:employee_create"), form)
        self.assertEqual(page.status_code, 200)
        self.assertTrue(page.context["form"].errors["base_rate"])
        self.client.post(reverse("organization:employee_create"),
                         {**form, "pay_basis": "monthly", "base_rate": "20000"})
        with use_company(self.company):
            self.assertEqual(EmployeeCompensation.objects.get(
                employee__first_name="Nadia").base_rate, Decimal("20000"))

    def test_granted_in_one_branch_sets_pay_there_only(self):
        self.grant("salary.view", self.unit, to=self.hana)
        self.grant("salary.prepare", self.unit, to=self.hana)
        form = self.client.get(reverse("organization:employee_create")).context["form"]
        self.assertFalse(form.fields["base_rate"].required)
        page = self.client.post(reverse("organization:employee_create"),
                                self.form(branch=self.branch.pk,
                                          department=self.hq_department.pk,
                                          designation=self.hq_designation.pk,
                                          pay_basis="monthly", base_rate="20000"))
        self.assertIn("do not set pay in this branch", str(page.context["form"].errors))
        self.client.post(reverse("organization:employee_create"),
                         self.form(pay_basis="monthly", base_rate="20000"))
        with use_company(self.company):
            self.assertEqual(EmployeeCompensation.objects.get(
                employee__first_name="Nadia").base_rate, Decimal("20000"))


class GrantedSalaryTests(HrCase):
    """Salary for HR comes only from the Access page, per branch - and it attaches
    to HR's own employee record (Hana), so an HR login without one cannot get it."""

    def setUp(self):
        super().setUp()
        self.grant("salary.view", self.unit, to=self.hana)

    def test_pay_shows_in_the_granted_branch_only(self):
        far = self.profile(self.far)
        self.assertTrue(far.context["may"]["salary"])
        self.assertContains(far, "Salary history")
        self.assertContains(far, RATE)
        near = self.profile(self.employee)
        self.assertFalse(near.context["may"]["salary"])
        self.assertNotContains(near, "Salary history")

    def test_the_edit_page_shows_it_but_view_alone_changes_nothing(self):
        page = self.edit(self.far)
        self.assertContains(page, RATE)
        self.assertContains(page, 'id="allowances"')
        self.assertFalse(page.context["may"]["salary"])

    def test_the_list_shows_pay_for_that_branch(self):
        page = self.client.get(reverse("employee_list"))
        shown = {row["e"].first_name: row["show_rate"] for row in page.context["rows"]}
        self.assertTrue(shown["Karim"])
        self.assertFalse(shown["Rahim"])
        self.assertContains(page, RATE, count=1)

    def test_the_access_page_describes_hr(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("organization:access_person", args=[self.hana.pk]))
        self.assertContains(page, "Salary only where given here")
