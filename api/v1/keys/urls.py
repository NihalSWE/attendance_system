from django.urls import path

from api.v1.keys import views

urlpatterns = [
    path("api-keys", views.KeyListView.as_view(), name="api_keys"),
    path("api-keys/scopes", views.ScopeListView.as_view(), name="api_key_scopes"),
    path("api-keys/<str:key_id>", views.KeyDetailView.as_view(), name="api_key"),
    path("api-keys/<str:key_id>/rotate", views.KeyRotateView.as_view(), name="api_key_rotate"),
]
