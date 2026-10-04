"""API keys for machines (docs/api/00-PLAN.md, 2.4)."""

from rest_framework.response import Response

from api.core import keys
from api.core.docs import PATH, Param, endpoint
from api.core.errors import ApiError
from api.core.permissions import IsCompanyAdmin
from api.core.scopes import SCOPES
from api.core.views import ApiView
from api.models import ApiKey
from api.v1.keys import serializers as s

AREA = "auth"
ROLES = ["Company owner or administrator"]
ERRORS = ["not_authenticated", "invalid_token", "token_expired", "session_ended",
          "signature_required", "invalid_signature", "two_step_setup_required",
          "permission_denied", "validation_error", "rate_limited", "server_error"]
KEY_EXAMPLE = {"id": "ak_P2v9QmX3tL7c", "name": "IGL ERP",
               "scopes": ["attendance:read", "employees:read"], "allowed_ips": ["203.0.113.7"],
               "expires_at": None, "created_at": "2026-10-04T10:15:00+06:00",
               "created_by": "owner@example.com", "last_used_at": "2026-10-04T11:00:00+06:00",
               "last_used_ip": "203.0.113.7", "revoked": False, "old_secret_valid_until": None}
KEY_PARAM = Param("key_id", PATH, "string", "The key id (ak_…).", example="ak_P2v9QmX3tL7c")


def _key(request, key_id):
    key = ApiKey.objects.select_related("created_by").filter(
        company_id=request.company_id, public_id=key_id).first()
    if key is None:
        raise ApiError("not_found")
    return key


class KeyListView(ApiView):
    permission_classes = [IsCompanyAdmin]
    company_required = True
    throttle_scope = "read"

    @endpoint(
        id="api-keys-list", area=AREA, title="API keys",
        summary="The company's API keys (never their secrets).",
        what_it_does=["Lists every key, revoked ones included, newest first."],
        description="Shows each key's scopes, allow-list, expiry and when it was last used.",
        roles=ROLES, paginated=True, response=s.KeySerializer,
        response_example={"count": 1, "next": None, "previous": None, "results": [KEY_EXAMPLE]},
        errors=ERRORS,
    )
    def get(self, request):
        rows = list(ApiKey.objects.select_related("created_by")
                    .filter(company_id=request.company_id).order_by("-created_at"))
        return self.paginated(request, rows, s.KeySerializer)

    @endpoint(
        id="api-keys-create", area=AREA, title="Create an API key",
        summary="A new key for a machine; its secret is shown once.",
        what_it_does=["Creates a key with the scopes, allow-list and expiry given.",
                      "Answers the secret - the only time it is shown."],
        description=("Give the key only the scopes the machine needs. Put the id and the secret "
                     "on the machine (in secure storage, never in source code); it signs every "
                     "request with the secret (see Logging in & request signing). The key can "
                     "never do more than you can, and stops if you no longer manage the company."),
        roles=ROLES, response_status=201,
        request=s.KeyCreateSerializer, response=s.KeyWithSecretSerializer,
        request_example={"name": "IGL ERP", "scopes": ["attendance:read", "employees:read"],
                         "allowed_ips": ["203.0.113.7"], "expires_at": None},
        response_example={**KEY_EXAMPLE, "last_used_at": None, "last_used_ip": None,
                          "secret": "sk_tqZ8…"},
        errors=ERRORS + ["unknown_field"],
    )
    def post(self, request):
        data = s.KeyCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = dict(data.validated_data)
        values["scopes"] = sorted(values["scopes"])
        key, secret = keys.create(request, request.company_id, **values)
        return Response({**s.KeySerializer(key).data, "secret": secret}, status=201)


