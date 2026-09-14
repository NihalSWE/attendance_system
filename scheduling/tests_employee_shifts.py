"""A5 shifts: the new shift fields, an employee's own shift, the time picker."""

import datetime
from datetime import date, time
from decimal import Decimal

from django import forms
from django.apps import apps
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from common.tenant import use_company
from employees.services import create_employee
from organization.catalogue import adopt_department, adopt_designation
from scheduling import services
from scheduling.calendar import WorkCalendar
from scheduling.models import EmployeeShiftAssignment, Shift
from scheduling.tests_screens import CalendarBase


class EmployeeShiftBase(CalendarBase):
    def setUp(self):
        super().setUp()
        with use_company(self.company):
            self.sales = adopt_department(self.hq, "SL", "Sales")
            clerk = adopt_designation(self.sales, "CLK", "Clerk")
        self.employee = create_employee(
            company=self.company, first_name="Rina", employee_code="E1", branch=self.hq,
            department=self.sales, designation=clerk,
            effective_from=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
            pay_basis="monthly", base_rate=Decimal("30000"),
        )["employee"]
        self.day = self._shift()
        self.night = self._shift(
            code="NIGHT", name="Night", start_time=time(22), end_time=time(6),
            spans_next_day=True, minimum_full_day_minutes=420, minimum_half_day_minutes=210,
        )
        self.early = self._shift(
            code="EARLY", name="Early", start_time=time(6), end_time=time(14),
            minimum_full_day_minutes=420, minimum_half_day_minutes=210,
        )
        services.update_attendance_settings(
            actor=self.admin, company_id=self.company.pk,
            values={"shift_mode": "company_single_shift", "company_shift": self.day,
                    "missing_punch_policy": "review_required"},
        )

    def give(self, shift, first, last=None, actor=None):
        return services.set_employee_shift(
            actor=actor or self.admin, company_id=self.company.pk,
            values={"employee": self.employee, "shift": shift, "first_day": first,
                    "last_day": last, "reason": ""},
        )

    def shift_on(self, on, employee_id="me"):
        calendar = WorkCalendar(self.company.pk, on, on)
        employee = self.employee.pk if employee_id == "me" else employee_id
        return calendar.shift_for(self.sales.pk, on, employee_id=employee)

    def own(self):
        with use_company(self.company):
            return list(
                EmployeeShiftAssignment.objects.exclude(status="cancelled").order_by("effective_from")
            )


class EmployeeShiftTests(EmployeeShiftBase):
    def test_an_own_shift_wins_over_the_company_shift(self):
        self.give(self.night, date(2026, 9, 10))
        self.assertEqual(self.shift_on(date(2026, 9, 9)), self.day)
        self.assertEqual(self.shift_on(date(2026, 9, 10)), self.night)
        self.assertEqual(self.shift_on(date(2027, 1, 1)), self.night)
        # A caller that does not pass the employee gets the company shift.
        self.assertEqual(self.shift_on(date(2026, 9, 10), employee_id=None), self.day)

    def test_a_temporary_shift_ends_and_the_employee_goes_back(self):
        self.give(self.night, date(2026, 9, 10), date(2026, 9, 12))
        self.assertEqual(self.shift_on(date(2026, 9, 12)), self.night)
        self.assertEqual(self.shift_on(date(2026, 9, 13)), self.day)
        self.assertEqual(self.own()[0].assignment_type, "temporary")

    def test_a_temporary_shift_inside_an_own_shift_hands_back_to_it(self):
        self.give(self.early, date(2026, 9, 1))
        self.give(self.night, date(2026, 9, 10), date(2026, 9, 12))
        self.assertEqual(self.shift_on(date(2026, 9, 9)), self.early)
        self.assertEqual(self.shift_on(date(2026, 9, 11)), self.night)
        self.assertEqual(self.shift_on(date(2026, 9, 13)), self.early)
        self.assertEqual([a.shift for a in self.own()], [self.early, self.night, self.early])

    def test_a_new_own_shift_closes_the_one_before(self):
        first = self.give(self.early, date(2026, 9, 1))
        self.give(self.night, date(2026, 9, 20))
        first.refresh_from_db()
        self.assertEqual(first.status, "ended")
        self.assertEqual(self.shift_on(date(2026, 9, 19)), self.early)
        self.assertEqual(self.shift_on(date(2026, 9, 20)), self.night)

    def test_the_same_first_day_replaces(self):
        first = self.give(self.early, date(2026, 9, 1))
        self.give(self.night, date(2026, 9, 1))
        first.refresh_from_db()
        self.assertEqual(first.status, "cancelled")
        self.assertEqual(self.shift_on(date(2026, 9, 5)), self.night)

    def test_a_change_before_a_later_one_is_refused(self):
        self.give(self.early, date(2026, 9, 20))
        with self.assertRaises(ValidationError) as caught:
            self.give(self.night, date(2026, 9, 1))
        self.assertIn("20 Sep 2026", str(caught.exception))

    def test_ending_an_own_shift(self):
        own = self.give(self.early, date(2026, 9, 1))
        services.end_employee_shift(
            actor=self.admin, company_id=self.company.pk, assignment_id=own.pk,
            last_day=date(2026, 9, 15),
        )
        self.assertEqual(self.shift_on(date(2026, 9, 15)), self.early)
        self.assertEqual(self.shift_on(date(2026, 9, 16)), self.day)

    def test_ending_before_it_starts_cancels_it(self):
        own = self.give(self.early, date(2026, 9, 20))
        services.end_employee_shift(
            actor=self.admin, company_id=self.company.pk, assignment_id=own.pk,
            last_day=date(2026, 9, 10),
        )
        own.refresh_from_db()
        self.assertEqual(own.status, "cancelled")

    def test_an_inactive_shift_cannot_be_given(self):
        services.set_shift_status(
            actor=self.admin, company_id=self.company.pk, shift_id=self.early.pk, status="inactive"
        )
        self.early.refresh_from_db()
        with self.assertRaises(ValidationError):
            self.give(self.early, date(2026, 9, 1))

    def test_hr_cannot_give_a_shift(self):
        with self.assertRaises(PermissionDenied):
            self.give(self.early, date(2026, 9, 1), actor=self.hr)

    def test_leave_is_measured_against_the_own_shift(self):
        from leaves import services as leave_services

        self.give(self.early, date(2026, 9, 1))
        casual = leave_services.create_leave_type(
            actor=self.admin, company_id=self.company.pk,
            values={"code": "CL", "name": "Casual", "description": ""},
        )
        request = leave_services.record_leave(actor=self.admin, company_id=self.company.pk, values={
            "employee": self.employee, "leave_type": casual,
            "start_date": date(2026, 9, 14), "end_date": date(2026, 9, 14),
            "pay_type": "paid", "reason": "",
        })
        with use_company(self.company):
            day = request.segments.first().days.first()
        # The early shift is 06:00-14:00 company time (Asia/Dhaka, UTC+6).
        self.assertEqual(day.covered_start_at.astimezone(datetime.timezone.utc).hour, 0)


