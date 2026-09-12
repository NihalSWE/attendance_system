"""Device-facing ingestion endpoints (ZKTeco ADMS / TA Push).

These are called by a device, not a browser, so they are CSRF-exempt and carry
their own device authentication instead. The exemption is scoped to these views
only — no CSRF setting is weakened anywhere else
(DEVICE_INTEGRATION_HANDOFF.md section 2b).

Responses are plain text in the exact shape the firmware expects. An
unrecognised device gets a 401 with no detail: the reply must not help an
unregistered caller discover which serials exist.
"""

import logging

from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from devices.adapters import UnknownAdapterError, get_adapter
from devices.adapters.base import ParsedMessage
from devices.models import BiometricDevice, DeviceMessage, DeviceServerAddressChange
from devices.services import server_address
from devices.services.commands import take_pending_commands
from devices.services.ingestion import (
    DeviceAuthenticationError,
    authenticate_device,
    ingest,
)

logger = logging.getLogger(__name__)


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        # A tunnel (ngrok) puts the real client first in the list.
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _text(body, status=200):
    return HttpResponse(body, status=status, content_type="text/plain; charset=utf-8")


def _unauthorized():
    return _text("Unauthorized", status=401)


def _resolve_device(request):
    """Authenticate the caller, or return an HttpResponse to send back.

    Every authenticated device request is also the evidence that decides
    whether a server-address change worked: the address the device dialled is
    on this request and nowhere else. Recording it here means no endpoint can
    forget to, and it costs a single indexed lookup when no change is running.
    """
    serial = request.GET.get("SN", "")
    device = authenticate_device(
        serial_number=serial,
        presented_key_id=request.GET.get("key_id", ""),
        presented_secret=request.GET.get("key", ""),
    )
    server_address.note_device_request(device=device, request=request)
    return device


def _body_text(request):
    encoding = request.encoding or "utf-8"
    return request.body.decode(encoding, errors="replace"), encoding


@csrf_exempt
@require_http_methods(["GET", "POST"])
def cdata(request):
    """Handshake (GET) and data upload (POST) — the main ADMS endpoint."""
    try:
        device = _resolve_device(request)
    except DeviceAuthenticationError as exc:
        logger.warning("Rejected device push: %s", exc)
        return _unauthorized()

    try:
        adapter = get_adapter(device.device_model.vendor.adapter_key)
    except UnknownAdapterError as exc:
        logger.error("Device %s: %s", device.pk, exc)
        return _text("Server configuration error", status=500)

    if request.method == "GET":
        # Registration/handshake: the device asks how to behave. No message is
        # stored for this; it carries no evidence.
        stamp = request.GET.get("Stamp", "0")
        return _text(
            adapter.handshake_response(
                device=device,
                stamp=stamp,
                pushver=request.GET.get("pushver", ""),
            )
        )

    raw_body, encoding = _body_text(request)
    parsed = adapter.parse(
        path_name="cdata_upload",
        query=request.GET,
        body_text=raw_body,
        serial_number=device.serial_number,
    )

    result = ingest(
        device=device,
        parsed=parsed,
        raw_body=raw_body,
        source_ip=_client_ip(request),
        headers=request.headers,
        content_type=request.content_type or "",
        encoding=encoding,
    )

    accepted = result.extraction.accepted_count if result.extraction else 0
    if result.is_replay:
        # Already held: acknowledge with the original record count so the
        # device advances its pointer instead of re-sending forever.
        accepted = result.message.record_count or 0

    return _text(adapter.acknowledgement(parsed=parsed, accepted_count=accepted))


@csrf_exempt
@require_http_methods(["GET"])
def getrequest(request):
    """The device polling for queued commands.

    This is the only channel back to a push-mode device: it asks, we answer.
    Any commands an administrator queued are handed over here, one per line;
    with nothing queued the device gets ``OK`` and does nothing. Either way the
    poll is a liveness signal, so last-seen is updated.
    """
    try:
        device = _resolve_device(request)
    except DeviceAuthenticationError as exc:
        logger.warning("Rejected device command poll: %s", exc)
        return _unauthorized()

    BiometricDevice.all_objects.filter(pk=device.pk).update(
        last_seen_at=timezone.now(), ip_address_last_seen=_client_ip(request)
    )

    body, issued = take_pending_commands(device)
    if issued:
        logger.info(
            "Issued %d command(s) to device %s: %s",
            len(issued), device.serial_number, [e["key"] for e in issued],
        )
        return _text(body)
    return _text("OK")


