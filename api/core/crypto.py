"""Secrets for logins and API keys (docs/api/00-PLAN.md, Part 2).

- Tokens are random values; only their SHA-256 is stored, so a copy of the
  database cannot be used to log in.
- Signing secrets (an app session's, an API key's) must be read back to check
  a signature, so they are stored encrypted (Fernet, a key derived from the
  project's SECRET_KEY), never in plain text, never logged.
"""

import base64
import hashlib
import hmac
import secrets

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _cipher():
    digest = hashlib.sha256(b"api-secrets:" + settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value):
    return _cipher().encrypt(value.encode()).decode("ascii") if value else ""


def decrypt(token):
    if not token:
        return ""
    try:
        return _cipher().decrypt(token.encode("ascii")).decode()
    except (InvalidToken, ValueError):
        return ""


def new_token(prefix, size=32):
    """A random value with a readable prefix, e.g. ``at_…`` for an access token."""
    return f"{prefix}_{secrets.token_urlsafe(size)}"


def digest(value):
    return hashlib.sha256((value or "").encode()).hexdigest()


def same(a, b):
    """Constant-time comparison, so timing tells nothing."""
    return hmac.compare_digest((a or "").encode(), (b or "").encode())
