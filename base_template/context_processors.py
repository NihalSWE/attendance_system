"""Shell context: the active company and the user's switchable memberships.

Presentation only. Every view and service still enforces tenant scope and
permissions itself — a name in this dropdown is not authorisation.
"""

from access_control.page_access import opener
from accounts.services import get_active_memberships
from tenants.models import Company
from base_template.navigation import company_menus
from devices.services.panel_access import may_manage_devices
from leaves.services import LEAVE_RECORDER_ROLES
from organization.services import STRUCTURE_ROLES


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

    self_service = getattr(request, "self_service", False)
    membership = next((m for m in memberships if m.company_id == company_id), None)
    menus = []
    unrestricted_admin = False
    branch_menus = []
    if membership and not self_service:
        unrestricted_admin = may_manage_devices(user, company_id)
        menus = company_menus(
            request, can_manage=membership.role in STRUCTURE_ROLES,
            can_manage_devices=unrestricted_admin,
            can_record_leave=membership.role in LEAVE_RECORDER_ROLES,
        )
    elif membership and self_service:
        # A12: the company pages this branch manager / person given access may
        # open (access_control.page_access), under "Company" in their sidebar.
        branch_menus = company_menus(
            request, can_manage=False, can_manage_devices=False,
            allowed=opener(user, company_id),
        )

    return {
        "memberships": memberships,
        "active_company": active_company,
        # Set by common.middleware.SelfServiceGate: an Employee or Branch
        # manager login, who gets the small "my" sidebar.
        "self_service": self_service,
        "company_menus": menus,
        "branch_menus": branch_menus,
        "sidebar_unrestricted_admin": unrestricted_admin,
        "sidebar_branch_manager": bool(membership and membership.role == 'manager'),
    }