@csrf_exempt
@require_http_methods(["POST"])
def registry(request):
    """PushSDK 3.x registration.

    A 3.x device posts its full capability block here and waits for a
    ``RegistryCode``. Until it gets one it repeats the POST indefinitely and
    never begins transmitting attendance data, so this endpoint is a
    precondition for any punch arriving at all.

    The posted block is durable evidence (firmware version, capacities, network
    configuration), so it is stored before the reply is sent.
    """
    try:
        device = _resolve_device(request)
    except DeviceAuthenticationError as exc:
        logger.warning("Rejected device registration: %s", exc)
        return _unauthorized()

    try:
        adapter = get_adapter(device.device_model.vendor.adapter_key)
    except UnknownAdapterError as exc:
        logger.error("Device %s: %s", device.pk, exc)
        return _text("Server configuration error", status=500)

    raw_body, encoding = _body_text(request)
    parsed = adapter.parse(
        path_name="registry",
        query=request.GET,
        body_text=raw_body,
        serial_number=device.serial_number,
    )
    ingest(
        device=device,
        parsed=parsed,
        raw_body=raw_body,
        source_ip=_client_ip(request),
        headers=request.headers,
        content_type=request.content_type or "",
        encoding=encoding,
    )
    _record_device_info(device, parsed)
    return _text(adapter.registry_response(device=device))


@csrf_exempt
@require_http_methods(["GET", "POST"])
def push(request):
    """PushSDK 3.x transmission-parameter request, sent after registration."""
    try:
        device = _resolve_device(request)
    except DeviceAuthenticationError as exc:
        logger.warning("Rejected device push-options request: %s", exc)
        return _unauthorized()

    try:
        adapter = get_adapter(device.device_model.vendor.adapter_key)
    except UnknownAdapterError as exc:
        logger.error("Device %s: %s", device.pk, exc)
        return _text("Server configuration error", status=500)

    BiometricDevice.all_objects.filter(pk=device.pk).update(
        last_seen_at=timezone.now(), ip_address_last_seen=_client_ip(request)
    )
    return _text(
        adapter.push_response(device=device, stamp=request.GET.get("Stamp", "0"))
    )


def _record_device_info(device, parsed):
    """Persist firmware facts the device reported, for the sync-health screen.

    Only diagnostic fields are written. Nothing here changes identity,
    authorization or any evidence row.
    """
    info = (parsed.payload_json or {}).get("device_info") or {}
    if not info:
        return
    firmware = info.get("FirmVer", "")[:64]
    settings = dict(device.settings or {})
    settings.update(
        {
            "device_name": info.get("~DeviceName", ""),
            "firmware_version": info.get("FirmVer", ""),
            "push_version": info.get("PushVersion", ""),
            "device_type": info.get("DeviceType", ""),
            "max_att_log_count": info.get("~MaxAttLogCount", ""),
            "max_user_count": info.get("~MaxUserCount", ""),
            "face_supported": info.get("FaceFunOn", ""),
            "fingerprint_supported": info.get("FingerFunOn", ""),
            "comm_type": info.get("CommType", ""),
            "device_ip": info.get("IPAddress", ""),
        }
    )
    BiometricDevice.all_objects.filter(pk=device.pk).update(
        firmware_version=firmware or device.firmware_version,
        settings=settings,
        last_seen_at=timezone.now(),
    )


