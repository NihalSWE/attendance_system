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
from devices.models import BiometricDevice, DeviceMessage
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
    """Authenticate the caller, or return an HttpResponse to send back."""
    serial = request.GET.get("SN", "")
    device = authenticate_device(
        serial_number=serial,
        presented_key_id=request.GET.get("key_id", ""),
        presented_secret=request.GET.get("key", ""),
    )
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
        return _text(adapter.handshake_response(device=device, stamp=stamp))

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

    We do not push commands yet, so this always answers ``OK``. It is still a
    useful liveness signal, so the device's last-seen time is updated.
    """
    try:
        device = _resolve_device(request)
    except DeviceAuthenticationError as exc:
        logger.warning("Rejected device command poll: %s", exc)
        return _unauthorized()

    BiometricDevice.all_objects.filter(pk=device.pk).update(
        last_seen_at=timezone.now(), ip_address_last_seen=_client_ip(request)
    )
    return _text("OK")


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
    return _text("OK")
