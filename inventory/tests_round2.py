"""Inventory round 2 (2026-11-01, memory: inventory-round-2-2026-11-01)."""

from django.test import TestCase
from django.urls import reverse

from inventory.models import Batch, Item
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
