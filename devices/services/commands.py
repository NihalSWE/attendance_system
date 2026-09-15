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
    "query_attlog": "Fetch attendance history (last 31 days)",
}

# The attendance push 2.x forms (devices/services/protocol.py ATT2). Each was
# sent to the SenseFace 3A (ZAM70-NF28VA-3.3.12, Push 3.1.2S announcing
# pushver 2.4.1) on 2026-09-15 and answered Return=0:
#
#   DATA QUERY USERINFO          every user re-sent as ``USER PIN=…`` lines in
#                                the operation log, with ``BIODATA`` rows
#   DATA QUERY USERINFO PIN=1    that one user
#   DATA QUERY ATTLOG StartTime=<local>\tEndTime=<local>
#                                the attendance records in the range re-sent as
#                                ATTLOG rows (ones we hold arrive as duplicates
#                                and are not counted twice)
#
# The 3.x table form above answers ``Return=-1004`` on the same device.
ATT2_COMMANDS = {
    "query_users": "DATA QUERY USERINFO",
}
HISTORY_DAYS = 31


def attlog_query(start, end):
    """The 2.x attendance-history request; times are the device's local time."""
    return (
        f"DATA QUERY ATTLOG StartTime={start:%Y-%m-%d %H:%M:%S}"
        f"\tEndTime={end:%Y-%m-%d %H:%M:%S}"
    )


def _device_now(device, now=None):
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        zone = ZoneInfo(device.timezone or "UTC")
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("UTC")
    return (now or timezone.now()).astimezone(zone).replace(tzinfo=None), zone


def command_body(device, command_key):
    """The body for ``command_key`` in the dialect this device speaks, or None."""
    from datetime import timedelta

    from devices.services import protocol

    dialect = protocol.dialect(device)
    if command_key not in protocol.SUPPORTED[dialect]:
        return None
    if dialect == protocol.ATT2:
        if command_key == "query_attlog":
            local_now, _ = _device_now(device)
            return attlog_query(local_now - timedelta(days=HISTORY_DAYS), local_now + timedelta(minutes=5))
        return ATT2_COMMANDS[command_key]
    return SAFE_COMMANDS[command_key]

# Settings we are willing to write back to the device. The key is what the UI
# may send; the value is (device option name, validator, help text). Anything
# not listed here cannot be set, so a crafted request can never reach the
# device's configuration.
#
# Deliberately excluded: the server address and the comm key. Getting either
# wrong makes the device unreachable, and recovering means physically walking
# to the terminal.
#
# The server address is now changeable from the software, but it must never be
# reachable through this table. ``queue_set_option`` writes whatever it is
# given straight to the device on the next poll, which is exactly the wrong
# shape for a setting that can strand the hardware. It goes through
# ``queue_server_address`` below instead, which is only called by
# ``devices.services.server_address`` after that module has proved the new
# address reaches this server. Adding a "server_address" key here would
# silently bypass that proof.
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
MUTATING_COMMAND_KEYS = {
    "set_option", "push_user", "delete_user", "set_server_address",
}

# The key used for the guarded server-address command. Named so a queued entry
# is recognisable in the pending list and in the device's own command result.
SERVER_ADDRESS_COMMAND_KEY = "set_server_address"

# Write-side field names for the device's ``user`` table, established by
# probing SenseFace 2A ZAM70-NF24HA-Ver3.0.15 directly. They are NOT the
# lowercase names the device uses when it uploads that same table, which is
# the trap here: a write using the upload spelling is accepted (Return=0) and
# silently creates a row with an empty id.
#
#   Pin      the user id, and the upsert key      (lowercase 'pin' is ignored)
#   Name     display name                         (lowercase 'name' is ignored)
#   CardNo   card number                          ('Card' is ignored)
#   Pri      privilege: 0 normal, 14 super admin
#   Grp      access group
#
# Verified: re-sending an existing Pin updates that row rather than adding a
# second one (row count stayed constant while the values changed).
USER_WRITE_FIELDS = ("Pin", "Name", "CardNo", "Pri", "Grp")

# DANGER, measured on real hardware: "DATA DELETE user uid=<n>" returns
# Return=0 and DELETES EVERY USER ON THE DEVICE, including their enrolled
# faces and fingerprints, which cannot be recovered from the server. The uid
# key is not recognised, and the device appears to treat the command as an
# unfiltered delete. Only ever delete by Pin, which was verified to remove
# exactly one user (row count 5 -> 4, the intended row gone, others intact).
FORBIDDEN_DELETE_KEYS = ("uid", "UID", "Uid")


