"""Capture an encrypted baseline for a single biometric hardware pilot."""

import json
import re
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from devices.adapters.zkteco_adms import parse_kv_row
from devices.models import DeviceEnrollment, DeviceOutboxCommand, DeviceSyncState, DeviceUserTemplate
from devices.services import commands, protocol, resolution, templates, terminal_card

QUERY_PREFIX = "pilot_bio_read:"
QUERY_WINDOW = timedelta(minutes=2)
BACKUP_UPLOAD_GRACE = timedelta(seconds=20)
BACKUP_REQUEST_PATTERN = r"^pilot_bio_read:[0-9]+:1:[0-9]+$"
FINGERPRINT_PREFIX = "terminal_fingerprint:"
RECOVERY_KEY = "terminal_fingerprint_recovery"


class FingerprintEnrollmentUnavailable(ValidationError):
    """This enrollment cannot safely be changed at the reader."""


class FingerprintBackupMissing(FingerprintEnrollmentUnavailable):
    """The reader acknowledged the query but HRM has yet to receive its data."""


def query_allowed(device, key):
    parts = key.removeprefix(QUERY_PREFIX).split(":")
    if len(parts) < 2:
        return False
    pin, method = parts[:2]
    if len(parts) == 3 and method == "1":
        return fingerprint_enabled(device, pin)
    return len(parts) == 2 and method in ("1", "9") and terminal_card.enabled(device, pin, include_card_only=False)


def capture_reply(device, command_id, body):
    """Retain only the requested user and biometric type from a pilot reply.

    Some firmware ignores query filters. A pilot reply bypasses ordinary raw
    message capture so neither the requested template nor anybody else's
    template is written to the message log.
    """
    if not command_id or not command_id.isascii() or not command_id.isdigit() or len(command_id) > 10:
        return None
    command = DeviceOutboxCommand.all_objects.filter(
        device=device, command_id=int(command_id), key__startswith=QUERY_PREFIX, sent_at__isnull=False,
    ).values_list("key", "sent_at").first()
    if not command:
        return None
    key, sent_at = command
    if not query_allowed(device, key) or timezone.now() - sent_at > QUERY_WINDOW:
        return "biodata=0"
    pin, method_type, *_ = key.removeprefix(QUERY_PREFIX).split(":")

    count = 0
    selected = []
    for line in body.splitlines():
        prefix, separator, values = line.strip().partition(" ")
        if not separator or prefix.lower() != "biodata":
            continue
        count += 1
        fields = {key.lower(): value for key, value in parse_kv_row(values).items()}
        if (fields.get("pin") == pin and fields.get("type") == method_type
                and (fields.get("tmp") or "").isascii()):
            selected.append(f"biodata {values}")
    if selected:
        templates.save_from_payload(device, "\n".join(selected), refresh_capture=True)
    return f"biodata={count}"


def fingerprint_enabled(device, pin):
    if device.terminal_fingerprint_control is not None:
        return device_wide_enabled(device)
    return settings.TERMINAL_FINGERPRINT_PILOT_ENABLED and (
        device_wide_enabled(device)
        or terminal_card.enabled(device, pin, include_card_only=False)
    )


def device_wide_enabled(device):
    if device.terminal_fingerprint_control is not None:
        return device.terminal_fingerprint_control and terminal_card.compatible(device)
    return (settings.TERMINAL_FINGERPRINT_PILOT_ENABLED
            and settings.TERMINAL_FINGERPRINT_DEVICE_WIDE_ENABLED
            and terminal_card.supported(device))


def _fingerprint_backups(device, pin, query=None):
    query = query or DeviceOutboxCommand.all_objects.filter(
        device=device, key=f"{QUERY_PREFIX}{pin}:1", status=DeviceOutboxCommand.Status.DONE,
        sent_at__isnull=False,
    ).order_by("-command_id").first()
    rows = list(DeviceUserTemplate.all_objects.filter(
        device=device, device_model_id=device.device_model_id, device_user_id=pin, vendor_type="1",
        captured_at__gte=query.sent_at if query else timezone.now(),
    ).order_by("number", "index"))
    if not query or query.return_code != "1":
        raise FingerprintEnrollmentUnavailable(f"{device.name}: the terminal must return exactly one enrolled finger.")
    if not rows:
        raise FingerprintBackupMissing("Reader reported a fingerprint but no backup arrived")
    if len(rows) != 1:
        raise FingerprintEnrollmentUnavailable("Fingerprint backup contained multiple templates")

    payloads = []
    for row in rows:
        payload = templates.as_payload(row)
        if (row.valid != "1" or row.duress != "0" or row.index != "0" or row.number not in "0123456789"
                or len(row.number) != 1 or row.major_version != "13"
                or any(not payload[key].isascii() or not payload[key].isdigit()
                       for key in ("minor_version", "format"))
                or any(char in payload["template"] for char in "\t\r\n")):
            raise FingerprintEnrollmentUnavailable(f"{device.name}: the fingerprint differs from the tested template format.")
        payloads.append(payload)
    return payloads


