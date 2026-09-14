"""Overtime (plan step A9): deciding it, keeping the decision, paying it."""

import datetime
from decimal import Decimal
from types import SimpleNamespace

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import SimpleTestCase
from django.urls import reverse

from accounts.models import CompanyMembership, User
from attendance.models import AttendanceRecord, ReviewStatus
from attendance.services import recalculate
from attendance.tests_live import DHAKA, LiveTestCase
from auditlog.models import AuditLog
from common.tenant import use_company
from payroll import overtime
from payroll.models import OvertimeDecision, PayrollPeriod, PayrollPolicyVersion, PayrollRun
from payroll.policy import SalaryRules, STANDARD_RULES, change_salary_rules, rules_for
from payroll.services import calculate_pay, generate_payroll
from scheduling import services as schedule

MONDAY = datetime.date(2026, 8, 10)
FRIDAY = datetime.date(2026, 8, 14)
LATER = datetime.datetime(2026, 9, 1, 12, tzinfo=DHAKA)


def rules(**changes):
    version = PayrollPolicyVersion(**changes)
    return SalaryRules.from_version(version)


class PayableMinutesTests(SimpleTestCase):
    def test_standard_rules_pay_double_and_every_minute(self):
        self.assertEqual(STANDARD_RULES.overtime_multiplier, Decimal("2"))
        self.assertEqual(STANDARD_RULES.day_off_multiplier, Decimal("2"))
        self.assertEqual(STANDARD_RULES.payable_overtime(7), 7)

    def test_minimum_and_rounding(self):
        company = rules(minimum_overtime_minutes=30, overtime_rounding_minutes=15)
        self.assertEqual(company.payable_overtime(20), 0)
        self.assertEqual(company.payable_overtime(95), 90)
        self.assertEqual(company.payable_overtime(0), 0)

    def test_no_overtime_pay(self):
        self.assertEqual(rules(overtime_method="none").payable_overtime(120), 0)


SHIFT = SimpleNamespace(scheduled_minutes=540, break_is_paid=False, default_break_minutes=60)


def day(approved, status="present", on=MONDAY):
    return SimpleNamespace(
        attendance_status=status, payable_fraction=Decimal("1"), worked_minutes=480,
        late_minutes=0, leave_day=None, shift=SHIFT, work_date=on,
        approved_overtime_minutes=approved,
    )


def line(result, code):
    return next((l for l in result["lines"] if l[1] == code), None)


class OvertimePayTests(SimpleTestCase):
    def test_monthly_overtime_is_a_days_pay_over_shift_hours_times_two(self):
        # 30,000 / 30 = 1,000 a day; 8 paid hours -> 125 an hour; 90 min x2.
        result = calculate_pay("monthly", "30000", [day(90)])
        overtime_line = line(result, "OVERTIME")
        self.assertEqual(overtime_line[5], Decimal("375.00"))
        self.assertEqual(overtime_line[3], Decimal("1.50"))
        self.assertEqual(result["gross"], Decimal("30375.00"))
        self.assertEqual(result["overtime"][0]["paid_minutes"], 90)

    def test_day_off_work_uses_its_own_multiplier(self):
        result = calculate_pay(
            "monthly", "30000", [day(0), day(60, status="weekly_off", on=FRIDAY)],
            rules=rules(holiday_overtime_multiplier=Decimal("3")),
        )
        self.assertIsNone(line(result, "OVERTIME"))
        self.assertEqual(line(result, "DAY_OFF_WORK")[5], Decimal("375.00"))

    def test_hourly_and_daily_staff(self):
        self.assertEqual(line(calculate_pay("hourly", "100", [day(30)]), "OVERTIME")[5], Decimal("100.00"))
        # 800 a day / 8 hours = 100 an hour; 60 min x2.
        self.assertEqual(line(calculate_pay("daily", "800", [day(60)]), "OVERTIME")[5], Decimal("200.00"))

    def test_under_the_minimum_pays_nothing(self):
        result = calculate_pay("monthly", "30000", [day(20)], rules=rules(minimum_overtime_minutes=30))
        self.assertIsNone(line(result, "OVERTIME"))

    def test_unapproved_overtime_pays_nothing(self):
        self.assertIsNone(line(calculate_pay("monthly", "30000", [day(0)]), "OVERTIME"))


