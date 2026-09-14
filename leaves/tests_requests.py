"""A8 requests: ownership, routing, atomic approval and existing salary inputs."""

import datetime
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from accounts.models import CompanyMembership, User
from attendance.tests_live import LiveTestCase
from common.tenant import use_company
from leaves.models import LeaveRequest, LeaveDay
from leaves.services import create_leave_type
from leaves import workflow
from organization.models import Branch
from payroll.services import calculate_pay
from attendance.models import AttendanceRecord
from tenants.services import onboard_company

DAY = datetime.date(2026, 8, 10)


class RequestTests(LiveTestCase):
    def setUp(self):
        super().setUp()
        self.worker = User.objects.create_user(email='worker@requests.test')
        self.manager = User.objects.create_user(email='manager@requests.test')
        self.worker_member = CompanyMembership.all_objects.create(
            company=self.company, user=self.worker, role='employee', status='active')
        self.manager_member = CompanyMembership.all_objects.create(
            company=self.company, user=self.manager, role='manager', status='active')
        with use_company(self.company):
            self.employee.user = self.worker
            self.employee.save(update_fields=['user'])
            self.manager_member.allowed_branches.add(self.branch)
        self.leave_type = create_leave_type(actor=self.admin, company_id=self.company.pk,
                                            values={'code': 'CAS', 'name': 'Casual'})

    def submit(self, **changes):
        return workflow.submit_request(actor=self.worker, company_id=self.company.pk, values={
            'leave_type': self.leave_type, 'start_date': DAY, 'end_date': DAY,
            'pay_type': 'paid', 'reason': 'Family appointment', **changes,
        })

    def decide(self, request, actor=None, **changes):
        return workflow.decide_request(actor=actor or self.manager, company_id=self.company.pk,
                                       request_id=request.pk, approve=True, **changes)

    def test_pending_then_unpaid_approval_changes_salary_inputs(self):
        request = self.submit()
        with use_company(self.company):
            self.assertEqual(request.status, 'pending')
            self.assertFalse(LeaveDay.objects.exists())
        self.decide(request, pay_type='unpaid', reason='Approved as unpaid')
        with use_company(self.company):
            day = LeaveDay.objects.get()
            self.assertEqual(day.approved_pay_type, 'unpaid')
            record = AttendanceRecord.objects.select_related('shift', 'leave_day').get(employee=self.employee, work_date=DAY)
            self.assertEqual(calculate_pay('monthly', '30000', [record])['net'], 29000)
        request.refresh_from_db()
        self.assertEqual(request.status, 'approved')
        with self.assertRaises(ValidationError):
            self.decide(request)

    def test_paid_approval_and_rejection(self):
        request = self.submit()
        self.decide(request)
        with use_company(self.company):
            self.assertEqual(LeaveDay.objects.get().approved_pay_percentage, 100)
        request = self.submit(start_date=DAY + datetime.timedelta(days=1), end_date=DAY + datetime.timedelta(days=1))
        with self.assertRaises(ValidationError):
            workflow.decide_request(actor=self.manager, company_id=self.company.pk,
                                    request_id=request.pk, approve=False)
        workflow.decide_request(actor=self.manager, company_id=self.company.pk,
                                request_id=request.pk, approve=False, reason='Please choose another day')
        with use_company(self.company):
            self.assertEqual(LeaveDay.objects.count(), 1)
        self.client.force_login(self.worker)
        page = self.client.get(reverse('me:leave'), {'year': 2026})
        self.assertContains(page, 'Please choose another day')

    def test_duplicate_pending_and_approved_dates_are_refused(self):
        request = self.submit()
        with self.assertRaises(ValidationError):
            self.submit()
        self.decide(request)
        with self.assertRaises(ValidationError):
            self.submit()

    def test_employee_withdraws_only_their_own_pending_request(self):
        request = self.submit()
        with self.assertRaises(PermissionDenied):
            workflow.withdraw_request(actor=self.manager, company_id=self.company.pk, request_id=request.pk)
        self.client.force_login(self.worker)
        url = reverse('me:leave_withdraw', args=[request.pk])
        self.assertContains(self.client.get(reverse('me:leave'), {'year': 2026}), url)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertRedirects(self.client.post(url), reverse('me:leave'))
        request.refresh_from_db()
        self.assertEqual(request.status, 'withdrawn')
        with self.assertRaises(ValidationError):
            self.decide(request)
        # The dates are free again; an approved request cannot be withdrawn.
        again = self.submit()
        self.decide(again)
        with self.assertRaises(ValidationError):
            workflow.withdraw_request(actor=self.worker, company_id=self.company.pk, request_id=again.pk)
        self.assertEqual(self.client.get(reverse('me:leave_withdraw', args=[again.pk])).status_code, 404)

    def test_manager_scope_and_admin_fallback(self):
        request = self.submit()
        with self.assertRaises(PermissionDenied):
            self.decide(request, actor=self.admin)  # Assigned to its manager.
        with use_company(self.company):
            self.manager_member.allowed_branches.clear()
        with self.assertRaises(PermissionDenied):
            self.decide(request)
        self.decide(request, actor=self.admin)

    def test_manager_request_goes_to_admin_and_cannot_be_self_approved(self):
        self.worker_member.role = 'manager'
        self.worker_member.save(update_fields=['role'])
        with use_company(self.company):
            self.worker_member.allowed_branches.add(self.branch)
        request = self.submit()
        for actor in (self.worker, self.manager):
            with self.assertRaises(PermissionDenied):
                self.decide(request, actor=actor)
        self.decide(request, actor=self.admin)

    def test_posted_month_and_calendar_changes_rechecked_at_approval(self):
        with patch('leaves.workflow.locked_ranges', return_value=[(DAY, DAY)]):
            with self.assertRaises(ValidationError):
                self.submit()
        request = self.submit()
        with patch('leaves.workflow.locked_ranges', return_value=[(DAY, DAY)]):
            with self.assertRaises(ValidationError):
                self.decide(request)
        with use_company(self.company):
            self.assertFalse(LeaveDay.objects.exists())
        request.refresh_from_db()
        self.assertEqual(request.status, 'pending')

    def test_audit_failure_rolls_back_approval_and_days(self):
        request = self.submit()
        with patch('leaves.workflow.record_company_event', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                self.decide(request)
        request.refresh_from_db()
        self.assertEqual(request.status, 'pending')
        with use_company(self.company):
            self.assertFalse(LeaveDay.objects.exists())

    def test_http_request_ignores_forged_employee_and_gates_inbox(self):
        self.client.force_login(self.worker)
        page = self.client.get(reverse('me:leave_request'))
        self.assertContains(page, 'data-daterange=')
        self.assertNotContains(page, 'name="employee"')
        response = self.client.post(reverse('me:leave_request'), {
            'employee': 99999, 'leave_type': self.leave_type.pk,
            'start_date': str(DAY), 'end_date': str(DAY), 'pay_type': 'paid', 'reason': 'Appointment',
        })
        self.assertRedirects(response, reverse('me:leave'))
        with use_company(self.company):
            request = LeaveRequest.objects.get()
            self.assertEqual(request.employee_id, self.employee.pk)
        self.assertEqual(self.client.get(reverse('me:leave_inbox')).status_code, 403)
        self.assertEqual(self.client.get(reverse('me:branch_attendance')).status_code, 403)
        self.client.force_login(self.manager)
        self.assertContains(self.client.get(reverse('me:leave_inbox')), self.employee.full_name)
        response = self.client.post(reverse('me:leave_decide', args=[request.pk]),
                                    {'decision': 'approve', 'pay_type': 'paid', 'reason': 'OK'})
        self.assertRedirects(response, reverse('me:leave_inbox'))

    def test_branch_attendance_and_inbox_fail_closed_for_other_branch(self):
        request = self.submit()
        with use_company(self.company):
            other = Branch.objects.create(code='OTH', name='Other branch')
            self.manager_member.allowed_branches.set([other])
        self.client.force_login(self.manager)
        self.assertNotContains(self.client.get(reverse('me:leave_inbox')), self.employee.full_name)
        self.assertEqual(self.client.get(reverse('me:leave_decide', args=[request.pk])).status_code, 404)
        response = self.client.get(reverse('me:branch_attendance'), {'date': str(DAY)})
        self.assertNotContains(response, self.employee.full_name)
        with use_company(self.company):
            self.manager_member.allowed_branches.set([self.branch])
        self.assertContains(self.client.get(reverse('me:branch_attendance'), {'date': str(DAY)}), self.employee.full_name)

    def test_foreign_company_and_inactive_manager_cannot_decide(self):
        request = self.submit()
        other = onboard_company(code='REQOTHER', slug='reqother', name='Other company')
        outsider = User.objects.create_user(email='outsider@requests.test')
        CompanyMembership.all_objects.create(company=other, user=outsider, role='company_admin', status='active')
        with self.assertRaises(PermissionDenied):
            workflow.decide_request(actor=outsider, company_id=other.pk, request_id=request.pk, approve=True)
        self.manager.is_active = False
        self.manager.save(update_fields=['is_active'])
        with self.assertRaises(PermissionDenied):
            self.decide(request)
        self.decide(request, actor=self.admin)

    def test_calendar_skips_weekly_off_and_rejects_empty_range(self):
        from scheduling.services import add_weekly_offs
        add_weekly_offs(actor=self.admin, company_id=self.company.pk,
                       values={'weekdays': [4], 'effective_from': DAY})
        friday = datetime.date(2026, 8, 14)
        with self.assertRaises(ValidationError):
            self.submit(start_date=friday, end_date=friday)
        request = self.submit(start_date=friday - datetime.timedelta(days=1),
                              end_date=friday + datetime.timedelta(days=1))
        self.decide(request)
        with use_company(self.company):
            self.assertEqual(LeaveDay.objects.count(), 2)
            self.assertFalse(LeaveDay.objects.filter(work_date=friday).exists())
