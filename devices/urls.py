"""Device routes.

The ``/iclock/`` paths are fixed by the ZKTeco firmware: the device builds them
itself from the server address and port an administrator types into its menu,
so we cannot rename them. They are mounted at the project root by
config/urls.py for that reason.
"""

from django.urls import path

from devices.views import ingestion

app_name = "devices"

# Device-facing (no session, CSRF-exempt, device-authenticated).
ingestion_urlpatterns = [
    path("iclock/cdata", ingestion.cdata, name="iclock_cdata"),
    path("iclock/getrequest", ingestion.getrequest, name="iclock_getrequest"),
    path("iclock/devicecmd", ingestion.devicecmd, name="iclock_devicecmd"),
]

urlpatterns = ingestion_urlpatterns
