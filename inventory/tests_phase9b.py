"""Phase 9.4 of the redesign (2026-10-28, docs/inventory/PRODUCTION_PLAN.md §15):
Stock in (a purchase, the first count, a customer return), Stock, Move stock
(send, receive) and Sell (sale, delivery, invoice) as one screen each, saving
in place; a refused line says which line and which box."""

from django.test import TestCase
from django.urls import reverse

from inventory import masters
from inventory.models import Batch, StockBalance, Warehouse
from inventory.models_store import StockTransfer
from inventory.testing import DAY, D
from purchasing.models import GoodsReceipt
from purchasing.services import SUPPLIER
from sales.models import Delivery, SalesInvoice, SalesOrder, SalesReturn
from sales.tests_flow import SalesFixture

FETCH = {"HTTP_X_REQUESTED_WITH": "fetch"}


class FlowFixture(SalesFixture):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)
        self.supplier = masters.create(SUPPLIER, **self.kw, values={"code": "SUP-1", "name": "Sweet Mills",
                                                                    "payment_terms_days": 30})

    def post(self, name, data, *args):
        return self.client.post(reverse(f"inventory:{name}", args=args), data, **FETCH)

    def have(self, item, store, batch=None):
        rows = StockBalance.all_objects.filter(item=item, warehouse=store)
        if batch is not None:
            rows = rows.filter(batch__batch_no=batch)
        return sum((row.qty_on_hand for row in rows), D("0"))


class StockInTests(FlowFixture, TestCase):
    def test_a_purchase_is_received_into_stock(self):
        body = self.post("stock_in_new", {
            "source": "purchase", "supplier": self.supplier.pk, "store": self.rms.pk, "day": DAY.isoformat(),
            "item_1": self.sugar.pk, "qty_1": "50", "unit_1": self.kg.pk, "cost_1": "80", "batch_1": "SB1",
            "expiry_1": "2027-05-01"}).json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(self.have(self.sugar, self.rms, "SB1"), D("50"))
        self.assertEqual(GoodsReceipt.all_objects.get(company=self.company).status, "posted")

    def test_a_refused_line_names_its_row_and_nothing_is_kept(self):
        body = self.post("stock_in_new", {
            "source": "purchase", "supplier": self.supplier.pk, "store": self.rms.pk, "day": DAY.isoformat(),
            "item_1": self.sugar.pk, "qty_1": "", "item_2": self.sugar.pk, "qty_2": "5", "cost_2": "x"}).json()
        self.assertEqual(set(body["errors"]), {"qty_1", "cost_2"})
        body = self.post("stock_in_new", {
            "source": "purchase", "supplier": self.supplier.pk, "store": self.rms.pk, "day": DAY.isoformat(),
            "item_1": self.bearing.pk, "qty_1": "2", "cost_1": "50",
            "item_2": self.sugar.pk, "qty_2": "5", "cost_2": "80"}).json()
        self.assertIn("expiry_2", body["errors"])            # sugar expires: its expiry is needed
        self.assertFalse(GoodsReceipt.all_objects.filter(company=self.company).exists())
        body = self.post("stock_in_new", {"source": "purchase", "store": self.rms.pk, "day": DAY.isoformat()}).json()
        self.assertIn("Add at least one line", body["message"])

    def test_the_first_count_is_posted_by_someone_who_may_approve(self):
        body = self.post("stock_in_new", {
            "source": "count", "store": self.rms.pk, "day": DAY.isoformat(),
            "item_1": self.bearing.pk, "qty_1": "40", "cost_1": "120"}).json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(self.have(self.bearing, self.rms), D("40"))

    def test_a_customer_return_good_and_damaged(self):
        body = self.post("stock_in_new", {
            "source": "return", "customer": self.customer.pk, "store": self.fgw.pk, "day": DAY.isoformat(),
            "reason": "Wrong order", "item_1": self.juice.pk, "qty_1": "5", "batch_1": "J1", "condition_1": "good",
            "item_2": self.juice.pk, "qty_2": "2", "batch_2": "J2", "condition_2": "damaged"}).json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(self.have(self.juice, self.fgw, "J1"), D("105"))
        self.assertEqual(self.have(self.juice, self.rejected), D("2"))
        self.assertEqual(SalesReturn.all_objects.get(company=self.company).status, "posted")
        body = self.post("stock_in_new", {
            "source": "return", "customer": self.customer.pk, "store": self.fgw.pk, "day": DAY.isoformat(),
            "reason": "x", "item_1": self.juice.pk, "qty_1": "1", "batch_1": "NOPE"}).json()
        self.assertIn("batch_1", body["errors"])

    def test_the_list_shows_every_kind(self):
        self.test_a_purchase_is_received_into_stock()
        page = self.client.get(reverse("inventory:stock_in"))
        self.assertContains(page, "Purchase")
        self.assertContains(page, reverse("purchasing:receipt_detail",
                                          args=[GoodsReceipt.all_objects.get(company=self.company).pk]))


