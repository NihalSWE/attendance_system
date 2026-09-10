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
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from accounts.services import ACTIVE_COMPANY_SESSION_KEY, get_active_memberships
from employees.models import Employee
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

    return render(request, "base_template/dashboard.html", {
        "total_employees": employees.count(),
        "active_count": by_status["active"],
        "probation_count": by_status["probation"],
        "resigned_count": by_status["resigned"],
        "branch_count": Branch.objects.count(),
        "department_count": CompanyDepartment.objects.count(),
        "recent": recent,
    })


@login_required
@company_admin_required
def employee_list(request):
    if not request.company_id:
        return _no_company(request)

    qs = Employee.objects.select_related("user").order_by("first_name", "last_name")

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

    try:
        per_page = min(int(request.GET.get("per_page", 25)), 100)
    except ValueError:
        per_page = 25

    paginator = Paginator(qs, per_page)
    page = paginator.get_page(request.GET.get("page"))

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

    return render(request, "base_template/employee_list.html", {
        "rows": rows,
        "page": page,
        "paginator": paginator,
        "search": search,
        "status": status,
        "per_page": per_page,
        "statuses": Employee.EmploymentStatus.choices,
    })


@login_required
@company_admin_required
def department_list(request):
    if not request.company_id:
        return _no_company(request)
    departments = (
        CompanyDepartment.objects.select_related("branch", "department", "head")
        .order_by("branch__name", "department__name")
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
