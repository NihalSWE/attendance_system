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
#   Privilege  0 normal, 2 enroller, 6 admin, 14 super admin. Measured
#            2026-09-19 (office 2A, test user 99999): 'Pri=14' answered
#            Return=0 and changed nothing; 'Privilege=14' read back as
#            privilege=14 and opened the admin menu.
#   Grp      access group (not measured; the device keeps group=1)
#
# Verified: re-sending an existing Pin updates that row rather than adding a
# second one (row count stayed constant while the values changed), and keeps
# its fingerprint and door permission (2026-09-19).
USER_WRITE_FIELDS = ("Pin", "Name", "CardNo", "Privilege", "Grp")

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
        "Privilege": str(int(privilege)),
        "Grp": str(int(group)),
    }
    body = "\t".join(f"{field}={values[field]}" for field in USER_WRITE_FIELDS)
    return f"DATA UPDATE user {body}"


# An access-control device (DeviceType=acc, the SenseFace 2A) also needs a
# door permission, or it recognises the person and refuses them: "Invalid time
# period", rtlog event 23. Measured 2026-09-19 on the office 2A with test user
# 99999: this answered Return=0, the userauthorize row count went 3 -> 4 and
# the person was let through. Time zone 1 and door 1 are the device defaults
# the users enrolled at the terminal have. The device counts this table when
# asked but does not upload its rows.
def build_access_grant(*, device_user_id):
    """Build the DATA UPDATE body that lets one user through door 1, time zone 1."""
    return (
        f"DATA UPDATE userauthorize Pin={device_user_id}"
        "\tAuthorizeTimezoneId=1\tAuthorizeDoorId=1"
    )


def needs_access_grant(device):
    """True unless the device is known to be time-attendance only (DeviceType=att).

    An access-control device refuses a user without a door permission. What
    the device is comes from its registration, which it does not repeat while
    it stays registered — so a device the server has not heard register (e.g.
    after the database was rebuilt) is treated as access control: on a device
    without doors the permission is simply refused, harmlessly.
    """
    settings = device.settings or {}
    announced = settings.get("announced") or {}
    return (announced.get("device_type") or settings.get("device_type") or "").lower() != "att"


def build_user_delete(*, device_user_id):
    """Build the DATA DELETE body that removes exactly one device user."""
    return f"DATA DELETE user Pin={device_user_id}"


# Writing a fingerprint or face template (PushSDK 3.x ``biodata`` table). The
# field names follow the write spelling measured for ``user`` above (capital
# first letter), in the order the device uploads them.
#
# Measured 2026-09-19 on the office 2A (PushSDK 3.x): Nihal's fingerprint
# (Type 1, MajorVer 13), captured by this device, written to test user 99999
# answered Return=0 and the device then identified Nihal's finger as 99999.
# Ajay's face (Type 9, MajorVer 40, MinorVer 1), finger and card, written back
# after he removed himself on the terminal, were each recognised as him. An
# unmeasured type still goes only to TEST_USER_ID. User writes to a 2.x device
# (the 3A) are refused whatever the type, until measured on a 3A.
TEMPLATE_WRITE_FIELDS = (
    ("Pin", None), ("No", "no"), ("Index", "index"), ("Valid", "valid"),
    ("Duress", "duress"), ("Type", "type"), ("MajorVer", "major_version"),
    ("MinorVer", "minor_version"), ("Format", "format"), ("Tmp", "template"),
)
MEASURED_TEMPLATE_TYPES = {"1", "9"}
#: Where an unmeasured template type may still be written, to measure it.
TEST_USER_ID = "99999"


def build_template_update(*, device_user_id, template):
    """Build the DATA UPDATE body that writes one template for one device user."""
    values = {"Pin": str(device_user_id)}
    for field, key in TEMPLATE_WRITE_FIELDS[1:]:
        values[field] = str(template.get(key) or "")
    body = "\t".join(f"{field}={values[field]}" for field, _ in TEMPLATE_WRITE_FIELDS)
    return f"DATA UPDATE biodata {body}"

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

    # Writes go in the outbox, not the small refresh queue: a company's worth
    # of them must fit.
    entries, error = _queue_group(
        device=device,
        commands=[(
            f"push_user:{clean_id}",
            build_user_update(device_user_id=clean_id, name=name,
                              card_number=card_number, privilege=privilege),
        )],
        requested_by=requested_by,
    )
    return (entries[0] if entries else None), error


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

    entries, error = _queue_group(
        device=device,
        commands=[(f"delete_user:{clean_id}", body)],
        requested_by=requested_by,
    )
    return (entries[0] if entries else None), error


