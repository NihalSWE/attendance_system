"""Server-to-device commands over the TA Push channel.

The device is always the initiator: it polls ``GET /iclock/getrequest`` and we
reply with any queued commands. This is the *only* way to ask a push-mode
device for something, and it is emphatically not polling — we never open a
connection to the device, never touch port 4370, and a device that is offline
simply collects its commands on its next poll
(DEVICE_INTEGRATION_HANDOFF.md section 1).

Queue storage is ``DeviceSyncState.state_data``, the field the dictionary
already reserves for "adapter-specific cursors" (MODEL_FIELD_DICTIONARY.md
section 29). A queued command is transient operational state, not evidence, so
it belongs there rather than in a new table — evidence of what a device
actually did stays in DeviceMessage/PunchEvent.

Only read-only queries are exposed here. Commands that mutate the device
(deleting users, clearing logs, rebooting, rewriting options) are deliberately
not implemented yet: they need an explicit operator confirmation path and an
audit trail before anyone can fire them from a web page.
"""

from django.db import transaction
from django.utils import timezone

from devices.models import DeviceSyncState

# Commands an administrator may trigger. The value is the ZKTeco command body;
# the key is what the UI sends, so an arbitrary string from a request can never
# become a command.
#
# Syntax note, established against the real hardware: an access-control
# (``DeviceType=acc``) device rejects the time-attendance form
# ``DATA QUERY USERINFO`` with ``Return=-629``. It accepts the table form
# below, answering ``Return=<row count>`` and re-uploading the table.
# Verified on SenseFace 2A ZAM70-NF24HA-Ver3.0.15 (query_users -> Return=4).
SAFE_COMMANDS = {
    # Re-send the whole user table (id, name, card, privilege).
    "query_users": "DATA QUERY tablename=user,fielddesc=*,filter=*",
    # Re-send the fingerprint/face template inventory.
    "query_biodata": "DATA QUERY tablename=biodata,fielddesc=*,filter=*",
    # Re-send the device's own options/capability block.
    "query_options": "DATA QUERY tablename=options,fielddesc=*,filter=*",
}

COMMAND_LABELS = {
    "query_users": "Refresh user list",
    "query_biodata": "Refresh biometric inventory",
    "query_options": "Refresh device settings",
}

# Settings we are willing to write back to the device. The key is what the UI
# may send; the value is (device option name, validator, help text). Anything
# not listed here cannot be set, so a crafted request can never reach the
# device's configuration.
#
# Deliberately excluded: network/server address and comm key. Getting those
# wrong makes the device unreachable, and recovering means physically walking
# to the terminal — so they stay a manual, on-device action.
WRITABLE_OPTIONS = {
    "push_interval_seconds": (
        "Delay",
        lambda v: 1 <= int(v) <= 3600,
        "How often the device contacts the server, in seconds.",
    ),
    "error_delay_seconds": (
        "ErrorDelay",
        lambda v: 1 <= int(v) <= 3600,
        "How long the device waits before retrying after a failure, in seconds.",
    ),
    "device_utc_offset_hours": (
        "TimeZone",
        lambda v: -12 <= int(v) <= 14,
        "The device's UTC offset in hours.",
    ),
    "realtime": (
        "Realtime",
        lambda v: int(v) in (0, 1),
        "1 sends each punch immediately; 0 batches them.",
    ),
    "lock_open_seconds": (
        "LockOn",
        lambda v: 0 <= int(v) <= 254,
        "How long the door relay stays open, in seconds.",
    ),
}

# Mutating commands are separated from queries so the UI can require an
# explicit confirmation and so an audit entry is always written for them.
MUTATING_COMMAND_KEYS = {"set_option"}

# A device that has been offline for a long time should not receive a pile of
# stale refresh requests the moment it reconnects.
MAX_PENDING = 10


def _sync_state(device):
    state, _ = DeviceSyncState.all_objects.get_or_create(
        device=device, defaults={"company_id": device.company_id}
    )
    return state


