"""Request signing - one format for app sessions and API keys
(docs/api/00-PLAN.md, 2.2; docs/api/02-authentication.md).

A signed request carries four headers:

    X-Key-Id:    the API key ("ak_…") or the app session ("ses_…")
    X-Timestamp: the current time, Unix seconds (e.g. 1791100800)
    X-Nonce:     a random value of 16-64 letters, digits, - or _, never reused
    X-Signature: hex( HMAC-SHA256( secret, canonical request ) )

The canonical request is six lines joined by "\\n":

    METHOD        upper case, e.g. POST
    PATH          the path exactly as sent, e.g. /api/v1/auth/me
    QUERY         the query string's "name=value" pairs as sent, sorted, joined
                  by "&" (empty when there is none)
    TIMESTAMP     the X-Timestamp value
    NONCE         the X-Nonce value
    BODY_SHA256   hex SHA-256 of the exact body bytes (of "" when empty)

Refused: a timestamp more than 5 minutes from the server's clock, a nonce
already used by the same key or session (a replay), or a signature that does
not match (the request was changed, or the wrong secret was used).
"""

import datetime
import hashlib
import hmac
import logging
import random
import re
import time

from django.db import IntegrityError, transaction
from django.utils import timezone

from api.core.crypto import same
from api.core.errors import ApiError

#: Refused signatures and replays, for the server log (docs/api/00-PLAN.md 2.7):
#: who (the session or key id), from where, on what - never the signature,
#: token or secret. Not the audit table: an attacker could flood it.
security_log = logging.getLogger("api.security")

WINDOW_SECONDS = 300
NONCE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
NONCE_KEEP_SECONDS = 600

HEADERS = ("HTTP_X_KEY_ID", "HTTP_X_TIMESTAMP", "HTTP_X_NONCE", "HTTP_X_SIGNATURE")


def canonical(method, path, query_string, timestamp, nonce, body):
    pairs = sorted(part for part in (query_string or "").split("&") if part)
    return "\n".join([
        method.upper(), path, "&".join(pairs), str(timestamp), nonce,
        hashlib.sha256(body or b"").hexdigest(),
    ])


def sign(secret, text):
    return hmac.new(secret.encode(), text.encode(), hashlib.sha256).hexdigest()


def has_headers(request):
    return any(request.META.get(name) for name in HEADERS)


def parts(request):
    meta = request.META
    return {
        "key_id": (meta.get("HTTP_X_KEY_ID") or "").strip(),
        "timestamp": (meta.get("HTTP_X_TIMESTAMP") or "").strip(),
        "nonce": (meta.get("HTTP_X_NONCE") or "").strip(),
        "signature": (meta.get("HTTP_X_SIGNATURE") or "").strip().lower(),
    }


def request_canonical(request, timestamp, nonce):
    return canonical(request.method, request.path, request.META.get("QUERY_STRING", ""),
                     timestamp, nonce, request.body)


def verify(request, owner, secrets):
    """Check the signature against ``secrets`` (the current one, and during a
    key rotation the previous one) and record the nonce. Raises ``ApiError``."""
    found = parts(request)
    if not all(found.values()):
        raise ApiError("missing_signature_headers")
    if not found["timestamp"].isdigit() or abs(time.time() - int(found["timestamp"])) > WINDOW_SECONDS:
        raise ApiError("timestamp_out_of_range")
    if not NONCE_PATTERN.match(found["nonce"]):
        raise ApiError("missing_signature_headers",
                       "X-Nonce must be 16-64 letters, digits, - or _.")
    text = request_canonical(request, found["timestamp"], found["nonce"])
    if not any(secret and same(sign(secret, text), found["signature"]) for secret in secrets):
        _refused(request, owner, "invalid_signature")
        raise ApiError("invalid_signature")
    try:
        _use_nonce(owner, found["nonce"])
    except ApiError:
        _refused(request, owner, "replay_detected")
        raise


def _refused(request, owner, code):
    from api.core.network import client_ip

    security_log.warning("%s: %s from %s on %s %s", code, owner, client_ip(request),
                         request.method, request.path)


def _use_nonce(owner, nonce):
    from api.models import UsedNonce

    try:
        with transaction.atomic():
            UsedNonce.objects.create(owner=owner, nonce=nonce)
    except IntegrityError:
        raise ApiError("replay_detected") from None
    if random.random() < 0.02:       # now and then, forget nonces too old to matter
        cutoff = timezone.now() - datetime.timedelta(seconds=NONCE_KEEP_SECONDS)
        UsedNonce.objects.filter(created_at__lt=cutoff).delete()
