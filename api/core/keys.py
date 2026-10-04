"""API keys for machines (docs/api/00-PLAN.md, 2.4).

Created by the company owner or administrator. The secret is shown once and
stored encrypted. Rotating keeps the old secret working for a grace period
(at most 24 hours); revoking stops the key at once.
"""

import datetime
import ipaddress

from django.db import transaction
from django.utils import timezone

from api.core import audit
from api.core.crypto import encrypt, new_token
from api.core.errors import ApiError
from api.core.scopes import SCOPES
from api.models import ApiKey

MAX_GRACE_HOURS = 24


def clean_scopes(scopes):
    unknown = sorted(set(scopes or []) - set(SCOPES))
    if unknown:
        raise ApiError("validation_error",
                       fields={"scopes": [f"Unknown scope: {', '.join(unknown)}."]})
    if not scopes:
        raise ApiError("validation_error", fields={"scopes": ["Give the key at least one scope."]})
    return sorted(set(scopes))


def clean_ips(ips):
    cleaned = []
    for ip in ips or []:
        try:
            cleaned.append(str(ipaddress.ip_address(str(ip).strip())))
        except ValueError:
            raise ApiError("validation_error",
                           fields={"allowed_ips": [f"{ip} is not an IP address."]}) from None
    return sorted(set(cleaned))


@transaction.atomic
def create(request, company_id, *, name, scopes, allowed_ips=None, expires_at=None):
    if expires_at is not None and expires_at <= timezone.now():
        raise ApiError("validation_error", fields={"expires_at": ["Choose a moment in the future."]})
    secret = new_token("sk", 32)
    key = ApiKey.objects.create(
        public_id=new_token("ak", 12), company_id=company_id, name=name.strip()[:120],
        secret_encrypted=encrypt(secret), scopes=clean_scopes(scopes),
        allowed_ips=clean_ips(allowed_ips), expires_at=expires_at, created_by=request.user,
    )
    audit.record(request, "api.key_created", user=request.user, company_id=company_id, obj=key,
                 data={"scopes": key.scopes, "allowed_ips": key.allowed_ips})
    return key, secret


@transaction.atomic
def change(request, key, values):
    before = {"name": key.name, "scopes": key.scopes, "allowed_ips": key.allowed_ips,
              "expires_at": key.expires_at.isoformat() if key.expires_at else None}
    if "name" in values:
        key.name = values["name"].strip()[:120]
    if "scopes" in values:
        key.scopes = clean_scopes(values["scopes"])
    if "allowed_ips" in values:
        key.allowed_ips = clean_ips(values["allowed_ips"])
    if "expires_at" in values:
        if values["expires_at"] is not None and values["expires_at"] <= timezone.now():
            raise ApiError("validation_error",
                           fields={"expires_at": ["Choose a moment in the future."]})
        key.expires_at = values["expires_at"]
    key.save()
    audit.record(request, "api.key_changed", user=request.user, company_id=key.company_id,
                 obj=key, data={"before": before})
    return key


@transaction.atomic
def rotate(request, key, grace_hours=1):
    if key.revoked_at is not None:
        raise ApiError("conflict", "This key is revoked.")
    grace_hours = max(0, min(int(grace_hours), MAX_GRACE_HOURS))
    secret = new_token("sk", 32)
    key.previous_secret_encrypted = key.secret_encrypted if grace_hours else ""
    key.previous_valid_until = (timezone.now() + datetime.timedelta(hours=grace_hours)
                                if grace_hours else None)
    key.secret_encrypted = encrypt(secret)
    key.save()
    audit.record(request, "api.key_rotated", user=request.user, company_id=key.company_id,
                 obj=key, data={"grace_hours": grace_hours})
    return key, secret


@transaction.atomic
def revoke(request, key):
    if key.revoked_at is None:
        key.revoked_at = timezone.now()
        key.previous_secret_encrypted = ""
        key.save(update_fields=["revoked_at", "previous_secret_encrypted"])
        audit.record(request, "api.key_revoked", user=request.user, company_id=key.company_id,
                     obj=key)
    return key
