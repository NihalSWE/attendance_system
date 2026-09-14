"""Shell context: the active company and the user's switchable memberships.

Presentation only. Every view and service still enforces tenant scope and
permissions itself — a name in this dropdown is not authorisation.
"""

from accounts.services import get_active_memberships
from tenants.models import Company


def shell(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"memberships": [], "active_company": None}
    if user.is_superuser and getattr(request.resolver_match, "namespace", "") in ("platform", "catalogue"):
        return {"memberships": [], "active_company": None, "platform_surface": True}

    memberships = list(
        get_active_memberships(user).select_related("company").order_by("company__name")
    )
    company_id = getattr(request, "company_id", None)
    active_company = None
    if company_id:
        active_company = next(
            (m.company for m in memberships if m.company_id == company_id), None
        ) or Company.objects.filter(pk=company_id).first()

    return {
        "memberships": memberships,
        "active_company": active_company,
        # Set by common.middleware.SelfServiceGate: an Employee or Branch
        # manager login, who gets the small "my" sidebar.
        "self_service": getattr(request, "self_service", False),
    }
