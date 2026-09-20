"""Reconstruct a device's own user roster from the messages it sent us.

A ZKTeco access-control device synchronises its user table to the server as
``tabledata`` uploads: ``user`` rows (id, name, card, privilege), ``biodata``
rows (one per enrolled fingerprint/face template) and ``userpic`` /
``biophoto`` rows (photos). This module reads those already-stored
``DeviceMessage`` rows and rebuilds the roster the administrator sees on the
device screen.

Deliberately no new model: the device — not us — owns this data, and
``DeviceMessage`` already preserves it as append-only evidence. Adding an
eleventh table would duplicate a fact we already hold and would put this
workstream's schema outside the agreed ten-model contract
(MODEL_FIELD_DICTIONARY.md sections 22-31).

Nothing here is authorization. A person appearing on the device roster means
the *device* can recognise them; whether their punch counts is decided by
DeviceEnrollment and the scope rules (DEVICE_ATTENDANCE_POLICY.md).
"""

from devices.adapters.zkteco_adms import parse_kv_row
from devices.models import DeviceEnrollment, DeviceMessage, PunchEvent

# ZKTeco biodata template types seen on SenseFace 2A firmware
# ZAM70-NF24HA-Ver3.0.15. Anything else is counted under "other" rather than
# guessed at, so an unrecognised type is visible instead of silently dropped.
BIO_TYPES = {
    "1": "fingerprint",
    "2": "face",
    "9": "face",
}

# Device privilege codes -> the label the device itself shows.
PRIVILEGE_LABELS = {
    "0": "Normal User",
    "2": "Enroller",
    "6": "Administrator",
    "14": "Super Admin",
}


def _tabledata_rows(device, row_prefix):
    """Yield parsed rows from every stored upload of one table.

    Messages are read oldest-first so a later upload's values overwrite an
    earlier one — the device re-sends its whole table, so the newest wins.
    Keys are lower-cased: the 3.x ``tabledata`` rows spell them ``pin`` and
    ``name``, the 2.x operation-log rows ``PIN`` and ``Name``.
    """
    # Matched on the payload rather than message_type: rows captured before
    # this firmware's tables were understood are typed 'unknown', and that
    # historical evidence must still be readable. "Contains", because a 2.x
    # operation log mixes USER lines in among OPLOG lines.
    messages = (
        DeviceMessage.all_objects.filter(
            device=device, raw_payload_text__contains=row_prefix
        )
        .order_by("received_at")
        .values_list("raw_payload_text", flat=True)
    )
    for payload in messages:
        for line in (payload or "").splitlines():
            line = line.strip()
            if not line.startswith(row_prefix):
                continue
            fields = parse_kv_row(line[len(row_prefix):].strip())
            yield {key.lower(): value for key, value in fields.items()}


# Where each table's rows are found. The 3.x form first (SenseFace 2A,
# ``tabledata``), then the 2.x form (SenseFace 3A, captured 2026-09-15):
#   USER PIN=1  Name=NIHAL  Pri=14  Passwd=  Card=196793  Grp=1 ...
#   BIODATA Pin=1  No=6  Index=0  Valid=1  Duress=0  Type=1 ...
# FP/FACE are the older 2.x template lines (PIN, FID).
USER_PREFIXES = ("user ", "USER ")
BIODATA_PREFIXES = ("biodata ", "BIODATA ")
LEGACY_TEMPLATE_PREFIXES = (("FP ", "fingerprint"), ("FACE ", "face"))
PHOTO_PREFIXES = ("userpic ", "biophoto ", "USERPIC ", "BIOPHOTO ")


def _rows(device, prefixes):
    for prefix in prefixes:
        yield from _tabledata_rows(device, prefix)


def _removed_pins(device):
    """Numbers the device no longer holds, judged by its latest complete list.

    A 3.x device answers "Refresh user list" with its whole user table, the
    upload's query carrying the ``cmdid`` it answers (split into ``packidx``
    parts when large). Anything else (a user added or edited on the terminal)
    is sent on its own, without ``cmdid``. A number seen only before the
    latest complete answer, and not in it, has been deleted from the device.
    Rows are kept on the screen — their scans still belong to that number —
    and marked. A 2.x device (the 3A) sends no such answer, so nothing is
    marked there.
    """
    uploads = list(
        DeviceMessage.all_objects.filter(device=device, raw_payload_text__contains="user ")
        .order_by("received_at")
        .values_list("received_at", "payload_json", "raw_payload_text")
    )
    answers = [
        (received, (payload or {}).get("query", {}))
        for received, payload, _ in uploads
        if (payload or {}).get("query", {}).get("cmdid")
        and ((payload or {}).get("query", {}).get("tablename") or [""])[0] == "user"
    ]
    if not answers:
        return set()
    latest_cmd = answers[-1][1]["cmdid"]
    listed_at = min(received for received, query in answers if query["cmdid"] == latest_cmd)

    def pins(text):
        for line in (text or "").splitlines():
            line = line.strip()
            if line.startswith("user "):
                pin = {k.lower(): v for k, v in parse_kv_row(line[5:]).items()}.get("pin", "").strip()
                if pin:
                    yield pin

    listed, later, earlier = set(), set(), set()
    for received, payload, text in uploads:
        query = (payload or {}).get("query", {})
        if query.get("cmdid") == latest_cmd:
            listed.update(pins(text))
        elif received > listed_at:
            later.update(pins(text))
        else:
            earlier.update(pins(text))
    return earlier - listed - later


