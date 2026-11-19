"""Effective-dated verification restrictions for one employee."""

from dataclasses import dataclass, field

from django.core.exceptions import PermissionDenied, ValidationError
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from auditlog.services import record_company_event
from devices.models import DeviceEnrollment, DeviceOutboxCommand, EmployeeVerificationPolicy, PunchEvent
from devices.services.panel_access import managing_membership, DENIED_MESSAGE
from employees.models import Employee

Method = PunchEvent.VerificationMethod
METHODS = (Method.FINGERPRINT, Method.FACE, Method.CARD)
BULK_UPDATE_SESSION_KEY = "latest_bulk_verification_updates"


@dataclass
class VerificationChanges:
    count: int = 0
    blocked: set[str] = field(default_factory=set)
    unblocked: set[str] = field(default_factory=set)
    card_updates: int = 0
    card_manual: bool = False
    fingerprint_updates: int = 0
    fingerprint_manual: bool = False
    terminal_updates: list[dict] = field(default_factory=list)


def terminal_update_status(command):
    if command is None:
        return "Status unavailable", "neutral"
    from devices.services import biometric_pilot

    statuses = DeviceOutboxCommand.Status
    if biometric_pilot.is_fingerprint_backup_request(command):
        if command.status == statuses.DROPPED:
            return "HRM only · " + command.return_code, "warning"
        if command.status == statuses.REFUSED or (
            command.status == statuses.DONE and command.return_code != "1"
        ):
            return "HRM only · reader did not return one enrolled finger", "warning"
        if command.sent_at and timezone.now() - command.sent_at > biometric_pilot.QUERY_WINDOW:
            return "HRM only · fingerprint backup expired", "warning"
        return "Waiting for fingerprint backup and terminal update", "warning"
    if command.status == statuses.DONE:
        if command.return_code == "0":
            return "Acknowledged · verify at the reader", "success"
        return "Unexpected response from the device", "warning"
    tone = "danger" if command.status in (statuses.REFUSED, statuses.DROPPED) else "warning"
    return command.get_status_display(), tone


def current_policy(company_id, employee_id, at):
    return EmployeeVerificationPolicy.all_objects.filter(
        company_id=company_id, employee_id=employee_id, effective_at__lte=at,
    ).order_by("-effective_at", "-pk")


def blocked_at(company_id, employee_id, at):
    return current_policy(company_id, employee_id, at).values_list("blocked_methods", flat=True).first() or []


def blocked_methods_by_employee(company_id, employee_ids, at):
    return dict(EmployeeVerificationPolicy.all_objects.filter(
        company_id=company_id, employee_id__in=employee_ids, effective_at__lte=at,
    ).order_by("employee_id", "-effective_at", "-pk").distinct("employee_id")
        .values_list("employee_id", "blocked_methods"))


def change(*, actor, employee, methods):
    """Set the blocked methods for one employee."""
    return _change(actor, employee.company_id, {employee.pk}, methods, "set")


def change_bulk(*, actor, company_id, employee_ids, methods, operation):
    """Add or remove selected restrictions for each employee."""
    if operation not in ("block", "unblock") or not methods:
        raise ValidationError("Choose an action and at least one verification method.")
    return _change(actor, company_id, set(employee_ids), methods, operation)


