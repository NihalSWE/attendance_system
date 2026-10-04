"""The API documentation site (docs/api/00-PLAN.md, Part 4).

Everything on it comes from one source:
- guide pages: the hand-written Markdown in ``docs/api/`` (the same files the
  team reads in the repository);
- endpoint pages: each endpoint's ``@endpoint`` declaration, its serializers'
  fields and the error catalogue;
- the error reference: the error catalogue;
- rate limits: the API settings.

Public on purpose: documentation, not data. Nothing here reads a company's
records.
"""

import json
from pathlib import Path

import markdown
from django.conf import settings
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.safestring import mark_safe

from api.core import errors as catalogue
from api.core.docs import AREAS
from api.core.fields import count, tree
from api.core.pagination import StandardPagination
from api.core.registry import by_id, endpoints
from api.core.samples import samples
from api.core.throttling import SCOPES, rate_of
from api.docs_site import postman as postman_collection

DOCS_DIR = Path(settings.BASE_DIR) / "docs" / "api"

#: Guide pages: address key -> (menu title, Markdown file).
GUIDES = {
    "getting-started": ("Getting started", "01-getting-started.md"),
    "authentication": ("Logging in & signing", "02-authentication.md"),
    "conventions": ("Conventions", "03-conventions.md"),
    "security": ("Security", "06-security.md"),
    "postman": ("Testing with Postman", "07-postman.md"),
    "changelog": ("Changelog", "CHANGELOG.md"),
}


def _markdown(filename):
    path = DOCS_DIR / filename
    if not path.exists():
        raise Http404("No such page.")
    html = markdown.markdown(path.read_text(encoding="utf-8"),
                             extensions=["tables", "fenced_code", "toc", "sane_lists"])
    return mark_safe(html)  # our own files, from the repository


def _menu(current):
    """The left menu: start pages, every area's endpoints, reference pages."""
    docs = endpoints()
    sections = [{"title": "Start here", "items": [
        {"label": "Getting started", "url": reverse("api:docs"),
         "active": current == "getting-started"},
        {"label": "Logging in & signing", "url": reverse("api:docs_authentication"),
         "active": current == "authentication"},
        {"label": "Conventions", "url": reverse("api:docs_conventions"),
         "active": current == "conventions"},
        {"label": "Testing with Postman", "url": reverse("api:docs_postman"),
         "active": current == "postman"},
    ]}]
    for area, title in AREAS:
        items = [{"label": doc.title, "method": doc.method,
                  "url": reverse("api:docs_endpoint", args=[doc.id]),
                  "active": current == f"endpoint:{doc.id}"}
                 for doc in docs if doc.area == area]
        if items:
            if area == "start":
                sections[0]["items"].extend(items)
            else:
                sections.append({"title": title, "items": items})
    sections.append({"title": "Reference", "items": [
        {"label": "Error reference", "url": reverse("api:docs_errors"),
         "active": current == "errors"},
        {"label": "Rate limits", "url": reverse("api:docs_rate_limits"),
         "active": current == "rate-limits"},
        {"label": "Security", "url": reverse("api:docs_security"),
         "active": current == "security"},
        {"label": "Changelog", "url": reverse("api:docs_changelog"),
         "active": current == "changelog"},
        {"label": "Postman collection (download)", "url": reverse("api:docs_postman_collection"),
         "external": True},
        {"label": "Swagger (OpenAPI)", "url": reverse("api:swagger"), "external": True},
    ]})
    return sections


def guide(request, page):
    if page not in GUIDES:
        raise Http404("No such page.")
    title, filename = GUIDES[page]
    return render(request, "api/docs/guide.html", {
        "menu": _menu(page), "title": title, "content": _markdown(filename),
    })


def postman(request):
    """The Postman collection to import (the guide: Testing with Postman)."""
    base = request.build_absolute_uri("/").rstrip("/")
    data = postman_collection.collection(
        base, lambda doc: request.build_absolute_uri(reverse("api:docs_endpoint", args=[doc.id])))
    response = JsonResponse(data, json_dumps_params={"indent": 2, "ensure_ascii": False})
    response["Content-Disposition"] = 'attachment; filename="attendance-api.postman_collection.json"'
    return response


def errors(request):
    groups = []
    for group in catalogue.GROUPS:
        specs = [spec for spec in catalogue.CATALOGUE.values() if spec.group == group]
        if specs:
            groups.append({"title": group, "errors": [_error(spec) for spec in specs]})
    intro = _markdown("04-errors.md") if (DOCS_DIR / "04-errors.md").exists() else ""
    return render(request, "api/docs/errors.html", {
        "menu": _menu("errors"), "title": "Error reference", "intro": intro,
        "groups": groups,
        "shape": json.dumps(catalogue.spec("validation_error").example(), indent=2),
    })


def rate_limits(request):
    rows = [{"scope": scope, "rate": rate_of(scope), "meaning": meaning}
            for scope, meaning in SCOPES.items()]
    return render(request, "api/docs/rate_limits.html", {
        "menu": _menu("rate-limits"), "title": "Rate limits", "rows": rows,
    })


def _error(spec):
    return {"code": spec.code, "status": spec.status, "title": spec.title,
            "meaning": spec.meaning, "fix": spec.fix,
            "example": json.dumps(spec.example(), indent=2)}


def _paginated(nodes):
    """A list answer's envelope around the item fields."""
    return [
        {"name": "count", "type": "integer", "required": False, "choices": [], "details": [],
         "description": "How many records match, on all pages together.", "children": []},
        {"name": "next", "type": "string (url)", "required": False, "choices": [],
         "details": ["may be null"], "children": [],
         "description": "The address of the next page, or null on the last page."},
        {"name": "previous", "type": "string (url)", "required": False, "choices": [],
         "details": ["may be null"], "children": [],
         "description": "The address of the previous page, or null on the first page."},
        {"name": "results", "type": "array of objects", "required": False, "choices": [],
         "details": [], "children": nodes,
         "description": "This page's records, each with the fields below."},
    ]


def endpoint_page(request, endpoint_id):
    doc = by_id(endpoint_id)
    if doc is None:
        raise Http404("No such endpoint.")
    docs = endpoints()
    index = next(i for i, d in enumerate(docs) if d.id == doc.id)
    params = list(doc.params)
    if doc.paginated:
        from api.core.docs import QUERY, Param

        params += [Param(name, QUERY, kind, words, example=example)
                   for name, kind, words, example in StandardPagination.PARAMS]
    request_tree = tree(doc.request, "request")
    response_tree = tree(doc.response, "response")
    if doc.paginated:
        response_tree = _paginated(response_tree)
    scope = getattr(doc.view, "throttle_scope", None)
    return render(request, "api/docs/endpoint.html", {
        "menu": _menu(f"endpoint:{doc.id}"),
        "title": doc.title,
        "doc": doc,
        "params": params,
        "request_tree": request_tree,
        "request_count": count(request_tree),
        "response_tree": response_tree,
        "response_count": count(response_tree),
        "request_example": (json.dumps(doc.request_example, indent=2)
                            if doc.request_example is not None else ""),
        "response_example": (json.dumps(doc.response_example, indent=2)
                             if doc.response_example is not None else ""),
        "samples": samples(doc, request.build_absolute_uri("/")),
        "errors": [_error(catalogue.spec(code)) for code in doc.errors],
        "rate": {"scope": scope, "rate": rate_of(scope)} if scope else None,
        "previous": docs[index - 1] if index > 0 else None,
        "next": docs[index + 1] if index + 1 < len(docs) else None,
    })
