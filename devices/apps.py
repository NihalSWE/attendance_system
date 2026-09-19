from django.apps import AppConfig


class DevicesConfig(AppConfig):
    name = 'devices'

    def ready(self):
        # Registers the BIOMETRIC_TEMPLATE_KEY system check.
        from devices.services import templates  # noqa: F401
