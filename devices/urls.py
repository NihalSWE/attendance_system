"""Device routes.

The ``/iclock/`` paths are fixed by the ZKTeco firmware: the device builds them
itself from the server address and port an administrator types into its menu,
so we cannot rename them. They are mounted at the project root by
config/urls.py for that reason. The administrator screens live under
``/devices/``.
"""

from django.urls import path

from devices.views import ingestion, ui

app_name = "devices"

# Device-facing (no session, CSRF-exempt, device-authenticated).
ingestion_urlpatterns = [
    path("iclock/cdata", ingestion.cdata, name="iclock_cdata"),
    path("iclock/getrequest", ingestion.getrequest, name="iclock_getrequest"),
    path("iclock/devicecmd", ingestion.devicecmd, name="iclock_devicecmd"),
]

# Administrator-facing (session, CSRF, tenant-scoped).
ui_urlpatterns = [
    path("devices/", ui.device_list, name="device_list"),
    path("devices/register/", ui.device_register, name="device_register"),
    path("devices/<uuid:public_id>/", ui.device_detail, name="device_detail"),
    path("devices/<uuid:public_id>/edit/", ui.device_edit, name="device_edit"),
    path("devices/<uuid:public_id>/retire/", ui.device_retire, name="device_retire"),
    path(
        "devices/<uuid:public_id>/departments/add/",
        ui.device_department_add,
        name="device_department_add",
    ),
    path(
        "devices/departments/<int:pk>/end/",
        ui.device_department_end,
        name="device_department_end",
    ),

    path("devices/enrollments/", ui.enrollment_list, name="enrollment_list"),
    path(
        "devices/enrollments/new/", ui.enrollment_create, name="enrollment_create"
    ),
    path(
        "devices/enrollments/<int:pk>/edit/",
        ui.enrollment_edit,
        name="enrollment_edit",
    ),

    path("devices/messages/", ui.message_list, name="message_list"),
    path(
        "devices/messages/<uuid:public_id>/",
        ui.message_detail,
        name="message_detail",
    ),
    path("devices/punches/", ui.punch_list, name="punch_list"),
    path("devices/punches/<int:pk>/", ui.punch_detail, name="punch_detail"),
    path("devices/unresolved/", ui.unresolved_queue, name="unresolved_queue"),
]

urlpatterns = ingestion_urlpatterns + ui_urlpatterns
