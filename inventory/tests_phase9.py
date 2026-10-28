"""Phase 9 of the redesign (2026-10-28, docs/inventory/PRODUCTION_PLAN.md §13, §15):
the production reports (cost against expected, material use, waste and failed
productions, shortages) and the conversion of what was made before the
redesign (productions and prices)."""

import importlib

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase
from django.urls import reverse

from common.tenant import use_company
from inventory.models import Item, ItemUom, Uom
from inventory.testing import D
from inventory.tests_phase7 import MATERIALS, RunFixture
from production import making, orders, planning
from production.models import Consumption, OutputPart, ProductionOrder
from production.tests_flow import ProductionFixture
from sales import orders as sales_orders


class ReportFixture(RunFixture):
    def report(self, slug, **params):
        url = reverse(f"inventory:report_{slug.replace('-', '_')}")
        day = self.today.isoformat()
        return self.client.get(url, {"date_from": day, "date_to": day, **params})


class ProductionReportTests(ReportFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.start()
        self.finish(good=D("1900"), damaged=D("100"), costs=[("Gas", D("240"))],
                    leftovers={self.pulp.pk: (D("5"), D("0"), ""), self.sugar.pk: (D("0"), D("1"), "spilled")})

    def test_production_cost_against_expected(self):
        page = self.report("production-cost")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, self.order.number)
        total = MATERIALS - D("1050") + D("240")                       # 5 kg pulp back at 210
        self.assertContains(page, f"{total:,.2f}")
        download = self.report("production-cost", format="xlsx")
        self.assertEqual(download.status_code, 200)

    def test_material_use_returned_and_wasted(self):
        page = self.report("material-use")
        self.assertContains(page, "Mango pulp")
        self.assertContains(page, "spilled", count=0)      # reasons are in the waste report
        waste = self.report("waste")
        self.assertContains(waste, "Wasted material")
        self.assertContains(waste, "spilled")
        self.assertContains(waste, "Damaged output")

    def test_a_failed_production_is_a_loss(self):
        second, _plan = planning.save(actor=self.owner, company_id=self.company.pk, product=self.juice,
                                      batches=1, into=self.fgw, day=self.today)
        making.start(actor=self.owner, company_id=self.company.pk, order_id=second.pk, work_date=self.today)
        making.finish(actor=self.owner, company_id=self.company.pk, order_id=second.pk,
                      values={"output_date": self.today, "good": D("0"), "damaged": D("1000"), "reason": "Burnt"})
        waste = self.report("waste")
        self.assertContains(waste, "Failed production")
        self.assertContains(waste, "Burnt")


class ShortageReportTests(ReportFixture, TestCase):
    def test_planned_productions_that_cannot_start(self):
        planning.save(actor=self.owner, company_id=self.company.pk, product=self.juice, batches=3,
                      into=self.fgw, day=self.today)
        page = self.report("shortages")
        self.assertContains(page, "RM-0002")
        self.assertContains(page, "Planned productions short")
        reports = self.client.get(reverse("inventory:reports"))
        for slug in ("production-cost", "material-use", "waste", "shortages"):
            self.assertContains(reports, reverse(f"inventory:report_{slug.replace('-', '_')}"))


class ConversionTests(ProductionFixture, TestCase):
    def convert(self):
        state = MigrationExecutor(connection).loader.project_state(("production", "0004_convert_existing"))
        migration = importlib.import_module("production.migrations.0004_convert_existing")
        migration.convert(state.apps, None)

    def test_old_productions_get_their_stores_and_old_prices_move_onto_products(self):
        # A production made the old way: release, use materials, post the output.
        order = self.released()
        use = orders.start_consumption(**self.kw, order_id=order.pk)
        orders.post_consumption(**self.kw, consumption_id=use.pk)
        Consumption.all_objects.filter(pk=use.pk).update(warehouse=None)
        orders.post_output(**self.kw, order_id=order.pk, values={
            "output_date": self.today(), "qty_good": D("990"), "qty_rejected": D("10"), "uom": self.pcs,
            "batch_no": "", "mfg_date": None, "expiry_date": None, "location": None, "packages": None,
            "note": ""})
        # One price list: its base and pack prices become the product's own.
        with use_company(self.company):
            carton = Uom.objects.get(code="CARTON")
        ItemUom.all_objects.create(company=self.company, item=self.juice, uom=carton, factor=24)
        prices = sales_orders.create_price_list(**self.kw, values={"name": "Retail", "valid_from": None,
                                                                    "valid_to": None, "note": ""})
        for uom, price in ((self.pcs, D("25")), (carton, D("560"))):
            sales_orders.set_price(**self.kw, price_list_id=prices.pk,
                                   values={"item": self.juice, "uom": uom, "price": price, "min_qty": D("0")})
        self.convert()
        self.convert()                                          # twice changes nothing
        self.assertEqual(Consumption.all_objects.get(pk=use.pk).warehouse, self.rms)
        part = OutputPart.all_objects.get(output__order=order)
        self.assertEqual((part.warehouse, part.qty_base), (self.fgw, D("990")))
        self.assertEqual(ProductionOrder.all_objects.get(pk=order.pk).other_cost, D("0"))
        self.assertEqual(Item.all_objects.get(pk=self.juice.pk).sale_price, D("25"))
        self.assertEqual(ItemUom.all_objects.get(item=self.juice, uom=carton).sale_price, D("560"))
        # The old production opens on the new page with its store.
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("production:run", args=[order.pk])), "Finished goods 990")

    def today(self):
        from django.utils import timezone

        return timezone.localdate()
