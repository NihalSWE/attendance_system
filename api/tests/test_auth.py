"""Phase 1: logging in, signing, sessions, two-step login and passwords
(docs/api/00-PLAN.md, Part 2; docs/api/02-authentication.md)."""

import datetime
import hashlib
import hmac
import io
import json
import secrets
import sys
import time
import types
from contextlib import redirect_stdout

import pyotp
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import caches
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import CompanyMembership
from api.core.crypto import encrypt
from api.core.registry import by_id
from api.core.samples import samples
from api.models import ApiSession, TwoStep
from tenants.services import onboard_company

User = get_user_model()
PASSWORD = "pw-Strong-12345"


def signature_headers(secret, key_id, method, path, query="", body="", *, ts=None, nonce=None):
    """Signs exactly as docs/api/02-authentication.md says - written here
    independently of the server's code, so the format itself is tested."""
    ts = str(int(time.time()) if ts is None else ts)
    nonce = nonce or secrets.token_hex(16)
    sorted_query = "&".join(sorted(p for p in query.split("&") if p))
    canonical = "\n".join([method, path, sorted_query, ts, nonce,
                           hashlib.sha256(body.encode()).hexdigest()])
    signature = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return {"HTTP_X_KEY_ID": key_id, "HTTP_X_TIMESTAMP": ts, "HTTP_X_NONCE": nonce,
            "HTTP_X_SIGNATURE": signature}


class ApiTestCase(TestCase):
    def setUp(self):
        caches["api"].clear()
        self.company = onboard_company(code="ACME", slug="acme", name="Acme Ltd")
        self.client = APIClient()

    def person(self, email, role="manager", company=None):
        user = User.objects.create_user(email=email, password=PASSWORD)
        CompanyMembership.all_objects.create(company=company or self.company, user=user,
                                             role=role, status="active")
        return user

    def call(self, method, path, body=None, *, session=None, query="", sign=True,
             token=None, headers=None, client=None):
        """A request; with ``session`` (a login answer) it carries the access
        token and the signature."""
        raw = json.dumps(body) if body is not None else ""
        extra = dict(headers or {})
        if session is not None:
            extra["HTTP_AUTHORIZATION"] = "Bearer " + (token or session["access_token"])
            if sign:
                extra.update(signature_headers(session["signing_secret"], session["session_id"],
                                               method, path, query, raw))
        url = path + ("?" + query if query else "")
        return (client or self.client).generic(method, url, raw, content_type="application/json",
                                               **extra)

    def login(self, email, password=PASSWORD, client_type="mobile"):
        return self.call("POST", "/api/v1/auth/login",
                         {"email": email, "password": password, "client_type": client_type})

    def logged_in(self, email):
        response = self.login(email)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def code_of(self, response):
        return response.json()["error"]["code"]

    def turn_on_two_step(self, user):
        secret = pyotp.random_base32()
        TwoStep.objects.create(user=user, secret_encrypted=encrypt(secret),
                               confirmed_at=timezone.now(), recovery_hashes=[])
        return secret


class LoginTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.person("rahim@example.test")

    def test_an_app_logs_in_and_signs_its_requests(self):
        session = self.logged_in("rahim@example.test")
        self.assertFalse(session["two_step_required"])
        for name in ("session_id", "access_token", "refresh_token", "signing_secret"):
            self.assertTrue(session[name], name)
        self.assertEqual(session["access_expires_in"], 600)
        stored = ApiSession.objects.get(public_id=session["session_id"])
        self.assertNotIn(session["access_token"], (stored.access_hash, stored.refresh_hash))
        response = self.call("GET", "/api/v1/auth/me", session=session)
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual(data["email"], "rahim@example.test")
        self.assertEqual(data["companies"], [{"id": self.company.pk, "name": "Acme Ltd",
                                              "role": "manager"}])
        self.assertEqual(data["session"]["id"], session["session_id"])

    def test_wrong_password_and_unknown_email_answer_the_same(self):
        wrong = self.login("rahim@example.test", "nope-nope-1")
        unknown = self.login("nobody@example.test", "nope-nope-1")
        self.assertEqual((wrong.status_code, self.code_of(wrong)), (401, "invalid_credentials"))
        self.assertEqual((unknown.status_code, self.code_of(unknown)), (401, "invalid_credentials"))

    def test_five_wrong_passwords_lock_the_login(self):
        for _ in range(5):
            self.login("rahim@example.test", "nope-nope-1")
        response = self.login("rahim@example.test")            # even the right password
        self.assertEqual((response.status_code, self.code_of(response)), (429, "login_locked"))
        self.assertGreater(int(response["Retry-After"]), 800)

    def test_the_platform_root_cannot_use_the_api(self):
        User.objects.create_superuser(email="root@example.test", password=PASSWORD)
        response = self.login("root@example.test")
        self.assertEqual(self.code_of(response), "platform_login_not_allowed")

    def test_a_login_without_an_active_company_is_refused(self):
        User.objects.create_user(email="loose@example.test", password=PASSWORD)
        self.assertEqual(self.code_of(self.login("loose@example.test")), "permission_denied")

    def test_no_login_is_401(self):
        response = self.call("GET", "/api/v1/auth/me")
        self.assertEqual((response.status_code, self.code_of(response)), (401, "not_authenticated"))

    def test_an_expired_access_token_says_so(self):
        session = self.logged_in("rahim@example.test")
        ApiSession.objects.filter(public_id=session["session_id"]).update(
            access_expires_at=timezone.now() - datetime.timedelta(seconds=1))
        self.assertEqual(self.code_of(self.call("GET", "/api/v1/auth/me", session=session)),
                         "token_expired")

    def test_logout_ends_the_session(self):
        session = self.logged_in("rahim@example.test")
        self.assertEqual(self.call("POST", "/api/v1/auth/logout", session=session).status_code, 200)
        response = self.call("GET", "/api/v1/auth/me", session=session)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(ApiSession.objects.get(public_id=session["session_id"]).state, "ended")

    @override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.Argon2PasswordHasher",
                                         "django.contrib.auth.hashers.MD5PasswordHasher"])
    def test_an_old_password_hash_is_upgraded_to_argon2(self):
        self.assertTrue(User.objects.get(pk=self.user.pk).password.startswith("md5$"))
        self.logged_in("rahim@example.test")
        self.assertTrue(User.objects.get(pk=self.user.pk).password.startswith("argon2"))


class SignatureTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.person("rahim@example.test")
        self.session = self.logged_in("rahim@example.test")

    def me(self, **headers):
        extra = {"HTTP_AUTHORIZATION": "Bearer " + self.session["access_token"], **headers}
        return self.client.get("/api/v1/auth/me", **extra)

    def signed(self, **changes):
        return signature_headers(self.session["signing_secret"], self.session["session_id"],
                                 "GET", "/api/v1/auth/me", **changes)

    def test_a_token_without_a_signature_is_refused(self):
        self.assertEqual(self.code_of(self.me()), "signature_required")

    def test_a_wrong_signature_is_refused(self):
        headers = self.signed()
        headers["HTTP_X_SIGNATURE"] = "0" * 64
        self.assertEqual(self.code_of(self.me(**headers)), "invalid_signature")

    def test_another_secret_is_refused(self):
        headers = signature_headers("ss_wrong", self.session["session_id"], "GET",
                                    "/api/v1/auth/me")
        self.assertEqual(self.code_of(self.me(**headers)), "invalid_signature")

    def test_an_old_timestamp_is_refused(self):
        headers = self.signed(ts=int(time.time()) - 301)
        self.assertEqual(self.code_of(self.me(**headers)), "timestamp_out_of_range")

    def test_milliseconds_are_refused(self):
        headers = self.signed(ts=int(time.time() * 1000))
        self.assertEqual(self.code_of(self.me(**headers)), "timestamp_out_of_range")

    def test_a_replayed_request_is_refused(self):
        headers = self.signed()
        self.assertEqual(self.me(**headers).status_code, 200)
        self.assertEqual(self.code_of(self.me(**headers)), "replay_detected")

    def test_a_missing_header_is_named(self):
        headers = self.signed()
        del headers["HTTP_X_NONCE"]
        self.assertEqual(self.code_of(self.me(**headers)), "missing_signature_headers")

    def test_the_key_id_must_be_this_session(self):
        other = self.logged_in("rahim@example.test")
        headers = signature_headers(other["signing_secret"], other["session_id"], "GET",
                                    "/api/v1/auth/me")
        self.assertEqual(self.code_of(self.me(**headers)), "invalid_signature")

    def test_the_query_is_signed_sorted(self):
        response = self.call("GET", "/api/v1/auth/sessions", session=self.session,
                             query="page_size=5&page=1")
        self.assertEqual(response.status_code, 200, response.content)

    def test_a_changed_body_breaks_the_signature(self):
        body = json.dumps({"current_password": PASSWORD, "new_password": "Another-pass-987"})
        headers = signature_headers(self.session["signing_secret"], self.session["session_id"],
                                    "POST", "/api/v1/auth/password/change", body=body)
        response = self.client.generic(
            "POST", "/api/v1/auth/password/change", body.replace("987", "988"),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + self.session["access_token"], **headers)
        self.assertEqual(self.code_of(response), "invalid_signature")

    def test_the_signature_test_explains_a_mistake(self):
        good = signature_headers(self.session["signing_secret"], self.session["session_id"],
                                 "POST", "/api/v1/auth/signature-test", body="{}")
        response = self.client.generic("POST", "/api/v1/auth/signature-test", "{}",
                                       content_type="application/json", **good)
        self.assertEqual(response.json()["problems"], [])
        self.assertTrue(response.json()["signature_ok"])
        bad = dict(good, HTTP_X_SIGNATURE="f" * 64, HTTP_X_TIMESTAMP=str(int(time.time() * 1000)))
        data = self.client.generic("POST", "/api/v1/auth/signature-test", "{}",
                                   content_type="application/json", **bad).json()
        self.assertFalse(data["signature_ok"])
        self.assertFalse(data["timestamp_ok"])
        self.assertEqual(len(data["problems"]), 2)
        self.assertIn("/api/v1/auth/signature-test", data["canonical_request"])

    def test_the_python_request_example_really_works(self):
        """The Python sample on the endpoint's page, run against the server."""
        code = dict((key, text) for key, _, text in samples(by_id("auth-me"), "http://testserver"))
        code = (code["python"].replace('"ses_..."', repr(self.session["session_id"]))
                .replace('"ss_..."', repr(self.session["signing_secret"]))
                .replace('"at_..."', repr(self.session["access_token"])))
        client = self.client

        def request(method, url, headers=None, data=None, timeout=None):
            extra = {"HTTP_" + name.upper().replace("-", "_"): value
                     for name, value in headers.items() if name != "Content-Type"}
            answer = client.generic(method, url.replace("http://testserver", ""),
                                    data or b"", content_type="application/json", **extra)
            return types.SimpleNamespace(ok=answer.status_code < 400, json=answer.json)

        fake = types.ModuleType("requests")
        fake.request = request
        printed = io.StringIO()
        saved = sys.modules.get("requests")
        sys.modules["requests"] = fake
        try:
            with redirect_stdout(printed):
                exec(compile(code, "sample.py", "exec"), {})
        finally:
            if saved is not None:
                sys.modules["requests"] = saved
            else:
                sys.modules.pop("requests", None)
        self.assertIn("rahim@example.test", printed.getvalue())


class RefreshTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.person("rahim@example.test")
        self.session = self.logged_in("rahim@example.test")

    def refresh(self, token, sign=True):
        body = {"refresh_token": token}
        raw = json.dumps(body)
        headers = signature_headers(self.session["signing_secret"], self.session["session_id"],
                                    "POST", "/api/v1/auth/refresh", body=raw) if sign else {}
        return self.client.generic("POST", "/api/v1/auth/refresh", raw,
                                   content_type="application/json", **headers)

    def test_refresh_gives_a_new_pair_and_keeps_the_secret(self):
        response = self.refresh(self.session["refresh_token"])
        self.assertEqual(response.status_code, 200, response.content)
        new = response.json()
        self.assertNotEqual(new["access_token"], self.session["access_token"])
        self.assertNotEqual(new["refresh_token"], self.session["refresh_token"])
        self.assertNotIn("signing_secret", new)
        renewed = {**self.session, **new}
        self.assertEqual(self.call("GET", "/api/v1/auth/me", session=renewed).status_code, 200)
        self.assertEqual(self.call("GET", "/api/v1/auth/me", session=self.session).status_code, 401)

    def test_refresh_must_be_signed(self):
        self.assertEqual(self.code_of(self.refresh(self.session["refresh_token"], sign=False)),
                         "signature_required")

    def test_a_reused_refresh_token_ends_the_session(self):
        new = self.refresh(self.session["refresh_token"]).json()
        ApiSession.objects.filter(public_id=self.session["session_id"]).update(
            rotated_at=timezone.now() - datetime.timedelta(minutes=2))
        self.assertEqual(self.code_of(self.refresh(self.session["refresh_token"])),
                         "refresh_token_reused")
        self.assertEqual(ApiSession.objects.get(public_id=self.session["session_id"]).state,
                         "ended")
        self.assertEqual(self.refresh(new["refresh_token"]).status_code, 401)

    def test_a_retry_within_a_minute_is_not_punished(self):
        self.assertEqual(self.refresh(self.session["refresh_token"]).status_code, 200)
        retry = self.refresh(self.session["refresh_token"])
        self.assertEqual(retry.status_code, 200, retry.content)
        self.assertEqual(ApiSession.objects.get(public_id=self.session["session_id"]).state,
                         "active")

    def test_an_unknown_refresh_token_is_refused(self):
        self.assertEqual(self.code_of(self.refresh("rt_nothing")), "invalid_token")


class SessionTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.person("rahim@example.test")
        self.phone = self.logged_in("rahim@example.test")
        self.laptop = self.logged_in("rahim@example.test")

    def test_my_sessions_mark_the_current_one(self):
        rows = self.call("GET", "/api/v1/auth/sessions", session=self.phone).json()["results"]
        self.assertEqual(len(rows), 2)
        current = [row["id"] for row in rows if row["current"]]
        self.assertEqual(current, [self.phone["session_id"]])

    def test_sign_out_one_session(self):
        path = f"/api/v1/auth/sessions/{self.laptop['session_id']}"
        self.assertEqual(self.call("DELETE", path, session=self.phone).status_code, 200)
        self.assertEqual(self.call("GET", "/api/v1/auth/me", session=self.laptop).status_code, 401)
        self.assertEqual(self.call("GET", "/api/v1/auth/me", session=self.phone).status_code, 200)

    def test_another_persons_session_is_not_found(self):
        self.person("karim@example.test")
        other = self.logged_in("karim@example.test")
        response = self.call("DELETE", f"/api/v1/auth/sessions/{other['session_id']}",
                             session=self.phone)
        self.assertEqual(response.status_code, 404)

    def test_sign_out_everywhere_else(self):
        self.assertEqual(self.call("POST", "/api/v1/auth/sessions/sign-out-others",
                                   session=self.phone).status_code, 200)
        self.assertEqual(self.call("GET", "/api/v1/auth/me", session=self.laptop).status_code, 401)
        self.assertEqual(self.call("GET", "/api/v1/auth/me", session=self.phone).status_code, 200)


class TwoStepTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.person("owner@example.test", role="owner")

    def test_an_owner_must_set_up_two_step_before_anything_else(self):
        session = self.logged_in("owner@example.test")
        self.assertTrue(session["two_step_setup_required"])
        self.assertEqual(self.call("GET", "/api/v1/auth/me", session=session).status_code, 200)
        blocked = self.call("GET", "/api/v1/api-keys", session=session)
        self.assertEqual(self.code_of(blocked), "two_step_setup_required")

        setup = self.call("POST", "/api/v1/auth/two-step/setup", session=session).json()
        self.assertTrue(setup["otpauth_url"].startswith("otpauth://totp/"))
        totp = pyotp.TOTP(setup["secret"])
        wrong = self.call("POST", "/api/v1/auth/two-step/confirm", {"code": "000000"},
                          session=session)
        self.assertEqual(self.code_of(wrong), "invalid_two_step_code")
        confirmed = self.call("POST", "/api/v1/auth/two-step/confirm", {"code": totp.now()},
                              session=session)
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        codes = confirmed.json()["recovery_codes"]
        self.assertEqual(len(codes), 10)
        self.assertEqual(self.call("GET", "/api/v1/api-keys", session=session).status_code, 200)

        # From now on, logging in asks for a code.
        first = self.login("owner@example.test").json()
        self.assertEqual(set(first), {"two_step_required", "challenge", "challenge_expires_in"})
        later = totp.at(int(time.time()) + 30)          # the code just used works only once
        done = self.call("POST", "/api/v1/auth/login/two-step",
                         {"challenge": first["challenge"], "code": later})
        self.assertEqual(done.status_code, 200, done.content)
        self.assertFalse(done.json()["two_step_setup_required"])
        again = self.call("POST", "/api/v1/auth/login/two-step",
                          {"challenge": first["challenge"], "code": later})
        self.assertEqual(self.code_of(again), "challenge_expired")

        # A recovery code works once.
        for expected in (200, "invalid_two_step_code"):
            challenge = self.login("owner@example.test").json()["challenge"]
            answer = self.call("POST", "/api/v1/auth/login/two-step",
                               {"challenge": challenge, "code": codes[0]})
            self.assertEqual(answer.status_code if expected == 200 else self.code_of(answer),
                             expected)

    def test_a_same_totp_code_works_once(self):
        secret = self.turn_on_two_step(self.owner)
        code = pyotp.TOTP(secret).now()
        challenge = self.login("owner@example.test").json()["challenge"]
        self.assertEqual(self.call("POST", "/api/v1/auth/login/two-step",
                                   {"challenge": challenge, "code": code}).status_code, 200)
        challenge = self.login("owner@example.test").json()["challenge"]
        reused = self.call("POST", "/api/v1/auth/login/two-step",
                           {"challenge": challenge, "code": code})
        self.assertEqual(self.code_of(reused), "invalid_two_step_code")

    def test_an_expired_challenge_is_refused(self):
        secret = self.turn_on_two_step(self.owner)
        challenge = self.login("owner@example.test").json()["challenge"]
        ApiSession.objects.filter(state="two_step").update(
            challenge_expires_at=timezone.now() - datetime.timedelta(seconds=1))
        response = self.call("POST", "/api/v1/auth/login/two-step",
                             {"challenge": challenge, "code": pyotp.TOTP(secret).now()})
        self.assertEqual(self.code_of(response), "challenge_expired")

    def test_an_owner_cannot_turn_it_off(self):
        secret = self.turn_on_two_step(self.owner)
        challenge = self.login("owner@example.test").json()["challenge"]
        session = self.call("POST", "/api/v1/auth/login/two-step",
                            {"challenge": challenge, "code": pyotp.TOTP(secret).now()}).json()
        response = self.call("POST", "/api/v1/auth/two-step/disable",
                             {"password": PASSWORD, "code": "123456"}, session=session)
        self.assertEqual(self.code_of(response), "permission_denied")

    def test_others_may_turn_it_on_and_off(self):
        self.person("staff@example.test")
        session = self.logged_in("staff@example.test")
        self.assertFalse(session["two_step_setup_required"])
        secret = self.call("POST", "/api/v1/auth/two-step/setup", session=session).json()["secret"]
        totp = pyotp.TOTP(secret)
        self.call("POST", "/api/v1/auth/two-step/confirm", {"code": totp.now()}, session=session)
        off = self.call("POST", "/api/v1/auth/two-step/disable",
                        {"password": PASSWORD, "code": totp.at(int(time.time()) + 30)},
                        session=session)
        self.assertEqual(off.status_code, 200, off.content)
        self.assertFalse(self.login("staff@example.test").json()["two_step_required"])


class WebTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.person("rahim@example.test")
        self.browser = APIClient(enforce_csrf_checks=True)

    def csrf(self):
        return self.browser.get("/api/v1/auth/web/csrf").json()["csrf_token"]

    def web(self, method, path, body=None, csrf=None):
        headers = {"HTTP_X_CSRFTOKEN": csrf} if csrf else {}
        return self.call(method, path, body, client=self.browser, headers=headers)

    def test_a_browser_logs_in_with_cookies_and_csrf(self):
        token = self.csrf()
        response = self.web("POST", "/api/v1/auth/web/login",
                            {"email": "rahim@example.test", "password": PASSWORD}, csrf=token)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn("access_token", response.json())
        cookie = response.cookies["api_access"]
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Strict")
        self.assertEqual(self.web("GET", "/api/v1/auth/me").status_code, 200)

        refused = self.web("POST", "/api/v1/auth/logout")
        self.assertEqual((refused.status_code, self.code_of(refused)), (403, "csrf_failed"))
        refreshed = self.web("POST", "/api/v1/auth/web/refresh", csrf=token)
        self.assertEqual(refreshed.status_code, 200, refreshed.content)
        self.assertEqual(self.web("GET", "/api/v1/auth/me").status_code, 200)
        self.assertEqual(self.web("POST", "/api/v1/auth/logout", csrf=token).status_code, 200)
        self.assertEqual(self.web("GET", "/api/v1/auth/me").status_code, 401)

    def test_login_without_the_csrf_token_is_refused(self):
        self.csrf()
        response = self.web("POST", "/api/v1/auth/web/login",
                            {"email": "rahim@example.test", "password": PASSWORD})
        self.assertEqual(self.code_of(response), "csrf_failed")

    def test_a_web_cookie_cannot_be_used_as_an_app_token(self):
        token = self.csrf()
        self.web("POST", "/api/v1/auth/web/login",
                 {"email": "rahim@example.test", "password": PASSWORD}, csrf=token)
        access = self.browser.cookies["api_access"].value
        response = self.client.get("/api/v1/auth/me", HTTP_AUTHORIZATION="Bearer " + access)
        self.assertEqual(self.code_of(response), "invalid_token")


class PasswordTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.person("rahim@example.test")
        self.phone = self.logged_in("rahim@example.test")
        self.laptop = self.logged_in("rahim@example.test")

    def test_changing_the_password_ends_the_other_sessions(self):
        response = self.call("POST", "/api/v1/auth/password/change",
                             {"current_password": PASSWORD, "new_password": "Another-pass-987"},
                             session=self.phone)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.call("GET", "/api/v1/auth/me", session=self.phone).status_code, 200)
        self.assertEqual(self.call("GET", "/api/v1/auth/me", session=self.laptop).status_code, 401)

    def test_a_wrong_current_password_is_refused(self):
        response = self.call("POST", "/api/v1/auth/password/change",
                             {"current_password": "nope", "new_password": "Another-pass-987"},
                             session=self.phone)
        self.assertEqual(response.status_code, 422)
        self.assertIn("current_password", response.json()["error"]["fields"])

    def test_a_password_changed_in_the_panels_ends_api_sessions(self):
        self.user.set_password("Changed-in-panel-1")
        self.user.save()
        response = self.call("GET", "/api/v1/auth/me", session=self.phone)
        self.assertEqual(self.code_of(response), "session_ended")

    def test_forgot_answers_the_same_and_reset_works_once(self):
        unknown = self.call("POST", "/api/v1/auth/password/forgot", {"email": "x@example.test"})
        known = self.call("POST", "/api/v1/auth/password/forgot", {"email": "rahim@example.test"})
        self.assertEqual(unknown.json(), known.json())
        self.assertEqual(len(mail.outbox), 1)
        token = mail.outbox[0].body.split("Your reset code is:\n")[1].split()[0]
        reset = self.call("POST", "/api/v1/auth/password/reset",
                          {"token": token, "new_password": "Brand-new-pass-55"})
        self.assertEqual(reset.status_code, 200, reset.content)
        self.assertEqual(self.call("GET", "/api/v1/auth/me", session=self.phone).status_code, 401)
        self.assertEqual(self.login("rahim@example.test", "Brand-new-pass-55").status_code, 200)
        again = self.call("POST", "/api/v1/auth/password/reset",
                          {"token": token, "new_password": "Brand-new-pass-56"})
        self.assertEqual(self.code_of(again), "invalid_reset")


class CompanySessionTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        owner = self.person("owner@example.test", role="owner")
        secret = self.turn_on_two_step(owner)
        challenge = self.login("owner@example.test").json()["challenge"]
        self.admin = self.call("POST", "/api/v1/auth/login/two-step",
                               {"challenge": challenge, "code": pyotp.TOTP(secret).now()}).json()
        self.person("staff@example.test")
        self.staff = self.logged_in("staff@example.test")
        other = onboard_company(code="OTH", slug="oth", name="Other Ltd")
        self.person("outsider@example.test", company=other)
        self.outsider = self.logged_in("outsider@example.test")

    def test_an_admin_sees_and_ends_staff_sessions_of_their_company_only(self):
        rows = self.call("GET", "/api/v1/company/sessions", session=self.admin).json()["results"]
        emails = {row["user_email"] for row in rows}
        self.assertIn("staff@example.test", emails)
        self.assertNotIn("outsider@example.test", emails)
        outsider = self.call("DELETE", f"/api/v1/company/sessions/{self.outsider['session_id']}",
                             session=self.admin)
        self.assertEqual(outsider.status_code, 404)
        ended = self.call("DELETE", f"/api/v1/company/sessions/{self.staff['session_id']}",
                          session=self.admin)
        self.assertEqual(ended.status_code, 200, ended.content)
        self.assertEqual(self.call("GET", "/api/v1/auth/me", session=self.staff).status_code, 401)

    def test_staff_cannot_see_company_sessions(self):
        response = self.call("GET", "/api/v1/company/sessions", session=self.staff)
        self.assertEqual((response.status_code, self.code_of(response)), (403, "permission_denied"))
