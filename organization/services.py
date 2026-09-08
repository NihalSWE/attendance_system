"""Company-scoped organization writes: branches (departments/designations follow).

Authorization lives here, not only in the view. A view is one caller; a service
is the only place a write can happen, so putting the check here means a future
management command, Celery task or FastAPI router cannot bypass it.

Three things are enforced on every write:

1. **Membership** - the actor holds an active CompanyMembership in this company.
2. **Action permission** - their role may manage organization structure.
3. **Row scope** - a member restricted to specific branches may only touch those.
   Submitted ids are re-checked here; a form is not a security boundary.

Every write shares one transaction with its AuditLog row, so a failed audit
rolls the business change back.
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from accounts.models import CompanyMembership
from accounts.services import get_active_memberships
from auditlog.services import record_company_event
from common.choices import ActiveStatus
from common.services import create_validated
from common.tenant import use_company
from organization.models import Branch

# Fields a company administrator may set. Anything outside this is rejected
# rather than silently ignored, so a crafted POST cannot reach another column.
BRANCH_FIELDS = (
    "code", "name", "address", "city", "postal_code", "country_code",
    "timezone", "email", "phone", "is_default", "status",
    "device_attendance_scope_override",
    "opened_on", "closed_on",
)

# Roles allowed to manage organization structure. HR/manager/auditor are not
# included: widening this is a deliberate decision, not a default.
STRUCTURE_ROLES = (
    CompanyMembership.Role.OWNER,
    CompanyMembership.Role.COMPANY_ADMIN,
)


# --------------------------------------------------------------------------
# Authorization
# --------------------------------------------------------------------------

def require_company_membership(actor, company_id):
    """Return the actor's active membership in this company, or refuse."""
    if actor is None or not actor.is_authenticated or not actor.is_active:
        raise PermissionDenied("Authentication is required.")
    membership = (
        get_active_memberships(actor)
        .filter(company_id=company_id)
        .select_related("company")
        .first()
    )
    if membership is None:
        raise PermissionDenied("You are not an active member of this company.")
    return membership


def require_structure_manager(actor, company_id):
    """Membership plus the right to manage branches/departments/designations."""
    membership = require_company_membership(actor, company_id)
    if membership.role not in STRUCTURE_ROLES:
        raise PermissionDenied(
            "Managing company structure requires owner or company administrator access."
        )
    return membership


def visible_branches(membership):
    """Branches this membership may see.

    An empty allowed_branches means unrestricted at branch level - NOT "no
    access". That distinction is the whole point of the field.
    """
    queryset = Branch.objects.all()
    if membership.allowed_branches.exists():
        queryset = queryset.filter(pk__in=membership.allowed_branches.values("pk"))
    return queryset


def assert_branch_in_scope(membership, branch):
    """Re-check a submitted branch id against the member's row scope."""
    if membership.allowed_branches.exists():
        if not membership.allowed_branches.filter(pk=branch.pk).exists():
            raise PermissionDenied("That branch is outside your assigned scope.")


def get_branch_for_edit(*, actor, company_id, branch_id):
    """Fetch a branch the actor is allowed to edit, or refuse."""
    membership = require_structure_manager(actor, company_id)
    with use_company(company_id):
        branch = Branch.objects.filter(pk=branch_id).first()
        if branch is None:
            raise PermissionDenied("Branch not found in this company.")
        assert_branch_in_scope(membership, branch)
    return membership, branch


# --------------------------------------------------------------------------
# Snapshots
# --------------------------------------------------------------------------

def branch_snapshot(branch):
    snapshot = {field: getattr(branch, field) for field in BRANCH_FIELDS}
    for field in ("opened_on", "closed_on"):
        value = snapshot.get(field)
        snapshot[field] = value.isoformat() if value else None
    return snapshot


def _clean_values(values):
    values = dict(values)
    unsupported = set(values) - set(BRANCH_FIELDS)
    if unsupported:
        raise ValidationError(f"Unsupported branch field: {', '.join(sorted(unsupported))}")
    return values


def _demote_other_defaults(company_id, keep_pk=None):
    """Only one active default branch may exist per company.

    A partial unique constraint enforces this in PostgreSQL, so without this
    step the insert would simply fail. Demoting first turns "set this one as
    default" into a working operation rather than an error.
    """
    queryset = Branch.objects.filter(is_default=True)
    if keep_pk:
        queryset = queryset.exclude(pk=keep_pk)
    queryset.update(is_default=False)


# --------------------------------------------------------------------------
# Writes
# --------------------------------------------------------------------------

@transaction.atomic
def create_branch(*, actor, company_id, values):
    """Create a branch. Atomic with its audit row."""
    membership = require_structure_manager(actor, company_id)
    values = _clean_values(values)

    with use_company(company_id):
        if values.get("is_default"):
            _demote_other_defaults(company_id)
        branch = create_validated(
            Branch,
            company=membership.company,
            created_by=actor,
            **values,
        )
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="branch.created", obj=branch, after=branch_snapshot(branch),
        )
    return branch


@transaction.atomic
def update_branch(*, actor, company_id, branch_id, values):
    """Update a branch the actor is scoped to. Atomic with its audit row."""
    membership, branch = get_branch_for_edit(
        actor=actor, company_id=company_id, branch_id=branch_id
    )
    values = _clean_values(values)
    before = branch_snapshot(branch)

    with use_company(company_id):
        if values.get("is_default"):
            _demote_other_defaults(company_id, keep_pk=branch.pk)
        for field, value in values.items():
            setattr(branch, field, value)
        branch.updated_by = actor
        branch.full_clean()
        branch.save()
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="branch.updated", obj=branch,
            before=before, after=branch_snapshot(branch),
        )
    return branch


@transaction.atomic
def set_branch_status(*, actor, company_id, branch_id, status, reason=""):
    """Activate or retire a branch.

    Nothing is deleted: a retired branch keeps its departments, employees and
    attendance history. The default branch cannot be retired, because a company
    must always have one.
    """
    membership, branch = get_branch_for_edit(
        actor=actor, company_id=company_id, branch_id=branch_id
    )
    if status not in dict(ActiveStatus.choices):
        raise ValidationError({"status": "Unknown status."})
    if status == ActiveStatus.INACTIVE and branch.is_default:
        raise ValidationError(
            {"status": "The default branch cannot be retired. Make another branch "
                       "the default first."}
        )

    before = branch_snapshot(branch)
    with use_company(company_id):
        branch.status = status
        branch.updated_by = actor
        branch.full_clean()
        branch.save()
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="branch.status_changed", obj=branch,
            before=before,
            after={**branch_snapshot(branch), "reason": reason},
        )
    return branch
