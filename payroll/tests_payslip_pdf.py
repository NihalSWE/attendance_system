"""A payslip as a PDF (A11): the page's own view, so the page's own rules.

Built through common/exports.py - one PDF path for the whole project - so a
Bangla name prints properly here too, with no second font or page setup.
"""

import datetime

from django.core import mail
from django.test import override_settings
from django.urls import reverse

from auditlog.models import AuditLog
from common import exports
from common.tests_exports import RAHIM_AHMED, glyph_ids, pdf_text
from common.tenant import use_company
from payroll.models import PayrollRecord, PayrollRun
from payroll.services import generate_payroll
from payroll import tests_basic
from payroll.tests_approval import finalise_payroll
from payroll.tests_branch_salary import BranchSalaryBase


class PayslipPdfTests(tests_basic.EndToEndTests.__mro__[1]):
    setUp = tests_basic.EndToEndTests.setUp

    def record(self, employee=None):
        run = generate_payroll(actor=self.admin, company_id=self.company.pk,
                               year=2026, month=8)
        with use_company(self.company):
            return PayrollRecord.objects.get(
                payroll_run=run, employee=employee or self.monthly)

    def download(self, record=None, user=None):
        record = record or self.record()
        self.client.force_login(user or self.admin)
        return record, self.client.get(
            reverse("payroll:payslip", args=[record.pk]), {"format": "pdf"})

    def test_it_holds_what_the_payslip_says(self):
        record, response = self.download()
        self.assertEqual(response["Content-Type"], "application/pdf")
        text = pdf_text(response.content)
        self.assertIn(record.employee.full_name, text)
        self.assertIn("August 2026", text)
        for heading in ("Payslip", "Earnings", "Deductions", "Attendance",
                        "Gross earnings", "Total deductions", "Net pay"):
            self.assertIn(heading, text)
        self.assertIn(f"{record.net_pay:,.2f}", text)
        with use_company(self.company):
            for line in record.lines.all():
                self.assertIn(line.code, text)
                self.assertIn(f"{line.amount:,.2f}", text)
                # Only the ASCII part: a subset font stores "x" and "-" as
                # glyph codes, so they cannot be read back out of the file -
                # which is exactly how a Bangla name survives in it.
                self.assertIn(line.description.split("(")[0].strip(), text)

    def test_the_filename_carries_the_person_and_the_month(self):
        record, response = self.download()
        self.assertIn(f'filename="payslip-{exports.slug(record.employee.full_name)}-2026-08.pdf"',
                      response["Content-Disposition"])

    def test_a_draft_says_it_is_a_draft(self):
        _record, response = self.download()
        self.assertIn("draft and can still change", pdf_text(response.content))

    def test_a_bangla_name_prints_as_letters_not_boxes(self):
        with use_company(self.company):
            first, last = RAHIM_AHMED.split(" ")
            self.monthly.first_name, self.monthly.last_name = first, last
            self.monthly.save(update_fields=["first_name", "last_name"])
        _record, response = self.download()
        self.assertIn(b"HindSiliguri", response.content)
        self.assertNotIn(b"Helvetica", response.content)
        self.assertNotIn(0, glyph_ids(exports.FONT, RAHIM_AHMED))

    def test_every_download_is_recorded(self):
        record, _response = self.download()
        entry = AuditLog.objects.get(action="export.downloaded")
        self.assertEqual(entry.after_data["page"], "payslip")
        self.assertEqual(entry.after_data["filters"]["payslip"], record.pk)
        self.assertEqual(entry.after_data["filters"]["period"], "August 2026")

    def test_the_page_offers_it(self):
        record = self.record()
        self.client.force_login(self.admin)
        page = self.client.get(reverse("payroll:payslip", args=[record.pk]))
        self.assertContains(page, "Download PDF")
        self.assertContains(page, 'href="?format=pdf"')


