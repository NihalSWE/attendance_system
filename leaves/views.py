"""Company pages for leave. Thin adapters over ``leaves.services``."""

import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Max, Sum, Exists, Min, OuterRef, Q, Subquery
from django.shortcuts import redirect
from base_template.tables import paginate, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from common.choices import ActiveStatus
from common.forms import apply_service_errors
from common.tenant import use_company
from employees.models import Employee, EmployeeAssignment
from leaves import services
from leaves.forms import CancelLeaveForm, LeaveTypeForm, LeaveTypeStatusForm, RecordLeaveForm
from leaves.models import LeaveRequest, LeaveRequestSegment, LeaveType
from organization.services import (
    STRUCTURE_ROLES,
    require_company_membership,
    require_structure_manager,
)
from organization.views import _company_or_redirect

MONTHS = [
    (1, "January"), (2, "February"), (3, "March"), (4, "April"), (5, "May"),
    (6, "June"), (7, "July"), (8, "August"), (9, "September"), (10, "October"),
    (11, "November"), (12, "December"),
]


def _form_page(request, *, form, title, submit_label, action, success, redirect_to,
               template="leaves/form.html", explanation=""):
    if request.method == "POST" and form.is_valid():
        try:
            result = action(form.cleaned_data)
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, success(result) if callable(success) else success)
            return redirect(redirect_to)
    return render(request, template, {
        "form": form,
        "title": title,
        "submit_label": submit_label,
        "explanation": explanation,
    })


# --------------------------------------------------------------------------
# Leave records
# --------------------------------------------------------------------------

@login_required
@require_http_methods(["GET"])
def leave_list(request):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    membership = require_company_membership(request.user, company_id)

    today = timezone.localdate()
    raw_year = request.GET.get("year", "").strip()
    raw_month = request.GET.get("month", "").strip()
    year = int(raw_year) if raw_year.isdigit() and 2000 <= int(raw_year) <= 2100 else today.year
    month = int(raw_month) if raw_month.isdigit() and 1 <= int(raw_month) <= 12 else today.month
    first = datetime.date(year, month, 1)
    last = (first.replace(day=28) + datetime.timedelta(days=4)).replace(day=1) - datetime.timedelta(days=1)
    status = request.GET.get("status", "").strip()
    query = request.GET.get("q", "").strip()[:200]

    with use_company(company_id):
        # EXISTS rather than a join: joining segments and then ordering by one
        # of their columns duplicates rows, and PostgreSQL refuses DISTINCT
        # combined with an ORDER BY outside the select list.
        in_month = LeaveRequestSegment.objects.filter(
            leave_request=OuterRef("pk"), start_date__lte=last, end_date__gte=first
        )
        queryset = (
            LeaveRequest.objects.select_related("employee", "submitted_by")
            .prefetch_related("segments__leave_type")
            .filter(Exists(in_month))
            .annotate(first_day=Min("segments__start_date"), last_day=Max("segments__end_date"),
                      table_type=Min("segments__leave_type__name"), table_pay=Min("segments__requested_pay_type"),
                      table_units=Sum("segments__requested_units"))
        )
        total = queryset.count()
        if status in dict(LeaveRequest.Status.choices):
            queryset = queryset.filter(status=status)
        if query:
            of_type = LeaveRequestSegment.objects.filter(
                leave_request=OuterRef("pk"), leave_type__name__icontains=query
            )
            queryset = queryset.filter(
                Q(employee__first_name__icontains=query)
                | Q(employee__last_name__icontains=query)
                | Exists(of_type)
            )
        filtered_total = queryset.count()
        page = paginate(request, queryset.order_by("-first_day", "-pk"),
            search=("employee__first_name", "employee__last_name", "table_type", "status"),
            order=(("employee__first_name", "employee__last_name"), "table_type", "first_day", "last_day", "table_units", "table_pay", "status", None))
        has_types = LeaveType.objects.filter(status=ActiveStatus.ACTIVE).exists()

    return render(request, "leaves/leave_list.html", {
        "page": page,
        "total": total,
        "filtered_total": filtered_total,
        "year": year,
        "month": month,
        "month_name": dict(MONTHS)[month],
        "months": MONTHS,
        "years": list(range(today.year + 1, today.year - 5, -1)),
        "status": status,
        "statuses": [
            (LeaveRequest.Status.APPROVED, "Approved"),
            (LeaveRequest.Status.CANCELLED, "Cancelled"),
        ],
        "query": query,
        "has_types": has_types,
        "can_record": membership.role in services.LEAVE_RECORDER_ROLES,
    })


