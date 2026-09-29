"""The profile's twelve actions (Ajay, 2026-09-27), each submitted through
the profile's modal the way a person would. The mapping is in
``organization.employee_actions``.

Head Office: Rahim (on the Front device), Clerk; Manny manages it.
Chittagong: Karim.
"""

import datetime
from decimal import Decimal

from django.urls import reverse

from access_control.branch_access import ALL_BRANCHES, branches_for
from accounts.models import CompanyMembership
from attendance import correction_services
from attendance.models import AttendanceCorrection
from attendance.services import recalculate
from auditlog.models import AuditLog
from common.tenant import use_company
from employees.models import Employee, EmployeeAssignment
from employees.services import create_employee
from leaves.models import LeaveRequest
from leaves.tests_branch_access import TwoBranchCase
from attendance.tests_live import UTC

TODAY = datetime.date.today()
# A finished working day in the recent past (every day is a working day here).
DAY = TODAY - datetime.timedelta(days=3)


class ActionCase(TwoBranchCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        self.profile_url = reverse("organization:employee_detail", args=[self.employee.pk])

    def url(self, name, employee=None):
        return reverse(f"organization:{name}", args=[(employee or self.employee).pk])

    def assertBack(self, response, tab="profile"):
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"{self.profile_url}#{tab}")

    def assertModalOpen(self, response, dialog):
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'data-open-on-load="{dialog}"')

    def fresh(self, employee=None):
        return Employee.all_objects.get(pk=(employee or self.employee).pk)

    def worked(self, day, start=(9, 0), end=(18, 0)):
        self.punch(day, *start)
        self.punch(day, *end)
        recalculate(self.company.pk, employee_ids=[self.employee.pk], start=day, end=day)
        return self.record(day)


class MenuTests(ActionCase):
    def test_the_owner_sees_the_twelve(self):
        page = self.client.get(self.profile_url)
        for label in ("Apply for leave", "Apply for late approval", "Manual entry",
                      "Set as admin", "Set as HR manager", "Set as line manager",
                      "Disallow overtime", "Remove from reports",
                      "Make employee status inactive", "Resign employee", "Delete employee"):
            with self.subTest(action=label):
                self.assertContains(page, label)
        for dialog in ("leave-dialog", "late-dialog", "missing-dialog", "admin-dialog",
                       "hr-dialog", "reports-dialog", "overtime-dialog", "inactive-dialog",
                       "resign-dialog", "delete-dialog"):
            with self.subTest(dialog=dialog):
                self.assertContains(page, f'id="{dialog}"')

    def test_sync_needs_an_id_that_can_go_on_a_device(self):
        self.assertNotContains(self.client.get(self.profile_url), "Sync employee")
        nadia = create_employee(
            company=self.company, first_name="Nadia", employee_code="105",
            branch=self.branch, department=self.hq_department,
            designation=self.hq_designation,
            effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC),
            pay_basis="monthly", base_rate=Decimal("20000"))["employee"]
        page = self.client.get(self.url("employee_detail", nadia))
        self.assertContains(page, "Sync employee")
        self.assertContains(page, reverse("devices:employees_send"))

    def test_view_only_gets_no_changing_actions(self):
        self.grant("employees.view", self.branch)
        self.client.force_login(self.clerk_user)
        page = self.client.get(self.profile_url)
        self.assertEqual(page.status_code, 200)
        for dialog in ("hr-dialog", "reports-dialog", "inactive-dialog", "resign-dialog",
                       "overtime-dialog", "leave-dialog"):
            self.assertNotContains(page, f'id="{dialog}"')

    def test_hr_does_not_get_set_as_hr(self):
        self.client.force_login(self.hr)
        page = self.client.get(self.profile_url)
        self.assertNotContains(page, 'id="hr-dialog"')
        self.assertContains(page, 'id="leave-dialog"')


