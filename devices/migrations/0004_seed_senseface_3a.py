"""Add the ZKTeco SenseFace 3A to the supported-hardware catalogue.

Same pattern as 0002_seed_hardware_catalogue: global reference data, so every
database gets it by running migrate and it shows under Register device.

The 3A is a ZKTeco ADMS push device like the 2A, so it uses the same adapter
(the vendor's ``zkteco_adms_push``). That adapter was verified against the
SenseFace 2A (firmware ZAM70-NF24HA-Ver3.0.15); the 3A is **not verified yet**
(added 2026-09-14 at Ajay's request, plan step A16). Capabilities are set
conservatively — only what a SenseFace certainly does (push, face) — and are
widened once the real device confirms more (card, fingerprint, commands).
"""

from django.db import migrations

MODEL = {
    "model_code": "senseface-3a",
    "name": "SenseFace 3A",
    "protocol": "adms_push",
    "capabilities": {
        "push": True,
        "face": True,
        "fingerprint": False,
        "card": False,
        "commands": False,
        "template_export": False,
        "template_import": False,
    },
    "supported_template_formats": [],
}


def seed(apps, schema_editor):
    DeviceVendor = apps.get_model("devices", "DeviceVendor")
    DeviceModel = apps.get_model("devices", "DeviceModel")
    # 0002 creates the vendor; a database whose catalogue was flushed (a test
    # elsewhere does that) gets it back rather than a crash.
    vendor, _ = DeviceVendor.objects.get_or_create(
        code="zkteco",
        defaults={
            "name": "ZKTeco",
            "adapter_key": "zkteco_adms_push",
            "description": (
                "ADMS/TA Push devices. The device posts outbound to this server; "
                "we never poll it."
            ),
            "support_url": "https://www.zkteco.com/en/Support",
        },
    )
    DeviceModel.objects.get_or_create(
        vendor=vendor, model_code=MODEL["model_code"], defaults=MODEL
    )


def unseed(apps, schema_editor):
    """Remove the row only while no device uses it (devices PROTECT their model)."""
    DeviceModel = apps.get_model("devices", "DeviceModel")
    DeviceModel.objects.filter(
        vendor__code="zkteco", model_code=MODEL["model_code"], devices__isnull=True
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("devices", "0003_device_server_address"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
