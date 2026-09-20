"""Who may see and fix attendance, and where (A12 part 7 / N10, heads 2026-09-20).

Three kinds of reach, all resolved by ``access_control.branch_access``:

- **Company logins** — owner, admin, HR, payroll manager, auditor — see every
  branch, exactly as before.
- **A branch login** (Employee or Branch manager) reaches the branches where it
  holds ``attendance.view`` / ``attendance.fix``; a branch manager holds every
  branch permission in its own branches automatically.
- **A department head** reaches only the people placed in the department(s)
  they head. A department belongs to one branch, so this never crosses a
  branch, and it is narrower than the branch itself.

Everything here works on a ``Scope`` (branches plus departments) rather than a
bare branch set, so the two dimensions are applied together and in one place.
Pages call these, and so do the services behind every write, so a crafted
request is refused exactly as a page view would be.
"""

import datetime

from django.core.exceptions import PermissionDenied
from django.db.models import Q

from access_control.branch_access import (
    ALL_BRANCHES,
    Scope,
    can,
    scope_for,
)
from attendance.models import AttendanceRecord
from common.middleware import SELF_SERVICE_ROLES
from common.tenant import use_company
from employees.models import Employee, EmployeeAssignment
from organization.services import require_company_membership

VIEW = "attendance.view"
FIX = "attendance.fix"

#: How an attendance row reaches its department (the placement it was worked in).
DEPARTMENT_FIELD = "employee_assignment__department"


def is_limited(membership):
    """True for the logins A12 limits: Employee and Branch manager."""
    return membership.role in SELF_SERVICE_ROLES


def view_scope(user, company_id):
    """``(membership, Scope)`` for seeing attendance; refuses if it reaches nothing."""
    membership = require_company_membership(user, company_id)
    if not is_limited(membership):
        return membership, Scope(ALL_BRANCHES)
    scope = scope_for(user, company_id, VIEW)
    if not scope:
        raise PermissionDenied(
            "Viewing attendance requires access to it in a branch, or heading a department."
        )
    return membership, scope


def now_scope(user, company_id):
    """``(membership, Scope)`` for the live "Now" badge."""
    membership = require_company_membership(user, company_id)
    if not is_limited(membership):
        return membership, Scope(ALL_BRANCHES)
    people_scope = scope_for(user, company_id, "employees.view")
    attendance_scope = scope_for(user, company_id, VIEW)
    if people_scope.is_all or attendance_scope.is_all:
        return membership, Scope(ALL_BRANCHES)
    scope = Scope(
        set(people_scope.branches) | set(attendance_scope.branches),
        people_scope.departments | attendance_scope.departments,
    )
    if not scope:
        raise PermissionDenied("You do not look after any branch or department.")
    return membership, scope


def fix_scope(user, company_id):
    """``(membership, Scope)`` where ``user`` may fix attendance; refuses if none."""
    membership = require_company_membership(user, company_id)
    scope = scope_for(user, company_id, FIX)
    if not scope:
        raise PermissionDenied(
            "Fixing attendance needs owner, company administrator or HR access, "
            "access to fix attendance in a branch, or heading the department."
        )
    return membership, scope


def in_scope(branch_id, department_id, scope):
    """True when a row in this branch/department is inside ``scope``."""
    if scope.is_all:
        return True
    if branch_id is not None and branch_id in scope.branches:
        return True
    return department_id is not None and department_id in scope.departments


def scope(queryset, where, field="branch", department_field=DEPARTMENT_FIELD):
    """Limit ``queryset`` to ``where`` (a Scope): its branches or its departments."""
    branches, departments = (
        (where.branches, where.departments)
        if isinstance(where, Scope) else (where, set())
    )
    if branches is ALL_BRANCHES:
        return queryset
    matches = Q(**{f"{field}__in": branches})
    if departments and department_field:
        matches |= Q(**{f"{department_field}__in": departments})
    return queryset.filter(matches)


def _company_zone(company_id):
    import zoneinfo

    from tenants.models import Company

    name = Company.objects.filter(pk=company_id).values_list("timezone", flat=True).first()
    try:
        return zoneinfo.ZoneInfo(name or "UTC")
    except zoneinfo.ZoneInfoNotFoundError:
        return zoneinfo.ZoneInfo("UTC")


def day_place(company_id, employee_id, work_date, company_tz=None):
    """``(branch_id, department_id)`` an employee-day belongs to.

    The day's record when there is one — it carries the placement the day was
    worked in. Otherwise the placement in force at midday that day, which is
    how attendance itself picks it (attendance.services._write_day).
    ``(None, None)`` when the person was not placed then.
    """
    with use_company(company_id):
        row = (
            AttendanceRecord.objects.filter(employee_id=employee_id, work_date=work_date)
            .values_list("branch_id", "employee_assignment__department_id")
            .first()
        )
        if row is not None:
            return row
        tz = company_tz or _company_zone(company_id)
        probe = datetime.datetime.combine(work_date, datetime.time(12), tzinfo=tz)
        placement = (
            EmployeeAssignment.objects.filter(
                employee_id=employee_id, effective_from__lte=probe,
            )
            .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=probe))
            .exclude(status=EmployeeAssignment.Status.CANCELLED)
            .order_by("-effective_from")
            .values_list("branch_id", "department_id")
            .first()
        )
        return placement if placement is not None else (None, None)


def day_branch(company_id, employee_id, work_date, company_tz=None):
    """Just the branch of an employee-day (kept for callers that only need it)."""
    return day_place(company_id, employee_id, work_date, company_tz)[0]


def _is_own(user, company_id, employee_id):
    with use_company(company_id):
        return Employee.objects.filter(pk=employee_id, user=user).exists()


def may_fix_day(user, company_id, employee_id, work_date, company_tz=None):
    branch_id, department_id = day_place(company_id, employee_id, work_date, company_tz)
    if branch_id is None and department_id is None:
        # Not placed that day: only someone who may fix every branch.
        return scope_for(user, company_id, FIX).is_all
    if can(user, company_id, FIX, branch_id):
        return True
    # Reaching it only as the head of its department: a head never decides
    # their own day — that falls back to the branch manager or the company.
    if department_id is not None and can(user, company_id, FIX, None, department_id):
        return not _is_own(user, company_id, employee_id)
    return False


def require_fix_day(user, company_id, employee_id, work_date, company_tz=None):
    """The actor's membership, if they may fix this employee-day; else refused."""
    membership = require_company_membership(user, company_id)
    if not may_fix_day(user, company_id, employee_id, work_date, company_tz):
        raise PermissionDenied("You cannot fix attendance for that day.")
    return membership


def require_view_day(user, company_id, employee_id, work_date, company_tz=None):
    """The actor's membership, if they may see this employee-day; else refused."""
    membership, where = view_scope(user, company_id)
    if where.is_all:
        return membership
    branch_id, department_id = day_place(company_id, employee_id, work_date, company_tz)
    if not in_scope(branch_id, department_id, where):
        raise PermissionDenied("That day is outside what you look after.")
    return membership


def still_in_for(user, company_id, now=None):
    """The still-in-after-shift list for someone who may fix attendance (N11).

    Limited to what they may fix — their branches, or the department they head.
    """
    from attendance.live_status import still_in_after_shift
    from organization.access_services import people

    try:
        _membership, where = fix_scope(user, company_id)
    except PermissionDenied:
        return []
    if where.is_all:
        return still_in_after_shift(company_id, now=now)
    with use_company(company_id):
        ids = list(people(where).values_list("pk", flat=True))
    return still_in_after_shift(company_id, employee_ids=ids, now=now) if ids else []
