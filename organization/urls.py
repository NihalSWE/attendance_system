"""Company organization routes. Feature pages live in their domain app.

The root's own department and designation routes live in
organization/catalogue_urls.py under their own namespace, because they belong
to the platform operator, not a tenant.
"""
from django.urls import path

from organization import (
    access_views,
    adoption_views,
    employee_detail_views,
    employee_edit_views,
    employee_views,
    views,
)

app_name = "organization"

urlpatterns = [
    path("branches/", views.branch_list, name="branch_list"),
    path("branches/add/", views.branch_create, name="branch_create"),
    path("branches/<int:pk>/edit/", views.branch_edit, name="branch_edit"),
    path("branches/<int:pk>/status/", views.branch_status, name="branch_status"),

    path("departments/", adoption_views.adoption_list, name="adoption_list"),
    path("departments/add/", adoption_views.adoption_create, name="adoption_create"),
    path("departments/copy/", adoption_views.adoption_copy, name="adoption_copy"),
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

    # Who has which access, per branch (A12). Branch managers reach these too.
    path("access/", access_views.access_list, name="access"),
    path("access/<int:employee_id>/", access_views.access_person, name="access_person"),

    path("employees/new/", employee_views.employee_create, name="employee_create"),
    path("employees/<int:pk>/", employee_detail_views.employee_detail, name="employee_detail"),
    path("employees/<int:pk>/edit/", employee_edit_views.employee_edit, name="employee_edit"),
    path("employees/<int:pk>/end/", employee_detail_views.employee_end, name="employee_end"),
    # Feed the dependent branch -> department -> designation selects.
    path(
        "employees/new/departments/",
        employee_views.branch_departments,
        name="employee_branch_departments",
    ),
    path(
        "employees/new/designations/",
        employee_views.department_designations,
        name="employee_department_designations",
    ),
]
