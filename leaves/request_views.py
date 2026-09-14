"""A8 pages stay under /me/; each service checks its own role and branch scope."""

import datetime
from zoneinfo import ZoneInfo

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from attendance.live_status import statuses_for
from attendance.models import AttendanceRecord
from attendance.services import recalculate
from common.forms import StyledFormMixin, apply_service_errors
from common.tenant import use_company
from employees.models import EmployeeAssignment
from leaves.forms import RequestLeaveForm, DecideLeaveForm
from leaves.models import LeaveType
from leaves import workflow


@login_required
@require_http_methods(['GET', 'POST'])
def request_leave(request):
    member = workflow.require_company_membership(request.user, request.company_id)
    with use_company(request.company_id):
        form = RequestLeaveForm(request.POST or None,
                                leave_types=LeaveType.objects.filter(status='active'),
                                initial={'pay_type': 'paid'})
        if request.method == 'POST' and form.is_valid():
            try:
                workflow.submit_request(actor=request.user, company_id=request.company_id, values=form.cleaned_data)
            except ValidationError as exc:
                apply_service_errors(form, exc)
            else:
                messages.success(request, 'Leave request submitted for approval.')
                return redirect('me:leave')
        return render(request, 'leaves/request_form.html', {'form': form, 'title': 'Request leave',
                       'submit_label': 'Submit request', 'back_url': 'me:leave'})


@login_required
@require_http_methods(['GET'])
def leave_inbox(request):
    member = workflow.reviewer(request.user, request.company_id)
    status = request.GET.get('status', 'pending')
    if status not in ('pending', 'approved', 'rejected'):
        status = 'pending'
    query = request.GET.get('q', '').strip()[:100]
    with use_company(request.company_id):
        items = workflow.reviewable(member).filter(status=status).select_related(
            'employee', 'submission_assignment__branch').prefetch_related('segments__leave_type')
        if query:
            items = items.filter(Q(employee__first_name__icontains=query) | Q(employee__last_name__icontains=query))
        page = Paginator(items.order_by('-submitted_at', '-pk'), 25).get_page(request.GET.get('page'))
        return render(request, 'leaves/inbox.html', {'page_obj': page, 'status': status, 'q': query})


@login_required
@require_http_methods(['GET', 'POST'])
def leave_decide(request, pk):
    member = workflow.reviewer(request.user, request.company_id)
    with use_company(request.company_id):
        leave = get_object_or_404(workflow.reviewable(member).select_related('employee'), pk=pk)
        segment = leave.segments.select_related('leave_type').get(status='active')
        form = DecideLeaveForm(request.POST or None, initial={'decision': 'approve', 'pay_type': segment.requested_pay_type})
        if request.method == 'POST' and form.is_valid():
            try:
                workflow.decide_request(actor=request.user, company_id=request.company_id, request_id=pk,
                                        approve=form.cleaned_data['decision'] == 'approve',
                                        pay_type=form.cleaned_data['pay_type'], reason=form.cleaned_data['reason'])
            except ValidationError as exc:
                apply_service_errors(form, exc)
            else:
                messages.success(request, 'Leave decision saved.')
                return redirect('me:leave_inbox')
        return render(request, 'leaves/request_form.html', {
            'form': form, 'title': 'Review leave request', 'submit_label': 'Save decision',
            'back_url': 'me:leave_inbox', 'leave': leave, 'segment': segment,
        })


class BranchAttendanceForm(StyledFormMixin, forms.Form):
    date = forms.DateField(widget=forms.DateInput(attrs={'data-datepicker': '', 'type': 'date'}))
    q = forms.CharField(label='Employee name', required=False, max_length=100)

    def clean_date(self):
        on = self.cleaned_data['date']
        if not 2000 <= on.year <= 2100:
            raise ValidationError('Choose a date from 2000 to 2100.')
        return on


@login_required
@require_http_methods(['GET'])
def branch_attendance(request):
    member = workflow.reviewer(request.user, request.company_id)
    if member.role != 'manager':
        raise PermissionDenied('This page is for branch managers.')
    today = timezone.now().astimezone(ZoneInfo(member.company.timezone or 'UTC')).date()
    form = BranchAttendanceForm(request.GET or None, initial={'date': today})
    valid = not request.GET or form.is_valid()
    on = form.cleaned_data['date'] if request.GET and valid else today
    query = form.cleaned_data.get('q', '') if request.GET and valid else ''
    rows = []
    page = None
    if valid:
        probe = datetime.datetime.combine(on, datetime.time(12), tzinfo=ZoneInfo(member.company.timezone or 'UTC'))
        with use_company(request.company_id):
            placements = EmployeeAssignment.objects.filter(
                branch_id__in=workflow.branch_ids(member), effective_from__lte=probe,
            ).filter(Q(effective_to__isnull=True) | Q(effective_to__gt=probe)).exclude(
                status__in=['draft', 'cancelled']).select_related('employee', 'branch')
            departments = list(member.allowed_departments.values_list('pk', flat=True))
            if departments:
                placements = placements.filter(department_id__in=departments)
            if query:
                placements = placements.filter(Q(employee__first_name__icontains=query) | Q(employee__last_name__icontains=query))
            page = Paginator(placements.order_by('employee__first_name', 'employee_id'), 25).get_page(request.GET.get('page'))
            ids = [placement.employee_id for placement in page]
            if ids:
                recalculate(request.company_id, start=on, end=on, employee_ids=ids)
            records = {record.employee_id: record for record in AttendanceRecord.objects.filter(
                employee_id__in=ids, work_date=on, branch_id__in=workflow.branch_ids(member))}
            live = statuses_for(request.company_id, employee_ids=ids) if ids and on == today else {}
            rows = [{'placement': p, 'record': records.get(p.employee_id), 'now': live.get(p.employee_id)} for p in page]
    return render(request, 'leaves/branch_attendance.html', {
        'form': form, 'rows': rows, 'page_obj': page, 'on': on, 'q': query,
    })
