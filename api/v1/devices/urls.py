from django.urls import path

from api.v1.devices import views

urlpatterns = [
    path("devices", views.DeviceListView.as_view(), name="devices"),
    path("devices/serial-check", views.SerialCheckView.as_view(), name="device_serial_check"),
    path("devices/connections", views.ConnectionsView.as_view(), name="device_connections"),
    path("devices/attendance-rules", views.RulesView.as_view(), name="device_rules"),
    path("devices/attendance-rules/recheck", views.RecheckView.as_view(),
         name="device_rules_recheck"),
    path("device-models", views.DeviceModelListView.as_view(), name="device_models"),
    path("device-departments/<int:link_id>/end", views.DepartmentLinkEndView.as_view(),
         name="device_department_end"),
    path("device-enrollments", views.EnrollmentListView.as_view(), name="device_enrollments"),
    path("device-enrollments/<int:enrollment_id>", views.EnrollmentDetailView.as_view(),
         name="device_enrollment"),
    path("devices/<uuid:device_id>", views.DeviceDetailView.as_view(), name="device"),
    path("devices/<uuid:device_id>/retire", views.RetireView.as_view(), name="device_retire"),
    path("devices/<uuid:device_id>/setup", views.SetupView.as_view(), name="device_setup"),
    path("devices/<uuid:device_id>/test-connection", views.ConnectionTestView.as_view(),
         name="device_test"),
    path("devices/<uuid:device_id>/departments", views.DepartmentLinksView.as_view(),
         name="device_departments"),
]
