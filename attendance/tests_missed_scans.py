"""N11: missed-scan requests, the still-in-after-shift alert, and unusual days to review.

Built on the two-branch company (leaves/tests_branch_access.py): Head Office has
Rahim, Clerk (an Employee login) and Manny the branch manager; Chittagong has
Karim. The shift is 09:00-18:00 with no break.
"""

import datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from attendance import access, correction_services as fixes, scan_requests
from attendance.live_status import still_in_after_shift
from attendance.models import AttendanceCorrection, AttendanceRecord, MissedScanRequest, ReviewStatus
from attendance.services import EARLY_CHECK_OUT, LONG_OUTSIDE, recalculate
from common.tenant import use_company
from employees.services import create_employee
from leaves import services as leave_services
from leaves.tests_branch_access import MONDAY, TwoBranchCase

DHAKA = datetime.timezone(datetime.timedelta(hours=6))
TUESDAY = MONDAY + datetime.timedelta(days=1)


def at(day, hour, minute=0):
    return datetime.datetime(day.year, day.month, day.day, hour, minute, tzinfo=DHAKA)


class UnusualDayTests(TwoBranchCase):
    def day(self, *times, on=MONDAY):
        for hour, minute in times:
            self.punch(on, hour, minute)
        recalculate(self.company.pk, employee_ids=[self.employee.pk], start=on, end=on)
        return self.record(on)

    def test_checking_out_long_before_the_shift_end_goes_to_review(self):
        # Scanned out for tea at 11:00 and walked back in behind a colleague.
        record = self.day((9, 0), (11, 0))
        self.assertEqual(record.review_status, ReviewStatus.NEEDS_REVIEW)
        self.assertEqual(record.review_reason, EARLY_CHECK_OUT)

    def test_a_long_time_outside_during_the_shift_goes_to_review(self):
        record = self.day((9, 0), (11, 0), (14, 0), (18, 0))
        self.assertEqual(record.review_reason, LONG_OUTSIDE)

    def test_ordinary_days_stay_clear(self):
        lunch = self.day((9, 0), (13, 0), (14, 0), (18, 0))
        self.assertEqual(lunch.review_status, ReviewStatus.CLEAN)
        early_by_an_hour = self.day((9, 0), (17, 0), on=TUESDAY)
        self.assertEqual(early_by_an_hour.review_status, ReviewStatus.CLEAN)

    def test_time_away_after_the_shift_is_not_counted_as_outside(self):
        wednesday = MONDAY + datetime.timedelta(days=2)
        record = self.day((9, 0), (18, 0), (20, 0), (22, 0), on=wednesday)
        self.assertEqual(record.review_status, ReviewStatus.CLEAN)

    def test_it_can_be_accepted_or_fixed_with_the_missing_scan(self):
        self.day((9, 0), (11, 0))
        fixes.accept_review(actor=self.admin, company_id=self.company.pk,
                            employee_id=self.employee.pk, work_date=MONDAY,
                            reason="Went home ill")
        recalculate(self.company.pk, employee_ids=[self.employee.pk], start=MONDAY, end=MONDAY)
        self.assertEqual(self.record(MONDAY).review_status, ReviewStatus.REVIEWED)

        self.day((9, 0), (11, 0), on=TUESDAY)
        fixes.add_scan(actor=self.admin, company_id=self.company.pk,
                       employee_id=self.employee.pk, work_date=TUESDAY,
                       at=at(TUESDAY, 11, 15), reason="Came back with Karim")
        fixes.add_scan(actor=self.admin, company_id=self.company.pk,
                       employee_id=self.employee.pk, work_date=TUESDAY,
                       at=at(TUESDAY, 18), reason="Left at the end")
        record = self.record(TUESDAY)
        self.assertEqual(record.review_status, ReviewStatus.CLEAN)
        self.assertEqual(record.attendance_status, "present")

    def test_a_half_day_leave_is_not_flagged_for_leaving_early(self):
        leave_services.record_leave(actor=self.admin, company_id=self.company.pk, values={
            "employee": self.employee, "leave_type": self.leave_type, "start_date": MONDAY,
            "end_date": MONDAY, "duration": "half_day", "pay_type": "paid", "reason": ""})
        record = self.day((9, 0), (13, 0))
        self.assertEqual(record.review_status, ReviewStatus.CLEAN)

    def test_the_review_list_names_them(self):
        self.day((9, 0), (11, 0))
        self.client.force_login(self.admin)
        page = self.client.get(reverse("attendance:attendance_review"))
        self.assertContains(page, "Left early")
        fix = self.client.get(reverse("attendance:attendance_day_fix",
                                      args=[self.employee.pk, MONDAY.isoformat()]))
        self.assertContains(fix, "Accept the day as it is")


