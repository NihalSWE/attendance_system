"""Adapter registry.

``DeviceVendor.adapter_key`` names one of these reviewed adapters. The mapping
is a fixed dictionary rather than a dynamic import so a database value can
never cause arbitrary code to load.
"""

from devices.adapters.base import DeviceAdapter, ParsedMessage, ParsedPunch
from devices.adapters.zkteco_adms import ZKTecoAdmsAdapter

_ADAPTERS = {
    ZKTecoAdmsAdapter.key: ZKTecoAdmsAdapter,
}


class UnknownAdapterError(LookupError):
    """Raised when a device's vendor names an adapter that does not exist."""


def get_adapter(adapter_key):
    try:
        return _ADAPTERS[adapter_key]()
    except KeyError:
        raise UnknownAdapterError(
            f"No reviewed adapter registered for {adapter_key!r}."
        ) from None


def adapter_keys():
    return sorted(_ADAPTERS)


__all__ = [
    "DeviceAdapter",
    "ParsedMessage",
    "ParsedPunch",
    "UnknownAdapterError",
    "adapter_keys",
    "get_adapter",
]
