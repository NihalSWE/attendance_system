"""Deciding whether a resolved punch counts, under the policy in force then.

Order of decisions (DEVICE_ATTENDANCE_POLICY.md):

1. Recognition — handled in resolution.py.
2. ``attendance_enabled`` — a hard per-enrollment denial that wins under every
   scope, including company_devices.
3. Effective scope — the first non-null of the dated EmployeeAssignment
   override, then the *employee's assigned* Branch override, then the company
   default. A precedence chain, not an intersection.
4. The scope's device rule.

Every value read here is the value that applied at the punch instant. Where
that cannot be established the punch becomes ``policy_unresolved`` and waits
for review; today's broader permission is never silently applied.

The employee's assignment/home branch is used throughout — never the branch of
the device they happened to walk up to, which would let a permissive branch
authorize attendance for a restricted employee.
"""

from dataclasses import dataclass, field

from django.db.models import Q
from django.utils import timezone

from common.choices import DeviceAttendanceScope
from devices.models import DeviceDepartment, PunchEvent
from devices.services.policy_history import is_missing, value_at_or_unresolved
from employees.models import EmployeeAssignment
from organization.models import Branch
from scheduling.models import CompanyAttendanceSettings


@dataclass
class AuthorizationOutcome:
    status: str
    snapshot: dict = field(default_factory=dict)


def _assignment_at(*, employee_id, company_id, at):
    """The employment placement effective when the punch happened."""
    return (
        EmployeeAssignment.all_objects.select_related("branch", "department")
        .filter(
            company_id=company_id,
            employee_id=employee_id,
            effective_from__lte=at,
        )
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=at))
        .exclude(status=EmployeeAssignment.Status.CANCELLED)
        .order_by("-effective_from", "-pk")
        .first()
    )


def _effective_scope(*, assignment, company_id, at, snapshot):
    """Resolve the scope precedence chain as it stood at ``at``.

    Returns ``(scope, unresolved_reason)``; a reason means the chain could not
    be established and the punch must be reviewed.
    """
    employee_override, reason = value_at_or_unresolved(
        assignment, "device_attendance_scope_override", at
    )
    if is_missing(employee_override):
        return None, reason
    if employee_override:
        snapshot["winning_policy_level"] = "employee_assignment"
        snapshot["employee_scope_override"] = employee_override
        return employee_override, ""
    snapshot["employee_scope_override"] = None

    branch = assignment.branch
    branch_override, reason = value_at_or_unresolved(
        branch, "device_attendance_scope_override", at
    )
    if is_missing(branch_override):
        return None, reason
    if branch_override:
        snapshot["winning_policy_level"] = "branch"
        snapshot["branch_scope_override"] = branch_override
        return branch_override, ""
    snapshot["branch_scope_override"] = None

    settings_row = CompanyAttendanceSettings.all_objects.filter(
        company_id=company_id
    ).first()
    if settings_row is None:
        return None, (
            "the company has no attendance settings row, so no default device "
            "scope can be established"
        )
    company_scope, reason = value_at_or_unresolved(
        settings_row, "device_attendance_scope", at
    )
    if is_missing(company_scope):
        return None, reason

    snapshot["winning_policy_level"] = "company_default"
    snapshot["company_scope"] = company_scope
    return company_scope, ""


def _device_serves_department(*, device, department_id, at):
    """department_devices rule for one device at one instant.

    A device with no active mapping at that time is a shared branch device.
    Mappings are dated, so this is answered from the interval directly rather
    than reconstructed.
    """
    links = DeviceDepartment.all_objects.filter(
        company_id=device.company_id,
        device=device,
        effective_from__lte=at,
    ).filter(Q(effective_to__isnull=True) | Q(effective_to__gt=at))
    links = links.exclude(status=DeviceDepartment.Status.ENDED)

    linked_department_ids = set(links.values_list("department_id", flat=True))
    if not linked_department_ids:
        return True, "device had no active department mapping, so it served the branch"
    if department_id in linked_department_ids:
        return True, f"device was mapped to department {department_id}"
    return False, (
        f"device was mapped only to departments {sorted(linked_department_ids)}, "
        f"not the employee's department {department_id}"
    )