class StillInAfterShiftTests(TwoBranchCase):
    def setUp(self):
        super().setUp()
        self.punch(MONDAY, 9)
        self.far_punch(MONDAY, 9)

    def names(self, rows):
        return [employee.first_name for employee, _status in rows]

    def test_listed_two_hours_after_the_shift_ends(self):
        self.assertEqual(still_in_after_shift(self.company.pk, now=at(MONDAY, 19)), [])
        rows = still_in_after_shift(self.company.pk, now=at(MONDAY, 20, 30))
        self.assertEqual(self.names(rows), ["Karim", "Rahim"])
        self.assertEqual((rows[1][1].since_text, rows[1][1].shift_end_text), ("09:00", "18:00"))

    def test_someone_who_scanned_out_is_not_listed(self):
        self.punch(MONDAY, 18, 30)
        self.assertEqual(self.names(still_in_after_shift(self.company.pk, now=at(MONDAY, 21))), ["Karim"])

    def test_limited_to_the_branches_where_you_may_fix_attendance(self):
        later = at(MONDAY, 21)
        self.assertEqual(self.names(access.still_in_for(self.manager, self.company.pk, now=later)), ["Rahim"])
        self.assertEqual(len(access.still_in_for(self.hr, self.company.pk, now=later)), 2)
        self.assertEqual(access.still_in_for(self.clerk_user, self.company.pk, now=later), [])


