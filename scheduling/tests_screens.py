"""Working-calendar screens: shifts, attendance settings, weekly offs, holidays.

Services are tested for authorization, validation and audit; views are tested
by making the request, because a query string such as an ordering or a related
name only fails when the page actually runs.
"""

from datetime import date, time

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts.models import CompanyMembership, User
from auditlog.models import AuditLog
from common.tenant import use_company
from organization.models import Branch
from scheduling import services
from scheduling.models import CompanyAttendanceSettings, Holiday, Shift, WeeklyOffRule
from tenants.services import onboard_company

DAY_SHIFT = {
    "code": "DAY",
    "name": "Day shift",
    "start_time": time(9, 0),
    "end_time": time(18, 0),
    "spans_next_day": False,
    "grace_in_minutes": 10,
    "minimum_full_day_minutes": 480,
    "minimum_half_day_minutes": 240,
}


class CalendarBase(TestCase):
    def setUp(self):
        self.company = onboard_company(code="ACME", slug="acme", name="Acme Ltd")
        self.other = onboard_company(code="OTHER", slug="other", name="Other Ltd")
        self.admin = User.objects.create_user(email="admin@acme.test", password="pw")
        self.hr = User.objects.create_user(email="hr@acme.test", password="pw")
        self.outsider = User.objects.create_user(email="admin@other.test", password="pw")
        CompanyMembership.all_objects.create(
            company=self.company, user=self.admin,
            role=CompanyMembership.Role.COMPANY_ADMIN,
            status=CompanyMembership.Status.ACTIVE,
        )
        CompanyMembership.all_objects.create(
            company=self.company, user=self.hr,
            role=CompanyMembership.Role.HR,
            status=CompanyMembership.Status.ACTIVE,
        )
        CompanyMembership.all_objects.create(
            company=self.other, user=self.outsider,
            role=CompanyMembership.Role.COMPANY_ADMIN,
            status=CompanyMembership.Status.ACTIVE,
        )
        with use_company(self.company):
            self.hq = Branch.objects.get(is_default=True)

    def _shift(self, **overrides):
        return services.create_shift(
            actor=self.admin, company_id=self.company.pk,
            values={**DAY_SHIFT, **overrides},
        )


class ShiftServiceTests(CalendarBase):
    def test_shift_length_is_derived_from_start_and_end(self):
        shift = self._shift()
        self.assertEqual(shift.scheduled_minutes, 540)

    def test_night_shift_length_crosses_midnight(self):
        shift = self._shift(
            code="NGT", name="Night", start_time=time(22, 0), end_time=time(6, 0),
            spans_next_day=True, minimum_full_day_minutes=420,
            minimum_half_day_minutes=210,
        )
        self.assertEqual(shift.scheduled_minutes, 480)

    def test_full_day_cannot_exceed_the_shift_length(self):
        with self.assertRaises(ValidationError) as caught:
            self._shift(minimum_full_day_minutes=600)
        self.assertIn("minimum_full_day_minutes", caught.exception.error_dict)

    def test_half_day_cannot_exceed_full_day(self):
        with self.assertRaises(ValidationError) as caught:
            self._shift(minimum_half_day_minutes=500)
        self.assertIn("minimum_half_day_minutes", caught.exception.error_dict)

    def test_hr_cannot_create_a_shift(self):
        with self.assertRaises(PermissionDenied):
            services.create_shift(
                actor=self.hr, company_id=self.company.pk, values=DAY_SHIFT
            )

    def test_another_company_cannot_create_here(self):
        with self.assertRaises(PermissionDenied):
            services.create_shift(
                actor=self.outsider, company_id=self.company.pk, values=DAY_SHIFT
            )

    def test_unexposed_field_is_rejected(self):
        with self.assertRaises(ValidationError):
            services.create_shift(
                actor=self.admin, company_id=self.company.pk,
                values={**DAY_SHIFT, "company": self.other},
            )

    def test_shift_creation_is_audited(self):
        shift = self._shift()
        self.assertTrue(
            AuditLog.objects.filter(action="shift.created", object_id=str(shift.pk)).exists()
        )

    def test_the_company_shift_cannot_be_deactivated(self):
        shift = self._shift()
        services.update_attendance_settings(
            actor=self.admin, company_id=self.company.pk,
            values={"company_shift": shift, "missing_punch_policy": "review_required"},
        )
        with self.assertRaises(ValidationError):
            services.set_shift_status(
                actor=self.admin, company_id=self.company.pk, shift_id=shift.pk,
                status="inactive",
            )

    def test_another_companys_shift_is_not_found(self):
        foreign = services.create_shift(
            actor=self.outsider, company_id=self.other.pk, values=DAY_SHIFT
        )
        with self.assertRaises(PermissionDenied):
            services.update_shift(
                actor=self.admin, company_id=self.company.pk, shift_id=foreign.pk,
                values={"name": "Stolen"},
            )