class LeaveTests(ActionCase):
    def test_apply_for_leave(self):
        response = self.client.post(self.url("employee_leave"), {
            "employee": self.employee.pk, "leave_type": self.leave_type.pk,
            "start_date": DAY.isoformat(), "end_date": DAY.isoformat(),
            "duration": "full_day", "pay_type": "paid", "reason": "Family"})
        self.assertBack(response, "leave")
        self.assertEqual(LeaveRequest.all_objects.filter(employee=self.employee).count(), 1)

    def test_a_refused_one_reopens_the_modal(self):
        response = self.client.post(self.url("employee_leave"), {
            "employee": self.employee.pk, "leave_type": "", "start_date": ""})
        self.assertModalOpen(response, "leave-dialog")

    def test_not_someone_elses_employee(self):
        response = self.client.post(self.url("employee_leave", self.far), {
            "employee": self.employee.pk, "leave_type": self.leave_type.pk,
            "start_date": DAY.isoformat(), "end_date": DAY.isoformat(),
            "duration": "full_day", "pay_type": "paid", "reason": ""})
        # The form only offers the person whose profile it is.
        self.assertModalOpen(response, "leave-dialog")
        self.assertFalse(LeaveRequest.all_objects.exists())


class LateApprovalTests(ActionCase):
    def test_a_late_day_is_approved_and_can_be_withdrawn(self):
        record = self.worked(DAY, start=(9, 40))
        self.assertGreater(record.late_minutes, 0)
        page = self.client.get(self.profile_url)
        self.assertContains(page, f'value="{DAY.isoformat()}"')
        response = self.client.post(self.url("employee_late"), {
            "work_date": DAY.isoformat(), "reason": "Road blocked"})
        self.assertBack(response, "attendance")
        record = self.record(DAY)
        self.assertEqual(record.late_minutes, 0)
        self.assertIn("Late approved: Road blocked", record.note)
        self.assertTrue(AuditLog.objects.filter(action="attendance.late_approved").exists())
        # It stays through the next recalculation, and goes when withdrawn.
        recalculate(self.company.pk, employee_ids=[self.employee.pk], start=DAY, end=DAY)
        self.assertEqual(self.record(DAY).late_minutes, 0)
        with use_company(self.company):
            correction = AttendanceCorrection.objects.get(
                employee=self.employee, correction_type="excuse_late")
        correction_services.withdraw(actor=self.admin, company_id=self.company.pk,
                                     correction_id=correction.pk, note="Mistake")
        self.assertGreater(self.record(DAY).late_minutes, 0)

    def test_a_day_not_late_is_not_offered_and_refused(self):
        self.worked(DAY)
        response = self.client.post(self.url("employee_late"), {
            "work_date": DAY.isoformat(), "reason": "x"})
        self.assertModalOpen(response, "late-dialog")
        self.assertContains(response, "no late day to approve")

    def test_twice_is_refused(self):
        self.worked(DAY, start=(9, 40))
        correction_services.excuse_late(actor=self.admin, company_id=self.company.pk,
                                        employee_id=self.employee.pk, work_date=DAY,
                                        reason="Road")
        with self.assertRaises(Exception):
            correction_services.excuse_late(actor=self.admin, company_id=self.company.pk,
                                            employee_id=self.employee.pk, work_date=DAY,
                                            reason="Again")

    def test_a_branch_manager_elsewhere_cannot(self):
        far_manager = self.member("far@liv.test", "manager", branches=[self.unit])
        self.worked(DAY, start=(9, 40))
        self.client.force_login(far_manager)
        response = self.client.post(self.url("employee_late"), {
            "work_date": DAY.isoformat(), "reason": "x"})
        self.assertEqual(response.status_code, 403)


class OvertimeTests(ActionCase):
    def test_disallowed_from_a_day_then_allowed_again(self):
        record = self.worked(DAY, end=(20, 0))
        self.assertGreater(record.calculated_overtime_minutes, 0)
        paid = record.approved_overtime_minutes
        self.assertGreater(paid, 0)
        response = self.client.post(self.url("employee_overtime"),
                                    {"from_day": DAY.isoformat()})
        self.assertBack(response)
        self.assertEqual(self.fresh().no_overtime_from, DAY)
        record = self.record(DAY)
        self.assertEqual(record.approved_overtime_minutes, 0)
        self.assertEqual(record.calculated_overtime_minutes, paid)   # still measured
        self.assertContains(self.client.get(self.profile_url), "Allow overtime")
        self.assertBack(self.client.post(self.url("employee_overtime"), {"allow": "1"}))
        self.assertIsNone(self.fresh().no_overtime_from)
        self.assertEqual(self.record(DAY).approved_overtime_minutes, paid)

    def test_days_before_keep_their_overtime(self):
        record = self.worked(DAY, end=(20, 0))
        self.client.post(self.url("employee_overtime"),
                         {"from_day": (DAY + datetime.timedelta(days=1)).isoformat()})
        self.assertEqual(self.record(DAY).approved_overtime_minutes,
                         record.approved_overtime_minutes)

    def test_the_overtime_page_does_not_ask_and_refuses_approval(self):
        from payroll import overtime

        self.punch(DAY, 9)          # nobody scanned out: it would wait for a decision
        recalculate(self.company.pk, employee_ids=[self.employee.pk], start=DAY, end=DAY)
        first = DAY.replace(day=1)
        waiting = overtime.undecided_count(self.company.pk, first, DAY)
        self.client.post(self.url("employee_overtime"), {"from_day": DAY.isoformat()})
        self.assertEqual(overtime.undecided_count(self.company.pk, first, DAY),
                         max(0, waiting - 1))

    def test_a_refused_day_reopens_the_modal(self):
        self.assertModalOpen(self.client.post(self.url("employee_overtime"), {"from_day": ""}),
                             "overtime-dialog")


