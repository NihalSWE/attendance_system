"""Company organization routes. Feature pages live in their domain app.

Root-only catalogue routes live in organization/catalogue_urls.py under their
own namespace, because they belong to the platform operator, not a tenant.
"""
from django.urls import path

from organization import adoption_views, views

app_name = "organization"

urlpatterns = [
    path("branches/", views.branch_list, name="branch_list"),
    path("branches/add/", views.branch_create, name="branch_create"),
    path("branches/<int:pk>/edit/", views.branch_edit, name="branch_edit"),
    path("branches/<int:pk>/status/", views.branch_status, name="branch_status"),

    path("departments/", adoption_views.adoption_list, name="adoption_list"),
    path("departments/add/", adoption_views.adoption_create, name="adoption_create"),
    path(
        "departments/<int:pk>/edit/",
        adoption_views.adoption_edit,
        name="adoption_edit",
    ),
    path(
        "departments/<int:pk>/status/",
        adoption_views.adoption_status,
        name="adoption_status",
    ),
    # Feeds the dependent job-title multiselect.
    path(
        "departments/titles/",
        adoption_views.department_titles,
        name="department_titles",
    ),
]
