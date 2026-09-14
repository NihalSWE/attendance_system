"""Which devices count for attendance, and re-checking punches that did not.

Two administrator actions (plan step N4):

- **The company's device scope** — assigned devices, department devices,
  branch devices or company devices (DEVICE_ATTENDANCE_POLICY.md). It is the
  last link of the precedence chain: an employee-assignment override wins, then
  the employee's branch override, then this.

- **Re-check punches** for a date range. A punch is judged by the settings in
  force when it happened, so ticking a forgotten device grant today does not
  bring back last week's excluded scans on its own — and should not, silently.
  A re-check is the administrator saying so explicitly: every excluded punch in
  the range is judged again under the settings as they are *now*, the whole
  run is audited, and the days of any punch that now counts are rebuilt.

A re-check only ever looks at punches that do not count. A punch that already
counts is never re-judged, so tightening a rule today cannot quietly take away
a day somebody was already credited for. Days inside a finalised salary month
are left alone — their punches are not even re-judged.
"""

import datetime
from collections import Counter
from dataclasses import dataclass, field

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from auditlog.services import record_company_event
from common.choices import DeviceAttendanceScope
from common.tenant import use_company
from devices.models import PunchEvent
from devices.services import panel_access
from devices.services.processing import EXCLUDING_STATUSES, resolve_and_authorize
from employees.models import EmployeeAssignment
from organization.models import Branch
from scheduling.models import CompanyAttendanceSettings

Status = PunchEvent.AuthorizationStatus

#: Every status a re-check looks at: whatever keeps a punch out of attendance.
RECHECKABLE = frozenset(EXCLUDING_STATUSES) | {Status.POLICY_UNRESOLVED}

#: A year and a bit: enough for any correction, small enough to stay one
#: transaction.
MAX_RECHECK_DAYS = 366

#: What each scope means, in the words the page uses.
SCOPE_HELP = {
    DeviceAttendanceScope.ASSIGNED_DEVICES: (
        "A punch counts only on a device the employee has been given: their "
        "enrollment on it has “Authorised for assigned-devices mode” ticked."
    ),
    DeviceAttendanceScope.DEPARTMENT_DEVICES: (
        "Any device in the employee's branch that serves their department. A "
        "device linked to no department serves the whole branch."
    ),
    DeviceAttendanceScope.BRANCH_DEVICES: (
        "Any device in the employee's own branch, whichever departments it serves."
    ),
    DeviceAttendanceScope.COMPANY_DEVICES: (
        "Any device in the company, including another branch's."
    ),
}

#: Why a punch does not count, and what fixes it.
EXCLUSION_HELP = {
    Status.UNAUTHORIZED_DEVICE: (
        "Not one of the employee's assigned devices",
        "Tick “Authorised for assigned-devices mode” on their enrollment, "
        "or choose a wider rule above, then re-check.",
    ),
    Status.DEPARTMENT_MISMATCH: (
        "The device serves another department",
        "Link the device to their department, or choose branch or company "
        "devices above, then re-check.",
    ),
    Status.BRANCH_MISMATCH: (
        "The device is in another branch",
        "Choose company devices above if other branches' devices should count, "
        "then re-check.",
    ),
    Status.ENROLLMENT_DISABLED: (
        "Attendance is switched off on the enrollment",
        "Switch “Attendance enabled” back on for that enrollment, then re-check.",
    ),
    Status.UNKNOWN_EMPLOYEE: (
        "Nobody is enrolled with that number on that device",
        "Enroll the employee on the device with that user number, starting on "
        "or before the punch, then re-check.",
    ),
    Status.EXPIRED_ENROLLMENT: (
        "The enrollment was not in effect at the punch time",
        "Usually the scans came before the enrollment was created. If they were "
        "this person, set the enrollment's start date earlier, then re-check.",
    ),
    Status.POLICY_UNRESOLVED: (
        "The rules at the time could not be established",
        "Re-checking judges it by today's rules instead.",
    ),
}


@dataclass
class RecheckResult:
    start: datetime.date
    end: datetime.date
    checked: int = 0
    now_count: int = 0
    still_excluded: Counter = field(default_factory=Counter)
    skipped_locked: int = 0
    days_rebuilt: int = 0

    def still_excluded_rows(self):
        return [
            (EXCLUSION_HELP.get(status, (status, ""))[0], count)
            for status, count in self.still_excluded.most_common()
        ]


def _membership(actor, company_id):
    membership = panel_access.managing_membership(actor, company_id)
    if membership is None:
        raise PermissionDenied(panel_access.DENIED_MESSAGE)
    return membership


def _settings(company_id, *, lock=False):
    rows = CompanyAttendanceSettings.all_objects.filter(company_id=company_id)
    if lock:
        rows = rows.select_for_update()
    row = rows.first()
    if row is None:
        raise ValidationError(
            "This company has no attendance settings yet. Open Attendance "
            "settings under Shifts and save them once."
        )
    return row


def _zone(company):
    import zoneinfo

    try:
        return zoneinfo.ZoneInfo(company.timezone or "UTC")
    except zoneinfo.ZoneInfoNotFoundError:
        return zoneinfo.ZoneInfo("UTC")


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def company_scope(company_id):
    return _settings(company_id).device_attendance_scope


