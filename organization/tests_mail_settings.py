"""Organisation → Email settings: the company's own mail account (2026-09-24)."""

import smtplib
from unittest import mock

from django.core import mail as django_mail
from django.core.exceptions import PermissionDenied
from django.core.mail.backends.locmem import EmailBackend as MemoryBackend
from django.test import override_settings
from django.urls import reverse

from auditlog.models import AuditLog
from common.tenant import use_company
from leaves.tests_branch_access import TwoBranchCase
from organization import mail_settings as mail
from organization.models import CompanyMailSettings

GOOD = {"action": "save", "from_email": "payroll@acme.test", "from_name": "Acme Payroll",
        "host": "smtp.example.com", "port": "587", "security": "starttls",
        "username": "apikey", "password": "s3cret-key", "is_active": "on"}


def memory(*_args, **_kwargs):
    return MemoryBackend(alias="test")


class MailSettingsCase(TwoBranchCase):
    url = reverse("organization:mail_settings")

    def save(self, user=None, **changes):
        self.client.force_login(user or self.admin)
        return self.client.post(self.url, {**GOOD, **changes}, follow=True)

    def saved(self):
        with use_company(self.company):
            return CompanyMailSettings.objects.get()


class SavingTests(MailSettingsCase):
    def test_the_password_is_stored_encrypted_and_reads_back(self):
        self.assertContains(self.save(), "Email settings saved.")
        row = self.saved()
        self.assertNotIn("s3cret-key", row.password_encrypted)
        self.assertEqual(mail.decrypt(row.password_encrypted), "s3cret-key")
        self.assertEqual((row.host, row.port, row.username), ("smtp.example.com", 587, "apikey"))

    def test_an_empty_password_keeps_the_saved_one(self):
        self.save()
        self.save(password="", from_name="Acme HR")
        row = self.saved()
        self.assertEqual(row.from_name, "Acme HR")
        self.assertEqual(mail.decrypt(row.password_encrypted), "s3cret-key")

    def test_the_page_never_shows_the_password(self):
        self.save()
        page = self.client.get(self.url)
        self.assertNotContains(page, "s3cret-key")
        self.assertNotContains(page, self.saved().password_encrypted)
        self.assertContains(page, "Leave empty to keep it")

    def test_it_is_audited_without_the_password(self):
        self.save()
        entry = AuditLog.objects.get(action="mail_settings.saved")
        self.assertTrue(entry.after_data["password_changed"])
        self.assertNotIn("s3cret-key", str(entry.after_data))
        self.assertNotIn("password_encrypted", str(entry.after_data))

    def test_a_server_address_with_a_port_or_path_is_refused(self):
        page = self.save(host="smtp.example.com:587")
        self.assertContains(page, "Enter just the server name")
        with use_company(self.company):
            self.assertFalse(CompanyMailSettings.objects.exists())

    def test_only_the_owner_or_company_admin(self):
        for user in (self.hr, self.manager):
            with self.subTest(user=user.email):
                self.client.force_login(user)
                self.assertNotEqual(self.client.get(self.url).status_code, 200)
        with self.assertRaises(PermissionDenied):
            mail.send_test(actor=self.hr, company_id=self.company.pk, to="x@acme.test")

    def test_it_is_in_the_organisation_menu(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("dashboard"))
        self.assertContains(page, self.url)


class WhichAccountTests(MailSettingsCase):
    @override_settings(MAIL_CONFIGURED=False)
    def test_nothing_set_up(self):
        self.assertEqual(mail.sender(self.company.pk)[0], None)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(self.url), "Not set up")

    @override_settings(MAIL_CONFIGURED=True, DEFAULT_FROM_EMAIL="server@host.test")
    def test_the_server_account_until_the_company_saves_its_own(self):
        self.assertEqual(mail.sender(self.company.pk), ("server", "server@host.test", ""))
        self.save()
        self.assertEqual(mail.sender(self.company.pk),
                         ("company", "payroll@acme.test", "Acme Payroll"))

    @override_settings(MAIL_CONFIGURED=True, DEFAULT_FROM_EMAIL="server@host.test")
    def test_switched_off_goes_back_to_the_server_account(self):
        self.save(is_active="")
        self.assertEqual(mail.sender(self.company.pk)[0], "server")
        self.assertIsNone(mail.connection_for(self.company.pk))


class SafetyTests(MailSettingsCase):
    def test_an_odd_port_is_refused(self):
        with self.assertRaisesMessage(mail.MailSettingsError, "not a mail port"):
            mail._check_host("smtp.example.com", 5432)

    def test_a_private_or_local_server_is_refused(self):
        for host in ("127.0.0.1", "10.0.0.5", "192.168.1.10", "localhost"):
            with self.subTest(host=host):
                with self.assertRaisesMessage(mail.MailSettingsError, "private or local"):
                    mail._check_host(host, 587)

    def test_a_password_saved_under_another_secret_cannot_be_read(self):
        token = mail.encrypt("x")
        with override_settings(SECRET_KEY="another-secret-key-entirely-different-1234"):
            with self.assertRaisesMessage(mail.MailSettingsError, "Enter the password again"):
                mail.decrypt(token)


class TestEmailTests(MailSettingsCase):
    def setUp(self):
        super().setUp()
        self.save()

    def test_a_working_account_sends_and_is_remembered(self):
        with mock.patch.object(mail, "_smtp", memory):
            page = self.client.post(self.url, {"action": "test", "test_to": "me@acme.test"},
                                    follow=True)
        self.assertContains(page, "Test email sent to me@acme.test")
        message = django_mail.outbox[-1]
        self.assertEqual(message.to, ["me@acme.test"])
        self.assertEqual(message.from_email, "Acme Payroll <payroll@acme.test>")
        row = self.saved()
        self.assertTrue(row.last_test_ok)
        self.assertContains(page, "Last test worked")

    def test_a_refused_login_is_said_plainly_and_remembered(self):
        refusing = mock.Mock(side_effect=smtplib.SMTPAuthenticationError(535, b"bad"))
        with mock.patch.object(mail, "_smtp", memory), \
                mock.patch.object(MemoryBackend, "send_messages", refusing):
            page = self.client.post(self.url, {"action": "test", "test_to": "me@acme.test"},
                                    follow=True)
        self.assertContains(page, "refused the username or password")
        row = self.saved()
        self.assertFalse(row.last_test_ok)
        self.assertContains(page, "Last test failed")
        self.assertTrue(AuditLog.objects.filter(action="mail_settings.tested").exists())

    def test_changing_the_server_forgets_the_last_test(self):
        with mock.patch.object(mail, "_smtp", memory):
            mail.send_test(actor=self.admin, company_id=self.company.pk, to="me@acme.test")
        self.save(host="smtp.other.com", password="")
        self.assertIsNone(self.saved().last_test_ok)