class StatusTests(ActionCase):
    """Inactive for a period (Nihal, 2026-09-29): from a first day to a last
    day, or until made active; scans blocked, days Inactive, no salary."""

    ONE = datetime.timedelta(days=1)

    def make_inactive(self, start, end=None, reason="Under investigation", employee=None):
        return self.client.post(self.url("employee_active", employee), {
            "start_date": start.isoformat(), "end_date": end.isoformat() if end else "",
            "reason": reason})

    def make_active(self):
        return self.client.post(self.url("employee_active"), {"active": "1"})

    def periods(self):
        from employees.models import EmployeeInactivePeriod

        return list(EmployeeInactivePeriod.all_objects.filter(employee=self.employee)
                    .order_by("start_date"))

    def punch_statuses(self, day):
        from devices.models import PunchEvent

        return {p.authorization_status for p in PunchEvent.all_objects.filter(
            employee=self.employee, punched_at_device__date=day)}

    def apply_due(self, today):
        from organization.employee_inactive import apply_due

        return apply_due(self.company.pk, today=today)

    def test_a_refused_form_reopens_it(self):
        self.assertModalOpen(self.client.post(self.url("employee_active"), {"reason": ""}),
                             "inactive-dialog")
        self.assertModalOpen(self.make_inactive(TODAY, TODAY - self.ONE), "inactive-dialog")
        self.assertEqual(self.periods(), [])

    def test_without_a_last_day_it_lasts_until_made_active(self):
        self.assertEqual(self.worked(DAY).attendance_status, "present")
        self.assertBack(self.make_inactive(DAY))
        self.assertEqual(self.fresh().employment_status, "suspended")
        day = self.record(DAY)
        self.assertEqual((day.attendance_status, day.worked_minutes, day.payable_fraction),
                         ("inactive", 0, 0))
        self.assertEqual(self.punch_statuses(DAY), {"employee_inactive"})
        page = self.client.get(self.profile_url)
        self.assertContains(page, "Make employee status active")
        self.assertContains(page, f"Inactive from {DAY.day} {DAY:%b %Y}")
        # Days pass: with no last day nothing changes by itself.
        self.apply_due(TODAY + datetime.timedelta(days=30))
        self.assertEqual(self.fresh().employment_status, "suspended")
        self.assertBack(self.make_active())
        self.assertEqual(self.fresh().employment_status, "active")
        # Active from today: the days before it stay inactive.
        self.assertEqual(self.periods()[0].end_date, TODAY - self.ONE)
        self.assertEqual(self.record(DAY).attendance_status, "inactive")
        self.assertEqual(AuditLog.objects.filter(action__in=[
            "employee.made_inactive", "employee.made_active"]).count(), 2)

    def test_with_a_last_day_they_are_active_again_by_themselves(self):
        self.make_inactive(TODAY, TODAY + 2 * self.ONE)
        self.assertEqual(self.fresh().employment_status, "suspended")
        self.apply_due(TODAY + 2 * self.ONE)
        self.assertEqual(self.fresh().employment_status, "suspended")
        self.apply_due(TODAY + 3 * self.ONE)
        self.assertEqual(self.fresh().employment_status, "active")

    def test_probation_is_given_back(self):
        Employee.all_objects.filter(pk=self.employee.pk).update(employment_status="probation")
        self.make_inactive(TODAY, TODAY)
        self.apply_due(TODAY + self.ONE)
        self.assertEqual(self.fresh().employment_status, "probation")

    def test_a_past_period_changes_the_days_not_the_status(self):
        self.worked(DAY)
        self.assertBack(self.make_inactive(DAY - self.ONE, DAY))
        self.assertEqual(self.fresh().employment_status, "active")
        self.assertEqual(self.record(DAY).attendance_status, "inactive")

    def test_a_planned_period_starts_by_itself_and_can_be_cancelled(self):
        start = TODAY + 2 * self.ONE
        self.make_inactive(start, start + 2 * self.ONE)
        self.assertEqual(self.fresh().employment_status, "active")
        page = self.client.get(self.profile_url)
        self.assertContains(page, "Inactive planned")
        self.assertContains(page, "Cancel planned inactive")
        self.apply_due(start)
        self.assertEqual(self.fresh().employment_status, "suspended")
        Employee.all_objects.filter(pk=self.employee.pk).update(employment_status="active")
        self.make_active()
        self.assertEqual(self.periods()[0].status, "cancelled")

    def test_made_active_the_same_day_its_scans_count_again(self):
        self.punch(TODAY, 12)
        self.make_inactive(TODAY)
        self.assertEqual(self.punch_statuses(TODAY), {"employee_inactive"})
        self.make_active()
        self.assertEqual(self.periods()[0].status, "cancelled")
        self.assertEqual(self.punch_statuses(TODAY), {"authorized"})

    def test_a_scan_arriving_on_an_inactive_day_is_blocked(self):
        from devices.models import PunchEvent
        from devices.services.processing import resolve_and_authorize

        self.make_inactive(DAY)
        punch = self.punch(DAY, 9)
        with use_company(self.company):
            resolve_and_authorize(PunchEvent.objects.get(pk=punch.pk))
        blocked = PunchEvent.all_objects.get(pk=punch.pk)
        self.assertEqual(blocked.authorization_status, "employee_inactive")
        self.assertEqual(blocked.processing_status, "excluded")
        self.assertIn("inactive", blocked.authorization_snapshot["decision_reason"])

    def test_not_over_another_period_or_leave(self):
        self.make_inactive(DAY, DAY + self.ONE)
        response = self.make_inactive(DAY + self.ONE, DAY + 2 * self.ONE)
        self.assertModalOpen(response, "inactive-dialog")
        self.assertContains(response, "already inactive from")
        from leaves import services as leave_services

        with use_company(self.company):
            leave_services.record_leave(actor=self.admin, company_id=self.company.pk, values={
                "employee": self.clerk, "leave_type": self.leave_type,
                "start_date": TODAY + 5 * self.ONE, "end_date": TODAY + 5 * self.ONE,
                "pay_type": "paid", "reason": ""})
        response = self.make_inactive(TODAY + 4 * self.ONE, employee=self.clerk)
        self.assertContains(response, "They have leave on")

    def test_no_leave_or_manual_entry_on_inactive_days(self):
        from django.core.exceptions import ValidationError

        from attendance import scan_requests
        from leaves import services as leave_services

        self.make_inactive(DAY, DAY)
        with self.assertRaisesMessage(ValidationError, "inactive on"):
            leave_services.record_leave(actor=self.admin, company_id=self.company.pk, values={
                "employee": self.employee, "leave_type": self.leave_type,
                "start_date": DAY, "end_date": DAY, "pay_type": "paid", "reason": ""})
        with self.assertRaisesMessage(ValidationError, "inactive on that day"):
            scan_requests.enter_for(
                actor=self.admin, company_id=self.company.pk, employee_id=self.employee.pk,
                work_date=DAY, kind="check_out",
                at=datetime.datetime.combine(DAY, datetime.time(18), tzinfo=UTC),
                at_out=None, reason="x")

    def test_an_old_suspension_is_made_active_as_before(self):
        Employee.all_objects.filter(pk=self.employee.pk).update(employment_status="suspended")
        self.assertBack(self.make_active())
        self.assertEqual(self.fresh().employment_status, "active")

    def test_not_oneself(self):
        with use_company(self.company):
            self.clerk.user = self.manager
            self.clerk.save(update_fields=["user"])
        self.client.force_login(self.manager)
        response = self.make_inactive(TODAY, reason="x", employee=self.clerk)
        self.assertEqual(response.status_code, 403)


