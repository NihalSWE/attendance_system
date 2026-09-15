"""Is the device connected? (plan step N8)

A terminal cannot be pinged: it calls the server, never the other way round.
So "connected" means one thing here — the device has checked in recently —
and the only honest test is to wait for its next check-in.

Three readings, from ``last_seen_at`` and the device's push interval:

    connected       seen within a few polls (never tighter than two minutes,
                    because a device's real polling drifts from its setting)
    late            "Last seen … ago" — quiet for a little while
    not_connected   quiet for longer than LATE_AFTER, or never seen

``last_seen_at`` is stamped on every authenticated request the device makes
(devices/views/ingestion.py), including the idle ``getrequest`` poll, so a
device with nothing to send still counts as connected.

A **test** is a start time. It passes when a check-in arrives after it. It can
also queue a harmless command (the device re-sends its settings) and follow it
to the answer, which proves the device takes commands as well as calls in.
After WAIT_LIMIT with nothing, the test says what to check.
"""

import datetime
import re
from dataclasses import dataclass, field

from django.utils import timezone

from devices.models import BiometricDevice, DeviceMessage, DeviceSyncState

#: Quiet for longer than this is not connected, whatever the interval.
LATE_AFTER = datetime.timedelta(minutes=15)
#: A test waits at least this long before giving advice.
MIN_WAIT = datetime.timedelta(minutes=2)
#: The harmless command a test may send: the device re-sends its own settings.
TEST_COMMAND = "query_options"

TONES = {
    "connected": "success",
    "late": "warning",
    "not_connected": "danger",
    "retired": "neutral",
    "suspended": "neutral",
}


def interval_seconds(device):
    try:
        return max(1, int((device.settings or {}).get("push_interval_seconds") or 10))
    except (TypeError, ValueError):
        return 10


def connected_within(device):
    return max(datetime.timedelta(seconds=interval_seconds(device) * 6),
               datetime.timedelta(minutes=2))


def wait_limit(device):
    return max(datetime.timedelta(seconds=interval_seconds(device) * 6), MIN_WAIT)


def ago(seconds):
    """A duration the way a person says it."""
    seconds = max(0, int(seconds))
    if seconds < 10:
        return "just now"
    if seconds < 60:
        return f"{seconds} s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} h ago"
    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''} ago"


@dataclass
class Connection:
    key: str
    label: str
    detail: str = ""
    last_seen_at: datetime.datetime | None = None

    @property
    def tone(self):
        return TONES[self.key]

    def as_dict(self):
        return {
            "key": self.key,
            "label": self.label,
            "tone": self.tone,
            "detail": self.detail,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
        }


def connection_of(device, now=None):
    now = now or timezone.now()
    if device.status == BiometricDevice.Status.RETIRED:
        return Connection("retired", "Retired", "", device.last_seen_at)
    if device.status == BiometricDevice.Status.SUSPENDED:
        return Connection("suspended", "Suspended", "", device.last_seen_at)

    seen = device.last_seen_at
    if seen is None:
        return Connection("not_connected", "Not connected", "Never checked in")
    quiet = now - seen
    if quiet <= connected_within(device):
        return Connection("connected", "Connected", f"Checked in {ago(quiet.total_seconds())}", seen)
    if quiet <= LATE_AFTER:
        return Connection("late", f"Last seen {ago(quiet.total_seconds())}", "", seen)
    return Connection(
        "not_connected", "Not connected", f"Last seen {ago(quiet.total_seconds())}", seen,
    )


def connections(devices, now=None):
    """``{public_id: dict}`` for the pages' live badges."""
    now = now or timezone.now()
    return {str(d.public_id): connection_of(d, now).as_dict() for d in devices}


def stopped_devices(company_id, now=None):
    """Active devices that are not checking in, most recently seen first.

    Pending devices are left out: one that has not made first contact yet is
    being set up, not broken, and its own page says so.
    """
    now = now or timezone.now()
    devices = BiometricDevice.all_objects.filter(
        company_id=company_id, status=BiometricDevice.Status.ACTIVE,
    ).select_related("branch").order_by("-last_seen_at", "name")
    return [
        (device, connection_of(device, now))
        for device in devices
        if connection_of(device, now).key == "not_connected"
    ]


