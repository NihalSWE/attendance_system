from django.urls import path

from ai_assistant import views

app_name = "ai_assistant"

urlpatterns = [
    path("", views.inbox, name="inbox"),
    path("<int:pk>/", views.message, name="message"),
    path("<int:pk>/<slug:action>/", views.message_action, name="message_action"),
    path("settings/", views.settings_page, name="settings"),
    path("settings/recipients/", views.recipient_add, name="recipient_add"),
    path("settings/recipients/<int:pk>/remove/", views.recipient_remove, name="recipient_remove"),
]
