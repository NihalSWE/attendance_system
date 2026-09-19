"""Fingerprint and face templates kept so they can be copied between devices.

"Enrol once, copy to the rest" (agreed 2026-09-19): a person is enrolled on one
device of a company, that device uploads the templates it captured, and the
server writes them to the company's other devices of the same model. The
software never creates a template; it only relays one a device captured, and
templates only transfer between devices of one model.

What is kept is keyed on the device and its user number, never an employee:
linking a device user to an employee belongs to the mapping step that waits for
the company-level departments change.

Templates are biometric data, so they are encrypted with
``settings.BIOMETRIC_TEMPLATE_KEY`` (a Fernet key). With no key, or a bad one,
saving and reading refuse with ``TemplateKeyMissing``; nothing is ever stored
in plain text by this module. The device holds the originals, so a lost key or
a flushed table is recovered by pulling the templates again.
"""

import hashlib

from django.conf import settings
from django.core import checks
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from devices.adapters.zkteco_adms import parse_kv_row
from devices.models import DeviceUserTemplate
from devices.services.device_roster import BIO_TYPES, BIODATA_PREFIXES, _rows

KEY_SETTING = "BIOMETRIC_TEMPLATE_KEY"


class TemplateKeyMissing(ImproperlyConfigured):
    """Templates cannot be saved or read: the encryption key is missing or wrong."""


def _cipher():
    from cryptography.fernet import Fernet

    key = (getattr(settings, KEY_SETTING, "") or "").strip()
    if not key:
        raise TemplateKeyMissing(
            f"{KEY_SETTING} is not set, so fingerprint and face templates cannot be "
            "saved. Add a key to the server's .env (see .env.example) and restart."
        )
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise TemplateKeyMissing(
            f"{KEY_SETTING} is not a valid key. Generate one as .env.example shows."
        ) from exc


def key_problem():
    """The reason templates cannot be saved, or "" when they can."""
    try:
        _cipher()
    except TemplateKeyMissing as exc:
        return str(exc)
    return ""


def encrypt(text):
    return _cipher().encrypt(text.encode("ascii"))


def decrypt(data):
    from cryptography.fernet import InvalidToken

    try:
        return _cipher().decrypt(bytes(data)).decode("ascii")
    except InvalidToken as exc:
        raise TemplateKeyMissing(
            f"A saved template cannot be read with the current {KEY_SETTING}; "
            "it was saved under another key. Save the templates from the device again."
        ) from exc


def _save_rows(device, rows):
    """Upsert parsed biodata rows. Returns (added, updated, unchanged)."""
    latest = {}
    for fields in rows:
        pin = (fields.get("pin") or "").strip()
        template = (fields.get("tmp") or "").strip()
        vendor_type = (fields.get("type") or "").strip()
        # A row without a user number cannot be written back to anyone.
        if not pin or not template or not vendor_type:
            continue
        key = (pin, vendor_type, (fields.get("no") or "").strip(),
               (fields.get("index") or "").strip())
        latest[key] = fields  # oldest first, so the newest upload wins

    added = updated = unchanged = 0
    if not latest:
        return added, updated, unchanged
    cipher = _cipher()  # refuse the whole batch before writing anything
    now = timezone.now()
    with transaction.atomic():
        existing = {
            (t.device_user_id, t.vendor_type, t.number, t.index): t
            for t in DeviceUserTemplate.all_objects.filter(device=device)
        }
        for key, fields in latest.items():
            template = fields["tmp"].strip()
            digest = hashlib.sha256(template.encode("ascii")).hexdigest()
            values = {
                "bio_type": BIO_TYPES.get(key[1], DeviceUserTemplate.BioType.OTHER),
                "valid": (fields.get("valid") or "").strip(),
                "duress": (fields.get("duress") or "").strip(),
                "major_version": (fields.get("majorver") or "").strip(),
                "minor_version": (fields.get("minorver") or "").strip(),
                "template_format": (fields.get("format") or "").strip(),
                "template_sha256": digest,
                "template_length": len(template),
            }
            row = existing.get(key)
            if row is not None and row.template_sha256 == digest:
                unchanged += 1
                continue
            values["template_encrypted"] = cipher.encrypt(template.encode("ascii"))
            values["captured_at"] = now
            if row is None:
                DeviceUserTemplate.all_objects.create(
                    company_id=device.company_id, device=device,
                    device_model_id=device.device_model_id,
                    device_user_id=key[0], vendor_type=key[1], number=key[2],
                    index=key[3], **values,
                )
                added += 1
            else:
                for field, value in values.items():
                    setattr(row, field, value)
                row.save()
                updated += 1
    return added, updated, unchanged


def save_from_payload(device, payload):
    """Save the templates in one upload (``biodata …`` / ``BIODATA …`` lines)."""
    rows = []
    for line in (payload or "").splitlines():
        line = line.strip()
        for prefix in BIODATA_PREFIXES:
            if line.startswith(prefix):
                fields = parse_kv_row(line[len(prefix):].strip())
                rows.append({key.lower(): value for key, value in fields.items()})
    return _save_rows(device, rows)


def save_from_messages(device):
    """Save every template in the uploads already stored for ``device``."""
    return _save_rows(device, _rows(device, BIODATA_PREFIXES))


def saved_counts(device):
    """``{device_user_id: {"fingerprint": n, "face": n}}`` of what the server holds."""
    counts = {}
    for pin, bio_type in DeviceUserTemplate.all_objects.filter(device=device).values_list(
        "device_user_id", "bio_type"
    ):
        counts.setdefault(pin, {"fingerprint": 0, "face": 0, "other": 0})[bio_type] += 1
    return counts


def as_payload(row):
    """The plain values ``commands.push_to_device`` takes for one saved template."""
    return {
        "type": row.vendor_type,
        "no": row.number,
        "index": row.index,
        "valid": row.valid or "1",
        "duress": row.duress or "0",
        "major_version": row.major_version,
        "minor_version": row.minor_version,
        "format": row.template_format,
        "template": decrypt(row.template_encrypted),
        "device_model_id": row.device_model_id,
    }


def templates_for(device, device_user_id):
    """``(finger, face)`` payloads saved for one device user; either may be None.

    One of each: the first fingerprint (lowest finger number) and the face.
    """
    rows = DeviceUserTemplate.all_objects.filter(
        device=device, device_user_id=str(device_user_id)
    ).order_by("number", "index")
    finger = next((r for r in rows if r.bio_type == DeviceUserTemplate.BioType.FINGERPRINT), None)
    face = next((r for r in rows if r.bio_type == DeviceUserTemplate.BioType.FACE), None)
    return (as_payload(finger) if finger else None, as_payload(face) if face else None)


@checks.register()
def check_template_key(app_configs, **kwargs):
    problem = key_problem()
    if problem:
        return [checks.Warning(problem, id="devices.W001")]
    return []
