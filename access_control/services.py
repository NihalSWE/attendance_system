"""Effective-permission resolution.

One question, one answer: *may this employee perform this action right now?*

Resolution order (first decisive answer wins):

    1. The permission exists and is active          -> else DENY
    2. The company has the feature enabled          -> else DENY
    3. An employee override applies                 -> grant/revoke decides
    4. The employee's designation rule applies      -> allowed/denied decides
    5. Nothing said yes                             -> DENY

**Default deny.** Silence never means permission. Enabling a company feature does
not hand every employee its actions — designation defaults and individual
overrides still decide, which is an explicit product requirement.

All lookups are dated, so asking "could they approve leave on 3 March?" gives the
answer that was true then, not the answer that is true today.
"""

from django.db.models import Q
from django.utils import timezone

from access_control.models import (
    AccessPermission,
    DesignationPermission,
    EmployeePermissionOverride,
)
from employees.models import EmployeeAssignment
from tenants.models import CompanyFeature


def _effective_at(queryset, at):
    """Rows whose dated interval covers ``at`` (inclusive start, exclusive end)."""
    return queryset.filter(
        Q(effective_from__lte=at)
        & (Q(effective_to__isnull=True) | Q(effective_to__gt=at))
    )


def is_feature_enabled(company, feature, at=None):
    """True when an active CompanyFeature enables the feature at ``at``.

    A 'disable' row wins over an 'enable' row: an explicit switch-off is a
    deliberate act and must not be overridden by a stale grant.
    """
    at = at or timezone.now()
    rows = CompanyFeature.all_objects.filter(
        company=company, feature=feature, is_active=True
    ).filter(
        Q(starts_at__isnull=True) | Q(starts_at__lte=at)
    ).filter(
        Q(ends_at__isnull=True) | Q(ends_at__gt=at)
    )
    effects = set(rows.values_list("effect", flat=True))
    if CompanyFeature.Effect.DISABLE in effects:
        return False
    return CompanyFeature.Effect.ENABLE in effects


def get_current_designation(employee, at=None):
    """The designation from the employee's assignment effective at ``at``."""
    at = at or timezone.now()
    assignment = (
        _effective_at(
            EmployeeAssignment.all_objects.filter(employee=employee).exclude(
                status="cancelled"
            ),
            at,
        )
        .order_by("-effective_from")
        .first()
    )
    return assignment.designation if assignment else None


def has_permission(employee, permission_code, at=None):
    """Return True if ``employee`` may perform ``permission_code`` at ``at``."""
    at = at or timezone.now()

    permission = AccessPermission.objects.filter(
        code=permission_code, is_active=True
    ).first()
    if permission is None:
        return False

    company = employee.company
    if not is_feature_enabled(company, permission.feature, at):
        return False

    # 3. Individual override wins over the designation default.
    override = (
        _effective_at(
            EmployeePermissionOverride.all_objects.filter(
                company=company,
                employee=employee,
                permission=permission,
                status=EmployeePermissionOverride.Status.ACTIVE,
            ),
            at,
        )
        .order_by("-effective_from")
        .first()
    )
    if override is not None:
        return override.effect == EmployeePermissionOverride.Effect.GRANT

    # 4. Fall back to the designation's dated rule.
    designation = get_current_designation(employee, at)
    if designation is None:
        return False

    rule = (
        _effective_at(
            DesignationPermission.all_objects.filter(
                company=company, designation=designation, permission=permission
            ),
            at,
        )
        .order_by("-effective_from")
        .first()
    )
    if rule is not None:
        return rule.access_level == DesignationPermission.AccessLevel.ALLOWED

    # 5. Default deny.
    return False


def get_effective_permissions(employee, at=None):
    """Every permission code this employee currently holds.

    Convenience for building menus. Menu visibility is presentation only — each
    page and service must still call has_permission() itself.
    """
    at = at or timezone.now()
    return {
        code
        for code in AccessPermission.objects.filter(is_active=True).values_list(
            "code", flat=True
        )
        if has_permission(employee, code, at)
    }
