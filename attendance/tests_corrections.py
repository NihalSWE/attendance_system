"""Fixing a day by hand, and the days to review (plan step N5).

The thing that matters most is that a fix *stays*: attendance rewrites itself
whenever a punch arrives, so a correction must be an input the calculation
reads, not an edit to its result. Most tests here therefore fix a day and then
recalculate it again before looking.
"""

import datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts.models import CompanyMembership, User
from attendance import correction_services as fixes
from attendance import live_status, tests_live
from attendance.models import AttendanceCorrection, AttendanceRecord
from attendance.services import recalculate
from auditlog.models import AuditLog
from common.tenant import use_company
from scheduling import services as schedule

UTC = datetime.timezone.utc
DHAKA = datetime.timezone(datetime.timedelta(hours=6))
S = AttendanceRecord.AttendanceStatus
AUG_10 = datetime.date(2026, 8, 10)  # Monday
AUG_11 = datetime.date(2026, 8, 11)
AUG_12 = datetime.date(2026, 8, 12)


def local(day, hour, minute=0, second=0):
    return datetime.datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=DHAKA)


class CorrectionTestCase(TestCase):
    # The attendance live-test company: an admin, a 09:00-18:00 shift with 10
    # minutes' grace, and Rahim on an authorised device. Borrowed through the
    # module so LiveTestCase is not collected twice.
    setUp_company = tests_live.LiveTestCase.setUp
    punch = tests_live.LiveTestCase.punch

    def setUp(self):
        self.setUp_company()

    def day(self, on):
        recalculate(self.company.pk, employee_ids=[self.employee.pk], start=on, end=on)
        with use_company(self.company):
            return AttendanceRecord.objects.filter(employee=self.employee, work_date=on).first()

    def add_scan(self, on, hour, minute=0, second=0, *, actor=None, reason="Device was offline"):
        return fixes.add_scan(
            actor=actor or self.admin, company_id=self.company.pk,
            employee_id=self.employee.pk, work_date=on,
            at=local(on, hour, minute, second), reason=reason,
        )

    def change_status(self, on, status, *, actor=None, reason="Worked on site"):
        return fixes.change_status(
            actor=actor or self.admin, company_id=self.company.pk,
            employee_id=self.employee.pk, work_date=on, status=status, reason=reason,
        )

    def member(self, role, email):
        user = User.objects.create_user(email=email, password="pw")
        CompanyMembership.all_objects.create(
            company=self.company, user=user, role=role,
            status=CompanyMembership.Status.ACTIVE,
        )
        return user

    def post_a_run(self):
        from payroll.models import PayrollPeriod, PayrollRun

        with use_company(self.company):
            period = PayrollPeriod.objects.create(
                company=self.company, name="Aug 2026",
                start_date=datetime.date(2026, 8, 1), end_date=datetime.date(2026, 8, 31),
            )
            PayrollRun.objects.create(
                company=self.company, payroll_period=period,
                status=PayrollRun.Status.POSTED,
            )


