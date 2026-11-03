"""How a made product is sold (2026-11-03, production/bottling.py): as it is,
and in bottle sizes - each its own product whose recipe takes the bulk
product and an empty bottle, cap and label."""

import datetime

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from common.tenant import use_company
from inventory import posting, pricing
from inventory.models import Batch, Item, ItemCategory, ItemKind, ItemUom, MoveType, Uom
from inventory.testing import D
from production import bottling, planning, recipes
from production.models import CostLine
from production.tests_flow import ProductionFixture

FETCH = {"HTTP_X_REQUESTED_WITH": "fetch"}


class BottleFixture(ProductionFixture):
    """Orange juice made here by the ML, 10,000 ML in stock at 0.27 a ML."""

    def setUp(self):
        super().setUp()
        with use_company(self.company):
            self.ml = Uom.objects.get(code="ML")
            self.carton = Uom.objects.get(code="CARTON")
            juice = ItemCategory.objects.get(code="JCE")
        self.bulk = self.item("FG-0101", "Orange juice", ItemKind.FINISHED, juice, self.ml, sourcing="make",
                              track_batch=True, track_expiry=True, shelf_life_days=15, qc_required=True)
        recipes.set_batch(**self.kw, item=self.bulk, batch_size=D("10000"))
        recipes.add_material(**self.kw, item=self.bulk, material=self.pulp, qty=D("45"))
        lot = Batch.all_objects.create(company=self.company, item=self.bulk, batch_no="OJ1", source="opening",
                                       expiry_date=datetime.date(2027, 1, 31))
        posting.post(company_id=self.company.pk, actor=self.owner, document_type="opening", document_id=2,
                     move_type=MoveType.OPENING, work_date=datetime.date(2026, 9, 1), lines=[
                         posting.Line(item=self.bulk, warehouse=self.rms, batch=lot, qty=D("10000"),
                                      unit_cost=D("0.27"))])

    def add(self, **values):
        values = {"size": D("250"), "unit": self.pcs, "containers": [self.bottle, self.cap], **values}
        return bottling.add_size(**self.kw, product=self.bulk, **values)


class AddSizeTests(BottleFixture, TestCase):
    def test_a_bottle_is_a_product_made_from_the_juice_and_its_bottle(self):
        bottle = self.add(bottling_cost=D("2"), sale_price=D("95"), carton=self.carton, carton_factor=D("24"))
        self.assertEqual((bottle.name, bottle.base_uom.code, bottle.sourcing, bottle.kind),
                         ("Orange juice 250 ml", "PCS", "make", ItemKind.FINISHED))
        # Batches, expiry and QC follow the juice.
        self.assertEqual((bottle.track_batch, bottle.track_expiry, bottle.shelf_life_days, bottle.qc_required),
                         (True, True, 15, True))
        found = recipes.page(self.company.pk, bottle)
        self.assertEqual(found["bom"].batch_size, D("1"))
        self.assertEqual(sorted((line.item.code, line.qty) for line in found["lines"]),
                         [("FG-0101", D("250")), ("PM-0001", D("1")), ("PM-0002", D("1"))])
        self.assertEqual(list(CostLine.all_objects.filter(item=bottle).values_list("label", "amount")),
                         [("Bottling", D("2.00"))])
        self.assertEqual(ItemUom.all_objects.get(item=bottle).factor, D("24"))
        # One bottle: 250 ML x 0.27 + bottle 3.20 + cap 0.90 + bottling 2.00.
        self.assertEqual(pricing.cost_per_unit(self.company.pk, bottle), D("73.60"))
        self.assertEqual(Item.all_objects.get(pk=bottle.pk).sale_price, D("95.00"))

    def test_one_litre_is_named_in_litres(self):
        self.assertEqual(self.add(size=D("1000"), containers=[]).name, "Orange juice 1 L")

    def test_a_price_below_cost_adds_nothing(self):
        with self.assertRaises(ValidationError) as caught:
            self.add(sale_price=D("50"))
        self.assertIn("Below cost: one filled bottle costs 71.60", caught.exception.message_dict["sale_price"][0])
        self.assertFalse(Item.all_objects.filter(name="Orange juice 250 ml").exists())

    def test_refused_twice_or_in_its_own_unit(self):
        self.add()
        with self.assertRaisesMessage(ValidationError, "exists already"):
            self.add()
        with self.assertRaisesMessage(ValidationError, "own unit"):
            self.add(unit=self.ml, name="Other")
        with self.assertRaisesMessage(ValidationError, "whole ML"):
            self.add(size=D("250.5"), name="Other")

    def test_ways_list_the_juice_then_each_bottle(self):
        self.bulk.sale_price = D("0.40")
        Item.all_objects.filter(pk=self.bulk.pk).update(sale_price=D("0.40"))
        bottle = self.add(sale_price=D("95"))
        found = bottling.ways(self.company.pk, self.owner, Item.all_objects.get(pk=self.bulk.pk))
        self.assertEqual([(way.item.pk, way.uses, way.on_hand) for way in found],
                         [(self.bulk.pk, None, D("10000")), (bottle.pk, D("250"), D("0"))])
        self.assertEqual((found[0].cost, found[0].margin), (D("0.27"), D("0.13")))
        self.assertEqual((found[1].cost, found[1].margin, found[1].price_per_bulk_unit),
                         (D("71.60"), D("23.40"), D("0.38")))

    def test_filling_bottles_takes_the_juice_from_stock(self):
        bottle = self.add()
        store = self.fgw
        found = planning.plan(self.company.pk, bottle, D("40"), store)
        juice = next(need for need in found.needs if need.item == self.bulk)
        self.assertEqual((juice.need, juice.short), (D("10000"), D("0")))
        self.assertFalse(planning.plan(self.company.pk, bottle, D("40"), store).short)
        self.assertTrue(planning.plan(self.company.pk, bottle, D("41"), store).short)


