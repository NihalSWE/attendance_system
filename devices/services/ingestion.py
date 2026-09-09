"""Durable ingestion of device messages and extraction of punch evidence.

Two rules shape this module (DEVICE_INTEGRATION_HANDOFF.md section 3):

1. **Durable before acknowledgement.** The raw ``DeviceMessage`` is committed
   in its own transaction before anything is parsed into punches, and the
   device is acknowledged even if extraction then fails. A background retry can
   always re-parse stored evidence; a device that is never acknowledged may
   discard records we cannot get back.
2. **A retransmission must not multiply attendance effects.** That is achieved
   by idempotent *processing*, never by deleting evidence. A re-sent batch is
   rejected at the message level by ``(device, idempotency_key)``; a re-sent
   individual punch is stored and marked ``confirmed_duplicate`` so it stays
   visible and reviewable while being excluded from downstream allocation.
"""

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.contrib.auth.hashers import check_password
from django.db import IntegrityError, transaction
from django.utils import timezone

from common.tenant import use_company
from devices.models import BiometricDevice, DeviceMessage, DeviceSyncState, PunchEvent
from devices.services.processing import resolve_and_authorize_safely

logger = logging.getLogger(__name__)

# Statuses that may send us data. A retired or suspended device is refused:
# its punches would otherwise silently re-enter attendance after a decommission.
INGESTING_STATUSES = (
    BiometricDevice.Status.PENDING,
    BiometricDevice.Status.ACTIVE,
    BiometricDevice.Status.OFFLINE,
)

# Request headers worth keeping for diagnosis. Anything carrying a credential
# is never snapshotted, so stored evidence cannot leak a device secret.
SAFE_HEADERS = ("Content-Type", "Content-Length", "User-Agent", "Host", "Accept")

DEFAULT_REPEAT_WINDOW_SECONDS = 30


class DeviceAuthenticationError(Exception):
    """The sender could not be identified as exactly one registered device."""


@dataclass
class ExtractionResult:
    created: list = field(default_factory=list)
    confirmed_duplicates: list = field(default_factory=list)
    probable_duplicates: list = field(default_factory=list)
    skipped_unparsable: int = 0

    @property
    def accepted_count(self):
        return len(self.created)


@dataclass
class IngestionResult:
    device: BiometricDevice
    message: DeviceMessage
    is_replay: bool
    extraction: ExtractionResult | None = None
    extraction_error: str = ""


def authenticate_device(*, serial_number, presented_key_id="", presented_secret=""):
    """Identify the sending device from its serial and optional shared secret.

    Runs before any tenant context exists, so it queries the unscoped manager
    and derives the company from trusted registration — never from anything the
    caller supplied (DEVICE_ATTENDANCE_POLICY.md, "Processing and history"
    step 1).

    Serial numbers are unique *per company*, so the same serial may legitimately
    exist in two tenants. When it does, the request is ambiguous and is refused
    unless a key id resolves it: guessing a company would attach one tenant's
    punches to another's employees.
    """
    serial_number = (serial_number or "").strip()
    if not serial_number:
        raise DeviceAuthenticationError("No serial number supplied.")

    candidates = list(
        BiometricDevice.all_objects.select_related("branch", "device_model__vendor")
        .filter(serial_number=serial_number, status__in=INGESTING_STATUSES)
    )
    if not candidates:
        raise DeviceAuthenticationError(
            f"No device registered for serial {serial_number!r} in an ingesting state."
        )

    if len(candidates) > 1:
        if not presented_key_id:
            raise DeviceAuthenticationError(
                f"Serial {serial_number!r} is registered by more than one company "
                "and no key id was presented to disambiguate."
            )
        candidates = [
            d for d in candidates if d.authentication_key_id == presented_key_id
        ]
        if len(candidates) != 1:
            raise DeviceAuthenticationError(
                f"Serial {serial_number!r} could not be resolved to exactly one device."
            )

    device = candidates[0]

    if device.authentication_secret_hash:
        if not check_password(presented_secret or "", device.authentication_secret_hash):
            raise DeviceAuthenticationError(
                f"Invalid device secret for serial {serial_number!r}."
            )
    return device


