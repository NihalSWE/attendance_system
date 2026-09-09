"""Device views: the device-facing ingestion endpoints and the admin UI."""

from devices.views.ingestion import cdata, devicecmd, getrequest

__all__ = ["cdata", "devicecmd", "getrequest"]
