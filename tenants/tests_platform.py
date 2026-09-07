"""Real PostgreSQL and HTTP checks for the root -> company-admin workflow."""
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, connection, transaction
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import CompanyMembership
from accounts.services import resolve_active_company_id
from common.tenant import use_company
from access_control.services import is_feature_enabled
from auditlog.models import AuditLog
from organization.models import Branch
from scheduling.models import CompanyAttendanceSettings
from tenants.models import Company, CompanyFeature, Feature
from tenants import platform_services as services

User = get_user_model()


class PlatformTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.root = User.objects.create_superuser(email="root@example.test", password="Root-test-786!R")
        cls.user = User.objects.create_user(email="admin@example.test", password="Admin-test-786!R")

    def setUp(self):
        self.client.force_login(self.root)
        self.values = dict(code="ACME", slug="acme", name="Acme Company", legal_name="", timezone="Asia/Dhaka", currency="BDT", country_code="BD", email="", phone="", address="")

    def create_company(self):
        return services.create_platform_company(actor=self.root, values=self.values)

    def url(self, action, company, *args):
        return reverse("platform:" + action, args=[company.public_id, *args])

    def test_root_without_membership_lands_on_platform(self):
        self.assertRedirects(self.client.get("/"), reverse("platform:company_list"))
        self.assertFalse(CompanyMembership.all_objects.filter(user=self.root).exists())

    def test_empty_company_list_and_datatable_count(self):
        self.assertContains(self.client.get(reverse("platform:company_list")), "Create company")
        data = self.client.get(reverse("platform:company_list"), {"format": "data", "draw": "<script>"}).json()
        self.assertEqual((data["recordsTotal"], data["draw"]), (0, 0))

    def test_cold_start_http_onboarding_and_admin_login(self):
        response = self.client.post(reverse("platform:company_create"), self.values)
        company = Company.objects.get(name="Acme Company")
        self.assertRedirects(response, self.url("company_detail", company))
        self.assertTrue(Branch.all_objects.get(company=company).is_default)
        self.assertEqual(CompanyAttendanceSettings.all_objects.filter(company=company).count(), 1)
        self.assertEqual(Feature.objects.count(), 3)
        response = self.client.post(self.url("administrator_create", company), {
            "account_mode": "new", "email": "new-admin@example.test", "first_name": "New", "last_name": "Admin",
            "password": "Different-Safe-Test-782!", "password_confirm": "Different-Safe-Test-782!", "role": "company_admin"})
        self.assertRedirects(response, self.url("company_detail", company))
        self.client.logout()
        self.assertTrue(self.client.login(email="new-admin@example.test", password="Different-Safe-Test-782!"))
        self.assertContains(self.client.get("/"), "Acme Company")
        self.assertEqual(self.client.get(reverse("platform:company_list")).status_code, 403)
        self.assertFalse(User.objects.get(email="new-admin@example.test").is_staff)
        self.assertFalse(CompanyMembership.all_objects.filter(user=self.root).exists())

    def grant_admin(self, company):
        member = services.grant_company_administrator(actor=self.root, company_id=company.pk,
            email="master@example.test", password="Admin-test-786!R")
        self.user = member.user
        return member

    def test_existing_account_cannot_be_reused_as_administrator(self):
        company = self.create_company()
        with self.assertRaises(ValidationError):
            services.grant_company_administrator(actor=self.root, company_id=company.pk,
                email=self.user.email, password="Another-safe-748!")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Admin-test-786!R"))

    def test_platform_routes_deny_normal_user_get_and_post(self):
        company = self.create_company()
        member = self.grant_admin(company)
        urls = [reverse("platform:company_list"), reverse("platform:company_create")]
        urls += [self.url(name, company) for name in ("company_detail", "company_edit", "company_status", "administrator_create", "company_feature")]
        urls.append(self.url("membership_edit", company, member.pk))
        self.client.force_login(self.user)
        for url in urls:
            for method in (self.client.get, self.client.post):
                with self.subTest(url=url, method=method.__name__):
                    self.assertEqual(method(url).status_code, 403)

    def test_service_authorization_cannot_be_bypassed(self):
        with self.assertRaises(PermissionDenied):
            services.create_platform_company(actor=self.user, values=self.values)
        self.assertFalse(Company.objects.exists())

    def test_inactive_root_rejected(self):
        self.root.is_active = False
        try:
            with self.assertRaises(PermissionDenied):
                services.create_platform_company(actor=self.root, values=self.values)
        finally:
            self.root.is_active = True

    def test_audit_failure_rolls_back_company(self):
        with patch("tenants.platform_services.record_platform_event", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.create_company()
        self.assertFalse(Company.objects.exists())
        self.assertFalse(Branch.all_objects.exists())

    def test_duplicate_names_get_distinct_generated_identifiers(self):
        company = self.create_company()
        second = self.create_company()
        self.assertEqual(company.slug, "acme-company")
        self.assertEqual(second.slug, "acme-company-1")
        self.assertEqual(int(second.code), int(company.code) + 1)
        self.assertGreaterEqual(int(company.code), 10001)

    def test_invalid_timezone_and_currency_rejected(self):
        for field, value in (("timezone", "Mars/Olympus"), ("currency", "12!")):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                services.create_platform_company(actor=self.root, values={**self.values, field: value})
        self.assertFalse(Company.objects.exists())

    def test_company_update_and_status_audit(self):
        company = self.create_company()
        response = self.client.post(self.url("company_edit", company), {**self.values, "name": "Acme Updated"})
        self.assertEqual(response.status_code, 302)
        event = AuditLog.objects.get(action="company.updated")
        self.assertEqual(event.before_data["name"], "Acme Company")
        self.assertEqual(event.after_data["name"], "Acme Updated")

    def test_suspension_revokes_existing_session_and_reactivation_restores(self):
        company = self.create_company()
        self.grant_admin(company)
        self.client.force_login(self.user)
        self.assertContains(self.client.get("/"), "Acme Company")
        services.change_company_status(actor=self.root, company_id=company.pk, status="suspended", reason="Review")
        self.assertIsNone(resolve_active_company_id(self.user, company.pk))
        self.assertContains(self.client.get("/"), "No active company access")
        services.change_company_status(actor=self.root, company_id=company.pk, status="active", reason="Resolved")
        self.assertContains(self.client.get("/"), "Acme Company")

    def test_no_root_membership_shortcut(self):
        company = self.create_company()
        with self.assertRaises(ValidationError):
            services.grant_company_administrator(actor=self.root, company_id=company.pk, email=self.root.email, password="Safe-test-9163!")

    def test_weak_password_rejected_and_not_audited(self):
        company = self.create_company()
        with self.assertRaises(ValidationError):
            services.grant_company_administrator(actor=self.root, company_id=company.pk, email="weak@example.test", password="123")
        self.assertFalse(User.objects.filter(email="weak@example.test").exists())
        self.assertEqual(AuditLog.objects.count(), 1)

    def test_membership_is_company_bound_and_revoke_works(self):
        company = self.create_company()
        member = self.grant_admin(company)
        other = Company.objects.create(name="Other", code="OTHER", slug="other")
        self.assertEqual(self.client.post(self.url("membership_edit", other, member.pk), {"email": self.user.email, "first_name": "", "last_name": "", "status": "ended"}).status_code, 404)
        self.client.post(self.url("membership_edit", company, member.pk), {"email": self.user.email, "first_name": "", "last_name": "", "status": "ended"})
        self.assertIsNone(resolve_active_company_id(self.user, company.pk))

    def test_feature_toggle_preserves_historical_answer_and_noop_retry(self):
        company = self.create_company()
        feature = Feature.objects.get(code="leave")
        at = timezone.now() - timedelta(days=1)
        with patch("tenants.platform_services.timezone.now", return_value=at):
            services.set_company_feature(actor=self.root, company_id=company.pk, feature_id=feature.pk, effect="enable", reason="Trial")
        services.set_company_feature(actor=self.root, company_id=company.pk, feature_id=feature.pk, effect="disable", reason="Ended")
        services.set_company_feature(actor=self.root, company_id=company.pk, feature_id=feature.pk, effect="disable", reason="Retry")
        self.assertTrue(is_feature_enabled(company, feature, at + timedelta(hours=1)))
        self.assertFalse(is_feature_enabled(company, feature))
        self.assertEqual(CompanyFeature.all_objects.count(), 2)
        self.assertEqual(AuditLog.objects.filter(action="company.feature_changed").count(), 2)

    def test_audit_has_no_password_and_is_immutable_in_database(self):
        company = self.create_company()
        services.grant_company_administrator(actor=self.root, company_id=company.pk, email="safe@example.test", password="Secret-testing-789!R")
        self.assertNotIn("Secret-testing", str(list(AuditLog.objects.values())))
        event = AuditLog.objects.first()
        with self.assertRaises(ValidationError):
            event.delete()
        with self.assertRaises(ValidationError):
            AuditLog.objects.filter(pk=event.pk).update(action="tampered")
        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("UPDATE auditlog_auditlog SET action = 'tampered' WHERE id = %s", [event.pk])

    def test_csrf_required_for_create(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.root)
        self.assertEqual(client.post(reverse("platform:company_create"), self.values).status_code, 403)

    def test_company_pages_deny_restricted_roles_and_scopes(self):
        company = self.create_company()
        member = self.grant_admin(company)
        self.client.force_login(self.user)
        member.allowed_branches.add(Branch.all_objects.get(company=company))
        self.assertEqual(self.client.get("/employees/").status_code, 403)
        with use_company(company):
            member.allowed_branches.clear()
        services.update_company_membership(actor=self.root, company_id=company.pk, membership_id=member.pk, role="employee", status="active")
        self.assertEqual(self.client.get("/employees/").status_code, 403)

    def test_datatable_search_pagination_and_bounds(self):
        Company.objects.bulk_create([Company(code=f"C{i:02d}", slug=f"c{i:02d}", name=f"Company {i:02d}") for i in range(35)])
        url = reverse("platform:company_list")
        data = self.client.get(url, {"format": "data", "start": 25, "length": 10}).json()
        self.assertEqual((data["recordsTotal"], len(data["data"])), (35, 10))
        filtered = self.client.get(url, {"format": "data", "search[value]": "Company 03"}).json()
        self.assertEqual(filtered["recordsFiltered"], 1)
        self.assertEqual(filtered["data"][0]["name"], "Company 03")
        bounded = self.client.get(url, {"format": "data", "start": "bad", "length": -1}).json()
        self.assertEqual(len(bounded["data"]), 1)

    def test_missing_status_reason_and_read_requests_do_not_write(self):
        company = self.create_company()
        before = AuditLog.objects.count()
        self.client.get(self.url("company_status", company))
        response = self.client.post(self.url("company_status", company), {"status": "suspended", "reason": ""})
        self.assertEqual(response.status_code, 200)
        company.refresh_from_db()
        self.assertEqual(company.status, "trial")
        self.assertEqual(AuditLog.objects.count(), before)

    def test_feature_future_schedule_rejects_current_toggle(self):
        company = self.create_company()
        feature = Feature.objects.get(code="leave")
        CompanyFeature.all_objects.create(company=company, feature=feature, effect="enable", starts_at=timezone.now() + timedelta(days=3))
        with self.assertRaises(ValidationError):
            services.set_company_feature(actor=self.root, company_id=company.pk, feature_id=feature.pk, effect="disable", reason="Cannot erase scheduled history")

    def test_signout_is_post_and_clears_session(self):
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)
        self.client.post(reverse("logout"))
        self.assertEqual(self.client.get(reverse("platform:company_list")).status_code, 302)
