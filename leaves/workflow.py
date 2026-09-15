"""Employee requests and branch-scoped decisions. No days count until approval."""

from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from access_control.branch_access import ALL_BRANCHES, branches_for
from accounts.models import CompanyMembership
from attendance.services import locked_ranges, recalculate
from auditlog.services import record_company_event
from common.services import create_validated
from common.tenant import use_company
from employees.models import Employee
from leaves.models import LeaveDay, LeaveRequest, LeaveRequestSegment, LeaveType, PayType
from leaves.services import check_allowance, is_half_day, plan_leave_days, write_approved_days, _writable
from organization.services import require_company_membership, STRUCTURE_ROLES


def approve_branches(member):
    """Branches where a non-company member decides leave (A12 part 5).

    A branch manager's own branches, plus any branch where they — or anyone
    else — were given "Approve leave requests".
    """
    return branches_for(member.user, member.company_id, 'leave.approve')


def reviewer(actor, company_id):
    member = require_company_membership(actor, company_id)
    if actor.is_superuser:
        raise PermissionDenied('Use your company login to decide leave.')
    if member.role in (*STRUCTURE_ROLES, 'manager') or approve_branches(member):
        return member
    raise PermissionDenied('Leave decisions require a branch manager, access to approve '
                           'leave, or a company administrator.')


def branch_ids(member):
    # A manager without explicitly assigned branches manages nothing.
    with use_company(member.company_id):
        return list(member.allowed_branches.values_list('pk', flat=True))


def reviewable(member):
    """Scope the inbox before pagination and re-use it at decision time.

    Company (owner/admin): requests from branch managers, and from branches
    with no branch manager. Everyone else: requests from people (not branch
    managers) in the branches where they approve leave, never their own.
    """
    requests = LeaveRequest.objects.exclude(employee__user_id=member.user_id)
    departments = list(member.allowed_departments.values_list('pk', flat=True))
    if departments:
        requests = requests.filter(submission_assignment__department_id__in=departments)
    managers = CompanyMembership.all_objects.filter(
        company_id=member.company_id, status='active', ended_at__isnull=True,
        user__is_active=True, role='manager',
    )
    requests = requests.annotate(
        requester_is_manager=Exists(managers.filter(user_id=OuterRef('employee__user_id'))),
        has_branch_manager=Exists(managers.filter(
            allowed_branches=OuterRef('submission_assignment__branch_id'),
        ).filter(Q(allowed_departments__isnull=True)
                 | Q(allowed_departments=OuterRef('submission_assignment__department_id')))),
    )
    if member.role not in STRUCTURE_ROLES:
        requests = requests.filter(requester_is_manager=False)
        branches = approve_branches(member)
        if branches is ALL_BRANCHES:
            return requests
        return requests.filter(submission_assignment__branch_id__in=branches)
    requests = requests.filter(Q(requester_is_manager=True) | Q(has_branch_manager=False))
    allowed = branch_ids(member)
    if allowed:
        requests = requests.filter(submission_assignment__branch_id__in=allowed)
    return requests


def _plan(company_id, employee, start, end, *, exclude_request=None):
    days, skipped = plan_leave_days(company_id=company_id, employee=employee,
                                   start_date=start, end_date=end)
    if not days:
        raise ValidationError('The range contains no working days.')
    if len({assignment.branch_id for _, assignment, *_ in days}) != 1:
        raise ValidationError('Submit separate requests for dates in different branches.')
    if any(start <= last and end >= first for first, last in locked_ranges(company_id)):
        raise ValidationError('These dates include a finalised salary month.')
    dates = [day[0] for day in days]
    if LeaveDay.objects.filter(employee=employee, work_date__in=dates,
                               status__in=['reserved', 'approved', 'consumed']).exists():
        raise ValidationError('You already have approved leave in this range.')
    pending = LeaveRequestSegment.objects.filter(
        leave_request__employee=employee, leave_request__status='pending',
        status='active', start_date__lte=end, end_date__gte=start,
    )
    if exclude_request:
        pending = pending.exclude(leave_request_id=exclude_request)
    if pending.exists():
        raise ValidationError('A pending request already overlaps these dates.')
    return days, skipped


