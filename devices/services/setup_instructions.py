"""What the administrator must type into the physical device.

DEVICE_INTEGRATION_HANDOFF.md section 2a: "Do not make them guess a URL." The
server knows its own address, port and path, so the screen states them rather
than leaving an administrator to work them out from documentation.

The device menu paths below are for ZKTeco ADMS firmware and are **unverified
against this SenseFace 2A**. They are labelled as such in the UI, and are
corrected once the real device has been walked through (handoff section 9
step 8).
"""

from dataclasses import dataclass
from urllib.parse import urlsplit

# The device builds /iclock/... itself; it is not configurable on the terminal.
INGESTION_PATH = "/iclock/cdata"


@dataclass
class SettingLine:
    """One row of the "type this into the device" table."""

    device_menu_label: str
    value: str
    note: str = ""
    verified: bool = False


def server_address(request):
    """Host and port as the device must reach them.

    Read from the actual request, so an administrator viewing the page through
    an ngrok tunnel is told the tunnel hostname rather than localhost — which
    the device cannot reach at all.
    """
    parts = urlsplit(request.build_absolute_uri("/"))
    host = parts.hostname or ""
    scheme = parts.scheme or "http"
    port = parts.port or (443 if scheme == "https" else 80)
    return scheme, host, port


def is_unreachable_host(host):
    """A device on the LAN cannot reach the developer's own machine."""
    return host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def build(request, device, *, comm_key=None):
    """Return the ordered list of values to enter on the terminal.

    ``comm_key`` is only ever the plaintext just issued during this request.
    It is never read back from the database, which stores a hash.
    """
    scheme, host, port = server_address(request)

    lines = [
        SettingLine(
            device_menu_label="Server address / ADMS server",
            value=host,
            note=(
                "Domain or IP only — the device adds the path itself."
                if host
                else "Unavailable"
            ),
        ),
        SettingLine(
            device_menu_label="Server port",
            value=str(port),
            note="443 for HTTPS, 80 for plain HTTP." if port in (80, 443) else "",
        ),
        SettingLine(
            device_menu_label="Enable domain name / use HTTPS",
            value="Yes" if scheme == "https" else "No",
            note="Must match the scheme above, or the push will not connect.",
        ),
        SettingLine(
            device_menu_label="Communication key (COMM key)",
            value=comm_key or "•••••••• (set)"
            if device.authentication_secret_hash
            else "Not set",
            note=(
                "Shown once. Copy it now — it is stored only as a hash and "
                "cannot be displayed again."
                if comm_key
                else "Regenerate it if the value on the device is unknown."
                if device.authentication_secret_hash
                else "This device accepts pushes without a key. Set one."
            ),
        ),
        SettingLine(
            device_menu_label="Device time zone",
            value=device.timezone or "not set",
            note="Must match the timezone recorded here, or punch times shift.",
        ),
        SettingLine(
            device_menu_label="Push interval",
            value=f"{(device.settings or {}).get('push_interval_seconds', 10)} seconds",
        ),
        SettingLine(
            device_menu_label="Real-time upload",
            value="On" if (device.settings or {}).get("realtime", True) else "Off",
        ),
    ]
    return lines


def full_endpoint(request):
    """The complete URL, for copying into a browser or curl while testing."""
    scheme, host, port = server_address(request)
    default_port = (scheme == "https" and port == 443) or (
        scheme == "http" and port == 80
    )
    authority = host if default_port else f"{host}:{port}"
    return f"{scheme}://{authority}{INGESTION_PATH}"
