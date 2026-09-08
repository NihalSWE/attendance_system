"""Root URL configuration."""

from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from base_template import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("platform/", include("tenants.urls")),
    path("organization/", include("organization.urls")),

    path("", views.dashboard, name="dashboard"),
    path("employees/", views.employee_list, name="employee_list"),
    path("departments/", views.department_list, name="department_list"),
    path("switch-company/", views.switch_company, name="switch_company"),

    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="base_template/login.html",
            redirect_authenticated_user=True,
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(next_page="login"), name="logout"),
]
