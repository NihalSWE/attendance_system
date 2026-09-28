"""Create company on the root (Nihal, 2026-09-28): the company and its
administrator's login on one form; no legal name; a one-line password help;
an eye to show a password."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import CompanyMembership
from tenants import platform_services as services
from tenants.models import Company

User = get_user_model()
PASSWORD = "Different-Safe-Test-782!"


class CreateCase(TestCase):
    def setUp(self):
        self.root = User.objects.create_superuser(email="root@example.test",
                                                  password="Root-test-786!R")
        self.client.force_login(self.root)

    def post(self, **changes):
        data = {"name": "Acme Company", "email": "Owner@Acme.test", "phone": "01711000000",
                "address": "Dhaka", "password": PASSWORD, "password_confirm": PASSWORD,
                "status": "active", **changes}
        return self.client.post(reverse("platform:company_create"), data)


class CreateCompanyTests(CreateCase):
    def test_the_form_asks_for_exactly_this(self):
        form = self.client.get(reverse("platform:company_create")).context["form"]
        self.assertEqual(list(form.fields), ["name", "email", "phone", "address", "password",
                                             "password_confirm", "status"])
        self.assertNotIn("legal_name", form.fields)

    def test_company_and_administrator_in_one_step(self):
        response = self.post()
        company = Company.objects.get(name="Acme Company")
        self.assertRedirects(response, reverse("platform:company_detail",
                                               args=[company.public_id]))
        self.assertEqual((company.email, company.address, company.status),
                         ("owner@acme.test", "Dhaka", "active"))
        member = CompanyMembership.all_objects.get(company=company)
        self.assertEqual((member.role, member.status, member.user.email),
                         ("company_admin", "active", "owner@acme.test"))
        self.client.logout()
        self.assertTrue(self.client.login(email="owner@acme.test", password=PASSWORD))
        self.assertContains(self.client.get("/"), "Acme Company")

    def test_trial_by_default(self):
        self.post(status="trial")
        self.assertEqual(Company.objects.get(name="Acme Company").status, "trial")

    def test_nothing_is_made_when_something_is_wrong(self):
        User.objects.create_user(email="taken@acme.test", password=PASSWORD)
        for changes, said in (({"password_confirm": "Other-Safe-Test-783!"}, "do not match"),
                              ({"password": "12345678", "password_confirm": "12345678"},
                               "entirely numeric"),
                              ({"email": "taken@acme.test"}, "already belongs")):
            with self.subTest(said=said):
                page = self.post(**changes)
                self.assertContains(page, said)
                self.assertFalse(Company.objects.filter(name="Acme Company").exists())

    def test_a_status_other_than_trial_or_active_is_refused(self):
        page = self.post(status="suspended")
        self.assertEqual(page.status_code, 200)
        self.assertFalse(Company.objects.filter(name="Acme Company").exists())


class EditPagesTests(TestCase):
    def setUp(self):
        self.root = User.objects.create_superuser(email="root@example.test",
                                                  password="Root-test-786!R")
        self.client.force_login(self.root)
        self.company = services.create_company_with_administrator(actor=self.root, values={
            "name": "Acme Company", "email": "owner@acme.test", "phone": "", "address": "",
            "password": PASSWORD, "password_confirm": PASSWORD, "status": "trial"})
        self.member = CompanyMembership.all_objects.get(company=self.company)

    def test_edit_company_has_no_legal_name(self):
        page = self.client.get(reverse("platform:company_edit", args=[self.company.public_id]))
        self.assertNotIn("legal_name", page.context["form"].fields)

    def test_the_password_help_is_one_plain_line(self):
        page = self.client.get(reverse("platform:membership_edit",
                                       args=[self.company.public_id, self.member.pk]))
        self.assertContains(page, "Leave blank to keep the current password.")
        self.assertNotContains(page, "&lt;ul&gt;")
        self.assertNotContains(page, "&lt;li&gt;")
        self.assertContains(page, "password_toggle.js")

    def test_the_sign_in_page_has_the_eye(self):
        self.client.logout()
        self.assertContains(self.client.get(reverse("login")), "password_toggle.js")


class MobileNumberTests(CreateCase):
    """11-digit Bangladesh mobile numbers only (Nihal, 2026-09-28)."""

    def test_typed_any_usual_way_it_is_kept_one_way(self):
        for typed in ("01712345678", "+8801712345678", "8801712345678"):
            with self.subTest(typed=typed):
                self.post(phone=typed, email=f"{typed[-4:]}{len(typed)}@acme.test",
                          name=f"Acme {typed}")
                self.assertEqual(Company.objects.get(name=f"Acme {typed}").phone,
                                 "8801712345678")

    def test_anything_else_is_refused_and_nothing_is_made(self):
        for typed in ("0171234567", "017123456789", "01212345678", "0212345678"):
            with self.subTest(typed=typed):
                page = self.post(phone=typed)
                self.assertContains(page, "11-digit Bangladesh mobile number")
                self.assertFalse(Company.objects.filter(name="Acme Company").exists())

    def test_blank_is_allowed(self):
        self.post(phone="+88")
        self.assertEqual(Company.objects.get(name="Acme Company").phone, "")

    def test_edit_company_checks_it_too(self):
        self.post()
        company = Company.objects.get(name="Acme Company")
        page = self.client.post(reverse("platform:company_edit", args=[company.public_id]),
                                {"name": "Acme Company", "email": "owner@acme.test",
                                 "phone": "12345", "address": ""})
        self.assertContains(page, "11-digit Bangladesh mobile number")
