"""The Developer's API link (2026-10-06): always on the platform owner's
sidebar; on a company's sidebar - for its owner and administrator - once the
platform owner enables the "Developer's API" module for that company."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import CompanyMembership
from tenants import platform_services as services
from tenants.models import Feature

User = get_user_model()
LINK = 'aria-label="Developer\'s API (opens in a new tab)"'


class DeveloperApiLinkTests(TestCase):
    def setUp(self):
        self.root = User.objects.create_superuser(email="root@devapi.test", password="Root-52947!")
        self.company = services.create_platform_company(actor=self.root,
                                                        values={"name": "Devapi Ltd"})
        self.feature = Feature.objects.get(code="developer_api")

    def member(self, email, role):
        user = User.objects.create_user(email=email, password="Pw-52947!x")
        CompanyMembership.all_objects.create(company=self.company, user=user, role=role,
                                             status="active")
        return user

    def page_for(self, user):
        self.client.force_login(user)
        # The Employees page: every company login with a sidebar may open it
        # (an Employee login is sent on to My account, which has its own).
        return self.client.get(reverse("employee_list"), follow=True)

    def switch(self, effect):
        services.set_company_feature(actor=self.root, company_id=self.company.pk,
                                     feature_id=self.feature.pk, effect=effect,
                                     reason="Developers")

    def test_the_platform_owner_always_has_it(self):
        self.client.force_login(self.root)
        page = self.client.get(reverse("platform:company_list"))
        self.assertContains(page, LINK)
        self.assertContains(page, f'href="{reverse("api:docs")}" target="_blank" rel="noopener"')

    def test_the_module_is_offered_on_the_feature_page(self):
        self.client.force_login(self.root)
        page = self.client.get(reverse("platform:company_detail", args=[self.company.public_id]))
        self.assertContains(page, "Developer&#x27;s API")
        form = self.client.get(reverse("platform:company_feature",
                                       args=[self.company.public_id]))
        self.assertContains(form, "Developer&#x27;s API")

    def test_a_company_sees_it_only_once_enabled(self):
        admin = self.member("admin@devapi.test", "company_admin")
        self.assertNotContains(self.page_for(admin), LINK)
        self.switch("enable")
        page = self.page_for(admin)
        self.assertContains(page, LINK)
        self.assertContains(page, f'href="{reverse("api:docs")}" target="_blank"')
        self.switch("disable")
        self.assertNotContains(self.page_for(admin), LINK)

    def test_only_the_owner_and_administrator(self):
        self.switch("enable")
        owner = self.member("owner@devapi.test", "owner")
        self.assertContains(self.page_for(owner), LINK)
        for email, role in (("hr@devapi.test", "hr"), ("staff@devapi.test", "employee")):
            with self.subTest(role=role):
                self.assertNotContains(self.page_for(self.member(email, role)), LINK)

    def test_another_companys_switch_does_not_count(self):
        other = services.create_platform_company(actor=self.root, values={"name": "Other Ltd"})
        services.set_company_feature(actor=self.root, company_id=other.pk,
                                     feature_id=self.feature.pk, effect="enable", reason="x")
        admin = self.member("admin@devapi.test", "company_admin")
        self.assertNotContains(self.page_for(admin), LINK)
