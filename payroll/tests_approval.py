"""A11: a month's salary is submitted, then approved - which finalises it.

Draft -> Waiting for approval -> Finalised. Whoever prepares salary submits it
(a branch manager with salary access; the owner/administrator can too), and
the owner/administrator approves it. A company has exactly one
owner/administrator (accounts: uniq_current_company_administrator), so when
they prepared the month themselves they approve their own - there is nobody
else. The approver can send it back, and whoever submitted it can take it
back, with a reason. While it waits nothing can change the numbers: Generate
and the bonus/deduction lines refuse.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from accounts.models import CompanyMembership, User
from attendance.services import locked_ranges
from auditlog.models import AuditLog
from common.tenant import use_company
from payroll.models import PayrollRecord, PayrollRun
from payroll.services import (
    add_adjustment,
    approval_blocker,
    attendance_changed_since,
    approve_payroll,
    generate_payroll,
    remove_adjustment,
    return_payroll,
    submit_payroll,
)
from payroll.tests_overtime import OvertimeBase
from attendance.tests_live import DHAKA

AUGUST = (datetime.date(2026, 8, 1), datetime.date(2026, 8, 31))
AUGUST_QUERY = {"month": 8, "year": 2026}


def finalise_payroll(*, actor, company_id, year, month):
    """Test helper: submit then approve as one person (a single-admin company).

    Tests that only need a finalised month use this; there is deliberately no
    one-step finalise in the real code.
    """
    submit_payroll(actor=actor, company_id=company_id, year=year, month=month)
    return approve_payroll(actor=actor, company_id=company_id, year=year, month=month)


class ApprovalCase(OvertimeBase):
    def setUp(self):
        super().setUp()
        self.work(datetime.date(2026, 8, 10), (9, 0), (18, 0))
        self.month(generate_payroll)

    def month(self, service, **extra):
        return service(actor=extra.pop("actor", self.admin), company_id=self.company.pk,
                       year=2026, month=8, **extra)

    def member(self, role, email=None, branches=()):
        user = User.objects.create_user(email=email or f"{role}@approval.test")
        membership = CompanyMembership.all_objects.create(
            company=self.company, user=user, role=role, status="active")
        with use_company(self.company):
            membership.allowed_branches.set(branches)
        return user

    def manager(self):
        """A branch manager of Head Office: holds salary.prepare there."""
        return self.member("manager", branches=[self.branch])

    def latest_run(self):
        with use_company(self.company):
            return PayrollRun.objects.order_by("-pk").first()


class TwoPeopleTests(ApprovalCase):
    def test_a_branch_manager_submits_and_the_admin_approves(self):
        manager = self.manager()
        run = self.month(submit_payroll, actor=manager)
        self.assertEqual((run.status, run.submitted_by), (PayrollRun.Status.SUBMITTED, manager))
        run = self.month(approve_payroll)
        self.assertEqual((run.status, run.posted_by), (PayrollRun.Status.POSTED, self.admin))
        self.assertIn(AUGUST, locked_ranges(self.company.pk))
        finalised = AuditLog.objects.get(action="payroll.finalised")
        self.assertEqual(finalised.after_data["submitted_by"], manager.pk)
        self.assertEqual(finalised.after_data["approved_by"], self.admin.pk)
        self.assertTrue(AuditLog.objects.filter(action="payroll.submitted").exists())

    def test_a_branch_manager_cannot_approve(self):
        manager = self.manager()
        self.month(submit_payroll, actor=manager)
        with self.assertRaises(PermissionDenied):
            self.month(approve_payroll, actor=manager)
        self.assertIn("owner or company administrator",
                      approval_blocker(manager, self.company.pk, self.latest_run()))
        self.assertEqual(self.latest_run().status, PayrollRun.Status.SUBMITTED)

    def test_the_admin_who_prepared_it_approves_their_own(self):
        """One owner/admin per company: there is nobody else to ask."""
        self.month(submit_payroll)
        self.assertIsNone(approval_blocker(self.admin, self.company.pk, self.latest_run()))
        self.assertEqual(self.month(approve_payroll).status, PayrollRun.Status.POSTED)

    def test_someone_who_cannot_prepare_salary_cannot_submit(self):
        hr = self.member("hr")
        with self.assertRaises(PermissionDenied):
            self.month(submit_payroll, actor=hr)
        clerk = self.member("employee", email="clerk@approval.test")
        with self.assertRaises(PermissionDenied):
            self.month(submit_payroll, actor=clerk)


class SendBackTests(ApprovalCase):
    def test_the_admin_sends_it_back_with_a_reason(self):
        manager = self.manager()
        self.month(submit_payroll, actor=manager)
        with self.assertRaises(ValidationError):
            self.month(return_payroll, reason="  ")
        run = self.month(return_payroll, reason="Rahim's rate is wrong")
        self.assertEqual((run.status, run.return_reason), (PayrollRun.Status.DRAFT, "Rahim's rate is wrong"))
        self.assertEqual(run.returned_by, self.admin)
        self.assertEqual(AuditLog.objects.get(action="payroll.returned").after_data["reason"],
                         "Rahim's rate is wrong")

    def test_the_submitter_can_take_it_back(self):
        manager = self.manager()
        self.month(submit_payroll, actor=manager)
        self.assertEqual(self.month(return_payroll, actor=manager, reason="Forgot a bonus").status,
                         PayrollRun.Status.DRAFT)

    def test_another_branch_manager_cannot_send_it_back(self):
        self.month(submit_payroll, actor=self.manager())
        other = self.member("manager", email="other@approval.test", branches=[self.branch])
        with self.assertRaises(PermissionDenied):
            self.month(return_payroll, actor=other, reason="Not mine to send back")

    def test_submitting_again_clears_the_last_reason(self):
        self.month(submit_payroll)
        self.month(return_payroll, reason="Fix it")
        self.month(generate_payroll)
        run = self.month(submit_payroll)
        self.assertEqual((run.return_reason, run.returned_by), ("", None))

    def test_only_a_waiting_month_can_be_sent_back(self):
        with self.assertRaisesMessage(ValidationError, "not waiting for approval"):
            self.month(return_payroll, reason="x")


class NothingChangesWhileWaitingTests(ApprovalCase):
    def test_generate_refuses_and_makes_no_second_run(self):
        self.month(submit_payroll)
        with self.assertRaisesMessage(ValidationError, "waiting for approval"):
            self.month(generate_payroll)
        with use_company(self.company):
            self.assertEqual(PayrollRun.objects.count(), 1)

    def test_bonus_lines_cannot_be_added_or_removed(self):
        with use_company(self.company):
            record = PayrollRecord.objects.get()
        add_adjustment(actor=self.admin, company_id=self.company.pk, record_id=record.pk,
                       adjustment_type="earning", amount=Decimal("500"), reason="Eid bonus")
        with use_company(self.company):
            record = PayrollRecord.objects.order_by("-pk").first()
            adjustment = record.employee.payroll_adjustments.get()
        self.month(submit_payroll)
        with self.assertRaises(ValidationError):
            add_adjustment(actor=self.admin, company_id=self.company.pk, record_id=record.pk,
                           adjustment_type="earning", amount=Decimal("100"), reason="More")
        with self.assertRaises(ValidationError):
            remove_adjustment(actor=self.admin, company_id=self.company.pk,
                              adjustment_id=adjustment.pk)

    def test_submitting_twice_says_it_is_already_waiting(self):
        self.month(submit_payroll)
        with self.assertRaisesMessage(ValidationError, "already waiting"):
            self.month(submit_payroll)

    def test_a_month_never_generated_cannot_be_submitted(self):
        with self.assertRaisesMessage(ValidationError, "Generate"):
            submit_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=9)

    def test_overtime_decided_after_generation_blocks_submit_and_approve(self):
        record = self.work(datetime.date(2026, 8, 12), (9, 0), (20, 0))
        self.month(generate_payroll)
        self.decide(record, approve=False, note="Not approved")
        with self.assertRaises(ValidationError):
            self.month(submit_payroll)
        self.month(generate_payroll)
        self.month(submit_payroll)
        # Decided while it waits: approving must refuse too.
        record2 = self.work(datetime.date(2026, 8, 13), (9, 0), (20, 0))
        self.decide(record2, approve=False, note="No")
        with self.assertRaisesMessage(ValidationError, "Send it back"):
            self.month(approve_payroll)


class PageTests(ApprovalCase):
    def home(self, user=None):
        self.client.force_login(user or self.admin)
        return self.client.get(reverse("payroll:payroll_home"), AUGUST_QUERY)

    def test_the_whole_flow_through_the_pages(self):
        manager = self.manager()
        submit = reverse("payroll:payroll_submit")
        self.assertContains(self.home(manager), f"{submit}?month=8&year=2026")
        self.assertRedirects(self.client.post(submit, AUGUST_QUERY),
                             reverse("payroll:payroll_home") + "?month=8&year=2026")

        page = self.home(manager)
        self.assertContains(page, "Waiting for approval")
        self.assertContains(page, "The owner or company administrator approves it.")
        self.assertContains(page, "Take back")
        self.assertNotContains(page, "Approve August")
        self.assertNotContains(page, "Regenerate August")
        # The approve page itself is closed to a branch manager.
        approve = reverse("payroll:payroll_finalise")
        self.assertRedirects(self.client.get(approve, AUGUST_QUERY), reverse("me:home"))

        page = self.home()
        self.assertContains(page, "Approve August")
        self.assertContains(page, "Send back")
        self.assertContains(self.client.get(approve, AUGUST_QUERY), "Approve and finalise")
        self.client.post(approve, AUGUST_QUERY)
        page = self.home()
        self.assertContains(page, "Finalised")
        self.assertContains(page, "Undo finalise")

    def test_a_send_back_reason_shows_on_the_page(self):
        self.month(submit_payroll, actor=self.manager())
        self.client.force_login(self.admin)
        send_back = reverse("payroll:payroll_return")
        self.assertContains(self.client.post(send_back, {**AUGUST_QUERY, "reason": ""}),
                            "This field is required")
        self.client.post(send_back, {**AUGUST_QUERY, "reason": "Check Rahim's overtime"})
        page = self.home()
        self.assertContains(page, "Sent back by")
        self.assertContains(page, "Check Rahim&#x27;s overtime")
        self.assertContains(page, "Regenerate August")


class AttendanceChangedTests(ApprovalCase):
    """Approving must not finalise figures the attendance no longer matches
    (Ajay, 2026-09-23). A day can still be fixed while a month waits; the
    approval is what refuses."""

    WORKED = datetime.date(2026, 8, 10)

    def fix_the_day(self):
        """A real fix through the real service: scans the device never got.

        Both halves of the pair - one scan alone leaves an unfinished visit,
        which changes nothing the pay is worked out from.
        """
        from attendance.correction_services import add_scan

        for hour, minute in ((19, 30), (20, 30)):
            add_scan(actor=self.admin, company_id=self.company.pk,
                     employee_id=self.employee.pk, work_date=self.WORKED,
                     at=datetime.datetime(2026, 8, 10, hour, minute, tzinfo=DHAKA),
                     reason="Stayed late; the terminal missed it")

    def test_a_day_fixed_while_it_waits_blocks_approval(self):
        self.month(submit_payroll)
        self.fix_the_day()
        with self.assertRaisesMessage(ValidationError, "Attendance changed for 1 person"):
            self.month(approve_payroll)
        self.assertEqual(self.latest_run().status, PayrollRun.Status.SUBMITTED)

    def test_send_back_generate_submit_approve_clears_it(self):
        self.month(submit_payroll)
        self.fix_the_day()
        self.month(return_payroll, reason="Attendance changed")
        self.month(generate_payroll)
        self.month(submit_payroll)
        self.assertEqual(self.month(approve_payroll).status, PayrollRun.Status.POSTED)

    def test_recalculating_without_changing_anything_does_not_block(self):
        """The screens rebuild an open month on every view; that is not a change."""
        from attendance.services import recalculate, refresh

        self.month(submit_payroll)
        recalculate(self.company.pk, start=self.WORKED, end=self.WORKED)
        refresh(self.company.pk, start=self.WORKED, end=self.WORKED)
        self.assertEqual(attendance_changed_since(self.company.pk, self.latest_run()), 0)
        self.assertEqual(self.month(approve_payroll).status, PayrollRun.Status.POSTED)

    def test_a_payslip_generated_before_this_check_existed_does_not_block(self):
        """Drafts already waiting when this shipped carry no fingerprint."""
        with use_company(self.company):
            run = self.latest_run()
            for record in run.records.all():
                record.calculation_snapshot.pop("attendance", None)
                record.save(update_fields=["calculation_snapshot"])
        self.month(submit_payroll)
        self.fix_the_day()
        self.assertEqual(attendance_changed_since(self.company.pk, self.latest_run()), 0)
        self.assertEqual(self.month(approve_payroll).status, PayrollRun.Status.POSTED)

    def test_the_salary_page_says_so_before_anyone_clicks_approve(self):
        self.month(submit_payroll)
        self.fix_the_day()
        self.client.force_login(self.admin)
        page = self.client.get(reverse("payroll:payroll_home"), AUGUST_QUERY)
        body = " ".join(page.content.decode().split())
        self.assertIn("Attendance changed for 1 person since this salary was generated", body)
        self.assertIn("cannot be approved until you do", body)
        # And the approve page refuses rather than offering a button that fails.
        self.client.post(reverse("payroll:payroll_finalise"), AUGUST_QUERY)
        self.assertEqual(self.latest_run().status, PayrollRun.Status.SUBMITTED)
