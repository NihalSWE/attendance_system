"""Panel views.

Every view here is tenant-scoped by construction: the TenantMiddleware sets the
active company from the user's membership, and the TenantOwned managers add the
company filter to each query. A view that forgets is not silently permissive —
the scoped manager raises rather than returning another tenant's rows.
"""

from functools import wraps
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LogoutView
from django.db.models import Count, OuterRef, Q, Subquery
from django.shortcuts import redirect
from base_template.tables import paginate, render
from django.views.decorators.http import require_POST

from accounts.services import ACTIVE_COMPANY_SESSION_KEY, get_active_memberships
from attendance import live_status
from employees.models import Employee, EmployeeAssignment, EmployeeCompensation
from organization.models import Branch, CompanyDepartment


def company_admin_required(view):
    """Until granular company pages ship, fail closed for restricted roles/scopes."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if request.user.is_superuser:
            return redirect("platform:company_list")
        if request.company_id:
            member = get_active_memberships(request.user).filter(company_id=request.company_id).first()
            if not member or member.role not in ("owner", "company_admin") or member.allowed_branches.exists() or member.allowed_departments.exists():
                raise PermissionDenied("These company pages currently require unrestricted company administrator access.")
        return view(request, *args, **kwargs)
    return wrapped


def _no_company(request):
    """Rendered when a signed-in user has no active company membership."""
    return render(request, "base_template/no_company.html", status=200)


@login_required
@company_admin_required
def dashboard(request):
    if request.user.is_superuser:
        return redirect("platform:company_list")
    if not request.company_id:
        return _no_company(request)

    employees = Employee.objects.all()
    by_status = {
        s: employees.filter(employment_status=s).count()
        for s in ("active", "probation", "resigned")
    }
    recent = employees.order_by("-created_at")[:8]

    # Devices that have stopped checking in (plan step N8), for the people who
    # can do something about it — the device pages refuse everybody else.
    from devices.services import connection, panel_access

    stopped_devices = (
        connection.stopped_devices(request.company_id)
        if panel_access.may_manage_devices(request.user, request.company_id)
        else []
    )

    return render(request, "base_template/dashboard.html", {
        "total_employees": employees.count(),
        "active_count": by_status["active"],
        "probation_count": by_status["probation"],
        "resigned_count": by_status["resigned"],
        "branch_count": Branch.objects.count(),
        "department_count": CompanyDepartment.objects.count(),
        "recent": recent,
        "stopped_devices": stopped_devices,
    })


def _employee_list_scope(request):
    """``(company_wide, view_branches)`` for the Employees list (A12 part 4).

    The owner and company admin see every employee, as before. Anyone else
    needs ``employees.view`` and sees the people whose latest placement is in
    those branches.
    """
    from access_control.branch_access import ALL_BRANCHES, branches_for

    member = get_active_memberships(request.user).filter(company_id=request.company_id).first()
    if member and member.role in ("owner", "company_admin") and (
        member.allowed_branches.exists() or member.allowed_departments.exists()
    ):
        # Unchanged from before A12: an administrator restricted to some
        # branches or departments is refused rather than shown the company.
        raise PermissionDenied("These company pages currently require unrestricted company administrator access.")
    branches = branches_for(request.user, request.company_id, "employees.view")
    if not branches:
        raise PermissionDenied("Viewing employees requires owner, company administrator or branch access.")
    # Owner / company admin: every branch, as before.
    return branches is ALL_BRANCHES, branches


@login_required
def employee_list(request):
    if request.user.is_superuser:
        return redirect("platform:company_list")
    if not request.company_id:
        return _no_company(request)
    from access_control.branch_access import ALL_BRANCHES, branches_for

    company_wide, view_branches = _employee_list_scope(request)

    assignment = EmployeeAssignment.objects.filter(employee=OuterRef("pk")).exclude(
        status="cancelled").order_by("-effective_from", "-pk")
    compensation = EmployeeCompensation.objects.filter(employee=OuterRef("pk")).exclude(
        status="cancelled").order_by("-effective_from", "-pk")
    qs = Employee.objects.select_related("user").annotate(
        table_code=Subquery(assignment.values("employee_code")[:1]),
        table_branch=Subquery(assignment.values("branch__name")[:1]),
        table_branch_id=Subquery(assignment.values("branch_id")[:1]),
        table_department=Subquery(assignment.values("department__department__name")[:1]),
        table_designation=Subquery(assignment.values("designation__designation__name")[:1]),
        table_rate=Subquery(compensation.values("base_rate")[:1]),
    ).order_by("first_name", "last_name")
    if view_branches is not ALL_BRANCHES:
        qs = qs.filter(table_branch_id__in=view_branches)

    search = request.GET.get("q", "").strip()
    if search:
        qs = qs.filter(
            Q(first_name__icontains=search)
            | Q(last_name__icontains=search)
            | Q(work_email__icontains=search)
            | Q(assignments__employee_code__icontains=search)
        ).distinct()

    status = request.GET.get("status", "").strip()
    if status:
        qs = qs.filter(employment_status=status)

    page = paginate(request, qs,
        search=("first_name", "last_name", "work_email", "table_code", "table_branch", "table_department", "table_designation", "employment_status"),
        # Sorting by pay would reveal pay order to someone who may not see pay.
        order=("table_code", ("first_name", "last_name"), "table_branch", "table_department", "table_designation", None, "employment_status", "table_rate" if company_wide else None, None))
    paginator, per_page = page.paginator, page.paginator.per_page

    # Current assignment per employee, for code/branch/department columns.
    rows = []
    for employee in page.object_list:
        assignment = (
            employee.assignments.select_related("branch", "department", "designation")
            .exclude(status="cancelled")
            .order_by("-effective_from")
            .first()
        )
        compensation = (
            employee.compensations.exclude(status="cancelled")
            .order_by("-effective_from")
            .first()
        )
        rows.append({"e": employee, "a": assignment, "c": compensation})

    # Where each person is right now, derived from today's scans. Rendered
    # server-side so the column is correct before any script runs; the page
    # then refreshes it once a minute.
    now_by_employee = live_status.statuses_for(
        request.company_id,
        employee_ids=[row["e"].pk for row in rows],
    )
    # A12 part 4: what this viewer may do on each row.
    salary_branches = ALL_BRANCHES if company_wide else branches_for(
        request.user, request.company_id, "salary.view")
    edit_branches = ALL_BRANCHES if company_wide else branches_for(
        request.user, request.company_id, "employees.edit")
    for row in rows:
        row["now"] = now_by_employee.get(row["e"].pk)
        branch_id = row["a"].branch_id if row["a"] else None
        row["show_rate"] = branch_id in salary_branches if branch_id else company_wide
        row["can_edit"] = branch_id in edit_branches if branch_id else company_wide

    return render(request, "base_template/employee_list.html", {
        "rows": rows,
        "employee_ids": ",".join(str(row["e"].pk) for row in rows),
        "page": page,
        "paginator": paginator,
        "search": search,
        "status": status,
        "per_page": per_page,
        "statuses": Employee.EmploymentStatus.choices,
        "company_wide": company_wide,
        "can_create": bool(edit_branches),
    })


@login_required
@company_admin_required
def department_list(request):
    if not request.company_id:
        return _no_company(request)
    departments = paginate(
        request,
        CompanyDepartment.objects.select_related("branch", "department", "head")
        .annotate(table_designations=Count("designations", distinct=True))
        .order_by("branch__name", "department__name"),
        search=("department__code", "department__name", "branch__name", "status"),
        order=("department__code", "department__name", "branch__name", "status", "table_designations"),
    )
    return render(request, "base_template/department_list.html",
                  {"departments": departments})


@require_POST
@login_required
def switch_company(request):
    """Change the active company.

    The submitted id is untrusted input: it is only honoured when the user holds
    an active membership in that company. Otherwise it is ignored entirely.
    """
    try:
        requested = int(request.POST.get("company_id", ""))
    except (ValueError, TypeError):
        requested = None
    if requested and get_active_memberships(request.user).filter(
        company_id=requested
    ).exists():
        request.session[ACTIVE_COMPANY_SESSION_KEY] = int(requested)
    else:
        messages.warning(request, "You do not have access to that company.")
    return redirect("dashboard")


class ConfirmingLogoutView(LogoutView):
    """Log out on POST; on GET, ask instead of refusing.

    Django's LogoutView is POST-only, and rightly so: allowing GET means any
    page could log a user out with `<img src="/logout/">`, which needs no CSRF
    token. Both sidebars already POST, so the button works either way.

    What the plain view gives someone who *reaches the URL another way* -
    typing it, a bookmark, a browser prefetch, or refreshing after logging out
    - is a bare 405 Method Not Allowed page. That reads as a broken site.
    This renders a confirmation with a real POST button instead, so the URL is
    never a dead end and the CSRF protection is untouched.
    """

    http_method_names = ["get", "post", "options"]

    def get(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("login")
        return render(request, "base_template/logout_confirm.html")


def csrf_failure(request, reason=""):
    """Friendly page for a rejected CSRF token.

    Django's default is a bare "Forbidden (403) CSRF verification failed",
    which reads as a broken site. The usual cause is harmless and common: a
    tab left open from before signing in still carries the pre-login token,
    because Django rotates it on login. Signing out from that stale tab then
    fails, and the raw page gives no hint that reloading fixes it.

    The protection itself is untouched — the request is still refused with 403.
    Only the explanation changes.
    """
    return render(
        request,
        "base_template/csrf_failure.html",
        {"reason": reason, "next_url": request.path},
        status=403,
    )
