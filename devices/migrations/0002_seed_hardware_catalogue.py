"""Seed the supported-hardware catalogue.

DeviceVendor and DeviceModel are global reference data, not company data and
not demo data: an administrator picks from this list when registering a device.
Shipping it as a migration is what lets the cold-start path work — reaching a
working Register Device page must not require a management command
(TEAM_LEAD_PLAYBOOK.md safeguard 6).

Only hardware with a reviewed adapter is listed. Adding a row here without an
adapter would offer an administrator a device we cannot actually ingest from.
"""

from django.db import migrations

VENDORS = [
    {
        "code": "zkteco",
        "name": "ZKTeco",
        "adapter_key": "zkteco_adms_push",
        "description": (
            "ADMS/TA Push devices. The device posts outbound to this server; "
            "we never poll it."
        ),
        "support_url": "https://www.zkteco.com/en/Support",
    },
]

MODELS = [
    {
        "vendor_code": "zkteco",
        "model_code": "senseface-2a",
        "name": "SenseFace 2A",
        "protocol": "adms_push",
        "capabilities": {
            "push": True,
            "face": True,
            "fingerprint": True,
            "card": True,
            "commands": False,
            "template_export": False,
            "template_import": False,
        },
        "supported_template_formats": [],
    },
]


def seed(apps, schema_editor):
    DeviceVendor = apps.get_model("devices", "DeviceVendor")
    DeviceModel = apps.get_model("devices", "DeviceModel")

    for vendor_data in VENDORS:
        vendor, _ = DeviceVendor.objects.get_or_create(
            code=vendor_data["code"], defaults=vendor_data
        )
        for model_data in MODELS:
            if model_data["vendor_code"] != vendor.code:
                continue
            fields = {k: v for k, v in model_data.items() if k != "vendor_code"}
            DeviceModel.objects.get_or_create(
                vendor=vendor, model_code=fields["model_code"], defaults=fields
            )


def unseed(apps, schema_editor):
    """Remove catalogue rows only while nothing references them.

    Devices reference their model with PROTECT, so a company that has already
    registered hardware keeps its catalogue rows and this becomes a no-op.
    """
    DeviceVendor = apps.get_model("devices", "DeviceVendor")
    DeviceModel = apps.get_model("devices", "DeviceModel")

    for model_data in MODELS:
        DeviceModel.objects.filter(
            vendor__code=model_data["vendor_code"],
            model_code=model_data["model_code"],
            devices__isnull=True,
        ).delete()
    for vendor_data in VENDORS:
        DeviceVendor.objects.filter(
            code=vendor_data["code"], models__isnull=True
        ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("devices", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
