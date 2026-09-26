"""Who sees which report, and where: exactly what the page it reads from allows.

Attendance reports (daily, weekly, monthly, custom, absent, late, working
hours, entry logs) follow the Daily list; the Leave report the Leave list; the
Overtime report the Overtime page. So a branch manager sees their branches, a
department head their department, and the company every branch - with no new
permission to hand out.
"""

from django.core.exceptions import PermissionDenied

from access_control.branch_access import (
    ALL_BRANCHES,
    Scope,
    branches_for_any,
    headed_departments,
)
from attendance import access as attendance_access
from organization.services import require_company_membership

ATTENDANCE, LEAVE, OVERTIME = "attendance", "leave", "overtime"


def report_scope(user, company_id, kind):
    """``(membership, Scope)`` for a report of this ``kind``; refuses if none."""
    if kind == ATTENDANCE:
        return attendance_access.view_scope(user, company_id)
    membership = require_company_membership(user, company_id)
    if kind == LEAVE:
        from leaves.views import _list_scope  # the Leave list's own rule

        _company_wide, scope, _record = _list_scope(user, company_id)
        return membership, scope
    if kind == OVERTIME:
        from payroll.overtime import OVERTIME_ROLES  # the Overtime page's rule

        if membership.role in OVERTIME_ROLES:
            return membership, Scope(ALL_BRANCHES)
        branches = branches_for_any(user, company_id, "overtime.view", "overtime.decide")
        scope = Scope(branches, headed_departments(user, company_id))
        if not scope:
            raise PermissionDenied(
                "The overtime report needs owner, company administrator or HR access, "
                "or access to overtime in a branch.")
        return membership, scope
    raise ValueError(f"Unknown report kind: {kind}")


def may_see(user, company_id, kind):
    try:
        report_scope(user, company_id, kind)
    except PermissionDenied:
        return False
    return True


def report_kinds(user, company_id):
    """The kinds of report ``user`` may open - for the Reports menu."""
    return {kind for kind in (ATTENDANCE, LEAVE, OVERTIME) if may_see(user, company_id, kind)}
