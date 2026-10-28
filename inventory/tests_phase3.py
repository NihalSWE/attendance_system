"""Phase 3 of the redesign (2026-10-27, docs/inventory/PRODUCTION_PLAN.md §15):
Production as its own module, how a product is got (buy / make / both), a sale
price per pack, and stock to keep as a quantity or days of sales."""

import datetime
import importlib
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from access_control.branch_access import feature_on, offered
from common.tenant import use_company
from inventory import keep, posting
from inventory.models import Item, ItemKind, ItemUom, MoveType
from inventory.reports import reorder_rows
from inventory.testing import D, InventoryFixture
from production.tests_flow import ProductionFixture
from tenants.models import CompanyFeature, Feature


class ProductionModuleTests(ProductionFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def test_production_off_hides_it_everywhere(self):
        self.enable_module(True, production=False)
        page = self.client.get(reverse("production:orders"))
        self.assertEqual(page.status_code, 404)
        self.assertContains(page, "Production is not enabled", status_code=404)
        menu = self.client.get(reverse("inventory:products"))
        self.assertNotContains(menu, f'href="{reverse("production:orders")}"')
        self.assertNotContains(menu, f'href="{reverse("inventory:materials")}"')
        self.assertContains(menu, f'href="{reverse("inventory:products")}"')
        self.assertNotIn("production.plan", offered(self.company.pk))
        # The finished good shows no recipe card.
        self.assertNotContains(self.client.get(reverse("inventory:item_detail", args=[self.juice.pk])),
                               'id="materials"')

    def test_production_needs_inventory(self):
        self.enable_module(False, production=True)
        self.assertFalse(feature_on(self.company.pk, "production"))

    def test_production_on_shows_it(self):
        page = self.client.get(reverse("production:orders"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, f'href="{reverse("inventory:materials")}"')
        self.assertIn("production.plan", offered(self.company.pk))

    def test_companies_with_inventory_keep_production(self):
        production = Feature.objects.get(code="production")
        CompanyFeature.all_objects.filter(company=self.company, feature=production).delete()
        self.assertFalse(feature_on(self.company.pk, "production"))
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        state = MigrationExecutor(connection).loader.project_state(("tenants", "0009_production_feature"))
        migration = importlib.import_module("tenants.migrations.0009_production_feature")
        migration.add(state.apps, None)
        self.assertTrue(feature_on(self.company.pk, "production"))


    def test_inventory_switched_off_and_on_again_still_gets_production(self):
        """2026-10-28: 0009 copied an old, ended inventory grant, so Production
        started already over and Materials was hidden; 0010 repairs it."""
        import datetime

        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        from django.utils import timezone

        inventory = Feature.objects.get(code="inventory")
        production = Feature.objects.get(code="production")
        CompanyFeature.all_objects.filter(company=self.company, feature=production).delete()
        before = timezone.now() - datetime.timedelta(days=10)
        CompanyFeature.all_objects.create(company=self.company, feature=inventory, effect="enable",
                                          starts_at=before - datetime.timedelta(days=1), ends_at=before,
                                          is_active=True)
        apps = MigrationExecutor(connection).loader.project_state(("tenants", "0010_repair_production_switch")).apps
        importlib.import_module("tenants.migrations.0009_production_feature").add(apps, None)
        self.assertTrue(feature_on(self.company.pk, "production"))
        # A database where the old 0009 already made an ended grant: 0010 repairs it.
        CompanyFeature.all_objects.filter(company=self.company, feature=production).update(
            starts_at=before - datetime.timedelta(days=1), ends_at=before)
        self.assertFalse(feature_on(self.company.pk, "production"))
        importlib.import_module("tenants.migrations.0010_repair_production_switch").repair(apps, None)
        self.assertTrue(feature_on(self.company.pk, "production"))
        # Production switched off by a person stays off.
        self.enable_module(True, production=False)
        importlib.import_module("tenants.migrations.0010_repair_production_switch").repair(apps, None)
        self.assertFalse(feature_on(self.company.pk, "production"))


class SourcingTests(ProductionFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def test_a_new_finished_good_is_made_and_anything_else_bought(self):
        self.client.post(reverse("inventory:finished_goods"), {
            "code": "FG-0002", "name": "Mango juice 1 l", "category": self.juice.category_id,
            "base_uom": self.pcs.pk, "keep_by": "quantity"})
        self.assertEqual(Item.all_objects.get(code="FG-0002").sourcing, "make")
        self.client.post(reverse("inventory:items"), {
            "code": "RM-0009", "name": "Salt", "kind": "raw", "category": self.pulp.category_id,
            "base_uom": self.kg.pk, "sourcing": "make", "keep_by": "days"})
        salt = Item.all_objects.get(code="RM-0009")
        self.assertEqual((salt.sourcing, salt.keep_by), ("buy", "quantity"))

    def test_a_bought_product_has_no_recipe_and_cannot_be_planned(self):
        Item.all_objects.filter(pk=self.juice.pk).update(sourcing="buy")
        page = self.client.get(reverse("inventory:item_detail", args=[self.juice.pk]))
        self.assertNotContains(page, 'id="materials"')
        response = self.client.post(reverse("production:recipe_batch", args=[self.juice.pk]),
                                    {"batch_size": "1000"}, follow=True)
        self.assertContains(response, "is bought ready")

    def test_keeping_by_days_needs_the_days(self):
        page = self.client.post(reverse("inventory:finished_goods"), {
            "code": "FG-0003", "name": "Juice 2 l", "category": self.juice.category_id,
            "base_uom": self.pcs.pk, "sourcing": "both", "keep_by": "days"})
        self.assertContains(page, "Say how many days of sales to keep.")


class PackPriceTests(InventoryFixture, TestCase):
    def test_a_pack_has_its_own_price(self):
        from sales.orders import price_for
        from sales.models import Customer

        with self.tenant():
            Item.objects.filter(pk=self.sugar.pk).update(sale_price=Decimal("20"))
            self.sugar.refresh_from_db()
            pack = ItemUom.objects.create(company=self.company, item=self.sugar, uom=self.bag, factor=24)
            customer = Customer.objects.create(company=self.company, code="C1", name="Shop")
        day = datetime.date(2026, 10, 27)
        self.assertEqual(price_for(customer, self.sugar, self.bag, 1, day), Decimal("480.0000"))
        ItemUom.all_objects.filter(pk=pack.pk).update(sale_price=Decimal("450"))
        self.assertEqual(price_for(customer, self.sugar, self.bag, 1, day), Decimal("450"))


class StockToKeepTests(InventoryFixture, TestCase):
    def test_days_of_sales_from_the_last_30_days(self):
        store = self.store("SPS", "Spares", "general")
        posting.post(company_id=self.company.pk, actor=self.owner, document_type="opening",
                     document_id=1, move_type=MoveType.OPENING, work_date=datetime.date(2026, 10, 1),
                     lines=[posting.Line(item=self.bearing, warehouse=store, qty=D("500"), unit_cost=D("10"))])
        posting.post(company_id=self.company.pk, actor=self.owner, document_type="delivery",
                     document_id=1, move_type=MoveType.DELIVERY, work_date=datetime.date(2026, 10, 20),
                     lines=[posting.Line(item=self.bearing, warehouse=store, qty=D("-300"))])
        Item.all_objects.filter(pk=self.bearing.pk).update(keep_by="days", keep_days=15)
        bearing = Item.all_objects.get(pk=self.bearing.pk)
        today = datetime.date(2026, 10, 27)
        self.assertEqual(keep.sold_per_day(self.company.pk, [bearing.pk], today)[bearing.pk], Decimal("10"))
        self.assertEqual(keep.stock_to_keep(self.company.pk, [bearing], today)[bearing.pk], Decimal("150"))
        Item.all_objects.filter(pk=self.bearing.pk).update(keep_days=25)   # keep 250, have 200
        with use_company(self.company):
            rows = reorder_rows(self.company.pk, [store.pk], [self.branch.pk])
        self.assertTrue(any(item.pk == bearing.pk and status == "Below minimum"
                            for item, _a, _c, _w, status in rows))