class HrAndLineManagerTests(ActionCase):
    PASSWORD = "Str0ng-pass-2026"

    def give_login(self):
        save = reverse("organization:employee_profile_edit", args=[self.employee.pk])
        self.client.post(save, {"section": "login_give", "login_email": "rahim@liv.test",
                                "login_password": self.PASSWORD,
                                "login_password_confirm": self.PASSWORD,
                                "login_role": "employee"})
        return save

    def test_set_as_hr_and_back(self):
        save = self.give_login()
        rahim = self.fresh().user
        response = self.client.post(save, {"section": "login_role", "login_role": "hr"})
        self.assertBack(response)
        membership = CompanyMembership.all_objects.get(user=rahim, company=self.company)
        self.assertEqual(membership.role, "hr")
        self.assertIs(branches_for(rahim, self.company.pk, "employees.edit"), ALL_BRANCHES)
        self.assertContains(self.client.get(self.profile_url), "Stop being HR")
        self.client.post(save, {"section": "login_role", "login_role": "employee"})
        membership.refresh_from_db()
        self.assertEqual(membership.role, "employee")

    def test_only_the_owner_or_admin_makes_someone_hr(self):
        self.give_login()
        self.client.force_login(self.manager)
        save = reverse("organization:employee_profile_edit", args=[self.employee.pk])
        response = self.client.post(save, {"section": "login_role", "login_role": "hr"})
        self.assertEqual(response.status_code, 403)

    def test_set_as_line_manager(self):
        response = self.client.post(self.url("employee_reports"), {"people": [self.clerk.pk]})
        self.assertBack(response)
        with use_company(self.company):
            placed = EmployeeAssignment.objects.get(employee=self.clerk, effective_to__isnull=True)
        self.assertEqual(placed.manager_id, self.employee.pk)
        page = self.client.get(self.profile_url)
        self.assertEqual([p.pk for p in page.context["reports"]], [self.clerk.pk])

    def test_not_someone_out_of_reach(self):
        self.client.force_login(self.manager)
        response = self.client.post(self.url("employee_reports"), {"people": [self.far.pk]})
        self.assertModalOpen(response, "reports-dialog")


