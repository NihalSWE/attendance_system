"""Production as its own module (2026-10-27, docs/inventory/PRODUCTION_PLAN.md
§1): a company that only buys and sells has Store & Inventory alone and never
sees materials, recipes or production. Production needs Store & Inventory.

Every company that has Store & Inventory switched on today keeps production:
it gets Production switched on too, from the same moment as its Inventory grant."""

from django.db import migrations
from django.db.models import Q
from django.utils import timezone

CODE = "production"


def add(apps, schema_editor):
    Feature = apps.get_model("tenants", "Feature")
    CompanyFeature = apps.get_model("tenants", "CompanyFeature")
    production, _ = Feature.objects.get_or_create(code=CODE, defaults={
        "name": "Production",
        "description": "Materials, recipes, making products and their cost. "
                       "Needs Store & Inventory.",
        "sort_order": 51,
    })
    inventory = Feature.objects.filter(code="inventory").first()
    if inventory is None:
        return
    # The switch-on in effect now (2026-10-28 fix): a company that switched
    # inventory off and on again has older, ended grants - copying one of
    # those made Production already over (tenants 0010 repairs that).
    now = timezone.now()
    current = (CompanyFeature.objects.filter(feature=inventory, effect="enable", is_active=True)
               .filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now))
               .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now)))
    for grant in current:
        if CompanyFeature.objects.filter(company_id=grant.company_id, feature=production).exists():
            continue
        CompanyFeature.objects.create(
            company_id=grant.company_id, feature=production, effect="enable",
            starts_at=grant.starts_at, ends_at=grant.ends_at, is_active=True,
            granted_by_id=grant.granted_by_id,
            reason="Production kept on with Store & Inventory when it became its own module (2026-10-27).")


def remove(apps, schema_editor):
    Feature = apps.get_model("tenants", "Feature")
    CompanyFeature = apps.get_model("tenants", "CompanyFeature")
    feature = Feature.objects.filter(code=CODE).first()
    if feature is not None:
        CompanyFeature.objects.filter(feature=feature).delete()
        feature.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0008_inventory_feature"),
    ]

    operations = [migrations.RunPython(add, remove)]
