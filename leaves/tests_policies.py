"""Leave policies, versions, the ledger and balances (Phase E2-E3, 2026-09-27;
docs/LEAVE_FULL_DESIGN.md §2-3).

Rahim and Clerk at Head Office (placed from 1 January 2026), Karim at
Chittagong. Casual leave (CAS).
"""

import datetime
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError

from common.tenant import use_company
from leaves import policies, policy_admin, services
from leaves.models import EmployeeLeavePolicy, LeaveLedgerEntry
from leaves.tests_branch_access import TwoBranchCase

D = datetime.date
Kind = LeaveLedgerEntry.Kind


class PolicyCase(TwoBranchCase):
    def rule(self, **values):
        return {"days_per_year": Decimal("12"), "accrual": "yearly", "carry_forward_days": None,
                "carry_forward_expires_months": None, "allow_half_day": True,
                "allow_hourly": True, "allow_negative": False, **values}

    def policy(self, code="STAFF", default=True, start=D(2026, 1, 1), **rule):
        policy = policy_admin.save_policy(actor=self.admin, company_id=self.company.pk, values={
            "code": code, "name": code.title(), "description": "", "is_default": default})
        policy_admin.add_version(actor=self.admin, company_id=self.company.pk,
                                 policy_id=policy.pk, effective_from=start, note="",
                                 rules={self.leave_type: self.rule(**rule)})
        return policy

    def post(self, until, employee=None):
        with use_company(self.company):
            policies.post(employee or self.employee, until=until)

    def entries(self, employee=None, **filters):
        with use_company(self.company):
            return list(LeaveLedgerEntry.objects.filter(employee=employee or self.employee,
                                                        **filters)
                        .order_by("entry_date", "pk").values_list("kind", "entry_date", "units"))

    def left(self, year, employee=None, as_of=None):
        with use_company(self.company):
            policies.post(employee or self.employee, until=as_of or D(year, 12, 31))
            return policies.balance(employee or self.employee, self.leave_type, year,
                                    as_of=as_of).left

    def take(self, start, end=None, employee=None, **values):
        return services.record_leave(actor=self.admin, company_id=self.company.pk, values={
            "employee": employee or self.employee, "leave_type": self.leave_type,
            "start_date": start, "end_date": end or start, "pay_type": "paid", "reason": "",
            **values})


class AccrualTests(PolicyCase):
    def test_yearly_all_on_the_first_day(self):
        self.policy()
        self.post(D(2026, 6, 30))
        self.assertEqual(self.entries(), [(Kind.ACCRUAL, D(2026, 1, 1), Decimal("12.00"))])
        self.post(D(2026, 9, 30))                                 # repeatable
        self.assertEqual(len(self.entries()), 1)

    def test_yearly_prorated_for_someone_who_joins_later(self):
        self.policy()
        with use_company(self.company):
            self.far.joining_date = D(2026, 4, 10)
            self.far.save(update_fields=["joining_date"])
        self.post(D(2026, 6, 30), employee=self.far)
        self.assertEqual(self.entries(self.far), [(Kind.ACCRUAL, D(2026, 4, 10), Decimal("9.00"))])

    def test_joining_after_the_fifteenth_starts_next_month(self):
        self.policy(accrual="monthly")
        with use_company(self.company):
            self.far.joining_date = D(2026, 4, 20)
            self.far.save(update_fields=["joining_date"])
        self.post(D(2026, 6, 30), employee=self.far)
        self.assertEqual([day for _k, day, _u in self.entries(self.far)],
                         [D(2026, 5, 1), D(2026, 6, 1)])

    def test_monthly_a_twelfth_each_month(self):
        self.policy(accrual="monthly", days_per_year=Decimal("18"))
        self.post(D(2026, 3, 31))
        self.assertEqual(self.entries(), [(Kind.ACCRUAL, D(2026, m, 1), Decimal("1.50"))
                                          for m in (1, 2, 3)])
        self.assertEqual(self.left(2026, as_of=D(2026, 3, 31)), Decimal("4.5"))


class CarryAndExpiryTests(PolicyCase):
    def test_carried_up_to_the_cap_then_expired_if_unused(self):
        self.policy(carry_forward_days=Decimal("5"), carry_forward_expires_months=3)
        self.take(D(2026, 8, 10), D(2026, 8, 12))                  # 3 of 12 taken
        self.post(D(2027, 1, 31))
        self.assertIn((Kind.CARRY_FORWARD, D(2027, 1, 1), Decimal("5.00")),
                      self.entries(year=2027))
        self.take(D(2027, 2, 1), D(2027, 2, 2))                    # 2 of the 5 used in time
        self.post(D(2027, 4, 30))
        self.assertIn((Kind.EXPIRY, D(2027, 4, 1), Decimal("-3.00")), self.entries(year=2027))
        # 12 given + 5 carried - 3 expired - 2 taken.
        self.assertEqual(self.left(2027), Decimal("12"))

    def test_carry_forward_follows_leave_cancelled_later(self):
        self.policy(carry_forward_days=Decimal("20"))
        leave = self.take(D(2026, 8, 10), D(2026, 8, 12))
        self.post(D(2027, 1, 31))
        self.assertIn((Kind.CARRY_FORWARD, D(2027, 1, 1), Decimal("9.00")),
                      self.entries(year=2027))
        services.cancel_leave(actor=self.admin, company_id=self.company.pk,
                              request_id=leave.pk, reason="Did not go")
        self.post(D(2027, 1, 31))
        self.assertIn((Kind.CARRY_FORWARD, D(2027, 1, 1), Decimal("12.00")),
                      self.entries(year=2027))

    def test_no_cap_no_carry(self):
        self.policy()
        self.post(D(2027, 1, 31))
        self.assertFalse(self.entries(kind=Kind.CARRY_FORWARD))