class EndingTests(ActionCase):
    def test_resign_in_the_modal(self):
        end = self.url("employee_end")
        refused = self.client.post(end, {"dialog": "resign", "last_day": DAY.isoformat(),
                                         "status": "resigned", "reason": ""})
        self.assertModalOpen(refused, "resign-dialog")
        self.client.post(end, {"dialog": "resign", "last_day": DAY.isoformat(),
                               "status": "resigned", "reason": "Moved away",
                               "end_device_enrollments": "on"})
        self.assertEqual(self.fresh().employment_status, "resigned")
        self.assertNotContains(self.client.get(self.profile_url), 'id="resign-dialog"')

    def test_delete_ends_employment_and_keeps_the_record(self):
        self.client.post(self.url("employee_end"), {
            "dialog": "delete", "last_day": DAY.isoformat(), "status": "terminated",
            "reason": "Duplicate record", "end_device_enrollments": "on"})
        self.assertEqual(self.fresh().employment_status, "terminated")
        self.assertTrue(Employee.all_objects.filter(pk=self.employee.pk).exists())


class ActionsShownDirectlyTests(ActionCase):
    """Shown directly, grouped in the profile's side rail (2026-09-28)."""

    def test_the_actions_are_grouped_buttons_not_a_menu(self):
        page = self.client.get(self.profile_url)
        self.assertContains(page, 'class="profile-hero"')
        self.assertContains(page, 'class="card rail-card profile-action-bar"')
        self.assertNotContains(page, "data-action-menu")
        self.assertNotContains(page, "{#")          # no template comment shown as text
        for group in ("Leave &amp; attendance", "Roles &amp; access", "Employment"):
            self.assertContains(page, f'<div class="rail-group__label">{group}</div>')
        self.assertContains(page, 'data-open-dialog="leave-dialog">')
        self.assertContains(page, "<span>Apply for leave</span>")
        self.assertContains(page, 'class="rail-action rail-action--danger" type="button" '
                                  'data-open-dialog="delete-dialog"')

    def test_this_months_numbers_in_the_hero(self):
        page = self.client.get(self.profile_url)
        for label in ("Present", "Absent", "Leave", "Waiting", "With us"):
            self.assertContains(page, f"<dt>{label}</dt>")

    def test_a_view_only_viewer_gets_no_action_groups(self):
        self.grant("employees.view", self.branch)
        self.client.force_login(self.clerk_user)
        page = self.client.get(self.profile_url)
        self.assertNotContains(page, "rail-group__label")