class EmployeeShiftPageTests(EmployeeShiftBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        self.url = reverse("organization:employee_edit", args=[self.employee.pk])

    def test_the_edit_page_shows_the_shift_worked_today(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'id="shift"')
        self.assertContains(response, "the company shift")

    def test_giving_a_shift_through_the_page(self):
        response = self.client.post(self.url, {
            "section": "shift", "shift": self.night.pk,
            "first_day": "2026-09-10", "last_day": "", "reason": "Covering nights",
        })
        self.assertRedirects(response, self.url + "#shift", fetch_redirect_response=False)
        [own] = self.own()
        self.assertEqual((own.shift, own.reason), (self.night, "Covering nights"))
        page = self.client.get(self.url)
        self.assertContains(page, "Covering nights")
        self.assertContains(page, "End this shift")

    def test_ending_through_the_page(self):
        own = self.give(self.night, date(2026, 9, 1))
        response = self.client.post(self.url, {
            "section": "shift_end", "assignment": own.pk, "last_day": "2026-09-05",
        })
        self.assertEqual(response.status_code, 302)
        own.refresh_from_db()
        self.assertEqual(own.status, "ended")

    def test_a_refused_shift_shows_the_reason(self):
        self.give(self.early, date(2026, 9, 20))
        response = self.client.post(self.url, {
            "section": "shift", "shift": self.night.pk, "first_day": "2026-09-01",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already has a shift change")


class ShiftFormTests(CalendarBase):
    def test_break_grace_out_and_overtime_are_saved(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("scheduling:shift_create"), {
            "code": "day", "name": "Day", "start_time": "09:00", "end_time": "18:00",
            "grace_in_minutes": "10", "grace_out_minutes": "5",
            "minimum_full_day_minutes": "480", "minimum_half_day_minutes": "240",
            "default_break_minutes": "60", "break_is_paid": "on",
            "overtime_after_minutes": "30",
        })
        self.assertRedirects(response, reverse("scheduling:schedule_overview"))
        with use_company(self.company):
            shift = Shift.objects.get()
        self.assertEqual(
            (shift.grace_out_minutes, shift.default_break_minutes, shift.break_is_paid,
             shift.overtime_after_minutes),
            (5, 60, True, 30),
        )

    def test_an_unpaid_break_as_long_as_the_shift_is_refused(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("scheduling:shift_create"), {
            "code": "x", "name": "X", "start_time": "09:00", "end_time": "10:00",
            "grace_in_minutes": "0", "grace_out_minutes": "0",
            "minimum_full_day_minutes": "0", "minimum_half_day_minutes": "0",
            "default_break_minutes": "60", "overtime_after_minutes": "0",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Unpaid break cannot consume the whole scheduled time.")

    def test_every_time_box_carries_the_time_picker(self):
        """Every HH:MM box in every project form gets the project time picker."""
        missing = []
        for app in apps.get_app_configs():
            if not app.path.startswith(str(apps.get_app_config("scheduling").path).rsplit("scheduling", 1)[0]):
                continue
            for name in ("forms", "employee_forms", "employee_edit_forms", "adoption_forms"):
                try:
                    module = __import__(f"{app.name}.{name}", fromlist=["x"])
                except ModuleNotFoundError:
                    continue
                for attr in vars(module).values():
                    if not (isinstance(attr, type) and issubclass(attr, forms.BaseForm)):
                        continue
                    for field_name, field in getattr(attr, "base_fields", {}).items():
                        widgets = getattr(field.widget, "widgets", [field.widget])
                        for widget in widgets:
                            if widget.attrs.get("placeholder") == "HH:MM" and "data-timepicker" not in widget.attrs:
                                missing.append(f"{attr.__module__}.{attr.__name__}.{field_name}")
        self.assertEqual(missing, [])

    def test_the_shift_page_loads_the_time_picker(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("scheduling:shift_create"))
        self.assertContains(response, "timepicker.js")
        self.assertContains(response, "data-timepicker")
