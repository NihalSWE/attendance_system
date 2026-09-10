"""Company organization routes. Feature pages live in their domain app.

Root-only catalogue routes live in organization/catalogue_urls.py under their
own namespace, because they belong to the platform operator, not a tenant.
"""
from django.urls import path

from organization import views

app_name = "organization"

urlpatterns = [
    path("branches/", views.branch_list, name="branch_list"),
    path("branches/add/", views.branch_create, name="branch_create"),
    path("branches/<int:pk>/edit/", views.branch_edit, name="branch_edit"),
    path("branches/<int:pk>/status/", views.branch_status, name="branch_status"),
]
