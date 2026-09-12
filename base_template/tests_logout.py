"""Signing out.

Django 5 made LogoutView POST-only on purpose: a GET logout can be triggered
by any page with ``<img src="/logout/">``, which carries no CSRF token. That
protection is kept here. What is fixed is the dead end it left — reaching
``/logout/`` by typing it, a bookmark, a prefetch or a refresh returned a bare
405 page, which reads as a broken site.
"""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

User = get_user_model()


class LogoutTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            email="member@example.test", password="pw-12345678"
        )

    def test_get_shows_a_confirmation_instead_of_405(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("logout"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sign out")

    def test_get_does_not_sign_the_user_out(self):
        """The whole reason GET is not the logout action.

        If a bare GET ended the session, any page could embed the URL and log
        a visitor out without their intent and without a CSRF token.
        """
        self.client.force_login(self.user)
        self.client.get(reverse("logout"))
        self.assertIn("_auth_user_id", self.client.session)

    def test_post_signs_out_and_lands_on_login(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("logout"), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(response.redirect_chain[-1][0], reverse("login"))

    def test_get_when_already_signed_out_redirects_rather_than_erroring(self):
        response = self.client.get(reverse("logout"), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.redirect_chain[-1][0], reverse("login"))

    def test_the_confirmation_page_posts_back_to_logout(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("logout"))
        content = response.content.decode()
        self.assertIn(f'action="{reverse("logout")}"', content)
        self.assertIn('method="post"', content)
        # Without the token the POST would be rejected by CSRF middleware.
        self.assertIn("csrfmiddlewaretoken", content)


class CsrfFailureTests(TestCase):
    """A rejected token must stay rejected, but must not look like a crash.

    The common cause is a stale tab: Django rotates the CSRF token on login,
    so a page opened before signing in still carries the old one. Clicking
    Sign out there previously produced a bare "Forbidden (403) CSRF
    verification failed" wall.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email="member@example.test", password="pw-12345678"
        )

    def test_a_stale_token_is_still_refused(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        response = client.post(
            reverse("logout"), {"csrfmiddlewaretoken": "stale-token-from-an-old-tab"}
        )
        self.assertEqual(response.status_code, 403)
        # Refused means refused: the session must survive.
        self.assertIn("_auth_user_id", client.session)

    def test_the_refusal_explains_itself_instead_of_showing_the_raw_wall(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        response = client.post(
            reverse("logout"), {"csrfmiddlewaretoken": "stale-token-from-an-old-tab"}
        )
        self.assertContains(response, "had gone stale", status_code=403)
        self.assertContains(response, "Reload this page", status_code=403)

    def test_a_valid_signout_is_unaffected(self):
        client = Client()
        client.force_login(self.user)
        response = client.post(reverse("logout"), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", client.session)
