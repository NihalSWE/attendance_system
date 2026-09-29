"""Manual attendance (Ajay, 2026-09-26): the missing case named, entered by
someone else for the person, waiting for approval - and an approved day that
stays out of Days to review when salary is generated.

Monday 10 August 2026, shift 9:00-18:00. Rahim (Head Office) has a check-in
only; Clerk (Head Office) has no scans; Karim is in Chittagong.
"""

import datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from attendance import scan_requests
from attendance.correction_services import review_queue
from attendance.models import AttendanceRecord, MissedScanRequest, ReviewStatus
from attendance.services import calculate_attendance, recalculate
from auditlog.models import AuditLog
from common.tenant import use_company
from leaves.tests_branch_access import DHAKA, MONDAY, TwoBranchCase

Kind = MissedScanRequest.Kind


def at(hour, minute=0, day=MONDAY):
    return datetime.datetime(day.year, day.month, day.day, hour, minute, tzinfo=DHAKA)


class ManualCase(TwoBranchCase):
    def setUp(self):
        super().setUp()
        self.punch(MONDAY, 9)                       # Rahim came in; no check-out
        recalculate(self.company.pk, start=MONDAY, end=MONDAY)

    def enter(self, employee=None, actor=None, kind=Kind.CHECK_OUT, first=None, second=None,
              reason="Left through the side gate"):
        return scan_requests.enter_for(
            actor=actor or self.hr, company_id=self.company.pk,
            employee_id=(employee or self.employee).pk, work_date=MONDAY, kind=kind,
            at=first, at_out=second, reason=reason)

    def decide(self, request, actor=None, approve=True, note=""):
        return scan_requests.decide(actor=actor or self.manager, company_id=self.company.pk,
                                    request_id=request.pk, approve=approve, note=note)

    def day(self, employee=None):
        with use_company(self.company):
            return AttendanceRecord.objects.get(employee=employee or self.employee, work_date=MONDAY)


class EnterForSomeoneTests(ManualCase):
    def test_hr_enters_a_missing_check_out_and_it_waits(self):
        request = self.enter(first=at(18))
        self.assertEqual((request.status, request.kind), ("pending", Kind.CHECK_OUT))
        self.assertTrue(scan_requests.entered_by_someone_else(request))
        # Nothing changes yet: the day is still closed by the rule at 18:00.
        self.assertTrue(self.day().check_out_by_rule)
        self.assertEqual(AuditLog.objects.filter(
            action="attendance.missed_scan_requested").last().after_data["kind"], "check_out")

    def test_approved_by_someone_else_it_counts(self):
        request = self.decide(self.enter(first=at(18)))
        self.assertEqual(request.status, "approved")
        day = self.day()
        self.assertEqual((day.last_out_at, day.check_out_by_rule), (at(18), False))
        self.assertIn("Entered for Rahim by hr@liv.test", request.correction.reason)

    def test_whoever_entered_it_does_not_approve_it(self):
        request = self.enter(first=at(18))
        with self.assertRaisesMessage(PermissionDenied, "someone else"):
            self.decide(request, actor=self.hr)

    def test_the_owner_or_admin_may_approve_their_own_entry(self):
        request = self.enter(first=at(18), actor=self.admin)
        self.assertEqual(self.decide(request, actor=self.admin).status, "approved")

    def test_not_for_someone_whose_attendance_they_do_not_see(self):
        with self.assertRaises(PermissionDenied):
            self.enter(employee=self.far, actor=self.manager, first=at(18))

    def test_not_for_oneself(self):
        # HR who is also an employee uses Report a missed scan for their own.
        with use_company(self.company):
            self.employee.user = self.hr
            self.employee.save(update_fields=["user"])
        with self.assertRaisesMessage(PermissionDenied, "Report a missed scan"):
            self.enter(actor=self.hr, first=at(18))


class CasesTests(ManualCase):
    def test_a_whole_missing_day_uses_the_shift(self):
        request = self.enter(employee=self.clerk, kind=Kind.WHOLE_DAY)
        self.assertEqual((request.scan_at, request.scan_out_at), (at(9), at(18)))
        self.decide(request)
        day = self.day(self.clerk)
        self.assertEqual((day.attendance_status, day.first_in_at, day.last_out_at),
                         ("present", at(9), at(18)))
        self.assertIsNotNone(MissedScanRequest.all_objects.get(pk=request.pk).correction_out)

    def test_both_missing_takes_two_times_in_order(self):
        with self.assertRaisesMessage(ValidationError, "after the check-in"):
            self.enter(employee=self.clerk, kind=Kind.BOTH, first=at(17), second=at(9))
        with self.assertRaises(ValidationError):
            self.enter(employee=self.clerk, kind=Kind.BOTH, first=at(9))   # no check-out
        request = self.enter(employee=self.clerk, kind=Kind.BOTH, first=at(9), second=at(17, 30))
        self.decide(request)
        self.assertEqual(self.day(self.clerk).worked_minutes, 8 * 60 + 30)

    def test_a_day_with_no_shift_cannot_be_a_whole_day(self):
        # A day with a shift always has its times; no shift means no day at all.
        from unittest import mock

        with use_company(self.company):
            AttendanceRecord.objects.filter(employee=self.clerk, work_date=MONDAY).delete()
        with mock.patch("attendance.services.refresh"),                 self.assertRaisesMessage(ValidationError, "no shift"):
            scan_requests._times(self.company.pk, self.clerk, MONDAY, Kind.WHOLE_DAY, None, None)

    def test_the_employee_names_the_case_too(self):
        request = scan_requests.submit(actor=self.clerk_user, company_id=self.company.pk,
                                       work_date=MONDAY, at=None, reason="Worked at the site",
                                       kind=Kind.WHOLE_DAY)
        self.assertEqual(request.kind, Kind.WHOLE_DAY)
        self.assertFalse(scan_requests.entered_by_someone_else(request))


