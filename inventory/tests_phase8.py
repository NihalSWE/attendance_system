"""Phase 8 of the redesign (2026-10-27, docs/inventory/PRODUCTION_PLAN.md §10, §15):
warranty as a company switch (off by default), warranty months and serial
numbers on items, serial numbers made at production, entered at receipt,
scanned out on deliveries, and claims - covered or not, replace / repair /
refund / refuse - with their summary."""

import datetime

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from inventory import masters, warranty
from inventory.models import Item, SerialNumber, StockBalance, WarrantyClaim
from inventory.testing import D
from inventory.tests_phase7 import RunFixture
from sales import deliveries, orders
from sales.tests_flow import DAY, SalesFixture

FETCH = {"HTTP_X_REQUESTED_WITH": "fetch"}


def switch_on(test):
    masters.update_settings(actor=test.owner, company_id=test.company.pk, values={"warranty_on": True})


class WarrantyFixture(SalesFixture):
    def setUp(self):
        super().setUp()
        switch_on(self)
        Item.all_objects.filter(pk=self.juice.pk).update(warranty_months=12, serial_numbers=True)
        self.juice.refresh_from_db()
        self.codes = ["JU-001", "JU-002", "JU-003", "JU-004"]
        warranty.register(**self.kw, item=self.juice, serials=self.codes, received_on=DAY)
        self.client.force_login(self.owner)

    def delivered(self, qty="3"):
        order = self.order(qty)
        orders.confirm(**self.kw, order_id=order.pk)
        delivery = deliveries.create_delivery(**self.kw, values={"order": order, "delivery_date": DAY,
                                                                 "vehicle_no": "", "driver": "", "note": ""})
        deliveries.post_delivery(**self.kw, delivery_id=delivery.pk)
        delivery.refresh_from_db()
        return delivery

    def claim(self, **values):
        return warranty.record_claim(**self.kw, values={"problem": "Leaks", "outcome": "repair",
                                                        "claim_date": DAY + datetime.timedelta(days=30), **values})


class SwitchTests(SalesFixture, TestCase):
    def test_off_by_default_and_nothing_shows(self):
        self.client.force_login(self.owner)
        self.assertFalse(warranty.warranty_on(self.company.pk))
        page = self.client.get(reverse("inventory:warranty"))
        self.assertContains(page, "Warranty is off")
        products = self.client.get(reverse("inventory:products"))
        self.assertNotContains(products, 'name="warranty_months"')
        self.assertNotContains(products, f'href="{reverse("inventory:warranty")}"')
        switch_on(self)
        products = self.client.get(reverse("inventory:products"))
        self.assertContains(products, 'name="warranty_months"')
        self.assertContains(products, f'href="{reverse("inventory:warranty")}"')

    def test_months_are_calendar_months(self):
        self.assertEqual(warranty.add_months(datetime.date(2026, 1, 31), 1), datetime.date(2026, 2, 28))
        self.assertEqual(warranty.add_months(datetime.date(2026, 10, 27), 18), datetime.date(2028, 4, 27))


class SerialTests(WarrantyFixture, TestCase):
    def test_entered_once_and_only_for_items_that_take_them(self):
        with self.assertRaisesMessage(ValidationError, "Already entered: JU-001"):
            warranty.register(**self.kw, item=self.juice, serials=["JU-001", "JU-009"], received_on=DAY)
        with self.assertRaises(ValidationError):
            warranty.register(**self.kw, item=self.sugar, serials=["S-1"], received_on=DAY)
        self.assertEqual(warranty.codes("ju-1, ju-2\nJU-1  ju-3"), ["JU-1", "JU-2", "JU-3"])

    def test_scanned_out_on_a_delivery(self):
        delivery = self.delivered("3")
        with self.assertRaisesMessage(ValidationError, "More serial numbers than the 3 delivered"):
            warranty.deliver(**self.kw, delivery=delivery, serials=self.codes)
        with self.assertRaisesMessage(ValidationError, "NOPE: no such serial number"):
            warranty.deliver(**self.kw, delivery=delivery, serials=["NOPE"])
        warranty.deliver(**self.kw, delivery=delivery, serials=self.codes[:3])
        unit = SerialNumber.all_objects.get(serial="JU-001")
        self.assertEqual((unit.status, unit.customer, unit.delivered_on), ("delivered", self.customer, DAY))
        with self.assertRaisesMessage(ValidationError, "with the customer already"):
            warranty.deliver(**self.kw, delivery=delivery, serials=["JU-001"])


