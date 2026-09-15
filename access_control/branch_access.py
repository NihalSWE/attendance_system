"""Branch access (A12): who may do what, and in which branches.

Kept simple, and separate from ``access_control.services.has_permission`` (the
older department/designation model, which no page uses):

- Owner and company admin: every permission in every branch.
- Branch manager: every branch permission in their own branches, automatically.
- The existing HR role keeps company-wide leave recording and overtime decisions.
- Anyone else: only what an owner, admin or branch manager granted them, per
  branch, as ``EmployeePermissionOverride`` rows (dated, revocable, audited).
  A granter can only hand on access they hold, in branches where they may
  grant it, and never to themselves.

Company-wide settings (salary rules, leave types, attendance settings,
branches and departments, finalising a month) are not branch permissions and
stay owner/admin only. Pages and services must check ``can`` / ``branches_for``
themselves; a menu entry is presentation only.
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from access_control.models import AccessPermission, EmployeePermissionOverride
from accounts.models import CompanyMembership
from accounts.services import get_active_memberships
from auditlog.services import record_company_event
from common.tenant import use_company
from employees.models import Employee
from organization.models import Branch
from tenants.models import Feature

Role = CompanyMembership.Role
Override = EmployeePermissionOverride

# (code, label, feature code, action). Checking needs no database rows; the
# AccessPermission row is created the first time a permission is granted
# (``_permission``), and an existing row with the same code is kept.
BRANCH_PERMISSIONS = (
    ("employees.view", "View employees", "employees", "view"),
    ("employees.edit", "Create and edit employees", "employees", "edit"),
    ("employees.logins", "Create and manage logins", "employees", "manage"),
    ("leave.view", "View leave", "leave", "view"),
    ("leave.record", "Record and cancel leave", "leave", "create"),
    ("leave.approve", "Approve leave requests", "leave", "approve"),
    ("overtime.view", "View overtime", "payroll", "view"),
    ("overtime.decide", "Decide overtime", "payroll", "approve"),
    ("salary.view", "View salaries and payslips", "payroll", "view"),
    ("salary.prepare", "Generate salary and add bonus or deduction lines", "payroll", "edit"),
    ("access.grant", "Give access to others", "access", "manage"),
)
CODES = tuple(code for code, *_ in BRANCH_PERMISSIONS)
LABELS = {code: label for code, label, *_ in BRANCH_PERMISSIONS}

COMPANY_WIDE_ROLES = (Role.OWNER, Role.COMPANY_ADMIN)
# What the existing HR role already did company-wide before A12 (kept, per Ajay).
HR_COMPANY_WIDE = frozenset({"leave.view", "leave.record", "overtime.view", "overtime.decide"})


class _AllBranches:
    """Every branch in the company (owner, company admin, company-wide HR)."""

    def __contains__(self, branch_id):
        return True

    def __bool__(self):
        return True

    def __repr__(self):
        return "ALL_BRANCHES"


ALL_BRANCHES = _AllBranches()


def _membership(user, company_id):
    if user is None or not getattr(user, "is_authenticated", False) or not user.is_active:
        return None
    return get_active_memberships(user).filter(company_id=company_id).first()


def _live(queryset, at):
    return queryset.filter(
        status=Override.Status.ACTIVE, effective_from__lte=at
    ).filter(Q(effective_to__isnull=True) | Q(effective_to__gt=at))


def branches_for(user, company_id, code, at=None):
    """``ALL_BRANCHES``, or the set of branch ids where ``user`` may do ``code``."""
    if code not in CODES:
        raise ValueError(f"Unknown branch permission: {code}")
    at = at or timezone.now()
    membership = _membership(user, company_id)
    if membership is None:
        return set()
    if membership.role in COMPANY_WIDE_ROLES:
        return ALL_BRANCHES
    if membership.role == Role.HR and code in HR_COMPANY_WIDE:
        return ALL_BRANCHES
    with use_company(company_id):
        branches = set()
        if membership.role == Role.MANAGER:
            # A manager runs their own branches: every branch permission there.
            branches = set(membership.allowed_branches.values_list("pk", flat=True))
        employee = Employee.objects.filter(user=user).first()
        if employee is not None:
            grants = _live(
                Override.objects.filter(employee=employee, permission__code=code), at
            ).prefetch_related("allowed_branches")
            for grant in grants:
                scope = {branch.pk for branch in grant.allowed_branches.all()}
                if grant.effect == Override.Effect.GRANT:
                    branches |= scope
                else:
                    branches = branches - scope if scope else set()
    return branches


def branches_for_any(user, company_id, *codes, at=None):
    """Branches where ``user`` may do at least one of ``codes``."""
    found = set()
    for code in codes:
        branches = branches_for(user, company_id, code, at)
        if branches is ALL_BRANCHES:
            return ALL_BRANCHES
        found |= branches
    return found


def can(user, company_id, code, branch_id=None, at=None):
    """True if ``user`` may do ``code`` in ``branch_id`` (or in any branch when None)."""
    branches = branches_for(user, company_id, code, at)
    if branches is ALL_BRANCHES:
        return True
    return bool(branches) if branch_id is None else branch_id in branches


def require(user, company_id, code, branch_id=None):
    if not can(user, company_id, code, branch_id):
        raise PermissionDenied("You do not have access to do this for that branch.")


def scope_queryset(queryset, user, company_id, code, field="branch"):
    """Limit a queryset to the branches where ``user`` may do ``code``."""
    branches = branches_for(user, company_id, code)
    if branches is ALL_BRANCHES:
        return queryset
    return queryset.filter(**{f"{field}__in": branches})


def _permission(code):
    """The catalogue row for a branch permission, created on first use."""
    permission = AccessPermission.objects.filter(code=code).first()
    if permission is not None:
        return permission
    _, label, feature_code, action = next(p for p in BRANCH_PERMISSIONS if p[0] == code)
    feature, _ = Feature.objects.get_or_create(
        code=feature_code, defaults={"name": feature_code.title()}
    )
    permission, _ = AccessPermission.objects.get_or_create(
        code=code,
        defaults={"name": label, "action": action, "feature": feature,
                  "is_sensitive": feature_code == "payroll"},
    )
    return permission


def _checked(actor, company_id, employee_id, code, branch_ids):
    """Shared validation for granting and removing. Call inside the tenant context."""
    membership = _membership(actor, company_id)
    if membership is None:
        raise PermissionDenied("Authentication is required.")
    if code not in CODES:
        raise ValidationError({"code": "Unknown permission."})
    branch_ids = {int(branch_id) for branch_id in branch_ids}
    if not branch_ids:
        raise ValidationError({"branches": "Choose at least one branch."})
    if set(Branch.objects.filter(pk__in=branch_ids).values_list("pk", flat=True)) != branch_ids:
        raise PermissionDenied("Branch not found in this company.")
    employee = Employee.objects.filter(pk=employee_id).first()
    if employee is None:
        raise PermissionDenied("Employee not found in this company.")
    if employee.user_id and employee.user_id == actor.pk:
        raise PermissionDenied("You cannot change your own access.")
    for branch_id in branch_ids:
        if not (can(actor, company_id, "access.grant", branch_id)
                and can(actor, company_id, code, branch_id)):
            raise PermissionDenied(
                "You can only give or remove access you hold, in branches where you may grant it."
            )
    return membership, employee, branch_ids, _permission(code)


def _current_grant(employee, permission, at):
    return _live(
        Override.objects.filter(employee=employee, permission=permission), at
    ).prefetch_related("allowed_branches").first()


def _new_grant(membership, actor, employee, permission, branch_ids, reason, at):
    grant = Override(
        company=membership.company, employee=employee, permission=permission,
        effect=Override.Effect.GRANT, effective_from=at, granted_by=actor,
        reason=reason, created_by=actor, updated_by=actor,
    )
    grant.full_clean()
    grant.save()
    grant.allowed_branches.set(branch_ids)
    return grant


def _end(override, actor, at):
    override.effective_to = at
    override.status = Override.Status.EXPIRED
    override.updated_by = actor
    override.save()


@transaction.atomic
def grant_access(*, actor, company_id, employee_id, code, branch_ids, reason=""):
    """Give ``employee`` permission ``code`` in ``branch_ids`` (added to what they have)."""
    with use_company(company_id):
        membership, employee, branch_ids, permission = _checked(
            actor, company_id, employee_id, code, branch_ids
        )
        at = timezone.now()
        current = _current_grant(employee, permission, at)
        before = sorted(b.pk for b in current.allowed_branches.all()) if current else []
        if current is not None and current.effect == Override.Effect.GRANT:
            current.allowed_branches.add(*branch_ids)
            grant = current
        else:
            if current is not None:
                _end(current, actor, at)
            grant = _new_grant(membership, actor, employee, permission, branch_ids, reason, at)
        after = sorted(b.pk for b in grant.allowed_branches.all())
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="access.granted", obj=grant,
            before={"employee_id": employee.pk, "code": code, "branches": before},
            after={"employee_id": employee.pk, "code": code, "branches": after, "reason": reason},
        )
    return grant


@transaction.atomic
def revoke_access(*, actor, company_id, employee_id, code, branch_ids, reason=""):
    """Remove ``code`` from ``employee`` in ``branch_ids``; other branches keep it."""
    with use_company(company_id):
        membership, employee, branch_ids, permission = _checked(
            actor, company_id, employee_id, code, branch_ids
        )
        at = timezone.now()
        current = _current_grant(employee, permission, at)
        held = {b.pk for b in current.allowed_branches.all()} if current else set()
        if current is None or current.effect != Override.Effect.GRANT or not held & branch_ids:
            raise ValidationError("This person has no such access to remove in those branches.")
        _end(current, actor, at)
        remaining = held - branch_ids
        if remaining:
            _new_grant(membership, actor, employee, permission, remaining, reason, at)
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="access.revoked", obj=current,
            before={"employee_id": employee.pk, "code": code, "branches": sorted(held)},
            after={"employee_id": employee.pk, "code": code, "branches": sorted(remaining),
                   "reason": reason},
        )
    return sorted(remaining)
