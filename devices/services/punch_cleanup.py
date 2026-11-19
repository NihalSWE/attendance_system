"""Verified index-only retention for the tested SenseFace 2A firmware.

The device menu's date deletion is not used. We select individual transaction
indices from a complete fresh inventory, match them to durable HRM punches,
and compare a second inventory after each bounded batch. Never clears tables.
"""
import logging
import re
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.cache import cache
from django.db import close_old_connections, transaction
from django.db.models import Q
from django.utils import timezone

from auditlog.models import AuditLog
from devices.models import (BiometricDevice, DeviceMessage, DeviceOutboxCommand,
                            DevicePunchCleanup, DevicePunchRetention, PunchEvent)
from devices.services import commands, protocol
from devices.services.transaction_inventory import _one, decode_time

FIRMWARE = "ZAM70-NF24HA-Ver3.0.15"
DELETE_PREFIX = "retention_delete:"
QUERY = "DATA QUERY tablename=transaction,fielddesc=*,filter=*"
BATCH_SIZE = 25
FRESH_FOR = timedelta(minutes=10)
WAIT_FOR = timedelta(minutes=20)
log = logging.getLogger(__name__)


def supported(device):
    return (device.serial_number in getattr(settings, "DEVICE_PUNCH_CLEANUP_SERIALS", [])
            and device.status == BiometricDevice.Status.ACTIVE
            and device.device_model.model_code == "senseface-2a"
            and device.firmware_version == FIRMWARE
            and protocol.dialect(device) == protocol.PUSH3)


def policy_token(policy):
    return [policy.pk, policy.days, policy.updated_at.isoformat()] if policy else None


def delete_body(index):
    if not re.fullmatch(r"[1-9][0-9]{0,9}", str(index)):
        raise ValueError("Invalid transaction index")
    return f"DATA DELETE transaction Index={index}"


def snapshot(device, command_id):
    """Return canonical rows only after a complete acknowledged query."""
    command = DeviceOutboxCommand.all_objects.get(
        company_id=device.company_id, device=device, command_id=command_id)
    if command.status in ("queued", "sent"):
        return None
    if command.status != "done" or not str(command.return_code).isdigit():
        raise ValueError("Reader refused the transaction inventory")
    if command.description != QUERY or not command.key.startswith("retention_query:"):
        raise ValueError("Wrong inventory command")
    packets, totals = {}, set()
    messages = DeviceMessage.all_objects.filter(company_id=device.company_id, device=device).filter(
        Q(payload_json__query__cmdid=[str(command_id)]) |
        Q(payload_json__query__cmdid=str(command_id)))
    for message in messages.only("raw_payload_text", "payload_json").iterator(chunk_size=100):
        query = (message.payload_json or {}).get("query", {})
        if _one(query.get("tablename")) != "transaction":
            continue
        part, total, count = (int(_one(query[k])) for k in ("packidx", "packcnt", "count"))
        lines = tuple(line for line in message.raw_payload_text.splitlines() if line.strip())
        if not 1 <= part <= total <= 10000 or count != len(lines):
            raise ValueError("Invalid inventory packet")
        if part in packets and packets[part] != lines:
            raise ValueError("Conflicting inventory packets")
        packets[part] = lines
        totals.add(total)
    if not packets and command.return_code == "0":
        return {}
    if (len(totals) != 1 or len(packets) != next(iter(totals), 0)
            or min(packets, default=0) != 1 or max(packets, default=0) != len(packets)):
        raise ValueError("Incomplete inventory; no deletion permitted")
    rows = {}
    for lines in packets.values():
        for line in lines:
            if not line.startswith("transaction "):
                raise ValueError("Unexpected inventory row")
            parts = [part.split("=", 1) for part in line[12:].split("\t")]
            fields = dict(parts)
            index = fields["index"]
            if len(fields) != len(parts) or index in rows:
                raise ValueError("Repeated inventory field or index")
            rows[index] = fields
    if len(rows) != int(command.return_code):
        raise ValueError("Inventory count does not match reader acknowledgement")
    return rows


def evidence(device, fields):
    at = decode_time(fields["time_second"], ZoneInfo(device.timezone))
    vendor_id = f"rtlog:{fields['index']}:{at:%Y-%m-%d %H:%M:%S}"
    return PunchEvent.all_objects.filter(
        company_id=device.company_id, device=device, vendor_punch_id=vendor_id,
        device_user_id=fields["pin"], punched_at_utc=at).exists()