ROLE_PRIVILEGES = (0, 2, 6, 14)


def push_to_device(device, device_user_id, name="", card="", role=0,
                   finger_template=None, face_template=None, requested_by=None):
    """Write one person to a device: user record, card, fingerprint and face.

    The seam agreed with Nihal (2026-09-19): plain values only, never an
    employee, so the later employee-to-device-user mapping calls this with what
    it resolved. A template is the dict ``templates.as_payload`` returns (the
    device's own fields plus ``template`` and ``device_model_id``), or None.

    On an access-control device the door permission goes with the user
    record (``build_access_grant``); without it the device refuses them.
    Everything is queued together or not at all, user record first, so the
    device never gets a template for a user it does not have. Returns
    ``(entries, error)``.
    """
    clean_id, error = _validated_user_id(device_user_id)
    if error:
        return [], error
    error = _unverified_user_writes(device)
    if error:
        return [], error
    try:
        role = int(role)
    except (TypeError, ValueError):
        role = -1
    if role not in ROLE_PRIVILEGES:
        return [], "Unknown privilege level."
    card = str(card or "").strip()
    if card and not card.isdigit():
        return [], "The card number must be digits only."

    templates = [t for t in (finger_template, face_template) if t]
    unmeasured = [t for t in templates if str(t.get("type")) not in MEASURED_TEMPLATE_TYPES]
    if unmeasured and clean_id != TEST_USER_ID:
        return [], (
            "Writing this kind of template is not measured on this device model yet. "
            f"Until it is, it is written only to test user {TEST_USER_ID}."
        )
    for template in templates:
        if template.get("device_model_id") != device.device_model_id:
            return [], (
                "That template was captured on another device model; fingerprints "
                "and faces only transfer between devices of the same model."
            )
        if not str(template.get("template") or "").strip():
            return [], "A template is empty."

    commands = [(
        f"push_user:{clean_id}",
        build_user_update(device_user_id=clean_id, name=name, card_number=card,
                          privilege=role),
    )]
    if needs_access_grant(device):
        commands.append((f"push_access:{clean_id}", build_access_grant(device_user_id=clean_id)))
    for template in templates:
        commands.append((
            f"push_template:{clean_id}:{template.get('type')}:{template.get('no')}:"
            f"{template.get('index')}",
            build_template_update(device_user_id=clean_id, template=template),
        ))
    return _queue_group(device=device, commands=commands, requested_by=requested_by)


#: Writes one device may have waiting in its outbox: a whole company's people
#: (user, door, finger, face each) with room to spare.
OUTBOX_LIMIT = 5000


def _queue_group(*, device, commands, requested_by=None):
    """Queue several ``(key, body)`` writes in the outbox, in order, all or none.

    A write still waiting with the same key is replaced (marked not sent), so
    mapping someone twice does not send them twice.
    """
    from devices.models import DeviceOutboxCommand
    from devices.services import templates

    now = timezone.now()
    with transaction.atomic():
        state = DeviceSyncState.all_objects.select_for_update().get(pk=_sync_state(device).pk)
        outbox = DeviceOutboxCommand.all_objects.filter(
            device=device, status=DeviceOutboxCommand.Status.QUEUED
        )
        keys = [key for key, _ in commands]
        outbox.filter(key__in=keys).update(
            status=DeviceOutboxCommand.Status.DROPPED, return_code="replaced", answered_at=now,
            body="", body_encrypted=None,
        )
        if outbox.count() + len(commands) > OUTBOX_LIMIT:
            return [], (
                f"This device already has {OUTBOX_LIMIT} writes waiting. Let it catch up first."
            )
        data = dict(state.state_data or {})
        next_id = int(data.get("last_command_id") or 0)
        entries = []
        for key, body in commands:
            next_id += 1
            description = _describe(body)
            # A template is biometric data: it waits encrypted, like the
            # saved templates themselves.
            secret = description != body
            row = DeviceOutboxCommand.all_objects.create(
                company_id=device.company_id, device=device, command_id=next_id, key=key,
                device_user_id=key.split(":")[1] if ":" in key else "",
                description=description, body="" if secret else body,
                body_encrypted=templates.encrypt(body) if secret else None,
                requested_by=getattr(requested_by, "email", "") or "", queued_at=now,
            )
            entries.append({"id": row.command_id, "key": key, "body": description})
        data["last_command_id"] = next_id
        state.state_data = data
        state.version = (state.version or 0) + 1
        state.save(update_fields=["state_data", "version", "updated_at"])
    return entries, ""


