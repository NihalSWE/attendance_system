"""The "Store & Inventory" module (docs/inventory/00-PLAN.md, Phase 1): when
the platform owner enables it for a company (Companies -> the company ->
Feature access), the company gets the Inventory menu and its permissions on
the Access page. Off for every company until enabled."""

from django.db import migrations

CODE = "inventory"


def add(apps, schema_editor):
    Feature = apps.get_model("tenants", "Feature")
    Feature.objects.get_or_create(code=CODE, defaults={
        "name": "Store & Inventory",
        "description": "Items, stores, batches, the stock ledger and opening stock; "
                       "later purchase, production and sales.",
        "sort_order": 50,
    })


def remove(apps, schema_editor):
    Feature = apps.get_model("tenants", "Feature")
    CompanyFeature = apps.get_model("tenants", "CompanyFeature")
    feature = Feature.objects.filter(code=CODE).first()
    if feature is not None and not CompanyFeature.objects.filter(feature=feature).exists():
        feature.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0007_developer_api_feature"),
    ]

    operations = [migrations.RunPython(add, remove)]
