"""A6 logins: giving an employee a login, and what that login may reach."""

import datetime
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts.models import CompanyMembership, User
from auditlog.models import AuditLog
from common.tenant import use_company
from employees.services import create_employee
from organization import employee_login
from organization.catalogue import adopt_department, adopt_designation
from organization.models import Branch
from tenants.services import onboard_company

PASSWORD = "Str0ng-pass-2026"


class LoginBase(TestCase):
    def setUp(self):
        self.company = onboard_company(code="ACME", slug="acme", name="Acme Ltd")
        self.admin = User.objects.create_user(email="admin@acme.test", password="pw")
        self.hr = User.objects.create_user(email="hr@acme.test", password="pw")
        for user, role in ((self.admin, "company_admin"), (self.hr, "hr")):
            CompanyMembership.all_objects.create(
                company=self.company, user=user, role=role,
                status=CompanyMembership.Status.ACTIVE,
            )
        with use_company(self.company):
            self.hq = Branch.objects.get(is_default=True)
            sales = adopt_department(self.hq, "SL", "Sales")
            clerk = adopt_designation(sales, "CLK", "Clerk")
        self.employee = create_employee(
            company=self.company, first_name="Rina", last_name="Akter", employee_code="E1",
            branch=self.hq, department=sales, designation=clerk,
            effective_from=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
            pay_basis="monthly", base_rate=Decimal("30000"),
        )["employee"]

    def give(self, actor=None, **values):
        return employee_login.give_login(
            actor=actor or self.admin, company_id=self.company.pk, employee_id=self.employee.pk,
            values={"email": "rina@acme.test", "password": PASSWORD,
                    "password_confirm": PASSWORD, "role": "employee", "branches": [], **values},
        )


class GivingALoginTests(LoginBase):
    def test_a_login_is_a_new_account_linked_to_the_employee(self):
        member = self.give()
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.user, member.user)
        self.assertEqual((member.role, member.status), ("employee", "active"))
        self.assertTrue(member.user.check_password(PASSWORD))
        self.assertEqual(member.user.first_name, "Rina")
        event = AuditLog.objects.get(action="employee.login_created")
        self.assertNotIn(PASSWORD, str(event.after_data))

    def test_an_email_that_already_has_a_login_is_refused(self):
        with self.assertRaises(ValidationError) as caught:
            self.give(email="HR@acme.test")
        self.assertIn("email", caught.exception.message_dict)
        self.employee.refresh_from_db()
        self.assertIsNone(self.employee.user)

    def test_one_login_per_employee(self):
        self.give()
        with self.assertRaises(ValidationError):
            self.give(email="other@acme.test")

    def test_a_branch_manager_needs_branches(self):
        with self.assertRaises(ValidationError) as caught:
            self.give(role="manager")
        self.assertIn("branches", caught.exception.message_dict)
        member = self.give(role="manager", branches=[self.hq])
        with use_company(self.company):
            self.assertEqual(list(member.allowed_branches.all()), [self.hq])

    def test_the_password_is_checked(self):
        with self.assertRaises(ValidationError) as caught:
            self.give(password="short", password_confirm="short")
        self.assertIn("password", caught.exception.message_dict)
        with self.assertRaises(ValidationError) as caught:
            self.give(password_confirm="different-Pass-1")
        self.assertIn("password_confirm", caught.exception.message_dict)

    def test_only_the_owner_or_company_admin_gives_logins(self):
        with self.assertRaises(PermissionDenied):
            self.give(actor=self.hr)


class ChangingALoginTests(LoginBase):
    def setUp(self):
        super().setUp()
        self.member = self.give()

    def call(self, fn, **kwargs):
        return fn(actor=self.admin, company_id=self.company.pk, employee_id=self.employee.pk, **kwargs)

    def test_changing_the_access_to_branch_manager(self):
        self.call(employee_login.change_login_role, values={"role": "manager", "branches": [self.hq]})
        self.member.refresh_from_db()
        self.assertEqual(self.member.role, "manager")

    def test_resetting_the_password(self):
        new = "An0ther-pass-2026"
        self.call(employee_login.reset_login_password, values={"password": new, "password_confirm": new})
        self.member.user.refresh_from_db()
        self.assertTrue(self.member.user.check_password(new))

    def test_no_reset_for_a_person_who_belongs_to_another_company(self):
        other = onboard_company(code="OTHER", slug="other", name="Other Ltd")
        CompanyMembership.all_objects.create(
            company=other, user=self.member.user, role="employee",
            status=CompanyMembership.Status.ACTIVE,
        )
        with self.assertRaises(PermissionDenied):
            self.call(employee_login.reset_login_password,
                      values={"password": PASSWORD, "password_confirm": PASSWORD})

    def test_disabling_and_enabling(self):
        self.call(employee_login.set_login_active, active=False)
        self.member.refresh_from_db()
        self.assertEqual(self.member.status, "suspended")
        self.call(employee_login.set_login_active, active=True)
        self.member.refresh_from_db()
        self.assertEqual(self.member.status, "active")

    def test_the_company_administrators_login_is_not_changed_from_an_employee(self):
        with use_company(self.company):
            self.employee.user = self.admin
            self.employee.save()
        with self.assertRaises(PermissionDenied):
            self.call(employee_login.set_login_active, active=False)