class MissedScanRequestTests(TwoBranchCase):
    def submit(self, when=None, *, on=MONDAY, actor=None, reason="Walked in behind Karim"):
        return scan_requests.submit(
            actor=actor or self.clerk_user, company_id=self.company.pk,
            work_date=on, at=when or at(on, 9, 5), reason=reason,
        )

    def decide(self, request, actor, approve=True, note=""):
        return scan_requests.decide(actor=actor, company_id=self.company.pk,
                                    request_id=request.pk, approve=approve, note=note)

    def clerk_day(self):
        with use_company(self.company):
            return AttendanceRecord.objects.filter(employee=self.clerk, work_date=MONDAY).first()

    def test_asking_changes_nothing_until_approved(self):
        request = self.submit()
        self.assertEqual((request.status, request.branch_id), ("pending", self.branch.pk))
        with use_company(self.company):
            self.assertFalse(AttendanceCorrection.objects.filter(employee=self.clerk).exists())
        record = self.clerk_day()
        self.assertTrue(record is None or record.first_in_at is None)

    def test_the_branch_manager_approves_and_the_scan_counts(self):
        request = self.decide(self.submit(), self.manager)
        self.assertEqual(request.status, "approved")
        self.assertEqual(request.correction.correction_type, "add_scan")
        self.assertEqual(self.clerk_day().first_in_at, at(MONDAY, 9, 5))

    def test_rejecting_needs_a_note(self):
        request = self.submit()
        with self.assertRaises(ValidationError):
            self.decide(request, self.manager, approve=False)
        request = self.decide(request, self.manager, approve=False, note="The gate log shows 10:40")
        self.assertEqual((request.status, request.correction), ("rejected", None))

    def test_a_scan_that_does_not_fit_the_day_is_refused_when_asked(self):
        with self.assertRaises(ValidationError) as caught:
            self.submit(at(TUESDAY, 10))
        self.assertIn("not part of this day", str(caught.exception))
        with self.assertRaises(ValidationError):
            self.submit(at(MONDAY, 9) + datetime.timedelta(days=3650))
        self.submit()
        with self.assertRaises(ValidationError):
            self.submit()  # the same scan again

    def test_only_whoever_may_fix_that_branch_sees_and_decides_it(self):
        request = self.submit()
        self.grant("attendance.fix", self.unit, to=self.far)
        for user, sees in ((self.manager, True), (self.hr, True), (self.admin, True)):
            _m, queryset = scan_requests.reviewable(user, self.company.pk)
            with use_company(self.company):
                self.assertEqual(queryset.filter(pk=request.pk).exists(), sees)
        with self.assertRaises(PermissionDenied):
            scan_requests.reviewable(self.clerk_user, self.company.pk)
        far_user = self.member("karim@liv.test", "employee")
        with use_company(self.company):
            self.far.user = far_user
            self.far.save(update_fields=["user"])
        with self.assertRaises(PermissionDenied):
            self.decide(request, far_user)
        self.client.force_login(far_user)
        url = reverse("attendance:missed_scan_decide", args=[request.pk])
        self.assertEqual(self.client.post(url, {"decision": "approve"}).status_code, 404)

    def test_nobody_decides_their_own(self):
        manny = create_employee(
            company=self.company, first_name="Manny", employee_code="E9",
            branch=self.branch, department=self.hq_department, designation=self.hq_designation,
            effective_from=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
            pay_basis="monthly", base_rate="30000",
        )["employee"]
        with use_company(self.company):
            manny.user = self.manager
            manny.save(update_fields=["user"])
        request = self.submit(actor=self.manager)
        _m, queryset = scan_requests.reviewable(self.manager, self.company.pk)
        with use_company(self.company):
            self.assertFalse(queryset.filter(pk=request.pk).exists())
        with self.assertRaises(PermissionDenied):
            self.decide(request, self.manager)
        self.decide(request, self.hr)

    def test_a_scan_that_became_a_repeat_stays_waiting(self):
        request = self.submit(at(MONDAY, 9, 5))
        with use_company(self.company):
            self.clerk.refresh_from_db()
        fixes.add_scan(actor=self.admin, company_id=self.company.pk, employee_id=self.clerk.pk,
                       work_date=MONDAY, at=at(MONDAY, 9, 5), reason="Device backlog")
        with self.assertRaises(ValidationError):
            self.decide(request, self.manager)
        request.refresh_from_db()
        self.assertEqual(request.status, "pending")

    def test_withdrawing_while_it_waits(self):
        request = self.submit()
        scan_requests.withdraw(actor=self.clerk_user, company_id=self.company.pk, request_id=request.pk)
        request.refresh_from_db()
        self.assertEqual(request.status, "withdrawn")
        with self.assertRaises(ValidationError):
            self.decide(request, self.manager)

    def test_the_pages(self):
        self.client.force_login(self.clerk_user)
        self.assertEqual(self.client.get(reverse("me:missed_scans")).status_code, 200)
        response = self.client.post(reverse("me:missed_scan_report"), {
            "work_date": MONDAY.isoformat(), "at_0": MONDAY.isoformat(), "at_1": "09:05",
            "reason": "Walked in behind Karim",
        })
        self.assertRedirects(response, reverse("me:missed_scans"))
        self.assertContains(self.client.get(reverse("me:missed_scans")), "Waiting")
        # An Employee login cannot open the deciding pages.
        self.assertRedirects(self.client.get(reverse("attendance:missed_scan_list")), reverse("me:home"))

        request = MissedScanRequest.all_objects.get(employee=self.clerk)
        self.client.force_login(self.manager)
        page = self.client.get(reverse("attendance:missed_scan_list"))
        self.assertContains(page, "Clerk")
        self.assertContains(self.client.get(reverse("attendance:attendance_review")), "Missed scans")
        url = reverse("attendance:missed_scan_decide", args=[request.pk])
        self.assertContains(self.client.get(url), "Approve and add the scan")
        self.assertRedirects(self.client.post(url, {"decision": "approve", "note": ""}),
                             reverse("attendance:missed_scan_list"))
        request.refresh_from_db()
        self.assertEqual(request.status, "approved")
