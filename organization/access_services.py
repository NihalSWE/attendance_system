"""Organisation → Access (A12 part 2): who has which access, in which branch.

Built on ``access_control.branch_access`` (part 1). This module only reads what
a person holds and turns a ticked grid into ``grant_access`` / ``revoke_access``
calls; every rule — who may give what, where, never to yourself — stays there.

Who opens the page: anyone who may give access somewhere (``access.grant``):
the owner and company admin (every branch) and a branch manager (their own
branches). They see the people currently placed in those branches.
"""

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import OuterRef, Subquery, Q
from django.utils import timezone

from access_control import branch_access as access
from accounts.models import CompanyMembership
from common.tenant import use_company
from employees.models import Employee, EmployeeAssignment
from organization.employee_login import ROLE_LABELS, login_for
from organization.models import Branch

Role = CompanyMembership.Role
Override = access.Override

ROLE_NOTES = {
    Role.OWNER: "Owner: every permission in every branch.",
    Role.COMPANY_ADMIN: "Company administrator: every permission in every branch.",
}
LOGIN_LABELS = {
    **ROLE_LABELS,
    Role.OWNER: "Owner",
    Role.COMPANY_ADMIN: "Company administrator",
    Role.HR: "HR",
}


def grant_branches(actor, company_id):
    """``ALL_BRANCHES`` or the ids of the branches where ``actor`` may give access."""
    branches = access.branches_for(actor, company_id, "access.grant")
    if not branches:
        raise PermissionDenied("You cannot give access to others.")
    return branches


def branch_choices(company_id, scope):
    """The branches the page shows, as Branch rows. Call inside the company.

    ``scope`` is an ``access.Scope`` or plain branches. A department head gets
    the branch their department sits in, and no other.
    """
    queryset = Branch.objects.order_by("name")
    branches, departments = as_scope(scope)
    if branches is access.ALL_BRANCHES:
        return list(queryset)
    matches = Q(pk__in=branches)
    if departments:
        matches |= Q(departments__in=departments)
    return list(queryset.filter(matches).distinct())


def _current_placement():
    return (
        EmployeeAssignment.objects.filter(employee=OuterRef("pk"), effective_to__isnull=True)
        .exclude(status__in=["cancelled", "draft"])
        .order_by("-effective_from", "-pk")
    )


def as_scope(value):
    """``(branches, departments)`` from a Scope, or from plain branches."""
    branches = getattr(value, "branches", value)
    return branches, set(getattr(value, "departments", ()) or ())


def people(scope):
    """Employees placed now in ``scope``. Call inside the company's context.

    ``scope`` is an ``access.Scope`` (branches plus the departments someone
    heads) or, as before, just branches. A department head sees the people
    placed in the department they head and nobody else.
    """
    placement = _current_placement()
    queryset = Employee.objects.select_related("user").annotate(
        table_code=Subquery(placement.values("employee_code")[:1]),
        table_branch=Subquery(placement.values("branch__name")[:1]),
        table_branch_id=Subquery(placement.values("branch_id")[:1]),
        table_department_id=Subquery(placement.values("department_id")[:1]),
    ).filter(table_branch_id__isnull=False)
    branches, departments = as_scope(scope)
    if branches is access.ALL_BRANCHES:
        return queryset
    matches = Q(table_branch_id__in=branches)
    if departments:
        matches |= Q(table_department_id__in=departments)
    return queryset.filter(matches)


def granted(employee):
    """``{code: {branch ids}}`` of the access given to ``employee`` by hand.

    Only live grants: access that comes with a role (owner, admin, branch
    manager, HR) is described separately, because it cannot be removed here.
    """
    result = {}
    for grant in access._live(
        Override.objects.filter(employee=employee, effect=Override.Effect.GRANT), timezone.now()
    ).select_related("permission").prefetch_related("allowed_branches"):
        code = grant.permission.code
        if code in access.CODES:
            result.setdefault(code, set()).update(b.pk for b in grant.allowed_branches.all())
    return result


