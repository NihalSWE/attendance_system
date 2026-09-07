"""Company onboarding service tests: atomicity and idempotency."""

from django.core.exceptions import ValidationError
from django.test import TestCase

from common.tenant import use_company
from organization.models import Branch
from scheduling.models import CompanyAttendanceSettings
from tenants.models import Company
from tenants.services import onboard_company


class CompanyOnboardingTests(TestCase):
    def test_onboarding_creates_company_branch_and_settings(self):
        company = onboard_company(code="ACME", slug="acme", name="Acme Ltd")
        with use_company(company):
            branch = Branch.objects.get()
            settings_row = CompanyAttendanceSettings.objects.get()
        self.assertTrue(branch.is_default)
        self.assertEqual(branch.code, "HQ")
        self.assertEqual(settings_row.company_id, company.pk)

    def test_onboarding_is_idempotent_on_code(self):
        # A retried onboarding (e.g. after a timeout) must not duplicate anything.
        first = onboard_company(code="ACME", slug="acme", name="Acme Ltd")
        second = onboard_company(code="ACME", slug="acme-2", name="Acme Ltd")
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Company.objects.count(), 1)
        with use_company(first):
            self.assertEqual(Branch.objects.count(), 1)
            self.assertEqual(CompanyAttendanceSettings.objects.count(), 1)

    def test_failure_rolls_back_the_whole_onboarding(self):
        # The branch code exceeds max_length, so branch creation fails *after*
        # the company row was written. Atomicity must undo the company too.
        with self.assertRaises(ValidationError):
            onboard_company(
                code="BAD", slug="bad", name="Bad Co",
                default_branch_code="X" * 100,
            )
        self.assertEqual(Company.objects.count(), 0)
        self.assertEqual(Branch.all_objects.count(), 0)

    def test_settings_default_to_department_shift_mode(self):
        # Single-shift mode would require a shift, which does not exist yet.
        company = onboard_company(code="ACME", slug="acme", name="Acme Ltd")
        with use_company(company):
            settings_row = CompanyAttendanceSettings.objects.get()
        self.assertEqual(settings_row.shift_mode, "department_shifts")

    def test_two_companies_onboard_independently(self):
        a = onboard_company(code="A", slug="a", name="Company A")
        b = onboard_company(code="B", slug="b", name="Company B")
        with use_company(a):
            self.assertEqual(Branch.objects.count(), 1)
        with use_company(b):
            self.assertEqual(Branch.objects.count(), 1)
        self.assertEqual(Branch.all_objects.count(), 2)
