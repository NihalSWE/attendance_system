"""Leave policies and balances through their pages (Phase E4, 2026-09-27):
every form submitted as the person who uses it."""

import datetime
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from common.tenant import use_company
from leaves import policy_admin
from leaves.models import (
    EmployeeLeavePolicy,
    LeaveLedgerEntry,
    LeavePolicy,
    LeavePolicyVersion,
)
from leaves.tests_branch_access import TwoBranchCase

TODAY = timezone.localdate()
THIS_YEAR = datetime.date(TODAY.year, 1, 1)


class PageCase(TwoBranchCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def rule_data(self, leave_type=None, **values):
        prefix = f"t{(leave_type or self.leave_type).pk}"
        data = {"include": "on", "days_per_year": "12", "accrual": "yearly",
                "carry_forward_days": "", "carry_forward_expires_months": "",
                "allow_half_day": "on", "allow_hourly": "on", **values}
        return {f"{prefix}-{key}": value for key, value in data.items() if value is not None}

    def staff(self, default=True):
        policy = policy_admin.save_policy(actor=self.admin, company_id=self.company.pk, values={
            "code": "STAFF", "name": "Staff", "description": "", "is_default": default})
        policy_admin.add_version(
            actor=self.admin, company_id=self.company.pk, policy_id=policy.pk,
            effective_from=THIS_YEAR, note="", rules={self.leave_type: {
                "days_per_year": Decimal("12"), "accrual": "yearly"}})
        return policy


class PolicyPagesTests(PageCase):
    def test_add_a_policy_and_its_first_version(self):
        response = self.client.post(reverse("leaves:leave_policy_create"), {
            "code": "staff", "name": "Staff", "description": "Office staff", "is_default": "on"})
        self.assertRedirects(response, reverse("leaves:leave_policy_list"))
        with use_company(self.company):
            policy = LeavePolicy.objects.get(code="STAFF")
        self.assertTrue(policy.is_default)
        page = self.client.get(reverse("leaves:leave_policy_version_create", args=[policy.pk]))
        self.assertContains(page, "Its first version may start on any date")
        response = self.client.post(
            reverse("leaves:leave_policy_version_create", args=[policy.pk]),
            {"effective_from": THIS_YEAR.isoformat(), "note": "Start",
             **self.rule_data(carry_forward_days="5", carry_forward_expires_months="3")})
        self.assertRedirects(response, reverse("leaves:leave_policy_detail", args=[policy.pk]))
        with use_company(self.company):
            rule = LeavePolicyVersion.objects.get(policy=policy).rules.get()
        self.assertEqual((rule.days_per_year, rule.carry_forward_days,
                          rule.carry_forward_expires_months, rule.allow_negative),
                         (Decimal("12"), Decimal("5"), 3, False))
        detail = self.client.get(reverse("leaves:leave_policy_detail", args=[policy.pk]))
        self.assertContains(detail, "In force")
        self.assertContains(detail, "Up to 5, expire after 3 months")

    def test_a_rule_without_days_is_refused_on_the_page(self):
        policy = policy_admin.save_policy(actor=self.admin, company_id=self.company.pk, values={
            "code": "X", "name": "X", "description": "", "is_default": False})
        page = self.client.post(reverse("leaves:leave_policy_version_create", args=[policy.pk]),
                                {"effective_from": THIS_YEAR.isoformat(), "note": "",
                                 **self.rule_data(days_per_year="")})
        self.assertContains(page, "Give the days per year.")
        page = self.client.post(reverse("leaves:leave_policy_version_create", args=[policy.pk]),
                                {"effective_from": THIS_YEAR.isoformat(), "note": ""})
        self.assertContains(page, "Give at least one leave type a rule.")

    def test_a_later_version_changed_and_removed_on_its_pages(self):
        policy = self.staff()
        later = datetime.date(TODAY.year + 1, 1, 1)
        self.client.post(reverse("leaves:leave_policy_version_create", args=[policy.pk]),
                         {"effective_from": later.isoformat(), "note": "",
                          **self.rule_data(days_per_year="15")})
        with use_company(self.company):
            version = LeavePolicyVersion.objects.get(policy=policy, number=2)
        self.client.post(reverse("leaves:leave_policy_version_edit", args=[version.pk]),
                         {"effective_from": later.isoformat(), "note": "Raised",
                          **self.rule_data(days_per_year="18")})
        with use_company(self.company):
            self.assertEqual(version.rules.get().days_per_year, Decimal("18"))
        self.client.post(reverse("leaves:leave_policy_version_delete", args=[version.pk]))
        with use_company(self.company):
            self.assertFalse(LeavePolicyVersion.objects.filter(pk=version.pk).exists())
        # One that has started is refused.
        with use_company(self.company):
            first = LeavePolicyVersion.objects.get(policy=policy, number=1)
        page = self.client.get(reverse("leaves:leave_policy_version_edit", args=[first.pk]),
                               follow=True)
        self.assertContains(page, "stays as it is")

    def test_edit_and_turn_off(self):
        policy = self.staff(default=False)
        self.client.post(reverse("leaves:leave_policy_edit", args=[policy.pk]), {
            "code": "STAFF", "name": "Office staff", "description": "", "is_default": ""})
        self.client.post(reverse("leaves:leave_policy_status", args=[policy.pk]),
                         {"status": "inactive"})
        policy.refresh_from_db()
        self.assertEqual((policy.name, policy.status), ("Office staff", "inactive"))

    def test_only_the_owner_or_admin(self):
        self.client.force_login(self.hr)
        self.assertEqual(self.client.get(reverse("leaves:leave_policy_list")).status_code, 403)
        self.client.force_login(self.admin)
        page = self.client.get(reverse("leaves:leave_policy_list"))
        self.assertEqual(page.status_code, 200)
        sidebar = page.content.decode().split("</aside>", 1)[0]
        self.assertIn(reverse("leaves:leave_policy_list"), sidebar)
        self.assertIn(reverse("leaves:leave_balance_list"), sidebar)


class BalancePagesTests(PageCase):
    def test_the_balances_page(self):
        self.staff()
        page = self.client.get(reverse("leaves:leave_balance_list"))
        self.assertEqual(page.status_code, 200)
        names = [row[1] for row in page.context["page"]]
        self.assertIn("Rahim", names)
        self.assertIn("Karim", names)
        self.client.force_login(self.manager)
        page = self.client.get(reverse("leaves:leave_balance_list"))
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("Karim", [row[1] for row in page.context["page"]])

    def test_the_employee_sees_their_balance(self):
        self.staff()
        self.client.force_login(self.clerk_user)
        page = self.client.get(reverse("me:leave"))
        self.assertContains(page, "Balance in")
        self.assertContains(page, "Staff")


class ProfileTests(PageCase):
    def profile(self):
        return reverse("organization:employee_detail", args=[self.employee.pk])

    def test_the_leave_tab_shows_the_balance_and_its_modals(self):
        self.staff()
        page = self.client.get(self.profile())
        self.assertContains(page, "Balance in")
        self.assertContains(page, 'id="leave_policy-dialog"')
        self.assertContains(page, 'id="adjust-dialog"')
        self.assertEqual(page.context["leave_policy"].code, "STAFF")

    def test_give_a_policy_in_the_modal(self):
        self.staff()
        workers = policy_admin.save_policy(actor=self.admin, company_id=self.company.pk,
                                           values={"code": "WORK", "name": "Workers",
                                                   "description": "", "is_default": False})
        response = self.client.post(
            reverse("organization:employee_leave_policy", args=[self.employee.pk]),
            {"policy": workers.pk, "effective_from": TODAY.isoformat()})
        self.assertRedirects(response, self.profile() + "#leave", fetch_redirect_response=False)
        with use_company(self.company):
            self.assertEqual(EmployeeLeavePolicy.objects.get(employee=self.employee).policy,
                             workers)

    def test_adjust_in_the_modal_and_a_refused_one_reopens(self):
        self.staff()
        url = reverse("organization:employee_leave_adjust", args=[self.employee.pk])
        refused = self.client.post(url, {"leave_type": self.leave_type.pk,
                                         "year": TODAY.year, "units": "2", "note": ""})
        self.assertContains(refused, 'data-open-on-load="adjust-dialog"')
        self.client.force_login(self.hr)
        response = self.client.post(url, {"leave_type": self.leave_type.pk, "year": TODAY.year,
                                          "units": "-1.5", "note": "Took a day off unrecorded"})
        self.assertEqual(response.status_code, 302)
        with use_company(self.company):
            self.assertEqual(LeaveLedgerEntry.objects.get(kind="adjustment").units,
                             Decimal("-1.50"))

    def test_a_branch_manager_cannot(self):
        self.staff()
        self.client.force_login(self.manager)
        page = self.client.get(self.profile())
        self.assertNotContains(page, 'id="adjust-dialog"')
        response = self.client.post(
            reverse("organization:employee_leave_adjust", args=[self.employee.pk]),
            {"leave_type": self.leave_type.pk, "year": TODAY.year, "units": "5", "note": "x"})
        self.assertEqual(response.status_code, 403)