#: A 2.x device silent this long (it polls every few seconds) is asked for
#: what it scanned meanwhile ...
CATCH_UP_AFTER_MINUTES = 2
#: ... and every device is asked once an hour anyway.
CATCH_UP_EVERY_MINUTES = 60


def note_poll(device, now=None):
    """Record a command poll; return ``(previous poll, last catch-up)``.

    Kept apart from ``last_seen_at``, which uploads stamp too: after a gap the
    device may upload before it polls, and that must not hide the gap.
    """
    from datetime import datetime

    def read(value):
        return datetime.fromisoformat(value) if value else None

    now = now or timezone.now()
    with transaction.atomic():
        state = DeviceSyncState.all_objects.select_for_update().get(pk=_sync_state(device).pk)
        data = dict(state.state_data or {})
        previous = data.get("last_poll_at")
        data["last_poll_at"] = now.isoformat()
        state.state_data = data
        state.save(update_fields=["state_data", "updated_at"])
    return read(previous), read(data.get("last_catch_up_at"))


def catch_up_after_gap(device, poll, now=None):
    """Ask a 2.x device for scans it has not handed over.

    Measured on the SenseFace 3A on 2026-09-15: a scan made while the server
    was unreachable (15:35) was not sent when the device reconnected; it only
    arrived when asked for with ``DATA QUERY ATTLOG``. Asked again, the same
    request returned nothing: the device sends only records it has not
    handed over yet, so asking is cheap and never repeats itself. It is asked
    on its first poll after a gap of a couple of minutes (or its first poll
    ever), and once an hour regardless. ``poll`` is what ``note_poll``
    returned. Returns the queued entry or None.
    """
    from datetime import timedelta

    from devices.services import protocol

    previous_poll, last_catch_up = poll
    now = now or timezone.now()
    gap = previous_poll is None or now - previous_poll >= timedelta(minutes=CATCH_UP_AFTER_MINUTES)
    due = last_catch_up is None or now - last_catch_up >= timedelta(minutes=CATCH_UP_EVERY_MINUTES)
    if not (gap or due) or protocol.dialect(device) != protocol.ATT2:
        return None
    local_now, _ = _device_now(device, now)
    entry, _ = _queue_raw(
        device=device, key="query_attlog",
        body=attlog_query(local_now - timedelta(days=HISTORY_DAYS), local_now + timedelta(minutes=5)),
    )
    with transaction.atomic():
        state = DeviceSyncState.all_objects.select_for_update().get(pk=_sync_state(device).pk)
        state.state_data = {**(state.state_data or {}), "last_catch_up_at": now.isoformat()}
        state.save(update_fields=["state_data", "updated_at"])
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



#: Commands handed over per check-in. A template write is 1-2 KB and the device
#: stores it before answering, so a reconnecting device gets a few at a time
#: rather than everything at once; the rest follow on its next check-ins.
COMMANDS_PER_POLL = 5
#: How many handed-over commands and answers are remembered for the screen.
REMEMBERED_COMMANDS = 50


def take_pending_commands(device):
    """Pop the next queued commands and format them for the getrequest reply.

    ZKTeco expects one command per line as ``C:<id>:<body>``. Commands are
    removed as they are handed over: the device acknowledges by acting, and a
    command left queued would be re-issued on every poll, 4 times a minute.
    At most COMMANDS_PER_POLL go per check-in, in the order they were queued,
    except a server-address change, whose port and host always go together.
    """
    lines, pending = _take_refresh_queue(device)
    # Writes waiting in the outbox fill what is left of this check-in; an
    # address change goes alone.
    if not any(str(e.get("key", "")).startswith(SERVER_ADDRESS_COMMAND_KEY) for e in pending):
        more_lines, more = _take_outbox(device, COMMANDS_PER_POLL - len(pending))
        lines += more_lines
        pending += more
    if not lines:
        return "", []
    return "\n".join(lines) + "\n", pending


