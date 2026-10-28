"""Convert what was made before the redesign (2026-10-28,
docs/inventory/PRODUCTION_PLAN.md §15 phase 9.2). Runs once at deploy; running
it again changes nothing.

Productions:
- a material use without a store took from its order's materials store: say so;
- a finished production's output went into its finished-goods store: record it
  as the store it went into (the new pages and reports read that);
- a finished production had no other costs: 0, not unknown.

Prices: a company with exactly one active price list gets that list's prices
onto its products where none is set yet - the base unit's price as the sale
price, a pack's price as the pack's own price (the price for any quantity).
With several lists nothing is guessed. The price lists stay as they are.
"""

from django.db import migrations

DONE = ("completed", "closed")


def convert(apps, schema_editor):
    Consumption = apps.get_model("production", "Consumption")
    ProductionOrder = apps.get_model("production", "ProductionOrder")
    Output = apps.get_model("production", "Output")
    OutputPart = apps.get_model("production", "OutputPart")
    for use in Consumption.objects.filter(warehouse__isnull=True).select_related("order"):
        use.warehouse_id = use.order.material_warehouse_id
        use.save(update_fields=["warehouse"])
    for output in Output.objects.filter(status="posted", parts__isnull=True).select_related("order"):
        order = output.order
        qty = order.good_qty_base if order.good_qty_base is not None else output.qty_good * output.factor
        if qty and qty > 0:
            OutputPart.objects.create(company_id=output.company_id, output=output,
                                      warehouse_id=order.output_warehouse_id, qty_base=qty)
    ProductionOrder.objects.filter(status__in=DONE, other_cost__isnull=True).update(other_cost=0)

    PriceList = apps.get_model("sales", "PriceList")
    PriceListLine = apps.get_model("sales", "PriceListLine")
    Item = apps.get_model("inventory", "Item")
    ItemUom = apps.get_model("inventory", "ItemUom")
    companies = (PriceList.objects.filter(status="active").values_list("company_id", flat=True).distinct())
    for company_id in set(companies):
        lists = list(PriceList.objects.filter(company_id=company_id, status="active"))
        if len(lists) != 1:
            continue
        for line in PriceListLine.objects.filter(price_list=lists[0], min_qty=0).select_related("item"):
            item = line.item
            if line.uom_id == item.base_uom_id:
                if item.sale_price is None:
                    Item.objects.filter(pk=item.pk).update(sale_price=line.price)
            else:
                ItemUom.objects.filter(item=item, uom_id=line.uom_id, sale_price__isnull=True).update(
                    sale_price=line.price)


class Migration(migrations.Migration):
    dependencies = [
        ("production", "0003_start_finish"),
        ("inventory", "0007_warranty"),
        ("sales", "0005_credit_limit_help"),
    ]

    operations = [migrations.RunPython(convert, migrations.RunPython.noop)]