# --------------------------------------------------------------------------
# Testing the connection
# --------------------------------------------------------------------------


@dataclass
class TestResult:
    connection: Connection
    started_at: datetime.datetime
    checked_in: bool = False
    checked_in_at: datetime.datetime | None = None
    waited_seconds: int = 0
    gave_up: bool = False
    command: dict = field(default_factory=dict)

    def as_dict(self, advice=None):
        return {
            "connection": self.connection.as_dict(),
            "started_at": self.started_at.isoformat(),
            "checked_in": self.checked_in,
            "checked_in_at": self.checked_in_at.isoformat() if self.checked_in_at else None,
            "waited_seconds": self.waited_seconds,
            "gave_up": self.gave_up,
            "command": self.command,
            "advice": advice or [],
        }


_RESULT_ID = re.compile(r"(?:^|[&\n])ID=(\d+)&Return=(-?\d+)")


def command_progress(device, command_id, since):
    """Where a queued test command has got to: queued, picked up, answered."""
    if not command_id:
        return {}
    state = DeviceSyncState.all_objects.filter(device=device).first()
    data = (state.state_data or {}) if state else {}
    if any(entry.get("id") == command_id for entry in data.get("pending_commands") or []):
        return {"id": command_id, "stage": "queued",
                "text": "Waiting for the device to pick up a test command."}

    answers = DeviceMessage.all_objects.filter(
        device=device, message_type=DeviceMessage.MessageType.COMMAND_RESULT,
        received_at__gte=since,
    ).order_by("received_at").values_list("raw_payload_text", "received_at")
    for body, received_at in answers:
        for match in _RESULT_ID.finditer(body or ""):
            if int(match.group(1)) == command_id:
                code = match.group(2)
                ok = code == "0"
                return {
                    "id": command_id, "stage": "answered", "ok": ok,
                    "answered_at": received_at.isoformat(),
                    "text": (
                        "The device ran the test command and answered."
                        if ok else
                        f"The device answered the test command with code {code}."
                    ),
                }
    if any(entry.get("id") == command_id for entry in data.get("in_flight_commands") or []):
        return {"id": command_id, "stage": "picked_up",
                "text": "The device picked up the test command; waiting for its answer."}
    return {"id": command_id, "stage": "unknown",
            "text": "The test command is no longer queued."}


def test_status(device, *, since, command_id=None, now=None):
    now = now or timezone.now()
    since = min(since, now)
    seen = device.last_seen_at
    checked_in = seen is not None and seen >= since
    waited = now - since
    return TestResult(
        connection=connection_of(device, now),
        started_at=since,
        checked_in=checked_in,
        checked_in_at=seen if checked_in else None,
        waited_seconds=int(waited.total_seconds()),
        gave_up=not checked_in and waited >= wait_limit(device),
        command=command_progress(device, command_id, since),
    )


def advice(device, setup_lines):
    """What to check when a test gets no check-in, most likely first."""
    lines = [
        {
            "title": "The server address on the terminal",
            "text": "It must point at this server exactly. On the device, check:",
            "values": [(line.device_menu_label, line.value) for line in setup_lines],
        },
        {
            "title": "The serial number",
            "text": (
                f"This device is registered here with serial {device.serial_number}. "
                "It must match the serial shown in the terminal's system information "
                "— a typo means its check-ins are refused."
            ),
            "values": [],
        },
        {
            "title": "The network",
            "text": (
                "The terminal needs a working cable or Wi-Fi connection that can reach "
                "the internet. Its network screen should show an IP address; if it "
                "shows none, the problem is the network, not this server."
            ),
            "values": [],
        },
        {
            "title": "The communication key",
            "text": (
                "If a communication key was set here, the same key must be typed on "
                "the terminal. If it was not, the terminal's key must be blank."
            ),
            "values": [],
        },
    ]
    return lines
