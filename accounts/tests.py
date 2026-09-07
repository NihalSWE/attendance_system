"""Tenant middleware and membership-resolution tests."""

from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from accounts.models import CompanyMembership, User
from accounts.services import ACTIVE_COMPANY_SESSION_KEY, resolve_active_company_id
from common.middleware import TenantMiddleware
from common.tenant import get_current_company_id, use_company
from tenants.models import Company


class TenantMiddlewareTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.company_a = Company.objects.create(code="A", slug="a", name="Company A")
        self.company_b = Company.objects.create(code="B", slug="b", name="Company B")
        self.user = User.objects.create_user(email="member@example.com", password="pw")
        with use_company(self.company_a):
            CompanyMembership.objects.create(
                user=self.user, role="owner", status="active"
            )

    def _run_request(self, user, session=None):
        """Run one request through the middleware; return the context seen inside."""
        request = self.factory.get("/")
        request.user = user
        request.session = session if session is not None else {}
        seen = {}

        def get_response(req):
            seen["company_id"] = get_current_company_id()
            seen["request_company_id"] = req.company_id
            return HttpResponse()

        TenantMiddleware(get_response)(request)
        return seen

    def test_sets_tenant_context_for_a_member(self):
        seen = self._run_request(self.user)
        self.assertEqual(seen["company_id"], self.company_a.pk)
        self.assertEqual(seen["request_company_id"], self.company_a.pk)

    def test_anonymous_request_gets_no_tenant_context(self):
        seen = self._run_request(AnonymousUser())
        self.assertIsNone(seen["company_id"])

    def test_context_is_cleared_after_the_response(self):
        # A leaked context would expose this tenant to the next request on the
        # same worker thread.
        self._run_request(self.user)
        self.assertIsNone(get_current_company_id())

    def test_context_cleared_even_when_the_view_raises(self):
        request = self.factory.get("/")
        request.user = self.user
        request.session = {}

        def boom(req):
            raise RuntimeError("view exploded")

        with self.assertRaises(RuntimeError):
            TenantMiddleware(boom)(request)
        self.assertIsNone(get_current_company_id())

    def test_cannot_select_a_company_without_membership(self):
        # User asks for Company B but only belongs to A: must never get B.
        seen = self._run_request(
            self.user, session={ACTIVE_COMPANY_SESSION_KEY: self.company_b.pk}
        )
        self.assertEqual(seen["company_id"], self.company_a.pk)
        self.assertNotEqual(seen["company_id"], self.company_b.pk)

    def test_session_selection_honoured_when_the_user_is_a_member(self):
        with use_company(self.company_b):
            CompanyMembership.objects.create(
                user=self.user, role="hr", status="active"
            )
        seen = self._run_request(
            self.user, session={ACTIVE_COMPANY_SESSION_KEY: self.company_b.pk}
        )
        self.assertEqual(seen["company_id"], self.company_b.pk)

    def test_non_active_membership_grants_no_context(self):
        invitee = User.objects.create_user(email="invited@example.com", password="pw")
        with use_company(self.company_a):
            CompanyMembership.objects.create(
                user=invitee, role="employee", status="invited"
            )
        seen = self._run_request(invitee)
        self.assertIsNone(seen["company_id"])

    def test_scoped_queries_work_inside_a_request(self):
        # The point of the middleware: ordinary .objects queries are scoped.
        request = self.factory.get("/")
        request.user = self.user
        request.session = {}
        seen = {}

        def get_response(req):
            seen["count"] = CompanyMembership.objects.count()
            return HttpResponse()

        TenantMiddleware(get_response)(request)
        self.assertEqual(seen["count"], 1)


class MembershipResolutionTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(code="A", slug="a", name="Company A")
        self.user = User.objects.create_user(email="u@example.com", password="pw")

    def test_user_with_no_membership_resolves_to_none(self):
        self.assertIsNone(resolve_active_company_id(self.user))

    def test_anonymous_resolves_to_none(self):
        self.assertIsNone(resolve_active_company_id(AnonymousUser()))
