"""The Overview reports (2026-11-03): profit on every sale (with or without an
invoice), every stock move, the daily summary and the activity log."""

import datetime

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from inventory.models import Item
from inventory.testing import D
from production.tests_flow import ProductionFixture
from sales.models import Customer

FETCH = {"HTTP_X_REQUESTED_WITH": "fetch"}


class OverviewFixture(ProductionFixture):
    """Sugar in stock at 125 a KG; 5 KG sold at 150 without an invoice, 2 KG
    at 160 with one: sales 1,070, cost 875, profit 195."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)
        self.today = timezone.localdate()
        with self.tenant():
            self.customer = Customer.objects.create(company=self.company, code="C1", name="City Shop",
                                                    customer_type="retailer", payment_terms_days=15)
        for qty, price, invoice in (("5", "150", False), ("2", "160", True)):
            body = self.client.post(reverse("inventory:sale_new"), {
                "customer": self.customer.pk, "store": self.rms.pk, "day": self.today.isoformat(),
                "item_1": self.sugar.pk, "qty_1": qty, "unit_1": self.kg.pk, "price_1": price,
                **({"invoice": "on"} if invoice else {})}, **FETCH).json()
            self.assertTrue(body["ok"], body)

    def report(self, slug, **params):
        dates = {"date_from": self.today.replace(day=1).isoformat(), "date_to": self.today.isoformat()}
        return self.client.get(reverse(f"inventory:report_{slug.replace('-', '_')}"), {**dates, **params})

    @staticmethod
    def summary(page):
        return dict(page.context["result"].summary)


class ProfitTests(OverviewFixture, TestCase):
    def test_every_sale_counts_with_or_without_an_invoice(self):
        page = self.report("profit")
        self.assertEqual(self.summary(page)["Sales"], "1,070.00")
        self.assertEqual(self.summary(page)["Cost of the goods"], "875.00")
        self.assertEqual(self.summary(page)["Profit"], "195.00")
        [row] = page.context["result"].rows
        self.assertEqual(row[:4], ["RM-0001", "Sugar", D("7"), "KG"])
        self.assertEqual(row[-3:], [D("875.00"), D("195.00"), D("18.2")])

    def test_grouped_by_customer_day_and_sale(self):
        self.assertEqual(self.report("profit", by="customer").context["result"].rows,
                         [["City Shop", 2, D("1070.00"), D("875.00"), D("195.00"), D("18.2")]])
        self.assertEqual(len(self.report("profit", by="sale").context["result"].rows), 2)
        [day] = self.report("profit", by="day").context["result"].rows
        self.assertEqual(day[0], self.today)

    def test_needs_the_cost_permission(self):
        viewer = self.member("viewer@inv.test", "employee")
        self.client.force_login(viewer)
        self.assertIn(self.report("profit").status_code, (302, 403))


class MovesAndSummaryTests(OverviewFixture, TestCase):
    def test_every_move_with_who_did_it(self):
        page = self.report("all-moves")
        rows = page.context["result"].rows
        sold = [row for row in rows if row[2] == "Sold"]
        self.assertEqual([row[8] for row in sold], [D("5"), D("2")])
        self.assertTrue(all(row[-1] for row in sold))
        self.assertContains(page, "Every kind of move")
        only = self.report("all-moves", move="delivery", person=self.owner.pk).context["result"].rows
        self.assertEqual(len(only), 2)

    def test_the_day_in_money(self):
        page = self.report("daily-summary")
        summary = self.summary(page)
        self.assertEqual((summary["Sold (at cost)"], summary["Sales"], summary["Profit"]),
                         ("875.00", "1,070.00", "195.00"))
        # The opening stock was posted before this month: 12,500 sugar + pulp 27,300 + bottles and caps.
        start = D(summary["Stock value at the start"].replace(",", ""))
        end = D(summary["Stock value at the end"].replace(",", ""))
        self.assertEqual(start - end, D("875.00"))
        self.assertEqual(len(self.report("daily-summary", by="month").context["result"].rows), 1)


class ActivityTests(OverviewFixture, TestCase):
    def test_who_did_what(self):
        Item.all_objects.filter(pk=self.sugar.pk)  # the sales above are recorded
        from inventory import pricing

        pricing.set_price(actor=self.owner, company_id=self.company.pk, item=self.sugar, price=D("150"))
        page = self.report("activity", area="stock")
        rows = page.context["result"].rows
        price = next(row for row in rows if row[3] == "Item price set")
        self.assertEqual(price[2], "Stock and items")
        self.assertIn("price: — → 150", price[5])
        self.assertIsInstance(price[0], datetime.datetime)
        self.assertContains(page, "Every area")

    def test_only_the_owner_admin_or_auditor(self):
        manager = self.member("manager@inv.test", "manager")
        self.client.force_login(manager)
        index = self.client.get(reverse("inventory:reports"))
        if index.status_code == 200:
            self.assertNotContains(index, reverse("inventory:report_activity"))
        self.assertIn(self.report("activity").status_code, (302, 403))
