"""Vendor adapter interface.

An adapter turns one vendor's wire format into vendor-neutral parsed records.
It performs **no** database writes, no employee resolution and no authorization:
those are policy decisions owned by devices/services/, so that adding a second
vendor never re-implements the policy rules.

``DeviceVendor.adapter_key`` selects an adapter from the registry in
``devices.adapters``. It is a lookup key into reviewed code, never an import
path taken from the database.
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ParsedPunch:
    """One attendance record as the device reported it, before interpretation.

    ``punched_at_device`` is naive local device time: converting it to UTC needs
    the device's configured timezone, which is a database fact the adapter does
    not read. ``punched_at_device_raw`` preserves the exact original string so
    an evidence row never loses what the device actually sent.
    """

    source_record_index: int
    device_user_id: str
    punched_at_device_raw: str
    punched_at_device: datetime | None
    verification_method: str
    reported_direction: str = ""
    reported_status_code: str = ""
    vendor_punch_id: str = ""
    vendor_sequence: str = ""
    raw_record: dict = field(default_factory=dict)


@dataclass
class ParsedMessage:
    """One inbound message, parsed but not yet interpreted or stored."""

    message_type: str
    punches: list[ParsedPunch] = field(default_factory=list)
    vendor_message_id: str = ""
    vendor_sequence: str = ""
    # Set only when the adapter can derive it from trustworthy device-supplied
    # identity; blank means "no reliable key", never a guess.
    idempotency_key: str = ""
    occurred_at_device: datetime | None = None
    record_count: int | None = None
    payload_json: dict | None = None
    parse_error: str = ""


class DeviceAdapter:
    """Base class for vendor adapters."""

    key = ""

    def parse(self, *, path_name, query, body_text, serial_number):
        """Return a ParsedMessage for one inbound request."""
        raise NotImplementedError

    def acknowledgement(self, *, parsed, accepted_count):
        """Return the exact response body this device expects on success.

        Getting this wrong is the difference between a device that drains its
        backlog and one that either re-sends forever or silently skips records,
        so each adapter states its expected format explicitly.
        """
        raise NotImplementedError