class PageTests(BottleFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def test_the_card_and_adding_a_size(self):
        page = self.client.get(reverse("inventory:product", args=[self.bulk.pk]))
        self.assertContains(page, "How you sell it")
        self.assertContains(page, "Add a bottle size")
        url = reverse("inventory:bottle_size_add", args=[self.bulk.pk])
        body = self.client.post(url, {"bottle-size": "", "bottle-unit": self.pcs.pk}, **FETCH).json()
        self.assertIn("bottle-size", body["errors"])
        body = self.client.post(url, {"bottle-size": "500", "bottle-unit": self.pcs.pk,
                                      "bottle-containers": [self.bottle.pk, self.cap.pk], "bottle-sale_price": "200",
                                      "bottle-carton_factor": "12"}, **FETCH).json()
        self.assertTrue(body["ok"], body)
        bottle = Item.all_objects.get(name="Orange juice 500 ml")
        self.assertEqual((bottle.sale_price, ItemUom.all_objects.get(item=bottle).factor), (D("200"), D("12")))
        page = self.client.get(reverse("inventory:product", args=[self.bulk.pk]))
        self.assertContains(page, "Orange juice 500 ml")
        self.assertContains(page, reverse("production:plan", args=[bottle.pk]))
        self.assertContains(page, "Fill bottles")
        sale = self.client.get(reverse("inventory:sale_new") + f"?item={bottle.pk}")
        self.assertContains(sale, f'value="{bottle.pk}" ')

    def test_the_dialog_knows_the_costs_to_show_one_bottle(self):
        page = self.client.get(reverse("inventory:product", args=[self.bulk.pk]))
        self.assertContains(page, 'data-bulk-cost="0.27')
        self.assertContains(page, f'&quot;{self.bottle.pk}&quot;: &quot;3.2')
        self.assertContains(page, "Cost of one filled bottle")
        self.assertContains(page, "Sale price of one filled bottle")
        # A material's sale price is only for selling the material itself.
        material = self.client.get(reverse("inventory:material", args=[self.bottle.pk]))
        self.assertContains(material, "Only if you sell the PET bottle 250 ml itself")
