"""Who may open and change Store & Inventory pages.

Two gates, both checked by every page and service:

1. **The module** - the platform owner enabled the "inventory" feature for
   the company (Companies -> Feature access). Without it every inventory page
   is "not available" and the permissions are not offered on the Access page.
2. **The branch permission** (A12, ``access_control.branch_access``):
   - company masters (units, categories, items, suppliers, customers) need
     the code in at least one branch - they belong to the whole company;
   - stores, bins, stock and documents need it in the store's branch.

Owner and company admin hold every code everywhere; a branch manager holds
every code in their branches; anyone else what the Access page gave them.
"""

from django.core.exceptions import PermissionDenied

from access_control.branch_access import ALL_BRANCHES, branches_for, can, feature_on
from organization.services import require_company_membership

FEATURE = "inventory"


def inventory_on(company_id):
    return bool(company_id) and feature_on(company_id, FEATURE)


def require_module(actor, company_id):
    """The actor's membership, once the company has the module."""
    membership = require_company_membership(actor, company_id)
    if not inventory_on(company_id):
        raise PermissionDenied("Store & Inventory is not enabled for this company.")
    return membership


def may(actor, company_id, code, branch_id=None):
    """``code`` in that branch, or anywhere at all when no branch is given."""
    return inventory_on(company_id) and can(actor, company_id, code, branch_id)


def require(actor, company_id, code, branch_id=None):
    """Membership, once the module is on and ``actor`` holds ``code`` there.

    ``code`` may be a tuple: any one of them is enough (viewing a sales order
    is open to whoever enters, confirms, delivers or invoices it).
    """
    membership = require_module(actor, company_id)
    codes = (code,) if isinstance(code, str) else code
    if not any(can(actor, company_id, one, branch_id) for one in codes):
        raise PermissionDenied("You do not have access to do this.")
    return membership


def scope_warehouses(queryset, actor, company_id, code, field="branch"):
    """Rows of stores (or of anything with a store) in branches where ``actor``
    holds ``code``. ``field`` is the path to the branch, e.g. ``warehouse__branch``."""
    codes = (code,) if isinstance(code, str) else code
    found = set()
    for one in codes:
        branches = branches_for(actor, company_id, one)
        if branches is ALL_BRANCHES:
            return queryset
        found |= branches
    return queryset.filter(**{f"{field}__in": found})


def sees_costs(actor, company_id):
    """Costs and stock values are hidden without "See stock costs and values"
    (as pay is hidden without "View salaries")."""
    return may(actor, company_id, "inventory.valuation")