class CheckTests(PolicyCase):
    def test_more_than_the_balance_is_refused(self):
        self.policy(days_per_year=Decimal("2"))
        with self.assertRaisesMessage(ValidationError, "days left in 2026"):
            self.take(D(2026, 8, 10), D(2026, 8, 12))
        self.take(D(2026, 8, 10), D(2026, 8, 11))
        self.assertEqual(self.left(2026), Decimal("0"))

    def test_monthly_counts_what_is_earned_by_the_leave(self):
        self.policy(accrual="monthly", days_per_year=Decimal("12"))
        # By 10 August: 8 months earned.
        with self.assertRaisesMessage(ValidationError, "8 days left"):
            self.take(D(2026, 8, 3), D(2026, 8, 12))

    def test_the_policy_replaces_the_types_days_per_year(self):
        self.leave_type.days_per_year = Decimal("1")
        self.leave_type.save(update_fields=["days_per_year"])
        staff = self.policy(default=False, days_per_year=Decimal("10"))
        policies.assign_policy(actor=self.admin, company_id=self.company.pk,
                               employee=self.employee, policy=staff,
                               effective_from=D(2026, 1, 1))
        self.take(D(2026, 8, 10), D(2026, 8, 12))
        # Clerk has no policy: the type's days per year still decide.
        with self.assertRaisesMessage(ValidationError, "of 1 days left"):
            self.take(D(2026, 8, 10), D(2026, 8, 11), employee=self.clerk)

    def test_half_days_and_hours_as_the_rule_allows(self):
        self.policy(allow_half_day=False, allow_hourly=False)
        with self.assertRaisesMessage(ValidationError, "does not allow half days"):
            self.take(D(2026, 8, 10), duration="half_day")
        with self.assertRaisesMessage(ValidationError, "by the hour"):
            self.take(D(2026, 8, 10), duration="hourly", start_time=datetime.time(9),
                      end_time=datetime.time(11))

    def test_may_go_below_zero_when_allowed(self):
        self.policy(days_per_year=Decimal("1"), allow_negative=True)
        self.take(D(2026, 8, 10), D(2026, 8, 12))
        self.assertEqual(self.left(2026), Decimal("-2"))

    def test_a_request_and_its_approval_are_checked_too(self):
        from leaves import workflow

        self.policy(days_per_year=Decimal("1"))
        with self.assertRaisesMessage(ValidationError, "days left"):
            workflow.submit_request(actor=self.clerk_user, company_id=self.company.pk, values={
                "leave_type": self.leave_type, "start_date": D(2026, 8, 10),
                "end_date": D(2026, 8, 11), "pay_type": "paid", "reason": "x"})


class AssignAndAdjustTests(PolicyCase):
    def test_someones_own_policy_from_a_date(self):
        self.policy(code="STAFF", days_per_year=Decimal("12"))
        workers = self.policy(code="WORK", default=False, days_per_year=Decimal("6"))
        self.post(D(2026, 12, 31))
        policies.assign_policy(actor=self.admin, company_id=self.company.pk,
                               employee=self.employee, policy=workers,
                               effective_from=D(2026, 1, 1))
        self.post(D(2026, 12, 31))
        self.assertEqual(self.entries(kind=Kind.ACCRUAL),
                         [(Kind.ACCRUAL, D(2026, 1, 1), Decimal("6.00"))])
        self.assertEqual(policies.policy_of(self.employee, D(2026, 5, 1)), workers)
        # Back to the default from a later date: that year's yearly entitlement stays.
        policies.assign_policy(actor=self.admin, company_id=self.company.pk,
                               employee=self.employee, policy=None,
                               effective_from=D(2026, 7, 1))
        with use_company(self.company):
            row = EmployeeLeavePolicy.objects.get(employee=self.employee)
        self.assertEqual(row.effective_to, D(2026, 6, 30))

    def test_an_earlier_date_than_their_latest_is_refused(self):
        workers = self.policy(code="WORK", default=False)
        policies.assign_policy(actor=self.admin, company_id=self.company.pk,
                               employee=self.employee, policy=workers,
                               effective_from=D(2026, 6, 1))
        with self.assertRaisesMessage(ValidationError, "later date"):
            policies.assign_policy(actor=self.admin, company_id=self.company.pk,
                                   employee=self.employee, policy=None,
                                   effective_from=D(2026, 3, 1))

    def test_adjust_by_hand(self):
        self.policy()
        policies.adjust(actor=self.hr, company_id=self.company.pk, employee=self.employee,
                        leave_type=self.leave_type, year=2026, units=Decimal("2"),
                        note="Worked on a holiday")
        self.assertEqual(self.left(2026, as_of=D(2026, 12, 31)), Decimal("14"))
        with self.assertRaisesMessage(ValidationError, "Say why"):
            policies.adjust(actor=self.admin, company_id=self.company.pk,
                            employee=self.employee, leave_type=self.leave_type, year=2026,
                            units=Decimal("1"), note="")
        with self.assertRaises(PermissionDenied):
            policies.adjust(actor=self.manager, company_id=self.company.pk,
                            employee=self.employee, leave_type=self.leave_type, year=2026,
                            units=Decimal("1"), note="x")
        # Adjustments by hand survive a policy change.
        policies.assign_policy(actor=self.admin, company_id=self.company.pk,
                               employee=self.employee, policy=None, effective_from=D(2026, 1, 1))
        self.assertEqual(len(self.entries(kind=Kind.ADJUSTMENT)), 1)