def overrides(company_id, *, now=None):
    """Where the company rule does not apply: branches and employees with their own."""
    now = now or timezone.now()
    with use_company(company_id):
        branches = list(
            Branch.objects.exclude(device_attendance_scope_override__isnull=True)
            .exclude(device_attendance_scope_override="")
            .order_by("name")
        )
        employees = (
            EmployeeAssignment.objects.exclude(status=EmployeeAssignment.Status.CANCELLED)
            .exclude(device_attendance_scope_override__isnull=True)
            .exclude(device_attendance_scope_override="")
            .filter(effective_from__lte=now)
            .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=now))
            .values("employee_id").distinct().count()
        )
    labels = dict(DeviceAttendanceScope.choices)
    return {
        "branches": [
            (branch, labels.get(branch.device_attendance_scope_override,
                                branch.device_attendance_scope_override))
            for branch in branches
        ],
        "employee_count": employees,
    }


def excluded_summary(company_id, *, start, end):
    """``[(status, label, what_to_do, count)]`` for punches that do not count."""
    company = _settings(company_id).company
    tz = _zone(company)
    since, until = _instants(start, end, tz)
    with use_company(company_id):
        counts = dict(
            PunchEvent.objects.filter(
                authorization_status__in=RECHECKABLE,
                punched_at_utc__gte=since, punched_at_utc__lt=until,
            )
            .values_list("authorization_status")
            .annotate(total=Count("pk"))
            .values_list("authorization_status", "total")
        )
    return [
        (status, *EXCLUSION_HELP[status], counts[status])
        for status in EXCLUSION_HELP
        if counts.get(status)
    ]


def _instants(start, end, tz):
    since = datetime.datetime.combine(start, datetime.time.min, tzinfo=tz)
    until = datetime.datetime.combine(
        end + datetime.timedelta(days=1), datetime.time.min, tzinfo=tz
    )
    return since, until


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------


@transaction.atomic
def set_company_scope(*, actor, company_id, scope):
    """Choose which devices count, company-wide. Audited; changes no punch.

    The audit record carries the field's previous value, which is what lets a
    punch that arrives late from an offline device still be judged by the rule
    that was in force when it was made (devices/services/policy_history.py).
    """
    membership = _membership(actor, company_id)
    if scope not in DeviceAttendanceScope.values:
        raise ValidationError({"scope": "Choose one of the four options."})

    settings = _settings(company_id, lock=True)
    before = {
        "device_attendance_scope": settings.device_attendance_scope,
        "settings_version": settings.settings_version,
    }
    if settings.device_attendance_scope == scope:
        return settings, False

    settings.device_attendance_scope = scope
    settings.settings_version += 1
    settings.updated_by = actor
    settings.save(update_fields=[
        "device_attendance_scope", "settings_version", "updated_by", "updated_at",
    ])
    record_company_event(
        actor=actor, membership=membership, company=membership.company,
        action="attendance_settings.device_scope_changed", obj=settings,
        before=before,
        after={
            "device_attendance_scope": scope,
            "settings_version": settings.settings_version,
        },
    )
    return settings, True


def validate_range(start, end):
    if start is None or end is None:
        raise ValidationError("Choose both dates.")
    if end < start:
        raise ValidationError({"end": "The end date is before the start date."})
    if (end - start).days + 1 > MAX_RECHECK_DAYS:
        raise ValidationError({
            "end": f"Re-check at most {MAX_RECHECK_DAYS} days at a time."
        })


def recheck_punches(*, actor, company_id, start, end, now=None):
    """Judge every excluded punch between two dates again, under today's rules.

    Identity is still resolved at each punch's own time — only the policy
    switches (the scope chain, the device grant, attendance enabled) are read
    as they are now. Returns a RecheckResult.
    """
    from attendance.services import _is_locked, locked_ranges, recalculate_for_punches

    membership = _membership(actor, company_id)
    validate_range(start, end)
    now = now or timezone.now()
    company = membership.company
    tz = _zone(company)
    since, until = _instants(start, end, tz)
    result = RecheckResult(start=start, end=end)
    locked = locked_ranges(company_id)
    note = {
        "rechecked_at": now.isoformat(),
        "rechecked_by": actor.pk,
        "judged_by": "the settings in force at the re-check",
    }

    before, after = {}, {}
    employee_days = set()
    with transaction.atomic(), use_company(company_id):
        punches = (
            PunchEvent.objects.select_for_update(of=("self",))
            .select_related("device")
            .filter(
                authorization_status__in=RECHECKABLE,
                punched_at_utc__gte=since, punched_at_utc__lt=until,
            )
            .order_by("punched_at_utc", "pk")
        )
        for punch in punches:
            day = punch.punched_at_utc.astimezone(tz).date()
            if _is_locked(day, locked):
                result.skipped_locked += 1
                continue
            previous = punch.authorization_status
            resolve_and_authorize(
                punch, policy_at=now, note={**note, "previous_status": previous}
            )
            result.checked += 1
            before[str(punch.pk)] = previous
            after[str(punch.pk)] = punch.authorization_status
            if punch.authorization_status == Status.AUTHORIZED:
                result.now_count += 1
                employee_days.add((punch.employee_id, day))
            else:
                result.still_excluded[punch.authorization_status] += 1

        record_company_event(
            actor=actor, membership=membership, company=company,
            action="punches.rechecked", obj=company,
            before={
                "range": [start.isoformat(), end.isoformat()],
                "punch_status": before,
            },
            after={
                "judged_by_settings_at": now.isoformat(),
                "punch_status": after,
                "checked": result.checked,
                "now_count": result.now_count,
                "skipped_in_finalised_salary_month": result.skipped_locked,
            },
        )

    # After the commit: the punches are settled whatever happens next, and
    # attendance rebuilding never raises into its caller.
    if employee_days:
        summary = recalculate_for_punches(company_id, employee_days)
        result.days_rebuilt = summary.get("days", 0)
    return result
