"""Device routes.

The ``/iclock/`` paths are fixed by the ZKTeco firmware: the device builds them
itself from the server address and port an administrator types into its menu,
so we cannot rename them. They are mounted at the project root by
config/urls.py for that reason. The administrator screens live under
``/devices/``.
"""

from django.urls import path, re_path

from devices.views import ingestion, ui

app_name = "devices"

# Device-facing (no session, CSRF-exempt, device-authenticated).
ingestion_urlpatterns = [
    path("iclock/cdata", ingestion.cdata, name="iclock_cdata"),
    path("iclock/getrequest", ingestion.getrequest, name="iclock_getrequest"),
    path("iclock/devicecmd", ingestion.devicecmd, name="iclock_devicecmd"),
    # PushSDK 3.x registration handshake. Without these a 3.x device registers
    # in a loop and never transmits (observed on SenseFace 2A firmware
    # ZAM70-NF24HA-Ver3.0.15).
    path("iclock/registry", ingestion.registry, name="iclock_registry"),
    path("iclock/push", ingestion.push, name="iclock_push"),
    # Last: anything else the firmware sends is captured verbatim instead of
    # 404ing, so commissioning a real device shows us what it actually does.
    re_path(r"^iclock/(?P<tail>.*)$", ingestion.capture, name="iclock_capture"),
]

# Administrator-facing (session, CSRF, tenant-scoped).
ui_urlpatterns = [
    path("devices/", ui.device_list, name="device_list"),
    path("devices/register/", ui.device_register, name="device_register"),
    path("devices/<uuid:public_id>/", ui.device_detail, name="device_detail"),
    path("devices/<uuid:public_id>/edit/", ui.device_edit, name="device_edit"),
    path("devices/<uuid:public_id>/users/", ui.device_users, name="device_users"),
    path(
        "devices/<uuid:public_id>/users/sync/",
        ui.device_users_sync,
        name="device_users_sync",
    ),
    path(
        "devices/<uuid:public_id>/users/push/",
        ui.device_user_push,
        name="device_user_push",
    ),
    path(
        "devices/<uuid:public_id>/users/delete/",
        ui.device_user_delete,
        name="device_user_delete",
    ),
    path(
        "devices/<uuid:public_id>/command/",
        ui.device_command,
        name="device_command",
    ),
    path(
        "devices/<uuid:public_id>/settings/",
        ui.device_set_option,
        name="device_set_option",
    ),
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
