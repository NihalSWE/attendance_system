"""The users each device holds, kept as rows (2026-10-18).

``device_roster.build_roster`` used to work the roster out on every page from
every upload the device ever sent. A device re-sends its whole user table -
names, fingerprints, faces, photos - so a busy one had 4,644 such uploads
(195 MB) and its Device users page took ten seconds, twice (the page and its
table). Now each upload is applied once, when it arrives (``record_message``,
from ingestion), to one ``DeviceUser`` row per number; the pages read the rows.

The rules are the ones ``device_roster`` applied to the whole history, so the
answer is the same:

- user fields: the newest upload wins (uploads applied in id order; an older
  one never overwrites a newer one's fields);
- templates: each (type, number, index) counted once, however often re-sent;
- photos: any photo row means "has a photo";
- removed from the device: a number in a 3.x user upload no later than the
  first part of the latest "Refresh user list" answer (``cmdid``), and not in
  that answer.

Nothing is lost if an upload is missed (a failure, uploads stored by an older
version of this code): ``sync`` applies every upload after the last one
applied, and every reader calls it first. ``rebuild`` starts a device again
from all of its uploads (``manage.py rebuild_device_users``).
"""

from django.db import transaction
from django.utils import timezone

from devices.adapters.zkteco_adms import parse_kv_row
from devices.models import DeviceMessage, DeviceUser, DeviceUserSync
from devices.services.device_roster import (
    BIO_TYPES,
    BIODATA_PREFIXES,
    LEGACY_TEMPLATE_PREFIXES,
    NO_USER_ROWS,
    PHOTO_PREFIXES,
    USER_PREFIXES,
)

#: Uploads read per round trip when catching up or rebuilding: a full user
#: table with photos is large, so a few at a time.
CHUNK = 20

USER_FIELDS = ("uid", "name", "card_number", "has_password", "group", "tz",
               "privilege_code", "disabled_on_device")


def _first(value):
    if isinstance(value, (list, tuple)):
        return value[0] if value else ""
    return value or ""


def _user_values(fields):
    """The device's user row, as ``device_roster`` read it."""
    return {
        "uid": (fields.get("uid") or "")[:32],
        "name": (fields.get("name") or "").strip()[:255],
        "card_number": (fields.get("cardno") or fields.get("card") or "").strip()[:64],
        "has_password": bool((fields.get("password") or fields.get("passwd") or "").strip()),
        "privilege_code": (fields.get("privilege") or fields.get("pri") or "").strip()[:16],
        "disabled_on_device": (fields.get("disable") or "0").strip() == "1",
        "group": (fields.get("grp") or fields.get("group") or "").strip()[:32],
        "tz": (fields.get("tz") or "").strip()[:64],
    }


def _fields(line, prefix):
    return {key.lower(): value for key, value in parse_kv_row(line[len(prefix):].strip()).items()}


def _parse(text):
    """What one upload says, line by line: user rows, template markers, photos,
    and the numbers in its 3.x ``user`` lines."""
    users, markers, photos, listed = [], [], set(), set()
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        prefix = next((p for p in USER_PREFIXES if line.startswith(p)), None)
        if prefix:
            fields = _fields(line, prefix)
            pin = (fields.get("pin") or "").strip()
            if pin:
                users.append((pin, _user_values(fields)))
                if prefix == "user ":
                    listed.add(pin)
            continue
        prefix = next((p for p in BIODATA_PREFIXES if line.startswith(p)), None)
        if prefix:
            fields = _fields(line, prefix)
            pin = (fields.get("pin") or "").strip()
            kind = BIO_TYPES.get((fields.get("type") or "").strip()) or "other"
            markers.append((pin, "|".join((kind, "bio", fields.get("type", ""),
                                           fields.get("no", ""), fields.get("index", "")))))
            continue
        legacy = next(((p, kind) for p, kind in LEGACY_TEMPLATE_PREFIXES if line.startswith(p)),
                      None)
        if legacy:
            fields = _fields(line, legacy[0])
            pin = (fields.get("pin") or "").strip()
            markers.append((pin, f"{legacy[1]}|legacy|{fields.get('fid', '')}"))
            continue
        prefix = next((p for p in PHOTO_PREFIXES if line.startswith(p)), None)
        if prefix:
            pin = (_fields(line, prefix).get("pin") or "").strip()
            if pin:
                photos.add(pin)
    return users, markers, photos, listed


def _count(row):
    """Templates by kind; each marker is ``kind|...`` and stored once."""
    kinds = [marker.split("|", 1)[0] for marker in row.bio_markers]
    row.fingerprint_count = kinds.count("fingerprint")
    row.face_count = kinds.count("face")
    row.other_biometric_count = len(kinds) - row.fingerprint_count - row.face_count


