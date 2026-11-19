"""Inspect captured PUSH transaction packets. Never imports, alters or deletes punches.

Security PUSH 20210907, appendices 5/6 encode months as 31 days. These
timestamps are NOT Unix timestamps. Inventory completeness is not permission
to clear logs and does not establish date-limited delete support.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.db.models import Q
from django.utils import timezone

from devices.models import DeviceMessage


def decode_time(value, zone):
    remaining, second = divmod(int(value), 60)
    remaining, minute = divmod(remaining, 60)
    remaining, hour = divmod(remaining, 24)
    remaining, day = divmod(remaining, 31)
    year, month = divmod(remaining, 12)
    return datetime(2000 + year, month + 1, day + 1, hour, minute, second, tzinfo=zone)


def _one(value):
    if isinstance(value, list):
        if len(value) != 1:
            raise ValueError("Expected one protocol value")
        return value[0]
    return value


def inspect(device, command_id, days, *, now=None):
    if command_id < 1 or days < 1:
        raise ValueError("Command ID and retention days must be positive")
    now = now or timezone.now()
    zone = ZoneInfo(device.timezone)  # No silent timezone fallback for retention.
    cutoff = now - timedelta(days=days)
    packets, totals, issues = {}, set(), set()
    messages = DeviceMessage.all_objects.filter(
        company_id=device.company_id, device=device,
    ).filter(
        Q(payload_json__query__cmdid=[str(command_id)])
        | Q(payload_json__query__cmdid=str(command_id))
    ).only("id", "payload_json", "raw_payload_text").order_by("id")
    for message in messages.iterator(chunk_size=100):
        query = (message.payload_json or {}).get("query", {})
        try:
            if _one(query.get("tablename")) != "transaction":
                continue
            index, total, count = (int(_one(query[k])) for k in ("packidx", "packcnt", "count"))
            if not 1 <= index <= total or count < 0:
                raise ValueError("Invalid packet metadata")
            lines = tuple(line for line in (message.raw_payload_text or "").splitlines() if line.strip())
            if len(lines) != count:
                issues.add("Packet row count mismatch")
            if index in packets and packets[index] != lines:
                issues.add("Conflicting retransmitted packet")
            packets[index] = lines
            totals.add(total)
        except (ValueError, TypeError, KeyError):
            issues.add("Invalid packet metadata")
    total = next(iter(totals), 0)
    if (len(totals) != 1 or len(packets) != total or not packets
            or min(packets) != 1 or max(packets) != total):
        issues.add("Incomplete packet set")
    dates, indices = [], set()
    for lines in packets.values():
        for line in lines:
            try:
                if not line.startswith("transaction "):
                    raise ValueError("Unexpected row")
                parts = [part.split("=", 1) for part in line[len("transaction "):].split("\t")]
                fields = dict(parts)
                if len(fields) != len(parts):
                    raise ValueError("Repeated field")
                index = fields["index"]
                if index in indices:
                    issues.add("Repeated transaction index")
                indices.add(index)
                dates.append(decode_time(fields["time_second"], zone))
            except (ValueError, TypeError, KeyError, OverflowError):
                issues.add("Unparseable transaction row")
    future = sum(at > now + timedelta(minutes=5) for at in dates)
    pre2000 = sum(at.year < 2000 for at in dates)
    if future or pre2000:
        issues.add("Reader clock anomalies require review")
    return {
        "command_id": command_id, "days": days, "cutoff": cutoff.isoformat(),
        "packet_count": len(packets), "records": len(dates),
        "older_records": sum(at < cutoff for at in dates),
        "retained_records": sum(at >= cutoff for at in dates),
        "oldest": min(dates).isoformat() if dates else None,
        "newest": max(dates).isoformat() if dates else None,
        "future_dated": future, "pre_2000": pre2000,
        "issues": sorted(issues), "cleanup_available": False,
        "notice": "Read-only snapshot of transaction records, including non-punch events. "
                  "Does not prove attendance reconciliation or enable deletion.",
    }