class WhatALoginReachesTests(LoginBase):
    """The gate: an Employee or Branch manager login stays on its own pages."""

    def setUp(self):
        super().setUp()
        self.member = self.give()
        self.assertTrue(self.client.login(email="rina@acme.test", password=PASSWORD))

    def test_signing_in_lands_on_my_account(self):
        response = self.client.get(reverse("dashboard"))
        self.assertRedirects(response, reverse("me:home"))
        page = self.client.get(reverse("me:home"))
        self.assertContains(page, "Rina Akter")
        self.assertContains(page, "My account")
        self.assertNotContains(page, 'href="/salary/"')  # the company sidebar is not shown

    def test_company_pages_send_them_home(self):
        for name in (
            "payroll:payroll_home", "attendance:attendance_list", "leaves:leave_list",
            "scheduling:schedule_overview", "employee_list", "organization:branch_list",
            "payroll:salary_settings",
        ):
            with self.subTest(page=name):
                self.assertRedirects(self.client.get(reverse(name)), reverse("me:home"))

    def test_a_form_post_to_a_company_page_is_refused(self):
        response = self.client.post(reverse("payroll:payroll_generate"), {"year": 2026, "month": 8})
        self.assertEqual(response.status_code, 403)

    def test_a_branch_manager_is_kept_out_too_for_now(self):
        employee_login.change_login_role(
            actor=self.admin, company_id=self.company.pk, employee_id=self.employee.pk,
            values={"role": "manager", "branches": [self.hq]},
        )
        self.assertRedirects(self.client.get(reverse("payroll:payroll_home")), reverse("me:home"))
        self.assertContains(self.client.get(reverse("me:home")), "Branch manager of Head Office")

    def test_changing_their_own_password(self):
        new = "Mine-now-2026x"
        response = self.client.post(reverse("me:password"), {
            "old_password": PASSWORD, "new_password1": new, "new_password2": new,
        })
        self.assertRedirects(response, reverse("me:home"))
        self.client.logout()
        self.assertTrue(self.client.login(email="rina@acme.test", password=new))

    def test_a_disabled_login_reaches_no_company(self):
        employee_login.set_login_active(
            actor=self.admin, company_id=self.company.pk, employee_id=self.employee.pk, active=False,
        )
        page = self.client.get(reverse("me:home"))
        self.assertContains(page, "No active company")

    def test_the_administrator_is_not_affected(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("payroll:payroll_home")).status_code, 200)


class LoginCardTests(LoginBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        self.url = reverse("organization:employee_edit", args=[self.employee.pk])

    def test_creating_a_login_from_the_edit_page(self):
        self.assertContains(self.client.get(self.url), "Create login")
        response = self.client.post(self.url, {
            "section": "login_give", "login_email": "rina@acme.test",
            "login_password": PASSWORD, "login_password_confirm": PASSWORD,
            "login_role": "employee",
        })
        self.assertRedirects(response, self.url + "#login", fetch_redirect_response=False)
        page = self.client.get(self.url)
        self.assertContains(page, "Can sign in")
        self.assertContains(page, "Disable login")

    def test_a_refusal_shows_on_the_email_field(self):
        response = self.client.post(self.url, {
            "section": "login_give", "login_email": "hr@acme.test",
            "login_password": PASSWORD, "login_password_confirm": PASSWORD,
            "login_role": "employee",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["give_login"].errors["login_email"],
            ["This email already has a login. Use a different email for this employee."],
        )

    def test_disabling_from_the_edit_page(self):
        self.give()
        response = self.client.post(self.url, {"section": "login_disable"})
        self.assertEqual(response.status_code, 302)
        self.assertContains(self.client.get(self.url), "Enable login")
