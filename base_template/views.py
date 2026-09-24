"""Panel views.

Every view here is tenant-scoped by construction: the TenantMiddleware sets the
active company from the user's membership, and the TenantOwned managers add the
company filter to each query. A view that forgets is not silently permissive —
the scoped manager raises rather than returning another tenant's rows.
"""

from dataclasses import dataclass, field
from functools import wraps

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LogoutView
from django.db.models import CharField, Count, IntegerField, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.shortcuts import redirect
from base_template.tables import code_sort_key, paginate, render
from django.views.decorators.http import require_POST

from access_control.page_access import may_open
from organization.access_services import branch_choices
from accounts.services import ACTIVE_COMPANY_SESSION_KEY, get_active_memberships
from attendance import live_status
from employees.models import Employee, EmployeeAssignment, EmployeeCompensation
from organization.models import Branch, Department


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

    # People still in long after their shift (N11), for whoever may fix it.
    from attendance import access as attendance_access
    from attendance.live_status import STILL_IN_ALERT_MINUTES

    still_in = attendance_access.still_in_for(request.user, request.company_id)

    return render(request, "base_template/dashboard.html", {
        "total_employees": employees.count(),
        "active_count": by_status["active"],
        "probation_count": by_status["probation"],
        "resigned_count": by_status["resigned"],
        "branch_count": Branch.objects.count(),
        "department_count": Department.objects.count(),
        "recent": recent,
        "stopped_devices": stopped_devices,
        "still_in": still_in,
        "still_in_hours": STILL_IN_ALERT_MINUTES // 60,
    })


def _employee_list_scope(request):
    """``(company_wide, Scope)`` for the Employees list (A12 part 4).

    The owner and company admin see every employee, as before. Anyone else
    needs ``employees.view`` and sees the people whose latest placement is in
    those branches — or, for a department head, in the department they head.
    """
    from access_control.branch_access import scope_for

    member = get_active_memberships(request.user).filter(company_id=request.company_id).first()
    if member and member.role in ("owner", "company_admin") and (
        member.allowed_branches.exists() or member.allowed_departments.exists()
    ):
        # Unchanged from before A12: an administrator restricted to some
        # branches or departments is refused rather than shown the company.
        raise PermissionDenied("These company pages currently require unrestricted company administrator access.")
    scope = scope_for(request.user, request.company_id, "employees.view")
    if not scope:
        raise PermissionDenied(
            "Viewing employees requires owner, company administrator, branch "
            "access, or heading a department."
        )
    # Owner / company admin: every branch, as before.
    return scope.is_all, scope


#: The "Needs setup" filter (Ajay, 2026-09-21). An imported employee sits in
#: the branch's Unassigned department with no salary until HR sets them up.
#: These are computed from the live data on every request - never from the
#: needs_hr_review flag, which is written once at import and would go stale the
#: moment HR fixed someone. Each option is inclusive: "Needs department" is
#: everyone still without one, whether or not they also need a salary.
SETUP_FILTERS = (
    ("", "All"),
    ("department", "Needs department"),
    ("salary", "Needs salary"),
    ("both", "Needs both"),
    ("complete", "Complete"),
)


@dataclass
class EmployeeListQuery:
    """Everything the Employees list shows, built once for the page and its
    downloads so the two cannot disagree."""

    queryset: object
    company_wide: bool
    salary_branches: object
    show_rate_column: bool
    columns: tuple
    search_fields: tuple
    search: str
    status: str
    branch: str
    setup: str
    branches: list = field(default_factory=list)
    chosen_branch: object = None
    setup_counts: dict = field(default_factory=dict)
    scope_name: str = ""