class AddScanTests(CorrectionTestCase):
    def test_a_forgotten_check_out_is_added_and_the_day_is_worked_out_again(self):
        self.punch(AUG_10, 9)
        before = self.day(AUG_10)
        self.assertTrue(before.check_out_by_rule)
        self.assertEqual(before.review_status, "needs_review")

        correction = self.add_scan(AUG_10, 18, 30)

        after = self.day(AUG_10)
        self.assertFalse(after.check_out_by_rule)
        self.assertEqual(after.review_status, "clean")
        self.assertEqual(after.last_out_at, local(AUG_10, 18, 30))
        with use_company(self.company):
            allocation = after.allocations.get(attendance_correction=correction)
        self.assertEqual(allocation.label, "check_out")
        self.assertIsNone(allocation.punch_event_id)
        # What the day was, and what it became, are kept on the correction.
        self.assertTrue(correction.before_snapshot["check_out_by_rule"])
        self.assertFalse(correction.after_snapshot["check_out_by_rule"])

    def test_the_fix_survives_the_next_recalculation_and_new_punches(self):
        self.punch(AUG_10, 9)
        self.add_scan(AUG_10, 18)
        self.punch(AUG_11, 9)  # a new scan for the next day arrives
        recalculate(self.company.pk, start=AUG_10, end=AUG_11)
        recalculate(self.company.pk, start=AUG_10, end=AUG_10)
        self.assertEqual(self.day(AUG_10).last_out_at, local(AUG_10, 18))

    def test_a_day_with_no_scans_at_all_can_be_rebuilt(self):
        self.assertEqual(self.day(AUG_12).attendance_status, S.ABSENT)
        self.add_scan(AUG_12, 9)
        self.add_scan(AUG_12, 18)
        day = self.day(AUG_12)
        self.assertEqual(day.attendance_status, S.PRESENT)
        self.assertEqual(day.worked_minutes, 540)

    def test_a_manual_scan_is_labelled_by_its_place_in_the_day(self):
        """Added between two real scans, it becomes a break, not a check-out."""
        self.punch(AUG_10, 9)
        self.punch(AUG_10, 18)
        self.add_scan(AUG_10, 13)
        self.add_scan(AUG_10, 14)
        with use_company(self.company):
            labels = list(
                self.day(AUG_10).allocations.order_by("sequence_number")
                .values_list("label", flat=True)
            )
        self.assertEqual(labels, ["check_in", "break_out", "break_in", "check_out"])

    def test_withdrawing_puts_the_day_back(self):
        self.punch(AUG_10, 9)
        correction = self.add_scan(AUG_10, 18)
        fixes.withdraw(
            actor=self.admin, company_id=self.company.pk,
            correction_id=correction.pk, note="Wrong person",
        )
        day = self.day(AUG_10)
        self.assertTrue(day.check_out_by_rule)
        correction.refresh_from_db()
        self.assertEqual(correction.status, "withdrawn")
        self.assertEqual(correction.decision_note, "Wrong person")
        # What the correction did is still on it, beside the withdrawal.
        self.assertFalse(correction.after_snapshot["check_out_by_rule"])
        self.assertTrue(correction.after_snapshot["withdrawal"]["after"]["check_out_by_rule"])
        with self.assertRaises(ValidationError):
            fixes.withdraw(actor=self.admin, company_id=self.company.pk,
                           correction_id=correction.pk, note="")

    def test_a_scan_belonging_to_the_next_day_is_refused(self):
        with self.assertRaises(ValidationError) as caught:
            fixes.add_scan(
                actor=self.admin, company_id=self.company.pk,
                employee_id=self.employee.pk, work_date=AUG_10,
                at=local(AUG_11, 10), reason="Typo",
            )
        self.assertIn("different day", str(caught.exception))
        with use_company(self.company):
            self.assertFalse(AttendanceCorrection.objects.exists())

    def test_a_scan_seconds_after_a_real_one_is_refused_as_a_repeat(self):
        self.punch(AUG_10, 9)
        with self.assertRaises(ValidationError) as caught:
            self.add_scan(AUG_10, 9, 0, 10)
        self.assertIn("repeat", str(caught.exception))

    def test_a_scan_in_the_future_or_without_a_reason_is_refused(self):
        with self.assertRaises(ValidationError):
            fixes.add_scan(
                actor=self.admin, company_id=self.company.pk,
                employee_id=self.employee.pk, work_date=datetime.date(2099, 1, 1),
                at=local(datetime.date(2099, 1, 1), 9), reason="Future",
            )
        with self.assertRaises(ValidationError):
            self.add_scan(AUG_10, 9, reason="   ")


