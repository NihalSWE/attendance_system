"""Card assignment changes verified on the SenseFace 2A pilot terminal."""

from django.conf import settings
from django.core.exceptions import ValidationError

from devices.adapters.zkteco_adms import parse_kv_row
from devices.models import BiometricDevice, DeviceEnrollment, DeviceMessage, DeviceOutboxCommand, DeviceSyncState, DeviceUser
from devices.services import commands, protocol, resolution

COMMAND_PREFIX = "terminal_card:"
RECOVERY_KEY = "terminal_card_recovery"
DEFAULT_FIELDS = {"disable": "0", "verify": "0", "starttime": "0", "endtime": "0", "password": ""}


class CardEnrollmentUnavailable(ValidationError):
    """A card update cannot be sent safely for this enrollment."""


def enabled(device, pin, *, include_card_only=True):
    allowed = pin in settings.TERMINAL_CARD_PILOT_USER_IDS or (
        include_card_only and (settings.TERMINAL_CARD_DEVICE_WIDE_ENABLED
                               or pin in settings.TERMINAL_CARD_ONLY_PILOT_USER_IDS)
    )
    return allowed and supported(device)


def supported(device):
    return (
        device.serial_number == settings.TERMINAL_CARD_PILOT_SERIAL
        and device.status == BiometricDevice.Status.ACTIVE
        and device.device_model.model_code == "senseface-2a"
        and device.firmware_version == "ZAM70-NF24HA-Ver3.0.15"
        and protocol.dialect(device) == protocol.PUSH3
    )


def _reported_identity(device, pin):
    user = DeviceUser.all_objects.filter(device=device, pin=pin, reported=True).first()
    raw = DeviceMessage.all_objects.filter(
        device=device, pk=user.user_message_id if user else None,
    ).values_list("raw_payload_text", flat=True).first() or ""
    for line in raw.splitlines():
        if not line.startswith("user "):
            continue
        fields = {key.lower(): value for key, value in parse_kv_row(line[5:]).items()}
        if fields.get("pin") == pin:
            break
    else:
        raise CardEnrollmentUnavailable("Refresh the terminal user list before changing this card")

    name, card = fields.get("name", ""), fields.get("cardno", "")
    group, role = fields.get("group", ""), fields.get("privilege", "")
    if (not (0 < len(name) <= 24) or any(char in name for char in "\t\r\n")
            or group != "1" or role != "0"
            or any(fields.get(key) != value for key, value in DEFAULT_FIELDS.items())
            or set(fields) - (DEFAULT_FIELDS.keys() | {"pin", "uid", "name", "cardno", "group", "privilege"})
            or (card and (not card.isascii() or not card.isdigit()))):
        raise CardEnrollmentUnavailable("Terminal user settings cannot be preserved by the current Card update")
    return name, card


def queue_change(*, enrollment, actor, blocked):
    """Called inside the policy transaction. Retain the original card for recovery."""
    device, pin = enrollment.device, enrollment.device_user_id
    state = DeviceSyncState.all_objects.select_for_update().get(pk=commands._sync_state(device).pk)
    data = dict(state.state_data or {})
    backup = dict(data.get(RECOVERY_KEY) or {})
    key = str(enrollment.pk)
    if not blocked and key not in backup:
        return []
    name, reported_card = _reported_identity(device, pin)
    if blocked:
        if not reported_card or int(reported_card) == 0:
            if key not in backup:
                raise CardEnrollmentUnavailable("Reader reported no card to block")
        else:
            backup[key] = reported_card
        data[RECOVERY_KEY] = backup
        state.state_data = data
        state.save(update_fields=["state_data", "updated_at"])
    card = "" if blocked else backup[key]
    body = commands.build_user_update(device_user_id=pin, name=name, card_number=card, privilege=0, group=1)
    entries, error = commands._queue_group(device=device, commands=[(f"{COMMAND_PREFIX}{pin}:{key}", body)],
                                          requested_by=actor)
    if not entries:
        raise ValidationError(f"{device.name}: {error}")
    return [entry["id"] for entry in entries]


def prepare_write(device, row, now):
    """Guard queued pilot commands and keep later user syncs from re-adding a blocked card."""
    special = row.key.startswith(COMMAND_PREFIX)
    if not enabled(device, row.device_user_id):
        return row.body, "Card pilot unavailable" if special else ""
    mapping = resolution.resolve_enrollment(device=device, device_user_id=row.device_user_id, at=now).enrollment
    if mapping is None or mapping.enrollment_status == DeviceEnrollment.EnrollmentStatus.REMOVED:
        return row.body, "Employee mapping changed" if special else ""
    key = str(mapping.pk)
    backup = DeviceSyncState.all_objects.filter(device=device).values_list("state_data", flat=True).first() or {}
    saved_card = (backup.get(RECOVERY_KEY) or {}).get(key)
    if not saved_card or (special and row.key != f"{COMMAND_PREFIX}{row.device_user_id}:{key}"):
        return row.body, "Employee mapping changed" if special else ""

    from devices.services.verification import Method, blocked_at

    blocked = Method.CARD in blocked_at(device.company_id, mapping.employee_id, now)
    parts = row.body.split("\t")
    if special:
        expected = "CardNo=" if blocked else f"CardNo={saved_card}"
        return row.body, "Card setting changed" if expected not in parts else ""
    if blocked:
        return "\t".join("CardNo=" if part.startswith("CardNo=") else part for part in parts), ""
    return row.body, ""


def latest_command(enrollment):
    return DeviceOutboxCommand.all_objects.filter(
        device_id=enrollment.device_id,
        key=f"{COMMAND_PREFIX}{enrollment.device_user_id}:{enrollment.pk}",
    ).order_by("-command_id").first()