class WhoMayDownloadTests(BranchSalaryBase):
    """The PDF is the payslip page's own view, so it carries the page's rules."""

    def setUp(self):
        super().setUp()
        self.generate()
        self.by_name = self.records()

    def pdf(self, record, user, url=None):
        self.client.force_login(user)
        return self.client.get(url or self.payslip_url(record), {"format": "pdf"})

    def test_a_branch_manager_gets_their_branch_and_not_another(self):
        mine = self.pdf(self.by_name["Rahim"], self.manager)
        self.assertEqual(mine["Content-Type"], "application/pdf")
        self.assertIn("Rahim", pdf_text(mine.content))
        self.assertEqual(self.pdf(self.by_name["Karim"], self.manager).status_code, 403)

    def test_an_employee_gets_their_own_finalised_payslip_only(self):
        me_url = reverse("me:payslip", args=[self.by_name["Clerk"].pk])
        # A draft is not theirs to see yet, in the PDF as on the page.
        self.assertEqual(self.pdf(None, self.clerk_user, me_url).status_code, 404)

        finalise_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        response = self.pdf(None, self.clerk_user, me_url)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("Clerk", pdf_text(response.content))
        # Somebody else's payslip is still not theirs.
        other = reverse("me:payslip", args=[self.by_name["Rahim"].pk])
        self.assertEqual(self.pdf(None, self.clerk_user, other).status_code, 404)

    def test_the_employees_own_page_offers_it(self):
        finalise_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        self.client.force_login(self.clerk_user)
        page = self.client.get(reverse("me:payslip", args=[self.by_name["Clerk"].pk]))
        self.assertContains(page, "Download PDF")


@override_settings(MAIL_CONFIGURED=True, DEFAULT_FROM_EMAIL="payroll@acme.test",
                   MAILERS={"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}})
class PayslipEmailTests(BranchSalaryBase):
    """Emailing a payslip is off until a mail server is configured, and only
    a finalised payslip goes out."""

    def setUp(self):
        super().setUp()
        self.generate()
        self.record = self.records()["Rahim"]
        with use_company(self.company):
            self.employee.work_email = "rahim@acme.test"
            self.employee.save(update_fields=["work_email"])

    def send(self, user=None, pk=None):
        self.client.force_login(user or self.admin)
        return self.client.post(
            reverse("payroll:payslip_email", args=[pk or self.record.pk]), follow=True)

    def finalise(self):
        finalise_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)

    def test_a_finalised_payslip_goes_out_with_the_pdf_attached(self):
        self.finalise()
        page = self.send()
        self.assertContains(page, "Payslip emailed to rahim@acme.test")
        message = mail.outbox[-1]
        self.assertEqual(message.to, ["rahim@acme.test"])
        self.assertIn("August 2026", message.subject)
        self.assertIn("Rahim", message.body)
        name, content, kind = message.attachments[0]
        self.assertEqual(kind, "application/pdf")
        self.assertTrue(name.startswith("payslip-rahim-2026-08"))
        self.assertIn("Net pay", pdf_text(content))
        entry = AuditLog.objects.get(action="payslip.emailed")
        self.assertEqual(entry.after_data["to"], "rahim@acme.test")

    def test_a_draft_is_not_sent(self):
        page = self.send()
        self.assertContains(page, "not finalised yet")
        self.assertEqual(mail.outbox, [])

    def test_somebody_with_no_address_is_not_sent(self):
        self.finalise()
        with use_company(self.company):
            self.employee.work_email = ""
            self.employee.save(update_fields=["work_email"])
        page = self.send()
        self.assertContains(page, "has no email address")
        self.assertEqual(mail.outbox, [])

    @override_settings(MAIL_CONFIGURED=False)
    def test_nothing_is_sent_and_the_button_says_so_when_mail_is_not_set_up(self):
        self.finalise()
        page = self.send()
        self.assertContains(page, "Email is not set up on this server yet")
        self.assertEqual(mail.outbox, [])
        page = self.client.get(reverse("payroll:payslip", args=[self.record.pk]))
        self.assertContains(page, "Email is not set up on this server yet")
        self.assertNotContains(page, f'action="/salary/payslips/{self.record.pk}/email/"')

    def test_a_branch_manager_sends_their_own_branch_only(self):
        self.finalise()
        self.assertContains(self.send(self.manager), "Payslip emailed")
        far = self.records()["Karim"]
        self.assertEqual(self.client.post(
            reverse("payroll:payslip_email", args=[far.pk])).status_code, 403)

    def test_the_employee_page_never_offers_it(self):
        self.finalise()
        self.client.force_login(self.clerk_user)
        page = self.client.get(reverse("me:payslip", args=[self.records()["Clerk"].pk]))
        self.assertContains(page, "Download PDF")
        self.assertNotContains(page, "Email to employee")
