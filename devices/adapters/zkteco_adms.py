"""ZKTeco ADMS ("TA Push") adapter.

The device initiates every exchange; we never poll it on port 4370
(DEVICE_INTEGRATION_HANDOFF.md section 1 — a prior K40 polling build failed).

Wire format, as documented for the ZKTeco push protocol:

    GET  /iclock/cdata?SN=<sn>&options=all&pushver=<v>   handshake, expects a
                                                        key=value config block
    POST /iclock/cdata?SN=<sn>&table=ATTLOG&Stamp=<s>    attendance batch,
                                                        tab-separated rows
    POST /iclock/cdata?SN=<sn>&table=OPERLOG             operation/user log
    GET  /iclock/getrequest?SN=<sn>                      device asks for commands
    POST /iclock/devicecmd?SN=<sn>                       command results

An ATTLOG row is tab-separated:

    <device user id>\t<YYYY-MM-DD HH:MM:SS>\t<status>\t<verify>\t<workcode>...

**Verification status.** Every format detail here is taken from the published
protocol, not from this SenseFace 2A's firmware. Nothing in this module has
been confirmed against the physical device yet; that is a later slice
(DEVICE_INTEGRATION_HANDOFF.md section 9 step 8). Treat the ack strings and
field order as assumptions to verify, and record real captures when we have
them.
"""

import hashlib
from datetime import datetime

from devices.adapters.base import DeviceAdapter, ParsedMessage, ParsedPunch
from devices.models import DeviceMessage, PunchEvent

# ZKTeco verify-mode codes -> our controlled vocabulary. Unlisted codes are
# recorded as 'unknown' rather than guessed, and the raw value is kept in
# raw_record either way.
VERIFY_MODES = {
    "0": PunchEvent.VerificationMethod.PIN,
    "1": PunchEvent.VerificationMethod.FINGERPRINT,
    "2": PunchEvent.VerificationMethod.CARD,
    "15": PunchEvent.VerificationMethod.FACE,
}

# rtlog reports verification differently from ATTLOG. These codes are the ones
# the SenseFace 2A (ZAM70-NF24HA-Ver3.0.15) actually emits, confirmed from
# captured traffic rather than taken from documentation:
#   1   -> fingerprint (35 captures, cardno always 0)
#   4   -> card        (the only capture carrying a real cardno, 196793,
#                       which matches that user's card in the device's own
#                       user table)
#   15  -> face        (149 captures, cardno always 0)
#   200 -> door/system event; those rows carry pin=0 and are never punches
# An unlisted code stays 'unknown' and keeps its raw value in raw_record,
# rather than being guessed into a method that was never verified.
RTLOG_VERIFY_MODES = {
    "0": PunchEvent.VerificationMethod.PIN,
    "1": PunchEvent.VerificationMethod.FINGERPRINT,
    "4": PunchEvent.VerificationMethod.CARD,
    "15": PunchEvent.VerificationMethod.FACE,
}

# ZKTeco punch-state codes. Informational only: the vendor's IN/OUT flag is
# never trusted to decide pairing (DEVICE_ATTENDANCE_POLICY.md).
PUNCH_STATES = {
    "0": "check_in",
    "1": "check_out",
    "2": "break_out",
    "3": "break_in",
    "4": "overtime_in",
    "5": "overtime_out",
}

DEVICE_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# rtlog in/out flag. Informational only, exactly like PUNCH_STATES: the
# vendor's direction guess never decides pairing
# (DEVICE_ATTENDANCE_POLICY.md).
RTLOG_INOUT_STATES = {
    "0": "check_in",
    "1": "check_out",
    "2": "system_event",
}


