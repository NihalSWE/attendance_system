"""A left employee's Employee ID is free for a new hire (2026-10-19).

The ID was never stuck: a new hire may take it from the day after the last
holder's final day. The refusal for an earlier joining date read "held by
someone whose placement has not ended", so a test record that had ended
looked as if it still owned the ID. It now says whose it was and from when
it is free.
"""

from django.urls import reverse

from common.tenant import use_company
from employees.models import EmployeeAssignment
from organization.tests_employee_detail import AUG_11, DetailTestCase


class ReuseTheIdTests(DetailTestCase):
    """A left employee's ID is free for a new hire from the day after their
    last day; a clash says whose it was and from when it is free."""

    def setUp(self):
        super().setUp()
        with use_company(self.company):
            self.first = EmployeeAssignment.objects.get(employee=self.employee)
        self.end(AUG_11)
        self.client.force_login(self.admin)

    def hire(self, joining):
        return self.client.post(reverse("organization:employee_create"), {
            "first_name": "Nadia", "last_name": "", "employee_code": "E1",
            "branch": self.first.branch_id, "department": self.first.department_id,
            "designation": self.first.designation_id, "manager": "",
            "effective_from": joining, "pay_basis": "monthly", "base_rate": "18000"})

    def test_a_joining_date_before_it_is_free_says_why(self):
        page = self.hire("2026-08-05")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Employee ID E1 belonged to Rahim until 11 Aug 2026")
        self.assertContains(page, "free from 12 Aug 2026")

    def test_a_new_hire_takes_it_from_the_next_day(self):
        self.assertEqual(self.hire("2026-08-12").status_code, 302)
        with use_company(self.company):
            holders = list(EmployeeAssignment.objects.filter(employee_code="E1")
                           .order_by("effective_from").values_list("employee__first_name", flat=True))
        self.assertEqual(holders, ["Rahim", "Nadia"])