def role_note(company_id, membership):
    """What a person may do because of their role, in words (or "")."""
    if membership is None or membership.status != CompanyMembership.Status.ACTIVE:
        return ""
    if membership.role in ROLE_NOTES:
        return ROLE_NOTES[membership.role]
    if membership.role == Role.MANAGER:
        with use_company(company_id):
            names = ", ".join(b.name for b in membership.allowed_branches.order_by("name"))
        return f"Branch manager: every permission in {names or 'no branch yet'}, automatically."
    if membership.role == Role.HR:
        return "HR: views and records leave and decides overtime in every branch."
    return ""


def person(actor, company_id, employee_id):
    """One person on the Access page, with what the actor may change for them."""
    branches = grant_branches(actor, company_id)
    with use_company(company_id):
        employee = people(branches).filter(pk=employee_id).first()
        if employee is None:
            raise PermissionDenied("This person is not in a branch where you can give access.")
        columns = branch_choices(company_id, branches)
    membership = login_for(company_id, employee)
    is_self = employee.user_id is not None and employee.user_id == actor.pk
    everything = bool(membership and membership.status == CompanyMembership.Status.ACTIVE
                      and membership.role in access.COMPANY_WIDE_ROLES)
    with use_company(company_id):
        held = granted(employee)
    # A cell can be changed where the actor may both give access and holds
    # that permission there (grant_access re-checks exactly this).
    editable = {
        code: {b.pk for b in columns if access.can(actor, company_id, code, b.pk)}
        for code in access.CODES
    }
    rows = [
        {
            "code": code,
            "label": label,
            "cells": [
                {
                    "branch": branch,
                    "name": f"grant__{code}__{branch.pk}",
                    "checked": branch.pk in held.get(code, set()),
                    "editable": branch.pk in editable[code] and not is_self and not everything,
                }
                for branch in columns
            ],
        }
        for code, label, *_ in access.BRANCH_PERMISSIONS
    ]
    return {
        "employee": employee,
        "membership": membership,
        "login_label": LOGIN_LABELS.get(membership.role, membership.get_role_display())
        if membership else "",
        "role_note": role_note(company_id, membership),
        "branches": columns,
        "rows": rows,
        "held": held,
        "is_self": is_self,
        "everything": everything,
        "can_give_login": (
            employee.user_id is None
            and access.can(actor, company_id, "employees.logins", employee.table_branch_id)
        ),
    }


@transaction.atomic
def save_person(actor, company_id, employee_id, ticked, reason=""):
    """Apply the grid: grant what was ticked, remove what was unticked.

    ``ticked`` is the set of ``(code, branch_id)`` pairs that came back ticked.
    Only cells the actor may change are compared, so a crafted post cannot
    touch another branch or a permission the actor does not hold.
    Returns ``(added, removed)`` as lists of ``(code, branch_id)``.
    """
    page = person(actor, company_id, employee_id)
    if page["is_self"]:
        raise PermissionDenied("You cannot change your own access.")
    if page["everything"]:
        raise PermissionDenied("The owner and company administrator already have every permission.")
    added, removed = [], []
    for row in page["rows"]:
        code = row["code"]
        editable = {cell["branch"].pk for cell in row["cells"] if cell["editable"]}
        have = page["held"].get(code, set()) & editable
        want = {branch for c, branch in ticked if c == code} & editable
        grant = sorted(want - have)
        remove = sorted(have - want)
        if grant:
            access.grant_access(actor=actor, company_id=company_id, employee_id=employee_id,
                                code=code, branch_ids=grant, reason=reason)
            added += [(code, b) for b in grant]
        if remove:
            access.revoke_access(actor=actor, company_id=company_id, employee_id=employee_id,
                                 code=code, branch_ids=remove, reason=reason)
            removed += [(code, b) for b in remove]
    return added, removed


def parse_ticked(post):
    """``{(code, branch_id)}`` from the grid's ``grant__<code>__<branch>`` boxes."""
    ticked = set()
    for key in post:
        parts = key.split("__")
        if len(parts) == 3 and parts[0] == "grant" and parts[1] in access.CODES and parts[2].isdigit():
            ticked.add((parts[1], int(parts[2])))
    return ticked