def _take_refresh_queue(device):
    """The DeviceSyncState queue part of a check-in: ``(lines, entries)``."""
    state = _sync_state(device)
    data = dict(state.state_data or {})
    queued = list(data.get("pending_commands") or [])
    if not queued:
        return [], []

    if any(str(e.get("key", "")).startswith(SERVER_ADDRESS_COMMAND_KEY) for e in queued):
        pending, rest = queued, []
    else:
        pending, rest = queued[:COMMANDS_PER_POLL], queued[COMMANDS_PER_POLL:]

    # A template that cannot be decrypted (key missing or changed) is dropped
    # and noted, never allowed to stop the device getting its other commands:
    # this reply is the device's only channel back.
    from devices.services.templates import TemplateKeyMissing

    lines, sent = [], []
    for entry in pending:
        try:
            lines.append(f"C:{entry['id']}:{_full_body(entry)}")
            sent.append(entry)
        except TemplateKeyMissing:
            data["command_results"] = (list(data.get("command_results") or []) + [{
                "id": entry["id"], "key": entry.get("key", ""), "command": entry.get("body", ""),
                "return": "not sent: template key missing", "at": timezone.now().isoformat(),
            }])[-REMEMBERED_COMMANDS:]
    pending = sent
    data["pending_commands"] = rest
    # Kept so a returned result can be described in the UI after the fact;
    # without the encrypted template, which has now left.
    handed_at = timezone.now().isoformat()
    data["in_flight_commands"] = (
        list(data.get("in_flight_commands") or [])
        + [
            {**{k: v for k, v in entry.items() if k != "body_encrypted"}, "handed_at": handed_at}
            for entry in pending
        ]
    )[-REMEMBERED_COMMANDS:]
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

    return lines, pending


def _take_outbox(device, room):
    """Hand over up to ``room`` outbox writes, oldest first: ``(lines, entries)``.

    The body leaves the database as it is sent. A template that cannot be
    decrypted (key missing or changed) is marked not sent and skipped, never
    allowed to stop the device getting the rest.
    """
    from devices.models import DeviceOutboxCommand
    from devices.services import templates

    if room <= 0:
        return [], []
    now = timezone.now()
    lines, entries = [], []
    with transaction.atomic():
        rows = list(
            DeviceOutboxCommand.all_objects.select_for_update()
            .filter(device=device, status=DeviceOutboxCommand.Status.QUEUED)
            .order_by("command_id")[:room]
        )
        for row in rows:
            try:
                body = templates.decrypt(row.body_encrypted) if row.body_encrypted else row.body
            except templates.TemplateKeyMissing:
                row.status, row.return_code = DeviceOutboxCommand.Status.DROPPED, "template key missing"
                row.answered_at = now
            else:
                lines.append(f"C:{row.command_id}:{body}")
                entries.append({"id": row.command_id, "key": row.key, "body": row.description})
                row.status, row.sent_at = DeviceOutboxCommand.Status.SENT, now
            row.body, row.body_encrypted = "", None
            row.save(update_fields=["status", "sent_at", "answered_at", "return_code",
                                    "body", "body_encrypted", "updated_at"])
    return lines, entries


def _full_body(entry):
    if entry.get("body_encrypted"):
        from devices.services import templates

        return templates.decrypt(entry["body_encrypted"].encode("ascii"))
    return entry["body"]


def parse_results(raw_body):
    """``[(command_id, return_code, cmd)]`` from a devicecmd body.

    The device answers ``ID=<n>&Return=<code>&CMD=<verb>``, several per body
    when it ran several commands, one per line.
    """
    results = []
    for line in (raw_body or "").splitlines():
        fields = {}
        for chunk in line.split("&"):
            key, _, value = chunk.strip().partition("=")
            if key:
                fields[key.strip()] = value.strip()
        try:
            results.append((int(fields.get("ID", "")), fields.get("Return", ""), fields.get("CMD", "")))
        except ValueError:
            continue
    return results


def note_results(device, raw_body):
    """Remember each command's answer next to the command, for the screen."""
    from devices.models import DeviceOutboxCommand

    results = parse_results(raw_body)
    if not results:
        return []
    now = timezone.now().isoformat()
    with transaction.atomic():
        outbox = {
            row.command_id: row for row in DeviceOutboxCommand.all_objects.select_for_update()
            .filter(device=device, command_id__in=[r[0] for r in results])
        }
        for command_id, code, _ in results:
            row = outbox.get(command_id)
            if row is not None:
                row.status = (DeviceOutboxCommand.Status.DONE if str(code).isdigit()
                              else DeviceOutboxCommand.Status.REFUSED)
                row.return_code, row.answered_at = code, timezone.now()
                row.save(update_fields=["status", "return_code", "answered_at", "updated_at"])
        state = DeviceSyncState.all_objects.select_for_update().get(pk=_sync_state(device).pk)
        data = dict(state.state_data or {})
        sent = {e.get("id"): e for e in data.get("in_flight_commands") or []}
        remembered = list(data.get("command_results") or [])
        for command_id, code, verb in results:
            if command_id in outbox:
                continue
            entry = sent.get(command_id, {})
            remembered.append({
                "id": command_id, "key": entry.get("key", ""),
                # Template bodies are biometric data: keep only the command's head.
                "command": _describe(entry.get("body", "")) or verb,
                "return": code, "at": now,
            })
        data["command_results"] = remembered[-REMEMBERED_COMMANDS:]
        state.state_data = data
        state.save(update_fields=["state_data", "updated_at"])
    return results


