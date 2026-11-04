"""Reports part 2 (2026-11-04): purchases by supplier, sales by item,
write-offs, below-cost sales and price changes, quality checks and batch trace."""

from django.test import TestCase
from django.urls import reverse

from inventory import pricing, qc
from inventory.models import Batch, StockBalance
from inventory.testing import D
from inventory.tests_overview_reports import FETCH, OverviewFixture
from production import making, planning
from purchasing.services import SUPPLIER


class SupplierAndItemTests(OverviewFixture, TestCase):
    def test_purchases_by_supplier_at_real_cost(self):
        from inventory import masters

        supplier = masters.create(SUPPLIER, actor=self.owner, company_id=self.company.pk, values={
            "code": "S1", "name": "Sugar Mills", "email": "", "payment_terms_days": 30})
        body = self.client.post(reverse("inventory:stock_in_new"), {
            "source": "purchase", "supplier": supplier.pk, "store": self.rms.pk, "day": self.today.isoformat(),
            "item_1": self.sugar.pk, "qty_1": "40", "unit_1": self.kg.pk, "cost_1": "100", "batch_1": "SB9",
            "expiry_1": "2027-05-01", "vat_rate": "15", "delivery_cost": "200"}, **FETCH).json()
        self.assertTrue(body["ok"], body)
        [row] = self.report("supplier-purchases").context["result"].rows
        # 4,000 at the price + 600 VAT + 200 delivery.
        self.assertEqual(row[:5], ["Sugar Mills", 1, D("4000.00"), D("800.00"), D("4800.00")])
        [row] = self.report("supplier-purchases", by="item").context["result"].rows
        self.assertEqual(row[1:4], ["RM-0001", "Sugar", D("40")])

    def test_sales_by_item_best_first(self):
        [row] = self.report("sales-by-item").context["result"].rows
        self.assertEqual(row[0:4], [1, "RM-0001", "Sugar", D("7")])
        self.assertEqual((row[-2], row[-1]), (D("1070.00"), D("100.0")))


class BelowCostTests(OverviewFixture, TestCase):
    def test_a_sale_below_cost_with_its_reason(self):
        body = self.client.post(reverse("inventory:sale_new"), {
            "customer": self.customer.pk, "store": self.rms.pk, "day": self.today.isoformat(),
            "item_1": self.sugar.pk, "qty_1": "1", "unit_1": self.kg.pk, "price_1": "100",
            "below_cost": "on", "below_cost_reason": "Old stock"}, **FETCH).json()
        self.assertTrue(body["ok"], body)
        [row] = self.report("below-cost").context["result"].rows
        self.assertEqual((row[7], row[8], row[9], row[10]), (D("100.00"), D("125.00"), D("-25.00"), "Old stock"))

    def test_price_changes_and_who(self):
        pricing.set_price(actor=self.owner, company_id=self.company.pk, item=self.sugar, price=D("150"))
        pricing.set_price(actor=self.owner, company_id=self.company.pk, item=self.sugar, price=D("170"))
        rows = self.report("below-cost", by="prices").context["result"].rows
        self.assertEqual([(row[4], row[5], row[6]) for row in rows],
                         [(D("150"), D("170"), D("20")), (None, D("150"), None)])


class MakingTests(OverviewFixture, TestCase):
    """2,000 bottles planned; 1,900 good and 100 damaged."""

    def setUp(self):
        super().setUp()
        self.recipe()
        order, _plan = planning.save(actor=self.owner, company_id=self.company.pk, product=self.juice,
                                     qty=D("2000"), into=self.fgw, day=self.today)
        making.start(actor=self.owner, company_id=self.company.pk, order_id=order.pk, work_date=self.today)
        self.order, _output = making.finish(actor=self.owner, company_id=self.company.pk, order_id=order.pk,
                                            values={"output_date": self.today, "good": D("1900"),
                                                    "damaged": D("100")})

    def test_damaged_in_making_is_written_off(self):
        rows = self.report("write-offs").context["result"].rows
        damaged = next(row for row in rows if row[1] == "Damaged in making")
        self.assertEqual((damaged[2], damaged[7], damaged[9]),
                         (self.order.number, D("100"), (D("100") * self.order.unit_cost).quantize(D("0.01"))))

    def test_batch_trace_back_to_the_pulp_and_forward_to_stock(self):
        made = Batch.all_objects.get(item=self.juice)
        rows = self.report("batch-trace", batch=made.pk).context["result"].rows
        what = [(row[1], row[3], row[6]) for row in rows]
        self.assertIn(("Back", "Made", made.batch_no), what)
        self.assertIn(("Back", "Used to make it", "P1"), what)
        self.assertIn(("Back", "First count", "P1"), what)
        self.assertIn(("Forward", "In stock now", made.batch_no), what)
        # And forward from the pulp to the juice it made.
        pulp = Batch.all_objects.get(item=self.pulp, batch_no="P1")
        rows = self.report("batch-trace", batch=pulp.pk).context["result"].rows
        self.assertIn(("Forward", "Made from it", made.batch_no), [(row[1], row[3], row[6]) for row in rows])
        self.assertContains(self.report("batch-trace"), "Pick a batch to trace.")


class QualityCheckTests(OverviewFixture, TestCase):
    def test_waiting_then_passed(self):
        Batch.all_objects.filter(item=self.sugar).update(status=Batch.Status.QUARANTINE)
        rows = self.report("quality-checks").context["result"].rows
        self.assertEqual([(row[3], row[7]) for row in rows], [("S1", "Waiting for QC")])
        batch = Batch.all_objects.get(item=self.sugar)
        self.assertTrue(StockBalance.all_objects.filter(batch=batch).exists())
        qc.decide(actor=self.owner, company_id=self.company.pk, batch_id=batch.pk, passed=True, notes="Clean")
        rows = self.report("quality-checks").context["result"].rows
        self.assertEqual([(row[3], row[7], row[8]) for row in rows], [("S1", "Passed", "Clean")])
