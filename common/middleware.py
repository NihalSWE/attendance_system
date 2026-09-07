"""Tenant context middleware.

Translates an authenticated HTTP request into a tenant context so that
``Model.objects`` on TenantOwned models is automatically scoped for the whole
request. Deliberately thin: the "which company may this user act in?" rule lives
in accounts.services so workers and a future FastAPI layer reuse it.

Must be listed AFTER django.contrib.auth's AuthenticationMiddleware, because it
depends on request.user being resolved.

The context is always cleared in a ``finally`` block. That is not optional:
threads and connections are reused between requests, so a leaked context would
let the next request read the previous tenant's data.
"""

from accounts.services import ACTIVE_COMPANY_SESSION_KEY, resolve_active_company_id
from common.tenant import clear_current_company_id, set_current_company_id


class TenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        session = getattr(request, "session", None)
        requested_company_id = (
            session.get(ACTIVE_COMPANY_SESSION_KEY) if session is not None else None
        )
        company_id = resolve_active_company_id(
            getattr(request, "user", None), requested_company_id
        )

        # Expose it on the request for views/templates that need the active tenant.
        request.company_id = company_id

        # Set unconditionally (None for anonymous) so the reset always restores a
        # clean state, and so an unauthenticated request fails loud rather than
        # inheriting whatever the previous request on this thread left behind.
        token = set_current_company_id(company_id)
        try:
            return self.get_response(request)
        finally:
            clear_current_company_id(token)
