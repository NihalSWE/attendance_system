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
from devices.models import DeviceEnrollment, DeviceMessage

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


def _tabledata_rows(device, tablename, row_prefix):
    """Yield parsed rows from every stored ``tabledata`` upload of one table.

    Messages are read oldest-first so a later upload's values overwrite an
    earlier one — the device re-sends its whole table, so the newest wins.
    """
    # Matched on the payload prefix rather than message_type: rows captured
    # before this firmware's tables were understood are typed 'unknown', and
    # that historical evidence must still be readable.
    messages = (
        DeviceMessage.all_objects.filter(
            device=device, raw_payload_text__startswith=row_prefix
        )
        .order_by("received_at")
        .values_list("raw_payload_text", flat=True)
    )
    for payload in messages:
        for line in (payload or "").splitlines():
            line = line.strip()
            if not line.startswith(row_prefix):
                continue
            yield parse_kv_row(line[len(row_prefix):].strip())


def build_roster(device):
    """Return one dict per user the device has reported, newest values winning.

    Each row carries what the device knows (id, name, privilege, card,
    password, credential counts) joined to what *we* know (the mapped employee
    and whether their punches are permitted).
    """
    users = {}

    for fields in _tabledata_rows(device, "user", "user "):
        pin = (fields.get("pin") or "").strip()
        if not pin:
            continue
        users[pin] = {
            "pin": pin,
            "uid": fields.get("uid", ""),
            "name": (fields.get("name") or "").strip(),
            "card_number": (fields.get("cardno") or "").strip(),
            "has_password": bool((fields.get("password") or "").strip()),
            "privilege_code": (fields.get("privilege") or "").strip(),
            "disabled_on_device": (fields.get("disable") or "0").strip() == "1",
            "fingerprint_count": 0,
            "face_count": 0,
            "other_biometric_count": 0,
            "has_photo": False,
        }

    # Credentials are counted per (pin, template index) so a re-sent table does
    # not inflate the totals.
    seen_bio = set()
    for fields in _tabledata_rows(device, "biodata", "biodata "):
        pin = (fields.get("pin") or "").strip()
        row = users.get(pin)
        if row is None:
            continue
        marker = (pin, fields.get("type", ""), fields.get("no", ""), fields.get("index", ""))
        if marker in seen_bio:
            continue
        seen_bio.add(marker)
        kind = BIO_TYPES.get((fields.get("type") or "").strip())
        if kind == "fingerprint":
            row["fingerprint_count"] += 1
        elif kind == "face":
            row["face_count"] += 1
        else:
            row["other_biometric_count"] += 1

    for tablename, prefix in (("userpic", "userpic "), ("biophoto", "biophoto ")):
        for fields in _tabledata_rows(device, tablename, prefix):
            row = users.get((fields.get("pin") or "").strip())
            if row is not None:
                row["has_photo"] = True

    # Join our side: who this device user maps to, and whether it counts.
    enrollments = {
        e.device_user_id: e
        for e in DeviceEnrollment.objects.filter(device=device)
        .select_related("employee")
        .order_by("effective_from")
    }

    roster = []
    for pin, row in users.items():
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
        roster.append(row)

    roster.sort(key=lambda r: (not r["is_mapped"], r["name"].lower(), r["pin"]))
    return roster
