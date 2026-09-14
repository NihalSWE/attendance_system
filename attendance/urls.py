from django.urls import path

from attendance import views

app_name = "attendance"

urlpatterns = [
    path("", views.attendance_list, name="attendance_list"),
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
    path(
        "corrections/<int:pk>/withdraw/",
        views.attendance_correction_withdraw,
        name="attendance_correction_withdraw",
    ),
]