def _apply(device, sync, rows, message_id, received_at, payload_json, text):
    """One upload onto the device's rows (``rows``: pin -> DeviceUser, changed in place)."""
    users, markers, photos, listed = _parse(text)
    if not (users or markers or photos):
        return
    query = (payload_json or {}).get("query", {}) or {}
    cmdid = str(_first(query.get("cmdid")))[:64]
    answer = bool(cmdid) and _first(query.get("tablename")) == "user"
    if answer and listed:
        if cmdid == sync.list_cmdid:
            sync.list_at = min(sync.list_at, received_at) if sync.list_at else received_at
        elif sync.list_at is None or received_at >= sync.list_at:
            sync.list_cmdid, sync.list_at = cmdid, received_at

    def row_for(pin):
        row = rows.get(pin)
        if row is None:
            row = rows[pin] = DeviceUser(company_id=device.company_id, device=device, pin=pin)
            row._changed = True
        return row

    for pin, values in users:
        row = row_for(pin)
        if row.user_message_id is None or message_id >= row.user_message_id:
            for field, value in values.items():
                setattr(row, field, value)
            row.user_message_id = message_id
        row.reported = True
        row._changed = True
    for pin in listed:
        row = row_for(pin)
        if row.last_seen_at is None or received_at > row.last_seen_at:
            row.last_seen_at = received_at
        if answer and cmdid == sync.list_cmdid:
            row.listed_cmdid = cmdid
        row._changed = True
    for pin, marker in markers:
        if not pin:
            continue
        row = row_for(pin)
        if marker not in row.bio_markers:
            row.bio_markers = [*row.bio_markers, marker]
            _count(row)
            row._changed = True
    for pin in photos:
        row = row_for(pin)
        if not row.has_photo:
            row.has_photo = True
            row._changed = True


def _messages(device, after=0, also=None):
    """The device's uploads that may carry user rows, oldest first."""
    found = (DeviceMessage.all_objects.filter(device=device)
             .exclude(message_type__in=NO_USER_ROWS))
    if also is not None and also <= after:
        from django.db.models import Q

        found = found.filter(Q(pk__gt=after) | Q(pk=also))
    else:
        found = found.filter(pk__gt=after)
    return (found.order_by("pk")
            .values_list("pk", "received_at", "payload_json", "raw_payload_text")
            .iterator(chunk_size=CHUNK))


def _save(rows):
    changed = [row for row in rows.values() if getattr(row, "_changed", False)]
    new = [row for row in changed if row.pk is None]
    old = [row for row in changed if row.pk is not None]
    now = timezone.now()
    for row in changed:
        row.updated_at = now
    DeviceUser.all_objects.bulk_create(new, batch_size=500)
    DeviceUser.all_objects.bulk_update(
        old, ["reported", *USER_FIELDS, "fingerprint_count", "face_count",
              "other_biometric_count", "has_photo", "bio_markers", "user_message_id",
              "last_seen_at", "listed_cmdid", "updated_at"], batch_size=500)
    for row in changed:
        row._changed = False


def _locked_sync(device):
    DeviceUserSync.all_objects.get_or_create(
        device=device, defaults={"company_id": device.company_id})
    return DeviceUserSync.all_objects.select_for_update().get(device=device)


def sync(device, *, also=None):
    """Apply every upload after the last one applied (and ``also``, a message
    id that may have committed out of order). Cheap when nothing is new: one
    query on the device's newest message id."""
    newest = (DeviceMessage.all_objects.filter(device=device)
              .exclude(message_type__in=NO_USER_ROWS).order_by("-pk")
              .values_list("pk", flat=True).first()) or 0
    state = DeviceUserSync.all_objects.filter(device=device).first()
    if state is not None and state.rebuilt_at is not None and newest <= state.through_message_id \
            and also is None:
        return state
    if state is None or state.rebuilt_at is None:
        return rebuild(device)
    with transaction.atomic():
        state = _locked_sync(device)
        rows = {row.pin: row for row in DeviceUser.all_objects.filter(device=device)}
        through = state.through_message_id
        for message_id, received_at, payload_json, text in _messages(device, through, also):
            _apply(device, state, rows, message_id, received_at, payload_json, text)
            through = max(through, message_id)
        _save(rows)
        state.through_message_id = through
        state.save(update_fields=["through_message_id", "list_cmdid", "list_at", "updated_at"])
    return state


def record_message(device, message):
    """Ingestion: the upload just stored, applied now (with anything missed)."""
    if message.message_type in NO_USER_ROWS:
        return None
    return sync(device, also=message.pk)


def rebuild(device):
    """Work a device's rows out again from all of its uploads."""
    with transaction.atomic():
        state = _locked_sync(device)
        DeviceUser.all_objects.filter(device=device).delete()
        state.list_cmdid, state.list_at = "", None
        rows, through = {}, 0
        for message_id, received_at, payload_json, text in _messages(device):
            _apply(device, state, rows, message_id, received_at, payload_json, text)
            through = max(through, message_id)
        _save(rows)
        state.through_message_id = through
        state.rebuilt_at = timezone.now()
        state.save()
    return state


def rows(device):
    """The device's rows, up to date."""
    sync(device)
    return list(DeviceUser.all_objects.filter(device=device))


def removed_pins(device):
    """Numbers the device no longer holds, by its latest complete user list."""
    state = sync(device)
    if not state.list_cmdid or state.list_at is None:
        return set()
    return set(
        DeviceUser.all_objects.filter(device=device, last_seen_at__lte=state.list_at)
        .exclude(listed_cmdid=state.list_cmdid)
        .values_list("pin", flat=True)
    )
