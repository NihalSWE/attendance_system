"""The whole inventory flow through the pages, in the order a factory works
(2026-11-01): Mango juice from three materials - buy with an order and a
quality check, Stock in, the recipe and its cost, the price, make it, pass its
quality check, sell the juice and a raw material, a customer return, move,
adjust, count, the invoice and challan, every report. One test, so each step
starts from what the one before left; the stock is checked at the end.

Recipe, one batch = 100 bottles: mango pulp 12 KG, sugar 3 KG, 100 bottles.
"""

import datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from common.tests_exports import pdf_text
from inventory import masters
from inventory.models import Batch, Item, ItemKind, StockBalance, Warehouse
from inventory.reports import REPORTS
from inventory.testing import InventoryFixture

D = Decimal
FETCH = {"HTTP_X_REQUESTED_WITH": "fetch"}
DAY = datetime.date(2026, 10, 1)


def iso(days):
    return (DAY + datetime.timedelta(days=days)).isoformat()


class FullFlow(InventoryFixture):
    """The flow's set-up and steps; FullFlowTests runs them."""
    def setUp(self):
        super().setUp()
        # A company has one owner or administrator: the owner approves too
        # (allowed when nobody else may).
        self.approver = self.owner
        self.client.force_login(self.owner)
        self.fruit = self.category("FRT", "Fruit", ItemKind.RAW)
        self.packs = self.category("BTL", "Bottles", ItemKind.PACKAGING)
        self.juices = self.category("JCE", "Juice", ItemKind.FINISHED)
        self.depot = self.store("DEP", "Depot", Warehouse.Kind.FINISHED)
        self.rejected = self.store("REJ", "Rejected", Warehouse.Kind.REJECTED)
        with self.tenant():
            from inventory.models import Uom

            self.carton = Uom.objects.get(code="CARTON")
        from purchasing.services import SUPPLIER

        self.supplier = masters.create(SUPPLIER, actor=self.owner, company_id=self.company.pk, values={
            "code": "S1", "name": "Mango Growers", "email": "orders@mango.test", "payment_terms_days": 30})
        from sales.models import Customer

        with self.tenant():
            self.customer = Customer.objects.create(company=self.company, code="C1", name="City Shop",
                                                    customer_type="retailer", payment_terms_days=15)

    # --- helpers ------------------------------------------------------------

    def post(self, name, data=None, *args, fetch=True):
        reply = self.client.post(reverse(name, args=args), data or {}, **(FETCH if fetch else {}))
        if fetch:
            body = reply.json()
            self.assertTrue(body.get("ok"), f"{name}: {body}")
            return body
        self.assertIn(reply.status_code, (200, 302), f"{name}: {reply.status_code}")
        return reply

    def stock(self, item, store=None):
        rows = StockBalance.all_objects.filter(company=self.company, item=item)
        if store is not None:
            rows = rows.filter(warehouse=store)
        return sum((row.qty_on_hand for row in rows), D("0"))

    def item_named(self, name):
        return Item.all_objects.get(company=self.company, name=name)

    def as_approver(self, name, *args, data=None):
        self.client.force_login(self.approver)
        reply = self.client.post(reverse(name, args=args), data or {})
        self.client.force_login(self.owner)
        self.assertIn(reply.status_code, (200, 302))
        return reply

    # --- the flow -----------------------------------------------------------

    def run_flow(self):
        # 1. Materials, from the Materials page. Sugar is already there.
        self.post("inventory:materials", {
            "sourcing": "buy", "kind": "raw", "name": "Mango pulp", "code": "", "category": self.fruit.pk,
            "base_uom": self.kg.pk, "track_batch": "on", "track_expiry": "on", "shelf_life_days": "540",
            "qc_required": "on", "default_warehouse": self.rms.pk, "standard_cost": "190"})
        self.post("inventory:materials", {
            "sourcing": "buy", "kind": "packaging", "name": "PET bottle 250 ml", "code": "",
            "category": self.packs.pk, "base_uom": self.pcs.pk, "default_warehouse": self.rms.pk})
        pulp, bottle = self.item_named("Mango pulp"), self.item_named("PET bottle 250 ml")
        self.assertEqual((pulp.code, bottle.code), ("RM-0002", "PK-0001"))

        # 2. The product: made here, by the bottle, also a carton of 24; no price yet.
        self.post("inventory:products", {
            "sourcing": "make", "name": "Mango juice 250 ml", "code": "", "category": self.juices.pk,
            "base_uom": self.pcs.pk, "pack_uom": self.carton.pk, "pack_factor": "24", "keep_by": "quantity",
            "min_stock": "100", "track_batch": "on", "track_expiry": "on", "shelf_life_days": "270",
            "qc_required": "on", "default_warehouse": self.fgw.pk})
        juice = self.item_named("Mango juice 250 ml")
        page = self.client.get(reverse("inventory:product", args=[juice.pk]))
        self.assertEqual([s["state"] for s in page.context["steps"]][:2], ["done", "now"])     # recipe next
        self.assertContains(page, "Set the price once the cost is known.")

        # 3. Its recipe: one batch makes 100; what it uses.
        self.post("production:recipe_batch", {"batch_size": "100"}, juice.pk, fetch=False)
        for material, qty in ((pulp, "12"), (self.sugar, "3"), (bottle, "100")):
            self.post("production:recipe_add", {"material": material.pk, "qty": qty, "scrap_pct": ""}, juice.pk,
                      fetch=False)

        # 4. Buy with an order: pulp 50 KG at 200, bottles 1,000 at 5; approved by someone else.
        self.post("purchasing:orders", {"supplier": self.supplier.pk, "branch": self.branch.pk,
                                        "order_date": iso(0)}, fetch=False)
        from purchasing.models import GoodsReceipt, GoodsReceiptLine, PurchaseOrder

        order = PurchaseOrder.all_objects.get(company=self.company)
        for item, qty, uom, price in ((pulp, "50", self.kg, "200"), (bottle, "1000", self.pcs, "5")):
            self.post("purchasing:order_line_add", {"item": item.pk, "qty": qty, "uom": uom.pk, "unit_price": price,
                                                    "discount_pct": "0", "vat_rate": "0"}, order.pk, fetch=False)
        self.post("purchasing:order_action", {}, order.pk, "submit", fetch=False)
        self.as_approver("purchasing:order_action", order.pk, "approve")
        order.refresh_from_db()
        self.assertEqual(order.status, "approved")
        self.assertContains(self.client.get(reverse("purchasing:order_detail", args=[order.pk])), "Receive goods")

        # 5. Receive it from the order; the pulp waits for its quality check.
        self.post("purchasing:receipts", {"order": order.pk, "warehouse": self.rms.pk, "received_date": iso(2),
                                          "challan_no": "MG-77"}, fetch=False)
        receipt = GoodsReceipt.all_objects.get(company=self.company)
        for line in GoodsReceiptLine.all_objects.filter(receipt=receipt, item=pulp):
            GoodsReceiptLine.all_objects.filter(pk=line.pk).update(batch_no="MP-1", mfg_date=DAY)
        reply = self.client.post(reverse("purchasing:receipt_action", args=[receipt.pk, "post"]), follow=True)
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, "posted", [str(m) for m in reply.context["messages"]])
        self.assertEqual((self.stock(pulp, self.rms), self.stock(bottle, self.rms)), (D("50"), D("1000")))
        waiting = self.client.get(reverse("inventory:qc"))
        self.assertContains(waiting, "Mango pulp")
        held = Batch.all_objects.get(item=pulp)
        self.assertEqual(held.status, "quarantine")
        self.post("inventory:qc_decide", {"result": "passed"}, held.pk)
        self.assertEqual(self.reload(held).status, "released")

        # 6. Stock in, a quick purchase: sugar 20 KG at 120, priced 150 to sell (raw material sale).
        page = self.client.get(reverse("inventory:stock_in_new") + f"?item={self.sugar.pk}")
        self.assertContains(page, f'data-store=""')       # sugar has no usual store: none filled
        self.post("inventory:stock_in_new", {
            "source": "purchase", "supplier": self.supplier.pk, "store": self.rms.pk, "day": iso(2),
            "item_1": self.sugar.pk, "qty_1": "20", "unit_1": self.kg.pk, "cost_1": "120", "sale_1": "150",
            "batch_1": "SG-1", "mfg_1": iso(0)})
        self.assertEqual(self.reload(self.sugar).sale_price, D("150"))
        page = self.client.get(reverse("inventory:stock_in_new"))
        self.assertContains(page, f'data-cost="120" data-cost-from="last price paid"')

        # 7. The cost is known: (12 x 200 + 3 x 120 + 100 x 5) / 100 = 32.60 a bottle. Price it at 45.
        page = self.client.get(reverse("inventory:product", args=[juice.pk]))
        self.assertEqual(page.context["price_cost"], D("32.60"))
        refused = self.client.post(reverse("inventory:item_price", args=[juice.pk]), {"price": "20"}, **FETCH).json()
        self.assertIn("Below cost", refused["errors"]["price"][0])
        self.post("inventory:item_price", {"price": "45"}, juice.pk)

        # 8. Make 2 batches: plan, start (materials out), finish 200 into the finished goods store.
        body = self.post("production:plan", {"batches": "2", "planned_date": iso(3), "into": self.fgw.pk,
                                             "action": "save"}, juice.pk)
        from production.models import ProductionOrder

        run = ProductionOrder.all_objects.get(company=self.company)
        self.assertEqual(body["redirect"], reverse("production:run", args=[run.pk]))
        self.post("production:run_start", {"work_date": iso(3)}, run.pk)
        self.assertEqual((self.stock(pulp), self.stock(self.sugar), self.stock(bottle)),
                         (D("26"), D("14"), D("800")))
        self.post("production:run_finish", {"good": "200", "output_date": iso(3), f"into-{self.fgw.pk}": "200"},
                  run.pk)
        self.assertEqual(self.stock(juice, self.fgw), D("200"))

        # 9. It waits for its quality check: Sell says why, then pass it.
        sale = {"customer": self.customer.pk, "store": self.fgw.pk, "day": iso(4), "item_1": juice.pk,
                "qty_1": "5", "unit_1": self.carton.pk, "invoice": "on"}
        refused = self.client.post(reverse("inventory:sale_new"), sale, **FETCH).json()
        self.assertFalse(refused["ok"])
        self.assertIn("waits for a quality check", " ".join([refused["message"], *refused.get("form_errors", [])]))
        made = Batch.all_objects.get(item=juice)
        self.post("inventory:qc_decide", {"result": "passed"}, made.pk)

        # 10. Sell the juice: 5 cartons = 120 bottles, at the price (45 x 24 a carton), invoiced.
        self.post("inventory:sale_new", sale)
        self.assertEqual(self.stock(juice, self.fgw), D("80"))
        from sales.models import Delivery, SalesInvoice

        bill = SalesInvoice.all_objects.get(company=self.company)
        self.assertEqual(bill.total, D("5400.00"))                 # 120 x 45, no VAT
        challan = Delivery.all_objects.get(company=self.company)
        text = pdf_text(self.client.get(reverse("sales:delivery_pdf", args=[challan.pk])).content)
        self.assertIn("DELIVERY CHALLAN", text)
        text = pdf_text(self.client.get(reverse("sales:invoice_pdf", args=[bill.pk])).content)
        for words in ("TAX INVOICE", "5,400.00", "Taka Five Thousand Four Hundred"):
            self.assertIn(words, text)

        # 11. Sell a raw material: 5 KG of sugar from the raw store.
        self.post("inventory:sale_new", {"customer": self.customer.pk, "store": self.rms.pk, "day": iso(4),
                                         "item_1": self.sugar.pk, "qty_1": "5", "unit_1": self.kg.pk})
        self.assertEqual(self.stock(self.sugar), D("9"))

        # 12. The customer returns 2 bottles in good condition.
        self.post("inventory:stock_in_new", {"source": "return", "customer": self.customer.pk, "store": self.fgw.pk,
                                             "day": iso(5), "reason": "Wrong flavour", "item_1": juice.pk,
                                             "qty_1": "2", "unit_1": self.pcs.pk, "batch_1": made.batch_no,
                                             "condition_1": "good"})
        self.assertEqual(self.stock(juice, self.fgw), D("82"))

        # 13. Move 24 bottles to the depot; it receives them.
        self.post("inventory:move_new", {"from_store": self.fgw.pk, "to_store": self.depot.pk, "day": iso(5),
                                         "item_1": juice.pk, "qty_1": "24", "unit_1": self.pcs.pk})
        from inventory.models import StockTransfer

        move = StockTransfer.all_objects.get(company=self.company)
        self.client.post(reverse("inventory:move_receive", args=[move.pk]), {}, **FETCH)
        self.assertEqual((self.stock(juice, self.fgw), self.stock(juice, self.depot)), (D("58"), D("24")))

        # 14. Adjust: 2 KG of sugar damaged, approved by someone else.
        self.post("inventory:store_list", {"warehouse": self.rms.pk, "adjustment_date": iso(6), "reason": "damage",
                                           "note": ""}, "adjustments", fetch=False)
        from inventory.models import StockAdjustment, StockCount

        adjustment = StockAdjustment.all_objects.get(company=self.company, count__isnull=True)
        sugar_batch = Batch.all_objects.get(item=self.sugar, batch_no="SG-1")
        self.post("inventory:store_line_add", {"item": self.sugar.pk, "batch": sugar_batch.pk, "qty_change": "-2",
                                               "uom": self.kg.pk, "note": "Torn bag"}, "adjustments", adjustment.pk,
                  fetch=False)
        page = self.client.get(reverse("inventory:store_detail", args=["adjustments", adjustment.pk]))
        self.assertContains(page, "After this")
        self.post("inventory:store_action", {}, "adjustments", adjustment.pk, "submit", fetch=False)
        self.as_approver("inventory:store_action", "adjustments", adjustment.pk, "approve")
        self.assertEqual(self.reload(adjustment).status, "posted")
        self.assertEqual(self.stock(self.sugar), D("7"))

        # 15. Count the bottles in the raw store: 790 found where the software says 800.
        self.post("inventory:store_list", {"warehouse": self.rms.pk, "count_date": iso(7), "note": ""}, "counts",
                  fetch=False)
        count = StockCount.all_objects.get(company=self.company)
        with self.tenant():
            lines = {line.item_id: line for line in count.lines.all()}
        counted = {f"counted_{line.pk}": str(line.system_qty) for line in lines.values()}
        counted[f"counted_{lines[bottle.pk].pk}"] = "790"
        self.client.post(reverse("inventory:count_save", args=[count.pk]), counted)
        self.post("inventory:store_action", {}, "counts", count.pk, "submit", fetch=False)
        self.as_approver("inventory:store_action", "counts", count.pk, "approve")
        self.assertEqual(self.reload(count).status, "approved")
        self.assertEqual(self.stock(bottle), D("790"))

        # 16. Where everything is now, and the pages that show it.
        self.assertEqual((self.stock(juice), self.stock(pulp), self.stock(self.sugar), self.stock(bottle)),
                         (D("82"), D("26"), D("7"), D("790")))
        stores = self.client.get(reverse("inventory:stores"))
        self.assertContains(stores, f'{reverse("inventory:stock_now")}?store={self.rms.pk}')
        for name in ("inventory:start", "inventory:materials", "inventory:products", "inventory:stock_now",
                     "inventory:stock_in", "inventory:moves", "inventory:sales", "production:runs",
                     "purchasing:orders", "purchasing:receipts", "inventory:qc"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        for report in REPORTS:
            reply = self.client.get(reverse(report.url_name), {"start": iso(0), "end": iso(30), "on": iso(30)})
            self.assertEqual(reply.status_code, 200, report.url_name)


class FullFlowTests(FullFlow, TestCase):
    def test_mango_juice_from_materials_to_sales(self):
        self.run_flow()
