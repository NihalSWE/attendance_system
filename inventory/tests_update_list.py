"""The inventory update list agreed on 2026-10-29 (memory: inventory-update-list-2026-10-29),
worked from 2026-10-31: "Put it in stock", prices and costs, dates, warranty and
guarantee, the menu."""

from django.test import TestCase
from django.urls import reverse

from inventory.models import Item
from inventory.tests_phase4 import FETCH, HubFixture


class MadeMaterialTests(HubFixture, TestCase):
    """Item 1: a material can be bought, made here, or both."""

    def make_cream(self):
        body = self.client.post(reverse("inventory:materials"), self.material(name="Cream", sourcing="make"),
                                **FETCH).json()
        self.assertTrue(body["ok"], body)
        return Item.all_objects.get(company=self.company, name="Cream")

    def test_a_made_material_has_a_recipe_a_cost_and_make(self):
        from decimal import Decimal

        from production import costing, recipes

        cream = self.make_cream()
        self.assertEqual(cream.sourcing, "make")
        page = self.client.get(reverse("inventory:material", args=[cream.pk]))
        self.assertContains(page, "Made here")
        self.assertContains(page, "Write the recipe")
        recipes.set_batch(**self.kw, item=cream, batch_size=Decimal("10"))
        recipes.add_material(**self.kw, item=cream, material=self.sugar, qty=Decimal("4"))
        page = self.client.get(reverse("inventory:material", args=[cream.pk]))
        self.assertContains(page, "Recipe: one batch makes 10 KG")
        self.assertContains(page, reverse("production:plan", args=[cream.pk]))
        # Sugar 4 kg x 125 for 10 kg of cream: 50 a kg, with none of it in stock.
        self.assertEqual(costing.unit_cost(self.company.pk, cream), Decimal("50"))
        self.assertEqual(self.client.get(reverse("production:plan", args=[cream.pk])).status_code, 200)

    def test_a_loop_through_a_made_material_is_refused(self):
        from decimal import Decimal

        from django.core.exceptions import ValidationError

        from production import recipes

        cream = self.make_cream()
        self.recipe()
        recipes.add_material(**self.kw, item=self.juice, material=cream, qty=Decimal("1"))
        recipes.set_batch(**self.kw, item=cream, batch_size=Decimal("10"))
        with self.assertRaisesMessage(ValidationError, "that is a loop"):
            recipes.add_material(**self.kw, item=cream, material=self.juice, qty=Decimal("1"))

    def test_a_bought_material_stays_bought_and_spares_are_never_made(self):
        body = self.client.post(reverse("inventory:materials"), self.material(), **FETCH).json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(Item.all_objects.get(company=self.company, name="Flour").sourcing, "buy")
        from django.core.exceptions import ValidationError

        from common.tenant import use_company

        bearing = Item.all_objects.get(pk=self.bearing.pk)
        bearing.sourcing = "make"
        with use_company(self.company), self.assertRaises(ValidationError):
            bearing.full_clean()


class PriceTests(HubFixture, TestCase):
    """Items 2-5: the price set once the cost is known, never below it by mistake."""

    def post(self, name, data, *args):
        return self.client.post(reverse(f"inventory:{name}", args=args), data, **FETCH)

    def test_the_price_card_suggests_and_guards(self):
        from decimal import Decimal as D

        from inventory import pricing

        # Pulp: 130 kg in stock at 210.
        self.assertEqual(pricing.cost_per_unit(self.company.pk, self.pulp), D("210.00"))
        self.assertEqual(pricing.suggest(D("210"), 30), D("300.00"))
        page = self.client.get(reverse("inventory:material", args=[self.pulp.pk]))
        self.assertContains(page, "Cost of one KG: <strong>210.00</strong>")
        body = self.post("item_price", {"price": "150"}, self.pulp.pk).json()
        self.assertIn("Below cost", body["errors"]["price"][0])
        body = self.post("item_price", {"price": "150", "below_cost": "on"}, self.pulp.pk).json()
        self.assertIn("reason", body["errors"])
        body = self.post("item_price", {"price": "150", "below_cost": "on", "reason": "Expires next week"},
                         self.pulp.pk).json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(Item.all_objects.get(pk=self.pulp.pk).sale_price, D("150"))
        from auditlog.models import AuditLog

        self.assertTrue(AuditLog.objects.filter(action="inventory.item.price_set",
                                                after_data__below_cost_reason="Expires next week").exists())
        # It shows in the list.
        self.assertContains(self.client.get(reverse("inventory:materials")), "Below cost")
        self.assertTrue(self.post("item_price", {"price": "300"}, self.pulp.pk).json()["ok"])

    def test_stock_in_sets_the_price_with_the_buy_price_in_front_of_you(self):
        from inventory.models import ItemKind

        flour = self.item("RM-0011", "Flour", ItemKind.RAW, self.raw, self.kg)
        from inventory import masters
        from purchasing.services import SUPPLIER

        supplier = masters.create(SUPPLIER, **self.kw, values={"code": "S1", "name": "Mills", "payment_terms_days": 0})
        line = {"source": "purchase", "supplier": supplier.pk, "store": self.rms.pk, "day": "2026-10-02",
                "item_1": flour.pk, "qty_1": "100", "cost_1": "60"}
        body = self.post("stock_in_new", {**line, "sale_1": "50"}).json()
        self.assertIn("sale_1", body["errors"])            # 50 for something that cost 60: refused
        self.assertIsNone(Item.all_objects.get(pk=flour.pk).sale_price)
        body = self.post("stock_in_new", {**line, "sale_1": "75"}).json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(str(Item.all_objects.get(pk=flour.pk).sale_price), "75.0000")

    def test_a_sale_below_cost_needs_a_reason(self):
        from sales.models import Customer, SalesOrder

        with self.tenant():
            customer = Customer.objects.create(company=self.company, code="C1", name="Shop")
        sale = {"customer": customer.pk, "store": self.rms.pk, "day": "2026-10-02",
                "item_1": self.pulp.pk, "qty_1": "5", "price_1": "100"}
        body = self.post("sale_new", sale).json()
        self.assertIn("price_1", body["errors"])            # pulp costs 210 a kg
        body = self.post("sale_new", {**sale, "below_cost": "on", "below_cost_reason": "Spoiling"}).json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(SalesOrder.all_objects.get(company=self.company).note, "Sold below cost: Spoiling")


class PutItInStockTests(HubFixture, TestCase):
    def test_a_new_material_goes_on_to_stock_in_with_itself_on_line_one(self):
        body = self.client.post(reverse("inventory:materials"), self.material(), **FETCH).json()
        flour = Item.all_objects.get(company=self.company, name="Flour")
        page = self.client.get(body["redirect"])
        stock_in = reverse("inventory:stock_in_new") + f"?item={flour.pk}"
        self.assertContains(page, "Put it in stock")
        self.assertContains(page, f'href="{stock_in}"')
        form = self.client.get(stock_in)
        self.assertContains(form, f'<option value="{flour.pk}"', count=3)       # every line offers it
        self.assertContains(form, f'data-expiry="" selected>')                  # line 1 starts with it