def candidates(device, rows, cutoff):
    possible, skipped = [], 0
    for index, fields in rows.items():
        try:
            at = decode_time(fields["time_second"], ZoneInfo(device.timezone))
            if at >= cutoff:
                continue
            delete_body(index)
            if at.year < 2000 or fields.get("eventtype") != "3":
                skipped += 1
                continue
            possible.append((at, index, fields["pin"], f"rtlog:{index}:{at:%Y-%m-%d %H:%M:%S}"))
        except (KeyError, ValueError, OverflowError):
            skipped += 1
    # Match in bounded queries rather than one database query per reader log.
    eligible = []
    for offset in range(0, len(possible), 500):
        batch = possible[offset:offset + 500]
        saved = set(PunchEvent.all_objects.filter(company_id=device.company_id, device=device,
            vendor_punch_id__in=[item[3] for item in batch]).values_list(
                "vendor_punch_id", "device_user_id", "punched_at_utc"))
        for at, index, pin, vendor_id in batch:
            if (vendor_id, pin, at) in saved:
                eligible.append((at, index))
            else:
                skipped += 1
    eligible.sort()
    return [index for _, index in eligible], skipped


def write_problem(device, row, body, now):
    """Revalidate immediately before a queued delete leaves HRM."""
    device = BiometricDevice.all_objects.select_related("device_model").get(pk=device.pk)
    if not supported(device):
        return "Retention capability disabled"
    job = DevicePunchCleanup.all_objects.filter(company_id=device.company_id, device=device).first()
    policy = DevicePunchRetention.all_objects.filter(company_id=device.company_id, device=device).first()
    if not job or job.phase != "deleting" or policy_token(policy) != job.state.get("policy"):
        return "Retention plan changed or stopped"
    state = job.state
    index = row.key.rsplit(":", 1)[-1]
    if not re.fullmatch(r"[1-9][0-9]{0,9}", index):
        return "Invalid retention index"
    if row.key != f"{DELETE_PREFIX}{state['before']}:{index}" or index not in state["targets"]:
        return "Unrecognised retention target"
    if body != delete_body(index) or now - datetime.fromisoformat(state["snapshot_at"]) > FRESH_FOR:
        return "Retention snapshot expired or command changed"
    fields = state["targets"][index]
    at = decode_time(fields["time_second"], ZoneInfo(device.timezone))
    if at >= now - timedelta(days=policy.days) or at >= datetime.fromisoformat(state["cutoff"]):
        return "Punch inside retention window"
    if not evidence(device, fields):
        return "Saved HRM punch could not be verified"
    return ""


def _queue(device, entries):
    result, error = commands._queue_group(device=device, commands=entries)
    if error:
        raise ValueError(error)
    return [entry["id"] for entry in result]


def _audit(job):
    AuditLog.objects.create(
        company_id=job.company_id, actor_type=AuditLog.ActorType.SYSTEM,
        action="device.punch_cleanup", object_app="devices", object_model="BiometricDevice",
        object_id=str(job.device_id), object_display="Device punch retention",
        after_data={"phase": job.phase, "message": job.message,
                    "state": {k: (list(v) if k == "targets" else v) for k, v in job.state.items()},
                    "verified_deleted": job.verified_deleted})