def _describe(body):
    if "\tTmp=" in body:
        return body.split("\tTmp=", 1)[0] + "\tTmp=…"
    return body


def recent_results(device, limit=10):
    """The latest answers from both queues, newest first, for the screen."""
    from devices.models import DeviceOutboxCommand

    state = DeviceSyncState.all_objects.filter(device=device).first()
    results = list((state.state_data or {}).get("command_results") or []) if state else []
    finished = (
        DeviceOutboxCommand.all_objects.filter(device=device)
        .exclude(status__in=[DeviceOutboxCommand.Status.QUEUED, DeviceOutboxCommand.Status.SENT])
        .order_by("-command_id")[:limit]
    )
    results += [
        {"id": row.command_id, "key": row.key, "command": row.description,
         "return": row.return_code, "at": (row.answered_at or row.queued_at).isoformat()}
        for row in finished
    ]
    results.sort(key=lambda r: r["id"], reverse=True)
    # 0 or a row count is success; a negative code or "not sent" is not.
    return [{**r, "ok": str(r.get("return", "")).isdigit()} for r in results[:limit]]


def pending_summary(device, limit=20):
    """Commands still waiting for the device's next poll, for the UI."""
    from devices.models import DeviceOutboxCommand

    state = DeviceSyncState.all_objects.filter(device=device).first()
    pending = list((state.state_data or {}).get("pending_commands") or []) if state else []
    pending += [
        {"id": row.command_id, "key": row.key, "body": row.description}
        for row in DeviceOutboxCommand.all_objects.filter(
            device=device, status=DeviceOutboxCommand.Status.QUEUED
        ).order_by("command_id")[:limit]
    ]
    return pending[:limit]


def waiting_count(device):
    """How many commands and writes are still waiting for this device."""
    from devices.models import DeviceOutboxCommand

    state = DeviceSyncState.all_objects.filter(device=device).first()
    queued = len((state.state_data or {}).get("pending_commands") or []) if state else 0
    return queued + DeviceOutboxCommand.all_objects.filter(
        device=device, status=DeviceOutboxCommand.Status.QUEUED
    ).count()


#: Answers older than this are not counted as part of "the job on screen".
JOB_WINDOW_MINUTES = 60


def job_progress(device, now=None):
    """How a device's queued writes are going, for the progress card.

    "The job" is what is still waiting or in flight, plus what was answered
    within the last hour — enough to show a run through to its end without
    dragging in last week's commands.
    """
    from datetime import timedelta

    from devices.models import DeviceOutboxCommand

    now = now or timezone.now()
    rows = DeviceOutboxCommand.all_objects.filter(device=device)
    waiting = rows.filter(status=DeviceOutboxCommand.Status.QUEUED).count()
    sent = rows.filter(status=DeviceOutboxCommand.Status.SENT).count()
    since = now - timedelta(minutes=JOB_WINDOW_MINUTES)
    answered = rows.filter(answered_at__gte=since)
    done = answered.filter(status=DeviceOutboxCommand.Status.DONE).count()
    refused = answered.exclude(status=DeviceOutboxCommand.Status.DONE).count()
    total = waiting + sent + done + refused
    interval = int((device.settings or {}).get("push_interval_seconds") or 10)
    # The device takes COMMANDS_PER_POLL per check-in, one check-in per interval.
    seconds_left = 0 if not (waiting + sent) else int(
        ((waiting + sent) / COMMANDS_PER_POLL) * interval)
    people = sorted({row.device_user_id for row in answered.exclude(
        status=DeviceOutboxCommand.Status.DONE) if row.device_user_id})
    return {
        "running": bool(waiting or sent),
        "waiting": waiting, "sent": sent, "done": done, "refused": refused,
        "total": total,
        "percent": int(round(100 * (done + refused) / total)) if total else 0,
        "seconds_left": seconds_left,
        "refused_users": people[:20],
    }
