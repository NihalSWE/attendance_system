"""A5c: dated weekly offs, safe corrections and always-paid calendar days."""

from datetime import date
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from accounts.models import CompanyMembership
from attendance.models import AttendanceRecord
from attendance.services import recalculate
from auditlog.models import AuditLog
from common.tenant import use_company
from payroll.forms import SalaryRulesForm
from payroll.models import PayrollPeriod, PayrollRun
from scheduling import services
from scheduling.calendar import WorkCalendar
from scheduling.forms import HolidayForm, HolidayYearForm, WeeklyOffForm
from scheduling.models import Holiday, WeeklyOffRule
from scheduling.tests_employee_shifts import EmployeeShiftBase
from scheduling.tests_screens import CalendarBase


class WeeklyOffDateTests(CalendarBase):
    def add(self, start, **values):
        return services.add_weekly_offs(
            actor=self.admin, company_id=self.company.pk,
            values={"weekdays": [4], "effective_from": start, **values},
        )[0]

    def change(self, rule, start, actor=None):
        return services.change_weekly_off_start(
            actor=actor or self.admin, company_id=self.company.pk,
            rule_id=rule.pk, effective_from=start,
        )

    def test_adjacent_periods_allowed_even_if_earlier_rule_still_marked_active(self):
        rule = self.add(date(2026, 1, 1))
        with use_company(self.company):
            rule.effective_to = date(2026, 9, 1)
            rule.save()
        newer = self.add(date(2026, 9, 1))
        self.assertNotEqual(rule.pk, newer.pk)

    def test_overlap_with_stopped_history_names_both_dates(self):
        rule = self.add(date(2026, 1, 1))
        services.end_weekly_off(
            actor=self.admin, company_id=self.company.pk, rule_id=rule.pk,
            effective_to=date(2026, 9, 1),
        )
        with self.assertRaises(ValidationError) as caught:
            self.add(date(2026, 8, 1))
        for phrase in ("Friday", "01 Jan 2026", "01 Sep 2026", "change its start date"):
            self.assertIn(phrase, str(caught.exception))

    def test_change_start_back_to_2000_keeps_identity_and_audits(self):
        rule = self.add(date(2026, 9, 1))
        changed = self.change(rule, date(2000, 1, 1))
        self.assertEqual(changed.pk, rule.pk)
        with use_company(self.company):
            rule.refresh_from_db()
            event = AuditLog.objects.get(action="weekly_off.start_changed")
        self.assertEqual(rule.effective_from, date(2000, 1, 1))
        self.assertEqual(event.before_data["effective_from"], "2026-09-01")
        self.assertEqual(event.after_data["effective_from"], "2000-01-01")

    def test_change_cannot_overlap_stopped_rule_but_can_touch_its_end(self):
        old = self.add(date(2026, 1, 1))
        services.end_weekly_off(
            actor=self.admin, company_id=self.company.pk, rule_id=old.pk,
            effective_to=date(2026, 8, 1),
        )
        newer = self.add(date(2026, 9, 1))
        with self.assertRaises(ValidationError):
            self.change(newer, date(2026, 7, 1))
        with use_company(self.company):
            newer.refresh_from_db()
        self.assertEqual(newer.effective_from, date(2026, 9, 1))
        self.change(newer, date(2026, 8, 1))

    def test_stopped_rule_start_can_change_but_must_precede_end(self):
        rule = self.add(date(2026, 1, 1))
        services.end_weekly_off(
            actor=self.admin, company_id=self.company.pk, rule_id=rule.pk,
            effective_to=date(2026, 8, 1),
        )
        for start in (date(2026, 8, 1), date(2026, 9, 1)):
            with self.assertRaises(ValidationError):
                self.change(rule, start)
        changed = self.change(rule, date(2000, 1, 1))
        self.assertEqual(changed.status, WeeklyOffRule.Status.ENDED)
        self.assertEqual(changed.effective_to, date(2026, 8, 1))

    def test_permission_and_company_are_rechecked(self):
        rule = self.add(date(2026, 9, 1))
        for actor in (self.hr, self.outsider):
            with self.assertRaises(PermissionDenied):
                self.change(rule, date(2000, 1, 1), actor=actor)
        with self.assertRaises(PermissionDenied):
            services.change_weekly_off_start(
                actor=self.outsider, company_id=self.other.pk, rule_id=rule.pk,
                effective_from=date(2000, 1, 1),
            )

    def test_restricted_admin_cannot_change_other_branch_rule(self):
        from organization.models import Branch

        with use_company(self.company):
            branch = Branch.objects.create(name="Other branch", code="B2")
            member = CompanyMembership.objects.get(user=self.admin)
            member.allowed_branches.add(self.hq)
            rule = WeeklyOffRule.objects.create(
                branch=branch, weekday=4, effective_from=date(2026, 9, 1)
            )
        with self.assertRaises(PermissionDenied):
            self.change(rule, date(2000, 1, 1))

    def test_branch_and_company_rules_have_independent_periods(self):
        self.add(date(2000, 1, 1))
        rule = self.add(date(2026, 9, 1), branch=self.hq)
        self.change(rule, date(2000, 1, 1))

    def test_start_screen_renders_with_project_calendar_and_posts(self):
        rule = self.add(date(2026, 9, 1))
        self.client.force_login(self.admin)
        url = reverse("scheduling:weekly_off_start", args=[rule.pk])
        response = self.client.get(url)
        self.assertContains(response, "data-datepicker")
        self.assertContains(self.client.get(reverse("scheduling:schedule_overview")), url)
        response = self.client.post(url, {"effective_from": "2000-01-01"})
        self.assertRedirects(response, reverse("scheduling:schedule_overview"))
        with use_company(self.company):
            rule.refresh_from_db()
        self.assertEqual(rule.effective_from, date(2000, 1, 1))


