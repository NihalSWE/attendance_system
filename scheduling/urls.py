from django.urls import path

from scheduling import views

app_name = "scheduling"

# URL names carry a schedule/shift/holiday prefix because the sidebar's active
# tag matches on the bare name, and "list" or "edit" would collide with other
# apps.
urlpatterns = [
    path("", views.schedule_overview, name="schedule_overview"),
    path("settings/", views.attendance_settings_edit, name="attendance_settings_edit"),

    path("shifts/add/", views.shift_create, name="shift_create"),
    path("shifts/<int:pk>/edit/", views.shift_edit, name="shift_edit"),
    path("shifts/<int:pk>/status/", views.shift_status, name="shift_status"),

    path("weekly-offs/add/", views.weekly_off_create, name="weekly_off_create"),
    path("weekly-offs/<int:pk>/stop/", views.weekly_off_end, name="weekly_off_end"),

    path("holidays/", views.holiday_list, name="holiday_list"),
    path("holidays/add/", views.holiday_create, name="holiday_create"),
    path("holidays/<int:pk>/edit/", views.holiday_edit, name="holiday_edit"),
    path("holidays/<int:pk>/cancel/", views.holiday_cancel, name="holiday_cancel"),
]
