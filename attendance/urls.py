from django.urls import path

from attendance import views

app_name = "attendance"

urlpatterns = [
    path("", views.attendance_list, name="attendance_list"),
    path("calculate/", views.attendance_calculate, name="attendance_calculate"),
]
