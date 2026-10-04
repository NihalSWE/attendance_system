"""Phase 0: the foundation every endpoint stands on (docs/api/00-PLAN.md)."""

import datetime

from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import CompanyMembership
from api.core.company import resolve_company
from api.core.errors import ApiError
from api.models import IdempotencyRecord
from api.tests import urls as test_urls
from tenants.services import onboard_company

User = get_user_model()


class PingTests(TestCase):
    def setUp(self):
        caches["api"].clear()

    def test_ping_answers_without_a_login(self):
        response = APIClient().get("/api/v1/ping")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual((data["status"], data["version"]), ("ok", "v1"))
        moment = datetime.datetime.fromisoformat(data["server_time"].replace("Z", "+00:00"))
        self.assertLess(abs((timezone.now() - moment).total_seconds()), 5)
        self.assertTrue(response["X-Request-Id"])

    def test_a_wrong_method_is_one_error_shape(self):
        response = APIClient().post("/api/v1/ping", {}, format="json")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.json()["error"]["code"], "method_not_allowed")

    @override_settings(REST_FRAMEWORK={
        "DEFAULT_PERMISSION_CLASSES": ["api.core.permissions.DenyAll"],
        "DEFAULT_THROTTLE_CLASSES": ["api.core.throttling.ApiRateThrottle"],
        "DEFAULT_THROTTLE_RATES": {"public": "2/minute", "read": "300/minute",
                                   "write": "120/minute"},
        "EXCEPTION_HANDLER": "api.core.errors.exception_handler",
        "DEFAULT_AUTHENTICATION_CLASSES": [], "UNAUTHENTICATED_USER": None,
        "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    })
    def test_rate_limit_answers_429_with_retry_after(self):
        client = APIClient()
        self.assertEqual(client.get("/api/v1/ping").status_code, 200)
        self.assertEqual(client.get("/api/v1/ping").status_code, 200)
        response = client.get("/api/v1/ping")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["error"]["code"], "rate_limited")
        self.assertTrue(int(response["Retry-After"]) > 0)


@override_settings(ROOT_URLCONF="api.tests.urls")
class ErrorShapeTests(TestCase):
    def setUp(self):
        caches["api"].clear()
        self.client = APIClient()

    def error(self, response):
        body = response.json()["error"]
        self.assertEqual(body["reference"], response["X-Request-Id"])
        return body

    def test_invalid_input_names_the_field(self):
        response = self.client.post("/api-test/echo", {}, format="json")
        self.assertEqual(response.status_code, 422)
        body = self.error(response)
        self.assertEqual(body["code"], "validation_error")
        self.assertEqual(body["fields"], {"name": ["This field is required."]})

    def test_an_unknown_field_is_refused_not_ignored(self):
        response = self.client.post("/api-test/echo", {"name": "Rahim", "nmae": "x"},
                                    format="json")
        self.assertEqual(response.status_code, 422)
        body = self.error(response)
        self.assertEqual(body["code"], "unknown_field")
        self.assertIn("nmae", body["fields"])

    def test_bad_json_is_a_bad_request(self):
        response = self.client.post("/api-test/echo", '{"name": ', content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.error(response)["code"], "bad_request")

    def test_not_json_is_unsupported(self):
        response = self.client.post("/api-test/echo", "name=x", content_type="text/plain")
        self.assertEqual(response.status_code, 415)
        self.assertEqual(self.error(response)["code"], "unsupported_media_type")

    def test_a_service_refusal_keeps_the_panels_words(self):
        response = self.client.post("/api-test/refuses", {}, format="json")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.error(response)["fields"],
                         {"email": ["Another company already uses this email."]})

    def test_a_conflict(self):
        response = self.client.post("/api-test/conflict", {}, format="json")
        self.assertEqual(response.status_code, 409)
        body = self.error(response)
        self.assertEqual((body["code"], body["message"]),
                         ("conflict", "Salary up to 31 Aug 2026 is already finalised."))

    def test_a_server_error_shows_nothing_internal(self):
        with self.assertLogs("api", "ERROR"):
            response = self.client.get("/api-test/broken")
        self.assertEqual(response.status_code, 500)
        body = self.error(response)
        self.assertEqual(body["code"], "server_error")
        self.assertNotIn("secret internal detail", response.content.decode())

    def test_an_endpoint_that_names_no_permission_is_closed(self):
        response = self.client.get("/api-test/denied")
        self.assertEqual(response.status_code, 401)            # not logged in
        self.assertEqual(self.error(response)["code"], "not_authenticated")
        self.client.force_authenticate(User.objects.create_user(email="d@example.test",
                                                                password="pw-12345678"))
        response = self.client.get("/api-test/denied")
        self.assertEqual(response.status_code, 403)            # logged in, still closed
        self.assertEqual(self.error(response)["code"], "permission_denied")

    def test_an_unknown_api_address_is_not_found_html_free(self):
        response = self.client.get("/api/v1/no-such-thing")
        self.assertEqual(response.status_code, 404)


