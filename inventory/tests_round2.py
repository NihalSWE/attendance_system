"""Inventory round 2 (2026-11-01, memory: inventory-round-2-2026-11-01)."""

from django.test import TestCase
from django.urls import reverse

from inventory.models import Batch, Item
from inventory.tests_phase4 import HubFixture
from inventory.tests_phase6 import FETCH
from inventory.tests_phase7 import RunFixture


class QualityCheckTests(RunFixture, TestCase):
    """Made stock waiting for its quality check is shown, explained and passed."""

    def made(self):
        Item.all_objects.filter(pk=self.juice.pk).update(qc_required=True)
        self.juice.refresh_from_db()
        self.start()
        _order, output = self.finish()
        self.assertEqual(output.batch.status, Batch.Status.QUARANTINE)
        return output.batch

    def test_listed_where_people_look(self):
        batch = self.made()
        page = self.client.get(reverse("inventory:qc"))
        self.assertContains(page, "Waiting for quality check")
        self.assertContains(page, batch.batch_no)
        self.assertContains(page, self.order.number)
        self.assertContains(self.client.get(reverse("inventory:product", args=[self.juice.pk])),
                            "Waiting for quality check")
        self.assertContains(self.client.get(reverse("inventory:start")), "Waiting for quality check (1)")

    def test_selling_it_says_why_and_passing_it_frees_it(self):
        from sales.models import Customer

        batch = self.made()
        with self.tenant():
            customer = Customer.objects.create(company=self.company, code="C1", name="Shop")
        sale = {"customer": customer.pk, "store": self.fgw.pk, "day": self.today.isoformat(),
                "item_1": self.juice.pk, "qty_1": "10", "price_1": "50"}
        body = self.client.post(reverse("inventory:sale_new"), sale, **FETCH).json()
        self.assertFalse(body["ok"])
        self.assertIn("waits for a quality check", " ".join([body["message"], *body.get("form_errors", [])]))
        body = self.client.post(reverse("inventory:qc_decide", args=[batch.pk]), {"result": "failed"},
                                **FETCH).json()
        self.assertIn("notes", body["errors"])
        body = self.client.post(reverse("inventory:qc_decide", args=[batch.pk]), {"result": "passed"},
                                **FETCH).json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(Batch.all_objects.get(pk=batch.pk).status, Batch.Status.RELEASED)
        self.assertNotContains(self.client.get(reverse("inventory:qc")), batch.batch_no)


class StockInFillsTests(HubFixture, TestCase):
    """Stock in starts each line from what the item already knows."""

    def test_the_line_offers_the_last_price_supplier_store_and_unit(self):
        import uuid
        from decimal import Decimal

        from inventory import masters
        from inventory.models import ItemUom
        from purchasing.services import SUPPLIER

        flour = self.item("RM-0099", "Flour", "raw", self.raw, self.kg)
        Item.all_objects.filter(pk=flour.pk).update(standard_cost=Decimal("50"), default_warehouse=self.rms,
                                                    shelf_life_days=90)
        with self.tenant():
            ItemUom.objects.create(company=self.company, item=flour, uom=self.bag, factor=Decimal("50"))
        page = self.client.get(reverse("inventory:stock_in_new"))
        self.assertContains(page, f'data-buy-unit="{self.bag.pk}" data-cost="50" data-cost-from="expected buy price" '
                                  f'data-store="{self.rms.pk}" data-supplier="" data-shelf="90"')
        supplier = masters.create(SUPPLIER, **self.kw, values={"code": uuid.uuid4().hex[:6], "name": "Mills",
                                                                 "payment_terms_days": 0})
        body = self.client.post(reverse("inventory:stock_in_new"), {
            "source": "purchase", "supplier": supplier.pk, "store": self.rms.pk, "day": "2026-10-02",
            "item_1": flour.pk, "qty_1": "2", "unit_1": self.bag.pk, "cost_1": "2600"}, **FETCH).json()
        self.assertTrue(body["ok"], body)
        page = self.client.get(reverse("inventory:stock_in_new"))
        self.assertContains(page, f'data-cost="52" data-cost-from="last price paid" data-store="{self.rms.pk}" '
                                  f'data-supplier="{supplier.pk}"')