def _advance(device, job, policy, now):
    state = job.state
    if job.phase == "paused":
        return  # An unexpected reader result requires investigation, not retries.
    changed = policy_token(policy) != state.get("policy") or not supported(device)
    if job.phase in ("deleting", "verifying"):
        # Once any writes may have been sent, always verify their result, even
        # if the administrator has since disabled or changed the policy.
        if job.phase == "deleting":
            writes = DeviceOutboxCommand.all_objects.filter(
                company_id=device.company_id, device=device, command_id__in=state["writes"])
            if changed or now - datetime.fromisoformat(state["snapshot_at"]) > FRESH_FOR:
                writes.filter(status="queued").update(status="dropped", body="", body_encrypted=None,
                    return_code="Retention changed or expired", answered_at=now)
            if writes.filter(status__in=["queued", "sent"]).exists():
                if now - datetime.fromisoformat(state["snapshot_at"]) <= WAIT_FOR:
                    return
                raise ValueError("Delete response timed out; investigate before resuming")
            state["after"] = _queue(device, [(f"retention_query:{state['before']}:after", QUERY)])[0]
            state["verify_started"] = now.isoformat()
            job.phase, job.message = "verifying", "Reading back the device logs to verify deletion."
            return
        after = snapshot(device, state["after"])
        if after is None:
            if now - datetime.fromisoformat(state["verify_started"]) > WAIT_FOR:
                raise ValueError("Readback timed out; cleanup paused")
            return
        before = snapshot(device, state["before"])
        targets = set(state["targets"])
        if before is None or any(after.get(i) != fields for i, fields in before.items() if i not in targets):
            raise ValueError("Unexpected non-target log change; cleanup paused for investigation")
        removed = targets - after.keys()
        if any(after[i] != before[i] for i in targets & after.keys()):
            raise ValueError("Target index changed; cleanup paused")
        job.verified_deleted += len(removed)
        state["deleted"] = len(removed)
        if removed != targets and not changed:
            raise ValueError("Some requested punches remain on the reader; cleanup paused")
        job.phase = "idle"
        job.message = f"Verified {len(removed)} device punches removed; other existing logs preserved."
        job.next_check_at = now + (timedelta(minutes=5) if state["eligible"] > len(removed)
                                  else timedelta(days=1))
        _audit(job)
        return
    if changed and job.phase == "inventory":
        job.phase = "idle"
        job.next_check_at = now
    if not policy or not supported(device):
        return
    if job.phase == "idle":
        if not changed and job.next_check_at and now < job.next_check_at:
            return
        before = _queue(device, [(f"retention_query:{job.pk}:before", QUERY)])[0]
        job.state = {"policy": policy_token(policy), "before": before,
                     "cutoff": (now - timedelta(days=policy.days)).isoformat(),
                     "started": now.isoformat()}
        job.phase, job.message = "inventory", "Waiting for a complete device log inventory."
        return
    rows = snapshot(device, state["before"])
    if rows is None:
        if now - datetime.fromisoformat(state["started"]) > WAIT_FOR:
            raise ValueError("Inventory timed out; no deletion sent")
        return
    query = DeviceOutboxCommand.all_objects.get(device=device, command_id=state["before"])
    if not query.sent_at or now - query.sent_at > FRESH_FOR:
        raise ValueError("Inventory is too old; no deletion sent")
    indices, skipped = candidates(device, rows, datetime.fromisoformat(state["cutoff"]))
    state.update(eligible=len(indices), skipped=skipped, snapshot_at=query.sent_at.isoformat())
    if not indices:
        job.phase, job.message = "idle", f"No verified old punches to remove. {skipped} other records retained for review."
        job.next_check_at = now + timedelta(days=1)
        _audit(job)
        return
    state["targets"] = {index: {k: rows[index][k] for k in ("index", "pin", "time_second", "eventtype")}
                        for index in indices[:BATCH_SIZE]}
    state["writes"] = _queue(device, [(f"{DELETE_PREFIX}{state['before']}:{index}", delete_body(index))
                                      for index in state["targets"]])
    job.phase, job.message = "deleting", f"Removing {len(state['targets'])} verified old punches from the reader."
    _audit(job)


@transaction.atomic
def advance(device_id):
    # This lock serializes concurrent workers and policy changes. Dispatch only
    # reads state, so it does not invert the device/outbox lock order.
    device = BiometricDevice.all_objects.select_for_update().select_related("device_model").get(pk=device_id)
    # Match command dispatch's sync-state -> outbox lock order.
    from devices.models import DeviceSyncState
    DeviceSyncState.all_objects.select_for_update().get(pk=commands._sync_state(device).pk)
    job = DevicePunchCleanup.all_objects.filter(company_id=device.company_id, device=device).first()
    policy = DevicePunchRetention.all_objects.filter(company_id=device.company_id, device=device).first()
    if job is None:
        if not policy or not supported(device):
            return
        job = DevicePunchCleanup.all_objects.create(company_id=device.company_id, device=device)
    try:
        _advance(device, job, policy, timezone.now())
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        job.phase, job.message = "paused", str(exc)[:255]
        _audit(job)
    job.save()


def _background(device_id):
    close_old_connections()
    try:
        advance(device_id)
    except Exception:
        log.exception("Punch retention failed for device %s", device_id)
    finally:
        close_old_connections()


def on_device_poll(device):
    # Keep ingestion responsive; persistent DB state survives worker restarts.
    if (device.serial_number not in getattr(settings, "DEVICE_PUNCH_CLEANUP_SERIALS", [])
            and not DevicePunchCleanup.all_objects.filter(company_id=device.company_id,
                device=device, phase__in=["deleting", "verifying"]).exists()):
        return
    if not cache.add(f"punch-cleanup:{device.pk}", True, 15):
        return
    device_id = device.pk
    transaction.on_commit(lambda: threading.Thread(
        target=_background, args=(device_id,), daemon=True).start())


def progress(device):
    job = DevicePunchCleanup.all_objects.filter(company_id=device.company_id, device=device).first()
    return {"supported": supported(device), "job": job}
