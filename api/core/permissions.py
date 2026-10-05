"""Who may call an endpoint (docs/api/00-PLAN.md, 2.5).

The API denies by default (settings: DenyAll). Every endpoint names its
permission classes itself - the documentation test refuses an endpoint that
does not - so nothing is ever open by accident.

- ``Public``: no login (only ping, login, password reset, signature test).
- ``IsPerson``: a logged-in person (an app or web session), not an API key.
- ``IsCompanyAdmin``: a person who is the owner or company administrator of
  the company the request acts for - the panels' own rule.
- ``HasScope``: an API key must hold every scope the view lists in
  ``required_scopes``; people are not limited by scopes (their role decides).
"""

from rest_framework.permissions import BasePermission

from api.core.errors import ApiError


class DenyAll(BasePermission):
    """The default for any endpoint that forgot to say who may use it."""

    message = "This endpoint is not open."

    def has_permission(self, request, view):
        return False


class Public(BasePermission):
    """No login needed. Use only for endpoints the plan lists as public."""

    def has_permission(self, request, view):
        return True


class IsPerson(BasePermission):
    def has_permission(self, request, view):
        return getattr(request, "api_session", None) is not None


class IsCompanyAdmin(BasePermission):
    message = "Only the company's owner or administrator may do this."

    def has_permission(self, request, view):
        from accounts.services import get_active_memberships
        from api.core.logins import ADMIN_ROLES

        if getattr(request, "api_session", None) is None:
            return False
        company_id = getattr(request, "company_id", None)
        return bool(company_id) and get_active_memberships(request.user).filter(
            company_id=company_id, role__in=ADMIN_ROLES).exists()


class HasScope(BasePermission):
    def has_permission(self, request, view):
        key = getattr(request, "api_key", None)
        if key is None:
            return getattr(request, "api_session", None) is not None
        missing = [scope for scope in getattr(view, "required_scopes", []) if scope not in key.scopes]
        if missing:
            raise ApiError("scope_missing", f"This API key needs the scope {', '.join(missing)}.")
        return True


class PanelRule(BasePermission):
    """The panels' own gate, for an endpoint that mirrors a panel page.

    The panels let the owner, company administrator, HR and payroll open the
    company pages; an Employee, Branch manager or Auditor login reaches only
    the pages for a permission they hold in some branch
    (``common.middleware.SelfServiceGate`` and ``access_control.page_access``).
    The view names the panel page it mirrors in ``panel_page`` - one name, or
    ``{method: name}`` when its methods mirror different pages - and the same
    check is made here; the panel's service then checks again (role, branch).

    API keys: ``read_scope`` (GET) or ``write_scope`` (anything else) must be
    among the key's scopes; None means keys cannot call it at all. A key acts
    as its creator, an owner or company administrator.
    """

    message = "Your login cannot open this part of the panel."

    def has_permission(self, request, view):
        from rest_framework.permissions import SAFE_METHODS

        from accounts.services import get_active_memberships
        from access_control.page_access import may_open
        from common.middleware import SELF_SERVICE_NAMESPACES, SELF_SERVICE_ROLES

        key = getattr(request, "api_key", None)
        if key is not None:
            scope = getattr(view, "read_scope" if request.method in SAFE_METHODS
                            else "write_scope", None)
            if not scope:
                raise ApiError("permission_denied", "API keys cannot use this endpoint; "
                                                    "a person must log in.")
            if scope not in key.scopes:
                raise ApiError("scope_missing", f"This API key needs the scope {scope}.")
            return True
        if getattr(request, "api_session", None) is None or not request.company_id:
            return False
        role = (get_active_memberships(request.user).filter(company_id=request.company_id)
                .values_list("role", flat=True).first())
        if role is None:
            return False
        if role not in SELF_SERVICE_ROLES:
            return True
        page = getattr(view, "panel_page", None)
        if isinstance(page, dict):
            page = page.get(request.method)
        if page and page.split(":")[0] in SELF_SERVICE_NAMESPACES:
            # A My account page (the leave approval inbox): open to every
            # login, as on the panel; its service decides who may act.
            return True
        return bool(page) and may_open(request.user, request.company_id, page)
