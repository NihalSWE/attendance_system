"""Moving a device to a different server address, without losing the device.

The device is always the initiator: it opens connections to us and we never
open one to it (DEVICE_INTEGRATION_HANDOFF.md section 1). Once a device has
switched to a new address, the only channel back to it runs *through* that new
address. So if the address is wrong, the software cannot reach the device to
put it right, and somebody has to walk to the terminal.

That is why the server address was left out of ``WRITABLE_OPTIONS``, and it is
still the governing fact. What changes here is not the risk, it is the order of
operations:

    **Prove the new address reaches this server before the device is told
    anything.**

Step 1 does that by asking this same running software to fetch its own probe
endpoint at the candidate address. Only if that round trip completes does the
device get a command. A typo, a wrong port, http/https swapped, a tunnel that
is not running, or a hostname missing from ALLOWED_HOSTS all fail here, with
the device untouched and the saved address unchanged — the clean revert.

After that the flow is a small state machine, advanced by the device's own
requests as they arrive:

    checking ─fail→ unreachable            (nothing was sent)
       │ pass
    queued ──→ delivered ──→ acknowledged ──→ confirmed   (saved)
       └──────────── deadline ─────────────┬→ not_applied (still at the old
                                           │               address: the device
                                           │               ignored it)
                                           └→ lost        (silent: it switched
                                                           and cannot reach us)

Nothing here runs on a timer. States advance when a device request arrives,
and the deadline is evaluated whenever the status is read, so no background
worker is needed.
"""

import hmac
import ipaddress
import logging
import secrets
import socket
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
from urllib.parse import urlsplit

from django.conf import settings
from django.db import IntegrityError, models, transaction
from django.utils import timezone

from devices.models import BiometricDevice, DeviceServerAddressChange
from devices.services.panel_access import assert_may_manage_devices

logger = logging.getLogger(__name__)

# The device option this writes. Established against the real hardware; see
# docs/DEVICE_SETUP.md "Changing the server address from the software" for the
# probe transcript and what this firmware does and does not expose.
SERVER_ADDRESS_OPTION = "IclockSvrIP"
SERVER_PORT_OPTION = "IclockSvrPort"

# How long step 1 waits for its own probe to come back. A few seconds: this is
# a request to ourselves over the public address, not a slow API.
PROBE_TIMEOUT_SECONDS = 5

# Step 5's window, tied to the device's push interval: it has to poll, apply
# the change, restart its connection and check in again. Six intervals gives
# the slowest of those room, with a floor for very short intervals and a cap so
# an administrator is never left watching a spinner for half an hour.
DEADLINE_INTERVALS = 6
MIN_DEADLINE_SECONDS = 180
MAX_DEADLINE_SECONDS = 1800

# Rate cap for step 1, per device. The probe is the only place this software
# makes an outbound request on a user's say-so, so it is capped even though it
# already requires an unrestricted company administrator.
MAX_PROBES_PER_HOUR = 10

#: Said when a change is asked for on a device that has never called in (plan
#: step N8 part 4). The software changes the address by leaving a command for
#: the device's next check-in; a device that has never checked in will never
#: collect it, and the change used to sit "in progress" and then end as "lost",
#: blaming the new address for a device that had simply never been connected.
NEVER_CONNECTED_MESSAGE = (
    "This device has never connected to this server. Set the server address "
    "on the terminal itself first (COMM \u2192 Cloud Server), using the values "
    "under \u201cEnter these on the device\u201d on its page. Once it has "
    "checked in, its address can be changed from here."
)

DEFAULT_PORTS = {"http": 80, "https": 443}


class ServerAddressError(Exception):
    """A change was refused before anything was written or sent."""


# ---------------------------------------------------------------- addresses


@dataclass(frozen=True)
class Address:
    """A server address as the device must dial it."""

    scheme: str
    host: str
    port: int

    @property
    def is_default_port(self):
        return DEFAULT_PORTS.get(self.scheme) == self.port

    @property
    def authority(self):
        return self.host if self.is_default_port else f"{self.host}:{self.port}"

    @property
    def text(self):
        return f"{self.scheme}://{self.authority}"

    def matches(self, other):
        """Same address, comparing what the device actually dialled.

        Hostnames are case-insensitive; the port is compared explicitly so a
        port-only change is not mistaken for a success, and the scheme so a
        plain-HTTP fallback is not read as an HTTPS confirmation.
        """
        if other is None:
            return False
        return (
            self.scheme == other.scheme
            and self.host.casefold() == other.host.casefold()
            and self.port == other.port
        )

    def __str__(self):
        return self.text


