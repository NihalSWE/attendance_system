"""A12 part 2: Organisation → Access — people per branch, ticks, logins."""

import datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from access_control.branch_access import can, grant_access
from access_control.models import EmployeePermissionOverride
from accounts.models import CompanyMembership, User
from auditlog.models import AuditLog
from common.tenant import use_company
from employees.services import create_employee
from organization.access_services import parse_ticked
from organization.catalogue import adopt_department, adopt_designation
from organization.models import Branch
from tenants.services import onboard_company

PASSWORD = "Str0ng-pass-2026"
START = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)


class AccessPageBase(TestCase):
    """Head Office: manager (Manny), clerk, other. Chittagong: remote (no login)."""

    def setUp(self):
        self.company = onboard_company(code="BRA", slug="bra", name="Branch Ltd")
        with use_company(self.company):
            self.hq = Branch.objects.get(is_default=True)
            self.unit = Branch.objects.create(
                company=self.company, code="CTG", name="Chittagong",
                timezone="Asia/Dhaka", country_code="BD",
            )
            self.department = adopt_department(self.hq, "SW", "Software")
            self.designation = adopt_designation(self.department, "DEV", "Developer")
            unit_department = adopt_department(self.unit, "SW", "Software")
            self.placement = {
                self.hq.pk: (self.department, self.designation),
                self.unit.pk: (unit_department, adopt_designation(unit_department, "DEV", "Developer")),
            }
        self.owner = self.member("owner@bra.test", "company_admin")
        self.manager = self.member("manager@bra.test", "manager", branches=[self.hq])
        self.hr = self.member("hr@bra.test", "hr")
        self.clerk_user, self.clerk = self.staff("clerk@bra.test", "E1")
        self.other_user, self.other = self.staff("other@bra.test", "E2")
        self.remote = self.employee("Remote", "C1", self.unit)
        self.manager_employee = self.employee("Manny", "M1", self.hq)
        with use_company(self.company):
            self.manager_employee.user = self.manager
            self.manager_employee.save(update_fields=["user"])

    def member(self, email, role, branches=()):
        user = User.objects.create_user(email=email)
        membership = CompanyMembership.all_objects.create(
            company=self.company, user=user, role=role, status="active"
        )
        with use_company(self.company):
            membership.allowed_branches.set(branches)
        return user

    def employee(self, name, code, branch):
        department, designation = self.placement[branch.pk]
        return create_employee(
            company=self.company, first_name=name, employee_code=code,
            branch=branch, department=department, designation=designation,
            effective_from=START, pay_basis="monthly", base_rate=Decimal("20000"),
        )["employee"]

    def staff(self, email, code):
        user = self.member(email, "employee")
        employee = self.employee(email.split("@")[0], code, self.hq)
        with use_company(self.company):
            employee.user = user
            employee.save(update_fields=["user"])
        return user, employee

    def grant(self, actor, employee, code, *branches):
        return grant_access(actor=actor, company_id=self.company.pk, employee_id=employee.pk,
                            code=code, branch_ids=[b.pk for b in branches])

    def login_as(self, user):
        self.client.force_login(user)

    def person_url(self, employee):
        return reverse("organization:access_person", args=[employee.pk])