class ChangeStatusTests(CorrectionTestCase):
    def test_an_absent_day_can_be_marked_present_and_stays_so(self):
        self.assertEqual(self.day(AUG_12).attendance_status, S.ABSENT)
        self.change_status(AUG_12, "present", reason="At the client's office")
        recalculate(self.company.pk, start=AUG_12, end=AUG_12)
        day = self.day(AUG_12)
        self.assertEqual(day.attendance_status, S.PRESENT)
        self.assertEqual(str(day.payable_fraction), "1.00")
        self.assertEqual(day.review_status, "reviewed")
        self.assertIn("At the client's office", day.note)

    def test_a_new_status_replaces_the_last(self):
        first = self.change_status(AUG_12, "half_day")
        self.change_status(AUG_12, "present")
        first.refresh_from_db()
        self.assertEqual(first.status, "superseded")
        with use_company(self.company):
            self.assertEqual(
                AttendanceCorrection.objects.filter(status="applied").count(), 1
            )
        self.assertEqual(self.day(AUG_12).attendance_status, S.PRESENT)

    def test_withdrawing_the_status_goes_back_to_what_the_scans_say(self):
        correction = self.change_status(AUG_12, "present")
        fixes.withdraw(actor=self.admin, company_id=self.company.pk,
                       correction_id=correction.pk, note="")
        self.assertEqual(self.day(AUG_12).attendance_status, S.ABSENT)

    def test_leave_holidays_and_weekly_offs_are_not_changed_here(self):
        schedule.add_weekly_offs(
            actor=self.admin, company_id=self.company.pk, values={
                "weekdays": [AUG_12.weekday()], "branch": None, "is_paid": True,
                "effective_from": datetime.date(2026, 1, 1),
            },
        )
        self.assertEqual(self.day(AUG_12).attendance_status, S.WEEKLY_OFF)
        with self.assertRaises(ValidationError):
            self.change_status(AUG_12, "present")

    def test_a_half_day_leave_day_is_changed_on_the_leave_page(self):
        """Came in on a half-day leave reads "present", but leave decides it."""
        from leaves import services as leave_services

        casual = leave_services.create_leave_type(
            actor=self.admin, company_id=self.company.pk,
            values={"code": "CAS", "name": "Casual"},
        )
        leave_services.record_leave(actor=self.admin, company_id=self.company.pk, values={
            "employee": self.employee, "leave_type": casual, "start_date": AUG_12,
            "end_date": AUG_12, "duration": "half_day", "pay_type": "paid", "reason": "",
        })
        self.punch(AUG_12, 13, 30)
        self.punch(AUG_12, 18)
        self.assertEqual(self.day(AUG_12).attendance_status, S.PRESENT)
        with self.assertRaises(ValidationError) as caught:
            self.change_status(AUG_12, "absent")
        self.assertIn("Leave page", str(caught.exception))
        with use_company(self.company):
            self.assertFalse(AttendanceCorrection.objects.exists())

    def test_only_present_half_day_or_absent(self):
        with self.assertRaises(ValidationError):
            self.change_status(AUG_12, "leave")


