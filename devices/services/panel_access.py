"""Who may manage devices, in one place.

The rule lived only in the ``company_user_required`` view decorator. That is
fine while a view is the only way in, but the server-address change is a write
that can strand hardware, so the service checks it too rather than trusting
that its caller was decorated. A form is a convenience layer, never the
security boundary — the same rule this project applies everywhere else.

The rule itself is unchanged: an unrestricted owner or company administrator of
the active company. A membership scoped to particular branches or departments
is refused, because a device belongs to a branch but its settings reach the
whole terminal.
"""

from django.core.exceptions import PermissionDenied

from accounts.services import get_active_memberships
from common.tenant import use_company

MANAGING_ROLES = ("owner", "company_admin")

DENIED_MESSAGE = (
    "Device management currently requires unrestricted company administrator "
    "access."
)


def managing_membership(user, company_id):
    """The membership that lets ``user`` manage devices, or None."""
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    if not company_id:
        return None
    # Platform staff administer companies, they do not operate their devices.
    if getattr(user, "is_superuser", False):
        return None

    membership = get_active_memberships(user).filter(company_id=company_id).first()
    if membership is None or membership.role not in MANAGING_ROLES:
        return None

    # allowed_branches/allowed_departments are tenant-scoped relations. A view
    # already has the context from the middleware, but a service may be called
    # from a device request or a shell, and the rule must answer the same way
    # everywhere rather than raising. The company is the one being asked about,
    # so scoping to it here is exact, not a widening.
    with use_company(company_id):
        if (
            membership.allowed_branches.exists()
            or membership.allowed_departments.exists()
        ):
            return None
    return membership


def may_manage_devices(user, company_id):
    return managing_membership(user, company_id) is not None


def assert_may_manage_devices(user, company_id):
    """Raise PermissionDenied unless ``user`` may manage this company's devices."""
    if not may_manage_devices(user, company_id):
        raise PermissionDenied(DENIED_MESSAGE)
