"""Penalty rules: finding occurrences, pricing them, and the versioned rules."""

import datetime
from decimal import Decimal
from types import SimpleNamespace

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from accounts.models import CompanyMembership, User
from attendance.models import AttendanceRecord
from auditlog.models import AuditLog
from common.tenant import use_company
from payroll import penalties
from payroll.models import AttendancePenaltyRule as Rule
from payroll.services import calculate_pay, expected_minutes
from tenants.services import onboard_company

S = AttendanceRecord.AttendanceStatus
SHIFT = SimpleNamespace(scheduled_minutes=540, break_is_paid=False, default_break_minutes=60)
UTC = datetime.timezone.utc


def day(n, status=S.PRESENT, late=0, worked=480, left_early=0, fraction="1"):
    date = datetime.date(2026, 9, n)
    end = datetime.datetime(2026, 9, n, 18, 0, tzinfo=UTC)
    return SimpleNamespace(
        work_date=date, attendance_status=status, late_minutes=late,
        worked_minutes=worked, payable_fraction=Decimal(fraction), leave_day=None,
        shift=SHIFT, scheduled_end_at=end,
        last_out_at=end - datetime.timedelta(minutes=left_early) if status == S.PRESENT else None,
    )


def rule(code="R", **values):
    fields = {
        "name": code.title(), "code": code, "version": 1, "metric": "late_minutes",
        "operator": "gte", "threshold_minutes": 10, "occurrence_mode": "single_day",
        "required_occurrences": 1, "deduction_method": "day_fraction",
        "deduction_value": Decimal("0.25"), "priority": 0, "exclusive_group": None,
        "maximum_deduction": None, "sequence_break_policy": {},
    }
    fields.update(values)
    return Rule(**fields)


def monthly_pay():
    # 30000 a month, 30-day basis: a day is 1000, an expected minute 1000/480.
    return penalties.PayValue("monthly", Decimal("30000"), Decimal("1000"), expected_minutes)


def amounts(found):
    return [o.amount for o in found]