class StaysOutOfReviewTests(ManualCase):
    def test_an_approved_early_leave_is_not_asked_about_again_when_salary_is_generated(self):
        # Clerk: both missing, out at 15:30 - long before 18:00, so the day
        # would be flagged "checked out long before the shift end".
        request = self.enter(employee=self.clerk, kind=Kind.BOTH, first=at(9), second=at(15, 30))
        self.decide(request)
        self.assertEqual(self.day(self.clerk).review_status, ReviewStatus.REVIEWED)
        # Generating salary recalculates the whole month; the day stays reviewed.
        calculate_attendance(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        self.assertEqual(self.day(self.clerk).review_status, ReviewStatus.REVIEWED)
        self.assertNotIn(self.clerk.pk, [r.employee_id for r in review_queue(self.company.pk)])

    def test_the_same_early_leave_added_by_hand_is_still_asked_about(self):
        # The control: without the approval's acceptance, such a day waits.
        from attendance.correction_services import add_scan

        add_scan(actor=self.admin, company_id=self.company.pk, employee_id=self.clerk.pk,
                 work_date=MONDAY, at=at(9), reason="By hand")
        add_scan(actor=self.admin, company_id=self.company.pk, employee_id=self.clerk.pk,
                 work_date=MONDAY, at=at(15, 30), reason="By hand")
        self.assertEqual(self.day(self.clerk).review_status, ReviewStatus.NEEDS_REVIEW)


class PageTests(ManualCase):
    def test_the_profile_offers_it_in_a_modal(self):
        # Their branch manager (HR does not hold "view employees", so has no profile).
        self.client.force_login(self.manager)
        page = self.client.get(reverse("organization:employee_detail", args=[self.employee.pk]))
        self.assertContains(page, 'data-open-dialog="missing-dialog"')
        self.assertContains(page, reverse("attendance:missed_scan_enter", args=[self.employee.pk]))

    def test_sending_it_goes_back_to_their_profile(self):
        self.client.force_login(self.hr)
        response = self.client.post(
            reverse("attendance:missed_scan_enter", args=[self.clerk.pk]),
            {"kind": "whole_day", "work_date": MONDAY.isoformat(), "reason": "At the site"})
        self.assertRedirects(response, reverse("organization:employee_detail",
                                               args=[self.clerk.pk]) + "#attendance",
                             fetch_redirect_response=False)
        self.assertTrue(MissedScanRequest.all_objects.filter(employee=self.clerk).exists())

    def test_a_missing_time_is_said_on_the_page(self):
        self.client.force_login(self.hr)
        page = self.client.post(reverse("attendance:missed_scan_enter", args=[self.clerk.pk]),
                                {"kind": "both", "work_date": MONDAY.isoformat(),
                                 "at_0": MONDAY.isoformat(), "at_1": "09:00",
                                 "reason": "x"})
        self.assertContains(page, "Enter the check-out time.")

    def test_the_approver_sees_the_case_and_who_entered_it(self):
        request = self.enter(employee=self.clerk, kind=Kind.BOTH, first=at(9), second=at(17))
        self.client.force_login(self.manager)
        page = self.client.get(reverse("attendance:missed_scan_decide", args=[request.pk]))
        self.assertContains(page, "Missing check-in and check-out")
        self.assertContains(page, "Entered by")
        self.assertContains(page, "hr@liv.test")

    def test_an_employee_login_cannot_enter_for_others(self):
        self.client.force_login(self.clerk_user)
        response = self.client.get(reverse("attendance:missed_scan_enter", args=[self.employee.pk]))
        self.assertNotEqual(response.status_code, 200)


class DayFromTheScanTests(ManualCase):
    """One calendar per time (Nihal, 2026-09-29): the attendance day is asked
    only for a whole missing day; a scan's day is its own date."""

    def post(self, **values):
        self.client.force_login(self.admin)
        return self.client.post(reverse("attendance:missed_scan_enter", args=[self.clerk.pk]),
                                {"reason": "Forgot the card", "approve_now": "on", **values})

    def test_both_missing_without_a_day_counts_on_the_scans_date(self):
        response = self.post(kind="both", at_0=MONDAY.isoformat(), at_1="09:00",
                             at_out_0=MONDAY.isoformat(), at_out_1="18:00")
        self.assertEqual(response.status_code, 302)
        request = MissedScanRequest.all_objects.get(employee=self.clerk)
        self.assertEqual((request.work_date, request.status), (MONDAY, "approved"))
        self.assertEqual(self.day(self.clerk).attendance_status, "present")

    def test_a_date_left_in_the_hidden_day_box_is_ignored(self):
        # The modal starts the day box on today; hidden, it must not overrule the scan.
        self.post(kind="check_in", work_date="2026-08-20", at_0=MONDAY.isoformat(), at_1="09:00")
        self.assertEqual(MissedScanRequest.all_objects.get(employee=self.clerk).work_date, MONDAY)

    def test_a_whole_day_still_asks_for_the_day(self):
        page = self.post(kind="whole_day")
        self.assertContains(page, "Choose the day that is missing.")
        self.assertFalse(MissedScanRequest.all_objects.filter(employee=self.clerk).exists())

    def test_the_service_works_the_day_out_for_the_employee_too(self):
        request = scan_requests.submit(actor=self.clerk_user, company_id=self.company.pk,
                                       work_date=None, at=at(9), reason="Card left at home",
                                       kind=Kind.CHECK_IN)
        self.assertEqual(request.work_date, MONDAY)

    def test_the_form_shows_the_day_only_for_a_whole_day(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("attendance:missed_scan_enter", args=[self.clerk.pk]))
        self.assertContains(page, 'data-missing-time="work_date"')


class HrEntryPointTests(ManualCase):
    """HR holds no "view employees", so has no profile to start from: Missed
    scans offers "Enter missing attendance" with a person picker."""

    def test_hr_starts_from_missed_scans(self):
        self.client.force_login(self.hr)
        page = self.client.get(reverse("attendance:missed_scan_list"))
        self.assertContains(page, 'data-open-dialog="pick-dialog"')
        self.assertIn(self.far, page.context["people"])          # HR: every branch
        response = self.client.get(reverse("attendance:missed_scan_pick"),
                                   {"employee": str(self.far.pk)})
        self.assertRedirects(response, reverse("attendance:missed_scan_enter",
                                               args=[self.far.pk]),
                             fetch_redirect_response=False)

    def test_a_branch_manager_picks_only_their_branch(self):
        self.client.force_login(self.manager)
        page = self.client.get(reverse("attendance:missed_scan_list"))
        self.assertNotIn(self.far, page.context["people"])
        response = self.client.get(reverse("attendance:missed_scan_pick"),
                                   {"employee": str(self.far.pk)}, follow=True)
        self.assertContains(response, "Choose someone whose attendance you look after.")


class ApproveNowTests(ManualCase):
    """The owner or company admin enters and approves in one step (Nihal,
    2026-09-28: an entry by them did not change the day until approved)."""

    def post(self, user, **extra):
        self.client.force_login(user)
        return self.client.post(reverse("attendance:missed_scan_enter", args=[self.clerk.pk]), {
            "kind": "both", "work_date": MONDAY.isoformat(),
            "at_0": MONDAY.isoformat(), "at_1": "09:00",
            "at_out_0": MONDAY.isoformat(), "at_out_1": "18:00",
            "reason": "Forgot the card", **extra})

    def test_the_admin_saves_it_and_the_day_is_present_at_once(self):
        response = self.post(self.admin, approve_now="on")
        self.assertEqual(response.status_code, 302)
        request = MissedScanRequest.all_objects.get(employee=self.clerk)
        self.assertEqual(request.status, MissedScanRequest.Status.APPROVED)
        self.assertEqual(request.decided_by, self.admin)
        self.assertEqual(self.day(self.clerk).attendance_status, "present")
        self.client.get(reverse("organization:employee_detail", args=[self.clerk.pk]))
        messages = [str(m) for m in response.wsgi_request._messages]
        self.assertIn("Saved and approved", " ".join(messages))

    def test_unticked_it_waits_as_before(self):
        self.post(self.admin)
        self.assertEqual(MissedScanRequest.all_objects.get(employee=self.clerk).status,
                         MissedScanRequest.Status.PENDING)
        self.assertNotEqual(self.day(self.clerk).attendance_status, "present")

    def test_hr_is_not_offered_it_and_cannot_force_it(self):
        self.client.force_login(self.hr)
        page = self.client.get(reverse("attendance:missed_scan_enter", args=[self.clerk.pk]))
        self.assertNotContains(page, "Approve it now")
        self.post(self.hr, approve_now="on")
        self.assertEqual(MissedScanRequest.all_objects.get(employee=self.clerk).status,
                         MissedScanRequest.Status.PENDING)

    def test_the_profile_modal_offers_it_to_the_admin(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("organization:employee_detail", args=[self.clerk.pk]))
        self.assertContains(page, "Approve it now")
        self.assertContains(page, "You may approve it yourself")