@transaction.atomic
def _change(actor, company_id, employee_ids, methods, operation):
    membership = managing_membership(actor, company_id)
    if membership is None:
        raise PermissionDenied(DENIED_MESSAGE)
    requested = set(methods)
    if requested.difference(METHODS):
        raise ValidationError("Choose fingerprint, face, or card.")
    employees = list(Employee.all_objects.select_for_update().filter(
        company_id=company_id, pk__in=employee_ids,
    ).order_by("pk").values_list("pk", flat=True))
    if len(employees) != len(employee_ids):
        raise ValidationError("At least one selected employee is unavailable. Select the employees again.")

    now = timezone.now()
    current = blocked_methods_by_employee(company_id, employees, now)
    if operation == "unblock" and not any(requested.intersection(methods) for methods in current.values()):
        raise ValidationError("None of the selected employees have the chosen methods blocked.")
    result = VerificationChanges()
    for employee_id in employees:
        previous = current.get(employee_id, [])
        next_methods = requested
        if operation == "block":
            next_methods = set(previous) | requested
        elif operation == "unblock":
            next_methods = set(previous) - requested
        blocked = [method.value for method in METHODS if method in next_methods]
        added = set(blocked).difference(previous)
        removed = set(previous).difference(blocked)
        changed = added | removed
        retry_fingerprint = (
            operation == "block" and Method.FINGERPRINT in requested
            and Method.FINGERPRINT in previous and settings.TERMINAL_FINGERPRINT_DEVICE_WIDE_ENABLED
        )
        if operation != "set":
            result.terminal_updates.extend(
                {"employee_id": employee_id, "method": method.value, "result": "unchanged"}
                for method in METHODS if method in requested and method not in changed
                and not (method == Method.FINGERPRINT and retry_fingerprint)
            )
        if retry_fingerprint:
            count, manual = _queue_terminal_method(
                company_id, employee_id, actor, now, Method.FINGERPRINT, True,
                result.terminal_updates, retry=True,
            )
            result.fingerprint_updates += count
            result.fingerprint_manual |= manual
        if not changed:
            continue
        policy = EmployeeVerificationPolicy.all_objects.create(
            company_id=company_id, employee_id=employee_id, effective_at=now, blocked_methods=blocked,
        )
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action="employee.verification_methods_changed", obj=policy,
                             before={"blocked_methods": previous}, after={"blocked_methods": blocked})
        result.count += 1
        result.blocked.update(added)
        result.unblocked.update(removed)
        if Method.CARD in changed:
            count, manual = _queue_terminal_method(company_id, employee_id, actor, now, Method.CARD,
                                                    Method.CARD in added, result.terminal_updates)
            result.card_updates += count
            result.card_manual |= manual
        if Method.FINGERPRINT in changed:
            count, manual = _queue_terminal_method(company_id, employee_id, actor, now, Method.FINGERPRINT,
                                                    Method.FINGERPRINT in added, result.terminal_updates)
            result.fingerprint_updates += count
            result.fingerprint_manual |= manual
        if operation != "set" and Method.FACE in changed:
            result.terminal_updates.append({"employee_id": employee_id, "method": Method.FACE.value,
                                            "result": "hrm_only",
                                            "reason": "The reader can still accept face scans. HRM excludes "
                                                      "them from attendance while Face is blocked."})
    return result


def _queue_terminal_method(company_id, employee_id, actor, now, method, blocked, updates, *, retry=False):
    from devices.services import biometric_pilot, terminal_card

    update = {"employee_id": employee_id, "method": method.value}
    if not settings.TERMINAL_CARD_PILOT_SERIAL or (
        method == Method.FINGERPRINT and not settings.TERMINAL_FINGERPRINT_PILOT_ENABLED
    ):
        updates.append({**update, "result": "hrm_only"})
        return 0, True
    if method == Method.FINGERPRINT:
        enabled, queue = biometric_pilot.fingerprint_enabled, biometric_pilot.queue_fingerprint_change
    else:
        enabled, queue = terminal_card.enabled, terminal_card.queue_change
    enrollments = list(DeviceEnrollment.all_objects.filter(
        company_id=company_id, employee_id=employee_id, effective_from__lte=now,
    ).filter(Q(effective_to__isnull=True) | Q(effective_to__gt=now)).exclude(
        enrollment_status=DeviceEnrollment.EnrollmentStatus.REMOVED,
    ).select_related("device__device_model").order_by("device_id"))
    if not enrollments:
        updates.append({**update, "result": "hrm_only", "reason": "No linked terminal enrollment"})
        return 0, True
    count = 0
    for enrollment in enrollments:
        target = {**update, "device_id": enrollment.device_id, "device_name": enrollment.device.name,
                  "user_id": enrollment.device_user_id}
        if not enabled(enrollment.device, enrollment.device_user_id):
            updates.append({**target, "result": "hrm_only", "reason": "Automatic updates are unavailable for this enrollment"})
            continue
        if retry and not biometric_pilot.can_retry_fingerprint_block(enrollment, now):
            command = biometric_pilot.latest_fingerprint_command(enrollment)
            updates.append({**target, "command_id": command.command_id})
            continue
        try:
            command_ids = queue(enrollment=enrollment, actor=actor, blocked=blocked)
        except (terminal_card.CardEnrollmentUnavailable, biometric_pilot.FingerprintEnrollmentUnavailable) as exc:
            updates.append({**target, "result": "hrm_only", "reason": exc.messages[0]})
            continue
        if command_ids:
            count += 1
            updates.extend({**target, "command_id": command_id} for command_id in command_ids)
        else:
            updates.append({**target, "result": "hrm_only", "reason": "No credential was saved by an earlier block"})
    return count, count != len(enrollments)
