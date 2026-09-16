"""Company profile: the fields, the logo, and where the logo is shown."""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import CompanyMembership, User
from organization.company_profile import CompanyProfileForm, save_profile
from tenants.models import Company
from tenants.services import onboard_company

PNG = SimpleUploadedFile("logo.png", b"\x89PNG\r\n\x1a\n" + b"0" * 40, content_type="image/png")


class CompanyProfileTests(TestCase):
    def setUp(self):
        self.company = onboard_company(code="PRO", slug="pro", name="Profile Ltd")
        self.admin = User.objects.create_user(email="admin@pro.test")
        CompanyMembership.all_objects.create(
            company=self.company, user=self.admin, role="company_admin", status="active")
        self.other = User.objects.create_user(email="staff@pro.test")
        CompanyMembership.all_objects.create(
            company=self.company, user=self.other, role="hr", status="active")
        self.url = reverse("organization:company_profile")

    def post(self, **changes):
        data = {"name": "Profile Ltd", "legal_name": "", "contact_person": "Ajay Ghosh",
                "email": "hello@pro.test", "phone": "", "address": "Dhaka"}
        data.update(changes)
        return self.client.post(self.url, data)

    def test_the_company_admin_saves_the_details(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(self.url), "Company profile")
        self.assertRedirects(self.post(), self.url)
        company = Company.objects.get(pk=self.company.pk)
        self.assertEqual((company.contact_person, company.address), ("Ajay Ghosh", "Dhaka"))

    def test_a_name_is_required(self):
        self.client.force_login(self.admin)
        self.assertContains(self.post(name=""), "required")

    def test_only_the_company_administrator_may_open_it(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_the_logo_must_be_an_image_and_small_enough(self):
        self.client.force_login(self.admin)
        bad = SimpleUploadedFile("logo.exe", b"nope", content_type="application/octet-stream")
        self.assertContains(self.client.post(self.url, {
            "name": "Profile Ltd", "logo": bad}), "PNG, JPG")
        big = SimpleUploadedFile("logo.png", b"0" * (2 * 1024 * 1024 + 1), content_type="image/png")
        self.assertContains(self.client.post(self.url, {
            "name": "Profile Ltd", "logo": big}), "larger than 2 MB")

    def test_the_sidebar_shows_the_default_logo_until_one_is_uploaded(self):
        self.client.force_login(self.admin)
        page = self.client.get(self.url)
        self.assertContains(page, "base_template/img/logo.png")
        self.assertContains(page, "IGL Web Ltd.")
        self.assertContains(page, "https://iglweb.com/web/")

    def test_an_uploaded_logo_replaces_it_everywhere(self):
        with override_settings(MEDIA_ROOT="/tmp/qa-logos"):
            form = CompanyProfileForm({"name": "Profile Ltd"}, {"logo": PNG},
                                      instance=Company.objects.get(pk=self.company.pk))
            self.assertTrue(form.is_valid(), form.errors)
            save_profile(actor=self.admin, company_id=self.company.pk, form=form)
            self.client.force_login(self.admin)
            page = self.client.get(self.url)
        self.assertContains(page, "company_logos/")
        self.assertNotContains(page, "base_template/img/logo.png")


class PlatformBrandTests(TestCase):
    """The platform operator's own screens are not a company (Ajay, 2026-09-16)."""

    def test_the_root_login_sees_the_default_logo(self):
        root = User.objects.create_superuser(email="root@platform.test", password="pw-12345678")
        self.client.force_login(root)
        page = self.client.get(reverse("platform:company_list"))
        self.assertContains(page, "base_template/img/logo.png")
        self.assertNotContains(page, "company_logos/")