class WeeklyOffRecalculationTests(EmployeeShiftBase):
    def setUp(self):
        super().setUp()
        self.rule = services.add_weekly_offs(
            actor=self.admin, company_id=self.company.pk,
            values={"weekdays": [4], "effective_from": date(2026, 9, 1)},
        )[0]
        recalculate(self.company.pk, start=date(2026, 8, 7), end=date(2026, 8, 7))

    def change(self, start):
        return services.change_weekly_off_start(
            actor=self.admin, company_id=self.company.pk, rule_id=self.rule.pk,
            effective_from=start,
        )

    def record(self):
        return AttendanceRecord.all_objects.get(
            company=self.company, employee=self.employee, work_date=date(2026, 8, 7)
        )

    def test_earlier_start_rebuilds_attendance_and_later_start_reverses_it(self):
        self.assertEqual(self.record().attendance_status, "absent")
        self.change(date(2026, 8, 1))
        self.assertEqual(self.record().attendance_status, "weekly_off")
        self.assertEqual(self.record().payable_fraction, 1)
        self.change(date(2026, 9, 1))
        self.assertEqual(self.record().attendance_status, "absent")

    def test_finalised_month_is_unchanged_while_unlocked_dates_rebuild(self):
        before = self.record()
        with use_company(self.company):
            period = PayrollPeriod.objects.create(
                name="Aug 2026", start_date=date(2026, 8, 1), end_date=date(2026, 8, 31)
            )
            PayrollRun.objects.create(payroll_period=period, status=PayrollRun.Status.POSTED)
        self.change(date(2026, 7, 1))
        after = self.record()
        self.assertEqual((after.attendance_status, after.updated_at),
                         (before.attendance_status, before.updated_at))
        self.assertEqual(AttendanceRecord.all_objects.get(
            company=self.company, employee=self.employee, work_date=date(2026, 7, 3)
        ).attendance_status, "weekly_off")

    def test_recalculation_failure_rolls_back_rule_attendance_and_audit(self):
        with patch("attendance.services.recalculate", side_effect=RuntimeError("test failure")):
            with self.assertRaises(RuntimeError):
                self.change(date(2000, 1, 1))
        with use_company(self.company):
            self.rule.refresh_from_db()
            self.assertFalse(AuditLog.objects.filter(action="weekly_off.start_changed").exists())
        self.assertEqual(self.rule.effective_from, date(2026, 9, 1))
        self.assertEqual(self.record().attendance_status, "absent")

    def test_backdating_does_not_expand_batches_before_employment(self):
        with patch("attendance.services.recalculate") as rebuild:
            self.change(date(2000, 1, 1))
        self.assertEqual(len(rebuild.call_args_list), 8)
        self.assertEqual(rebuild.call_args_list[0].kwargs["start"], date(2026, 1, 1))
        self.assertEqual(rebuild.call_args_list[-1].kwargs["end"], date(2026, 8, 31))