def parse_address(raw):
    """Parse what an administrator typed into a scheme, host and port.

    Accepts ``https://host``, ``host:8000`` or a bare ``host``; a missing
    scheme defaults to https, because a device that can reach us over TLS
    should, and a missing port to that scheme's default. Raises
    ServerAddressError with a message meant for the person who typed it.
    """
    text = (raw or "").strip()
    if not text:
        raise ServerAddressError("Enter a server address.")
    if len(text) > 255:
        raise ServerAddressError("That address is too long.")
    if any(char.isspace() for char in text):
        raise ServerAddressError("An address cannot contain spaces.")

    if "//" not in text:
        text = f"https://{text}"

    parts = urlsplit(text)
    scheme = (parts.scheme or "").lower()
    if scheme not in DEFAULT_PORTS:
        raise ServerAddressError(
            "The address must start with http:// or https:// — those are the "
            "only schemes the device can use."
        )
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ServerAddressError(
            "Enter the address only. The device builds the /iclock/ path itself."
        )
    if parts.username or parts.password:
        raise ServerAddressError("An address cannot carry a username or password.")

    try:
        host = parts.hostname or ""
        port = parts.port or DEFAULT_PORTS[scheme]
    except ValueError:
        raise ServerAddressError("That port is not a number.") from None

    if not host:
        raise ServerAddressError("Enter a hostname or IP address.")
    if not 1 <= port <= 65535:
        raise ServerAddressError("The port must be between 1 and 65535.")

    _reject_dangerous_host(host)
    return Address(scheme=scheme, host=host, port=port)


def _reject_dangerous_host(host):
    """Refuse targets that are never a device server.

    This is deliberately narrow. A device on the office LAN reaching the server
    at 192.168.x.x is the normal production shape of this product, so blocking
    private ranges outright would block the main use case. What is blocked is
    the set that is never a legitimate answer and is dangerous to fetch:
    link-local (cloud instance metadata lives at 169.254.169.254), multicast
    and other reserved space.

    The rest of the safety comes from the probe itself rather than from
    guessing at addresses: it requires an authenticated unrestricted
    administrator, is rate-capped, follows no redirects, and only reports
    success when the reply carries a signature that this deployment alone can
    produce — so it cannot be used to read anything out of an internal service.
    """
    candidates = [host]
    try:
        resolved = socket.getaddrinfo(host, None)
    except socket.gaierror:
        # An unresolvable name is not a security problem; step 1 will simply
        # fail to connect and report that.
        resolved = []
    candidates.extend(info[4][0] for info in resolved)

    for candidate in candidates:
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if address.is_link_local or address.is_multicast or address.is_reserved:
            raise ServerAddressError(
                f"{host} is not an address a device can be pointed at."
            )


def current_address(device):
    """The address the device is known to be using, or None if never proven."""
    if not device.server_host or not device.server_scheme or not device.server_port:
        return None
    return Address(
        scheme=device.server_scheme,
        host=device.server_host,
        port=device.server_port,
    )


def observed_address(request):
    """The address a device actually dialled to reach this request.

    Read from the request rather than from settings, because that is the only
    evidence of where the device is pointing. A tunnel or load balancer
    terminates TLS and forwards plain HTTP, so ``X-Forwarded-Proto`` carries
    the scheme the device really used — the same correction
    ``setup_instructions.server_address`` makes, and for the same reason.
    """
    parts = urlsplit(request.build_absolute_uri("/"))
    host = parts.hostname or ""
    scheme = parts.scheme or "http"

    forwarded = request.META.get("HTTP_X_FORWARDED_PROTO", "")
    if forwarded:
        scheme = forwarded.split(",")[0].strip().lower() or scheme

    if scheme not in DEFAULT_PORTS:
        scheme = "https"
    port = parts.port or DEFAULT_PORTS[scheme]
    if not host:
        return None
    return Address(scheme=scheme, host=host, port=port)


def _previous(attempt):
    if not attempt.previous_host:
        return None
    return Address(
        scheme=attempt.previous_scheme,
        host=attempt.previous_host,
        port=attempt.previous_port,
    )


