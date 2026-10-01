from django.urls import path

from webhooks import views

app_name = "webhooks"

urlpatterns = [
    path("", views.webhook_settings, name="settings"),
    path("guide/", views.webhook_guide, name="guide"),
]
