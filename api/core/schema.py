"""Tells Swagger (drf-spectacular) how the API authenticates, so the schema
lists it instead of warning that it cannot."""

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class ApiAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "api.core.auth.ApiAuthentication"
    name = "sessionOrApiKey"

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "description": (
                "Apps: Authorization: Bearer <access token> plus the signature headers "
                "X-Key-Id, X-Timestamp, X-Nonce, X-Signature. API keys: the signature headers "
                "only. Browser frontends: the session cookies plus X-CSRFToken. See "
                "/api/docs/authentication/."),
        }