class ReviewTests(CorrectionTestCase):
    def test_accepting_the_rules_check_out_clears_the_review_and_stays(self):
        self.punch(AUG_10, 9)
        fixes.accept_review(
            actor=self.admin, company_id=self.company.pk,
            employee_id=self.employee.pk, work_date=AUG_10,
            reason="Left at six, confirmed",
        )
        recalculate(self.company.pk, start=AUG_10, end=AUG_10)
        day = self.day(AUG_10)
        self.assertEqual(day.review_status, "reviewed")
        self.assertTrue(day.check_out_by_rule)
        self.assertNotIn(day, fixes.review_queue(self.company.pk))

    def test_a_clean_day_or_open_overtime_cannot_be_accepted_here(self):
        self.punch(AUG_10, 9)
        self.punch(AUG_10, 18)
        with self.assertRaises(ValidationError):
            fixes.accept_review(actor=self.admin, company_id=self.company.pk,
                                employee_id=self.employee.pk, work_date=AUG_10, reason="x")
        self.punch(AUG_11, 9)
        self.punch(AUG_11, 18)
        self.punch(AUG_11, 19)  # back after the shift, never scanned out
        self.assertEqual(self.day(AUG_11).review_reason, fixes.OPEN_OVERTIME)
        with self.assertRaises(ValidationError) as caught:
            fixes.accept_review(actor=self.admin, company_id=self.company.pk,
                                employee_id=self.employee.pk, work_date=AUG_11, reason="x")
        self.assertIn("Overtime page", str(caught.exception))

    def test_the_queue_holds_both_kinds_and_nothing_else(self):
        self.punch(AUG_10, 9)                      # check-out by rule
        self.punch(AUG_11, 9)
        self.punch(AUG_11, 18)
        self.punch(AUG_11, 19)                     # open overtime
        self.punch(AUG_12, 9)
        self.punch(AUG_12, 18)                     # clean
        recalculate(self.company.pk, start=AUG_10, end=AUG_12)
        queue = fixes.review_queue(self.company.pk)
        self.assertEqual([r.work_date for r in queue], [AUG_10, AUG_11])

    def test_a_finalised_salary_month_leaves_the_queue(self):
        self.punch(AUG_10, 9)
        recalculate(self.company.pk, start=AUG_10, end=AUG_10)
        self.post_a_run()
        self.assertEqual(fixes.review_queue(self.company.pk), [])


class RulesTests(CorrectionTestCase):
    def test_a_finalised_salary_month_cannot_be_changed(self):
        self.punch(AUG_10, 9)
        correction = self.add_scan(AUG_10, 18)
        self.post_a_run()
        with self.assertRaises(ValidationError):
            self.add_scan(AUG_10, 13)
        with self.assertRaises(ValidationError):
            self.change_status(AUG_12, "present")
        with self.assertRaises(ValidationError):
            fixes.withdraw(actor=self.admin, company_id=self.company.pk,
                           correction_id=correction.pk, note="")

    def test_hr_may_fix_a_day_and_an_employee_may_not(self):
        hr = self.member(CompanyMembership.Role.HR, "hr@liv.test")
        self.change_status(AUG_12, "present", actor=hr)
        employee = self.member(CompanyMembership.Role.EMPLOYEE, "me@liv.test")
        with self.assertRaises(PermissionDenied):
            self.change_status(AUG_12, "absent", actor=employee)

    def test_another_companys_employee_cannot_be_touched(self):
        from tenants.services import onboard_company

        other = onboard_company(code="OTH", slug="oth", name="Other Ltd")
        outsider = User.objects.create_user(email="admin@oth.test", password="pw")
        CompanyMembership.all_objects.create(
            company=other, user=outsider, role=CompanyMembership.Role.COMPANY_ADMIN,
            status=CompanyMembership.Status.ACTIVE,
        )
        with self.assertRaises(PermissionDenied):
            fixes.add_scan(
                actor=outsider, company_id=other.pk, employee_id=self.employee.pk,
                work_date=AUG_10, at=local(AUG_10, 9), reason="Not mine",
            )
        self.assertFalse(AttendanceCorrection.all_objects.exists())

    def test_every_fix_is_audited_with_the_day_before_and_after(self):
        self.punch(AUG_10, 9)
        correction = self.add_scan(AUG_10, 18)
        fixes.withdraw(actor=self.admin, company_id=self.company.pk,
                       correction_id=correction.pk, note="")
        added = AuditLog.objects.get(action="attendance.scan_added")
        self.assertEqual(added.object_id, str(correction.pk))
        self.assertTrue(added.before_data["day"]["check_out_by_rule"])
        self.assertFalse(added.after_data["day"]["check_out_by_rule"])
        self.assertEqual(added.after_data["reason"], "Device was offline")
        self.assertTrue(AuditLog.objects.filter(action="attendance.correction_withdrawn").exists())

    def test_the_now_badge_sees_a_scan_added_by_hand(self):
        self.add_scan(AUG_10, 9)
        status = live_status.statuses_for(
            self.company.pk, employee_ids=[self.employee.pk], now=local(AUG_10, 10),
        )[self.employee.pk]
        self.assertEqual(status.key, "in_office")


