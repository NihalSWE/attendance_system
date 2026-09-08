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

        return ParsedMessage(
            message_type=DeviceMessage.MessageType.UNKNOWN,
            payload_json={"query": dict(query), "path_name": path_name},
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

    def handshake_response(self, *, device, stamp):
        """Config block returned to a registering device.

        These key=value lines are how the device learns how often to push and
        which tables to send. ``Stamp``/``OpStamp`` are the device's resume
        pointers for attendance and operation logs.
        """
        settings = device.settings or {}
        lines = [
            f"GET OPTION FROM: {device.serial_number}",
            f"Stamp={stamp}",
            f"OpStamp={stamp}",
            f"ErrorDelay={settings.get('error_delay_seconds', 30)}",
            f"Delay={settings.get('push_interval_seconds', 10)}",
            "TransTimes=00:00;14:05",
            f"TransInterval={settings.get('trans_interval_minutes', 1)}",
            "TransFlag=TransData AttLog OpLog AttPhoto EnrollUser ChgUser EnrollFP",
            f"TimeZone={settings.get('device_utc_offset_hours', 6)}",
            f"Realtime={1 if settings.get('realtime', True) else 0}",
            "Encrypt=0",
        ]
        return "\n".join(lines) + "\n"