def queue_fingerprint_change(*, enrollment, actor, blocked, backup_query=None):
    device, pin = enrollment.device, enrollment.device_user_id
    state = DeviceSyncState.all_objects.select_for_update().get(pk=commands._sync_state(device).pk)
    data = dict(state.state_data or {})
    recovery = dict(data.get(RECOVERY_KEY) or {})
    key = str(enrollment.pk)

    try:
        if blocked:
            if device_wide_enabled(device) and backup_query is None:
                clean_pin, error = commands._validated_user_id(pin)
                if error or not clean_pin.isascii() or clean_pin != pin:
                    raise FingerprintEnrollmentUnavailable("Invalid terminal user ID")
                entries, error = commands._queue_group(
                    device=device, requested_by=actor, commands=[
                        (f"{QUERY_PREFIX}{pin}:1:{key}",
                         f"DATA QUERY tablename=biodata,fielddesc=*,filter=Type=1\tPin={pin}")
                    ])
                if error:
                    raise FingerprintEnrollmentUnavailable(error)
                return [entry["id"] for entry in entries]
            payloads = _fingerprint_backups(device, pin, backup_query)
            recovery[key] = templates.encrypt(json.dumps(payloads)).decode("ascii")
            data[RECOVERY_KEY] = recovery
            state.state_data = data
            state.save(update_fields=["state_data", "updated_at"])
        elif key in recovery:
            if device_wide_enabled(device):
                previous = _fingerprint_writes(enrollment).order_by("-command_id").first()
                if previous is None or "\tValid=0\t" not in previous.description or not previous.sent_at:
                    return []
            payloads = json.loads(templates.decrypt(recovery[key].encode("ascii")))
        else:
            return []

        writes = []
        for payload in payloads:
            body = commands.build_template_update(
                device_user_id=pin, template={**payload, "valid": "0" if blocked else payload["valid"]},
                dialect=protocol.PUSH3,
            )
            suffix = f":backup:{backup_query.command_id}" if backup_query else ""
            writes.append((f"{FINGERPRINT_PREFIX}{pin}:{key}:{payload['no']}{suffix}", body))
        entries, error = commands._queue_group(device=device, commands=writes, requested_by=actor)
    except templates.TemplateKeyMissing as exc:
        raise FingerprintEnrollmentUnavailable(f"{device.name}: the encrypted fingerprint backup is unavailable.") from exc
    if not entries:
        raise FingerprintEnrollmentUnavailable(f"{device.name}: {error}")
    return [entry["id"] for entry in entries]


def _block_request_enrollment(device, request, now):
    from devices.services.verification import Method, current_policy

    pin, method, enrollment_id = request.key.removeprefix(QUERY_PREFIX).split(":")
    enrollment = resolution.resolve_enrollment(device=device, device_user_id=pin, at=now).enrollment
    if (method != "1" or enrollment is None or str(enrollment.pk) != enrollment_id
            or enrollment.enrollment_status == DeviceEnrollment.EnrollmentStatus.REMOVED):
        return None
    policy = current_policy(device.company_id, enrollment.employee_id, now).first()
    if policy and Method.FINGERPRINT in policy.blocked_methods and policy.effective_at <= request.queued_at:
        return enrollment
    return None


def backup_requests(device):
    return DeviceOutboxCommand.all_objects.filter(
        company_id=device.company_id, device=device, key__regex=BACKUP_REQUEST_PATTERN,
    )


def backup_in_flight(device, now):
    requests = backup_requests(device).filter(sent_at__gte=now - QUERY_WINDOW)
    if requests.filter(status=DeviceOutboxCommand.Status.SENT).exists():
        return True
    return any(
        not fingerprint_backup_write(request)
        for request in requests.filter(status=DeviceOutboxCommand.Status.DONE, return_code="1",
                                       answered_at__gte=now - BACKUP_UPLOAD_GRACE)
    )