class MoveTests(FlowFixture, TestCase):
    def test_sent_waits_in_transit_until_received(self):
        depot = self.store("DEP", "Depot", Warehouse.Kind.FINISHED)
        body = self.post("move_new", {"from_store": self.fgw.pk, "to_store": self.fgw.pk, "day": DAY.isoformat(),
                                      "item_1": self.juice.pk, "qty_1": "10"}).json()
        self.assertIn("to_store", body["errors"])
        j2 = Batch.all_objects.get(item=self.juice, batch_no="J2")
        body = self.post("move_new", {"from_store": self.fgw.pk, "to_store": depot.pk, "day": DAY.isoformat(),
                                      "item_1": self.juice.pk, "qty_1": "10", "batch_1": j2.pk}).json()
        self.assertTrue(body["ok"], body)
        transfer = StockTransfer.all_objects.get(company=self.company)
        self.assertEqual(transfer.status, "in_transit")
        self.assertEqual(self.have(self.juice, self.fgw, "J2"), D("190"))     # the batch asked for
        page = self.client.get(reverse("inventory:moves"))
        self.assertContains(page, "Receive")
        self.assertTrue(self.post("move_receive", {}, transfer.pk).json()["ok"])
        self.assertEqual(self.have(self.juice, depot), D("10"))


class SellTests(FlowFixture, TestCase):
    def test_a_sale_is_delivered_first_expiry_first_and_invoiced(self):
        body = self.post("sale_new", {"customer": self.customer.pk, "store": self.fgw.pk, "day": DAY.isoformat(),
                                      "invoice": "on", "item_1": self.juice.pk, "qty_1": "120"}).json()
        self.assertTrue(body["ok"], body)
        order = SalesOrder.all_objects.get(company=self.company)
        self.assertEqual(order.status, "delivered")
        self.assertEqual((self.have(self.juice, self.fgw, "J1"), self.have(self.juice, self.fgw, "J2")),
                         (D("0"), D("180")))
        invoice = SalesInvoice.all_objects.get(company=self.company)
        self.assertEqual(invoice.amount, D("2640"))          # the customer's price list: 22 each
        page = self.client.get(reverse("inventory:sales"))
        self.assertContains(page, order.number)
        self.assertContains(page, invoice.number)

    def test_more_than_the_store_has_is_refused_and_nothing_kept(self):
        body = self.post("sale_new", {"customer": self.customer.pk, "store": self.fgw.pk, "day": DAY.isoformat(),
                                      "item_1": self.juice.pk, "qty_1": "1000"}).json()
        self.assertFalse(body["ok"])
        self.assertIn("Not enough", body["message"])
        self.assertFalse(SalesOrder.all_objects.filter(company=self.company).exists())
        self.assertFalse(Delivery.all_objects.filter(company=self.company).exists())


class StockPageTests(FlowFixture, TestCase):
    def test_where_everything_is_and_what_expires_first(self):
        page = self.client.get(reverse("inventory:stock_now"))
        self.assertContains(page, "Mango juice 250 ml")
        self.assertContains(page, "Finished goods 300")
        self.assertContains(page, "Expires first")
        for name in ("inventory:stock_in", "inventory:stock_now", "inventory:moves", "inventory:sales"):
            self.assertContains(page, f'href="{reverse(name)}"')
