"""Rate limits: each endpoint names its kind (``throttle_scope``): public,
read or write - and later login and others. The rates are in settings
(REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]) and shown on each endpoint's page.

Counted per caller: the logged-in person or API key once phase 1 adds logins,
otherwise the address the request came from. Counts are kept in the database
cache (settings CACHES["api"]) so every server worker shares them.
"""

from django.core.cache import caches
from rest_framework.settings import api_settings
from rest_framework.throttling import SimpleRateThrottle

#: What each kind means, for the Rate limits page.
SCOPES = {
    "public": "Endpoints that need no login, such as ping. Counted per address.",
    "read": "Reading data: lists and single records.",
    "write": "Creating, changing and actions.",
}


def rate_of(scope):
    return api_settings.DEFAULT_THROTTLE_RATES.get(scope, "")


class ApiRateThrottle(SimpleRateThrottle):
    cache = caches["api"]

    def allow_request(self, request, view):
        self.scope = getattr(view, "throttle_scope", None)
        if not self.scope:
            return True
        self.rate = self.get_rate()
        self.num_requests, self.duration = self.parse_rate(self.rate)
        return super().allow_request(request, view)

    def get_rate(self):
        # Read live, not the copy DRF takes at import: a changed setting applies.
        return rate_of(self.scope) or None

    def get_cache_key(self, request, view):
        caller = getattr(request, "api_caller", None)
        ident = caller or self.get_ident(request)
        return f"api-throttle:{self.scope}:{ident}"
