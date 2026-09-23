"""The "Needs setup" filter on the Employees list (Ajay, 2026-09-21).

States are computed from the live data - is the department the branch's
Unassigned row, is there a salary record - never from the needs_hr_review
flag, which the import writes once and nobody clears. The fixture imports two
people into Head Office the real way, then HR fixes one of them.
"""

from decimal import Decimal

from django.urls import reverse

from common.tenant import use_company
from employees.models import Employee
from leaves.tests_branch_access import TwoBranchCase
from organization import import_services
from organization.employee_edit_services import change_placement, change_salary


class SetupCase(TwoBranchCase):
    def setUp(self):
        super().setUp()
        rows = [{"line": 2, "employee_id": "445961", "name": "Imported One"},
                {"line": 3, "employee_id": "445962", "name": "Imported Two"}]
        created = import_services.commit(actor=self.admin, company_id=self.company.pk,
                                         rows=rows, branch_id=self.branch.pk)
        self.one, self.two = created

    def page(self, user=None, **params):
        self.client.force_login(user or self.admin)
        response = self.client.get(reverse("employee_list"), params)
        self.assertEqual(response.status_code, 200)
        return response

    def counts(self, response):
        return {link["key"] or "all": link["count"] for link in response.context["setup_links"]}

    def names(self, response):
        return {row["e"].full_name for row in response.context["rows"]}

    def placement_of(self, employee):
        with use_company(self.company):
            return employee.assignments.get()

    def give_salary(self, employee):
        placement = self.placement_of(employee)
        change_salary(actor=self.admin, company_id=self.company.pk, employee_id=employee.pk,
                      values={"pay_basis": "monthly", "base_rate": Decimal("20000"),
                              "effective_at": placement.effective_from, "reason": ""})

    def give_department(self, employee):
        placement = self.placement_of(employee)
        change_placement(actor=self.admin, company_id=self.company.pk, employee_id=employee.pk,
                         values={"branch": self.branch, "department": self.hq_department,
                                 "designation": self.hq_designation,
                                 "employee_code": placement.employee_code,
                                 "effective_at": placement.effective_from, "reason": ""})


class CountsAndFilterTests(SetupCase):
    def test_the_counts_after_an_import(self):
        # Rahim, Clerk and Karim are set up; the two imported people need both.
        self.assertEqual(self.counts(self.page()), {
            "all": 5, "department": 2, "salary": 2, "both": 2, "complete": 3,
        })

    def test_each_filter_lists_its_people(self):
        self.assertEqual(self.names(self.page(setup="both")), {"Imported One", "Imported Two"})
        self.assertEqual(self.names(self.page(setup="complete")), {"Rahim", "Clerk", "Karim"})

    def test_fixing_someone_moves_them_at_once(self):
        """The screen follows the data, not the flag the import wrote."""
        self.give_salary(self.one)
        response = self.page()
        self.assertEqual(self.counts(response), {
            "all": 5, "department": 2, "salary": 1, "both": 1, "complete": 3,
        })
        self.assertEqual(self.names(self.page(setup="salary")), {"Imported Two"})

        self.give_department(self.one)
        self.assertEqual(self.names(self.page(setup="complete")),
                         {"Rahim", "Clerk", "Karim", "Imported One"})
        # The import's flag is still there for the audit trail - and ignored.
        with use_company(self.company):
            self.assertTrue(Employee.objects.get(pk=self.one.pk).metadata["needs_hr_review"])

    def test_the_filter_keeps_the_other_filters(self):
        response = self.page(setup="both", q="One")
        self.assertEqual(self.names(response), {"Imported One"})
        # Each setup link carries the search along.
        link = next(link for link in response.context["setup_links"] if link["key"] == "salary")
        self.assertIn("q=One", link["url"])

    def test_an_unknown_setup_value_is_all(self):
        response = self.page(setup="nonsense")
        self.assertEqual(len(self.names(response)), 5)
        self.assertEqual(response.context["setup"], "")