def evaluate(*, punch, enrollment):
    """Decide the authorization status for one resolved punch.

    ``punch`` supplies the device, the instant and the source branch;
    ``enrollment`` is the mapping resolved for that instant.
    """
    at = punch.punched_at_utc
    device = punch.device
    snapshot = {
        "evaluated_at": timezone.now().isoformat(),
        "event_time_utc": at.isoformat(),
        "device_id": device.pk,
        "source_branch_id": punch.branch_id,
        "enrollment_id": enrollment.pk,
        "employee_id": enrollment.employee_id,
    }

    # 2. The hard denial, checked before any scope is even resolved.
    attendance_enabled, reason = value_at_or_unresolved(
        enrollment, "attendance_enabled", at
    )
    if is_missing(attendance_enabled):
        snapshot["decision_reason"] = (
            f"could not establish historical attendance_enabled: {reason}"
        )
        return AuthorizationOutcome(
            PunchEvent.AuthorizationStatus.POLICY_UNRESOLVED, snapshot
        )
    snapshot["attendance_enabled"] = bool(attendance_enabled)
    if not attendance_enabled:
        snapshot["decision_reason"] = (
            "enrollment had attendance disabled at the event time; this denial "
            "applies under every scope"
        )
        return AuthorizationOutcome(
            PunchEvent.AuthorizationStatus.ENROLLMENT_DISABLED, snapshot
        )

    assignment = _assignment_at(
        employee_id=enrollment.employee_id, company_id=device.company_id, at=at
    )
    if assignment is None:
        snapshot["decision_reason"] = (
            "the employee had no active assignment at the event time, so their "
            "home branch and effective scope cannot be established"
        )
        return AuthorizationOutcome(
            PunchEvent.AuthorizationStatus.POLICY_UNRESOLVED, snapshot
        )

    snapshot["assignment_id"] = assignment.pk
    snapshot["employee_branch_id"] = assignment.branch_id
    snapshot["employee_department_id"] = assignment.department_id

    # 3. The precedence chain.
    scope, unresolved_reason = _effective_scope(
        assignment=assignment, company_id=device.company_id, at=at, snapshot=snapshot
    )
    if scope is None:
        snapshot["decision_reason"] = (
            f"could not establish the effective device scope: {unresolved_reason}"
        )
        return AuthorizationOutcome(
            PunchEvent.AuthorizationStatus.POLICY_UNRESOLVED, snapshot
        )
    snapshot["effective_scope"] = scope

    # 4. The scope's device rule.
    if scope == DeviceAttendanceScope.ASSIGNED_DEVICES:
        granted, reason = value_at_or_unresolved(
            enrollment, "assigned_device_authorized", at
        )
        if is_missing(granted):
            snapshot["decision_reason"] = (
                f"could not establish historical assigned_device_authorized: {reason}"
            )
            return AuthorizationOutcome(
                PunchEvent.AuthorizationStatus.POLICY_UNRESOLVED, snapshot
            )
        snapshot["assigned_device_authorized"] = bool(granted)
        if not granted:
            snapshot["decision_reason"] = (
                "assigned_devices scope requires an explicit device grant; this "
                "enrollment provided recognition only"
            )
            return AuthorizationOutcome(
                PunchEvent.AuthorizationStatus.UNAUTHORIZED_DEVICE, snapshot
            )
        snapshot["decision_reason"] = "device was explicitly granted to this employee"
        return AuthorizationOutcome(PunchEvent.AuthorizationStatus.AUTHORIZED, snapshot)

    if scope == DeviceAttendanceScope.DEPARTMENT_DEVICES:
        if device.branch_id != assignment.branch_id:
            snapshot["decision_reason"] = (
                f"device sits in branch {device.branch_id}, outside the "
                f"employee's assigned branch {assignment.branch_id}"
            )
            return AuthorizationOutcome(
                PunchEvent.AuthorizationStatus.BRANCH_MISMATCH, snapshot
            )
        serves, reason = _device_serves_department(
            device=device, department_id=assignment.department_id, at=at
        )
        snapshot["department_rule"] = reason
        if not serves:
            snapshot["decision_reason"] = reason
            return AuthorizationOutcome(
                PunchEvent.AuthorizationStatus.DEPARTMENT_MISMATCH, snapshot
            )
        snapshot["decision_reason"] = reason
        return AuthorizationOutcome(PunchEvent.AuthorizationStatus.AUTHORIZED, snapshot)

    if scope == DeviceAttendanceScope.BRANCH_DEVICES:
        if device.branch_id != assignment.branch_id:
            snapshot["decision_reason"] = (
                f"device sits in branch {device.branch_id}, outside the "
                f"employee's assigned branch {assignment.branch_id}"
            )
            return AuthorizationOutcome(
                PunchEvent.AuthorizationStatus.BRANCH_MISMATCH, snapshot
            )
        # Department mappings deliberately do not restrict this mode.
        snapshot["decision_reason"] = (
            "device is in the employee's assigned branch; department mappings "
            "do not restrict branch_devices"
        )
        return AuthorizationOutcome(PunchEvent.AuthorizationStatus.AUTHORIZED, snapshot)

    if scope == DeviceAttendanceScope.COMPANY_DEVICES:
        # Tenant isolation already guarantees same-company; another branch is
        # allowed here, and the source branch stays recorded on the punch.
        snapshot["decision_reason"] = (
            "company_devices scope allows any eligible device of the same "
            "company, including another branch"
        )
        return AuthorizationOutcome(PunchEvent.AuthorizationStatus.AUTHORIZED, snapshot)

    snapshot["decision_reason"] = f"unrecognised device scope {scope!r}"
    return AuthorizationOutcome(
        PunchEvent.AuthorizationStatus.POLICY_UNRESOLVED, snapshot
    )