class KeyDetailView(ApiView):
    permission_classes = [IsCompanyAdmin]
    company_required = True
    throttle_scope = "write"

    @endpoint(
        id="api-keys-get", area=AREA, title="One API key",
        summary="One key's details (never its secret).",
        what_it_does=["Shows the key's scopes, allow-list, expiry and last use."],
        description="The secret cannot be shown again; rotate the key for a new one.",
        roles=ROLES, params=[KEY_PARAM], response=s.KeySerializer,
        response_example=KEY_EXAMPLE, errors=ERRORS + ["not_found"],
    )
    def get(self, request, key_id):
        return Response(s.KeySerializer(_key(request, key_id)).data)

    @endpoint(
        id="api-keys-change", area=AREA, title="Change an API key",
        summary="A new name, scopes, allow-list or expiry.",
        what_it_does=["Changes only the fields sent."],
        description="Scopes and the allow-list are replaced as a whole by what is sent.",
        roles=ROLES, params=[KEY_PARAM],
        request=s.KeyChangeSerializer, response=s.KeySerializer,
        request_example={"scopes": ["attendance:read"]},
        response_example={**KEY_EXAMPLE, "scopes": ["attendance:read"]},
        errors=ERRORS + ["not_found", "unknown_field"],
    )
    def patch(self, request, key_id):
        key = _key(request, key_id)
        data = s.KeyChangeSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        values = dict(data.validated_data)
        if "scopes" in values:
            values["scopes"] = sorted(values["scopes"])
        return Response(s.KeySerializer(keys.change(request, key, values)).data)

    @endpoint(
        id="api-keys-revoke", area=AREA, title="Revoke an API key",
        summary="The key stops working at once.",
        what_it_does=["Revokes the key: every request with it is refused from now on."],
        description="It stays in the list as revoked, for the record. It cannot be undone.",
        roles=ROLES, params=[KEY_PARAM], response=s.KeySerializer,
        response_example={**KEY_EXAMPLE, "revoked": True}, errors=ERRORS + ["not_found"],
    )
    def delete(self, request, key_id):
        return Response(s.KeySerializer(keys.revoke(request, _key(request, key_id))).data)


class KeyRotateView(ApiView):
    permission_classes = [IsCompanyAdmin]
    company_required = True
    throttle_scope = "write"

    @endpoint(
        id="api-keys-rotate", area=AREA, title="Rotate an API key's secret",
        summary="A new secret; the old one keeps working for a while.",
        what_it_does=["Makes a new secret and answers it - shown once.",
                      "Keeps the old secret working for grace_hours (default 1, at most 24)."],
        description=("Rotate when a secret may have been seen, or now and then as good "
                     "practice. Put the new secret on the machine before the grace period ends."),
        roles=ROLES, params=[KEY_PARAM],
        request=s.RotateSerializer, response=s.KeyWithSecretSerializer,
        request_example={"grace_hours": 1},
        response_example={**KEY_EXAMPLE, "old_secret_valid_until": "2026-10-04T12:15:00+06:00",
                          "secret": "sk_Wn4r…"},
        errors=ERRORS + ["not_found", "conflict", "unknown_field"],
    )
    def post(self, request, key_id):
        data = s.RotateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        key, secret = keys.rotate(request, _key(request, key_id), data.validated_data["grace_hours"])
        return Response({**s.KeySerializer(key).data, "secret": secret})


class ScopeListView(ApiView):
    permission_classes = [IsCompanyAdmin]
    company_required = True
    throttle_scope = "read"

    @endpoint(
        id="api-keys-scopes", area=AREA, title="API key scopes",
        summary="Every scope a key can be given, and what it allows.",
        what_it_does=["Lists the scopes, for choosing a key's."],
        description="Each endpoint's page says which scope a key needs to call it.",
        roles=ROLES, paginated=True, response=s.ScopeSerializer,
        response_example={"count": 17, "next": None, "previous": None, "results": [
            {"name": "employees:read", "description": "Read employees and their profiles."}]},
        errors=ERRORS,
    )
    def get(self, request):
        rows = [{"name": name, "description": words} for name, words in SCOPES.items()]
        return self.paginated(request, rows, s.ScopeSerializer)