class ChipTests(SetupCase):
    def test_the_row_says_what_is_missing(self):
        response = self.page()
        self.assertContains(response, "No department", count=2)
        self.assertContains(response, "No salary", count=2)

    def test_a_set_up_person_has_no_chip(self):
        self.give_salary(self.one)
        self.give_department(self.one)
        gaps = {row["e"].full_name: row["gaps"] for row in self.page().context["rows"]}
        self.assertEqual(gaps["Imported One"], [])
        self.assertEqual(gaps["Imported Two"], ["No department", "No salary"])


class PayVisibilityTests(SetupCase):
    def test_a_department_head_is_never_told_who_has_a_salary(self):
        with use_company(self.company):
            self.hq_department.head = self.clerk
            self.hq_department.save(update_fields=["head"])
        self.give_department(self.one)            # now in the head's department, no salary
        response = self.page(self.clerk_user)
        keys = [link["key"] for link in response.context["setup_links"]]
        self.assertNotIn("salary", keys)
        self.assertNotIn("both", keys)
        self.assertNotContains(response, "No salary")
        # Imported One has no salary, but as far as a head can tell, is complete.
        self.assertIn("Imported One", self.names(self.page(self.clerk_user, setup="complete")))
        # A crafted ?setup=salary tells them nothing either.
        self.assertEqual(self.names(self.page(self.clerk_user, setup="salary")), set())

    def test_salary_gaps_count_only_where_the_viewer_may_see_pay(self):
        """Pay seen in Chittagong only: Head Office's imports do not 'need salary'."""
        self.grant("employees.view", self.branch, self.unit)
        self.grant("salary.view", self.unit)
        counts = self.counts(self.page(self.clerk_user))
        self.assertEqual(counts["salary"], 0)
        self.assertEqual(counts["department"], 2)


class BranchFilterTests(SetupCase):
    """One branch at a time on the Employees list (Nihal, 2026-09-23)."""

    def test_the_company_can_pick_one_branch(self):
        response = self.page()
        self.assertEqual({b.pk for b in response.context["branches"]},
                         {self.branch.pk, self.unit.pk})
        self.assertEqual(self.names(self.page(branch=str(self.unit.pk))), {"Karim"})
        self.assertEqual(
            self.names(self.page(branch=str(self.branch.pk))),
            {"Rahim", "Clerk", "Imported One", "Imported Two"},
        )

    def test_the_counts_follow_the_branch(self):
        counts = self.counts(self.page(branch=str(self.unit.pk)))
        self.assertEqual(counts, {"all": 1, "department": 0, "salary": 0,
                                  "both": 0, "complete": 1})

    def test_it_keeps_the_other_filters(self):
        response = self.page(branch=str(self.branch.pk), setup="both")
        self.assertEqual(self.names(response), {"Imported One", "Imported Two"})
        link = next(link for link in response.context["setup_links"] if link["key"] == "salary")
        self.assertIn(f"branch={self.branch.pk}", link["url"])

    def test_an_unknown_or_out_of_reach_branch_is_ignored(self):
        self.assertEqual(len(self.names(self.page(branch="999999"))), 5)
        self.assertEqual(self.page(branch="999999").context["branch"], "")

    def test_a_branch_manager_is_offered_their_own_branch_only(self):
        response = self.page(self.manager)
        self.assertEqual([b.pk for b in response.context["branches"]], [self.branch.pk])
        # Asking for the branch they cannot see shows nobody, not everybody.
        self.assertEqual(self.names(self.page(self.manager, branch=str(self.unit.pk))),
                         {"Rahim", "Clerk", "Imported One", "Imported Two"})

    def test_the_download_follows_it_and_is_named_for_it(self):
        from common import exports
        from common.tests_exports import xlsx_table

        self.client.force_login(self.admin)
        response = self.client.get(reverse("employee_list"),
                                   {"branch": self.unit.pk, "format": "xlsx"})
        lines, headers, rows = xlsx_table(response.content)
        self.assertEqual([dict(zip(headers, row))["Name"] for row in rows], ["Karim"])
        self.assertIn("Scope: Chittagong", lines[1])
        self.assertIn(f"employees-{exports.slug(self.unit.name)}-", response["Content-Disposition"])

    def test_the_page_offers_the_filter_only_when_there_is_a_choice(self):
        response = self.page()
        self.assertContains(response, "All branches")
        # A branch manager sees one branch, so no picker is drawn.
        self.assertNotContains(self.page(self.manager), "All branches")