class PageTests(CorrectionTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def fix_url(self, on):
        return reverse("attendance:attendance_day_fix", args=[self.employee.pk, on.isoformat()])

    def test_the_review_page_links_each_kind_to_the_right_place(self):
        self.punch(AUG_10, 9)
        self.punch(AUG_11, 9)
        self.punch(AUG_11, 18)
        self.punch(AUG_11, 19)
        recalculate(self.company.pk, start=AUG_10, end=AUG_11)
        with use_company(self.company):
            overtime_day = AttendanceRecord.objects.get(work_date=AUG_11)
        response = self.client.get(reverse("attendance:attendance_review"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Check-out by rule")
        self.assertContains(response, self.fix_url(AUG_10))
        self.assertContains(response, "Overtime, no check-out")
        self.assertContains(response, reverse("payroll:overtime_decide", args=[overtime_day.pk]))

    def test_adding_a_scan_from_the_page(self):
        self.punch(AUG_10, 9)
        response = self.client.post(self.fix_url(AUG_10), {
            "action": "add_scan", "scan-at_0": "2026-08-10", "scan-at_1": "18:00",
            "scan-reason": "Forgot to scan out",
        })
        self.assertRedirects(response, self.fix_url(AUG_10))
        page = self.client.get(self.fix_url(AUG_10))
        self.assertContains(page, "Added by hand")
        self.assertContains(page, "Forgot to scan out")
        self.assertContains(page, "In force")

    def test_a_refused_scan_shows_why_next_to_the_field(self):
        self.punch(AUG_10, 9)
        response = self.client.post(self.fix_url(AUG_10), {
            "action": "add_scan", "scan-at_0": "2026-08-11", "scan-at_1": "10:00",
            "scan-reason": "Typo",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "different day")

    def test_a_scan_needs_a_time_not_just_a_date(self):
        response = self.client.post(self.fix_url(AUG_10), {
            "action": "add_scan", "scan-at_0": "2026-08-10", "scan-at_1": "",
            "scan-reason": "x",
        })
        self.assertContains(response, "Enter the time of the scan.")

    def test_changing_the_status_and_withdrawing_from_the_page(self):
        self.day(AUG_12)
        self.client.post(self.fix_url(AUG_12), {
            "action": "change_status", "status-status": "present",
            "status-reason": "On site",
        })
        self.assertEqual(self.day(AUG_12).attendance_status, S.PRESENT)
        with use_company(self.company):
            correction = AttendanceCorrection.objects.get()
        response = self.client.post(
            reverse("attendance:attendance_correction_withdraw", args=[correction.pk]),
            {"note": "Mistake"},
        )
        self.assertRedirects(response, self.fix_url(AUG_12))
        self.assertEqual(self.day(AUG_12).attendance_status, S.ABSENT)

    def test_the_calendar_panel_offers_the_fix(self):
        self.punch(AUG_10, 9)
        self.day(AUG_10)
        response = self.client.get(
            reverse("attendance:attendance_day", args=[self.employee.pk, "2026-08-10"])
        )
        self.assertContains(response, self.fix_url(AUG_10))

    def test_an_employee_cannot_open_the_pages(self):
        """Refused or sent to their own panel — never shown the page."""
        self.punch(AUG_10, 9)
        self.day(AUG_10)
        self.client.force_login(self.member(CompanyMembership.Role.EMPLOYEE, "me@liv.test"))
        for url in (reverse("attendance:attendance_review"), self.fix_url(AUG_10)):
            with self.subTest(url=url):
                self.assertIn(self.client.get(url).status_code, (302, 403))
        with use_company(self.company):
            self.assertFalse(AttendanceCorrection.objects.exists())