def build_user_update(*, device_user_id, name="", card_number="", privilege=0, group=1):
    """Build the DATA UPDATE body that creates or updates one device user.

    Creating a user makes the device able to *identify* that number. It does
    not enrol a face or fingerprint: those are captured by the device's own
    sensor and never leave it, so a pushed user can only be recognised by card
    or password until someone enrols their biometrics at the terminal.
    """
    values = {
        "Pin": str(device_user_id),
        "Name": (name or "")[:24],
        "CardNo": str(card_number or ""),
        "Pri": str(int(privilege)),
        "Grp": str(int(group)),
    }
    body = "\t".join(f"{field}={values[field]}" for field in USER_WRITE_FIELDS)
    return f"DATA UPDATE user {body}"


def build_user_delete(*, device_user_id):
    """Build the DATA DELETE body that removes exactly one device user."""
    return f"DATA DELETE user Pin={device_user_id}"

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

    Returns the queued entry, or None when the key is not recognised or this
    device's dialect has no such request. Queuing the same command twice while
    one is still pending is a no-op: the device would answer both with
    identical data.
    """
    body = command_body(device, command_key)
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


def _validated_user_id(device_user_id):
    """Return (clean_id, error). Device user ids are digits on this firmware."""
    value = str(device_user_id or "").strip()
    if not value:
        return None, "A device user number is required."
    if not value.isdigit():
        return None, "The device user number must be digits only."
    if len(value) > 20:
        return None, "That device user number is too long."
    return value, ""


def _unverified_user_writes(device):
    """Refuse user writes a device's dialect has not been verified for.

    The write forms here were measured on the 3.x SenseFace 2A, where one
    wrong key deleted every user. Nothing has been written to a 2.x device
    yet, so none is sent to one until its own form is measured.
    """
    from devices.services import protocol

    if protocol.dialect(device) == protocol.ATT2:
        return (
            "Adding or removing users from the software is not verified on this "
            "device's protocol yet. Add or edit the user on the terminal; the user "
            "list here updates by itself."
        )
    return ""


def queue_user_push(*, device, device_user_id, name="", card_number="",
                    privilege=0, requested_by=None):
    """Queue creation/update of one user on the device.

    Writing the person's *record* is all this does. Their face or fingerprint
    still has to be enrolled at the terminal, because the sensor captures the
    template and we never hold it.
    """
    clean_id, error = _validated_user_id(device_user_id)
    if error:
        return None, error
    error = _unverified_user_writes(device)
    if error:
        return None, error
    if int(privilege) not in (0, 2, 6, 14):
        return None, "Unknown privilege level."

    return _queue_raw(
        device=device,
        key=f"push_user:{clean_id}",
        body=build_user_update(
            device_user_id=clean_id,
            name=name,
            card_number=card_number,
            privilege=privilege,
        ),
        requested_by=requested_by,
    )


def queue_user_delete(*, device, device_user_id, requested_by=None):
    """Queue removal of exactly one user from the device.

    Deleting a user destroys their enrolled face/fingerprint on the device,
    and those templates exist nowhere else — so this is irreversible without
    walking to the terminal and enrolling the person again.
    """
    clean_id, error = _validated_user_id(device_user_id)
    if error:
        return None, error
    error = _unverified_user_writes(device)
    if error:
        return None, error

    body = build_user_delete(device_user_id=clean_id)
    # Belt and braces: a uid-keyed delete wipes the whole device (see
    # FORBIDDEN_DELETE_KEYS), so refuse to emit one even if a future edit
    # changes how the body is built.
    for forbidden in FORBIDDEN_DELETE_KEYS:
        if f"{forbidden}=" in body:
            return None, "Refusing to send a delete that could clear the device."

    return _queue_raw(
        device=device,
        key=f"delete_user:{clean_id}",
        body=body,
        requested_by=requested_by,
    )


#: A 2.x device that was silent this long is asked for what it scanned meanwhile.
CATCH_UP_AFTER_MINUTES = 10


def note_poll(device, now=None):
    """Record a command poll; return the previous one (None if never recorded).

    Kept apart from ``last_seen_at``, which uploads stamp too: after a gap the
    device may upload before it polls, and that must not hide the gap.
    """
    from datetime import datetime

    now = now or timezone.now()
    with transaction.atomic():
        state = DeviceSyncState.all_objects.select_for_update().get(pk=_sync_state(device).pk)
        data = dict(state.state_data or {})
        previous = data.get("last_poll_at")
        data["last_poll_at"] = now.isoformat()
        state.state_data = data
        state.save(update_fields=["state_data", "updated_at"])
    return datetime.fromisoformat(previous) if previous else None


def catch_up_after_gap(device, previous_poll, now=None):
    """Ask a reconnecting 2.x device for the scans it made while unreachable.

    Measured on the SenseFace 3A on 2026-09-15: a scan made while the server
    was unreachable (15:35) was not sent when the device reconnected; it only
    arrived when asked for with ``DATA QUERY ATTLOG``. So on its first poll
    after a gap — or its first poll ever — the history since an hour before
    it went quiet is requested. Returns the queued entry or None.
    """
    from datetime import timedelta

    from devices.services import protocol

    now = now or timezone.now()
    if previous_poll is not None and now - previous_poll < timedelta(minutes=CATCH_UP_AFTER_MINUTES):
        return None
    if protocol.dialect(device) != protocol.ATT2:
        return None
    oldest = now - timedelta(days=HISTORY_DAYS)
    since = max(previous_poll - timedelta(hours=1), oldest) if previous_poll else oldest
    local_now, zone = _device_now(device, now)
    local_since = since.astimezone(zone).replace(tzinfo=None)
    entry, _ = _queue_raw(
        device=device, key="query_attlog",
        body=attlog_query(local_since, local_now + timedelta(minutes=5)),
    )
    return entry


def _queue_raw(*, device, key, body, requested_by=None):
    """Append one already-built command. Callers validate their own input."""
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
            "key": key,
            "body": body,
            "queued_at": timezone.now().isoformat(),
            "requested_by": getattr(requested_by, "email", "") or "",
        }
        # A later command for the same target supersedes an earlier one.
        pending = [e for e in pending if e.get("key") != key]
        pending.append(entry)
        data["pending_commands"] = pending
        data["last_command_id"] = next_id
        state.state_data = data
        state.version = (state.version or 0) + 1
        state.save(update_fields=["state_data", "version", "updated_at"])
        return entry, ""


def build_server_address_updates(address):
    """The SET OPTION bodies that repoint a device at ``address``.

    Two commands, not one, and this is measured rather than assumed. Sending
    ``SET OPTION IclockSvrIP=host\tIclockSvrPort=443`` to a SenseFace 2A
    (ZAM70-NF24HA-Ver3.0.15) answers ``Return=0`` and then reads back as

        IclockSvrIP=host\tIclockSvrPort=443 , IclockSvrPort=8081

    — the whole tab-separated string became the value of the first option and
    the port never changed. The same trap as the lowercase ``DATA UPDATE
    user`` field names above: this firmware answers 0 and quietly does the
    wrong thing. One option per command reads back correctly.

    The port goes first so the pair is never applied as "new host, old port".
    Both are handed over in the same getrequest reply, so the device applies
    them before it next tries to connect.
    """
    from devices.services.server_address import (
        SERVER_ADDRESS_OPTION,
        SERVER_PORT_OPTION,
    )

    return (
        f"SET OPTION {SERVER_PORT_OPTION}={address.port}",
        f"SET OPTION {SERVER_ADDRESS_OPTION}={address.host}",
    )


def queue_server_address(*, device, address, requested_by=None):
    """Queue the server-address change. Guarded caller only.

    Returns (host_entry, port_entry, error). Deliberately not reachable
    through ``WRITABLE_OPTIONS``: this is the one device write that can make
    the device unreachable, so the only caller is
    ``devices.services.server_address.request_change``, which has already
    proved the address reaches this server. Nothing here re-validates the
    address, because nothing here could — only the round trip can.

    If the host command cannot be queued the port command is withdrawn, so the
    device is never handed half an address.
    """
    port_body, host_body = build_server_address_updates(address)

    port_entry, error = _queue_raw(
        device=device,
        key=f"{SERVER_ADDRESS_COMMAND_KEY}:port",
        body=port_body,
        requested_by=requested_by,
    )
    if port_entry is None:
        return None, None, error

    host_entry, error = _queue_raw(
        device=device,
        key=SERVER_ADDRESS_COMMAND_KEY,
        body=host_body,
        requested_by=requested_by,
    )
    if host_entry is None:
        drop_pending_command(device, port_entry["id"])
        return None, None, error

    return host_entry, port_entry, ""


def drop_pending_command(device, command_id):
    """Withdraw a queued command that has not been handed over yet."""
    with transaction.atomic():
        state = DeviceSyncState.all_objects.select_for_update().get(
            pk=_sync_state(device).pk
        )
        data = dict(state.state_data or {})
        data["pending_commands"] = [
            entry for entry in (data.get("pending_commands") or [])
            if entry.get("id") != command_id
        ]
        state.state_data = data
        state.version = (state.version or 0) + 1
        state.save(update_fields=["state_data", "version", "updated_at"])



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

    # Handing the command over is step 3 of an address change: it is the only
    # moment we know the device has actually taken it.
    if any(
        str(e.get("key", "")).startswith(SERVER_ADDRESS_COMMAND_KEY)
        for e in pending
    ):
        from devices.services import server_address

        server_address.note_command_delivered(
            device=device, command_ids={e["id"] for e in pending}
        )

    return "\n".join(lines) + "\n", pending


def pending_summary(device):
    """Commands still waiting for the device's next poll, for the UI."""
    state = DeviceSyncState.all_objects.filter(device=device).first()
    if not state:
        return []
    return list((state.state_data or {}).get("pending_commands") or [])
