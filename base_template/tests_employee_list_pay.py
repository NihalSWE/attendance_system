"""The Employees list shows a pay column only to someone who may see pay.

Ajay, 2026-09-20: the "Base rate" column used to be rendered for every viewer,
showing a dash to anyone without ``salary.view`` — a department head, or an
employee login granted only ``employees.view``. The column now exists only when
the viewer may see pay in at least one branch; the per-row check that hides
another branch's pay from a partly-granted viewer is unchanged.

A branch manager is not one of those viewers: they hold every branch permission
in their own branches automatically, ``salary.view`` included, so their column
stays. That is asserted here too, because it is the easy thing to break.
"""

from django.urls import reverse

from common.tenant import use_company
from leaves.tests_branch_access import TwoBranchCase

HEADER = "Base rate"
RAHIM_PAY = "30,000"


class EmployeeListPayColumnTests(TwoBranchCase):
    def list_page(self, user):
        self.client.force_login(user)
        page = self.client.get(reverse("employee_list"))
        self.assertEqual(page.status_code, 200)
        self.client.logout()
        return page

    def headers(self, page):
        head = page.content.decode().split("<thead>", 1)[1].split("</thead>", 1)[0]
        return head.count("<th")

    # --- who keeps the column -------------------------------------------

    def test_a_company_login_sees_the_column_and_the_pay(self):
        page = self.list_page(self.admin)
        self.assertContains(page, HEADER)
        self.assertContains(page, RAHIM_PAY)

    def test_a_branch_manager_keeps_the_column(self):
        """They hold salary.view in their own branches without being granted it."""
        page = self.list_page(self.manager)
        self.assertContains(page, HEADER)
        self.assertContains(page, RAHIM_PAY)

    # --- who loses it ----------------------------------------------------

    def test_a_viewer_granted_only_employees_view_gets_no_column(self):
        self.grant("employees.view", self.branch)
        page = self.list_page(self.clerk_user)
        self.assertContains(page, "Rahim")       # the list itself still opens
        self.assertNotContains(page, HEADER)
        self.assertNotContains(page, RAHIM_PAY)

    def test_granting_salary_view_as_well_brings_the_column_back(self):
        self.grant("employees.view", self.branch)
        self.grant("salary.view", self.branch)
        page = self.list_page(self.clerk_user)
        self.assertContains(page, HEADER)
        self.assertContains(page, RAHIM_PAY)

    def test_salary_view_in_another_branch_still_brings_the_column(self):
        """Pay somewhere is enough for the column; the row check hides the rest."""
        self.grant("employees.view", self.branch, self.unit)
        self.grant("salary.view", self.unit)
        page = self.list_page(self.clerk_user)
        self.assertContains(page, HEADER)
        self.assertContains(page, "Karim")       # the branch whose pay they see
        self.assertContains(page, "Rahim")       # listed, but pay withheld
        self.assertEqual(page.content.decode().count(RAHIM_PAY), 1)

    def test_a_department_head_gets_no_column(self):
        with use_company(self.company):
            self.hq_department.head = self.clerk
            self.hq_department.save(update_fields=["head"])
        page = self.list_page(self.clerk_user)
        self.assertContains(page, "Rahim")       # the head's own department
        self.assertNotContains(page, HEADER)
        self.assertNotContains(page, RAHIM_PAY)

    # --- the table stays consistent --------------------------------------

    def test_dropping_the_column_drops_its_cells_too(self):
        """A header and its cells must go together, or every row is skewed."""
        self.grant("employees.view", self.branch)
        for user in (self.admin, self.clerk_user):
            with self.subTest(user=user.email):
                page = self.list_page(user)
                body = page.content.decode().split("</thead>", 1)[1]
                first_row = body.split("<tr>", 1)[1].split("</tr>", 1)[0]
                self.assertEqual(first_row.count("<td"), self.headers(page))

    def test_the_column_count_differs_by_exactly_one(self):
        self.grant("employees.view", self.branch)
        self.assertEqual(
            self.headers(self.list_page(self.admin)) - 1,
            self.headers(self.list_page(self.clerk_user)),
        )

    def test_sorting_by_pay_is_offered_to_a_company_login_only(self):
        """Column 8 is the rate; a branch viewer may not sort the list by pay."""
        self.assertContains(self.list_page(self.admin), 'data-orderable="1,2,3,4,5,7,8"')
        self.assertContains(self.list_page(self.manager), 'data-orderable="1,2,3,4,5,7"')