class AccessListTests(AccessPageBase):
    def test_owner_sees_everyone_manager_only_their_branch(self):
        self.login_as(self.owner)
        page = self.client.get(reverse("organization:access"))
        self.assertEqual(page.status_code, 200)
        for name in ("clerk", "other", "Remote", "Manny"):
            self.assertContains(page, name)

        self.login_as(self.manager)
        page = self.client.get(reverse("organization:access"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "clerk")
        self.assertNotContains(page, "Remote")
        self.assertContains(page, "Branch manager: every permission in Head Office")

    def test_people_who_cannot_give_access_are_refused(self):
        self.login_as(self.hr)
        self.assertEqual(self.client.get(reverse("organization:access")).status_code, 403)
        # An Employee login is kept on its own pages by the gate.
        self.login_as(self.clerk_user)
        self.assertRedirects(self.client.get(reverse("organization:access")), reverse("me:home"))

    def test_the_gate_opens_only_the_access_pages_to_a_branch_manager(self):
        self.login_as(self.manager)
        self.assertEqual(self.client.get(self.person_url(self.clerk)).status_code, 200)
        self.assertRedirects(self.client.get(reverse("payroll:payroll_home")), reverse("me:home"))

    def test_a_granted_permission_shows_in_the_list(self):
        self.grant(self.owner, self.clerk, "leave.view", self.hq)
        self.login_as(self.owner)
        page = self.client.get(reverse("organization:access"), {"branch": self.hq.pk})
        self.assertContains(page, "View leave — Head Office")
        self.assertNotContains(page, "Remote")


class PersonAccessTests(AccessPageBase):
    def test_manager_ticks_and_unticks_in_their_branch(self):
        self.login_as(self.manager)
        response = self.client.post(self.person_url(self.clerk), {
            "section": "access", "reason": "Covers leave",
            f"grant__leave.view__{self.hq.pk}": "on",
            f"grant__leave.approve__{self.hq.pk}": "on",
        })
        self.assertRedirects(response, self.person_url(self.clerk))
        self.assertTrue(can(self.clerk_user, self.company.pk, "leave.approve", self.hq.pk))
        self.assertTrue(AuditLog.objects.filter(action="access.granted").exists())

        self.client.post(self.person_url(self.clerk), {
            "section": "access", f"grant__leave.view__{self.hq.pk}": "on",
        })
        self.assertFalse(can(self.clerk_user, self.company.pk, "leave.approve", self.hq.pk))
        self.assertTrue(can(self.clerk_user, self.company.pk, "leave.view", self.hq.pk))

    def test_a_crafted_tick_for_another_branch_is_ignored(self):
        self.login_as(self.manager)
        self.client.post(self.person_url(self.clerk), {
            "section": "access", f"grant__salary.view__{self.unit.pk}": "on",
        })
        self.assertFalse(can(self.clerk_user, self.company.pk, "salary.view", self.unit.pk))
        with use_company(self.company):
            self.assertFalse(EmployeePermissionOverride.objects.exists())

    def test_a_manager_cannot_open_someone_in_another_branch(self):
        self.login_as(self.manager)
        self.assertEqual(self.client.get(self.person_url(self.remote)).status_code, 403)

    def test_nobody_changes_their_own_access_and_admins_need_nothing(self):
        self.login_as(self.manager)
        page = self.client.get(self.person_url(self.manager_employee))
        self.assertContains(page, "This is you")
        self.assertNotContains(page, "Save access")

    def test_owner_gives_access_in_any_branch(self):
        self.login_as(self.owner)
        self.client.post(self.person_url(self.remote), {
            "section": "access", f"grant__salary.view__{self.unit.pk}": "on",
            f"grant__salary.view__{self.hq.pk}": "on",
        })
        with use_company(self.company):
            grant = EmployeePermissionOverride.objects.get(employee=self.remote)
            self.assertEqual(
                sorted(grant.allowed_branches.values_list("pk", flat=True)),
                sorted([self.hq.pk, self.unit.pk]),
            )

    def test_parse_ticked_ignores_unknown_and_malformed_names(self):
        self.assertEqual(
            parse_ticked({"grant__leave.view__3": "on", "grant__salary.finalise__3": "on",
                          "grant__leave.view__x": "on", "reason": "x"}),
            {("leave.view", 3)},
        )


class BranchPagesTests(AccessPageBase):
    """A12 part 3: company pages open by permission; sidebar and My account follow."""

    def test_a_person_given_access_to_give_access_reaches_the_access_page(self):
        self.grant(self.owner, self.clerk, "access.grant", self.hq)
        self.grant(self.owner, self.clerk, "leave.view", self.hq)
        self.login_as(self.clerk_user)
        page = self.client.get(reverse("organization:access"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Company")
        self.assertContains(page, f'href="{reverse("organization:access")}"')
        # Pages not opened to branches yet still send them home.
        self.assertRedirects(self.client.get(reverse("leaves:leave_list")), reverse("me:home"))

    def test_without_access_nothing_opens_and_no_company_menu(self):
        self.login_as(self.clerk_user)
        self.assertRedirects(self.client.get(reverse("organization:access")), reverse("me:home"))
        home = self.client.get(reverse("me:home"))
        self.assertNotContains(home, "Your branches")
        self.assertNotContains(home, 'data-menu="organisation"')

    def test_a_branch_manager_sees_the_company_menu_and_their_branch_card(self):
        self.login_as(self.manager)
        home = self.client.get(reverse("me:home"))
        self.assertContains(home, 'data-menu="organisation"')
        self.assertContains(home, "Your branches")
        self.assertContains(home, "People placed in your branches")
        self.assertContains(home, "Leave waiting for your approval")
        self.assertContains(home, "Give access")
        self.assertRedirects(self.client.get(reverse("payroll:payroll_home")), reverse("me:home"))

    def test_a_grant_without_a_page_shows_the_card_but_opens_nothing(self):
        self.grant(self.owner, self.clerk, "leave.view", self.hq)
        self.login_as(self.clerk_user)
        home = self.client.get(reverse("me:home"))
        self.assertContains(home, "Your branches")
        self.assertContains(home, "Head Office")
        self.assertNotContains(home, "Give access")
        self.assertNotContains(home, 'data-menu="organisation"')

    def test_the_owner_keeps_the_company_sidebar(self):
        self.login_as(self.owner)
        page = self.client.get(reverse("organization:access"))
        self.assertContains(page, 'data-menu="employees"')
        self.assertContains(page, 'data-menu="salary"')

    def test_may_open_needs_a_listed_page_and_its_permission(self):
        from access_control.page_access import may_open

        company = self.company.pk
        self.assertTrue(may_open(self.manager, company, "organization:access"))
        self.assertFalse(may_open(self.manager, company, "payroll:payroll_home"))
        self.assertFalse(may_open(self.clerk_user, company, "organization:access"))


class BranchLoginTests(AccessPageBase):
    def test_manager_creates_an_employee_login_in_their_branch(self):
        new = self.employee("Newbie", "E9", self.hq)
        self.login_as(self.manager)
        page = self.client.get(self.person_url(new))
        self.assertContains(page, "Create login")
        self.assertNotContains(page, "Branches they manage")
        response = self.client.post(self.person_url(new), {
            "section": "login", "login_email": "newbie@bra.test",
            "login_password": PASSWORD, "login_password_confirm": PASSWORD,
        })
        self.assertRedirects(response, self.person_url(new))
        membership = CompanyMembership.all_objects.get(user__email="newbie@bra.test")
        self.assertEqual(membership.role, "employee")

    def test_manager_cannot_create_a_login_in_another_branch(self):
        from django.core.exceptions import PermissionDenied

        from organization.employee_login import give_login

        with self.assertRaises(PermissionDenied):
            give_login(actor=self.manager, company_id=self.company.pk, employee_id=self.remote.pk,
                       values={"email": "remote@bra.test", "password": PASSWORD,
                               "password_confirm": PASSWORD, "role": "employee", "branches": []})

    def test_only_the_company_makes_someone_a_branch_manager(self):
        from django.core.exceptions import PermissionDenied

        from organization.employee_login import give_login

        new = self.employee("Hopeful", "E8", self.hq)
        with self.assertRaises(PermissionDenied):
            give_login(actor=self.manager, company_id=self.company.pk, employee_id=new.pk,
                       values={"email": "hopeful@bra.test", "password": PASSWORD,
                               "password_confirm": PASSWORD, "role": "manager",
                               "branches": [self.hq]})
