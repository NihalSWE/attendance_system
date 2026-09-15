"""Tenant context middleware.

Translates an authenticated HTTP request into a tenant context so that
``Model.objects`` on TenantOwned models is automatically scoped for the whole
request. Deliberately thin: the "which company may this user act in?" rule lives
in accounts.services so workers and a future FastAPI layer reuse it.

Must be listed AFTER django.contrib.auth's AuthenticationMiddleware, because it
depends on request.user being resolved.

The context is always cleared in a ``finally`` block. That is not optional:
threads and connections are reused between requests, so a leaked context would
let the next request read the previous tenant's data.
"""

from accounts.services import ACTIVE_COMPANY_SESSION_KEY, resolve_active_company_id
from common.tenant import clear_current_company_id, set_current_company_id


class TenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        session = getattr(request, "session", None)
        requested_company_id = (
            session.get(ACTIVE_COMPANY_SESSION_KEY) if session is not None else None
        )
        company_id = resolve_active_company_id(
            getattr(request, "user", None), requested_company_id
        )

        # Expose it on the request for views/templates that need the active tenant.
        request.company_id = company_id

        # Set unconditionally (None for anonymous) so the reset always restores a
        # clean state, and so an unauthenticated request fails loud rather than
        # inheriting whatever the previous request on this thread left behind.
        token = set_current_company_id(company_id)
        try:
            return self.get_response(request)
        finally:
            clear_current_company_id(token)


# Roles whose people use their own pages, not the company's.
SELF_SERVICE_ROLES = ("employee", "manager")
# Their pages, plus signing in and out.
SELF_SERVICE_NAMESPACES = ("me",)
ALWAYS_OPEN_URL_NAMES = ("login", "logout", "switch_company")
# Company pages a branch manager may open (A12). Each checks access itself;
# part 3 of A12 opens the rest of the company pages by permission.
BRANCH_MANAGER_VIEWS = ("organization:access", "organization:access_person")


class SelfServiceGate:
    """Keep employees and branch managers on their own pages.

    Company pages were built for the owner and company administrator; several
    only check that the person is a member of the company, which was enough
    while nobody else could sign in. An Employee or Branch manager login may
    only reach the ``me`` pages (and sign in/out); anything else sends them to
    My account, or is refused for a form post. One gate instead of a check in
    every view, so a company page added later is closed to them by default.

    Also sets ``request.self_service`` for the sidebar. Must follow
    TenantMiddleware (it needs ``request.company_id``).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        from django.core.exceptions import PermissionDenied
        from django.shortcuts import redirect

        from accounts.models import CompanyMembership

        request.self_service = False
        user = getattr(request, "user", None)
        company_id = getattr(request, "company_id", None)
        if user is None or not user.is_authenticated or user.is_superuser or not company_id:
            return None
        role = (
            CompanyMembership.all_objects.filter(
                company_id=company_id, user=user, status=CompanyMembership.Status.ACTIVE
            ).values_list("role", flat=True).first()
        )
        if role not in SELF_SERVICE_ROLES:
            return None
        request.self_service = True
        match = request.resolver_match
        if match is None:
            return None
        if match.namespace in SELF_SERVICE_NAMESPACES or match.url_name in ALWAYS_OPEN_URL_NAMES:
            return None
        if role == CompanyMembership.Role.MANAGER and match.view_name in BRANCH_MANAGER_VIEWS:
            return None
        if request.method in ("GET", "HEAD"):
            return redirect("me:home")
        raise PermissionDenied("This page is for the company's administrators.")