class AttendanceSettingsTests(CalendarBase):
    def test_onboarding_leaves_the_company_without_a_shift(self):
        settings = services.get_attendance_settings(self.company.pk)
        self.assertIsNone(settings.company_shift_id)

    def test_choosing_single_shift_mode_with_a_company_shift(self):
        shift = self._shift()
        settings = services.update_attendance_settings(
            actor=self.admin, company_id=self.company.pk,
            values={"shift_mode": "company_single_shift", "company_shift": shift,
                    "missing_punch_policy": "auto_absent"},
        )
        self.assertEqual(settings.company_shift_id, shift.pk)
        self.assertEqual(
            settings.shift_mode, CompanyAttendanceSettings.ShiftMode.COMPANY_SINGLE_SHIFT
        )
        self.assertEqual(settings.missing_punch_policy, "auto_absent")
        self.assertEqual(settings.settings_version, 2)

    def test_an_inactive_shift_cannot_become_the_company_shift(self):
        shift = self._shift()
        services.set_shift_status(
            actor=self.admin, company_id=self.company.pk, shift_id=shift.pk,
            status="inactive",
        )
        shift.refresh_from_db()
        with self.assertRaises(ValidationError):
            services.update_attendance_settings(
                actor=self.admin, company_id=self.company.pk,
                values={"company_shift": shift, "missing_punch_policy": "review_required"},
            )


class WeeklyOffTests(CalendarBase):
    def _add(self, **overrides):
        values = {"weekdays": [4], "branch": None, "is_paid": True,
                  "effective_from": date(2026, 1, 1)}
        values.update(overrides)
        return services.add_weekly_offs(
            actor=self.admin, company_id=self.company.pk, values=values
        )

    def test_company_wide_weekly_off(self):
        [rule] = self._add()
        self.assertIsNone(rule.branch_id)
        self.assertEqual(rule.get_weekday_display(), "Friday")

    def test_several_days_in_one_step_store_one_rule_each(self):
        rules = self._add(weekdays=[4, 5])
        self.assertEqual(
            sorted(rule.get_weekday_display() for rule in rules),
            ["Friday", "Saturday"],
        )
        with use_company(self.company):
            self.assertEqual(WeeklyOffRule.objects.count(), 2)

    def test_a_clash_adds_none_of_the_selected_days(self):
        self._add(weekdays=[4])
        with self.assertRaises(ValidationError) as caught:
            self._add(weekdays=[4, 5])
        self.assertIn("Friday", str(caught.exception))
        with use_company(self.company):
            # Saturday was not added on its own: all or nothing.
            self.assertFalse(WeeklyOffRule.objects.filter(weekday=5).exists())

    def test_no_day_selected_is_refused(self):
        with self.assertRaises(ValidationError):
            self._add(weekdays=[])

    def test_the_same_day_can_be_off_for_one_branch_and_company_wide(self):
        self._add()
        [rule] = self._add(branch=self.hq)
        self.assertEqual(rule.branch_id, self.hq.pk)

    def test_stopping_keeps_the_rule_with_an_end_date(self):
        [rule] = self._add()
        services.end_weekly_off(
            actor=self.admin, company_id=self.company.pk, rule_id=rule.pk,
            effective_to=date(2026, 7, 1),
        )
        rule.refresh_from_db()
        self.assertEqual(rule.status, WeeklyOffRule.Status.ENDED)
        self.assertEqual(rule.effective_to, date(2026, 7, 1))

    def test_stop_date_must_be_after_the_start(self):
        [rule] = self._add()
        with self.assertRaises(ValidationError):
            services.end_weekly_off(
                actor=self.admin, company_id=self.company.pk, rule_id=rule.pk,
                effective_to=date(2025, 12, 1),
            )


