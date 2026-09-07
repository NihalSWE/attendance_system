from django.urls import path
from django.views.generic import RedirectView
from tenants import platform_views as views

app_name = "platform"
urlpatterns = [
    path("", RedirectView.as_view(pattern_name="platform:company_list", permanent=False), name="index"),
    path("companies/", views.company_list, name="company_list"),
    path("companies/create/", views.company_create, name="company_create"),
    path("companies/<uuid:public_id>/", views.company_detail, name="company_detail"),
    path("companies/<uuid:public_id>/edit/", views.company_edit, name="company_edit"),
    path("companies/<uuid:public_id>/status/", views.company_status, name="company_status"),
    path("companies/<uuid:public_id>/administrators/add/", views.administrator_create, name="administrator_create"),
    path("companies/<uuid:public_id>/memberships/<int:membership_id>/", views.membership_edit, name="membership_edit"),
    path("companies/<uuid:public_id>/features/", views.company_feature, name="company_feature"),
]
