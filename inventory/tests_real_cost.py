"""The real cost of what is bought (2026-11-03, purchasing/landed.py).

The delivery cost paid on a purchase, and the VAT unless the company claims
it back, are shared over the lines by their value; each line goes into stock
at its real cost, and the cost of a product made from it follows. Stock in and
a receipt against a purchase order work the same way.
"""

import datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from common.tenant import use_company
from inventory import pricing
from inventory.models import InventorySettings, StockMove
from inventory.testing import DAY, D
from inventory.tests_phase9b import FlowFixture
from purchasing import landed, receipts
from purchasing.models import GoodsReceipt
from purchasing.tests_flow import PurchaseFixture


def moved_at(item):
    """The unit cost ``item`` last went into stock at."""
    return StockMove.all_objects.filter(item=item, qty__gt=0).latest("pk").unit_cost


class StockInRealCostTests(FlowFixture, TestCase):
    """Sugar 50 KG at 80 (4,000) and bearings 100 PCS at 10 (1,000): subtotal
    5,000; 15 % VAT is 750 and the delivery 250, so 1,000 is added - one fifth
    of each line's value."""

    def buy(self, **extra):
        return self.post("stock_in_new", {
            "source": "purchase", "supplier": self.supplier.pk, "store": self.rms.pk, "day": DAY.isoformat(),
            "item_1": self.sugar.pk, "qty_1": "50", "unit_1": self.kg.pk, "cost_1": "80", "batch_1": "SB1",
            "expiry_1": "2027-05-01",
            "item_2": self.bearing.pk, "qty_2": "100", "unit_2": self.pcs.pk, "cost_2": "10", **extra}).json()

    def setting(self, value):
        with use_company(self.company):
            InventorySettings.objects.update_or_create(company=self.company, defaults={"purchase_vat": value})

    def test_vat_and_delivery_go_into_the_real_cost(self):
        body = self.buy(vat_rate="15", delivery_cost="250")
        self.assertTrue(body["ok"], body)
        self.assertIn("Total paid 6,000.00 Tk with VAT 750.00 and delivery 250.00.", body["message"])
        self.assertIn("real cost", body["message"])
        # Each line carries its share: 4,000 of 5,000 carries 800, 1,000 carries 200.
        self.assertEqual(moved_at(self.sugar), D("96"))
        self.assertEqual(moved_at(self.bearing), D("12"))
        receipt = GoodsReceipt.all_objects.get(company=self.company)
        self.assertEqual((receipt.vat_amount, receipt.delivery_cost, receipt.vat_in_cost), (D("750"), D("250"), True))
        with use_company(self.company):
            self.assertEqual(sorted(receipt.lines.values_list("extra_cost", flat=True)), [D("200"), D("800")])
        # Prices, margins and recipes count the real cost.
        self.assertEqual(pricing.cost_per_unit(self.company.pk, self.sugar), D("96"))

    def test_a_company_that_claims_the_vat_back_adds_only_the_delivery(self):
        self.setting(InventorySettings.PurchaseVat.CLAIM)
        self.buy(vat_rate="15", delivery_cost="250")
        self.assertEqual(moved_at(self.sugar), D("84"))       # 80 + 250 x 4/5 / 50
        self.assertEqual(moved_at(self.bearing), D("10.5"))
        receipt = GoodsReceipt.all_objects.get(company=self.company)
        self.assertEqual((receipt.vat_amount, receipt.vat_in_cost), (D("750"), False))

    def test_the_vat_amount_typed_last_wins(self):
        self.buy(vat_rate="15", vat_amount="500", vat_by="amount")
        receipt = GoodsReceipt.all_objects.get(company=self.company)
        self.assertEqual((receipt.vat_by, receipt.vat_amount, receipt.vat_rate), ("amount", D("500"), D("10")))
        self.assertEqual(moved_at(self.sugar), D("88"))

    def test_nothing_added_leaves_the_cost_as_written(self):
        self.buy()
        self.assertEqual(moved_at(self.sugar), D("80"))
        self.assertEqual(moved_at(self.bearing), D("10"))

    def test_the_next_purchase_starts_from_the_suppliers_price_not_the_real_cost(self):
        """Otherwise the VAT and delivery would be counted twice."""
        self.buy(vat_rate="15", delivery_cost="250")
        page = self.client.get(reverse("inventory:stock_in_new"))
        options = [option for option in page.context["items"] if option["item"].pk == self.sugar.pk]
        self.assertEqual(options[0]["cost"], "80")

    def test_the_page_has_the_delivery_box_and_says_how_it_counts(self):
        page = self.client.get(reverse("inventory:stock_in_new"))
        self.assertContains(page, 'name="delivery_cost"')
        self.assertContains(page, 'data-vat-in-cost="1"')
        self.assertContains(page, "The VAT and the delivery cost are part of what the goods cost")
        self.setting(InventorySettings.PurchaseVat.CLAIM)
        page = self.client.get(reverse("inventory:stock_in_new"))
        self.assertContains(page, 'data-vat-in-cost="0"')
        self.assertContains(page, "The VAT is claimed back")


