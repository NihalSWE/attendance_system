"""The "Developer's API" module (2026-10-06): when the platform owner enables
it for a company (Companies → the company → Feature access → Change access),
that company's owner and administrator see a Developer's API link in their
sidebar, to the API documentation. Off for every company until enabled."""

from django.db import migrations

CODE = "developer_api"


def add(apps, schema_editor):
    Feature = apps.get_model("tenants", "Feature")
    Feature.objects.get_or_create(code=CODE, defaults={
        "name": "Developer's API",
        "description": "A Developer's API link in the company's sidebar, to the API "
                       "documentation, for its owner and administrator.",
        "sort_order": 90,
    })


def remove(apps, schema_editor):
    Feature = apps.get_model("tenants", "Feature")
    CompanyFeature = apps.get_model("tenants", "CompanyFeature")
    feature = Feature.objects.filter(code=CODE).first()
    if feature is not None and not CompanyFeature.objects.filter(feature=feature).exists():
        feature.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0006_company_contact_person"),
    ]

    operations = [migrations.RunPython(add, remove)]
