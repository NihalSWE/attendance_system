from django.urls import path

from attendance import views

app_name = "attendance"

urlpatterns = [
    path("", views.attendance_list, name="attendance_list"),
    path("calendar/", views.attendance_calendar, name="attendance_calendar"),
    path(
        "calendar/<int:employee_id>/<slug:on>/",
        views.attendance_day,
        name="attendance_day",
    ),
    path("calculate/", views.attendance_calculate, name="attendance_calculate"),
]
