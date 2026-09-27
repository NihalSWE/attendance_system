from django.urls import path

from leaves import policy_views, views

app_name = "leaves"

# Names carry a leave_ prefix: the sidebar's active tag matches bare names.
urlpatterns = [
    path("", views.leave_list, name="leave_list"),
    path("record/", views.leave_record, name="leave_record"),
    path("<int:pk>/cancel/", views.leave_cancel, name="leave_cancel"),
    path("<int:pk>/change/", views.leave_amend, name="leave_amend"),

    path("types/", views.leave_type_list, name="leave_type_list"),
    path("types/add/", views.leave_type_create, name="leave_type_create"),
    path("types/defaults/", views.leave_type_defaults, name="leave_type_defaults"),
    path("types/<int:pk>/edit/", views.leave_type_edit, name="leave_type_edit"),
    path("types/<int:pk>/status/", views.leave_type_status, name="leave_type_status"),

    # Leave policies, versions and balances (Phase E, 2026-09-27).
    path("policies/", policy_views.policy_list, name="leave_policy_list"),
    path("policies/add/", policy_views.policy_create, name="leave_policy_create"),
    path("policies/<int:pk>/", policy_views.policy_detail, name="leave_policy_detail"),
    path("policies/<int:pk>/edit/", policy_views.policy_edit, name="leave_policy_edit"),
    path("policies/<int:pk>/status/", policy_views.policy_status, name="leave_policy_status"),
    path("policies/<int:pk>/versions/add/", policy_views.version_create,
         name="leave_policy_version_create"),
    path("policies/versions/<int:pk>/change/", policy_views.version_edit,
         name="leave_policy_version_edit"),
    path("policies/versions/<int:pk>/remove/", policy_views.version_delete,
         name="leave_policy_version_delete"),
    path("balances/", policy_views.balance_list, name="leave_balance_list"),
]