def employee_list_query(request):
    """Scope, filter and annotate the Employees list for ``request.user``.

    Scoping (A12 part 4 / department heads), the page's own filters (search,
    status, setup), and the pay rules: ``show_rate_column`` is True only for a
    viewer who may see pay in at least one branch, and the salary side of
    "Needs setup" only counts rows whose pay that viewer may see - so the
    filter never tells a department head who has a salary.
    """
    from access_control.branch_access import ALL_BRANCHES, branches_for
    from devices.services.mapping import UNASSIGNED_CODE
    from organization.models import Branch

    company_wide, view_branches = _employee_list_scope(request)

    assignment = EmployeeAssignment.objects.filter(employee=OuterRef("pk")).exclude(
        status="cancelled").order_by("-effective_from", "-pk")
    compensation = EmployeeCompensation.objects.filter(employee=OuterRef("pk")).exclude(
        status="cancelled").order_by("-effective_from", "-pk")
    qs = Employee.objects.select_related("user").annotate(
        table_code=Subquery(assignment.values("employee_code")[:1]),
        table_branch=Subquery(assignment.values("branch__name")[:1]),
        table_branch_id=Subquery(assignment.values("branch_id")[:1]),
        table_department=Subquery(assignment.values("department__name")[:1]),
        table_department_id=Subquery(assignment.values("department_id")[:1]),
        # Coalesced: someone with no placement must still land in exactly one
        # setup state, and NOT (NULL = x) would silently drop them.
        setup_department_code=Coalesce(
            Subquery(assignment.values("department__code")[:1]), Value(""),
            output_field=CharField()),
        setup_branch_id=Coalesce(
            Subquery(assignment.values("branch_id")[:1]), Value(0),
            output_field=IntegerField()),
        table_designation=Subquery(assignment.values("designation__name")[:1]),
        table_rate=Subquery(compensation.values("base_rate")[:1]),
    ).annotate(
        # Employee ID sorts as a number: 99 before 100.
        table_code_sort=code_sort_key("table_code"),
    ).order_by("first_name", "last_name")
    if not view_branches.is_all:
        reachable = Q(table_branch_id__in=view_branches.branches)
        if view_branches.departments:
            reachable |= Q(table_department_id__in=view_branches.departments)
        qs = qs.filter(reachable)

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

    # One branch at a time, for a company that has several. The choices are
    # the branches this viewer may see anyway, and the scoping above still
    # applies - asking for another branch shows nobody rather than somebody.
    branches = branch_choices(request.company_id, view_branches)
    branch = request.GET.get("branch", "").strip()
    chosen_branch = next((b for b in branches if str(b.pk) == branch), None)
    if chosen_branch is not None:
        qs = qs.filter(table_branch_id=chosen_branch.pk)
    else:
        branch = ""

    # A12 part 4: the pay column only exists for someone who may see pay
    # somewhere (Ajay, 2026-09-20).
    salary_branches = ALL_BRANCHES if company_wide else branches_for(
        request.user, request.company_id, "salary.view")
    show_rate_column = company_wide or bool(salary_branches)

    needs_department = Q(setup_department_code=UNASSIGNED_CODE)
    needs_salary = Q(table_rate__isnull=True)
    if salary_branches is not ALL_BRANCHES:
        # Only rows whose pay this viewer may see; for someone who may see no
        # pay at all, nobody "needs salary" as far as they can tell.
        needs_salary = (needs_salary & Q(setup_branch_id__in=salary_branches)
                        if salary_branches else Q(pk__isnull=True))
    conditions = {
        "department": needs_department,
        "salary": needs_salary,
        "both": needs_department & needs_salary,
        "complete": ~needs_department & ~needs_salary,
    }
    setup_counts = qs.aggregate(
        all=Count("pk", distinct=True),
        **{key: Count("pk", filter=condition, distinct=True)
           for key, condition in conditions.items()},
    )
    setup = request.GET.get("setup", "").strip()
    if setup in conditions:
        qs = qs.filter(conditions[setup])
    else:
        setup = ""

    columns = [None, "table_code_sort", ("first_name", "last_name"), "table_branch",
               "table_department", "table_designation", None, "employment_status"]
    if show_rate_column:
        # Sorting by pay would reveal pay order to someone who may not see pay,
        # so it is sortable only for a viewer who may see every row's rate.
        columns.append("table_rate" if company_wide else None)
    columns += [None, None]

    # What a download is named after: the one branch this viewer sees, or the
    # company when they see more than one.
    scope_name = chosen_branch.name if chosen_branch is not None else ""
    if not scope_name and not view_branches.is_all and len(view_branches.branches) == 1             and not view_branches.departments:
        scope_name = Branch.objects.filter(pk__in=view_branches.branches).values_list(
            "name", flat=True).first() or ""

    return EmployeeListQuery(
        queryset=qs, company_wide=company_wide, salary_branches=salary_branches,
        show_rate_column=show_rate_column, columns=tuple(columns),
        search_fields=("first_name", "last_name", "work_email", "table_code", "table_branch",
                       "table_department", "table_designation", "employment_status"),
        search=search, status=status, branch=branch, setup=setup,
        setup_counts=setup_counts, branches=branches, chosen_branch=chosen_branch,
        scope_name=scope_name,
    )