def _device_zoneinfo(device):
    try:
        return ZoneInfo(device.timezone) if device.timezone else ZoneInfo("UTC")
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning(
            "Device %s has an unusable timezone %r; interpreting as UTC.",
            device.pk, device.timezone,
        )
        return ZoneInfo("UTC")


def _header_snapshot(headers):
    snapshot = {}
    for name in SAFE_HEADERS:
        value = headers.get(name)
        if value:
            snapshot[name] = value
    return snapshot


@transaction.atomic
def capture_message(*, device, parsed, raw_body, source_ip=None, headers=None,
                    content_type="", encoding="utf-8"):
    """Persist the raw message. Returns ``(message, created)``.

    Committed before any punch parsing runs, so acknowledging the device is
    always backed by durable evidence. A replay carrying an idempotency key we
    have already stored returns the original row with ``created=False``.
    """
    headers = headers or {}
    payload_hash = hashlib.sha256(raw_body.encode("utf-8", "replace")).hexdigest()

    if parsed.idempotency_key:
        existing = DeviceMessage.all_objects.filter(
            device=device, idempotency_key=parsed.idempotency_key
        ).first()
        if existing:
            return existing, False

    message = DeviceMessage(
        company_id=device.company_id,
        device=device,
        # Snapshot: the device's branch may be corrected later without
        # rewriting what was true when this message arrived.
        branch_id=device.branch_id,
        message_type=parsed.message_type,
        vendor_message_id=parsed.vendor_message_id,
        vendor_sequence=parsed.vendor_sequence,
        idempotency_key=parsed.idempotency_key,
        occurred_at_device=parsed.occurred_at_device,
        received_at=timezone.now(),
        content_type=content_type,
        encoding=encoding,
        raw_payload_text=raw_body,
        payload_json=parsed.payload_json,
        payload_hash=payload_hash,
        source_ip=source_ip,
        request_headers_snapshot=_header_snapshot(headers),
        record_count=parsed.record_count,
        processing_status=DeviceMessage.ProcessingStatus.RECEIVED,
        processing_error=parsed.parse_error,
    )
    try:
        with transaction.atomic():
            message.save()
    except IntegrityError:
        # Two concurrent deliveries of the same batch: the unique constraint on
        # (device, idempotency_key) decided the winner, so return that row.
        existing = DeviceMessage.all_objects.filter(
            device=device, idempotency_key=parsed.idempotency_key
        ).first()
        if existing is None:
            raise
        return existing, False

    return message, True


def _natural_key_duplicate(*, device, punch, punched_at_utc):
    """An identical punch already recorded.

    Identity is the device, the device-reported user id, the interpreted
    instant and the vendor status code — never the timestamp alone
    (MODEL_FIELD_DICTIONARY.md section 31). The row being evaluated has not
    been saved yet, so it cannot match itself; rows earlier in the same batch
    are compared, because a device can repeat a record within one batch.
    """
    return (
        PunchEvent.all_objects.filter(
            company_id=device.company_id,
            device=device,
            device_user_id=punch.device_user_id,
            punched_at_utc=punched_at_utc,
            reported_status_code=punch.reported_status_code,
        )
        .order_by("id")
        .first()
    )


def _repeat_window_seconds(company_id):
    """Company's configured repeat-scan window, defaulting when unset."""
    from scheduling.models import CompanyAttendanceSettings

    seconds = (
        CompanyAttendanceSettings.all_objects.filter(company_id=company_id)
        .values_list("duplicate_punch_window_seconds", flat=True)
        .first()
    )
    return seconds if seconds is not None else DEFAULT_REPEAT_WINDOW_SECONDS


