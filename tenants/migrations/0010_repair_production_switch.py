"""Repair what 0009 got wrong (2026-10-28): for a company that had switched
Store & Inventory off and on again, 0009 copied an older, already ended
inventory grant, so Production started already over - and Materials, Make and
the recipe were hidden although the company has inventory today.

For every company with Store & Inventory on now and Production not on, where
the only Production grants are the ones 0009 made (none set by a person), a
Production grant is added from now, open-ended. A company where someone switched
Production off on purpose is left as it is.
"""

from django.db import migrations
from django.db.models import Q
from django.utils import timezone

MADE_BY_0009 = "Production kept on with Store & Inventory when it became its own module (2026-10-27)."


def _on(CompanyFeature, company_id, feature, now):
    rows = (CompanyFeature.objects.filter(company_id=company_id, feature=feature, is_active=True)
            .filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now))
            .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now)))
    effects = set(rows.values_list("effect", flat=True))
    return "disable" not in effects and "enable" in effects


def repair(apps, schema_editor):
    Feature = apps.get_model("tenants", "Feature")
    CompanyFeature = apps.get_model("tenants", "CompanyFeature")
    inventory = Feature.objects.filter(code="inventory").first()
    production = Feature.objects.filter(code="production").first()
    if inventory is None or production is None:
        return
    now = timezone.now()
    companies = set(CompanyFeature.objects.filter(feature=inventory).values_list("company_id", flat=True))
    for company_id in companies:
        if not _on(CompanyFeature, company_id, inventory, now) or _on(CompanyFeature, company_id, production, now):
            continue
        grants = CompanyFeature.objects.filter(company_id=company_id, feature=production)
        if grants.exclude(reason=MADE_BY_0009).exists():
            continue    # someone set Production for this company: theirs to decide
        CompanyFeature.objects.create(
            company_id=company_id, feature=production, effect="enable", starts_at=now, ends_at=None,
            is_active=True, reason="Production kept on with Store & Inventory (repair of 0009, 2026-10-28).")


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0009_production_feature"),
    ]

    operations = [migrations.RunPython(repair, migrations.RunPython.noop)]
