"""LFA through its pages (2026-09-28): every form submitted as the person who
uses it - the settings, the employee's claim, the decision, cancel, paid."""

import datetime
import shutil
import tempfile
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from common.tenant import use_company
from employees.models import Employee
from leaves.tests_branch_access import TwoBranchCase
from payroll.models import LfaClaim, LfaSettings

MEDIA = tempfile.mkdtemp(prefix="lfa-pages-")
THIS_MONTH = timezone.localdate().replace(day=1).isoformat()

SETTINGS = {
    "enabled": "1", "name": "Leave Fare Assistance", "description": "Once a year.",
    "amount_method": "basic_months", "fixed_amount": "", "months": "1",
    "max_amount": "", "min_service_months": "12", "probation_eligible": "0",
    "cycle": "calendar_year", "claims_per_cycle": "1", "requires_leave": "0",
    "min_leave_days": "", "requires_document": "0", "prorate_first_cycle": "0",
    "payment": "with_salary",
}


def pdf():
    return SimpleUploadedFile("ticket.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")


@override_settings(MEDIA_ROOT=MEDIA)
class PageCase(TwoBranchCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        super().setUp()
        with use_company(self.company):
            Employee.objects.update(joining_date=datetime.date(2020, 1, 1))
        self.clerk.refresh_from_db()

    def switch_on(self, **values):
        self.client.force_login(self.admin)
        return self.client.post(reverse("payroll:lfa_settings"), {**SETTINGS, **values})

    def clerk_claims(self, **extra):
        self.client.force_login(self.clerk_user)
        return self.client.post(reverse("me:lfa"), {"note": "Family trip", **extra})

    def claim(self):
        return LfaClaim.all_objects.get(employee=self.clerk)


class SettingsPageTests(PageCase):
    def test_the_company_fills_in_its_rules_and_switches_it_on(self):
        response = self.switch_on(requires_leave="1", min_leave_days="3",
                                  leave_types=[self.leave_type.pk], requires_document="1")
        self.assertRedirects(response, reverse("payroll:lfa_settings"))
        saved = LfaSettings.all_objects.get(company=self.company)
        self.assertTrue(saved.enabled and saved.requires_leave and saved.requires_document)
        self.assertEqual(saved.min_leave_days, Decimal("3"))
        with use_company(self.company):
            self.assertEqual(list(saved.leave_types.values_list("pk", flat=True)),
                             [self.leave_type.pk])
        page = self.client.get(reverse("payroll:lfa_settings"))
        self.assertContains(page, 'data-show-when="amount_method:fixed"')
        self.assertContains(page, 'data-show-when="amount_method:basic_months,gross_months"')
        self.assertContains(page, "Months of gross salary")

    def test_a_missing_number_is_said_on_the_form(self):
        page = self.switch_on(amount_method="fixed", fixed_amount="")
        self.assertContains(page, "Give the amount.")
        self.assertFalse(LfaSettings.all_objects.exists())

    def test_only_the_owner_or_admin(self):
        self.client.force_login(self.hr)
        self.assertEqual(self.client.get(reverse("payroll:lfa_settings")).status_code, 403)

    def test_the_menu(self):
        self.switch_on()
        page = self.client.get(reverse("payroll:lfa_claims"))
        sidebar = page.content.decode().split("</aside>", 1)[0]
        self.assertIn(reverse("payroll:lfa_settings"), sidebar)
        self.assertIn(reverse("payroll:lfa_claims"), sidebar)


class EmployeePageTests(PageCase):
    def test_no_my_lfa_until_it_is_on(self):
        self.client.force_login(self.clerk_user)
        self.assertNotContains(self.client.get(reverse("me:home")), reverse("me:lfa"))
        self.switch_on()
        self.client.force_login(self.clerk_user)
        self.assertContains(self.client.get(reverse("me:home")), reverse("me:lfa"))

    def test_claim_and_withdraw(self):
        self.switch_on()
        self.client.force_login(self.clerk_user)
        page = self.client.get(reverse("me:lfa"))
        self.assertContains(page, "You can claim it")
        self.assertContains(page, "30,000.00")
        self.assertRedirects(self.clerk_claims(), reverse("me:lfa"))
        claim = self.claim()
        self.assertEqual((claim.status, claim.note), ("pending", "Family trip"))
        self.assertContains(self.client.get(reverse("me:lfa")), "Already claimed 1 of 1")
        self.client.post(reverse("me:lfa_withdraw", args=[claim.pk]))
        claim.refresh_from_db()
        self.assertEqual(claim.status, "withdrawn")

    def test_proof_when_asked_for(self):
        self.switch_on(requires_document="1")
        page = self.clerk_claims()
        self.assertContains(page, 'data-open-on-load="claim-dialog"')
        self.assertFalse(LfaClaim.all_objects.exists())
        self.clerk_claims(document=pdf())
        claim = self.claim()
        self.assertEqual(claim.document_name, "ticket.pdf")
        reply = self.client.get(reverse("me:lfa_document", args=[claim.pk]))
        self.assertEqual(reply["Content-Type"], "application/pdf")
        for closer in reply._resource_closers:
            closer()
        self.client.force_login(self.hr)
        self.assertEqual(self.client.get(
            reverse("payroll:lfa_document", args=[claim.pk])).status_code, 403)


class DecisionPageTests(PageCase):
    def test_the_manager_approves_it_onto_a_payslip(self):
        self.switch_on()
        self.clerk_claims()
        claim = self.claim()
        self.client.force_login(self.manager)
        listing = self.client.get(reverse("payroll:lfa_claims"))
        self.assertContains(listing, reverse("payroll:lfa_claim", args=[claim.pk]))
        refused = self.client.post(reverse("payroll:lfa_claim", args=[claim.pk]),
                                   {"decision": "reject", "note": ""})
        self.assertContains(refused, "Say why it is rejected")
        response = self.client.post(reverse("payroll:lfa_claim", args=[claim.pk]), {
            "decision": "approve", "amount": "30000", "pay_month": THIS_MONTH, "note": ""})
        self.assertRedirects(response, reverse("payroll:lfa_claim", args=[claim.pk]))
        claim.refresh_from_db()
        self.assertEqual(claim.status, "approved")
        self.assertEqual(claim.payroll_adjustment.target_payroll_period.start_date.isoformat(),
                         THIS_MONTH)
        page = self.client.get(reverse("payroll:lfa_claim", args=[claim.pk]))
        self.assertContains(page, 'id="cancel-dialog"')
        response = self.client.post(reverse("payroll:lfa_cancel", args=[claim.pk]),
                                    {"note": "Entered twice"})
        self.assertRedirects(response, reverse("payroll:lfa_claim", args=[claim.pk]))
        claim.refresh_from_db()
        self.assertEqual(claim.status, "cancelled")

    def test_paid_separately_is_marked_paid_in_its_modal(self):
        self.switch_on(payment="separately")
        self.clerk_claims()
        claim = self.claim()
        self.client.force_login(self.admin)
        self.client.post(reverse("payroll:lfa_claim", args=[claim.pk]),
                         {"decision": "approve", "amount": "30000", "note": ""})
        refused = self.client.post(reverse("payroll:lfa_paid", args=[claim.pk]),
                                   {"paid_on": "", "reference": ""})
        self.assertContains(refused, 'data-open-on-load="paid-dialog"')
        self.client.post(reverse("payroll:lfa_paid", args=[claim.pk]),
                         {"paid_on": "2026-08-25", "reference": "TRX-9"})
        claim.refresh_from_db()
        self.assertEqual((claim.status, claim.payment_reference), ("paid", "TRX-9"))

    def test_another_branchs_manager_does_not_see_it(self):
        self.switch_on()
        self.clerk_claims()
        far_manager = self.member("far@liv.test", "manager", branches=[self.unit])
        self.client.force_login(far_manager)
        self.assertEqual(self.client.get(
            reverse("payroll:lfa_claim", args=[self.claim().pk])).status_code, 404)

    def test_enter_a_claim_for_someone(self):
        self.switch_on()
        self.client.force_login(self.admin)
        page = self.client.get(reverse("payroll:lfa_claim_new"), {"employee": self.far.pk})
        self.assertContains(page, "Karim can claim it")
        response = self.client.post(reverse("payroll:lfa_claim_new"),
                                    {"employee": self.far.pk, "note": "No login"})
        claim = LfaClaim.all_objects.get(employee=self.far)
        self.assertRedirects(response, reverse("payroll:lfa_claim", args=[claim.pk]))
        self.assertEqual(claim.submitted_by, self.admin)
