"""Delete the device uploads nothing needs any more (2026-10-18).

A device re-sends its whole user table - names, fingerprints, faces, photos -
every time it is asked. A live device had sent it 4,644 times (195 MB); since
2026-10-18 the user list is kept as ``DeviceUser`` rows, so the old copies are
only weight: slower backups and copies, a bigger database.

An upload is kept when anything in it is still the newest of its kind:

- the newest user row of each number (newest values win);
- the latest 3.x user line of each number (for "removed from the device");
- the newest copy of each fingerprint or face template;
- the newest photo of each number;
- every part of the latest "Refresh user list" answer;
- anything with a line this module does not recognise, anything with no user,
  template or photo line at all, and anything punches point to.

Everything else is a full copy of something kept. ``prune`` deletes those for
one device inside a transaction, rebuilds the device's user list from what is
left, and compares it with the list before; any difference undoes the delete
(``PruneMismatch``). ``plan`` only counts.
"""

from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import Sum
from django.db.models.functions import Length

from devices.models import DeviceMessage, PunchEvent
from devices.services import device_users
from devices.services.device_roster import (
    BIODATA_PREFIXES,
    LEGACY_TEMPLATE_PREFIXES,
    NO_USER_ROWS,
    PHOTO_PREFIXES,
    USER_PREFIXES,
)

KNOWN = (*USER_PREFIXES, *BIODATA_PREFIXES, *PHOTO_PREFIXES,
         *(prefix for prefix, _kind in LEGACY_TEMPLATE_PREFIXES))
COMPARED = ("pin", "uid", "name", "card_number", "has_password", "group", "tz",
            "privilege_code", "disabled_on_device", "fingerprint_count", "face_count",
            "other_biometric_count", "has_photo", "only_in_scans", "removed_from_device",
            "is_mapped")


class PruneMismatch(Exception):
    """The user list would change without the uploads: nothing was deleted."""


@dataclass
class Plan:
    device: object
    uploads: int = 0
    kept: set = field(default_factory=set)
    deletable: list = field(default_factory=list)
    bytes: int = 0


def plan(device):
    """Which of the device's uploads are still needed, and which are copies."""
    from devices.services.device_users import _parse

    result = Plan(device=device)
    newest_user, latest_seen, newest_marker, newest_photo = {}, {}, {}, {}
    answers, last_answer = {}, (None, None)
    keep = set()
    punched = set(PunchEvent.all_objects.filter(device=device)
                  .values_list("device_message_id", flat=True).distinct())
    messages = (DeviceMessage.all_objects.filter(device=device)
                .exclude(message_type__in=NO_USER_ROWS).order_by("pk")
                .values_list("pk", "received_at", "payload_json", "raw_payload_text")
                .iterator(chunk_size=device_users.CHUNK))
    for pk, received_at, payload_json, text in messages:
        result.uploads += 1
        lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
        if pk in punched or not lines or any(not line.startswith(KNOWN) for line in lines):
            keep.add(pk)
            continue
        users, markers, photos, listed = _parse(text)
        for pin, _values in users:
            newest_user[pin] = pk
        for pin in listed:
            if pin not in latest_seen or received_at >= latest_seen[pin][0]:
                latest_seen[pin] = (received_at, pk)
        for pin, marker in markers:
            newest_marker[(pin, marker)] = pk
        for pin in photos:
            newest_photo[pin] = pk
        query = (payload_json or {}).get("query", {}) or {}
        cmdid = str(device_users._first(query.get("cmdid")))
        if cmdid and device_users._first(query.get("tablename")) == "user" and listed:
            answers.setdefault(cmdid, set()).add(pk)
            if last_answer[1] is None or received_at >= last_answer[1]:
                last_answer = (cmdid, received_at)
    keep |= set(newest_user.values()) | {pk for _at, pk in latest_seen.values()}
    keep |= set(newest_marker.values()) | set(newest_photo.values())
    if last_answer[0]:
        keep |= answers[last_answer[0]]
    result.kept = keep
    result.deletable = sorted(
        DeviceMessage.all_objects.filter(device=device).exclude(message_type__in=NO_USER_ROWS)
        .exclude(pk__in=keep).values_list("pk", flat=True))
    if result.deletable:
        result.bytes = (DeviceMessage.all_objects.filter(pk__in=result.deletable)
                        .aggregate(size=Sum(Length("raw_payload_text")))["size"] or 0)
    return result


def _roster(device):
    from common.tenant import use_company
    from devices.services.device_roster import build_roster

    with use_company(device.company_id):
        return sorted(tuple(row[key] for key in COMPARED) for row in build_roster(device))


def prune(device, *, apply=True):
    """Delete the copies for one device, if the user list stays exactly as it is.

    ``apply=False`` does the same work and undoes it: a check that changes
    nothing. Returns the ``Plan``; raises ``PruneMismatch`` when the list would
    change (nothing deleted).
    """
    result = plan(device)
    if not result.deletable:
        return result
    before = _roster(device)
    with transaction.atomic():
        for start in range(0, len(result.deletable), 500):
            DeviceMessage.all_objects.filter(pk__in=result.deletable[start:start + 500]).delete()
        device_users.rebuild(device)
        after = _roster(device)
        if after != before:
            raise PruneMismatch(f"Device {device.pk}: the user list would change; nothing deleted.")
        if not apply:
            transaction.set_rollback(True)     # a check: the uploads and rows stay
    return result
