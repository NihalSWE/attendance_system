from django.urls import path

from leaves import views

app_name = "leaves"

# Names carry a leave_ prefix: the sidebar's active tag matches bare names.
urlpatterns = [
    path("", views.leave_list, name="leave_list"),
    path("record/", views.leave_record, name="leave_record"),
    path("<int:pk>/cancel/", views.leave_cancel, name="leave_cancel"),

    path("types/", views.leave_type_list, name="leave_type_list"),
    path("types/add/", views.leave_type_create, name="leave_type_create"),
    path("types/defaults/", views.leave_type_defaults, name="leave_type_defaults"),
    path("types/<int:pk>/edit/", views.leave_type_edit, name="leave_type_edit"),
    path("types/<int:pk>/status/", views.leave_type_status, name="leave_type_status"),
]
