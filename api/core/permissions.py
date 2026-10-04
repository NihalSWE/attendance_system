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