class OrderReceiptRealCostTests(PurchaseFixture, TestCase):
    """A goods receipt against a purchase order: the VAT comes from the order,
    the delivery cost is written on the receipt, and posting counts both."""

    def test_the_vat_of_the_order_and_the_delivery_go_into_the_cost(self):
        _, order, order_line = self.approved_order(bags="6", price="6500")
        order_line.vat_rate = D("10")
        order_line.save()
        receipt = receipts.create_receipt(**self.kw, values={
            "order": order, "supplier": None, "warehouse": self.rms,
            "received_date": DAY + datetime.timedelta(days=2), "challan_no": "", "invoice_no": "",
            "vehicle_no": "", "note": ""})
        self.assertEqual((receipt.vat_by, receipt.vat_rate), ("rate", D("10")))
        receipts.set_costs(**self.kw, receipt_id=receipt.pk,
                           values={"vat_rate": D("10"), "vat_amount": None, "delivery_cost": D("1950")})
        with use_company(self.company):
            receipt.lines.update(mfg_date=DAY)
        receipts.post_receipt(**self.kw, receipt_id=receipt.pk)
        # 6 bags x 6,500 = 39,000; VAT 3,900 + delivery 1,950 = 5,850: 15 % more.
        # One bag is 50 KG: 6,500 x 1.15 / 50 = 149.50 a KG.
        self.assertEqual(moved_at(self.sugar), D("149.5"))
        self.assertEqual(self.average(), D("149.5"))

    def test_the_receipt_page_shows_the_bill_and_takes_the_costs(self):
        _, order, order_line = self.approved_order(bags="2", price="6500")
        receipt = receipts.create_receipt(**self.kw, values={
            "order": order, "supplier": None, "warehouse": self.rms,
            "received_date": DAY + datetime.timedelta(days=2), "challan_no": "", "invoice_no": "",
            "vehicle_no": "", "note": ""})
        self.client.force_login(self.owner)
        response = self.client.post(reverse("purchasing:receipt_costs", args=[receipt.pk]),
                                    {"vat_rate": "", "vat_amount": "1300", "delivery_cost": "1300"}, follow=True)
        self.assertContains(response, "VAT and delivery cost saved")
        self.assertContains(response, "Bill and real cost")
        self.assertContains(response, "15,600.00 Tk")         # 13,000 + 1,300 + 1,300
        self.assertContains(response, "7,800.00")             # 6,500 + its 1,300 share a bag
        receipt.refresh_from_db()
        self.assertEqual((receipt.vat_by, receipt.vat_amount, receipt.delivery_cost),
                         ("amount", D("1300"), D("1300")))

    def test_a_posted_receipt_cannot_change_its_costs(self):
        _, order, order_line = self.approved_order(bags="1")
        receipt = self.receive(order, order_line, "1")
        with self.assertRaisesMessage(Exception, "only a draft can change"):
            receipts.set_costs(**self.kw, receipt_id=receipt.pk,
                               values={"vat_rate": D("5"), "vat_amount": None, "delivery_cost": None})


class ShareTests(TestCase):
    def test_the_shares_add_up_to_the_paisa(self):
        shares = landed.share(D("1.00"), {1: D("1"), 2: D("1"), 3: D("1")})
        self.assertEqual(sum(shares.values()), D("1.00"))
        self.assertEqual(sorted(shares.values()), [D("0.33"), D("0.33"), D("0.34")])

    def test_by_value(self):
        self.assertEqual(landed.share(D("100"), {"a": D("300"), "b": D("100")}), {"a": D("75"), "b": D("25")})

    def test_nothing_to_share_or_no_weight(self):
        self.assertEqual(landed.share(D("0"), {"a": D("5")}), {"a": D("0")})
        self.assertEqual(landed.share(D("10"), {"a": D("0")}), {"a": D("0")})

    def test_vat_by_rate_or_amount(self):
        self.assertEqual(landed.vat_on(D("200"), by="rate", rate=D("15"), amount=None), (D("15"), D("30.00")))
        self.assertEqual(landed.vat_on(D("200"), by="amount", rate=None, amount=D("30")), (D("15.00"), D("30.00")))
        self.assertEqual(landed.vat_on(D("0"), by="amount", rate=None, amount=D("30")), (Decimal("0"), D("30.00")))
