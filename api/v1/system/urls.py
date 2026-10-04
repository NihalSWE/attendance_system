from django.urls import path

from api.v1.system import views

urlpatterns = [
    path("ping", views.PingView.as_view(), name="ping"),
]
