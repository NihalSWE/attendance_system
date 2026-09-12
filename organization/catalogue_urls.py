"""Root-only department and designation routes, mounted under ``/platform/``.

Their own namespace rather than ``organization``: these screens are the
platform operator's, not a tenant's, and sharing a namespace with the company
routes would make it ambiguous which surface a reversed URL belongs to (and
raises Django's urls.W005).

Mounted alongside ``tenants.urls`` rather than under a prefix of their own,
because the address bar is something the operator reads: /platform/departments/
says what the page is, /platform/catalogue/departments/ says what we called the
table. Django tries ``tenants.urls`` first and falls through to here, and
neither defines the other's paths.
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