@csrf_exempt
@require_http_methods(["GET", "POST"])
def capture(request, tail=""):
    """Catch-all for any other /iclock/ path the firmware uses.

    The documented ADMS paths are not the whole story: firmware varies, and a
    404 during commissioning teaches us nothing about what the device actually
    sends. This stores the request verbatim as an unknown-type message so it
    can be read in the message log, then acknowledges.

    Acknowledging an unparsed request is safe because the payload is durably
    stored first — no evidence is lost, and the row is visible as unknown. It
    is not safe to leave the device retrying forever against a 404 while its
    buffer fills.
    """
    try:
        device = _resolve_device(request)
    except DeviceAuthenticationError as exc:
        logger.warning("Rejected device request on /iclock/%s: %s", tail, exc)
        return _unauthorized()

    raw_body, encoding = _body_text(request)
    parsed = ParsedMessage(
        message_type=DeviceMessage.MessageType.UNKNOWN,
        payload_json={
            "path": request.path,
            "method": request.method,
            "query": dict(request.GET),
        },
        parse_error=(
            f"Unrecognised device path {request.path!r}; stored for inspection "
            "rather than parsed."
        ),
    )
    ingest(
        device=device,
        parsed=parsed,
        raw_body=raw_body,
        source_ip=_client_ip(request),
        headers=request.headers,
        content_type=request.content_type or "",
        encoding=encoding,
    )
    logger.info(
        "Captured unrecognised device request: %s %s", request.method, request.path
    )
    return _text("OK")


@csrf_exempt
@require_http_methods(["POST"])
def devicecmd(request):
    """Result of a command the device executed. Stored as evidence."""
    try:
        device = _resolve_device(request)
    except DeviceAuthenticationError as exc:
        logger.warning("Rejected device command result: %s", exc)
        return _unauthorized()

    try:
        adapter = get_adapter(device.device_model.vendor.adapter_key)
    except UnknownAdapterError as exc:
        logger.error("Device %s: %s", device.pk, exc)
        return _text("Server configuration error", status=500)

    raw_body, encoding = _body_text(request)
    parsed = adapter.parse(
        path_name="devicecmd",
        query=request.GET,
        body_text=raw_body,
        serial_number=device.serial_number,
    )
    ingest(
        device=device,
        parsed=parsed,
        raw_body=raw_body,
        source_ip=_client_ip(request),
        headers=request.headers,
        content_type=request.content_type or "",
        encoding=encoding,
    )
    _note_address_command_result(device, raw_body)
    return _text("OK")


def _note_address_command_result(device, raw_body):
    """Pass a ``ID=n&Return=c`` result to the server-address flow.

    The device answers every command in the same shape, so the id is what ties
    a result to the attempt that queued it. A result is never treated as proof
    the address works — the device acknowledges before it tries the new
    address — it only makes the eventual timeout explainable.
    """
    fields = {}
    for chunk in (raw_body or "").replace("\n", "&").split("&"):
        key, _, value = chunk.strip().partition("=")
        if key:
            fields[key.strip()] = value.strip()
    try:
        command_id = int(fields.get("ID", ""))
    except ValueError:
        return
    server_address.note_command_result(
        device=device, command_id=command_id, return_code=fields.get("Return", "")
    )


@csrf_exempt
@require_http_methods(["GET"])
def address_check(request):
    """Step 1 of a server address change: prove the address reaches *us*.

    Fetched by this same running software against the address an administrator
    just typed, before the device is told anything. The device never calls it.

    Answering with a signature derived from this deployment's SECRET_KEY is the
    whole point: a reachable address is not enough, because a device pointed at
    somebody else's working server is just as lost as one pointed at nothing.
    Only the software holding that key can produce the expected body, so a
    matching reply means the round trip came back here.

    The token is single-use and belongs to one attempt, so this cannot be
    replayed and is not an oracle for anything: with no live token it answers
    404 and reveals nothing about the deployment, the device or the company.
    """
    token = request.GET.get("token", "")
    if not token or len(token) > 64:
        return _text("Not found", status=404)

    attempt = DeviceServerAddressChange.all_objects.filter(
        probe_token=token, status=DeviceServerAddressChange.Status.CHECKING
    ).first()
    if attempt is None:
        return _text("Not found", status=404)

    return _text(server_address.probe_body(token))
