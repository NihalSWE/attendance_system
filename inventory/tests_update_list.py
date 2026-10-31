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