@override_settings(ROOT_URLCONF="api.tests.urls")
class IdempotencyTests(TestCase):
    def setUp(self):
        caches["api"].clear()
        test_urls.COUNTER["made"] = 0
        self.client = APIClient()
        self.user = User.objects.create_user(email="app@example.test", password="pw-12345678")
        self.client.force_authenticate(self.user)

    def test_the_same_key_returns_the_first_answer_and_does_nothing_twice(self):
        first = self.client.post("/api-test/make", {"a": 1}, format="json",
                                 HTTP_IDEMPOTENCY_KEY="k-1")
        again = self.client.post("/api-test/make", {"a": 1}, format="json",
                                 HTTP_IDEMPOTENCY_KEY="k-1")
        self.assertEqual((first.status_code, again.status_code), (201, 201))
        self.assertEqual(first.json(), again.json())
        self.assertEqual(again["Idempotent-Replay"], "true")
        self.assertEqual(test_urls.COUNTER["made"], 1)

    def test_a_key_reused_for_another_request_is_refused(self):
        self.client.post("/api-test/make", {"a": 1}, format="json", HTTP_IDEMPOTENCY_KEY="k-2")
        response = self.client.post("/api-test/make", {"a": 2}, format="json",
                                    HTTP_IDEMPOTENCY_KEY="k-2")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "idempotency_key_reused")

    def test_without_a_key_or_after_24_hours_it_runs_again(self):
        self.client.post("/api-test/make", {}, format="json")
        self.client.post("/api-test/make", {}, format="json")
        self.assertEqual(test_urls.COUNTER["made"], 2)
        self.client.post("/api-test/make", {}, format="json", HTTP_IDEMPOTENCY_KEY="k-3")
        IdempotencyRecord.objects.update(created_at=timezone.now() - datetime.timedelta(hours=25))
        self.client.post("/api-test/make", {}, format="json", HTTP_IDEMPOTENCY_KEY="k-3")
        self.assertEqual(test_urls.COUNTER["made"], 4)

    def test_keys_are_per_caller(self):
        self.client.post("/api-test/make", {}, format="json", HTTP_IDEMPOTENCY_KEY="k-4")
        other = APIClient()
        other.force_authenticate(User.objects.create_user(email="b@example.test",
                                                          password="pw-12345678"))
        other.post("/api-test/make", {}, format="json", HTTP_IDEMPOTENCY_KEY="k-4")
        self.assertEqual(test_urls.COUNTER["made"], 2)


class CompanyHeaderTests(TestCase):
    """Which company a request acts for (wired to logins in phase 1)."""

    def setUp(self):
        self.one = onboard_company(code="ONE", slug="one", name="One Ltd")
        self.two = onboard_company(code="TWO", slug="two", name="Two Ltd")
        self.other = onboard_company(code="OTH", slug="oth", name="Other Ltd")
        self.user = User.objects.create_user(email="m@example.test", password="pw-12345678")

    def member(self, company):
        CompanyMembership.all_objects.create(company=company, user=self.user,
                                             role="manager", status="active")

    def request(self, header=None):
        from django.test import RequestFactory

        extra = {"HTTP_X_COMPANY": str(header)} if header is not None else {}
        return RequestFactory().get("/api/v1/x", **extra)

    def test_one_company_needs_no_header(self):
        self.member(self.one)
        self.assertEqual(resolve_company(self.request(), self.user), self.one.pk)

    def test_several_companies_need_the_header(self):
        self.member(self.one)
        self.member(self.two)
        with self.assertRaises(ApiError) as caught:
            resolve_company(self.request(), self.user)
        self.assertEqual(caught.exception.spec.code, "validation_error")
        self.assertEqual(resolve_company(self.request(self.two.pk), self.user), self.two.pk)

    def test_a_company_they_do_not_belong_to_is_not_found(self):
        self.member(self.one)
        with self.assertRaises(ApiError) as caught:
            resolve_company(self.request(self.other.pk), self.user)
        self.assertEqual(caught.exception.spec.code, "not_found")


class DocumentationSiteTests(TestCase):
    def test_the_pages_render(self):
        client = self.client
        for url, words in (
            ("/api/docs/", "Getting started"),
            ("/api/docs/conventions/", "Idempotency-Key"),
            ("/api/docs/changelog/", "Phase 0"),
            ("/api/docs/errors/", "idempotency_key_reused"),
            ("/api/docs/rate-limits/", "60/minute"),
            ("/api/docs/endpoint/ping/", "server_time"),
            ("/api/swagger/", "swagger"),
        ):
            with self.subTest(url=url):
                response = client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, words)

    def test_the_endpoint_page_has_every_part(self):
        page = self.client.get("/api/docs/endpoint/ping/").content.decode()
        for part in ("What it does", "Request parameters", "Request example",
                     "Response fields", "Response example", "Errors this endpoint can return",
                     "/api/v1/ping", "cURL", "Python", "Kotlin", "Swift", "Dart",
                     "rate_limited", "Fix:", "60/minute"):
            with self.subTest(part=part):
                self.assertIn(part, page)

    def test_the_schema_is_served(self):
        response = self.client.get("/api/v1/schema/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"/api/v1/ping", response.content)

    def test_an_unknown_endpoint_page_is_404(self):
        self.assertEqual(self.client.get("/api/docs/endpoint/nope/").status_code, 404)

    def test_an_employee_logged_into_the_panel_can_read_the_docs(self):
        # The panels' gate keeps employees on their own pages - but not /api/.
        company = onboard_company(code="EMP", slug="emp", name="Emp Ltd")
        user = User.objects.create_user(email="e@example.test", password="pw-12345678")
        CompanyMembership.all_objects.create(company=company, user=user, role="employee",
                                             status="active")
        self.client.force_login(user)
        self.assertEqual(self.client.get("/api/docs/").status_code, 200)


@override_settings(CORS_ALLOWED_ORIGINS=["https://app.example.com"])
class CorsTests(TestCase):
    def test_only_listed_frontends_may_call_from_a_browser(self):
        allowed = self.client.get("/api/v1/ping", HTTP_ORIGIN="https://app.example.com")
        self.assertEqual(allowed["Access-Control-Allow-Origin"], "https://app.example.com")
        other = self.client.get("/api/v1/ping", HTTP_ORIGIN="https://evil.example.com")
        self.assertFalse(other.has_header("Access-Control-Allow-Origin"))
