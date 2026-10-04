"""Every error the API can return, in one place (docs/api/00-PLAN.md, Part 4.2).

The catalogue is the single source for three things that must always agree:
the error the API actually answers with, the list on each endpoint's page, and
the error reference page that shows them all. An endpoint names the codes it
can return; the documentation test refuses any code that is not here.

Every error answers in one shape:

    {"error": {"code": "validation_error",
               "message": "Some fields are not valid.",
               "fields": {"email": ["Enter a valid email address."]},
               "reference": "5f0c2a9e1b7d"}}

``fields`` only when particular fields are at fault; ``reference`` always - it
is also the response's ``X-Request-Id`` and is written to the server log, so a
report can be matched to what happened.
"""

import logging
import uuid
from dataclasses import dataclass

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import RequestDataTooBig
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import exceptions as drf
from rest_framework.response import Response

logger = logging.getLogger("api")


@dataclass(frozen=True)
class ErrorSpec:
    code: str
    status: int
    title: str
    meaning: str
    fix: str
    group: str
    example_message: str = ""
    example_fields: dict | None = None

    def example(self):
        body = {"code": self.code, "message": self.example_message or self.title}
        if self.example_fields:
            body["fields"] = self.example_fields
        body["reference"] = "5f0c2a9e1b7d"
        return {"error": body}


#: The order groups appear in on the error reference page.
GROUPS = ("Request", "Authentication", "Permission", "Limits", "Server")

_SPECS = (
    # --- Request ---------------------------------------------------------------
    ErrorSpec(
        "bad_request", 400, "The request could not be read",
        "The body is not valid JSON, or the request is malformed.",
        "Send a JSON body with Content-Type: application/json, and check it is "
        "valid JSON (a JSON linter will show where it breaks).",
        "Request", "JSON parse error - Expecting ',' delimiter: line 3 column 5."),
    ErrorSpec(
        "validation_error", 422, "Some fields are not valid",
        "The request was read, but one or more values are missing or not "
        "allowed. The same rules apply as in the panels.",
        "Read error.fields: it names each field at fault and why. Fix those "
        "values and send the request again.",
        "Request", "Some fields are not valid.",
        {"name": ["This field is required."]}),
    ErrorSpec(
        "unknown_field", 422, "A field is not accepted",
        "The body contains a field this endpoint does not take - often a typo "
        "in a field name.",
        "Remove the field, or correct its name to one listed under Request "
        "parameters on the endpoint's page.",
        "Request", "Some fields are not accepted.",
        {"nmae": ["This field is not accepted here."]}),
    ErrorSpec(
        "not_found", 404, "Not found",
        "There is no such record - or it belongs to another company, which "
        "is never revealed.",
        "Check the ID in the path. IDs come from the list endpoint of the "
        "same kind of record.",
        "Request"),
    ErrorSpec(
        "method_not_allowed", 405, "This method is not allowed here",
        "The path exists but does not accept this HTTP method (for example a "
        "DELETE on a read-only resource).",
        "Use one of the methods listed on the endpoint's page.",
        "Request", 'Method "DELETE" not allowed.'),
    ErrorSpec(
        "unsupported_media_type", 415, "Unsupported content type",
        "The body is not sent as JSON (or as multipart, where an endpoint "
        "takes files).",
        "Send Content-Type: application/json, or multipart/form-data for "
        "uploads.",
        "Request", 'Unsupported media type "text/plain" in request.'),
    ErrorSpec(
        "payload_too_large", 413, "The request is too large",
        "The body is larger than the server accepts.",
        "Send less at once - page through large lists, and keep uploads "
        "under the size stated on the endpoint.",
        "Request"),
    ErrorSpec(
        "conflict", 409, "This cannot be done in the current state",
        "The request is valid, but the record's state does not allow it - "
        "for example a change reaching into a finalised payroll.",
        "Read the message: it says what stands in the way and, usually, "
        "what to do first.",
        "Request", "Salary up to 31 Aug 2026 is already finalised."),
    ErrorSpec(
        "idempotency_key_reused", 422, "Idempotency-Key reused for a different request",
        "This Idempotency-Key was already used, within the last 24 hours, for "
        "a request with a different body or path.",
        "Use a new Idempotency-Key for every new action; reuse a key only to "
        "retry exactly the same request.",
        "Request"),
    # --- Authentication --------------------------------------------------------
    ErrorSpec(
        "not_authenticated", 401, "Not logged in",
        "This endpoint needs a login (or a signed API key), and none was sent "
        "or it was not valid.",
        "Log in first and send the credentials on every request, as described "
        "under Logging in & request signing.",
        "Authentication", "Authentication credentials were not provided."),
    # --- Permission ------------------------------------------------------------
    ErrorSpec(
        "permission_denied", 403, "Not allowed",
        "You are logged in, but your role (or your API key's scopes) does not "
        "allow this. The same rules apply as in the panels.",
        "Ask the company owner or administrator for the permission, or use an "
        "API key with the scope this endpoint lists.",
        "Permission", "You may not manage that branch."),
    # --- Limits ----------------------------------------------------------------
    ErrorSpec(
        "rate_limited", 429, "Too many requests",
        "Too many requests in a short time, from this login, API key or "
        "address. Each kind of endpoint has its own limit.",
        "Wait the number of seconds in the Retry-After header, then try "
        "again. See Rate limits for each kind of endpoint.",
        "Limits", "Request was throttled. Expected available in 42 seconds."),
    # --- Server ----------------------------------------------------------------
    ErrorSpec(
        "server_error", 500, "Something went wrong on our side",
        "An unexpected error. Nothing about it is shown here; it is written to "
        "the server log under the reference.",
        "Try again in a moment. If it keeps happening, send the reference to "
        "support - it identifies exactly this request.",
        "Server", "Something went wrong on our side."),
)

