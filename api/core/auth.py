"""Who is calling - checked on every request (docs/api/00-PLAN.md, 2.1).

Three ways in, tried in this order:

1. **API key** (machines): ``X-Key-Id: ak_…`` and a signature made with the
   key's secret. No Authorization header.
2. **App session** (mobile, desktop): ``Authorization: Bearer <access token>``
   *and* a signature made with the session's signing secret
   (``X-Key-Id: ses_…``).
3. **Web session** (a browser frontend): the access token in a Secure,
   HttpOnly cookie, plus the CSRF token on anything that changes data.

Anything else is anonymous - and every endpoint but the public ones refuses
anonymous callers.
"""

from django.conf import settings
from django.middleware.csrf import CsrfViewMiddleware
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication

from api.core import signing
from api.core.crypto import decrypt, digest
from api.core.errors import ApiError
from api.core.logins import ADMIN_ROLES, still_valid
from api.core.network import client_ip
from api.models import ApiKey, ApiSession

ACCESS_COOKIE = "api_access"
REFRESH_COOKIE = "api_refresh"
TOUCH_EVERY_SECONDS = 60


class _CsrfCheck(CsrfViewMiddleware):
    def _reject(self, request, reason):
        return reason


def enforce_csrf(request):
    raw = getattr(request, "_request", request)      # Django's request under DRF's
    check = _CsrfCheck(lambda req: None)
    check.process_request(raw)
    reason = check.process_view(raw, None, (), {})
    if reason:
        raise ApiError("csrf_failed")


class ApiAuthentication(BaseAuthentication):
    def authenticate(self, request):
        meta = request.META
        key_id = (meta.get("HTTP_X_KEY_ID") or "").strip()
        bearer = meta.get("HTTP_AUTHORIZATION", "")
        if key_id.startswith("ak_") and not bearer:
            return self._api_key(request, key_id)
        if bearer.startswith("Bearer "):
            return self._app(request, bearer[7:].strip())
        if request.COOKIES.get(ACCESS_COOKIE):
            return self._web(request, request.COOKIES[ACCESS_COOKIE])
        if signing.has_headers(request):
            raise ApiError("missing_signature_headers",
                           "A signed request needs an API key (X-Key-Id: ak_…) or, for an "
                           "app session, the Authorization header too.")
        return None

    def authenticate_header(self, request):
        # Makes DRF answer 401 (not 403) when credentials are missing.
        return 'Bearer realm="api"'

    # --- the three ways in ---

    def _session(self, token, client_types):
        session = (ApiSession.objects.select_related("user")
                   .filter(access_hash=digest(token)).exclude(access_hash="").first())
        if session is None or session.client_type not in client_types:
            raise ApiError("invalid_token")
        still_valid(session)
        if session.access_expires_at is None or session.access_expires_at < timezone.now():
            raise ApiError("token_expired")
        return session

    def _touch(self, request, session):
        now = timezone.now()
        if session.last_used_at is None or (now - session.last_used_at).total_seconds() > TOUCH_EVERY_SECONDS:
            ApiSession.objects.filter(pk=session.pk).update(last_used_at=now,
                                                            last_ip=client_ip(request))

    def _app(self, request, token):
        session = self._session(token, (ApiSession.ClientType.MOBILE,
                                        ApiSession.ClientType.DESKTOP))
        if not signing.has_headers(request):
            raise ApiError("signature_required")
        if (request.META.get("HTTP_X_KEY_ID") or "").strip() != session.public_id:
            raise ApiError("invalid_signature",
                           "X-Key-Id must be this session's id (the session_id from login).")
        signing.verify(request, session.public_id, [decrypt(session.signing_secret_encrypted)])
        return self._done(request, session)

    def _web(self, request, token):
        session = self._session(token, (ApiSession.ClientType.WEB,))
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            enforce_csrf(request)
        return self._done(request, session)

    def _done(self, request, session):
        self._touch(request, session)
        request.api_session = session
        request.api_caller = f"user:{session.user_id}"
        return (session.user, session)

    def _api_key(self, request, key_id):
        key = ApiKey.objects.select_related("company", "created_by").filter(public_id=key_id).first()
        if key is None:
            raise ApiError("invalid_api_key")
        now = timezone.now()
        if key.revoked_at is not None:
            raise ApiError("api_key_revoked")
        if key.expires_at is not None and key.expires_at < now:
            raise ApiError("api_key_expired")
        ip = client_ip(request)
        if key.allowed_ips and ip not in key.allowed_ips:
            raise ApiError("ip_not_allowed")
        secrets = [decrypt(key.secret_encrypted)]
        if key.previous_secret_encrypted and key.previous_valid_until and key.previous_valid_until > now:
            secrets.append(decrypt(key.previous_secret_encrypted))
        signing.verify(request, key.public_id, secrets)
        creator = key.created_by
        # A key can never do more than the person who made it - and stops when
        # that person no longer manages the company.
        from accounts.services import get_active_memberships

        if (creator is None or not creator.is_active or not get_active_memberships(creator)
                .filter(company_id=key.company_id, role__in=ADMIN_ROLES).exists()):
            raise ApiError("api_key_revoked",
                           "The person who created this key no longer manages the company.")
        ApiKey.objects.filter(pk=key.pk).update(last_used_at=now, last_used_ip=ip)
        request.api_key = key
        request.api_caller = f"key:{key.public_id}"
        return (creator, key)


def cookie_options():
    return {
        "httponly": True,
        "secure": bool(getattr(settings, "API_COOKIE_SECURE", not settings.DEBUG)),
        "samesite": getattr(settings, "API_COOKIE_SAMESITE", "Strict"),
    }