def setup_gaps(employee, assignment, compensation, show_rate):
    """What an employee still needs, for the chip on their row."""
    from devices.services.mapping import UNASSIGNED_CODE

    gaps = []
    if assignment is not None and assignment.department.code == UNASSIGNED_CODE:
        gaps.append("No department")
    if show_rate and compensation is None:
        gaps.append("No salary")
    return gaps


@login_required
def employee_list(request):
    if request.user.is_superuser:
        return redirect("platform:company_list")
    if not request.company_id:
        return _no_company(request)
    from access_control.branch_access import ALL_BRANCHES, branches_for

    listing = employee_list_query(request)
    # A download is this same view with ?format=, so it is the page's own
    # permissions and the page's own query - it cannot show more than the page.
    fmt = request.GET.get("format", "")
    if fmt in ("xlsx", "pdf"):
        from base_template.employee_export import export_employees

        return export_employees(request, listing, fmt)
    company_wide = listing.company_wide
    salary_branches = listing.salary_branches

    page = paginate(request, listing.queryset,
        search=listing.search_fields, order=listing.columns)
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
    edit_branches = ALL_BRANCHES if company_wide else branches_for(
        request.user, request.company_id, "employees.edit")
    # Device mapping (Map / Bulk map): the active devices of each branch this
    # viewer may map in, and what each row already has.
    from devices.models import BiometricDevice
    from devices.services.mapping import device_badges

    devices_by_branch = {}
    for device in BiometricDevice.objects.exclude(status__in=["retired", "suspended"]).select_related(
            "branch").order_by("name"):
        if edit_branches is ALL_BRANCHES or device.branch_id in edit_branches:
            devices_by_branch.setdefault(device.branch_id, {"name": device.branch.name, "devices": []})[
                "devices"].append({"id": device.pk, "name": device.name})
    badges = device_badges(request.company_id, [row["e"].pk for row in rows])
    for row in rows:
        row["now"] = now_by_employee.get(row["e"].pk)
        branch_id = row["a"].branch_id if row["a"] else None
        row["show_rate"] = branch_id in salary_branches if branch_id else company_wide
        row["can_edit"] = branch_id in edit_branches if branch_id else company_wide
        row["device"] = badges.get(row["e"].pk)
        row["can_map"] = row["can_edit"] and branch_id in devices_by_branch
        row["gaps"] = setup_gaps(row["e"], row["a"], row["c"], row["show_rate"])

    # The setup filter keeps the other filters; the salary options only exist
    # for a viewer who may see pay somewhere.
    setup_links = []
    for key, label in SETUP_FILTERS:
        if key in ("salary", "both") and not listing.show_rate_column:
            continue
        params = request.GET.copy()
        for drop in ("setup", "page", "table", "draw", "start", "length"):
            params.pop(drop, None)
        if key:
            params["setup"] = key
        setup_links.append({
            "key": key, "label": label, "count": listing.setup_counts.get(key or "all", 0),
            "url": f"?{params.urlencode()}" if params else "?",
            "active": key == listing.setup,
        })
    export_params = request.GET.copy()
    for drop in ("page", "per_page", "table", "draw", "start", "length", "format"):
        export_params.pop(drop, None)

    return render(request, "base_template/employee_list.html", {
        "rows": rows,
        "employee_ids": ",".join(str(row["e"].pk) for row in rows),
        "page": page,
        "paginator": paginator,
        "search": listing.search,
        "status": listing.status,
        "branches": listing.branches,
        "branch": listing.branch,
        "setup": listing.setup,
        "setup_links": setup_links,
        "export_query": export_params.urlencode(),
        "per_page": per_page,
        "statuses": Employee.EmploymentStatus.choices,
        "company_wide": company_wide,
        "show_rate_column": listing.show_rate_column,
        "can_create": bool(edit_branches),
        "map_branches": [
            {"id": pk, "name": value["name"], "devices": value["devices"]}
            for pk, value in sorted(devices_by_branch.items(), key=lambda item: item[1]["name"])
        ],
        # Nihal's pages: linked once they are open to this viewer (A12 part 7).
        "live_now": company_wide or may_open(request.user, request.company_id, "attendance:attendance_now"),
        "detail_links": company_wide or may_open(request.user, request.company_id, "organization:employee_detail"),
    })


@login_required
@company_admin_required
def department_list(request):
    if not request.company_id:
        return _no_company(request)
    departments = paginate(
        request,
        Department.objects.select_related("branch", "head")
        .annotate(table_designations=Count("designations", distinct=True))
        .order_by("branch__name", "name"),
        search=("code", "name", "branch__name", "status"),
        order=("code", "name", "branch__name", "status", "table_designations"),
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