class OvertimeBase(LiveTestCase):
    def work(self, day, *times):
        for hour, minute in times:
            self.punch(day, hour, minute)
        recalculate(self.company.pk, start=day, end=day, now=LATER)
        return self.record(day)

    def decide(self, record, **kwargs):
        kwargs.setdefault("approve", True)
        return overtime.decide_overtime(
            actor=kwargs.pop("actor", self.admin), company_id=self.company.pk,
            record_id=record.pk, **kwargs,
        )


class DecidingTests(OvertimeBase):
    def test_staying_late_and_scanning_out_is_approved_automatically(self):
        # Ajay, 2026-09-14: scanned out means the time is known; nobody approves it.
        record = self.work(MONDAY, (9, 0), (20, 0))
        self.assertEqual(
            (record.calculated_overtime_minutes, record.approved_overtime_minutes), (120, 120)
        )
        page = overtime.overtime_month(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        [row] = page["rows"]
        self.assertEqual((row.state, row.approved_minutes, row.paid_minutes), ("automatic", 120, 120))
        self.assertEqual(overtime.undecided_count(self.company.pk, MONDAY.replace(day=1),
                                                  datetime.date(2026, 8, 31)), 0)

    def test_a_day_still_running_approves_nothing_yet(self):
        self.punch(MONDAY, 9)
        self.punch(MONDAY, 20)
        recalculate(self.company.pk, start=MONDAY, end=MONDAY,
                    now=datetime.datetime(2026, 8, 10, 21, tzinfo=DHAKA))
        self.assertEqual(self.record(MONDAY).approved_overtime_minutes, 0)

    def test_overtime_too_short_to_pay_does_not_wait(self):
        change_salary_rules(actor=self.admin, company_id=self.company.pk, values={
            "effective_from": datetime.date(2026, 8, 1), "minimum_overtime_minutes": 60,
        })
        self.work(MONDAY, (9, 0), (18, 40))              # 40 min: under the minimum
        self.work(datetime.date(2026, 8, 11), (9, 0), (19, 30))  # 90 min: paid
        page = overtime.overtime_month(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        self.assertEqual(sorted(row.state for row in page["rows"]), ["automatic", "too_short"])
        self.assertEqual(overtime.undecided_count(self.company.pk, datetime.date(2026, 8, 1),
                                                  datetime.date(2026, 8, 31)), 0)

    def test_approving_fewer_minutes_survives_recalculation(self):
        record = self.work(MONDAY, (9, 0), (20, 0))
        self.decide(record, minutes=90, note="Stock count")
        self.assertEqual(self.record(MONDAY).approved_overtime_minutes, 90)
        # Attendance rewrites the day on its own; the approval stays.
        recalculate(self.company.pk, start=MONDAY, end=MONDAY, now=LATER)
        self.assertEqual(self.record(MONDAY).approved_overtime_minutes, 90)
        with use_company(self.company):
            self.assertTrue(AuditLog.objects.filter(action="overtime.approved").exists())

    def test_cannot_approve_more_than_was_counted(self):
        record = self.work(MONDAY, (9, 0), (20, 0))
        with self.assertRaises(ValidationError) as caught:
            self.decide(record, minutes=121)
        self.assertIn("minutes", caught.exception.error_dict)

    def test_rejecting_pays_nothing_and_undo_goes_back_to_automatic(self):
        record = self.work(MONDAY, (9, 0), (20, 0))
        self.decide(record, approve=False)
        self.assertEqual(self.record(MONDAY).approved_overtime_minutes, 0)
        with use_company(self.company):
            self.assertEqual(OvertimeDecision.objects.get().status, "rejected")
        state = overtime.undo_overtime_decision(
            actor=self.admin, company_id=self.company.pk, record_id=record.pk
        )
        self.assertEqual(state, "automatic")
        self.assertEqual(self.record(MONDAY).approved_overtime_minutes, 120)
        with use_company(self.company):
            self.assertFalse(OvertimeDecision.objects.exists())

    def test_a_day_without_overtime_cannot_be_decided(self):
        record = self.work(MONDAY, (9, 0), (18, 0))
        with self.assertRaises(ValidationError):
            self.decide(record, minutes=10)

    def test_overtime_after_delays_the_start(self):
        self.shift.overtime_after_minutes = 30
        with use_company(self.company):
            self.shift.save()
        record = self.work(MONDAY, (9, 0), (20, 0))
        self.assertEqual(record.calculated_overtime_minutes, 90)


class OpenSessionTests(OvertimeBase):
    """Came back after the shift and never scanned out."""

    def test_approver_sets_the_time_they_left(self):
        record = self.work(MONDAY, (9, 0), (18, 0), (19, 0))
        with use_company(self.company):
            claim = overtime.claim_for(record)
        self.assertIsNotNone(claim.open_from)
        self.assertEqual(record.review_status, ReviewStatus.NEEDS_REVIEW)

        with self.assertRaises(ValidationError) as caught:
            self.decide(record)
        self.assertIn("check_out", caught.exception.error_dict)

        decision = self.decide(record, check_out=datetime.time(21, 30))
        self.assertEqual(decision.approved_minutes, 150)
        self.assertEqual(decision.check_out_at, datetime.datetime(2026, 8, 10, 21, 30, tzinfo=DHAKA))
        stored = self.record(MONDAY)
        self.assertEqual((stored.approved_overtime_minutes, stored.review_status), (150, ReviewStatus.REVIEWED))

        recalculate(self.company.pk, start=MONDAY, end=MONDAY, now=LATER)
        stored = self.record(MONDAY)
        self.assertEqual((stored.approved_overtime_minutes, stored.review_status), (150, ReviewStatus.REVIEWED))

        overtime.undo_overtime_decision(actor=self.admin, company_id=self.company.pk, record_id=record.pk)
        stored = self.record(MONDAY)
        self.assertEqual((stored.approved_overtime_minutes, stored.review_status), (0, ReviewStatus.NEEDS_REVIEW))

    def test_a_time_after_midnight_is_the_next_morning(self):
        record = self.work(MONDAY, (9, 0), (18, 0), (22, 0))
        decision = self.decide(record, check_out=datetime.time(1, 0))
        self.assertEqual(decision.approved_minutes, 180)

    def test_a_time_before_overtime_starts_is_refused(self):
        self.shift.overtime_after_minutes = 120
        with use_company(self.company):
            self.shift.save()
        record = self.work(MONDAY, (9, 0), (18, 0), (18, 30))
        with self.assertRaises(ValidationError) as caught:
            self.decide(record, check_out=datetime.time(19, 30))
        self.assertIn("check_out", caught.exception.error_dict)


class DayOffTests(OvertimeBase):
    def test_every_minute_on_a_weekly_off_counts(self):
        schedule.add_weekly_offs(
            actor=self.admin, company_id=self.company.pk,
            values={"branch": None, "weekdays": [4], "is_paid": True,
                    "effective_from": datetime.date(2026, 1, 1)},
        )
        record = self.work(FRIDAY, (10, 0), (14, 0))
        with use_company(self.company):
            claim = overtime.claim_for(record)
        self.assertEqual((claim.day_off, claim.minutes), (True, 240))
        # Scanned in and out: approved automatically, like any overtime.
        self.assertEqual(record.approved_overtime_minutes, 240)
        self.decide(record, minutes=180)
        self.assertEqual(self.record(FRIDAY).approved_overtime_minutes, 180)


class SalaryTests(OvertimeBase):
    def test_generated_salary_pays_approved_overtime(self):
        record = self.work(MONDAY, (9, 0), (20, 0))
        self.decide(record, minutes=90)
        run = generate_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        with use_company(self.company):
            payslip = run.records.get(employee=self.employee)
            lines = {l.code: l for l in payslip.lines.all()}
        # 30,000 / 30 = 1,000 a day; a 9-hour shift -> 111.11 an hour; 1.5 h x2.
        self.assertEqual(lines["OVERTIME"].amount, Decimal("333.33"))
        self.assertEqual(payslip.calculation_snapshot["overtime"][0]["paid_minutes"], 90)

    def test_salary_pays_automatic_overtime_without_anyone_approving(self):
        self.work(MONDAY, (9, 0), (20, 0))
        run = generate_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        with use_company(self.company):
            line = run.records.get(employee=self.employee).lines.get(code="OVERTIME")
        # 2 h x 111.11 x 2.
        self.assertEqual(line.amount, Decimal("444.44"))

    def test_an_open_session_pays_nothing_until_decided(self):
        self.work(MONDAY, (9, 0), (18, 0), (19, 0))
        run = generate_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        with use_company(self.company):
            self.assertFalse(
                run.records.get(employee=self.employee).lines.filter(code="OVERTIME").exists()
            )
        self.assertEqual(overtime.undecided_count(self.company.pk, datetime.date(2026, 8, 1),
                                                  datetime.date(2026, 8, 31)), 1)

    def test_a_finalised_month_is_closed(self):
        record = self.work(MONDAY, (9, 0), (20, 0))
        with use_company(self.company):
            period = PayrollPeriod.objects.create(
                company=self.company, name="Aug", start_date=datetime.date(2026, 8, 1),
                end_date=datetime.date(2026, 8, 31),
            )
            PayrollRun.objects.create(company=self.company, payroll_period=period,
                                      status=PayrollRun.Status.POSTED)
        with self.assertRaises(ValidationError):
            self.decide(record, minutes=60)

    def test_salary_rules_save_the_overtime_settings(self):
        change_salary_rules(actor=self.admin, company_id=self.company.pk, values={
            "effective_from": datetime.date(2026, 8, 1),
            "overtime_multiplier": Decimal("1.5"),
            "holiday_overtime_multiplier": Decimal("2.5"),
            "minimum_overtime_minutes": 30,
            "overtime_rounding_minutes": 15,
        })
        found = rules_for(self.company.pk, datetime.date(2026, 8, 1))
        self.assertEqual(
            (found.overtime_multiplier, found.day_off_multiplier, found.overtime_minimum, found.overtime_step),
            (Decimal("1.5"), Decimal("2.5"), 30, 15),
        )

    def test_an_unknown_rounding_step_is_refused(self):
        with self.assertRaises(ValidationError):
            change_salary_rules(actor=self.admin, company_id=self.company.pk, values={
                "effective_from": datetime.date(2026, 8, 1), "overtime_rounding_minutes": 7,
            })


class AccessTests(OvertimeBase):
    def member(self, email, role):
        user = User.objects.create_user(email=email, password="pw")
        CompanyMembership.all_objects.create(
            company=self.company, user=user, role=role, status=CompanyMembership.Status.ACTIVE,
        )
        return user

    def test_hr_decides_and_a_manager_cannot(self):
        record = self.work(MONDAY, (9, 0), (20, 0))
        hr = self.member("hr@liv.test", CompanyMembership.Role.HR)
        self.decide(record, minutes=60, actor=hr)
        manager = self.member("boss@liv.test", CompanyMembership.Role.MANAGER)
        with self.assertRaises(PermissionDenied):
            self.decide(record, minutes=60, actor=manager)


class ScreenTests(OvertimeBase):
    def test_list_decide_and_salary_notice(self):
        record = self.work(MONDAY, (9, 0), (20, 0))
        self.client.force_login(self.admin)
        page = self.client.get(reverse("payroll:overtime_list"), {"month": 8, "year": 2026})
        self.assertContains(page, "Rahim")
        self.assertContains(page, "2h 0m")
        self.assertContains(page, "Approved automatically")
        day_page = self.client.get(reverse("payroll:overtime_decide", args=[record.pk]))
        self.assertContains(day_page, "Nobody needs to approve it")

        generate_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        response = self.client.post(
            reverse("payroll:overtime_decide", args=[record.pk]),
            {"decision": "approve", "minutes": "60", "note": ""},
        )
        self.assertRedirects(response, reverse("payroll:overtime_list") + "?month=8&year=2026&show=all")
        salary = self.client.get(reverse("payroll:payroll_home"), {"month": 8, "year": 2026})
        self.assertContains(salary, "after this")

    def test_open_session_page_asks_for_the_time_they_left(self):
        record = self.work(MONDAY, (9, 0), (18, 0), (19, 0))
        self.client.force_login(self.admin)
        page = self.client.get(reverse("payroll:overtime_decide", args=[record.pk]))
        self.assertContains(page, "They left at")
        self.assertContains(page, "data-timepicker")
        self.assertNotContains(page, "Minutes to approve")

    def test_settings_page_shows_overtime_fields(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("payroll:salary_settings"))
        self.assertContains(page, "Overtime pays (× the hourly rate)")
        self.assertContains(page, "Approved overtime pays 2× the hourly rate")
