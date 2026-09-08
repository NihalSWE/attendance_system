from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.contrib.auth import get_user_model
from django.db import IntegrityError, close_old_connections, transaction
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.core.exceptions import ValidationError

from accounts.models import CompanyMembership
from common.forms import normalize_bd_phone
from common.tenant import get_current_company_id
from scheduling.models import CompanyAttendanceSettings, Shift
from tenants.forms import CompanyForm, AdministratorForm, CompanyFeatureForm, CompanyStatusForm
from tenants.models import Company
from tenants import platform_services as services

User = get_user_model()


class PlatformCorrectionsTests(TestCase):
    def setUp(self):
        self.root = User.objects.create_superuser(email="root@corrections.test", password="Root-52947!")
        self.client.force_login(self.root)
        self.company = services.create_platform_company(actor=self.root, values={"name": "Felna Tech"})

    def test_company_route_is_explicit(self):
        self.assertEqual(reverse("platform:company_list"), "/platform/companies/")
        self.assertRedirects(self.client.get("/platform/"), "/platform/companies/")

    def test_generated_identifiers_defaults_and_forged_post(self):
        self.assertEqual((self.company.currency, self.company.country_code, self.company.timezone), ("BDT", "BD", "Asia/Dhaka"))
        self.assertNotIn("code", CompanyForm().fields)
        self.assertNotIn("slug", CompanyForm().fields)
        self.assertEqual(CompanyForm().fields["address"].widget.input_type, "text")
        form = CompanyForm({"name": "Renamed", "code": "FORGED", "slug": "forged", "currency": "USD"}, instance=self.company)
        self.assertTrue(form.is_valid(), form.errors)
        services.update_platform_company(actor=self.root, company_id=self.company.pk, values=form.cleaned_data)
        self.company.refresh_from_db()
        self.assertEqual(self.company.slug, "felna-tech")
        self.assertEqual(self.company.currency, "BDT")
        with self.assertRaises(ValidationError):
            services.update_platform_company(actor=self.root, company_id=self.company.pk, values={"code": "forged"})

    def test_slug_suffixes_and_long_names(self):
        second = services.create_platform_company(actor=self.root, values={"name": "Felna Tech"})
        third = services.create_platform_company(actor=self.root, values={"name": "Felna Tech"})
        self.assertEqual((second.slug, third.slug), ("felna-tech-1", "felna-tech-2"))
        a = Company.objects.create(name="A" * 255)
        b = Company.objects.create(name="A" * 255)
        self.assertEqual(len(b.slug), 64)
        self.assertTrue(b.slug.endswith("-1"))
        self.assertNotEqual(a.code, b.code)

    def test_settings_admin_get_and_post_are_scoped(self):
        settings = CompanyAttendanceSettings.all_objects.get(company=self.company)
        other = Company.objects.create(name="Other")
        shift = Shift.all_objects.create(company=other, code="X", name="Other shift", start_time="09:00", end_time="17:00", scheduled_minutes=480)
        url = reverse("admin:scheduling_companyattendancesettings_change", args=[settings.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Other shift")
        form = response.context["adminform"].form
        self.assertFalse(form.fields["company_shift"].queryset.filter(pk=shift.pk).exists())
        self.assertIsNone(get_current_company_id())
        from django.forms.models import model_to_dict
        values = model_to_dict(settings)
        values = {key: value if value is not None else "" for key, value in values.items()}
        values["effective_from_0"] = settings.effective_from.date().isoformat()
        values["effective_from_1"] = settings.effective_from.strftime("%H:%M:%S")
        values["duplicate_punch_window_seconds"] = 45
        response = self.client.post(url, values)
        self.assertEqual(response.status_code, 302, getattr(response, "context", None))
        settings.refresh_from_db()
        self.assertEqual(settings.duplicate_punch_window_seconds, 45)
        response = self.client.post(url, {"shift_mode": "company_single_shift", "company_shift": shift.pk})
        self.assertEqual(response.status_code, 200)
        self.assertIn("company_shift", response.context["adminform"].form.errors)
        self.assertIsNone(get_current_company_id())

    def test_one_master_admin_and_edit_password(self):
        member = services.grant_company_administrator(actor=self.root, company_id=self.company.pk, email="master@felna.test", password="Master-safe-592!")
        self.assertEqual(member.role, "company_admin")
        with self.assertRaises(ValidationError):
            services.grant_company_administrator(actor=self.root, company_id=self.company.pk, email="second@felna.test", password="Second-safe-592!")
        self.assertFalse(User.objects.filter(email="second@felna.test").exists())
        user2 = User.objects.create_user(email="second@felna.test")
        with self.assertRaises(IntegrityError), transaction.atomic():
            CompanyMembership.all_objects.create(company=self.company, user=user2, role="owner", status="active")
        other = Company.objects.create(name="Another")
        with self.assertRaises(IntegrityError), transaction.atomic():
            CompanyMembership.all_objects.create(company=other, user=member.user, role="company_admin", status="active")
        url = reverse("platform:membership_edit", args=[self.company.public_id, member.pk])
        response = self.client.post(url, {"email": "updated@felna.test", "first_name": "Master", "last_name": "Account", "status": "active", "password": "New-master-safe-529!", "password_confirm": "New-master-safe-529!", "role": "employee"})
        self.assertEqual(response.status_code, 302)
        member.refresh_from_db()
        self.assertEqual(member.role, "company_admin")
        self.assertTrue(self.client.login(email="updated@felna.test", password="New-master-safe-529!"))

    def test_control_types_and_phone_normalization(self):
        self.assertNotIn("role", AdministratorForm().fields)
        self.assertNotIn("user", AdministratorForm().fields)
        # Every select is a real Select2 - database-backed and fixed choice lists
        # alike. A native select can style its closed box, but its open dropdown
        # is drawn by the operating system. Supersedes the earlier rule that kept
        # fixed choices native.
        self.assertIn("js-select2", CompanyFeatureForm().fields["feature"].widget.attrs["class"])
        self.assertIn("js-select2", CompanyStatusForm().fields["status"].widget.attrs["class"])
        # `.select` remains as the no-JavaScript fallback.
        self.assertIn("select", CompanyStatusForm().fields["status"].widget.attrs["class"])
        self.assertEqual(normalize_bd_phone("01712345678"), "8801712345678")
        self.assertEqual(normalize_bd_phone("+8801712345678"), "8801712345678")
        self.assertEqual(normalize_bd_phone("+88"), "")
        with self.assertRaises(ValidationError):
            normalize_bd_phone("017abc12345678")
        form = CompanyForm({"name": "Bad phone", "phone": "not-a-number"})
        self.assertFalse(form.is_valid())
        self.assertIn("phone", form.errors)


class ConcurrentCompanyIdentifiersTests(TransactionTestCase):
    def test_simultaneous_same_name_creates_distinct_identifiers(self):
        root = User.objects.create_superuser(email="concurrent@root.test", password="Root-safe-921!")
        barrier = Barrier(2)

        def create():
            close_old_connections()
            try:
                actor = User.objects.get(pk=root.pk)
                barrier.wait(timeout=10)
                company = services.create_platform_company(actor=actor, values={"name": "Concurrent Company"})
                return company.code, company.slug
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(create) for _ in range(2)]
            results = [future.result(timeout=30) for future in futures]
        self.assertEqual(len({code for code, slug in results}), 2)
        self.assertEqual({slug for code, slug in results}, {"concurrent-company", "concurrent-company-1"})