class HolidayTests(CalendarBase):
    def _add(self, **overrides):
        values = {"holiday_date": date(2026, 12, 16), "name": "Victory Day",
                  "branch": None, "is_paid": True, "description": ""}
        values.update(overrides)
        return services.create_holiday(
            actor=self.admin, company_id=self.company.pk, values=values
        )

    def test_add_a_holiday(self):
        holiday = self._add()
        self.assertEqual(holiday.status, Holiday.Status.ACTIVE)

    def test_duplicate_date_is_a_readable_error(self):
        self._add()
        with self.assertRaises(ValidationError) as caught:
            self._add(name="Something else")
        self.assertIn("holiday_date", caught.exception.error_dict)

    def test_cancel_keeps_the_record(self):
        holiday = self._add()
        services.cancel_holiday(
            actor=self.admin, company_id=self.company.pk, holiday_id=holiday.pk
        )
        holiday.refresh_from_db()
        self.assertEqual(holiday.status, Holiday.Status.CANCELLED)
        self.assertEqual(holiday.cancelled_by, self.admin)

    def test_a_cancelled_date_can_be_used_again(self):
        holiday = self._add()
        services.cancel_holiday(
            actor=self.admin, company_id=self.company.pk, holiday_id=holiday.pk
        )
        again = self._add(name="Victory Day (moved)")
        self.assertEqual(again.status, Holiday.Status.ACTIVE)

    def test_edit_does_not_collide_with_itself(self):
        holiday = self._add()
        services.update_holiday(
            actor=self.admin, company_id=self.company.pk, holiday_id=holiday.pk,
            values={"name": "Bijoy Dibos"},
        )
        holiday.refresh_from_db()
        self.assertEqual(holiday.name, "Bijoy Dibos")


