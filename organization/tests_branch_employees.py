"""A12 part 4: the Employees area, limited to the viewer's branches."""

import datetime
from decimal import Decimal

from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.urls import reverse

from access_control.page_access import BRANCH_PAGES

from accounts.models import CompanyMembership
from common.tenant import use_company
from employees.models import Employee, EmployeeCompensation
from organization import employee_edit_services as edit
from organization import employee_login
from organization.tests_access_page import PASSWORD, AccessPageBase


class BranchEmployeesBase(AccessPageBase):
    """Head Office: manager (Manny), clerk, other. Chittagong: Remote."""

    def edit_url(self, employee):
        return reverse("organization:employee_edit", args=[employee.pk])


class EmployeeListTests(BranchEmployeesBase):
    def test_a_branch_manager_sees_their_branch_with_pay_and_actions(self):
        self.login_as(self.manager)
        page = self.client.get(reverse("employee_list"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "clerk")
        self.assertNotContains(page, "Remote")
        self.assertContains(page, "20,000.00")
        self.assertContains(page, self.edit_url(self.clerk))
        self.assertContains(page, reverse("organization:employee_create"))
        # Their sidebar now has the Employees menu.
        self.assertContains(page, 'data-menu="employees"')

    def test_view_only_access_hides_pay_and_editing(self):
        self.grant(self.owner, self.clerk, "employees.view", self.hq)
        self.login_as(self.clerk_user)
        page = self.client.get(reverse("employee_list"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "other")
        self.assertNotContains(page, "20,000.00")
        self.assertNotContains(page, self.edit_url(self.other))
        self.assertNotContains(page, reverse("organization:employee_create"))

    def test_access_in_another_branch_shows_that_branch_only(self):
        self.grant(self.owner, self.clerk, "employees.view", self.unit)
        self.login_as(self.clerk_user)
        page = self.client.get(reverse("employee_list"))
        self.assertContains(page, "Remote")
        self.assertNotContains(page, ">other<")

    def test_the_owner_still_sees_everyone(self):
        self.login_as(self.owner)
        page = self.client.get(reverse("employee_list"))
        for name in ("clerk", "Remote", "Manny"):
            self.assertContains(page, name)
        self.assertContains(page, reverse("organization:employee_detail", args=[self.remote.pk]))

    def test_hr_and_plain_employees_are_kept_out(self):
        self.login_as(self.hr)
        self.assertEqual(self.client.get(reverse("employee_list")).status_code, 403)
        self.login_as(self.clerk_user)
        self.assertRedirects(self.client.get(reverse("employee_list")), reverse("me:home"))

    def test_nihals_pages_are_linked_once_they_are_branch_pages(self):
        # A12 part 7: the live "Now" refresh and the employee page follow
        # BRANCH_PAGES, so adding them there is all it takes.
        self.login_as(self.manager)
        detail = 'href="%s"' % reverse("organization:employee_detail", args=[self.clerk.pk])
        page = self.client.get(reverse("employee_list"))
        self.assertContains(page, "data-now-board")
        self.assertContains(page, detail)
        with patch.dict(BRANCH_PAGES):
            # N10 listed both; without the entries the links go again.
            del BRANCH_PAGES["attendance:attendance_now"]
            del BRANCH_PAGES["organization:employee_detail"]
            page = self.client.get(reverse("employee_list"))
        self.assertNotContains(page, "data-now-board")
        self.assertNotContains(page, detail)


class EditEmployeeTests(BranchEmployeesBase):
    def test_a_branch_manager_edits_details_in_their_branch(self):
        self.login_as(self.manager)
        response = self.client.post(self.edit_url(self.clerk), {
            "section": "details", "first_name": "Clara", "last_name": "Clerk",
            "work_email": "", "phone": "", "joining_date": "",
        })
        self.assertRedirects(response, self.edit_url(self.clerk))
        with use_company(self.company):
            self.assertEqual(Employee.objects.get(pk=self.clerk.pk).first_name, "Clara")

    def test_another_branch_is_refused(self):
        self.login_as(self.manager)
        self.assertEqual(self.client.get(self.edit_url(self.remote)).status_code, 403)

    def test_a_placement_cannot_move_to_a_branch_they_do_not_look_after(self):
        dept, desig = self.placement[self.unit.pk]
        with self.assertRaises(PermissionDenied):
            edit.change_placement(actor=self.manager, company_id=self.company.pk,
                                  employee_id=self.clerk.pk, values={
                                      "branch": self.unit, "department": dept, "designation": desig,
                                      "employee_code": "E1", "reason": "",
                                      "effective_at": datetime.datetime(2026, 9, 1, tzinfo=datetime.timezone.utc),
                                  })

    def test_pay_follows_prepare_salary(self):
        # The branch manager prepares salary for Head Office: pay may be changed.
        edit.change_salary(actor=self.manager, company_id=self.company.pk, employee_id=self.clerk.pk,
                           values={"pay_basis": "monthly", "base_rate": Decimal("22000"),
                                   "effective_at": datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
                                   "reason": ""})
        # Someone who may only edit people may not.
        self.grant(self.owner, self.clerk, "employees.edit", self.hq)
        self.login_as(self.clerk_user)
        page = self.client.get(self.edit_url(self.other))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Pay is set by")
        self.assertNotContains(page, "20,000.00")
        response = self.client.post(self.edit_url(self.other), {
            "section": "salary", "pay_basis": "monthly", "base_rate": "90000",
            "salary_from": "2026-01-01",
        })
        self.assertEqual(response.status_code, 403)
        with use_company(self.company):
            self.assertEqual(
                EmployeeCompensation.objects.get(employee=self.other).base_rate, Decimal("20000")
            )

    def test_shift_and_branch_manager_role_stay_with_the_company(self):
        self.login_as(self.manager)
        for section in ("shift", "login_role"):
            with self.subTest(section=section):
                self.assertEqual(
                    self.client.post(self.edit_url(self.clerk), {"section": section}).status_code, 403
                )
        self.assertContains(self.client.get(self.edit_url(self.clerk)),
                            "Own shifts are given by the owner or company administrator")


class BranchLoginManagementTests(BranchEmployeesBase):
    def test_a_branch_manager_resets_an_employee_password_and_disables_them(self):
        employee_login.reset_login_password(
            actor=self.manager, company_id=self.company.pk, employee_id=self.clerk.pk,
            values={"password": PASSWORD, "password_confirm": PASSWORD},
        )
        employee_login.set_login_active(
            actor=self.manager, company_id=self.company.pk, employee_id=self.clerk.pk, active=False,
        )
        self.assertEqual(
            CompanyMembership.all_objects.get(user=self.clerk_user).status,
            CompanyMembership.Status.SUSPENDED,
        )

    def test_another_branch_manager_login_is_the_companys(self):
        other_manager = self.employee("Mona", "M2", self.hq)
        employee_login.give_login(
            actor=self.owner, company_id=self.company.pk, employee_id=other_manager.pk,
            values={"email": "mona@bra.test", "password": PASSWORD, "password_confirm": PASSWORD,
                    "role": "manager", "branches": [self.hq]},
        )
        with self.assertRaises(PermissionDenied):
            employee_login.set_login_active(
                actor=self.manager, company_id=self.company.pk, employee_id=other_manager.pk,
                active=False,
            )

    def test_not_in_another_branch(self):
        remote_login = self.employee("Rita", "C2", self.unit)
        employee_login.give_login(
            actor=self.owner, company_id=self.company.pk, employee_id=remote_login.pk,
            values={"email": "rita@bra.test", "password": PASSWORD, "password_confirm": PASSWORD,
                    "role": "employee", "branches": []},
        )
        with self.assertRaises(PermissionDenied):
            employee_login.reset_login_password(
                actor=self.manager, company_id=self.company.pk, employee_id=remote_login.pk,
                values={"password": PASSWORD, "password_confirm": PASSWORD},
            )


class CreateEmployeeTests(BranchEmployeesBase):
    def post(self, branch, code="N1"):
        department, designation = self.placement[branch.pk]
        return self.client.post(reverse("organization:employee_create"), {
            "first_name": "Nadia", "last_name": "", "employee_code": code,
            "branch": branch.pk, "department": department.pk, "designation": designation.pk,
            "manager": "", "effective_from": "2026-09-01", "pay_basis": "monthly",
            "base_rate": "18000",
        })

    def test_a_branch_manager_adds_someone_to_their_branch_only(self):
        self.login_as(self.manager)
        page = self.client.get(reverse("organization:employee_create"))
        self.assertContains(page, "Head Office")
        self.assertNotContains(page, "Chittagong")
        self.assertRedirects(self.post(self.hq), reverse("employee_list"))
        self.post(self.unit, code="N2")
        with use_company(self.company):
            names = list(Employee.objects.filter(first_name="Nadia").values_list("pk", flat=True))
        self.assertEqual(len(names), 1)

    def test_the_department_lookups_stay_in_their_branches(self):
        self.login_as(self.manager)
        departments = reverse("organization:employee_branch_departments")
        self.assertTrue(self.client.get(departments, {"branch": self.hq.pk}).json()["results"])
        self.assertEqual(self.client.get(departments, {"branch": self.unit.pk}).json()["results"], [])
        unit_department, _ = self.placement[self.unit.pk]
        designations = reverse("organization:employee_department_designations")
        self.assertEqual(
            self.client.get(designations, {"department": unit_department.pk}).json()["results"], []
        )

    def test_edit_only_access_cannot_create_because_pay_needs_salary_access(self):
        self.grant(self.owner, self.clerk, "employees.edit", self.hq)
        self.login_as(self.clerk_user)
        self.assertEqual(self.post(self.hq).status_code, 403)
