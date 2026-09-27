"""Company organization routes. Feature pages live in their domain app.

The root's own department and designation routes live in
organization/catalogue_urls.py under their own namespace, because they belong
to the platform operator, not a tenant.
"""
from django.urls import path

from organization import (
    company_views,
    access_views,
    adoption_views,
    employee_detail_views,
    employee_records_views,
    employee_actions_views,
    employee_edit_views,
    employee_views,
    import_views,
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
    path("designations/", adoption_views.designation_list, name="designation_list"),
    path("designations/add/", adoption_views.designation_create, name="designation_create"),
    path(
        "designations/<int:pk>/edit/",
        adoption_views.designation_edit,
        name="designation_edit",
    ),
    path(
        "designations/<int:pk>/status/",
        adoption_views.designation_status,
        name="designation_status",
    ),

    # Who has which access, per branch (A12). Branch managers reach these too.
    path("access/", access_views.access_list, name="access"),
    path("access/<int:employee_id>/", access_views.access_person, name="access_person"),

    path("employees/new/", employee_views.employee_create, name="employee_create"),
    # Bulk import: upload, preview, confirm (nothing is written until confirmed).
    path("employees/import/", import_views.employee_import, name="employee_import"),
    path(
        "employees/import/confirm/",
        import_views.employee_import_confirm,
        name="employee_import_confirm",
    ),
    path(
        "employees/import/demo/",
        import_views.employee_import_demo,
        name="employee_import_demo",
    ),
    path("company/", company_views.company_profile, name="company_profile"),
    path("email/", company_views.mail_settings, name="mail_settings"),
    path("employees/<int:pk>/", employee_detail_views.employee_detail, name="employee_detail"),
    path("employees/<int:pk>/edit/", employee_edit_views.employee_edit, name="employee_edit"),
    path("employees/<int:pk>/end/", employee_detail_views.employee_end, name="employee_end"),
    path("employees/<int:pk>/photo/", employee_detail_views.employee_photo, name="employee_photo"),
    path("employees/<int:pk>/photo/change/", employee_detail_views.employee_photo_change,
         name="employee_photo_change"),
    path("employees/<int:pk>/personal/", employee_detail_views.employee_personal,
         name="employee_personal"),
    # The Edit employee cards as profile modals (Ajay, 2026-09-27).
    path("employees/<int:pk>/profile/edit/", employee_detail_views.employee_profile_edit,
         name="employee_profile_edit"),
    path("employees/<int:pk>/line-manager/", employee_detail_views.employee_line_manager,
         name="employee_line_manager"),
    # Education, documents and device permissions on the profile (2026-09-27).
    path("employees/<int:pk>/education/", employee_records_views.employee_education_save,
         name="employee_education_add"),
    path("employees/<int:pk>/education/<int:row>/",
         employee_records_views.employee_education_save, name="employee_education_edit"),
    path("employees/<int:pk>/education/<int:row>/remove/",
         employee_records_views.employee_education_remove, name="employee_education_remove"),
    path("employees/<int:pk>/documents/", employee_records_views.employee_document_add,
         name="employee_document_add"),
    path("employees/<int:pk>/documents/<int:row>/", employee_records_views.employee_document,
         name="employee_document"),
    path("employees/<int:pk>/documents/<int:row>/remove/",
         employee_records_views.employee_document_remove, name="employee_document_remove"),
    # The profile's actions (2026-09-27).
    path("employees/<int:pk>/leave/", employee_actions_views.employee_leave,
         name="employee_leave"),
    path("employees/<int:pk>/late/", employee_actions_views.employee_late,
         name="employee_late"),
    path("employees/<int:pk>/active/", employee_actions_views.employee_active,
         name="employee_active"),
    path("employees/<int:pk>/overtime/", employee_actions_views.employee_overtime,
         name="employee_overtime"),
    path("employees/<int:pk>/reports-to/", employee_actions_views.employee_reports,
         name="employee_reports"),
    path("employees/<int:pk>/devices/<int:enrollment>/",
         employee_records_views.employee_device_permission,
         name="employee_device_permission"),
    path("employees/<int:pk>/reports/", employee_detail_views.employee_report_visibility,
         name="employee_report_visibility"),
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