CATALOGUE = {spec.code: spec for spec in _SPECS}


def spec(code):
    return CATALOGUE[code]


class ApiError(drf.APIException):
    """Raise a catalogue error from a view: ``raise ApiError("conflict", msg)``."""

    def __init__(self, code, message=None, fields=None):
        self.spec = CATALOGUE[code]
        self.status_code = self.spec.status
        self.message = message or self.spec.title
        self.fields = fields
        super().__init__(self.message)


def new_reference():
    return uuid.uuid4().hex[:12]


def error_response(code, message=None, fields=None, *, reference=None, headers=None):
    error_spec = CATALOGUE[code]
    reference = reference or new_reference()
    body = {"code": code, "message": message or error_spec.title}
    if fields:
        body["fields"] = fields
    body["reference"] = reference
    response = Response({"error": body}, status=error_spec.status, headers=headers)
    response["X-Request-Id"] = reference
    return response


def _as_lists(detail):
    """DRF / Django error details as {field: [message, ...]}."""
    if isinstance(detail, dict):
        return {str(key): _messages(value) for key, value in detail.items()}
    return {"non_field_errors": _messages(detail)}


def _messages(value):
    if isinstance(value, (list, tuple)):
        found = []
        for item in value:
            found.extend(_messages(item) if isinstance(item, (list, tuple, dict)) else [str(item)])
        return found
    if isinstance(value, dict):
        return [f"{key}: {', '.join(_messages(item))}" for key, item in value.items()]
    return [str(value)]


def _has_code(detail, code):
    if isinstance(detail, dict):
        return any(_has_code(value, code) for value in detail.values())
    if isinstance(detail, (list, tuple)):
        return any(_has_code(value, code) for value in detail)
    return getattr(detail, "code", None) == code


def exception_handler(exc, context):
    """Every exception an API view raises becomes one catalogue error."""
    if isinstance(exc, ApiError):
        return error_response(exc.spec.code, exc.message, exc.fields)
    if isinstance(exc, drf.ValidationError):
        unknown = _has_code(exc.detail, "unknown_field")
        fields = _as_lists(exc.detail)
        general = fields.pop("non_field_errors", None)
        return error_response(
            "unknown_field" if unknown else "validation_error",
            general[0] if general and not fields else None,
            fields or None,
        )
    if isinstance(exc, DjangoValidationError):
        # A service refused: its words, field by field, as the panels show them.
        if hasattr(exc, "message_dict"):
            fields = {key: list(value) for key, value in exc.message_dict.items()}
            general = fields.pop("__all__", None)
            return error_response("validation_error",
                                  general[0] if general and not fields else None,
                                  fields or None)
        return error_response("validation_error", " ".join(exc.messages))
    if isinstance(exc, drf.ParseError):
        return error_response("bad_request", str(exc.detail))
    if isinstance(exc, (drf.NotAuthenticated, drf.AuthenticationFailed)):
        response = error_response("not_authenticated", str(exc.detail))
        auth_header = getattr(exc, "auth_header", None)
        if auth_header:
            response["WWW-Authenticate"] = auth_header
        return response
    if isinstance(exc, (drf.PermissionDenied, DjangoPermissionDenied)):
        message = str(exc.detail) if isinstance(exc, drf.PermissionDenied) else (
            str(exc) or None)
        if message == drf.PermissionDenied.default_detail:
            message = None
        return error_response("permission_denied", message)
    if isinstance(exc, (drf.NotFound, Http404)):
        return error_response("not_found")
    if isinstance(exc, drf.MethodNotAllowed):
        return error_response("method_not_allowed", str(exc.detail))
    if isinstance(exc, drf.UnsupportedMediaType):
        return error_response("unsupported_media_type", str(exc.detail))
    if isinstance(exc, drf.Throttled):
        headers = {"Retry-After": str(int(exc.wait))} if exc.wait is not None else None
        return error_response("rate_limited", str(exc.detail), headers=headers)
    if isinstance(exc, RequestDataTooBig):
        return error_response("payload_too_large")
    # Anything else is ours: logged with the reference, nothing shown.
    reference = new_reference()
    logger.exception("API error %s in %s", reference,
                     getattr(context.get("view"), "__class__", type(None)).__name__)
    return error_response("server_error", reference=reference)
