from django.urls import path

from api.v1.integrations import views

urlpatterns = [
    path("webhook", views.WebhookSettingsView.as_view(), name="webhook"),
    path("webhook/secret", views.WebhookSecretView.as_view(), name="webhook_secret"),
    path("webhook/test", views.WebhookTestView.as_view(), name="webhook_test"),
    path("webhook/test-event", views.WebhookTestEventView.as_view(), name="webhook_test_event"),
    path("webhook/events", views.WebhookEventsView.as_view(), name="webhook_events"),
    path("webhook/send-now", views.WebhookSendNowView.as_view(), name="webhook_send_now"),
    path("webhook/send-again", views.WebhookSendAgainView.as_view(), name="webhook_send_again"),
    path("webhook/debug", views.WebhookDebugView.as_view(), name="webhook_debug"),
    path("webhook/guide", views.WebhookGuideView.as_view(), name="webhook_guide"),
    path("audit-log", views.AuditLogView.as_view(), name="audit_log"),
]