class ClaimTests(WarrantyFixture, TestCase):
    def setUp(self):
        super().setUp()
        warranty.deliver(**self.kw, delivery=self.delivered("3"), serials=self.codes[:3])

    def test_covered_replace_takes_the_faulty_unit_back_and_sends_a_new_one(self):
        found = warranty.lookup(self.company.pk, "ju-001", DAY + datetime.timedelta(days=30))
        self.assertTrue(found["covered"])
        self.assertEqual(found["until"], datetime.date(2027, 10, 1))
        before = StockBalance.all_objects.filter(item=self.juice, warehouse=self.fgw)
        before = sum(row.qty_on_hand for row in before)
        claim = self.claim(serial="JU-001", outcome="replace", back_to=self.rejected, replaced_from=self.fgw)
        self.assertEqual((claim.covered, claim.customer), (True, self.customer))
        after = sum(row.qty_on_hand for row in StockBalance.all_objects.filter(item=self.juice, warehouse=self.fgw))
        self.assertEqual(after, before - 1)
        self.assertEqual(StockBalance.all_objects.get(item=self.juice, warehouse=self.rejected).qty_on_hand, D("1"))
        self.assertEqual(SerialNumber.all_objects.get(serial="JU-001").status, "returned")
        with self.assertRaisesMessage(ValidationError, "replaced or refunded already"):
            self.claim(serial="JU-001", outcome="refund")

    def test_out_of_warranty_needs_a_reason_to_cover_it(self):
        late = DAY + datetime.timedelta(days=400)
        with self.assertRaisesMessage(ValidationError, "Out of warranty since 01 Oct 2027"):
            self.claim(serial="JU-002", claim_date=late)
        claim = self.claim(serial="JU-002", claim_date=late, reason="Good customer", cost=D("150"))
        self.assertFalse(claim.covered)
        with self.assertRaises(ValidationError) as caught:
            self.claim(serial="JU-003", outcome="refuse")
        self.assertIn("reason", caught.exception.message_dict)

    def test_without_a_serial_the_item_and_its_delivery_date(self):
        Item.all_objects.filter(pk=self.bearing.pk).update(warranty_months=6)
        bearing = Item.all_objects.get(pk=self.bearing.pk)
        with self.assertRaises(ValidationError) as caught:
            self.claim(item=bearing)
        self.assertIn("sold_on", caught.exception.message_dict)
        claim = self.claim(item=bearing, sold_on=DAY, customer=self.customer)
        self.assertEqual(claim.covered_until, datetime.date(2027, 4, 1))
        with self.assertRaisesMessage(ValidationError, "scan the unit's"):
            self.claim(item=self.juice, sold_on=DAY)

    def test_the_summary_by_item(self):
        self.claim(serial="JU-001", cost=D("200"))
        self.claim(serial="JU-002", outcome="refuse", reason="Dropped")
        per_item, problems = warranty.summary(self.company.pk)
        self.assertEqual([(row["claims"], row["refused"], row["cost"]) for row in per_item], [(2, 1, D("200"))])
        self.assertEqual(problems, [("leaks", 2)])


class PageTests(WarrantyFixture, TestCase):
    def test_claim_lookup_and_labels_on_the_page(self):
        warranty.deliver(**self.kw, delivery=self.delivered("1"), serials=["JU-004"])
        body = self.client.get(reverse("inventory:serial_lookup"), {"serial": "ju-004"}).json()
        self.assertTrue(body["covered"])
        self.assertIn("covered until 01 Oct 2027", body["text"])
        reply = self.client.post(reverse("inventory:claim_save"), {
            "serial": "JU-004", "claim_date": "2026-11-01", "problem": "Cracked", "outcome": "repair",
            "cost": "120"}, **FETCH).json()
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(WarrantyClaim.all_objects.get(company=self.company).cost, D("120"))
        reply = self.client.post(reverse("inventory:claim_save"), {
            "serial": "", "claim_date": "2026-11-01", "problem": "", "outcome": ""}, **FETCH).json()
        self.assertIn("problem", reply["errors"])
        page = self.client.get(reverse("inventory:warranty"))
        self.assertContains(page, "Cracked")
        pdf = self.client.get(reverse("inventory:serial_labels"), {"item": self.juice.pk})
        self.assertEqual(pdf["Content-Type"], "application/pdf")

    def test_serials_in_and_out_from_the_page(self):
        reply = self.client.post(reverse("inventory:serials_add"), {
            "item": self.juice.pk, "serials": "JU-010\nJU-011", "received_on": "2026-10-01"}, **FETCH).json()
        self.assertTrue(reply["ok"], reply)
        delivery = self.delivered("2")
        reply = self.client.post(reverse("inventory:serials_deliver"), {
            "delivery": delivery.pk, "serials": "JU-010 JU-011"}, **FETCH).json()
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(SerialNumber.all_objects.filter(delivery_id=delivery.pk).count(), 2)

    def test_recording_needs_its_permission(self):
        self.client.force_login(self.member("viewer@inv.test", "employee"))
        reply = self.client.post(reverse("inventory:claim_save"), {"serial": "JU-001"}, **FETCH)
        self.assertNotEqual(reply.status_code, 200)


class MadeSerialsTests(RunFixture, TestCase):
    def test_a_finished_production_makes_one_per_unit_and_reverse_takes_them_away(self):
        switch_on(self)
        Item.all_objects.filter(pk=self.juice.pk).update(serial_numbers=True, warranty_months=12)
        self.start()
        _order, output = self.finish(good=D("1980"), reason="")
        made = SerialNumber.all_objects.filter(source="production", source_id=output.pk)
        self.assertEqual(made.count(), 1980)
        self.assertEqual(made.order_by("serial").first().serial, f"{output.batch_no}-00001")
        self.assertContains(self.client.get(reverse("production:run", args=[self.order.pk])), "Print serial labels")
        self.reverse_it()
        self.assertFalse(made.exists())

    def reverse_it(self):
        from production import making

        making.reverse(actor=self.owner, company_id=self.company.pk, order_id=self.order.pk, reason="Test",
                       work_date=self.today)