class CalendarScreenTests(CalendarBase):
    def test_overview_renders_and_warns_until_a_shift_is_chosen(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("scheduling:schedule_overview"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Attendance cannot be calculated yet")

    def test_overview_shows_the_chosen_company_shift(self):
        shift = self._shift()
        services.update_attendance_settings(
            actor=self.admin, company_id=self.company.pk,
            values={"company_shift": shift, "missing_punch_policy": "review_required"},
        )
        self.client.force_login(self.admin)
        response = self.client.get(reverse("scheduling:schedule_overview"))
        self.assertNotContains(response, "Attendance cannot be calculated yet")
        self.assertContains(response, "Day shift")
        self.assertContains(response, "540")

    def test_every_form_page_renders(self):
        shift = self._shift()
        [rule] = services.add_weekly_offs(
            actor=self.admin, company_id=self.company.pk,
            values={"weekdays": [4], "branch": None, "is_paid": True,
                    "effective_from": date(2026, 1, 1)},
        )
        holiday = services.create_holiday(
            actor=self.admin, company_id=self.company.pk,
            values={"holiday_date": date(2026, 12, 16), "name": "Victory Day",
                    "branch": None, "is_paid": True, "description": ""},
        )
        self.client.force_login(self.admin)
        for url in (
            reverse("scheduling:attendance_settings_edit"),
            reverse("scheduling:shift_create"),
            reverse("scheduling:shift_edit", args=[shift.pk]),
            reverse("scheduling:shift_status", args=[shift.pk]),
            reverse("scheduling:weekly_off_create"),
            reverse("scheduling:weekly_off_end", args=[rule.pk]),
            reverse("scheduling:holiday_list"),
            reverse("scheduling:holiday_create"),
            reverse("scheduling:holiday_edit", args=[holiday.pk]),
            reverse("scheduling:holiday_cancel", args=[holiday.pk]),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_creating_a_shift_through_the_form(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("scheduling:shift_create"), {
            "code": "day", "name": "Day shift", "start_time": "09:00",
            "end_time": "18:00", "grace_in_minutes": "10",
            "minimum_full_day_minutes": "480", "minimum_half_day_minutes": "240",
        })
        self.assertRedirects(response, reverse("scheduling:schedule_overview"))
        with use_company(self.company):
            shift = Shift.objects.get()
        self.assertEqual(shift.code, "DAY")
        self.assertEqual(shift.scheduled_minutes, 540)

    def test_an_end_before_the_start_is_a_night_shift(self):
        # No "ends on the next day" box: the times say it.
        self.client.force_login(self.admin)
        page = self.client.get(reverse("scheduling:shift_create"))
        self.assertNotContains(page, "spans_next_day")
        response = self.client.post(reverse("scheduling:shift_create"), {
            "code": "ngt", "name": "Night", "start_time": "22:00", "end_time": "06:00",
            "grace_in_minutes": "0", "minimum_full_day_minutes": "420",
            "minimum_half_day_minutes": "210",
        })
        self.assertRedirects(response, reverse("scheduling:schedule_overview"))
        with use_company(self.company):
            shift = Shift.objects.get()
        self.assertTrue(shift.spans_next_day)
        self.assertEqual(shift.scheduled_minutes, 480)

    def test_editing_a_night_shift_into_a_day_shift_clears_next_day(self):
        shift = self._shift(
            code="NGT", name="Night", start_time=time(22, 0), end_time=time(6, 0),
            minimum_full_day_minutes=420, minimum_half_day_minutes=210,
        )
        self.assertTrue(shift.spans_next_day)
        shift = services.update_shift(
            actor=self.admin, company_id=self.company.pk, shift_id=shift.pk,
            values={"start_time": time(9, 0), "end_time": time(17, 0)},
        )
        self.assertFalse(shift.spans_next_day)
        self.assertEqual(shift.scheduled_minutes, 480)

    def test_same_start_and_end_is_a_field_error(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("scheduling:shift_create"), {
            "code": "X", "name": "X", "start_time": "09:00", "end_time": "09:00",
            "grace_in_minutes": "0", "minimum_full_day_minutes": "0",
            "minimum_half_day_minutes": "0",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cannot start and end at the same time")

    def test_paid_break_shows_only_with_a_break(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("scheduling:shift_create")).content.decode()
        self.assertIn('data-show-when="default_break_minutes:&gt;0"', page)

    def test_paid_tick_without_a_break_is_dropped(self):
        shift = self._shift(default_break_minutes=0, break_is_paid=True)
        self.assertFalse(shift.break_is_paid)
        shift = self._shift(code="LUNCH", default_break_minutes=60, break_is_paid=True)
        self.assertTrue(shift.break_is_paid)

    def test_weekly_off_page_shows_seven_day_buttons_saturday_first(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("scheduling:weekly_off_create"))
        body = response.content.decode()
        self.assertEqual(body.count('class="day-picker__input"'), 7)
        self.assertLess(body.index("Saturday"), body.index("Friday"))

    def test_adding_two_days_through_the_form(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("scheduling:weekly_off_create"), {
            "weekdays": ["4", "5"], "effective_from": "2026-01-01",
        })
        self.assertRedirects(response, reverse("scheduling:schedule_overview"))
        with use_company(self.company):
            self.assertEqual(
                sorted(WeeklyOffRule.objects.values_list("weekday", flat=True)), [4, 5]
            )

    def test_no_day_selected_shows_a_field_error(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("scheduling:weekly_off_create"), {
            "effective_from": "2026-01-01",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select at least one day.")

    def test_duplicate_holiday_shows_on_the_date_field(self):
        services.create_holiday(
            actor=self.admin, company_id=self.company.pk,
            values={"holiday_date": date(2026, 12, 16), "name": "Victory Day",
                    "branch": None, "is_paid": True, "description": ""},
        )
        self.client.force_login(self.admin)
        response = self.client.post(reverse("scheduling:holiday_create"), {
            "holiday_date": "2026-12-16", "name": "Again",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "is already a holiday on this date")

    def test_hr_sees_the_overview_but_cannot_open_a_form(self):
        self.client.force_login(self.hr)
        overview = self.client.get(reverse("scheduling:schedule_overview"))
        self.assertEqual(overview.status_code, 200)
        self.assertNotContains(overview, "Add shift")
        self.assertEqual(
            self.client.get(reverse("scheduling:shift_create")).status_code, 403
        )

    def test_holidays_do_not_leak_between_companies(self):
        services.create_holiday(
            actor=self.outsider, company_id=self.other.pk,
            values={"holiday_date": date(2026, 12, 16), "name": "Their holiday",
                    "branch": None, "is_paid": True, "description": ""},
        )
        self.client.force_login(self.admin)
        response = self.client.get(reverse("scheduling:holiday_list") + "?year=2026")
        self.assertNotContains(response, "Their holiday")


class DepartmentShiftTests(CalendarBase):
    def setUp(self):
        super().setUp()
        from organization.catalogue import adopt_department
        with use_company(self.company):
            self.sales = adopt_department(self.hq, "SL", "Sales")
        self.day = self._shift()
        self.early = self._shift(
            code="EARLY", name="Early", start_time=time(6), end_time=time(14),
            minimum_full_day_minutes=420, minimum_half_day_minutes=210,
        )

    def _set(self, shift, starts):
        return services.set_department_shift(
            actor=self.admin, company_id=self.company.pk,
            values={"department": self.sales, "shift": shift, "effective_from": starts},
        )

    def test_single_shift_mode_needs_a_company_shift(self):
        with self.assertRaises(ValidationError):
            services.update_attendance_settings(
                actor=self.admin, company_id=self.company.pk,
                values={"shift_mode": "company_single_shift", "company_shift": None,
                        "missing_punch_policy": "review_required"},
            )

    def test_department_mode_resolves_the_department_shift_then_the_company_shift(self):
        from scheduling.calendar import WorkCalendar
        services.update_attendance_settings(
            actor=self.admin, company_id=self.company.pk,
            values={"shift_mode": "department_shifts", "company_shift": self.day,
                    "missing_punch_policy": "review_required"},
        )
        self._set(self.early, date(2026, 9, 1))
        calendar = WorkCalendar(self.company.pk, date(2026, 9, 1), date(2026, 9, 30))
        self.assertEqual(calendar.shift_for(self.sales.pk, date(2026, 9, 10)), self.early)
        # A department with no shift of its own falls back to the company shift.
        self.assertEqual(calendar.shift_for(None, date(2026, 9, 10)), self.day)
        # Before the department shift started, the company shift applied.
        self.assertEqual(calendar.shift_for(self.sales.pk, date(2026, 8, 31)), self.day)

    def test_changing_the_shift_closes_the_previous_one(self):
        first = self._set(self.day, date(2026, 9, 1))
        second = self._set(self.early, date(2026, 9, 15))
        first.refresh_from_db()
        self.assertEqual(first.effective_to, date(2026, 9, 15))
        self.assertEqual(second.effective_from, date(2026, 9, 15))

    def test_same_start_date_replaces_the_shift(self):
        first = self._set(self.day, date(2026, 9, 1))
        again = self._set(self.early, date(2026, 9, 1))
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(again.shift, self.early)

    def test_a_date_before_a_later_change_is_refused(self):
        self._set(self.day, date(2026, 9, 1))
        self._set(self.early, date(2026, 9, 15))
        with self.assertRaises(ValidationError):
            self._set(self.day, date(2026, 9, 10))

    def test_set_shift_page_and_overview_render(self):
        self._set(self.early, date(2026, 9, 1))
        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.get(reverse("scheduling:department_shift_set")).status_code, 200
        )
        overview = self.client.get(reverse("scheduling:schedule_overview"))
        self.assertContains(overview, "Department shifts")
        self.assertContains(overview, "Sales")
