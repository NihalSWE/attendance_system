"""The base every API endpoint inherits.

- Every response carries ``X-Request-Id`` (an error's ``reference`` is the
  same value), so any answer can be matched to the server log.
- ``Idempotency-Key``: a create or action sent again with the same key, by the
  same caller, within 24 hours, returns the first answer instead of running
  twice. A key reused for a different request is refused.
"""

import datetime
import hashlib
import uuid

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from api.core.company import resolve_company
from api.core.errors import ApiError
from api.models import IdempotencyRecord

IDEMPOTENT_METHODS = ("POST", "PUT", "PATCH", "DELETE")
KEEP_FOR = datetime.timedelta(hours=24)
HEADER = "HTTP_IDEMPOTENCY_KEY"


class _Replay(Exception):
    def __init__(self, record):
        self.record = record


def caller_of(request):
    """Who is calling, for idempotency and rate limits: the person or API key."""
    caller = getattr(request, "api_caller", None)
    if caller:
        return caller
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return f"user:{user.pk}"
    return None


class ApiView(APIView):
    """The base of every endpoint.

    ``company_required = True``: the request acts for one company (an API
    key's, or the person's - ``X-Company`` when they have several); it is in
    ``request.company_id`` and set as the tenant context for the request, as
    the panels' code expects. ``allowed_during_two_step_setup = True``: the
    endpoint stays open to an owner or administrator who still has to set up
    two-step login (everything else answers two_step_setup_required).
    """

    company_required = False
    allowed_during_two_step_setup = False

    def perform_authentication(self, request):
        super().perform_authentication(request)
        request.company_id = None
        session = getattr(request, "api_session", None)
        if (session is not None and session.needs_two_step_setup
                and not self.allowed_during_two_step_setup):
            raise ApiError("two_step_setup_required")
        if self.company_required and request.user is not None:
            key = getattr(request, "api_key", None)
            request.company_id = key.company_id if key is not None else resolve_company(
                request, request.user)
            from common.tenant import set_current_company_id

            self._tenant_token = set_current_company_id(request.company_id)

    def paginated(self, request, rows, serializer):
        """A paged answer: {"count", "next", "previous", "results"}."""
        from api.core.pagination import StandardPagination

        paginator = StandardPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(serializer(page, many=True).data)

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self._idempotency = None
        key = (request.META.get(HEADER) or "").strip()[:128]
        caller = caller_of(request)
        if not key or not caller or request.method not in IDEMPOTENT_METHODS:
            return
        digest = hashlib.sha256(
            request.method.encode() + b" " + request.get_full_path().encode() + b"\n"
            + (request.body or b"")).hexdigest()
        record = IdempotencyRecord.objects.filter(
            caller=caller, key=key, created_at__gte=timezone.now() - KEEP_FOR).first()
        if record is not None:
            if record.request_hash != digest:
                raise ApiError("idempotency_key_reused")
            raise _Replay(record)
        self._idempotency = (caller, key, digest)

    def handle_exception(self, exc):
        if isinstance(exc, _Replay):
            response = Response(exc.record.response_body, status=exc.record.status_code)
            response["Idempotent-Replay"] = "true"
            return response
        return super().handle_exception(exc)

    def finalize_response(self, request, response, *args, **kwargs):
        token = getattr(self, "_tenant_token", None)
        if token is not None:
            from common.tenant import clear_current_company_id

            clear_current_company_id(token)
            self._tenant_token = None
        response = super().finalize_response(request, response, *args, **kwargs)
        if not response.has_header("X-Request-Id"):
            response["X-Request-Id"] = uuid.uuid4().hex[:12]
        pending = getattr(self, "_idempotency", None)
        if pending and response.status_code < 500 and not response.has_header("Idempotent-Replay"):
            caller, key, digest = pending
            IdempotencyRecord.objects.filter(caller=caller, key=key).delete()   # an expired one
            try:
                with transaction.atomic():
                    IdempotencyRecord.objects.create(
                        caller=caller, key=key, request_hash=digest,
                        status_code=response.status_code,
                        response_body=getattr(response, "data", None))
            except IntegrityError:
                pass          # a parallel retry stored it first: that answer stands
        return response