def _target(attempt):
    return Address(
        scheme=attempt.new_scheme, host=attempt.new_host, port=attempt.new_port
    )


# -------------------------------------------------------------- step 1: probe


def probe_signature(token):
    """What the probe endpoint must answer with.

    Derived from the deployment's own SECRET_KEY, so a reply can only be
    produced by this running software. That is the difference between "the
    address reaches *a* server" and "the address reaches *us*" — and it is the
    whole point of step 1, because a device pointed at somebody else's server
    is just as lost as one pointed at nothing.
    """
    return hmac.new(
        settings.SECRET_KEY.encode(), f"device-address-probe:{token}".encode(), sha256
    ).hexdigest()


def probe_body(token):
    return f"DEVICE-ADDRESS-CHECK {probe_signature(token)}\n"


def _probe_url(address, token):
    from django.urls import reverse

    return f"{address.text}{reverse('devices:iclock_address_check')}?token={token}"


def _run_probe(address, token, *, fetch=None):
    """Fetch our own probe endpoint at ``address``. Returns (ok, reason).

    Redirects are not followed: a redirect means the address does not serve
    this software directly, and following one would let an unrelated host
    decide where we look next.
    """
    import urllib.error
    import urllib.request

    if fetch is None:

        def fetch(url):
            class _NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *args, **kwargs):
                    return None

            opener = urllib.request.build_opener(_NoRedirect)
            request = urllib.request.Request(
                url, headers={"User-Agent": "attendance-server-address-check"}
            )
            with opener.open(request, timeout=PROBE_TIMEOUT_SECONDS) as response:
                return response.status, response.read(4096).decode(
                    "utf-8", errors="replace"
                )

    url = _probe_url(address, token)
    try:
        status, body = fetch(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 400:
            return False, (
                f"{address.host} answered with HTTP 400. Django rejects a "
                "hostname that is not in ALLOWED_HOSTS, and a device pointed "
                "at it would be refused the same way."
            )
        return False, f"{address.host} answered with HTTP {exc.code}."
    except urllib.error.URLError as exc:
        return False, f"Could not connect to {address.authority}: {exc.reason}."
    except (TimeoutError, socket.timeout):
        return False, (
            f"{address.authority} did not answer within "
            f"{PROBE_TIMEOUT_SECONDS} seconds."
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Address probe for %s failed: %s", url, exc)
        return False, f"Could not reach {address.authority}."

    if status != 200:
        return False, f"{address.host} answered with HTTP {status}."
    if not hmac.compare_digest(body.strip(), probe_body(token).strip()):
        return False, (
            f"Something is answering at {address.authority}, but it is not "
            "this attendance server. Check the address and the port."
        )
    return True, ""


# ------------------------------------------------------------------ the flow


def _deadline_window(device):
    interval = int((device.settings or {}).get("push_interval_seconds") or 10)
    seconds = interval * DEADLINE_INTERVALS
    return timedelta(
        seconds=max(MIN_DEADLINE_SECONDS, min(seconds, MAX_DEADLINE_SECONDS))
    )


def in_flight_change(device):
    """The change still moving for this device, or None.

    Includes the probe stage, because a second change must be refused from the
    moment the first is requested.
    """
    return (
        DeviceServerAddressChange.all_objects.filter(
            device=device, status__in=DeviceServerAddressChange.IN_FLIGHT
        )
        .order_by("-created_at")
        .first()
    )


def _advanceable_change(device):
    """The change a device request may move along, or None.

    Deliberately excludes the probe stage. While step 1 is running the device
    has not been told anything, so nothing it does is evidence about this
    change — it is simply where it already was. Treating a check-in during the
    probe as a confirmation is wrong twice over: it saves an address the device
    was never asked to move to, and it flips the attempt out of ``checking``
    while the probe is still in flight, so the probe endpoint stops recognising
    its own token and the check fails.

    Both of those happened against the real SenseFace 2A, which polls about
    every ten seconds and so lands inside the probe window routinely.
    """
    return (
        DeviceServerAddressChange.all_objects.filter(
            device=device, status__in=DeviceServerAddressChange.SENT
        )
        .order_by("-created_at")
        .first()
    )


def latest_change(device):
    return (
        DeviceServerAddressChange.all_objects.filter(device=device)
        .order_by("-created_at")
        .first()
    )


def history(device, limit=10):
    return list(
        DeviceServerAddressChange.all_objects.filter(device=device)
        .select_related("created_by")
        .order_by("-created_at")[:limit]
    )


def _recent_probe_count(device):
    since = timezone.now() - timedelta(hours=1)
    return DeviceServerAddressChange.all_objects.filter(
        device=device, probe_started_at__gte=since
    ).count()


def request_change(*, device, actor, raw_address, fetch=None):
    """Steps 1 and 2: prove the address, then queue the change.

    Returns the attempt. Raises ServerAddressError when the request is refused
    before anything is written — a bad address, a retired device, a change
    already running, or too many probes in the last hour.

    The attempt row is created *before* the probe runs so the rate cap counts
    every attempt including the failures, and so a probe that never returns
    still leaves a trace of who asked for what.

    The audit entry for the *request* is written by the view, which is the only
    caller that has the actor's IP address. Every later outcome is audited from
    here, because they happen on a device request or on somebody reading the
    status, with no view in the stack.
    """
    # Re-checked here, not merely on the view: this is the one device write
    # that can put hardware out of reach, so it does not rely on a decorator
    # having run somewhere up the stack. PermissionDenied, not
    # ServerAddressError — a caller who may not do this gets no detail.
    assert_may_manage_devices(actor, device.company_id)

    if device.status == BiometricDevice.Status.RETIRED:
        raise ServerAddressError(
            "This device is retired. It no longer accepts data or commands."
        )
    if device.last_seen_at is None:
        # Refused before anything is written: no attempt row, no probe, no
        # command waiting for a device that is not there to collect it.
        raise ServerAddressError(NEVER_CONNECTED_MESSAGE)

    target = parse_address(raw_address)
    saved = current_address(device)
    if saved is not None and saved.matches(target):
        raise ServerAddressError(f"{device.name} already uses {target.text}.")

    if in_flight_change(device) is not None:
        raise ServerAddressError(
            "A server address change is already in progress for this device. "
            "Wait for it to finish before starting another."
        )
    if _recent_probe_count(device) >= MAX_PROBES_PER_HOUR:
        raise ServerAddressError(
            "Too many address checks for this device in the last hour. Wait a "
            "while before trying again."
        )

    token = secrets.token_urlsafe(32)
    try:
        with transaction.atomic():
            attempt = DeviceServerAddressChange.all_objects.create(
                company_id=device.company_id,
                device=device,
                created_by=actor,
                updated_by=actor,
                previous_scheme=saved.scheme if saved else "",
                previous_host=saved.host if saved else "",
                previous_port=saved.port if saved else None,
                new_scheme=target.scheme,
                new_host=target.host,
                new_port=target.port,
                status=DeviceServerAddressChange.Status.CHECKING,
                probe_token=token,
                probe_started_at=timezone.now(),
            )
    except IntegrityError:
        # The partial unique index caught a second change that slipped past the
        # check above. Same answer, just from the database.
        raise ServerAddressError(
            "A server address change is already in progress for this device."
        ) from None

    ok, reason = _run_probe(target, token, fetch=fetch)
    attempt.probe_completed_at = timezone.now()
    # Single use: the token is spent whether the probe passed or failed.
    attempt.probe_token = ""

    if not ok:
        attempt.status = DeviceServerAddressChange.Status.UNREACHABLE
        attempt.failure_code = "probe_failed"
        attempt.failure_reason = reason
        attempt.save(update_fields=[
            "status", "probe_completed_at", "probe_token",
            "failure_code", "failure_reason", "updated_at",
        ])
        return attempt

    _queue_to_device(attempt, target, actor)
    return attempt


def _queue_to_device(attempt, target, actor):
    """Step 2. Only ever called after the probe passed."""
    from devices.services.commands import queue_server_address

    entry, port_entry, error = queue_server_address(
        device=attempt.device, address=target, requested_by=actor
    )
    now = timezone.now()
    if entry is None:
        attempt.status = DeviceServerAddressChange.Status.UNREACHABLE
        attempt.failure_code = "queue_failed"
        attempt.failure_reason = error or "The change could not be queued."
        attempt.save(update_fields=[
            "status", "probe_completed_at", "probe_token",
            "failure_code", "failure_reason", "updated_at",
        ])
        return

    attempt.status = DeviceServerAddressChange.Status.QUEUED
    attempt.command_id = entry["id"]
    attempt.port_command_id = port_entry["id"]
    attempt.command_queued_at = now
    attempt.deadline_at = now + _deadline_window(attempt.device)
    attempt.save(update_fields=[
        "status", "probe_completed_at", "probe_token", "command_id",
        "port_command_id", "command_queued_at", "deadline_at", "updated_at",
    ])


def _attempt_for_command(device, command_id):
    """The attempt that queued ``command_id``, in flight or just finished.

    Not restricted to in-flight attempts on purpose. A device that is already
    at the target address confirms on its very next request, which can beat
    its own command result back to us — measured on the real hardware. The
    evidence of what the device said is worth keeping either way, so it is
    matched by command id rather than by the attempt still being open.
    """
    return (
        DeviceServerAddressChange.all_objects.filter(device=device)
        .filter(models.Q(command_id=command_id) | models.Q(port_command_id=command_id))
        .order_by("-created_at")
        .first()
    )


def note_command_delivered(*, device, command_ids):
    """Step 3: the device fetched its queued commands.

    Matched by command id rather than by the attempt still being open, so the
    handover is recorded even when the confirmation beat it — which happens
    whenever the device is already at the target address, because the
    confirming request and the command handover are the same round trip.
    Only an attempt that is still moving has its status advanced.
    """
    attempt = None
    for command_id in command_ids:
        attempt = _attempt_for_command(device, command_id)
        if attempt is not None:
            break
    if attempt is None or attempt.command_delivered_at is not None:
        return attempt

    attempt.command_delivered_at = timezone.now()
    fields = ["command_delivered_at", "updated_at"]
    if attempt.status == DeviceServerAddressChange.Status.QUEUED:
        attempt.status = DeviceServerAddressChange.Status.DELIVERED
        fields.append("status")
    attempt.save(update_fields=fields)
    return attempt


def note_command_result(*, device, command_id, return_code):
    """Step 3b: the device reported what it did with the command.

    A result is not proof the address works — the device says "OK" and only
    then tries the new address — so this never confirms. It is recorded so the
    two timeout outcomes can be explained accurately.

    Only the host command decides. The port command travels with it and its
    result is not separately interesting: a wrong port shows up as the device
    never arriving, which the deadline already handles.
    """
    attempt = _attempt_for_command(device, command_id)
    if attempt is None or attempt.command_id != command_id:
        return None

    attempt.command_acknowledged_at = timezone.now()
    attempt.command_return_code = str(return_code)[:32]
    fields = ["command_acknowledged_at", "command_return_code", "updated_at"]
    rejected = False

    # Read the verdict off the stored row before touching it. A device that is
    # already at the target address confirms on its next request, which can
    # beat its own command result back to us, and a finished attempt must not
    # be re-opened by a late acknowledgement.
    if attempt.is_in_flight:
        if str(return_code).strip() not in ("0", ""):
            # The device refused it outright. Nothing changed on the device,
            # and there is no reason to make anyone wait for the deadline.
            attempt.status = DeviceServerAddressChange.Status.NOT_APPLIED
            attempt.failure_code = "device_rejected"
            attempt.failure_reason = (
                f"The device rejected the change with code {return_code}. Its "
                "server address is unchanged."
            )
            fields += ["status", "failure_code", "failure_reason"]
            rejected = True
        elif attempt.status in (
            DeviceServerAddressChange.Status.QUEUED,
            DeviceServerAddressChange.Status.DELIVERED,
        ):
            attempt.status = DeviceServerAddressChange.Status.ACKNOWLEDGED
            fields.append("status")

    attempt.save(update_fields=fields)
    if rejected:
        _audit_outcome(attempt)
    return attempt


def note_device_request(*, device, request):
    """Step 4: a request arrived from the device. Which address did it use?

    This is the only trustworthy evidence that the device switched. The device
    saying "OK" to the command is not: it acknowledges before it tries. So the
    address is taken from the request that actually arrived.

    Called from every authenticated device endpoint, and cheap when no change
    is running.
    """
    attempt = _advanceable_change(device)
    if attempt is None:
        return None

    seen = observed_address(request)
    if seen is None:
        return attempt

    now = timezone.now()
    if seen.matches(_target(attempt)):
        return _confirm(attempt, seen, now)

    previous = _previous(attempt)
    if previous is None or seen.matches(previous):
        # Either the device is demonstrably still on the old address, or it had
        # no proven address yet and is checking in from somewhere that is not
        # the target. Both mean "the change has not taken effect".
        attempt.seen_at_old_address_at = now
        attempt.save(update_fields=["seen_at_old_address_at", "updated_at"])
    return attempt


def _audit_outcome(attempt, *, actor_type="device"):
    """Record how an attempt ended, wherever it ended.

    Steps 3-5 finish on a device request or on somebody reading the status,
    not on the administrator's own POST, so the view cannot write these. The
    actor is the device or the system rather than a user, because no user was
    present when the outcome was decided.
    """
    from auditlog.models import AuditLog

    AuditLog.objects.create(
        company_id=attempt.company_id,
        actor_user=None,
        actor_type=actor_type,
        action=f"device.server_address.{attempt.status}",
        object_app="devices",
        object_model="deviceserveraddresschange",
        object_id=str(attempt.pk),
        object_display=str(attempt)[:255],
        before_data={"address": _text_or_blank(_previous(attempt))},
        after_data={
            "address": _target(attempt).text,
            "status": attempt.status,
            "reason": attempt.failure_reason,
            "requested_by": getattr(attempt.created_by, "email", "") or "",
        },
    )


def _text_or_blank(address):
    return address.text if address else ""


def previous_address_text(attempt):
    """The address this attempt started from, or "" when there was none."""
    return _text_or_blank(_previous(attempt))


def _confirm(attempt, seen, now):
    """Step 4 succeeded: save the new address as the device's current one."""
    with transaction.atomic():
        attempt.status = DeviceServerAddressChange.Status.CONFIRMED
        attempt.seen_at_new_address_at = now
        attempt.failure_code = ""
        attempt.failure_reason = ""
        attempt.save(update_fields=[
            "status", "seen_at_new_address_at", "failure_code",
            "failure_reason", "updated_at",
        ])
        BiometricDevice.all_objects.filter(pk=attempt.device_id).update(
            server_scheme=seen.scheme,
            server_host=seen.host,
            server_port=seen.port,
        )
    _audit_outcome(attempt)
    logger.info(
        "Device %s confirmed at its new server address %s",
        attempt.device_id, seen.text,
    )
    return attempt


def refresh(attempt):
    """Evaluate the deadline. Called whenever a status is read.

    Keeping the timeout here rather than in a scheduled job is deliberate: the
    only thing that changes an attempt is a device request or somebody looking
    at it, and both paths come through this module.
    """
    if attempt is None or not attempt.is_in_flight:
        return attempt
    if attempt.deadline_at is None or timezone.now() < attempt.deadline_at:
        return attempt

    previous = _previous(attempt)
    if attempt.seen_at_old_address_at is not None:
        attempt.status = DeviceServerAddressChange.Status.NOT_APPLIED
        attempt.failure_code = "still_on_old_address"
        attempt.failure_reason = (
            "The device kept checking in at the old address, so it never "
            "applied the change. Nothing on the device was altered."
        )
    else:
        attempt.status = DeviceServerAddressChange.Status.LOST
        attempt.failure_code = "device_silent"
        attempt.failure_reason = (
            "The device stopped checking in and has not appeared at the new "
            "address. It has most likely switched to an address it cannot "
            "reach, which only the terminal can undo."
        )
    attempt.save(update_fields=[
        "status", "failure_code", "failure_reason", "updated_at",
    ])
    _audit_outcome(attempt, actor_type="system")
    logger.warning(
        "Device %s address change to %s ended as %s",
        attempt.device_id, attempt.new_host, attempt.status,
    )
    return attempt


def cancel(*, attempt, actor):
    """Abandon an attempt that has not been sent to the device.

    Only possible while the probe is still running. Once the command is queued
    the device may already have taken it, and pretending otherwise would be a
    lie about the state of the hardware.
    """
    assert_may_manage_devices(actor, attempt.company_id)
    if attempt.status != DeviceServerAddressChange.Status.CHECKING:
        raise ServerAddressError(
            "This change has already been sent to the device and cannot be "
            "called back. Wait for it to finish."
        )
    attempt.status = DeviceServerAddressChange.Status.CANCELLED
    attempt.probe_token = ""
    attempt.updated_by = actor
    attempt.save(update_fields=["status", "probe_token", "updated_by", "updated_at"])
    return attempt


# ------------------------------------------------------------------- the UI


def recovery_instructions(attempt):
    """Exactly what to type back into the terminal after a lost device.

    Named values, not "restore the previous setting": whoever reads this is
    standing at a device that can no longer be reached from here, and the old
    address is the one thing the software still knows and they may not.
    """
    previous = _previous(attempt)
    if previous is None:
        return None
    return {
        "menu_path": "Menu → Comm. → Cloud Server Settings → Server Address",
        "address": previous.host,
        "port": str(previous.port),
        "scheme": previous.scheme,
        "domain_name_setting": "On" if not _looks_like_ip(previous.host) else "Off",
        "full": previous.text,
    }


def _looks_like_ip(host):
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


#: Status -> what to tell the administrator. Written as sentences about the
#: hardware, because "failed" tells nobody whether the terminal was touched.
STATUS_MESSAGES = {
    DeviceServerAddressChange.Status.CHECKING: (
        "Checking the new address…"
    ),
    DeviceServerAddressChange.Status.UNREACHABLE: (
        "Address not reachable — nothing was changed"
    ),
    DeviceServerAddressChange.Status.QUEUED: (
        "Waiting for the device to pick up the change"
    ),
    DeviceServerAddressChange.Status.DELIVERED: (
        "Device received the change — waiting for it to connect at the new "
        "address"
    ),
    DeviceServerAddressChange.Status.ACKNOWLEDGED: (
        "Device received the change — waiting for it to connect at the new "
        "address"
    ),
    DeviceServerAddressChange.Status.CONFIRMED: (
        "Connected at the new address — saved"
    ),
    DeviceServerAddressChange.Status.NOT_APPLIED: (
        "Device did not apply the change — still on the old address"
    ),
    DeviceServerAddressChange.Status.LOST: (
        "Device is not reachable at the new address — set it back on the device"
    ),
    DeviceServerAddressChange.Status.CANCELLED: (
        "Cancelled before anything was sent to the device"
    ),
}

TERMINAL_STATUSES = (
    DeviceServerAddressChange.Status.UNREACHABLE,
    DeviceServerAddressChange.Status.CONFIRMED,
    DeviceServerAddressChange.Status.NOT_APPLIED,
    DeviceServerAddressChange.Status.LOST,
    DeviceServerAddressChange.Status.CANCELLED,
)


def next_check_in_at(device, attempt):
    """When the device is next expected to poll, from its push interval.

    An estimate, and labelled as one in the UI: a device that is offline will
    simply not appear. It is still worth showing, because "waiting" with no
    sense of how long is the thing that makes people power-cycle hardware.
    """
    if attempt.command_queued_at is None:
        return None
    interval = int((device.settings or {}).get("push_interval_seconds") or 10)
    last_seen = device.last_seen_at or attempt.command_queued_at
    return max(last_seen, attempt.command_queued_at) + timedelta(seconds=interval)


def status_payload(device, attempt):
    """Everything the status panel needs, as plain JSON-safe values."""
    if attempt is None:
        return {
            "active": False,
            "status": "",
            "message": "",
            "saved_address": (
                current_address(device).text if current_address(device) else ""
            ),
        }

    attempt = refresh(attempt)
    target = _target(attempt)
    previous = _previous(attempt)
    payload = {
        "active": attempt.is_in_flight,
        "status": attempt.status,
        "message": STATUS_MESSAGES.get(attempt.status, attempt.get_status_display()),
        "new_address": target.text,
        "previous_address": previous.text if previous else "",
        "saved_address": (
            current_address(device).text if current_address(device) else ""
        ),
        "reason": attempt.failure_reason,
        "sent_to_device": attempt.was_sent_to_device,
        "requested_at": attempt.created_at.isoformat(),
        "requested_by": getattr(attempt.created_by, "email", "") or "",
        "deadline_at": (
            attempt.deadline_at.isoformat() if attempt.deadline_at else ""
        ),
        "finished": attempt.status in TERMINAL_STATUSES,
        "recovery": None,
    }
    if attempt.is_in_flight and attempt.command_queued_at is not None:
        expected = next_check_in_at(device, attempt)
        payload["next_check_in_at"] = expected.isoformat() if expected else ""
    if attempt.status == DeviceServerAddressChange.Status.LOST:
        payload["recovery"] = recovery_instructions(attempt)
    return payload
