from django.urls import path

from ask import views

app_name = "ask"

urlpatterns = [
    path("", views.ask, name="ask"),
    path("settings/", views.settings_page, name="settings"),
]