@transaction.atomic
def finish_fingerprint_backups(device):
    """Turn acknowledged scoped reads into writes once, at the next reader poll."""
    if not device_wide_enabled(device):
        return
    now = timezone.now()
    requests = backup_requests(device).filter(
        status=DeviceOutboxCommand.Status.DONE, answered_at__gte=now - QUERY_WINDOW,
    ).order_by("command_id")
    if not requests.exists():
        return
    DeviceSyncState.all_objects.select_for_update().get(pk=commands._sync_state(device).pk)
    for request in requests.select_for_update():
        if fingerprint_backup_write(request):
            continue
        try:
            enrollment = _block_request_enrollment(device, request, now)
            if not enrollment or not fingerprint_enabled(device, request.device_user_id):
                raise FingerprintEnrollmentUnavailable("Fingerprint setting or terminal mapping changed")
            if not request.sent_at or now - request.sent_at > QUERY_WINDOW:
                raise FingerprintEnrollmentUnavailable("Fingerprint backup request expired")
            queue_fingerprint_change(enrollment=enrollment, actor=None, blocked=True, backup_query=request)
        except FingerprintEnrollmentUnavailable as exc:
            if isinstance(exc, FingerprintBackupMissing) and now - request.answered_at < BACKUP_UPLOAD_GRACE:
                continue
            request.status = DeviceOutboxCommand.Status.DROPPED
            request.return_code = exc.messages[0][:64]
            request.save(update_fields=["status", "return_code", "updated_at"])


def fingerprint_backup_write(request):
    pin, _, enrollment = request.key.removeprefix(QUERY_PREFIX).split(":")
    return DeviceOutboxCommand.all_objects.filter(
        company_id=request.company_id, device_id=request.device_id,
        key__startswith=f"{FINGERPRINT_PREFIX}{pin}:{enrollment}:",
        key__endswith=f":backup:{request.command_id}",
    ).first()


def is_fingerprint_backup_request(command):
    return bool(re.fullmatch(BACKUP_REQUEST_PATTERN, command.key))


def fingerprint_write_problem(device, row, body, now):
    special = row.key.startswith(FINGERPRINT_PREFIX)
    normal = row.key.startswith(f"push_template:{row.device_user_id}:1:")
    if not (special or normal):
        return ""
    if not fingerprint_enabled(device, row.device_user_id):
        return "Fingerprint pilot unavailable" if special else ""
    mapping = resolution.resolve_enrollment(device=device, device_user_id=row.device_user_id, at=now).enrollment
    if mapping is None or mapping.enrollment_status == DeviceEnrollment.EnrollmentStatus.REMOVED:
        return "Employee mapping changed" if special else ""
    state = DeviceSyncState.all_objects.filter(device=device).values_list("state_data", flat=True).first() or {}
    key = str(mapping.pk)
    if key not in (state.get(RECOVERY_KEY) or {}) or (
        special and not row.key.startswith(f"{FINGERPRINT_PREFIX}{row.device_user_id}:{key}:")
    ):
        return "Employee mapping changed" if special else ""

    from devices.services.verification import Method, blocked_at

    blocked = Method.FINGERPRINT in blocked_at(device.company_id, mapping.employee_id, now)
    if special:
        expected = "Valid=0" if blocked else "Valid=1"
        return "Fingerprint setting changed" if expected not in body.split("\t") else ""
    return "Fingerprint blocked for this pilot enrollment" if blocked else ""


def _fingerprint_writes(enrollment):
    return DeviceOutboxCommand.all_objects.filter(
        device_id=enrollment.device_id,
        key__startswith=f"{FINGERPRINT_PREFIX}{enrollment.device_user_id}:{enrollment.pk}:",
    )


def latest_fingerprint_command(enrollment):
    write = _fingerprint_writes(enrollment).order_by("-command_id").first()
    request = DeviceOutboxCommand.all_objects.filter(
        company_id=enrollment.company_id, device_id=enrollment.device_id,
        key=f"{QUERY_PREFIX}{enrollment.device_user_id}:1:{enrollment.pk}",
        command_id__gt=write.command_id if write else 0,
    ).order_by("-command_id").first()
    return request or write


def can_retry_fingerprint_block(enrollment, now):
    command = latest_fingerprint_command(enrollment)
    if not command:
        return True
    if is_fingerprint_backup_request(command):
        return (
            command.status in (DeviceOutboxCommand.Status.DROPPED, DeviceOutboxCommand.Status.REFUSED)
            or (command.sent_at and now - command.sent_at > QUERY_WINDOW)
        )
    # An issued block may already be active even if its receipt was lost.
    return not command.sent_at and command.status == DeviceOutboxCommand.Status.DROPPED
