"""Every documented endpoint, found from the API's own URLs.

Walks ``api.v1.urls``; for each view and each HTTP method that carries an
``@endpoint`` declaration, records the method and the public path
(``/api/v1/employees/{id}``). The documentation site and the documentation
test both read this list, so a route without documentation cannot hide.
"""

import copy
import re

from django.urls import URLPattern, URLResolver

from api.core.docs import AREAS

METHODS = ("get", "post", "put", "patch", "delete")
_CONVERTER = re.compile(r"<(?:\w+:)?(\w+)>")


def _public_path(prefix, pattern):
    return "/api/v1/" + _CONVERTER.sub(r"{\1}", prefix + str(pattern)).lstrip("^").rstrip("$")


def routes():
    """``(view class, path)`` for every API route, documented or not."""
    from api.v1 import urls

    found = []

    def walk(patterns, prefix):
        for pattern in patterns:
            if isinstance(pattern, URLResolver):
                walk(pattern.url_patterns, prefix + str(pattern.pattern))
            elif isinstance(pattern, URLPattern):
                cls = getattr(pattern.callback, "cls", None)
                if cls is not None:
                    found.append((cls, _public_path(prefix, pattern.pattern)))

    walk(urls.urlpatterns, "")
    return found


def endpoints():
    """Every documented endpoint, in menu order, each with method and path."""
    order = {area: index for index, (area, _) in enumerate(AREAS)}
    found = []
    for cls, path in routes():
        for method in METHODS:
            handler = getattr(cls, method, None)
            doc = getattr(handler, "_api_doc", None)
            if doc is None:
                continue
            doc = copy.copy(doc)
            doc.method, doc.path, doc.view = method.upper(), path, cls
            found.append(doc)
    return sorted(found, key=lambda d: order.get(d.area, len(order)))


def by_id(endpoint_id):
    return next((doc for doc in endpoints() if doc.id == endpoint_id), None)


def undocumented():
    """``(view, method, path)`` for every route method that has no declaration."""
    missing = []
    for cls, path in routes():
        for method in METHODS:
            handler = getattr(cls, method, None)
            if handler is not None and getattr(handler, "_api_doc", None) is None:
                missing.append((cls, method.upper(), path))
    return missing
