"""Version 1 of the API: ``/api/v1/...``. Each phase adds its area here."""

from django.urls import include, path

app_name = "v1"

urlpatterns = [
    path("", include("api.v1.system.urls")),
]
