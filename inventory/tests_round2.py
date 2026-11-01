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
