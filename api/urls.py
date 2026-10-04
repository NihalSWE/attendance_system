"""Everything under /api/: the versioned API, its documentation site, Swagger."""

from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from api.core.permissions import Public
from api.docs_site import views as docs

app_name = "api"

urlpatterns = [
    path("v1/", include("api.v1.urls")),
    # The OpenAPI schema (behind the Swagger link and the Postman collection).
    path("v1/schema/", SpectacularAPIView.as_view(
        permission_classes=[Public], authentication_classes=[]), name="schema"),
    path("swagger/", SpectacularSwaggerView.as_view(
        url_name="api:schema", permission_classes=[Public], authentication_classes=[]),
        name="swagger"),
    # Our own documentation site (docs/api/00-PLAN.md, Part 4).
    path("docs/", docs.guide, {"page": "getting-started"}, name="docs"),
    path("docs/authentication/", docs.guide, {"page": "authentication"},
         name="docs_authentication"),
    path("docs/postman/", docs.guide, {"page": "postman"}, name="docs_postman"),
    path("docs/postman.json", docs.postman, name="docs_postman_collection"),
    path("docs/security/", docs.guide, {"page": "security"}, name="docs_security"),
    path("docs/conventions/", docs.guide, {"page": "conventions"}, name="docs_conventions"),
    path("docs/changelog/", docs.guide, {"page": "changelog"}, name="docs_changelog"),
    path("docs/errors/", docs.errors, name="docs_errors"),
    path("docs/rate-limits/", docs.rate_limits, name="docs_rate_limits"),
    path("docs/endpoint/<slug:endpoint_id>/", docs.endpoint_page, name="docs_endpoint"),
]