class PaidCalendarTests(CalendarBase):
    def test_paid_flags_are_not_editable_and_posted_false_is_ignored(self):
        for form in (HolidayForm(), HolidayYearForm(), WeeklyOffForm()):
            self.assertNotIn("is_paid", form.fields)
        rule = services.add_weekly_offs(
            actor=self.admin, company_id=self.company.pk,
            values={"weekdays": [4], "effective_from": date(2026, 1, 1), "is_paid": False},
        )[0]
        holiday = services.create_holiday(
            actor=self.admin, company_id=self.company.pk,
            values={"holiday_date": date(2026, 8, 8), "name": "Day off", "is_paid": False},
        )
        edited = services.update_holiday(
            actor=self.admin, company_id=self.company.pk, holiday_id=holiday.pk,
            values={"name": "Company day", "is_paid": False},
        )
        batch = services.add_holidays(
            actor=self.admin, company_id=self.company.pk,
            values={"days": [(date(2026, 8, 9), "Another day")], "is_paid": False},
        )[0]
        self.assertTrue(all(row.is_paid for row in (rule, holiday, edited, batch)))

    def test_legacy_unpaid_flags_do_not_make_days_off_unpaid(self):
        with use_company(self.company):
            WeeklyOffRule.objects.create(weekday=4, effective_from=date(2000, 1, 1), is_paid=False)
            Holiday.objects.create(holiday_date=date(2026, 8, 8), name="Old holiday", is_paid=False)
        calendar = WorkCalendar(self.company.pk, date(2026, 8, 7), date(2026, 8, 8))
        self.assertTrue(calendar.day(self.hq.pk, date(2026, 8, 7)).is_paid)
        self.assertTrue(calendar.day(self.hq.pk, date(2026, 8, 8)).is_paid)

    def test_forms_save_without_paid_fields(self):
        self.client.force_login(self.admin)
        for url, data in (
            ("scheduling:weekly_off_create", {"weekdays": [4], "effective_from": "2026-01-01"}),
            ("scheduling:holiday_create", {"holiday_date": "2026-08-08", "name": "Company day"}),
            ("scheduling:holiday_year", {"save": "1", "year": "2026", "date": ["2026-08-09"],
                                         "name": ["Calendar day"]}),
        ):
            with self.subTest(url=url):
                self.assertNotContains(self.client.get(reverse(url)), 'name="is_paid"')
                self.assertEqual(self.client.post(reverse(url), data).status_code, 302)
        with use_company(self.company):
            self.assertFalse(Holiday.objects.filter(is_paid=False).exists())
            self.assertFalse(WeeklyOffRule.objects.filter(is_paid=False).exists())

    def test_day_value_label_explains_absence_and_overtime(self):
        field = SalaryRulesForm.base_fields["monthly_proration_method"]
        self.assertIn("absence", field.label)
        self.assertIn("overtime", field.help_text)
