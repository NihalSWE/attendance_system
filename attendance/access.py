"""Who may see and fix attendance, and in which branches (A12 part 7, N10).

Follows docs/A12_BRANCH_ACCESS_FOR_NIHAL.md:

- **Seeing** attendance (Daily list, Calendar, day panel): company logins —
  owner, admin, HR, payroll manager, auditor — see every branch, exactly as
  before. Only an Employee or Branch-manager login is limited, to the branches
  where it holds ``attendance.view`` (a branch manager holds it in their own
  branches automatically).
- **Fixing** a day (Days to review, Fix a day, Withdraw): ``attendance.fix`` in
  the day's branch. Owner, admin and HR hold it company-wide
  (``HR_COMPANY_WIDE``), which is what the old owner/admin/HR rule was.
- **The "Now" badge**: company logins as before; a branch login gets only the
  people placed in branches where it may view employees or attendance.

Pages call these, and so do the services behind every write, so a crafted
request is refused exactly as a page view would be.
"""

import datetime

from django.core.exceptions import PermissionDenied
from django.db.models import Q

from access_control.branch_access import ALL_BRANCHES, branches_for, branches_for_any, can
from attendance.models import AttendanceRecord
from common.middleware import SELF_SERVICE_ROLES
from common.tenant import use_company
from employees.models import EmployeeAssignment
from organization.services import require_company_membership

VIEW = "attendance.view"
FIX = "attendance.fix"


def is_limited(membership):
    """True for the logins A12 limits to branches: Employee and Branch manager."""
    return membership.role in SELF_SERVICE_ROLES


def view_branches(user, company_id):
    """``(membership, branches)`` for seeing attendance; refuses if none."""
    membership = require_company_membership(user, company_id)
    if not is_limited(membership):
        return membership, ALL_BRANCHES
    branches = branches_for(user, company_id, VIEW)
    if not branches:
        raise PermissionDenied("Viewing attendance requires access to it in a branch.")
    return membership, branches


def now_branches(user, company_id):
    """``(membership, branches)`` for the live "Now" badge."""
    membership = require_company_membership(user, company_id)
    if not is_limited(membership):
        return membership, ALL_BRANCHES
    branches = branches_for_any(user, company_id, "employees.view", VIEW)
    if not branches:
        raise PermissionDenied("You do not look after any branch.")
    return membership, branches


def fix_branches(user, company_id):
    """``(membership, branches)`` where ``user`` may fix attendance; refuses if none."""
    membership = require_company_membership(user, company_id)
    branches = branches_for(user, company_id, FIX)
    if not branches:
        raise PermissionDenied(
            "Fixing attendance needs owner, company administrator or HR access, "
            "or access to fix attendance in a branch."
        )
    return membership, branches


def in_branches(branch_id, branches):
    return branches is ALL_BRANCHES or (branch_id is not None and branch_id in branches)


def scope(queryset, branches, field="branch"):
    """Limit ``queryset`` to ``branches`` (no filter for every branch)."""
    if branches is ALL_BRANCHES:
        return queryset
    return queryset.filter(**{f"{field}__in": branches})


def _company_zone(company_id):
    import zoneinfo

    from tenants.models import Company

    name = Company.objects.filter(pk=company_id).values_list("timezone", flat=True).first()
    try:
        return zoneinfo.ZoneInfo(name or "UTC")
    except zoneinfo.ZoneInfoNotFoundError:
        return zoneinfo.ZoneInfo("UTC")


def day_branch(company_id, employee_id, work_date, company_tz=None):
    """The branch an employee-day belongs to.

    The day's record when there is one — it carries the placement the day was
    worked in. Otherwise the placement in force at midday that day, which is
    how attendance itself picks it (attendance.services._write_day). None when
    the person was not placed then.
    """
    with use_company(company_id):
        record = (
            AttendanceRecord.objects.filter(employee_id=employee_id, work_date=work_date)
            .values_list("branch_id", flat=True).first()
        )
        if record is not None:
            return record
        tz = company_tz or _company_zone(company_id)
        probe = datetime.datetime.combine(work_date, datetime.time(12), tzinfo=tz)
        return (
            EmployeeAssignment.objects.filter(
                employee_id=employee_id, effective_from__lte=probe,
            )
            .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=probe))
            .exclude(status=EmployeeAssignment.Status.CANCELLED)
            .order_by("-effective_from")
            .values_list("branch_id", flat=True).first()
        )


def may_fix_day(user, company_id, employee_id, work_date, company_tz=None):
    branch_id = day_branch(company_id, employee_id, work_date, company_tz)
    if branch_id is None:
        # Not placed that day: only someone who may fix every branch.
        return branches_for(user, company_id, FIX) is ALL_BRANCHES
    return can(user, company_id, FIX, branch_id)


def require_fix_day(user, company_id, employee_id, work_date, company_tz=None):
    """The actor's membership, if they may fix this employee-day; else refused."""
    membership = require_company_membership(user, company_id)
    if not may_fix_day(user, company_id, employee_id, work_date, company_tz):
        raise PermissionDenied("You cannot fix attendance for that branch.")
    return membership


def require_view_day(user, company_id, employee_id, work_date, company_tz=None):
    """The actor's membership, if they may see this employee-day; else refused."""
    membership, branches = view_branches(user, company_id)
    if branches is ALL_BRANCHES:
        return membership
    if not in_branches(day_branch(company_id, employee_id, work_date, company_tz), branches):
        raise PermissionDenied("That day is in a branch you do not look after.")
    return membership


def still_in_for(user, company_id, now=None):
    """The still-in-after-shift list for someone who may fix attendance (N11).

    Limited to the branches where they may fix it; empty for anyone else.
    """
    from attendance.live_status import still_in_after_shift
    from organization.access_services import people

    try:
        _membership, branches = fix_branches(user, company_id)
    except PermissionDenied:
        return []
    if branches is ALL_BRANCHES:
        return still_in_after_shift(company_id, now=now)
    with use_company(company_id):
        ids = list(people(branches).values_list("pk", flat=True))
    return still_in_after_shift(company_id, employee_ids=ids, now=now) if ids else []
