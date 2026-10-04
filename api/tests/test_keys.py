"""Phase 1: API keys for machines (docs/api/00-PLAN.md, 2.4)."""

import datetime
import json

import pyotp
from django.test import override_settings
from django.utils import timezone

from accounts.models import CompanyMembership
from api.models import ApiKey
from api.tests.test_auth import ApiTestCase, signature_headers
from auditlog.models import AuditLog
from tenants.services import onboard_company

PROBE = "/api-test/key-probe"


@override_settings(ROOT_URLCONF="api.tests.urls")
class KeyTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.person("owner@example.test", role="owner")
        secret = self.turn_on_two_step(self.owner)
        challenge = self.login("owner@example.test").json()["challenge"]
        self.admin = self.call("POST", "/api/v1/auth/login/two-step",
                               {"challenge": challenge, "code": pyotp.TOTP(secret).now()}).json()

    def create(self, **values):
        body = {"name": "IGL ERP", "scopes": ["employees:read"], **values}
        response = self.call("POST", "/api/v1/api-keys", body, session=self.admin)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def machine(self, key, secret=None, method="GET", body="", **extra):
        headers = signature_headers(secret or key["secret"], key["id"], method, PROBE, body=body)
        return self.client.generic(method, PROBE, body, content_type="application/json",
                                   **headers, **extra)

    def test_a_key_works_for_its_company_as_its_creator(self):
        key = self.create()
        self.assertTrue(key["secret"].startswith("sk_"))
        response = self.machine(key)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"company_id": self.company.pk,
                                           "user": "owner@example.test"})
        body = json.dumps({"a": 1})
        self.assertEqual(self.machine(key, method="POST", body=body).json(), {"got": {"a": 1}})
        stored = ApiKey.objects.get(public_id=key["id"])
        self.assertNotIn(key["secret"], stored.secret_encrypted)
        self.assertIsNotNone(stored.last_used_at)
        self.assertTrue(AuditLog.objects.filter(action="api.key_created").exists())

    def test_the_secret_is_never_shown_again(self):
        key = self.create()
        shown = self.call("GET", f"/api/v1/api-keys/{key['id']}", session=self.admin).json()
        self.assertNotIn("secret", shown)
        listed = self.call("GET", "/api/v1/api-keys", session=self.admin).json()["results"]
        self.assertEqual([row["id"] for row in listed], [key["id"]])
        self.assertNotIn("secret", listed[0])

    def test_a_missing_scope_is_refused(self):
        key = self.create(scopes=["attendance:read"])
        response = self.machine(key)
        self.assertEqual((response.status_code, self.code_of(response)), (403, "scope_missing"))

    def test_unknown_scopes_and_bad_addresses_are_refused(self):
        bad_scope = self.call("POST", "/api/v1/api-keys",
                              {"name": "x", "scopes": ["everything"]}, session=self.admin)
        self.assertEqual(bad_scope.status_code, 422)
        bad_ip = self.call("POST", "/api/v1/api-keys",
                           {"name": "x", "scopes": ["employees:read"], "allowed_ips": ["1.2.3"]},
                           session=self.admin)
        self.assertIn("allowed_ips", bad_ip.json()["error"]["fields"])

    def test_the_ip_allow_list(self):
        key = self.create(allowed_ips=["203.0.113.7"])
        self.assertEqual(self.code_of(self.machine(key)), "ip_not_allowed")
        self.assertEqual(self.machine(key, REMOTE_ADDR="203.0.113.7").status_code, 200)

    @override_settings(API_PROXY_COUNT=1)
    def test_behind_one_proxy_the_forwarded_address_counts(self):
        key = self.create(allowed_ips=["203.0.113.7"])
        ok = self.machine(key, HTTP_X_FORWARDED_FOR="203.0.113.7")
        self.assertEqual(ok.status_code, 200, ok.content)
        spoofed = self.machine(key, HTTP_X_FORWARDED_FOR="203.0.113.7, 198.51.100.1")
        self.assertEqual(self.code_of(spoofed), "ip_not_allowed")

    def test_an_expired_key_stops(self):
        key = self.create()
        ApiKey.objects.filter(public_id=key["id"]).update(
            expires_at=timezone.now() - datetime.timedelta(seconds=1))
        self.assertEqual(self.code_of(self.machine(key)), "api_key_expired")

    def test_a_revoked_key_stops_at_once(self):
        key = self.create()
        revoked = self.call("DELETE", f"/api/v1/api-keys/{key['id']}", session=self.admin)
        self.assertTrue(revoked.json()["revoked"])
        self.assertEqual(self.code_of(self.machine(key)), "api_key_revoked")

    def test_a_key_stops_when_its_creator_no_longer_manages_the_company(self):
        key = self.create()
        CompanyMembership.all_objects.filter(user=self.owner).update(role="manager")
        self.assertEqual(self.code_of(self.machine(key)), "api_key_revoked")

    def test_rotation_keeps_the_old_secret_for_the_grace_period(self):
        key = self.create()
        rotated = self.call("POST", f"/api/v1/api-keys/{key['id']}/rotate", {"grace_hours": 1},
                            session=self.admin).json()
        self.assertNotEqual(rotated["secret"], key["secret"])
        self.assertEqual(self.machine(key).status_code, 200)                  # the old one, still
        self.assertEqual(self.machine(key, rotated["secret"]).status_code, 200)
        ApiKey.objects.filter(public_id=key["id"]).update(
            previous_valid_until=timezone.now() - datetime.timedelta(seconds=1))
        self.assertEqual(self.code_of(self.machine(key)), "invalid_signature")
        self.assertEqual(self.machine(key, rotated["secret"]).status_code, 200)

    def test_rotation_with_no_grace_stops_the_old_secret_at_once(self):
        key = self.create()
        rotated = self.call("POST", f"/api/v1/api-keys/{key['id']}/rotate", {"grace_hours": 0},
                            session=self.admin).json()
        self.assertEqual(self.code_of(self.machine(key)), "invalid_signature")
        self.assertEqual(self.machine(key, rotated["secret"]).status_code, 200)

    def test_change_a_key(self):
        key = self.create()
        changed = self.call("PATCH", f"/api/v1/api-keys/{key['id']}",
                            {"scopes": ["attendance:read", "employees:read"]}, session=self.admin)
        self.assertEqual(changed.json()["scopes"], ["attendance:read", "employees:read"])
        self.assertEqual(changed.json()["name"], "IGL ERP")

    def test_a_key_needs_a_signature(self):
        key = self.create()
        response = self.client.get(PROBE, HTTP_X_KEY_ID=key["id"])
        self.assertEqual(self.code_of(response), "missing_signature_headers")

    def test_a_key_cannot_manage_keys(self):
        key = self.create(scopes=["employees:read"])
        headers = signature_headers(key["secret"], key["id"], "GET", "/api/v1/api-keys")
        response = self.client.get("/api/v1/api-keys", **headers)
        self.assertEqual(response.status_code, 403)

    def test_staff_cannot_create_keys(self):
        self.person("staff@example.test")
        staff = self.logged_in("staff@example.test")
        response = self.call("POST", "/api/v1/api-keys",
                             {"name": "x", "scopes": ["employees:read"]}, session=staff)
        self.assertEqual(self.code_of(response), "permission_denied")

    def test_another_companys_key_is_not_found(self):
        key = self.create()
        ApiKey.objects.filter(public_id=key["id"]).update(
            company=onboard_company(code="OTH", slug="oth", name="Other Ltd"))
        response = self.call("GET", f"/api/v1/api-keys/{key['id']}", session=self.admin)
        self.assertEqual(response.status_code, 404)

    def test_scopes_are_listed(self):
        rows = self.call("GET", "/api/v1/api-keys/scopes", session=self.admin,
                         query="page_size=100").json()["results"]
        self.assertIn("employees:read", {row["name"] for row in rows})
