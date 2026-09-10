"""Root-only catalogue routes, mounted under ``/platform/catalogue/``.

Their own namespace rather than ``organization``: these screens are the
platform operator's, not a tenant's, and sharing a namespace with the company
routes would make it ambiguous which surface a reversed URL belongs to (and
raises Django's urls.W005).
"""
from django.urls import path

from organization import catalogue_views as views

app_name = "catalogue"

urlpatterns = [
    path("departments/", views.department_list, name="department_list"),
    path("departments/add/", views.department_create, name="department_create"),
    path("departments/<int:pk>/edit/", views.department_edit, name="department_edit"),
    path(
        "departments/<int:pk>/status/",
        views.department_status,
        name="department_status",
    ),
    path("designations/", views.designation_list, name="designation_list"),
    path("designations/add/", views.designation_create, name="designation_create"),
    path(
        "designations/<int:pk>/edit/",
        views.designation_edit,
        name="designation_edit",
    ),
    path(
        "designations/<int:pk>/status/",
        views.designation_status,
        name="designation_status",
    ),
]
