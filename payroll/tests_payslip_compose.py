"""Email to employee opens a compose page (Nihal, 2026-09-24): From, To,
Subject and Message already written and free to change, the payslip PDF
attached, sent through the company's own mail account."""

from unittest import mock

from django.core import mail as django_mail
from django.core.mail.backends.locmem import EmailBackend as MemoryBackend
from django.test import override_settings
from django.urls import reverse

from auditlog.models import AuditLog
from common.tenant import use_company
from organization import mail_settings
from organization.models import CompanyMailSettings
from payroll.tests_approval import finalise_payroll
from payroll.tests_branch_salary import BranchSalaryBase


@override_settings(MAIL_CONFIGURED=True, DEFAULT_FROM_EMAIL="server@host.test",
                   MAILERS={"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}})
class ComposeCase(BranchSalaryBase):
    def setUp(self):
        super().setUp()
        self.generate()
        finalise_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        self.record = self.records()["Rahim"]
        with use_company(self.company):
            self.employee.work_email = "rahim@acme.test"
            self.employee.save(update_fields=["work_email"])
        self.url = reverse("payroll:payslip_email", args=[self.record.pk])
        self.client.force_login(self.admin)

    def compose(self, **changes):
        fields = {"from_name": "Acme HR", "to": "rahim.home@mail.test",
                  "subject": "Your August payslip", "body": "Hello Rahim,\nAttached.",
                  **changes}
        return self.client.post(self.url, fields, follow=True)

    def own_account(self, active=True):
        with use_company(self.company):
            CompanyMailSettings.objects.create(
                company=self.company, host="smtp.example.com", port=587,
                from_email="payroll@acme.test", from_name="Acme Payroll",
                password_encrypted=mail_settings.encrypt("key"), is_active=active)


class ComposePageTests(ComposeCase):
    def test_the_button_opens_the_compose_page(self):
        page = self.client.get(reverse("payroll:payslip", args=[self.record.pk]))
        self.assertContains(page, f'href="{self.url}"')
        self.assertNotContains(page, "data-email-blocked")

    def test_everything_is_written_in_already(self):
        page = self.client.get(self.url)
        form = page.context["form"]
        self.assertEqual(form["to"].value(), "rahim@acme.test")
        self.assertIn("August 2026", form["subject"].value())
        self.assertIn("Dear Rahim", form["body"].value())
        self.assertContains(page, "&lt;server@host.test&gt;")
        self.assertContains(page, "payslip-rahim-2026-08")        # the attachment
        self.assertContains(page, "Send email")
        self.assertNotContains(page, "disabled")

    def test_what_is_typed_is_what_is_sent(self):
        page = self.compose()
        self.assertContains(page, "Payslip emailed to rahim.home@mail.test")
        message = django_mail.outbox[-1]
        self.assertEqual(message.to, ["rahim.home@mail.test"])
        self.assertEqual(message.subject, "Your August payslip")
        self.assertEqual(message.body, "Hello Rahim,\nAttached.")
        self.assertEqual(message.from_email, "Acme HR <server@host.test>")
        name, _content, kind = message.attachments[0]
        self.assertEqual((name.startswith("payslip-rahim-2026-08"), kind), (True, "application/pdf"))
        entry = AuditLog.objects.get(action="payslip.emailed")
        self.assertEqual(entry.after_data["to"], "rahim.home@mail.test")
        self.assertEqual(entry.after_data["subject"], "Your August payslip")

    def test_someone_with_no_address_can_be_sent_to_a_typed_one(self):
        with use_company(self.company):
            self.employee.work_email = ""
            self.employee.save(update_fields=["work_email"])
        self.assertEqual(self.client.get(self.url).context["form"]["to"].value(), "")
        self.assertContains(self.compose(to="rahim@other.test"), "Payslip emailed to rahim@other.test")

    def test_a_bad_address_or_a_two_line_subject_stays_on_the_page(self):
        for changes, said in (({"to": "not-an-address"}, "valid email"),
                              ({"subject": "One\nTwo"}, "must be on one line")):
            with self.subTest(**changes):
                page = self.compose(**changes)
                self.assertContains(page, said)
                self.assertEqual(django_mail.outbox, [])

    def test_a_mail_server_refusal_is_said_on_the_compose_page(self):
        refusing = mock.Mock(side_effect=mail_settings.MailSettingsError("The mail server refused it."))
        with mock.patch.object(mail_settings, "send", refusing):
            page = self.compose()
        self.assertContains(page, "The mail server refused it.")
        self.assertEqual(page.context["form"]["to"].value(), "rahim.home@mail.test")  # kept


class CompanyAccountTests(ComposeCase):
    def test_it_goes_out_through_the_company_account_from_its_address(self):
        self.own_account()
        with mock.patch.object(mail_settings, "_smtp",
                               lambda *a, **k: MemoryBackend(alias="company")) as _smtp:
            page = self.client.get(self.url)
            self.assertContains(page, "&lt;payroll@acme.test&gt;")
            self.assertEqual(page.context["form"]["from_name"].value(), "Acme Payroll")
            self.compose(from_name="Acme Payroll")
        message = django_mail.outbox[-1]
        self.assertEqual(message.from_email, "Acme Payroll <payroll@acme.test>")
        self.assertEqual(AuditLog.objects.get(action="payslip.emailed").after_data["through"],
                         "company")

    def test_switched_off_uses_the_server_account(self):
        self.own_account(active=False)
        self.compose()
        self.assertEqual(django_mail.outbox[-1].from_email, "Acme HR <server@host.test>")


class BlockedTests(ComposeCase):
    @override_settings(MAIL_CONFIGURED=False)
    def test_not_set_up_is_said_on_both_pages(self):
        payslip = self.client.get(reverse("payroll:payslip", args=[self.record.pk]))
        self.assertContains(payslip, "data-email-blocked")
        self.assertContains(payslip, "Email is not set up on this server yet")
        self.assertContains(payslip, reverse("organization:mail_settings"))   # the admin's fix
        page = self.client.get(self.url)
        self.assertContains(page, "This payslip cannot be emailed yet.")
        self.assertContains(page, "disabled")
        self.compose()
        self.assertEqual(django_mail.outbox, [])

    def test_a_draft_month_is_said_and_not_sent(self):
        from payroll.services import reopen_payroll

        reopen_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8,
                       reason="Fixing a line")
        record = self.records()["Rahim"]
        page = self.client.get(reverse("payroll:payslip_email", args=[record.pk]))
        self.assertContains(page, "not finalised yet")
        self.client.post(reverse("payroll:payslip_email", args=[record.pk]),
                         {"to": "x@y.test", "subject": "S", "body": "B"})
        self.assertEqual(django_mail.outbox, [])

    def test_the_branch_manager_only_in_their_branch(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        far = self.records()["Karim"]
        self.assertEqual(self.client.get(
            reverse("payroll:payslip_email", args=[far.pk])).status_code, 403)
