from django.urls import path

from attendance import scan_request_views, views

app_name = "attendance"

urlpatterns = [
    path("", views.attendance_list, name="attendance_list"),
    path("late/", views.attendance_late, name="attendance_late"),
    path("now/", views.attendance_now, name="attendance_now"),
    path("calendar/", views.attendance_calendar, name="attendance_calendar"),
    path(
        "calendar/<int:employee_id>/<slug:on>/",
        views.attendance_day,
        name="attendance_day",
    ),
    path("review/", views.attendance_review, name="attendance_review"),
    path(
        "day/<int:employee_id>/<slug:on>/fix/",
        views.attendance_day_fix,
        name="attendance_day_fix",
    ),
    path("missed-scans/", scan_request_views.missed_scan_list, name="missed_scan_list"),
    path(
        "missed-scans/<int:pk>/",
        scan_request_views.missed_scan_decide,
        name="missed_scan_decide",
    ),
    path(
        "corrections/<int:pk>/withdraw/",
        views.attendance_correction_withdraw,
        name="attendance_correction_withdraw",
    ),
]
