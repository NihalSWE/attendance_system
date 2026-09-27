"""Leave, Ajay's handover phase 1 (2026-09-27): cancel some days of a leave,
change an approved leave, and attendance worked out again at once.

Rahim (Head Office) takes Casual leave Monday 10 to Wednesday 12 August 2026.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from attendance.models import AttendanceRecord
from auditlog.models import AuditLog
from common.tenant import use_company
from leaves import services
from leaves.models import LeaveDay, LeaveRequest, LeaveRequestSegment
from leaves.tests_branch_access import MONDAY, TwoBranchCase

TUESDAY = MONDAY + datetime.timedelta(days=1)
WEDNESDAY = MONDAY + datetime.timedelta(days=2)
THURSDAY = MONDAY + datetime.timedelta(days=3)


class LeaveCase(TwoBranchCase):
    def setUp(self):
        super().setUp()
        self.leave = self.record(self.employee, MONDAY, WEDNESDAY)

    def record(self, employee, start, end, leave_type=None):
        return services.record_leave(actor=self.admin, company_id=self.company.pk, values={
            "employee": employee, "leave_type": leave_type or self.leave_type,
            "start_date": start, "end_date": end, "pay_type": "paid", "reason": "Family",
        })

    def live(self, leave=None):
        with use_company(self.company):
            return [d.work_date for d in services.live_days(leave or self.leave)]

    def status_on(self, day, employee=None):
        with use_company(self.company):
            return AttendanceRecord.objects.get(employee=employee or self.employee,
                                                work_date=day).attendance_status

    def fresh(self):
        return LeaveRequest.all_objects.get(pk=self.leave.pk)


class AttendanceAtOnceTests(LeaveCase):
    def test_recording_and_cancelling_change_the_day_at_once(self):
        # No refresh in between: the days are worked out when the leave is written.
        self.assertEqual(self.status_on(TUESDAY), "leave")
        services.cancel_leave(actor=self.admin, company_id=self.company.pk,
                              request_id=self.leave.pk, reason="Not needed")
        self.assertNotEqual(self.status_on(TUESDAY), "leave")


class CancelSomeDaysTests(LeaveCase):
    def cancel(self, days, reason="Came back early"):
        return services.cancel_days(actor=self.admin, company_id=self.company.pk,
                                    request_id=self.leave.pk, work_dates=days, reason=reason)

    def test_some_days_stop_counting_and_the_rest_stay(self):
        leave = self.cancel([TUESDAY])
        self.assertEqual(leave.status, LeaveRequest.Status.PARTIALLY_CANCELLED)
        self.assertEqual(self.live(), [MONDAY, WEDNESDAY])
        self.assertEqual(self.status_on(TUESDAY), "absent")         # at once
        self.assertEqual(self.status_on(MONDAY), "leave")
        entry = AuditLog.objects.get(action="leave.days_cancelled")
        self.assertEqual(entry.after_data["dates"], [TUESDAY.isoformat()])
        self.assertEqual(entry.after_data["days_left"], 2)

    def test_the_freed_days_are_back_in_the_allowance(self):
        self.leave_type.days_per_year = Decimal("3")
        self.leave_type.save(update_fields=["days_per_year"])
        with use_company(self.company):
            self.assertEqual(services.allowance_left(self.employee, self.leave_type, 2026), 0)
        self.cancel([TUESDAY])
        with use_company(self.company):
            self.assertEqual(services.allowance_left(self.employee, self.leave_type, 2026), 1)

    def test_every_day_chosen_cancels_the_whole_leave(self):
        self.assertEqual(self.cancel([MONDAY, TUESDAY, WEDNESDAY]).status,
                         LeaveRequest.Status.CANCELLED)

    def test_a_day_that_is_not_part_of_it_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "not part of this leave"):
            self.cancel([THURSDAY])

    def test_a_reason_is_needed(self):
        with self.assertRaisesMessage(ValidationError, "Say why"):
            self.cancel([TUESDAY], reason=" ")

    def test_twice_then_whole(self):
        self.cancel([TUESDAY])
        self.cancel([WEDNESDAY])
        self.assertEqual(self.live(), [MONDAY])
        services.cancel_leave(actor=self.admin, company_id=self.company.pk,
                              request_id=self.leave.pk, reason="All of it")
        self.assertEqual(self.live(), [])
        self.assertEqual(self.fresh().status, LeaveRequest.Status.CANCELLED)

    def test_a_branch_manager_elsewhere_cannot(self):
        far_leave = self.record(self.far, MONDAY, TUESDAY)
        with self.assertRaises(PermissionDenied):
            services.cancel_days(actor=self.manager, company_id=self.company.pk,
                                 request_id=far_leave.pk, work_dates=[MONDAY], reason="x")


class ChangeLeaveTests(LeaveCase):
    def change(self, **values):
        return services.amend_leave(actor=self.admin, company_id=self.company.pk,
                                    request_id=self.leave.pk, values={
                                        "leave_type": self.leave_type, "start_date": MONDAY,
                                        "end_date": TUESDAY, "pay_type": "paid",
                                        "reason": "Shorter", **values})

    def test_the_dates_change_on_the_same_leave(self):
        leave = self.change()
        self.assertEqual(leave.pk, self.leave.pk)
        self.assertEqual(leave.status, LeaveRequest.Status.APPROVED)
        self.assertEqual(self.live(), [MONDAY, TUESDAY])
        self.assertEqual(self.status_on(WEDNESDAY), "absent")       # at once
        with use_company(self.company):
            parts = list(leave.segments.order_by("sequence_number")
                         .values_list("status", "end_date"))
        self.assertEqual(parts, [(LeaveRequestSegment.Status.CANCELLED, WEDNESDAY),
                                 (LeaveRequestSegment.Status.ACTIVE, TUESDAY)])
        entry = AuditLog.objects.get(action="leave.amended")
        self.assertEqual(entry.before_data["days"], [d.isoformat() for d in
                                                     (MONDAY, TUESDAY, WEDNESDAY)])
        self.assertEqual(entry.after_data["end_date"], TUESDAY.isoformat())

    def test_pay_can_change(self):
        self.change(pay_type="unpaid", end_date=WEDNESDAY)
        with use_company(self.company):
            self.assertEqual(set(LeaveDay.objects.filter(
                request_segment__leave_request=self.leave, status="approved")
                .values_list("approved_pay_type", flat=True)), {"unpaid"})

    def test_a_clash_is_refused_and_nothing_changes(self):
        self.record(self.employee, THURSDAY, THURSDAY)
        with self.assertRaisesMessage(ValidationError, "already on leave"):
            self.change(end_date=THURSDAY)
        self.assertEqual(self.live(), [MONDAY, TUESDAY, WEDNESDAY])   # rolled back
        self.assertEqual(self.fresh().status, LeaveRequest.Status.APPROVED)

    def test_over_the_allowance_is_refused(self):
        self.leave_type.days_per_year = Decimal("3")
        self.leave_type.save(update_fields=["days_per_year"])
        with self.assertRaisesMessage(ValidationError, "days left"):
            self.change(end_date=THURSDAY)
        self.assertEqual(self.live(), [MONDAY, TUESDAY, WEDNESDAY])

    def test_only_approved_leave(self):
        services.cancel_leave(actor=self.admin, company_id=self.company.pk,
                              request_id=self.leave.pk, reason="x")
        with self.assertRaisesMessage(ValidationError, "Only approved leave"):
            self.change()


class ListAndReportTests(LeaveCase):
    def test_the_list_shows_the_current_dates_and_the_days_that_count(self):
        services.amend_leave(actor=self.admin, company_id=self.company.pk,
                             request_id=self.leave.pk, values={
                                 "leave_type": self.leave_type, "start_date": MONDAY,
                                 "end_date": TUESDAY, "pay_type": "paid", "reason": ""})
        self.client.force_login(self.admin)
        page = self.client.get(reverse("leaves:leave_list"), {"month": "8", "year": "2026"})
        row = next(r for r in page.context["page"].object_list if r.pk == self.leave.pk)
        self.assertEqual((row.first_day, row.last_day, row.table_units),
                         (MONDAY, TUESDAY, Decimal("2")))
        self.assertContains(page, reverse("leaves:leave_amend", args=[self.leave.pk]))

    def test_a_partly_cancelled_leave_counts_its_live_days_everywhere(self):
        services.cancel_days(actor=self.admin, company_id=self.company.pk,
                             request_id=self.leave.pk, work_dates=[TUESDAY], reason="Back")
        self.client.force_login(self.admin)
        page = self.client.get(reverse("leaves:leave_list"), {"month": "8", "year": "2026"})
        row = next(r for r in page.context["page"].object_list if r.pk == self.leave.pk)
        self.assertEqual(row.table_units, Decimal("2"))
        report = self.client.get(reverse("reports:leave"), {
            "date_from": "2026-08-01", "date_to": "2026-08-31"})
        rows = report.context["result"].rows
        self.assertEqual([(r[1], r[6]) for r in rows], [("Rahim", "2")])   # approved includes it


class PageTests(LeaveCase):
    def test_the_cancel_page_offers_whole_or_some_days(self):
        self.client.force_login(self.admin)
        url = reverse("leaves:leave_cancel", args=[self.leave.pk])
        page = self.client.get(url)
        self.assertContains(page, "Some days")
        self.assertContains(page, TUESDAY.isoformat())
        response = self.client.post(url, {"what": "some", "work_dates": [TUESDAY.isoformat()],
                                          "reason": "Back early"}, follow=True)
        self.assertContains(response, "Some days cancelled")
        self.assertEqual(self.live(), [MONDAY, WEDNESDAY])

    def test_some_days_without_choosing_any_is_said(self):
        self.client.force_login(self.admin)
        page = self.client.post(reverse("leaves:leave_cancel", args=[self.leave.pk]),
                                {"what": "some", "reason": "x"})
        self.assertContains(page, "Choose the days to cancel.")

    def test_the_whole_leave_as_before(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("leaves:leave_cancel", args=[self.leave.pk]),
                                    {"what": "all", "reason": ""}, follow=True)
        self.assertContains(response, "Leave cancelled.")

    def test_the_change_page_starts_filled_in(self):
        self.client.force_login(self.admin)
        url = reverse("leaves:leave_amend", args=[self.leave.pk])
        form = self.client.get(url).context["form"]
        self.assertEqual((form.initial["start_date"], form.initial["end_date"]),
                         (MONDAY, WEDNESDAY))
        self.assertNotIn("employee", form.fields)
        response = self.client.post(url, {
            "leave_type": self.leave_type.pk, "start_date": MONDAY.isoformat(),
            "end_date": TUESDAY.isoformat(), "duration": "full_day", "pay_type": "paid",
            "reason": "Shorter"}, follow=True)
        self.assertContains(response, "Leave changed.")
        self.assertEqual(self.live(), [MONDAY, TUESDAY])