class ProductStepsTests(HubFixture, TestCase):
    """The product page in the order the work is done; the price waits for the cost."""

    def test_a_new_product_starts_at_its_recipe_and_its_price_waits(self):
        body = self.client.post(reverse("inventory:products"), self.product(sale_price=""), **FETCH).json()
        self.assertTrue(body["ok"], body)
        product = Item.all_objects.get(company=self.company, name="Biscuit 100 g")
        page = self.client.get(reverse("inventory:product", args=[product.pk]))
        states = [(step["key"], step["state"]) for step in page.context["steps"]]
        self.assertEqual(states[:3], [("details", "done"), ("recipe", "now"), ("cost", "later")])
        self.assertContains(page, "Set the price once the cost is known.")
        self.assertNotContains(page, f'action="{reverse("inventory:item_price", args=[product.pk])}"')
        self.assertContains(page, "Write the recipe first.")
        html = page.content.decode()
        order = [html.index(f'id="{anchor}"') for anchor in ("details", "recipe", "price", "make", "stock")]
        self.assertEqual(order, sorted(order))


class StoreContentsTests(HubFixture, TestCase):
    """The stores list says what each store holds, and opens it."""

    def test_items_in_each_store(self):
        page = self.client.get(reverse("inventory:stores"))
        self.assertContains(page, "Items in it")
        self.assertContains(page, f'{reverse("inventory:stock_now")}?store={self.rms.pk}')
        for extra in ({"search[value]": self.rms.name}, {"order[0][column]": "5", "order[0][dir]": "desc"}):
            reply = self.client.get(reverse("inventory:stores"), {"table": "1", "draw": "2", "start": "0",
                                                                  "length": "25", **extra})
            self.assertEqual(reply.status_code, 200, extra)
            self.assertTrue(reply.json()["data"])
        opened = self.client.get(reverse("inventory:stock_now"), {"store": self.rms.pk})
        self.assertContains(opened, f"What's in {self.rms.name}")


class PurchaseGuideTests(HubFixture, TestCase):
    """Every purchase page says where it sits in the flow and what it does."""

    def test_the_guide_on_the_purchase_pages(self):
        for name, words in (("purchasing:requisitions", "Ask to buy."), ("purchasing:orders", "Order from the supplier."),
                            ("purchasing:receipts", "Receive what arrived."), ("purchasing:qc", "Check the quality."),
                            ("purchasing:returns", "Send goods back.")):
            page = self.client.get(reverse(name))
            self.assertEqual(page.status_code, 200, name)
            self.assertContains(page, words)
            self.assertContains(page, 'aria-label="How a purchase goes"')


class AdjustGuideTests(HubFixture, TestCase):
    """Adjust stock and Stock counts say what they are for and what is next."""

    def test_an_adjustment_shows_its_effect_and_next_step(self):
        import datetime
        from decimal import Decimal

        from inventory import adjustments
        from inventory.models import Batch

        row = adjustments.create_adjustment(**self.kw, values={
            "warehouse": self.rms, "adjustment_date": datetime.date(2026, 10, 5), "reason": "damage", "note": ""})
        batch = Batch.all_objects.get(item=self.pulp, batch_no="P1")
        adjustments.add_line(**self.kw, adjustment_id=row.pk, values={
            "item": self.pulp, "batch": batch, "qty_change": Decimal("-5"), "uom": self.kg, "note": ""})
        page = self.client.get(reverse("inventory:store_detail", args=["adjustments", row.pk]))
        self.assertContains(page, "Adjust stock")
        self.assertContains(page, "In store now")
        self.assertContains(page, "After this")
        self.assertContains(page, "Send for approval")
        self.assertEqual(page.context["lines"][0].after_qty, page.context["lines"][0].now_qty - 5)
        for name, words in (("inventory:store_adjustments", "Use a stock count"),
                            ("inventory:store_counts", "Count a store")):
            self.assertContains(self.client.get(reverse(name)), words)