def parse_kv_row(line):
    """Parse one tab-separated ``key=value`` rtlog row into a dict.

    Values are kept as received; a pair without ``=`` is skipped rather than
    guessed at, so a malformed row cannot silently invent a field.
    """
    fields = {}
    for chunk in line.replace("\r", "").split("\t"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        key, _, value = chunk.partition("=")
        fields[key.strip()] = value.strip()
    return fields


# PushSDK 3.x transmission bitmask. The 2.x firmware accepted a space-separated
# list of table names ("TransData AttLog OpLog ..."); 3.x expects a digit
# string and silently transmits nothing when handed the older form. Confirmed
# against SenseFace 2A firmware ZAM70-NF24HA-Ver3.0.15 / PushVersion 3.0.4S.
PUSH3_TRANS_FLAG = "1111000000"

# Server version announced to a 3.x device during registration.
PUSH3_SERVER_VERSION = "3.0.1"


def parse_device_info(body_text):
    """Parse the comma-separated key=value block a device posts to /registry.

    The body looks like ``DeviceType=acc,~DeviceName=SenseFace 2A,FirmVer=...``.
    Keys are returned verbatim, leading ``~`` included, so a capture can be
    compared against the vendor documentation without normalisation hiding a
    difference.
    """
    info = {}
    for chunk in (body_text or "").replace("\r", "").replace("\n", ",").split(","):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        key, _, value = chunk.partition("=")
        info[key.strip()] = value.strip()
    return info


def uses_push3(pushver):
    """True when the device announces PushSDK 3.x or newer.

    ``pushver`` arrives as a query parameter such as ``3.1.2``. Anything we
    cannot read as a major version falls back to the 2.x behaviour, because
    that is what every previously-tested firmware expects.
    """
    major = (str(pushver or "").strip().split(".") or [""])[0]
    try:
        return int(major) >= 3
    except ValueError:
        return False


class ZKTecoAdmsAdapter(DeviceAdapter):
    key = "zkteco_adms_push"

    def parse(self, *, path_name, query, body_text, serial_number):
        if path_name == "cdata_handshake":
            return ParsedMessage(
                message_type=DeviceMessage.MessageType.DEVICE_INFO,
                payload_json={"query": dict(query)},
            )

        if path_name == "cdata_upload":
            table = (query.get("table") or "").upper()
            if table == "ATTLOG":
                return self._parse_attlog(query, body_text, serial_number)
            if table == "RTLOG":
                return self._parse_rtlog(query, body_text, serial_number)
            if table == "RTSTATE":
                # Door/sensor/relay state. Useful liveness, never attendance.
                return ParsedMessage(
                    message_type=DeviceMessage.MessageType.HEARTBEAT,
                    payload_json={"query": dict(query), "table": "RTSTATE"},
                )
            if table == "TABLEDATA":
                # User, biometric template and photo synchronisation.
                return ParsedMessage(
                    message_type=DeviceMessage.MessageType.ENROLLMENT_RESULT,
                    payload_json={
                        "query": dict(query),
                        "table": "TABLEDATA",
                        "tablename": query.get("tablename", ""),
                    },
                )
            # OPERLOG carries user/template administration events. We store it
            # verbatim; interpreting it is not needed for attendance and is not
            # guessed here.
            return ParsedMessage(
                message_type=(
                    DeviceMessage.MessageType.ENROLLMENT_RESULT
                    if table == "OPERLOG"
                    else DeviceMessage.MessageType.UNKNOWN
                ),
                vendor_sequence=query.get("Stamp", ""),
                payload_json={"query": dict(query), "table": table},
            )

        if path_name == "getrequest":
            return ParsedMessage(
                message_type=DeviceMessage.MessageType.HEARTBEAT,
                payload_json={"query": dict(query)},
            )

        if path_name == "devicecmd":
            return ParsedMessage(
                message_type=DeviceMessage.MessageType.COMMAND_RESULT,
                payload_json={"query": dict(query)},
            )

        if path_name == "registry":
            return ParsedMessage(
                message_type=DeviceMessage.MessageType.DEVICE_INFO,
                payload_json={
                    "query": dict(query),
                    "device_info": parse_device_info(body_text),
                },
            )

        if path_name == "push":
            return ParsedMessage(
                message_type=DeviceMessage.MessageType.DEVICE_INFO,
                payload_json={"query": dict(query)},
            )

        return ParsedMessage(
            message_type=DeviceMessage.MessageType.UNKNOWN,
            payload_json={"query": dict(query), "path_name": path_name},
        )

    def _parse_rtlog(self, query, body_text, serial_number):
        """Parse the real-time event log of an access-control (``acc``) device.

        Confirmed against SenseFace 2A ZAM70-NF24HA-Ver3.0.15. Unlike ATTLOG's
        positional columns, each row is tab-separated ``key=value`` pairs::

            time=2026-09-09 12:09:10\tpin=445966\tcardno=0\teventaddr=1
            \tevent=3\tinoutstatus=0\tverifytype=1\tindex=40\t...

        The same table also carries door/alarm events, which have no user
        (``pin=0``). Those are preserved in the stored message but produce no
        punch: inventing an attendance fact from a door sensor would be a
        fabricated event, not evidence.
        """
        punches = []
        errors = []

        for index, line in enumerate(body_text.splitlines()):
            if not line.strip():
                continue
            fields = parse_kv_row(line)
            if "pin" not in fields:
                # Not a user event (door/sensor/alarm row); kept in the raw
                # payload, deliberately not turned into a punch.
                continue

            device_user_id = (fields.get("pin") or "").strip()
            raw_timestamp = (fields.get("time") or "").strip()
            if not device_user_id or device_user_id == "0":
                continue

            punched_at_device = None
            try:
                punched_at_device = datetime.strptime(raw_timestamp, DEVICE_TIME_FORMAT)
            except ValueError:
                # Seen on this firmware before its clock syncs: timestamps like
                # '1971--10--29 -23:-27:-54'. The row is still evidence, so it
                # is retained with its raw string and flagged for review.
                errors.append(f"line {index}: unparsable timestamp {raw_timestamp!r}")

            event_index = (fields.get("index") or "").strip()
            # The device's own event index restarts after a factory reset, so
            # it is combined with the raw timestamp: a genuine retransmission
            # still collides, while a post-reset event does not silently
            # displace an older punch.
            vendor_punch_id = (
                f"rtlog:{event_index}:{raw_timestamp}" if event_index else ""
            )

            punches.append(
                ParsedPunch(
                    source_record_index=index,
                    device_user_id=device_user_id,
                    punched_at_device_raw=raw_timestamp,
                    punched_at_device=punched_at_device,
                    verification_method=RTLOG_VERIFY_MODES.get(
                        (fields.get("verifytype") or "").strip(),
                        PunchEvent.VerificationMethod.UNKNOWN,
                    ),
                    # inoutstatus is the vendor's own in/out guess. Recorded as
                    # informational only; pairing never trusts it.
                    reported_direction=RTLOG_INOUT_STATES.get(
                        (fields.get("inoutstatus") or "").strip(), ""
                    ),
                    reported_status_code=(fields.get("event") or "").strip(),
                    vendor_punch_id=vendor_punch_id,
                    vendor_sequence=event_index,
                    raw_record={
                        "line": line,
                        "fields": fields,
                        "table": "RTLOG",
                    },
                )
            )

        payload_digest = hashlib.sha256(body_text.encode("utf-8", "replace")).hexdigest()
        idempotency_key = f"rtlog:{serial_number}:{payload_digest[:32]}"

        return ParsedMessage(
            message_type=DeviceMessage.MessageType.PUNCH_BATCH,
            punches=punches,
            idempotency_key=idempotency_key,
            record_count=len(punches),
            payload_json={"query": dict(query), "table": "RTLOG"},
            parse_error="; ".join(errors),
        )

    def _parse_attlog(self, query, body_text, serial_number):
        stamp = query.get("Stamp", "")
        punches = []
        errors = []

        # Keep the physical line number as source_record_index so an evidence
        # row can always be traced back to its exact position in the payload,
        # including blank/garbled lines that produced no punch.
        for index, line in enumerate(body_text.splitlines()):
            if not line.strip():
                continue
            fields = line.split("\t")
            if len(fields) < 2:
                errors.append(f"line {index}: expected tab-separated fields")
                continue

            device_user_id = fields[0].strip()
            raw_timestamp = fields[1].strip()
            status_code = fields[2].strip() if len(fields) > 2 else ""
            verify_code = fields[3].strip() if len(fields) > 3 else ""

            punched_at_device = None
            try:
                punched_at_device = datetime.strptime(raw_timestamp, DEVICE_TIME_FORMAT)
            except ValueError:
                # A record we cannot interpret is still evidence: it is kept
                # with its raw string and flagged, never dropped.
                errors.append(f"line {index}: unparsable timestamp {raw_timestamp!r}")

            punches.append(
                ParsedPunch(
                    source_record_index=index,
                    device_user_id=device_user_id,
                    punched_at_device_raw=raw_timestamp,
                    punched_at_device=punched_at_device,
                    verification_method=VERIFY_MODES.get(
                        verify_code, PunchEvent.VerificationMethod.UNKNOWN
                    ),
                    reported_direction=PUNCH_STATES.get(status_code, ""),
                    reported_status_code=status_code,
                    vendor_sequence=stamp,
                    raw_record={
                        "line": line,
                        "fields": fields,
                        "table": "ATTLOG",
                        "stamp": stamp,
                    },
                )
            )

        # The device sends no per-batch message id, so the key is derived from
        # device-supplied identity (serial + stamp) plus a content hash. That
        # makes a genuine retransmission collide, while two different batches
        # that happen to share a stamp do not.
        payload_digest = hashlib.sha256(body_text.encode("utf-8", "replace")).hexdigest()
        idempotency_key = f"attlog:{serial_number}:{stamp}:{payload_digest[:32]}"

        return ParsedMessage(
            message_type=DeviceMessage.MessageType.PUNCH_BATCH,
            punches=punches,
            vendor_sequence=stamp,
            idempotency_key=idempotency_key,
            record_count=len(punches),
            payload_json={"query": dict(query), "table": "ATTLOG"},
            parse_error="; ".join(errors),
        )

    def acknowledgement(self, *, parsed, accepted_count):
        """ZKTeco push expects a plain-text body, not JSON.

        For an attendance batch the documented reply is ``OK: <count>``; other
        requests take a bare ``OK``. Both are assumptions until confirmed on
        the real firmware — a wrong ack is exactly what makes a device re-send
        forever or skip records.
        """
        if parsed.message_type == DeviceMessage.MessageType.PUNCH_BATCH:
            return f"OK: {accepted_count}"
        return "OK"

    def handshake_response(self, *, device, stamp, pushver=""):
        """Config block returned to a registering device.

        These key=value lines are how the device learns how often to push and
        which tables to send. ``Stamp``/``OpStamp`` are the device's resume
        pointers for attendance and operation logs.
        """
        settings = device.settings or {}
        push3 = uses_push3(pushver)
        lines = [
            f"GET OPTION FROM: {device.serial_number}",
            f"Stamp={stamp}",
            f"OpStamp={stamp}",
            f"ErrorDelay={settings.get('error_delay_seconds', 30)}",
            f"Delay={settings.get('push_interval_seconds', 10)}",
            "TransTimes=00:00;14:05",
            f"TransInterval={settings.get('trans_interval_minutes', 1)}",
            # 3.x needs the numeric bitmask; the 2.x table-name list makes it
            # register successfully and then transmit nothing at all.
            f"TransFlag={PUSH3_TRANS_FLAG}"
            if push3
            else "TransFlag=TransData AttLog OpLog AttPhoto EnrollUser ChgUser EnrollFP",
            f"TimeZone={settings.get('device_utc_offset_hours', 6)}",
            f"Realtime={1 if settings.get('realtime', True) else 0}",
            "Encrypt=0",
        ]
        if push3:
            lines.append(f"ServerVer={PUSH3_SERVER_VERSION}")
            lines.append("PushProtVer=3.0.1")
        return "\n".join(lines) + "\n"

    def registry_response(self, *, device):
        """Reply to ``POST /iclock/registry`` for a PushSDK 3.x device.

        The firmware treats registration as incomplete until it receives a
        ``RegistryCode``, and simply repeats the POST forever otherwise — the
        loop observed on SenseFace 2A ZAM70-NF24HA-Ver3.0.15. The code only has
        to be a stable per-device token; we derive it from the device's
        immutable public_id so a restart does not invalidate it.
        """
        code = hashlib.sha256(str(device.public_id).encode()).hexdigest()[:16].upper()
        return f"RegistryCode={code}\n"

    def push_response(self, *, device, stamp="0"):
        """Reply to ``GET /iclock/push`` — the 3.x transmission parameters."""
        settings = device.settings or {}
        lines = [
            f"ErrorDelay={settings.get('error_delay_seconds', 30)}",
            f"Delay={settings.get('push_interval_seconds', 10)}",
            "TransTimes=00:00;14:05",
            f"TransInterval={settings.get('trans_interval_minutes', 1)}",
            f"TransFlag={PUSH3_TRANS_FLAG}",
            f"TimeZone={settings.get('device_utc_offset_hours', 6)}",
            f"Realtime={1 if settings.get('realtime', True) else 0}",
            f"ServerVer={PUSH3_SERVER_VERSION}",
            f"Stamp={stamp}",
            "Encrypt=0",
        ]
        return "\n".join(lines) + "\n"
