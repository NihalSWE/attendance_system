"""Device routes.

The ``/iclock/`` paths are fixed by the ZKTeco firmware: the device builds them
itself from the server address and port an administrator types into its menu,
so we cannot rename them. They are mounted at the project root by
config/urls.py for that reason. The administrator screens live under
``/devices/``.
"""

from django.urls import path, re_path

from devices.views import ingestion, mapping, ui

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
    # Not called by the device: the server fetches this on itself, at a
    # candidate address, before repointing a device at it. It sits under
    # /iclock/ so the check exercises the same host, port and scheme the
    # device will use, including anything a proxy does to that path.
    path(
        "iclock/serveraddress-check",
        ingestion.address_check,
        name="iclock_address_check",
    ),
    # Last: anything else the firmware sends is captured verbatim instead of
    # 404ing, so commissioning a real device shows us what it actually does.
    re_path(r"^iclock/(?P<tail>.*)$", ingestion.capture, name="iclock_capture"),
]

# Administrator-facing (session, CSRF, tenant-scoped).
ui_urlpatterns = [
    path("devices/", ui.device_list, name="device_list"),
    path("devices/register/", ui.device_register, name="device_register"),
    path("devices/connection/", ui.device_connections, name="device_connections"),
    path("devices/<uuid:public_id>/", ui.device_detail, name="device_detail"),
    path("devices/<uuid:public_id>/edit/", ui.device_edit, name="device_edit"),
    path(
        "devices/<uuid:public_id>/connection/test/",
        ui.device_connection_test,
        name="device_connection_test",
    ),
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
        "devices/<uuid:public_id>/users/map-automatically/",
        mapping.device_map_automatically,
        name="device_map_automatically",
    ),
    path(
        "devices/<uuid:public_id>/users/transfer/",
        mapping.device_users_transfer,
        name="device_users_transfer",
    ),
    path("devices/map/employee/", mapping.employee_map, name="employee_map"),
    path("devices/map/branch/", mapping.employee_bulk_map, name="employee_bulk_map"),
    path(
        "devices/<uuid:public_id>/users/templates/save/",
        ui.device_templates_save,
        name="device_templates_save",
    ),
    path(
        "devices/<uuid:public_id>/users/templates/trial/",
        ui.device_template_trial,
        name="device_template_trial",
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
    path(
        "devices/<uuid:public_id>/server-address/status/",
        ui.device_server_address_status,
        name="device_server_address_status",
    ),
    path(
        "devices/<uuid:public_id>/server-address/cancel/",
        ui.device_server_address_cancel,
        name="device_server_address_cancel",
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
    path(
        "devices/attendance-rules/",
        ui.attendance_rules_page,
        name="attendance_rules",
    ),
    path(
        "devices/attendance-rules/recheck/",
        ui.attendance_recheck,
        name="attendance_recheck",
    ),
]

urlpatterns = ingestion_urlpatterns + ui_urlpatterns
