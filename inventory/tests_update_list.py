"""The inventory update list agreed on 2026-10-29 (memory: inventory-update-list-2026-10-29),
worked from 2026-10-31: "Put it in stock", prices and costs, dates, warranty and
guarantee, the menu."""

from django.test import TestCase
from django.urls import reverse

from inventory.models import Item
from inventory.tests_phase4 import FETCH, HubFixture


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