class VersionTests(PolicyCase):
    def test_a_later_version_starts_when_it_says(self):
        today = D(2026, 9, 27)
        policy = self.policy(accrual="monthly", days_per_year=Decimal("12"))
        policy_admin.add_version(actor=self.admin, company_id=self.company.pk,
                                 policy_id=policy.pk, effective_from=D(2026, 11, 1), note="More",
                                 rules={self.leave_type: self.rule(accrual="monthly",
                                                                   days_per_year=Decimal("24"))},
                                 today=today)
        self.post(D(2026, 12, 31))
        units = [u for _k, _d, u in self.entries()]
        self.assertEqual(units, [Decimal("1.00")] * 10 + [Decimal("2.00")] * 2)

    def test_the_past_is_not_rewritten(self):
        today = D(2026, 9, 27)
        policy = self.policy()
        with self.assertRaisesMessage(ValidationError, "today or later"):
            policy_admin.add_version(actor=self.admin, company_id=self.company.pk,
                                     policy_id=policy.pk, effective_from=D(2026, 3, 1), note="",
                                     rules={self.leave_type: self.rule()}, today=today)
        with use_company(self.company):
            first = policy.versions.get()
        with self.assertRaisesMessage(ValidationError, "has started"):
            policy_admin.edit_version(actor=self.admin, company_id=self.company.pk,
                                      version_id=first.pk, effective_from=D(2027, 1, 1),
                                      note="", rules={self.leave_type: self.rule()}, today=today)

    def test_a_version_not_started_can_be_changed_and_removed(self):
        today = D(2026, 9, 27)
        policy = self.policy()
        later = policy_admin.add_version(
            actor=self.admin, company_id=self.company.pk, policy_id=policy.pk,
            effective_from=D(2027, 1, 1), note="", rules={self.leave_type: self.rule()},
            today=today)
        policy_admin.edit_version(actor=self.admin, company_id=self.company.pk,
                                  version_id=later.pk, effective_from=D(2027, 2, 1), note="x",
                                  rules={self.leave_type: self.rule(days_per_year=Decimal("15"))},
                                  today=today)
        policy_admin.delete_version(actor=self.admin, company_id=self.company.pk,
                                    version_id=later.pk, today=today)
        with use_company(self.company):
            self.assertEqual(policy.versions.count(), 1)

    def test_only_the_owner_or_admin_sets_up_policies(self):
        with self.assertRaises(PermissionDenied):
            policy_admin.save_policy(actor=self.hr, company_id=self.company.pk,
                                     values={"code": "X", "name": "X", "description": "",
                                             "is_default": False})


class NothingChangesWithoutAPolicyTests(PolicyCase):
    def test_no_policy_no_entries_and_the_old_allowance(self):
        self.leave_type.days_per_year = Decimal("2")
        self.leave_type.save(update_fields=["days_per_year"])
        with self.assertRaisesMessage(ValidationError, "of 2 days left"):
            self.take(D(2026, 8, 10), D(2026, 8, 12))
        self.post(D(2026, 12, 31))
        self.assertEqual(self.entries(), [])


class CommandTests(PolicyCase):
    def test_the_nightly_command_posts_and_can_run_again(self):
        from io import StringIO

        from django.core.management import call_command

        self.policy()
        out = StringIO()
        call_command("post_leave_accruals", stdout=out)
        call_command("post_leave_accruals", stdout=out)
        self.assertIn("brought up to", out.getvalue())
        self.assertEqual(len(self.entries(kind=Kind.ACCRUAL, year=2026)), 1)