class FindingPenaltiesTests(SimpleTestCase):
    def test_every_day_it_happens(self):
        month = [day(1, late=15), day(2, late=5), day(3, late=40)]
        found = penalties.assess([rule()], month, monthly_pay())
        self.assertEqual(amounts(found), [Decimal("250.00"), Decimal("250.00")])
        self.assertEqual([o.first.day for o in found], [1, 3])

    def test_every_three_days_in_a_month(self):
        month = [day(n, late=15) for n in range(1, 8)]  # 7 late days
        found = penalties.assess(
            [rule(occurrence_mode="within_period", required_occurrences=3,
                  deduction_method="full_day")],
            month, monthly_pay(),
        )
        self.assertEqual(amounts(found), [Decimal("1000.00"), Decimal("1000.00")])
        self.assertEqual([len(o.days) for o in found], [3, 3])

    def test_days_in_a_row_skip_weekly_offs_and_break_on_leave(self):
        month = [
            day(1, late=15), day(2, late=15),
            day(3, status=S.WEEKLY_OFF),          # skipped, the run continues
            day(4, late=15),                      # third in a row -> one penalty
            day(5, late=15), day(6, late=15),
            day(7, status=S.LEAVE, fraction="1"),  # breaks the run
            day(8, late=15),
            day(9, late=0),                       # on time: breaks too
            day(10, late=15),
        ]
        found = penalties.assess(
            [rule(occurrence_mode="consecutive_workdays", required_occurrences=3,
                  deduction_method="full_day")],
            month, monthly_pay(),
        )
        self.assertEqual([[r.work_date.day for r, _ in o.days] for o in found], [[1, 2, 4]])

    def test_a_group_counts_only_the_larger_deduction_each_day(self):
        month = [day(1, late=15), day(2, late=130)]
        small = rule("SMALL", exclusive_group="LATE")
        big = rule("BIG", threshold_minutes=120, deduction_method="full_day", exclusive_group="LATE")
        found = penalties.assess([small, big], month, monthly_pay())
        self.assertEqual(
            sorted((o.first.day, o.rule.code, o.amount) for o in found),
            [(1, "SMALL", Decimal("250.00")), (2, "BIG", Decimal("1000.00"))],
        )

    def test_without_a_group_both_rules_add_up(self):
        month = [day(2, late=130)]
        found = penalties.assess(
            [rule("SMALL"), rule("BIG", threshold_minutes=120, deduction_method="full_day")],
            month, monthly_pay(),
        )
        self.assertEqual(sum(amounts(found)), Decimal("1250.00"))

    def test_the_minutes_themselves(self):
        found = penalties.assess(
            [rule(deduction_method="actual_minutes")], [day(1, late=48)], monthly_pay(),
        )
        self.assertEqual(amounts(found), [Decimal("100.00")])  # 48 x 1000/480
        self.assertEqual(found[0].minutes, 48)

    def test_leaving_early_and_working_short(self):
        month = [day(1, left_early=30, worked=450), day(2, worked=400)]
        early = penalties.assess(
            [rule(metric="early_out_minutes", threshold_minutes=15, deduction_method="fixed_amount",
                  deduction_value=Decimal("50"))], month, monthly_pay(),
        )
        self.assertEqual([o.first.day for o in early], [1])
        short = penalties.assess(
            [rule(metric="worked_shortfall", threshold_minutes=60, deduction_method="fixed_amount",
                  deduction_value=Decimal("50"))], month, monthly_pay(),
        )
        self.assertEqual([o.first.day for o in short], [2])  # 80 minutes short

    def test_an_absent_day_rule(self):
        month = [day(1), day(2, status=S.ABSENT, worked=0), day(3, status=S.ABSENT, worked=0)]
        found = penalties.assess(
            [rule(metric="absence", threshold_minutes=None, deduction_method="fixed_amount",
                  deduction_value=Decimal("300"))], month, monthly_pay(),
        )
        self.assertEqual(amounts(found), [Decimal("300.00"), Decimal("300.00")])

    def test_a_rule_maximum_and_the_monthly_limit(self):
        month = [day(n, late=15) for n in range(1, 11)]  # 10 x 250 = 2500
        capped = penalties.assess([rule(maximum_deduction=Decimal("1000"))], month, monthly_pay())
        self.assertEqual(sum(amounts(capped)), Decimal("1000.00"))
        self.assertEqual(capped[0].details["capped"], "rule maximum")
        limited = penalties.assess(
            [rule()], month, monthly_pay(), gross=Decimal("30000"), max_percent=Decimal("5"),
        )
        self.assertEqual(sum(amounts(limited)), Decimal("1500.00"))  # 5% of 30000

    def test_a_waived_occurrence_is_skipped(self):
        month = [day(1, late=15), day(2, late=15)]
        found = penalties.assess([rule()], month, monthly_pay())
        waived = {f"7:{found[0].identity}"}
        again = penalties.assess([rule()], month, monthly_pay(), waived=waived, employee_key="7:")
        self.assertEqual([o.first.day for o in again], [2])

    def test_hourly_staff_lose_the_hours(self):
        pay = penalties.PayValue("hourly", Decimal("100"), None, expected_minutes)
        found = penalties.assess([rule(deduction_method="full_day")], [day(1, late=15)], pay)
        self.assertEqual(amounts(found), [Decimal("800.00")])  # 8 expected hours

    def test_penalties_become_payslip_lines(self):
        month = [day(n) for n in range(1, 21)] + [day(21, late=15)]
        result = calculate_pay("monthly", Decimal("30000"), month, penalty_rules=[rule()])
        [(index, occurrence)] = result["penalties"]
        self.assertEqual(result["lines"][index][1], "PENALTY")
        self.assertIn("21 Sep", result["lines"][index][2])
        self.assertEqual(result["net"], Decimal("29750.00"))


