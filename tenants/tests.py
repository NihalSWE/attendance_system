"""Two-company tenant-isolation tests (Lesson 4 acceptance gate).

Exercises the scoped manager via CompanyMembership (a TenantOwned model). These
assert that acting as one company cannot see or fetch another company's rows,
that a missing tenant context fails loud, and that the unscoped manager still
sees everything for platform/root use.
"""

from django.test import TestCase

from accounts.models import CompanyMembership, User
from common.tenant import use_company
from tenants.models import Company


class TenantIsolationTests(TestCase):
    def setUp(self):
        self.company_a = Company.objects.create(code="A", slug="a", name="Company A")
        self.company_b = Company.objects.create(code="B", slug="b", name="Company B")
        self.user_a = User.objects.create_user(email="a@example.com", password="pw")
        self.user_b = User.objects.create_user(email="b@example.com", password="pw")
        with use_company(self.company_a):
            self.membership_a = CompanyMembership.objects.create(
                user=self.user_a, role="owner", status="active"
            )
        with use_company(self.company_b):
            self.membership_b = CompanyMembership.objects.create(
                user=self.user_b, role="owner", status="active"
            )

    def test_scoped_manager_returns_only_current_company(self):
        with use_company(self.company_a):
            self.assertEqual(list(CompanyMembership.objects.all()), [self.membership_a])
        with use_company(self.company_b):
            self.assertEqual(list(CompanyMembership.objects.all()), [self.membership_b])

    def test_cannot_fetch_other_company_row_by_id(self):
        # The classic isolation bug: get-by-id must not leak across tenants.
        with use_company(self.company_a):
            with self.assertRaises(CompanyMembership.DoesNotExist):
                CompanyMembership.objects.get(pk=self.membership_b.pk)

    def test_missing_tenant_context_fails_loud(self):
        with self.assertRaises(RuntimeError):
            list(CompanyMembership.objects.all())

    def test_unscoped_manager_sees_all_companies(self):
        # Platform/root + bootstrap path.
        self.assertEqual(CompanyMembership.all_objects.count(), 2)

    def test_company_is_stamped_from_context_on_create(self):
        user_c = User.objects.create_user(email="c@example.com", password="pw")
        with use_company(self.company_a):
            membership = CompanyMembership.objects.create(
                user=user_c, role="employee", status="active"
            )
        self.assertEqual(membership.company_id, self.company_a.pk)