def build_roster(device):
    """Return one dict per user the device has reported, newest values winning.

    Each row carries what the device knows (id, name, privilege, card,
    password, credential counts) joined to what *we* know (the mapped employee
    and whether their punches are permitted).
    """
    users = {}

    def blank(pin):
        return {
            "pin": pin, "uid": "", "name": "", "card_number": "", "has_password": False,
            "privilege_code": "", "disabled_on_device": False, "fingerprint_count": 0,
            "face_count": 0, "other_biometric_count": 0, "has_photo": False,
            "only_in_scans": False,
        }

    for fields in _rows(device, USER_PREFIXES):
        pin = (fields.get("pin") or "").strip()
        if not pin:
            continue
        row = blank(pin)
        row.update({
            "uid": fields.get("uid", ""),
            "name": (fields.get("name") or "").strip(),
            "card_number": (fields.get("cardno") or fields.get("card") or "").strip(),
            "has_password": bool((fields.get("password") or fields.get("passwd") or "").strip()),
            "privilege_code": (fields.get("privilege") or fields.get("pri") or "").strip(),
            "disabled_on_device": (fields.get("disable") or "0").strip() == "1",
        })
        users[pin] = row

    # Credentials are counted per (pin, template index) so a re-sent table does
    # not inflate the totals.
    seen_bio = set()

    def count(pin, marker, kind):
        row = users.get(pin)
        if row is None or marker in seen_bio:
            return
        seen_bio.add(marker)
        if kind == "fingerprint":
            row["fingerprint_count"] += 1
        elif kind == "face":
            row["face_count"] += 1
        else:
            row["other_biometric_count"] += 1

    for fields in _rows(device, BIODATA_PREFIXES):
        pin = (fields.get("pin") or "").strip()
        marker = (pin, fields.get("type", ""), fields.get("no", ""), fields.get("index", ""))
        count(pin, marker, BIO_TYPES.get((fields.get("type") or "").strip()))
    for prefix, kind in LEGACY_TEMPLATE_PREFIXES:
        for fields in _rows(device, (prefix,)):
            pin = (fields.get("pin") or "").strip()
            count(pin, (pin, kind, fields.get("fid", "")), kind)

    for fields in _rows(device, PHOTO_PREFIXES):
        row = users.get((fields.get("pin") or "").strip())
        if row is not None:
            row["has_photo"] = True

    # A number that scanned but whose user record has not reached us yet
    # (the 3A reports a user only when it is added, edited or asked for). It
    # is listed so it can be mapped now; "Refresh user list" fetches the name.
    scanned = (
        PunchEvent.all_objects.filter(device=device)
        .exclude(device_user_id="").order_by()
        .values_list("device_user_id", flat=True).distinct()
    )
    for pin in scanned:
        if pin not in users:
            users[pin] = {**blank(pin), "only_in_scans": True}

    # Join our side: who this device user maps to, and whether it counts.
    enrollments = {
        e.device_user_id: e
        for e in DeviceEnrollment.objects.filter(device=device)
        .select_related("employee")
        .order_by("effective_from")
    }

    removed = _removed_pins(device)
    roster = []
    for pin, row in users.items():
        row["removed_from_device"] = pin in removed
        enrollment = enrollments.get(pin)
        row["privilege_label"] = PRIVILEGE_LABELS.get(
            row["privilege_code"], f"Code {row['privilege_code']}" if row["privilege_code"] else ""
        )
        row["enrollment"] = enrollment
        row["employee"] = enrollment.employee if enrollment else None
        row["is_mapped"] = enrollment is not None
        # "Recognised by the device" and "allowed to count" are separate
        # states, and the screen must not blur them.
        row["attendance_enabled"] = bool(enrollment and enrollment.attendance_enabled)
        row["assigned_device_authorized"] = bool(
            enrollment and enrollment.assigned_device_authorized
        )
        # Plain values for the table's server-side search and sort (N9).
        row["employee_name"] = enrollment.employee.full_name if enrollment else ""
        row["counts_for_attendance"] = (
            2 if row["attendance_enabled"] and row["assigned_device_authorized"]
            else 1 if row["attendance_enabled"]
            else 0
        )
        roster.append(row)

    roster.sort(key=lambda r: (r["removed_from_device"], not r["is_mapped"], r["name"].lower(), r["pin"]))
    return roster
