"""Company organization routes. Feature pages live in their domain app."""
from django.urls import path

from organization import views

app_name = "organization"

urlpatterns = [
    path("branches/", views.branch_list, name="branch_list"),
    path("branches/add/", views.branch_create, name="branch_create"),
    path("branches/<int:pk>/edit/", views.branch_edit, name="branch_edit"),
    path("branches/<int:pk>/status/", views.branch_status, name="branch_status"),
]
