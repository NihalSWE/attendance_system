"""The caller's address.

Behind a reverse proxy (nginx on the live server) the connection comes from
the proxy, and the real address is in X-Forwarded-For. A client can write
anything into that header, so only the entries added by our own proxies are
trusted: ``API_PROXY_COUNT`` in .env says how many proxies stand in front
(1 for nginx). 0 - the default - uses the connection's own address.
"""

from django.conf import settings


def client_ip(request):
    proxies = int(getattr(settings, "API_PROXY_COUNT", 0) or 0)
    remote = request.META.get("REMOTE_ADDR")
    if proxies <= 0:
        return remote
    chain = [part.strip() for part in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")
             if part.strip()]
    if len(chain) >= proxies:
        return chain[-proxies]
    return remote