def queue_command(*, device, command_key, requested_by=None):
    """Queue one safe command for the device's next poll.

    Returns the queued entry, or None when the key is not recognised. Queuing
    the same command twice while one is still pending is a no-op: the device
    would answer both with identical data.
    """
    body = SAFE_COMMANDS.get(command_key)
    if body is None:
        return None

    with transaction.atomic():
        state = DeviceSyncState.all_objects.select_for_update().get(pk=_sync_state(device).pk)
        data = dict(state.state_data or {})
        pending = list(data.get("pending_commands") or [])

        if any(entry.get("key") == command_key for entry in pending):
            return None
        if len(pending) >= MAX_PENDING:
            return None

        # The id is echoed back by the device in its devicecmd result, which is
        # how a result is matched to the request that caused it.
        next_id = int(data.get("last_command_id") or 0) + 1
        entry = {
            "id": next_id,
            "key": command_key,
            "body": body,
            "queued_at": timezone.now().isoformat(),
            "requested_by": getattr(requested_by, "email", "") or "",
        }
        pending.append(entry)
        data["pending_commands"] = pending
        data["last_command_id"] = next_id
        state.state_data = data
        state.version = (state.version or 0) + 1
        state.save(update_fields=["state_data", "version", "updated_at"])
        return entry


def queue_set_option(*, device, option_key, value, requested_by=None):
    """Queue a device-configuration change.

    Returns (entry, error). The option must be on the allowlist and its value
    must pass that option's validator, so neither an unknown option name nor an
    out-of-range value can reach the device.

    The new value is also written to ``BiometricDevice.settings`` so our
    handshake keeps announcing the same figure — otherwise the next
    registration would quietly reset the device to the old one.
    """
    spec = WRITABLE_OPTIONS.get(option_key)
    if spec is None:
        return None, "That setting cannot be changed from here."
    option_name, validator, _ = spec

    try:
        if not validator(value):
            return None, f"{value!r} is out of range for this setting."
        clean_value = int(value)
    except (TypeError, ValueError):
        return None, f"{value!r} is not a whole number."

    with transaction.atomic():
        state = DeviceSyncState.all_objects.select_for_update().get(
            pk=_sync_state(device).pk
        )
        data = dict(state.state_data or {})
        pending = list(data.get("pending_commands") or [])
        if len(pending) >= MAX_PENDING:
            return None, "Too many commands are already queued for this device."

        next_id = int(data.get("last_command_id") or 0) + 1
        entry = {
            "id": next_id,
            "key": f"set_option:{option_key}",
            "body": f"SET OPTION {option_name}={clean_value}",
            "queued_at": timezone.now().isoformat(),
            "requested_by": getattr(requested_by, "email", "") or "",
        }
        # A later SET for the same option supersedes an earlier one.
        pending = [e for e in pending if e.get("key") != entry["key"]]
        pending.append(entry)
        data["pending_commands"] = pending
        data["last_command_id"] = next_id
        state.state_data = data
        state.version = (state.version or 0) + 1
        state.save(update_fields=["state_data", "version", "updated_at"])

        settings = dict(device.settings or {})
        settings[option_key] = clean_value
        type(device).all_objects.filter(pk=device.pk).update(settings=settings)

    return entry, ""


def take_pending_commands(device):
    """Pop every queued command and format it for the getrequest reply.

    ZKTeco expects one command per line as ``C:<id>:<body>``. Commands are
    removed as they are handed over: the device acknowledges by acting, and a
    command left queued would be re-issued on every poll, 4 times a minute.
    """
    state = _sync_state(device)
    data = dict(state.state_data or {})
    pending = list(data.get("pending_commands") or [])
    if not pending:
        return "", []

    lines = [f"C:{entry['id']}:{entry['body']}" for entry in pending]
    data["pending_commands"] = []
    # Kept so a returned result can be described in the UI after the fact.
    data["in_flight_commands"] = pending
    state.state_data = data
    state.version = (state.version or 0) + 1
    state.save(update_fields=["state_data", "version", "updated_at"])
    return "\n".join(lines) + "\n", pending


def pending_summary(device):
    """Commands still waiting for the device's next poll, for the UI."""
    state = DeviceSyncState.all_objects.filter(device=device).first()
    if not state:
        return []
    return list((state.state_data or {}).get("pending_commands") or [])