class PenaltyRuleServiceTests(TestCase):
    def setUp(self):
        self.company = onboard_company(code="ACME", slug="acme", name="Acme Ltd")
        self.admin = User.objects.create_user(email="admin@acme.test", password="pw")
        self.hr = User.objects.create_user(email="hr@acme.test", password="pw")
        for user, role in ((self.admin, "company_admin"), (self.hr, "hr")):
            CompanyMembership.all_objects.create(
                company=self.company, user=user, role=role,
                status=CompanyMembership.Status.ACTIVE,
            )

    def values(self, month=datetime.date(2026, 9, 1), **overrides):
        return {
            "effective_from": month, "name": "Late 10+", "metric": "late_minutes",
            "operator": "gte", "threshold_minutes": 10, "occurrence_mode": "single_day",
            "required_occurrences": 1, "deduction_method": "day_fraction",
            "deduction_value": Decimal("0.25"), "exclusive_group": "",
            "maximum_deduction": None, **overrides,
        }

    def create(self, **overrides):
        return penalties.create_penalty_rule(
            actor=self.admin, company_id=self.company.pk, values=self.values(**overrides)
        )

    def test_adding_a_rule(self):
        created = self.create()
        self.assertEqual((created.code, created.version, created.status), ("LATE_10", 1, "active"))
        self.assertEqual(penalties.rules_in_force(self.company.pk, datetime.date(2026, 9, 1)), [created])
        self.assertEqual(penalties.rules_in_force(self.company.pk, datetime.date(2026, 8, 1)), [])
        self.assertTrue(AuditLog.objects.filter(action="penalty_rule.created").exists())

    def test_changing_a_rule_keeps_the_old_version_for_earlier_months(self):
        first = self.create()
        second = penalties.change_penalty_rule(
            actor=self.admin, company_id=self.company.pk, rule_id=first.pk,
            values=self.values(datetime.date(2026, 11, 1), deduction_value=Decimal("0.5")),
        )
        first.refresh_from_db()
        self.assertEqual(first.effective_to, datetime.date(2026, 11, 1))
        self.assertEqual((second.code, second.version), (first.code, 2))
        self.assertEqual(penalties.rules_in_force(self.company.pk, datetime.date(2026, 10, 1)), [first])
        self.assertEqual(penalties.rules_in_force(self.company.pk, datetime.date(2026, 11, 1)), [second])

    def test_changing_from_the_same_month_replaces_the_version(self):
        first = self.create()
        penalties.change_penalty_rule(
            actor=self.admin, company_id=self.company.pk, rule_id=first.pk,
            values=self.values(deduction_value=Decimal("0.5")),
        )
        first.refresh_from_db()
        self.assertEqual(first.status, "retired")

    def test_a_change_before_a_later_change_is_refused(self):
        first = self.create()
        penalties.change_penalty_rule(
            actor=self.admin, company_id=self.company.pk, rule_id=first.pk,
            values=self.values(datetime.date(2026, 12, 1)),
        )
        with self.assertRaises(ValidationError) as caught:
            penalties.change_penalty_rule(
                actor=self.admin, company_id=self.company.pk, rule_id=first.pk,
                values=self.values(datetime.date(2026, 10, 1)),
            )
        self.assertIn("December 2026", str(caught.exception))

    def test_stopping_a_rule(self):
        created = self.create()
        penalties.stop_penalty_rule(
            actor=self.admin, company_id=self.company.pk, rule_id=created.pk,
            stops_from=datetime.date(2026, 12, 1),
        )
        self.assertEqual(penalties.rules_in_force(self.company.pk, datetime.date(2026, 11, 1)), [created])
        self.assertEqual(penalties.rules_in_force(self.company.pk, datetime.date(2026, 12, 1)), [])

    def test_inputs_must_agree(self):
        for overrides, field in (
            ({"threshold_minutes": None}, "threshold_minutes"),
            ({"occurrence_mode": "within_period", "required_occurrences": 1}, "required_occurrences"),
            ({"deduction_value": Decimal("0")}, "deduction_value"),
            ({"metric": "outside_minutes"}, "metric"),
            ({"effective_from": datetime.date(2026, 9, 5)}, "effective_from"),
        ):
            with self.subTest(field=field), self.assertRaises(ValidationError) as caught:
                self.create(**overrides)
            self.assertIn(field, caught.exception.message_dict)

    def test_an_absence_rule_needs_no_minutes(self):
        created = self.create(metric="absence", threshold_minutes=None,
                              deduction_method="full_day", deduction_value=None)
        self.assertIsNone(created.threshold_minutes)

    def test_hr_cannot_add_rules(self):
        with self.assertRaises(PermissionDenied):
            penalties.create_penalty_rule(
                actor=self.hr, company_id=self.company.pk, values=self.values()
            )

    def test_the_pages(self):
        self.client.force_login(self.admin)
        settings_page = self.client.get(reverse("payroll:salary_settings"))
        self.assertContains(settings_page, "No penalty rules")
        response = self.client.post(reverse("payroll:penalty_rule_create"), {
            "name": "Late 10+", "metric": "late_minutes", "operator": "gte",
            "threshold_minutes": "10", "occurrence_mode": "within_period",
            "required_occurrences": "3", "deduction_method": "full_day",
            "deduction_value": "", "applies_month": "9", "applies_year": "2026",
        })
        self.assertRedirects(response, reverse("payroll:salary_settings") + "#penalty-rules",
                             fetch_redirect_response=False)
        with use_company(self.company):
            created = Rule.objects.get()
        page = self.client.get(reverse("payroll:salary_settings"))
        self.assertContains(page, "Arriving late by at least 10 min, every 3 days in a month")
        self.assertContains(page, "A full day&#x27;s pay")
        for name in ("payroll:penalty_rule_change", "payroll:penalty_rule_stop"):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name, args=[created.pk])).status_code, 200)
        stop = self.client.post(reverse("payroll:penalty_rule_stop", args=[created.pk]), {
            "stops_month": "9", "stops_year": "2026",
        })
        self.assertEqual(stop.status_code, 302)
        created.refresh_from_db()
        self.assertEqual(created.status, "retired")

    def test_a_form_error_is_shown_next_to_its_field(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("payroll:penalty_rule_create"), {
            "name": "Late", "metric": "late_minutes", "operator": "gte",
            "threshold_minutes": "", "occurrence_mode": "single_day",
            "required_occurrences": "1", "deduction_method": "full_day",
            "applies_month": "9", "applies_year": "2026",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Say how many minutes.")
