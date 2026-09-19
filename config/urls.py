"""Root URL configuration."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from base_template import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("platform/", include("tenants.urls")),
    # which is our word, not the operator's -- but the views live in
    # organization/ with their models.
    path("organization/", include("organization.urls")),
    path("shifts/", include("scheduling.urls")),
    path("leave/", include("leaves.urls")),
    path("attendance/", include("attendance.urls")),
    path("salary/", include("payroll.urls")),
    # An Employee or Branch manager login's own pages.
    path("me/", include("base_template.me_urls")),
    # Device integration. Mounted at the root because the /iclock/ paths are
    # built by the ZKTeco firmware itself and cannot be prefixed.
    path("", include("devices.urls")),

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
    # POST logs out; GET asks rather than returning a bare 405.
    path("logout/", views.ConfirmingLogoutView.as_view(next_page="login"), name="logout"),
]

# Development only: with DEBUG off the web server serves /media/ itself.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
