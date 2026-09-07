"""Membership resolution services.

Business rules live here, not in middleware. Middleware should stay a thin
adapter that translates an HTTP request into a call on these functions, so the
same rules are reusable from Celery tasks, management commands and a future
FastAPI router.

Security note: these queries deliberately use the unscoped ``all_objects``
manager, because resolving *which* company a user may act in necessarily happens
before any tenant context exists. That is the one legitimate bootstrap use of the
escape hatch; the rule below still only ever returns a company the user actually
holds an ACTIVE membership in.
"""

from accounts.models import CompanyMembership

# Where the user's chosen company is remembered between requests.
ACTIVE_COMPANY_SESSION_KEY = "active_company_id"


def get_active_memberships(user):
    """ACTIVE memberships for a user, across all companies."""
    return CompanyMembership.all_objects.filter(
        user=user, status=CompanyMembership.Status.ACTIVE,
        company__status__in=("trial", "active"),
    )


def resolve_active_company_id(user, requested_company_id=None):
    """Return the company id this user may act in, or None.

    If ``requested_company_id`` is supplied (e.g. from the session, where the
    user picked a company), it is honoured ONLY when the user holds an active
    membership in it. An unauthorised or stale request silently falls back to a
    company they do legitimately belong to — never to the requested one.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return None

    memberships = get_active_memberships(user)

    if requested_company_id is not None:
        try:
            requested_company_id = int(requested_company_id)
        except (TypeError, ValueError):
            requested_company_id = None
    if requested_company_id is not None:
        if memberships.filter(company_id=requested_company_id).exists():
            return requested_company_id

    return (
        memberships.order_by("company_id")
        .values_list("company_id", flat=True)
        .first()
    )
