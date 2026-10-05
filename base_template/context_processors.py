"""Shell context: the active company and the user's switchable memberships.

Presentation only. Every view and service still enforces tenant scope and
permissions itself — a name in this dropdown is not authorisation.
"""

from access_control.branch_access import branches_for, can
from access_control.page_access import opener
from accounts.services import get_active_memberships
from tenants.models import Company
from base_template.navigation import company_menus
from devices.services.panel_access import may_manage_devices
from leaves.services import LEAVE_RECORDER_ROLES
from organization.services import STRUCTURE_ROLES
from payroll.services import salary_month_branches
from reports.access import report_kinds


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
    leave_approver = False
    if membership and not self_service:
        unrestricted_admin = may_manage_devices(user, company_id)
        menus = company_menus(
            request, can_manage=membership.role in STRUCTURE_ROLES,
            can_manage_devices=unrestricted_admin,
            can_record_leave=membership.role in LEAVE_RECORDER_ROLES,
            can_see_salary=bool(salary_month_branches(user, company_id)[1]),
            report_kinds=report_kinds(user, company_id),
            # HR views and edits employees everywhere (Ajay, 2026-09-27).
            people_codes=({code for code in ("employees.view", "employees.edit")
                           if branches_for(user, company_id, code)}
                          if membership.role == "hr" else None),
        )
    elif membership and self_service:
        # A12: the company pages this branch manager / person given access may
        # open (access_control.page_access), under "Company" in their sidebar.
        branch_menus = company_menus(
            request, can_manage=False, can_manage_devices=False,
            allowed=opener(user, company_id),
        )
        # The approval inbox: a branch manager, or someone given "Approve leave".
        leave_approver = membership.role == "manager" or can(user, company_id, "leave.approve")

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
        "sidebar_leave_approver": leave_approver,
        # "My LFA", once the company has switched LFA on (2026-09-28).
        "sidebar_lfa": bool(self_service and company_id and _lfa_on(company_id)),
        # Developer's API: the owner and administrator of a company the platform
        # owner enabled it for (2026-10-06).
        "sidebar_developer_api": bool(
            membership and not self_service and membership.role in DEVELOPER_API_ROLES
            and developer_api_on(company_id)),
    }


#: Who sees the Developer's API link once the company has the module: those who
#: make the company's API keys and set up its integrations.
DEVELOPER_API_ROLES = ("owner", "company_admin")


def developer_api_on(company_id):
    """Whether the platform owner has enabled the Developer's API module for
    this company (tenants Feature "developer_api", Companies → Feature access)."""
    from access_control.services import is_feature_enabled
    from tenants.models import Feature

    feature = Feature.objects.filter(code="developer_api", is_active=True).first()
    return feature is not None and is_feature_enabled(company_id, feature)


def _lfa_on(company_id):
    from payroll.models import LfaSettings

    return LfaSettings.all_objects.filter(company_id=company_id, enabled=True).exists()