@transaction.atomic
def submit_request(*, actor, company_id, values):
    member = require_company_membership(actor, company_id)
    if actor.is_superuser:
        raise PermissionDenied('Use your employee login to request leave.')
    values = _writable(values, ('leave_type', 'start_date', 'end_date', 'duration', 'pay_type', 'reason'))
    half = is_half_day(values)
    with use_company(company_id):
        employee = Employee.objects.select_for_update().filter(user=actor).first()
        if employee is None or employee.employment_status not in ('active', 'probation'):
            raise PermissionDenied('An active employee record linked to your login is required.')
        leave_type = LeaveType.objects.filter(pk=values['leave_type'].pk, status='active').first()
        if leave_type is None:
            raise ValidationError({'leave_type': 'Choose an active leave type in this company.'})
        if values['pay_type'] not in PayType.values:
            raise ValidationError({'pay_type': 'Choose paid or unpaid.'})
        reason = str(values.get('reason', '')).strip()
        if not reason:
            raise ValidationError({'reason': 'Give a reason for your request.'})
        days, skipped = _plan(company_id, employee, values['start_date'], values['end_date'])
        check_allowance(employee, leave_type, days, half)
        request = create_validated(
            LeaveRequest, company=member.company, employee=employee,
            submission_assignment=days[0][1], reason=reason, status='pending',
            submitted_at=timezone.now(), submitted_by=actor, created_by=actor, updated_by=actor,
        )
        create_validated(
            LeaveRequestSegment, company=member.company, leave_request=request,
            leave_type=leave_type, start_date=values['start_date'], end_date=values['end_date'],
            duration_type=(LeaveRequestSegment.DurationType.HALF_DAY if half
                           else LeaveRequestSegment.DurationType.FULL_DAY),
            timezone=member.company.timezone or 'UTC',
            requested_units=Decimal('0.5') if half else Decimal(len(days)),
            requested_minutes=(days[0][4].scheduled_minutes // 2 if half
                               else sum(shift.scheduled_minutes for *_, shift in days)),
            requested_pay_type=values['pay_type'],
            requested_pay_percentage=100 if values['pay_type'] == 'paid' else 0,
        )
        record_company_event(actor=actor, membership=member, company=member.company,
                             action='leave.submitted', obj=request,
                             after={'status': 'pending', 'working_days': len(days)})
        return request


@transaction.atomic
def withdraw_request(*, actor, company_id, request_id):
    """The employee takes back their own pending request. Nothing had counted yet."""
    member = require_company_membership(actor, company_id)
    with use_company(company_id):
        request = LeaveRequest.objects.select_for_update(of=('self',)).filter(
            pk=request_id, employee__user=actor).first()
        if request is None:
            raise PermissionDenied('Leave request not found.')
        if request.status != 'pending':
            raise ValidationError('Only a pending request can be withdrawn.')
        request.segments.update(status=LeaveRequestSegment.Status.CANCELLED)
        request.status = LeaveRequest.Status.WITHDRAWN
        request.decision_snapshot = {**request.decision_snapshot, 'withdrawn_by': actor.pk}
        request.updated_by = actor
        request.full_clean()
        request.save()
        record_company_event(actor=actor, membership=member, company=member.company,
                             action='leave.withdrawn', obj=request,
                             before={'status': 'pending'}, after={'status': request.status})
        return request


@transaction.atomic
def decide_request(*, actor, company_id, request_id, approve, pay_type='paid', reason=''):
    member = reviewer(actor, company_id)
    with use_company(company_id):
        request = reviewable(member).select_for_update(of=('self',)).select_related(
            'employee', 'submission_assignment').filter(pk=request_id).first()
        if request is None:
            raise PermissionDenied('This request is outside your scope or is your own leave.')
        if request.submitted_by_id == actor.pk:
            raise PermissionDenied('You cannot decide your own leave request.')
        if request.status != 'pending':
            raise ValidationError('This request has already been decided.')
        employee = Employee.objects.select_for_update().get(pk=request.employee_id)
        segment = request.segments.get(status='active')
        reason = str(reason).strip()
        if not approve and not reason:
            raise ValidationError({'reason': 'Give a reason for rejecting the request.'})
        if approve:
            if pay_type not in PayType.values:
                raise ValidationError({'pay_type': 'Choose paid or unpaid.'})
            days, skipped = _plan(company_id, employee, segment.start_date, segment.end_date,
                                  exclude_request=request.pk)
            if any(assignment.branch_id != request.submission_assignment.branch_id
                   for _, assignment, *_ in days):
                raise ValidationError('The employee changed branch. Ask them to submit a new request.')
            # Rechecked here: other leave may have been approved since the request.
            check_allowance(employee, segment.leave_type, days,
                            segment.duration_type == LeaveRequestSegment.DurationType.HALF_DAY)
            write_approved_days(company=member.company, employee=employee, segment=segment,
                                days=days, pay_type=pay_type)
        request.status = 'approved' if approve else 'rejected'
        request.decided_at = timezone.now()
        request.updated_by = actor
        request.decision_snapshot = {'decided_by': actor.pk, 'reason': reason,
                                     'pay_type': pay_type if approve else None}
        request.full_clean()
        request.save()
        record_company_event(actor=actor, membership=member, company=member.company,
                             action='leave.' + request.status, obj=request,
                             before={'status': 'pending'}, after=request.decision_snapshot | {'status': request.status})
        if approve:
            recalculate(company_id, start=segment.start_date, end=segment.end_date,
                        employee_ids=[employee.pk])
        return request
