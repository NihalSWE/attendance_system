"""The inventory updates of 2026-11-02: VAT and the total on a purchase, the
last price paid as the cost of one, the item lists of a store, stock waiting
for a quality check said on Sell, Materials by type, and several recipe
materials added at once."""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from common.tenant import use_company
from inventory import pricing
from inventory.models import Batch, InventorySettings, Item
from inventory.testing import DAY, D
from inventory.tests_phase9b import FETCH, FlowFixture
from production import recipes
from production.tests_flow import ProductionFixture
from purchasing.models import GoodsReceipt


class StockInBillTests(FlowFixture, TestCase):
    def buy(self, cost, batch, **extra):
        return self.post("stock_in_new", {
            "source": "purchase", "supplier": self.supplier.pk, "store": self.rms.pk, "day": DAY.isoformat(),
            "item_1": self.sugar.pk, "qty_1": "50", "unit_1": self.kg.pk, "cost_1": cost, "batch_1": batch,
            "expiry_1": "2027-05-01", **extra}).json()

    def test_the_vat_rate_gives_the_amount_and_the_total(self):
        body = self.buy("80", "SB1", vat_rate="15")
        self.assertTrue(body["ok"], body)
        receipt = GoodsReceipt.all_objects.get(company=self.company)
        self.assertEqual((receipt.vat_rate, receipt.vat_amount), (D("15"), D("600")))
        self.assertIn("Total 4,600.00 Tk with VAT 600.00.", body["message"])

    def test_the_vat_amount_gives_the_rate(self):
        self.buy("80", "SB1", vat_amount="400")
        receipt = GoodsReceipt.all_objects.get(company=self.company)
        self.assertEqual((receipt.vat_rate, receipt.vat_amount), (D("10"), D("400")))

    def test_the_cost_paid_is_the_new_buy_price_and_the_cost_of_one(self):
        self.buy("80", "SB1")
        self.assertEqual(Item.all_objects.get(pk=self.sugar.pk).standard_cost, D("80"))
        self.buy("100", "SB2")
        sugar = Item.all_objects.get(pk=self.sugar.pk)
        self.assertEqual(sugar.standard_cost, D("100"))
        # The last price paid counts, not the average of old and new stock...
        self.assertEqual(pricing.cost_per_unit(self.company.pk, sugar), D("100"))
        # ...unless the company keeps to the average cost.
        with use_company(self.company):
            InventorySettings.objects.update_or_create(company=self.company, defaults={"price_cost": "average"})
        self.assertEqual(pricing.cost_per_unit(self.company.pk, sugar), D("90"))

    def test_the_page_explains_each_source_and_knows_the_stores(self):
        page = self.client.get(reverse("inventory:stock_in_new"))
        self.assertContains(page, "Stock you already had on the first day")
        self.assertContains(page, "A customer sent back what you sold them")
        self.assertContains(page, 'id="store-kinds"')
        self.assertContains(page, 'data-lines-mode="in"')
        self.assertContains(page, 'name="vat_rate"')
        self.assertContains(page, "Total to pay")


class SellQualityCheckTests(FlowFixture, TestCase):
    def test_stock_waiting_for_a_check_is_said_apart(self):
        Batch.all_objects.filter(pk=self.j1.pk).update(status=Batch.Status.QUARANTINE)
        page = self.client.get(reverse("inventory:sale_new"))
        rows = page.context["balances"]
        self.assertEqual({row["batch"]: row["held"] for row in rows if row["item"] == self.juice.pk},
                         {self.j1.pk: True, self.j2.pk: False})
        self.assertContains(page, f'data-qc-url="{reverse("inventory:qc")}"')
        # Selling more than is ready is refused, and says why.
        body = self.post("sale_new", {"customer": self.customer.pk, "store": self.fgw.pk, "day": DAY.isoformat(),
                                      "item_1": self.juice.pk, "qty_1": "250", "price_1": "30"}).json()
        self.assertFalse(body["ok"])
        self.assertIn("waits for a quality check", " ".join([body["message"], *body["form_errors"]]))


class MaterialsAndRecipeTests(ProductionFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def test_materials_by_type(self):
        page = self.client.get(reverse("inventory:materials"))
        self.assertNotContains(page, "Mango juice")
        for label in ("All materials", "Raw materials", "Packaging", "Finished goods"):
            self.assertContains(page, label)
        packaging = self.client.get(reverse("inventory:materials"), {"kind": "packaging"})
        self.assertContains(packaging, "PET bottle 250 ml")
        self.assertNotContains(packaging, "Mango pulp")
        finished = self.client.get(reverse("inventory:materials"), {"kind": "finished"})
        self.assertContains(finished, "Mango juice")
        self.assertContains(finished, reverse("inventory:product", args=[self.juice.pk]))

    def test_several_materials_at_once(self):
        self.client.post(reverse("production:recipe_batch", args=[self.juice.pk]), {"batch_size": "1000"})
        reply = self.client.post(reverse("production:recipe_add", args=[self.juice.pk]), {
            "source": "existing", "materials": [self.pulp.pk, self.bottle.pk, self.cap.pk],
            f"qty_{self.pulp.pk}": "120", f"scrap_{self.pulp.pk}": "2",
            f"qty_{self.bottle.pk}": "1000", f"qty_{self.cap.pk}": ""}, **FETCH)
        self.assertEqual(reply.status_code, 400)
        self.assertEqual(list(reply.json()["errors"]), [f"qty_{self.cap.pk}"])
        with use_company(self.company):
            self.assertFalse(recipes.current(self.company.pk, self.juice).lines.exists())
        reply = self.client.post(reverse("production:recipe_add", args=[self.juice.pk]), {
            "source": "existing", "materials": [self.pulp.pk, self.bottle.pk, self.cap.pk],
            f"qty_{self.pulp.pk}": "120", f"scrap_{self.pulp.pk}": "2",
            f"qty_{self.bottle.pk}": "1000", f"qty_{self.cap.pk}": "1000"}, **FETCH)
        self.assertTrue(reply.json()["ok"], reply.json())
        self.assertIn("Added 3 materials per batch", reply.json()["message"])
        with use_company(self.company):
            lines = {line.item_id: (line.qty, line.scrap_pct)
                     for line in recipes.current(self.company.pk, self.juice).lines.all()}
        self.assertEqual(lines[self.pulp.pk], (D("120"), D("2")))
        self.assertEqual(lines[self.cap.pk][0], D("1000"))

    def test_the_cost_of_one_batch_is_written_out(self):
        self.client.post(reverse("production:recipe_batch", args=[self.juice.pk]), {"batch_size": "1000"})
        recipes.add_material(**self.kw, item=self.juice, material=self.pulp, qty=Decimal("120"),
                             scrap_pct=Decimal("10"))
        page = self.client.get(reverse("inventory:product", args=[self.juice.pk]))
        self.assertContains(page, "120 KG + 10 % wastage = 132 KG ×")
        self.assertContains(page, "the last price paid for it")