@login_required
@require_http_methods(["GET", "POST"])
def leave_record(request):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    services.require_leave_recorder(request.user, company_id)
    with use_company(company_id):
        current_code = EmployeeAssignment.objects.filter(employee=OuterRef("pk")).exclude(
            status="cancelled").order_by("-effective_from", "-pk").values("employee_code")[:1]
        form = RecordLeaveForm(
            request.POST or None,
            employees=Employee.objects.exclude(
                employment_status__in=[
                    Employee.EmploymentStatus.RESIGNED,
                    Employee.EmploymentStatus.TERMINATED,
                    Employee.EmploymentStatus.RETIRED,
                ]
            ).annotate(table_code=Subquery(current_code)).order_by("first_name", "last_name"),
            leave_types=LeaveType.objects.filter(status=ActiveStatus.ACTIVE).order_by("name"),
            initial={"pay_type": "paid", "duration": "full_day"},
        )
        return _form_page(
            request,
            form=form,
            title="Record leave",
            submit_label="Record leave",
            template="leaves/record_form.html",
            redirect_to="leaves:leave_list",
            explanation=(
                "For leave that is already approved. Weekly off days and holidays "
                "inside the dates are not counted as leave."
            ),
            success=lambda leave: (
                f"Leave recorded: {leave.segments.first().requested_units.normalize()} "
                "working day(s)."
            ),
            action=lambda data: services.record_leave(
                actor=request.user, company_id=company_id, values=data
            ),
        )


@login_required
@require_http_methods(["GET", "POST"])
def leave_cancel(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    _, leave = services.get_leave_for_edit(
        actor=request.user, company_id=company_id, request_id=pk
    )
    with use_company(company_id):
        segment = leave.segments.select_related("leave_type").first()
    form = CancelLeaveForm(request.POST or None)
    return _form_page(
        request,
        form=form,
        title=f"Cancel leave for {leave.employee.full_name}",
        submit_label="Cancel leave",
        redirect_to="leaves:leave_list",
        explanation=(
            f"{segment.leave_type.name}, {segment.start_date:%d %b %Y} to "
            f"{segment.end_date:%d %b %Y}. It is kept on record as cancelled; its "
            "days stop counting once attendance is recalculated."
        ),
        success="Leave cancelled.",
        action=lambda data: services.cancel_leave(
            actor=request.user, company_id=company_id, request_id=leave.pk,
            reason=data.get("reason", ""),
        ),
    )


# --------------------------------------------------------------------------
# Leave types
# --------------------------------------------------------------------------

@login_required
@require_http_methods(["GET"])
def leave_type_list(request):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    membership = require_company_membership(request.user, company_id)
    with use_company(company_id):
        leave_types = paginate(request, LeaveType.objects.order_by("status", "name"),
            search=("code", "name", "description", "status"),
            order=("code", "name", "days_per_year", "description", "status", None))
        default_codes = [code for code, *_ in services.DEFAULT_LEAVE_TYPES]
        missing_defaults = LeaveType.objects.filter(code__in=default_codes).count() < len(default_codes)
    return render(request, "leaves/leave_type_list.html", {
        "leave_types": leave_types,
        "can_manage": membership.role in STRUCTURE_ROLES,
        "missing_defaults": missing_defaults,
    })


@login_required
@require_http_methods(["POST"])
def leave_type_defaults(request):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    created = services.add_default_leave_types(actor=request.user, company_id=company_id)
    if created:
        messages.success(request, "Added " + ", ".join(t.name for t in created) + ".")
    else:
        messages.info(request, "This company already has every default leave type.")
    return redirect("leaves:leave_type_list")


@login_required
@require_http_methods(["GET", "POST"])
def leave_type_create(request):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    require_structure_manager(request.user, company_id)
    with use_company(company_id):
        return _form_page(
            request,
            form=LeaveTypeForm(request.POST or None),
            title="Add leave type",
            submit_label="Add leave type",
            redirect_to="leaves:leave_type_list",
            success="Leave type added.",
            action=lambda data: services.create_leave_type(
                actor=request.user, company_id=company_id, values=data
            ),
        )


@login_required
@require_http_methods(["GET", "POST"])
def leave_type_edit(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    _, leave_type = services.get_leave_type_for_edit(
        actor=request.user, company_id=company_id, leave_type_id=pk
    )
    with use_company(company_id):
        return _form_page(
            request,
            form=LeaveTypeForm(request.POST or None, instance=leave_type),
            title=f"Edit {leave_type.name}",
            submit_label="Save leave type",
            redirect_to="leaves:leave_type_list",
            success="Leave type saved.",
            action=lambda data: services.update_leave_type(
                actor=request.user, company_id=company_id,
                leave_type_id=leave_type.pk, values=data,
            ),
        )


@login_required
@require_http_methods(["GET", "POST"])
def leave_type_status(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    _, leave_type = services.get_leave_type_for_edit(
        actor=request.user, company_id=company_id, leave_type_id=pk
    )
    return _form_page(
        request,
        form=LeaveTypeStatusForm(request.POST or None, initial={"status": leave_type.status}),
        title=f"Change status of {leave_type.name}",
        submit_label="Save status",
        redirect_to="leaves:leave_type_list",
        explanation=(
            "An inactive leave type cannot be chosen for new leave. Leave already "
            "recorded with it is unchanged."
        ),
        success="Leave type status saved.",
        action=lambda data: services.set_leave_type_status(
            actor=request.user, company_id=company_id,
            leave_type_id=leave_type.pk, status=data["status"],
        ),
    )