def _nearby_punch(*, device, punch, punched_at_utc, window_seconds):
    """A different punch from the same identity within the repeat window.

    Proximity is *not* proof of duplication, so this only supports marking a
    row ``probable_duplicate`` for review; it never excludes it. Rows earlier
    in the same batch count: a rapid repeat usually arrives inside one upload,
    not in a later one.
    """
    if window_seconds <= 0:
        return None
    delta = timedelta(seconds=window_seconds)
    return (
        PunchEvent.all_objects.filter(
            company_id=device.company_id,
            device=device,
            device_user_id=punch.device_user_id,
            punched_at_utc__gte=punched_at_utc - delta,
            punched_at_utc__lte=punched_at_utc + delta,
        )
        .exclude(punched_at_utc=punched_at_utc)
        .order_by("id")
        .first()
    )


def extract_punch_events(*, device, message, parsed):
    """Create PunchEvent rows for one captured message.

    Safe to re-run: the unique constraint on ``(device_message,
    source_record_index)`` means an interrupted extraction can be retried
    without producing a second copy of the same record.
    """
    result = ExtractionResult()
    if not parsed.punches:
        return result

    tzinfo = _device_zoneinfo(device)
    window_seconds = _repeat_window_seconds(device.company_id)

    already_extracted = set(
        PunchEvent.all_objects.filter(device_message=message).values_list(
            "source_record_index", flat=True
        )
    )

    for punch in parsed.punches:
        if punch.source_record_index in already_extracted:
            continue

        if punch.punched_at_device is None:
            # The raw line is already preserved verbatim in the message, which
            # is the append-only evidence. A punch row needs a real instant, so
            # this record is counted and the message is flagged for review
            # rather than being given an invented timestamp.
            result.skipped_unparsable += 1
            continue

        punched_at_device = punch.punched_at_device.replace(tzinfo=tzinfo)
        punched_at_utc = punched_at_device.astimezone(ZoneInfo("UTC"))
        offset = punched_at_device.utcoffset()

        dedupe_status = PunchEvent.DedupeStatus.UNIQUE
        processing_status = PunchEvent.ProcessingStatus.PENDING
        duplicate_of = None

        identical = _natural_key_duplicate(
            device=device, punch=punch, punched_at_utc=punched_at_utc
        )
        if identical is not None:
            # A retransmitted record: kept as evidence, excluded from
            # allocation so the attendance effect is not counted twice.
            dedupe_status = PunchEvent.DedupeStatus.CONFIRMED_DUPLICATE
            processing_status = PunchEvent.ProcessingStatus.EXCLUDED
            duplicate_of = identical
        else:
            nearby = _nearby_punch(
                device=device,
                punch=punch,
                punched_at_utc=punched_at_utc,
                window_seconds=window_seconds,
            )
            if nearby is not None:
                # Ambiguous repeat: flagged for a human, never auto-excluded.
                dedupe_status = PunchEvent.DedupeStatus.PROBABLE_DUPLICATE
                processing_status = PunchEvent.ProcessingStatus.NEEDS_REVIEW
                duplicate_of = nearby

        event = PunchEvent(
            company_id=device.company_id,
            device_message=message,
            device=device,
            branch_id=device.branch_id,
            device_user_id=punch.device_user_id,
            vendor_punch_id=punch.vendor_punch_id,
            vendor_sequence=punch.vendor_sequence,
            source_record_index=punch.source_record_index,
            punched_at_device_raw=punch.punched_at_device_raw,
            punched_at_device=punched_at_device,
            punched_at_utc=punched_at_utc,
            device_timezone=device.timezone,
            utc_offset_minutes=int(offset.total_seconds() // 60) if offset else None,
            received_at=message.received_at,
            verification_method=punch.verification_method,
            reported_direction=punch.reported_direction,
            reported_status_code=punch.reported_status_code,
            raw_record=punch.raw_record,
            dedupe_status=dedupe_status,
            duplicate_of=duplicate_of,
            processing_status=processing_status,
        )
        try:
            with transaction.atomic():
                event.save()
        except IntegrityError:
            # Concurrent extraction of the same record; the constraint held.
            continue

        # Evaluate immediately so an administrator sees a decided punch, not a
        # pending one. Failure here is recorded on the row; the punch itself is
        # already durable and is never lost.
        resolve_and_authorize_safely(event)

        result.created.append(event)
        if dedupe_status == PunchEvent.DedupeStatus.CONFIRMED_DUPLICATE:
            result.confirmed_duplicates.append(event)
        elif dedupe_status == PunchEvent.DedupeStatus.PROBABLE_DUPLICATE:
            result.probable_duplicates.append(event)

    return result


def _touch_sync_state(*, device, message, punch_count, error_message=""):
    """Update the device's live health row. Never treated as evidence."""
    state, _ = DeviceSyncState.all_objects.get_or_create(
        device=device, defaults={"company_id": device.company_id}
    )
    now = timezone.now()
    state.last_message_received_at = message.received_at
    if punch_count:
        state.last_punch_received_at = message.received_at
    if message.vendor_sequence:
        state.last_vendor_sequence = message.vendor_sequence
    if error_message:
        state.last_error_at = now
        state.last_error_message = error_message
        state.consecutive_error_count += 1
    else:
        state.last_success_at = now
        state.consecutive_error_count = 0
    state.version += 1
    state.save()

    BiometricDevice.all_objects.filter(pk=device.pk).update(
        last_seen_at=now, last_message_at=message.received_at
    )
    return state


def ingest(*, device, parsed, raw_body, source_ip=None, headers=None,
           content_type="", encoding="utf-8"):
    """Capture one message durably, then best-effort extract its punches.

    Extraction failure never prevents acknowledgement: the raw message is
    already committed, so the punches can be recovered by reprocessing.
    """
    message, created = capture_message(
        device=device,
        parsed=parsed,
        raw_body=raw_body,
        source_ip=source_ip,
        headers=headers,
        content_type=content_type,
        encoding=encoding,
    )

    if not created:
        # Replay of a batch we already hold. Acknowledged again so the device
        # can advance its pointer, but nothing is stored or counted twice.
        return IngestionResult(
            device=device, message=message, is_replay=True,
            extraction=ExtractionResult(),
        )

    with use_company(device.company_id):
        message.processing_status = DeviceMessage.ProcessingStatus.PARSING
        message.processing_started_at = timezone.now()
        message.processing_attempts += 1
        message.save(update_fields=[
            "processing_status", "processing_started_at", "processing_attempts",
        ])

        extraction_error = ""
        try:
            extraction = extract_punch_events(
                device=device, message=message, parsed=parsed
            )
        except Exception as exc:  # noqa: BLE001 - evidence is already durable
            logger.exception("Punch extraction failed for message %s", message.pk)
            extraction = None
            extraction_error = f"{type(exc).__name__}: {exc}"
            message.processing_status = DeviceMessage.ProcessingStatus.FAILED
            message.processing_error = extraction_error
            message.save(update_fields=["processing_status", "processing_error"])
        else:
            partial = bool(extraction.skipped_unparsable) or bool(parsed.parse_error)
            message.processing_status = (
                DeviceMessage.ProcessingStatus.PARTIALLY_FAILED
                if partial
                else DeviceMessage.ProcessingStatus.PARSED
            )
            if extraction.skipped_unparsable:
                note = (
                    f"{extraction.skipped_unparsable} record(s) had an "
                    "uninterpretable timestamp and were not extracted."
                )
                message.processing_error = (
                    f"{message.processing_error}; {note}".strip("; ")
                )
            message.processed_at = timezone.now()
            message.save(update_fields=[
                "processing_status", "processing_error", "processed_at",
            ])

        _touch_sync_state(
            device=device,
            message=message,
            punch_count=extraction.accepted_count if extraction else 0,
            error_message=extraction_error,
        )

    return IngestionResult(
        device=device,
        message=message,
        is_replay=False,
        extraction=extraction,
        extraction_error=extraction_error,
    )
