"""Scheduling tests: shift validity, roster overlap, weekly offs, holidays."""

from datetime import date, datetime, time, timezone as dt_timezone

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from common.tenant import use_company
from employees.models import Employee
from organization.models import Branch, CompanyDepartment, Department
from scheduling.models import (
    CompanyAttendanceSettings,
    DepartmentShift,
    EmployeeShiftAssignment,
    Holiday,
    HolidayWorkAssignment,
    Shift,
    WeeklyOffRule,
)
from tenants.models import Company


def dt(y, m, d):
    return datetime(y, m, d, tzinfo=dt_timezone.utc)


class SchedulingTests(TestCase):
    def setUp(self):
        software_entry = Department.objects.create(code="SW", name="Software")
        self.company = Company.objects.create(code="A", slug="a", name="Company A")
        with use_company(self.company):
            self.branch = Branch.objects.create(
                code="HQ", name="Head Office", is_default=True
            )
            self.department = CompanyDepartment.objects.create(
                branch=self.branch, department=software_entry
            )
            self.employee = Employee.objects.create(first_name="Alice")
            self.day_shift = Shift.objects.create(
                code="DAY", name="Day", start_time=time(9), end_time=time(18),
                scheduled_minutes=480, default_break_minutes=60,
                minimum_full_day_minutes=420, minimum_half_day_minutes=210,
            )

    # --- shifts -----------------------------------------------------------

    def test_night_shift_may_end_before_it_starts(self):
        with use_company(self.company):
            night = Shift(
                code="NGT", name="Night", start_time=time(22), end_time=time(6),
                spans_next_day=True, scheduled_minutes=480,
            )
            night.company = self.company
            night.full_clean()  # must not raise

    def test_non_spanning_shift_ending_before_start_rejected(self):
        with use_company(self.company):
            bad = Shift(
                code="BAD", name="Bad", start_time=time(18), end_time=time(9),
                spans_next_day=False, scheduled_minutes=480,
            )
            bad.company = self.company
            with self.assertRaises(ValidationError):
                bad.full_clean()

    def test_half_day_cannot_exceed_full_day_minutes(self):
        with use_company(self.company):
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    Shift.objects.create(
                        code="X", name="X", start_time=time(9), end_time=time(18),
                        scheduled_minutes=480,
                        minimum_full_day_minutes=200,
                        minimum_half_day_minutes=400,
                    )

    def test_duplicate_shift_code_in_company_rejected(self):
        with use_company(self.company):
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    Shift.objects.create(
                        code="DAY", name="Another Day", start_time=time(9),
                        end_time=time(18), scheduled_minutes=480,
                    )

    # --- company attendance settings --------------------------------------

    def test_single_shift_mode_requires_a_company_shift(self):
        with use_company(self.company):
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    CompanyAttendanceSettings.objects.create(
                        shift_mode="company_single_shift",
                        company_shift=None,
                        effective_from=dt(2024, 1, 1),
                    )

    def test_department_shift_mode_needs_no_company_shift(self):
        with use_company(self.company):
            settings_row = CompanyAttendanceSettings.objects.create(
                shift_mode="department_shifts", effective_from=dt(2024, 1, 1)
            )
            self.assertIsNone(settings_row.company_shift)

    def test_only_one_settings_row_per_company(self):
        with use_company(self.company):
            CompanyAttendanceSettings.objects.create(
                shift_mode="department_shifts", effective_from=dt(2024, 1, 1)
            )
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    CompanyAttendanceSettings.objects.create(
                        shift_mode="department_shifts", effective_from=dt(2024, 6, 1)
                    )

    # --- department shifts -------------------------------------------------

    def test_department_cannot_have_two_defaults_at_once(self):
        with use_company(self.company):
            other = Shift.objects.create(
                code="EVE", name="Evening", start_time=time(14), end_time=time(22),
                scheduled_minutes=480,
            )
            DepartmentShift.objects.create(
                department=self.department, shift=self.day_shift,
                is_default=True, effective_from=date(2024, 1, 1),
            )
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    DepartmentShift.objects.create(
                        department=self.department, shift=other,
                        is_default=True, effective_from=date(2024, 6, 1),
                    )

    def test_default_may_change_after_previous_one_ends(self):
        with use_company(self.company):
            other = Shift.objects.create(
                code="EVE", name="Evening", start_time=time(14), end_time=time(22),
                scheduled_minutes=480,
            )
            DepartmentShift.objects.create(
                department=self.department, shift=self.day_shift, is_default=True,
                effective_from=date(2024, 1, 1), effective_to=date(2024, 6, 1),
                status="ended",
            )
            new_default = DepartmentShift.objects.create(
                department=self.department, shift=other, is_default=True,
                effective_from=date(2024, 6, 1),
            )
            self.assertTrue(new_default.is_default)

    # --- employee shift assignment ----------------------------------------

    def test_employee_cannot_have_overlapping_shift_assignments(self):
        with use_company(self.company):
            EmployeeShiftAssignment.objects.create(
                employee=self.employee, shift=self.day_shift,
                effective_from=dt(2024, 1, 1),
            )
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    EmployeeShiftAssignment.objects.create(
                        employee=self.employee, shift=self.day_shift,
                        effective_from=dt(2024, 3, 1),
                    )

    # --- weekly off rules ---------------------------------------------------

    def test_duplicate_company_wide_weekly_off_rejected(self):
        # Both rules have branch=NULL; Coalesce must make them collide.
        with use_company(self.company):
            WeeklyOffRule.objects.create(weekday=4, effective_from=date(2024, 1, 1))
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    WeeklyOffRule.objects.create(
                        weekday=4, effective_from=date(2024, 6, 1)
                    )

    def test_branch_rule_and_company_rule_can_coexist(self):
        with use_company(self.company):
            WeeklyOffRule.objects.create(weekday=4, effective_from=date(2024, 1, 1))
            branch_rule = WeeklyOffRule.objects.create(
                branch=self.branch, weekday=4, effective_from=date(2024, 1, 1)
            )
            self.assertEqual(branch_rule.branch, self.branch)

    def test_different_weekdays_do_not_collide(self):
        with use_company(self.company):
            WeeklyOffRule.objects.create(weekday=4, effective_from=date(2024, 1, 1))
            saturday = WeeklyOffRule.objects.create(
                weekday=5, effective_from=date(2024, 1, 1)
            )
            self.assertEqual(saturday.weekday, 5)

    # --- holidays ------------------------------------------------------------

    def test_duplicate_active_company_wide_holiday_rejected(self):
        with use_company(self.company):
            Holiday.objects.create(holiday_date=date(2024, 12, 25), name="Christmas")
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    Holiday.objects.create(
                        holiday_date=date(2024, 12, 25), name="Duplicate"
                    )

    def test_cancelled_holiday_frees_the_date(self):
        with use_company(self.company):
            Holiday.objects.create(
                holiday_date=date(2024, 12, 25), name="Christmas", status="cancelled"
            )
            replacement = Holiday.objects.create(
                holiday_date=date(2024, 12, 25), name="Christmas (rescheduled)"
            )
            self.assertEqual(replacement.status, "active")

    # --- holiday work --------------------------------------------------------

    def test_holiday_work_requires_exactly_one_source(self):
        with use_company(self.company):
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    HolidayWorkAssignment.objects.create(
                        employee=self.employee, work_date=date(2024, 12, 25),
                        holiday=None, weekly_off_rule=None,
                    )

    def test_working_a_weekly_off_needs_no_holiday_row(self):
        # Proves a recurring weekly off can be worked without deleting the rule.
        with use_company(self.company):
            rule = WeeklyOffRule.objects.create(
                weekday=4, effective_from=date(2024, 1, 1)
            )
            work = HolidayWorkAssignment(
                employee=self.employee,
                work_date=date(2024, 3, 1),  # a Friday
                weekly_off_rule=rule,
                treatment="overtime",
            )
            work.company = self.company
            work.full_clean()
            work.save()
            self.assertIsNone(work.holiday)

    def test_work_date_must_match_the_holiday_date(self):
        with use_company(self.company):
            holiday = Holiday.objects.create(
                holiday_date=date(2024, 12, 25), name="Christmas"
            )
            work = HolidayWorkAssignment(
                employee=self.employee, work_date=date(2024, 12, 26), holiday=holiday
            )
            work.company = self.company
            with self.assertRaises(ValidationError):
                work.full_clean()

    def test_work_date_must_fall_on_the_rule_weekday(self):
        with use_company(self.company):
            rule = WeeklyOffRule.objects.create(
                weekday=4, effective_from=date(2024, 1, 1)  # Friday
            )
            work = HolidayWorkAssignment(
                employee=self.employee,
                work_date=date(2024, 3, 2),  # a Saturday
                weekly_off_rule=rule,
            )
            work.company = self.company
            with self.assertRaises(ValidationError):
                work.full_clean()
