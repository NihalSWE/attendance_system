"""Phase 4 of the redesign (2026-10-27, docs/inventory/PRODUCTION_PLAN.md §15):
Materials and Products on real records - add, edit and delete saving with
fetch, the checklist and Next steps from the company's own data, and the
product hub with stock by store and batch and what sold in the last 30 days."""

import datetime

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from common.tenant import use_company
from inventory import hub, posting
from inventory.models import Item, ItemKind, ItemUom, MoveType, Uom
from inventory.testing import D
from production.tests_flow import ProductionFixture

FETCH = {"HTTP_X_REQUESTED_WITH": "fetch"}


class HubFixture(ProductionFixture):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)
        with use_company(self.company):
            self.carton = Uom.objects.get(code="CARTON")

    def post(self, name, data, *args):
        return self.client.post(reverse(f"inventory:{name}", args=args), data, **FETCH)

    def material(self, **extra):
        return {"kind": "raw", "name": "Flour", "code": "", "category": self.raw.pk,
                "base_uom": self.kg.pk, **extra}

    def product(self, **extra):
        return {"name": "Biscuit 100 g", "code": "", "category": self.juice.category_id,
                "base_uom": self.pcs.pk, "sale_price": "20", "sourcing": "make",
                "keep_by": "quantity", **extra}


class StartPageTests(HubFixture, TestCase):
    def steps(self):
        return {step["what"]: step for step in hub.checklist(self.company.pk, self.owner)}

    def test_the_checklist_follows_what_is_done(self):
        steps = self.steps()
        self.assertTrue(steps["Materials added"]["done"])
        self.assertTrue(steps["Stock in"]["done"])         # the fixture's first count
        self.assertFalse(steps["Recipes saved"]["done"])
        self.assertIn("Mango juice", steps["Recipes saved"]["detail"])
        self.assertEqual(steps["Recipes saved"]["url"], reverse("inventory:product", args=[self.juice.pk]))
        self.assertFalse(steps["First production finished"]["done"])
        self.recipe()
        self.assertTrue(self.steps()["Recipes saved"]["done"])
        page = self.client.get(reverse("inventory:start"))
        self.assertContains(page, "What to do")
        self.assertContains(page, "ui-next")

    def test_a_trading_company_sees_products_stock_in_and_sell(self):
        self.enable_module(True, production=False)
        steps = self.steps()
        self.assertNotIn("Materials added", steps)
        self.assertNotIn("Recipes saved", steps)
        self.assertNotIn("First production finished", steps)
        self.assertIn("First sale delivered", steps)
        page = self.client.get(reverse("inventory:start"))
        self.assertNotContains(page, f'href="{reverse("inventory:materials")}"')
        self.assertNotContains(page, f'href="{reverse("production:orders")}"')

    def test_the_menu_starts_with_the_work_in_order(self):
        page = self.client.get(reverse("inventory:start")).content.decode()
        places = [page.find(f'href="{reverse(name)}"') for name in
                  ("inventory:start", "inventory:materials", "inventory:products")]
        self.assertTrue(all(place > 0 for place in places))
        self.assertEqual(places, sorted(places))


class MaterialsTests(HubFixture, TestCase):
    def test_the_list_shows_materials_with_their_status(self):
        Item.all_objects.filter(pk=self.bottle.pk).update(min_stock=D("6000"))
        page = self.client.get(reverse("inventory:materials"))
        for name in ("Mango pulp", "PET bottle 250 ml", "Cap 28 mm"):
            self.assertContains(page, name)
        self.assertNotContains(page, "Mango juice")
        self.assertContains(page, "130 KG")            # pulp in two batches
        low = self.client.get(reverse("inventory:materials"), {"status": "wait"})
        self.assertContains(low, "PET bottle 250 ml")
        self.assertNotContains(low, "Mango pulp")
        # The table's own draw (search, pages) answers with rows and counts.
        draw = self.client.get(reverse("inventory:materials"),
                               {"table": "1", "draw": "1", "start": "0", "length": "10",
                                "search[value]": "cap"}).json()
        self.assertEqual((draw["recordsTotal"], draw["recordsFiltered"]), (4, 1))

    def test_without_production_there_are_no_materials(self):
        self.enable_module(True, production=False)
        page = self.client.get(reverse("inventory:materials"))
        self.assertContains(page, "Production is not enabled", status_code=404)

    def test_adding_a_material_saves_in_place_with_its_bag(self):
        body = self.post("materials", self.material(pack_uom=self.bag.pk, pack_factor="50")).json()
        self.assertTrue(body["ok"], body)
        flour = Item.all_objects.get(company=self.company, name="Flour")
        self.assertEqual(flour.code, "RM-0003")       # RM-0001 and RM-0002 are taken
        self.assertEqual(body["redirect"], reverse("inventory:material", args=[flour.pk]))
        pack = ItemUom.all_objects.get(item=flour)
        self.assertEqual((pack.uom_id, pack.factor), (self.bag.pk, D("50")))

    def test_the_add_form_asks_each_box_once_in_plain_words(self):
        page = self.client.get(reverse("inventory:materials")).content.decode()
        for label in ("Type", "Unit", "Bought in (optional)", "Stock to keep", "Quality check before use"):
            self.assertIn(f">{label}", page)
        # Stock to keep is always there for a material (no days-of-sales choice).
        self.assertNotIn('name="min_stock" data-show-when', page)
        self.assertNotIn('data-show-when="keep_by', page)

    def test_a_refused_material_names_each_box(self):
        reply = self.post("materials", self.material(name="", pack_uom=self.bag.pk))
        self.assertEqual(reply.status_code, 400)
        body = reply.json()
        self.assertEqual(body["errors"]["name"], ["Fill this in."])
        self.assertIn("pack_factor", body["errors"])
        body = self.post("materials", self.material(pack_uom=self.kg.pk, pack_factor="5")).json()
        self.assertIn("pack_uom", body["errors"])
        body = self.post("materials", self.material(kind="finished")).json()
        self.assertIn("kind", body["errors"])
        self.assertFalse(Item.all_objects.filter(company=self.company, name="Flour").exists())

    def test_someone_without_set_up_cannot_add(self):
        self.client.force_login(self.member("keeper@inv.test", "employee"))
        self.assertEqual(self.post("materials", self.material()).status_code, 403)

    def test_the_material_page_shows_stock_batches_and_use(self):
        self.recipe()
        page = self.client.get(reverse("inventory:material", args=[self.pulp.pk]))
        self.assertContains(page, "P1")
        self.assertContains(page, "P2")
        self.assertContains(page, "Mango juice 250 ml")   # used in
        self.assertContains(page, "Deactivate")           # it has moved: no Delete
        # A product's address on the material page goes to its hub.
        self.assertRedirects(self.client.get(reverse("inventory:material", args=[self.juice.pk])),
                             reverse("inventory:product", args=[self.juice.pk]))


class ProductsTests(HubFixture, TestCase):
    def test_adding_a_product_with_a_carton_at_its_own_price(self):
        body = self.post("products", self.product(pack_uom=self.carton.pk, pack_factor="24",
                                                  pack_price="450")).json()
        self.assertTrue(body["ok"], body)
        biscuit = Item.all_objects.get(company=self.company, name="Biscuit 100 g")
        self.assertEqual((biscuit.code, biscuit.kind, biscuit.sourcing), ("FG-0002", ItemKind.FINISHED, "make"))
        pack = ItemUom.all_objects.get(item=biscuit)
        self.assertEqual((pack.factor, pack.sale_price), (D("24"), D("450")))
        page = self.client.get(reverse("inventory:product", args=[biscuit.pk]))
        self.assertContains(page, "450")
        self.assertContains(page, "No recipe yet")

    def test_a_product_needs_its_price_and_a_price_needs_its_unit(self):
        body = self.post("products", self.product(sale_price="", pack_price="450")).json()
        self.assertIn("sale_price", body["errors"])
        self.assertIn("pack_uom", body["errors"])

    def test_the_list_shows_how_each_is_got_and_its_margin(self):
        Item.all_objects.filter(pk=self.juice.pk).update(sale_price=D("25"))
        posting.post(company_id=self.company.pk, actor=self.owner, document_type="opening", document_id=2,
                     move_type=MoveType.OPENING, work_date=datetime.date(2026, 9, 2), lines=[
                         posting.Line(item=self.juice, warehouse=self.fgw, batch=self.batch("J1"),
                                      qty=D("100"), unit_cost=D("15"))])
        page = self.client.get(reverse("inventory:products"))
        self.assertContains(page, "Mango juice 250 ml")
        self.assertContains(page, "Make")
        self.assertContains(page, "10.00 (40.0%)")
        self.assertNotContains(page, "Mango pulp")

    def test_the_hub_shows_stock_by_batch_recipe_and_sales(self):
        self.recipe()
        today = timezone.localdate()
        posting.post(company_id=self.company.pk, actor=self.owner, document_type="opening", document_id=2,
                     move_type=MoveType.OPENING, work_date=today - datetime.timedelta(days=20), lines=[
                         posting.Line(item=self.juice, warehouse=self.fgw, batch=self.batch("J1"),
                                      qty=D("900"), unit_cost=D("15"))])
        posting.post(company_id=self.company.pk, actor=self.owner, document_type="delivery", document_id=1,
                     move_type=MoveType.DELIVERY, work_date=today - datetime.timedelta(days=2), lines=[
                         posting.Line(item=self.juice, warehouse=self.fgw, batch=self.batch("J1"),
                                      qty=D("-300"))])
        page = self.client.get(reverse("inventory:product", args=[self.juice.pk]))
        self.assertContains(page, "J1")
        self.assertContains(page, "Finished goods")              # the store
        self.assertContains(page, "Recipe: one batch makes 1,000 PCS")
        self.assertContains(page, "Sold, last 30 days")
        self.assertEqual(hub.sold(self.company.pk, self.juice)[0], D("300.00"))
        # Pulp 130 kg / 45 kg a batch: 2 batches; it limits the recipe.
        self.assertContains(page, "enough for</span> <strong>2 batches")

    def batch(self, number):
        from inventory.models import Batch

        found = Batch.all_objects.filter(company=self.company, item=self.juice, batch_no=number).first()
        return found or Batch.all_objects.create(company=self.company, item=self.juice, batch_no=number,
                                                 source="opening", expiry_date=datetime.date(2027, 6, 30))


class EditAndDeleteTests(HubFixture, TestCase):
    def test_editing_saves_in_place(self):
        body = self.post("item_save", {"kind": "raw", "name": "Mango pulp (Alphonso)", "code": "RM-0002",
                                       "category": self.raw.pk, "base_uom": self.kg.pk,
                                       "track_batch": "on", "track_expiry": "on",
                                       "shelf_life_days": "540"}, self.pulp.pk).json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(self.reload(self.pulp).name, "Mango pulp (Alphonso)")
        # Stock has moved: the unit stays.
        body = self.post("item_save", {"kind": "raw", "name": "Mango pulp", "code": "RM-0002",
                                       "category": self.raw.pk, "base_uom": self.pcs.pk,
                                       "track_batch": "on", "track_expiry": "on",
                                       "shelf_life_days": "540"}, self.pulp.pk).json()
        self.assertIn("base_uom", body["errors"])

    def test_a_new_item_is_deleted_and_a_used_one_deactivated(self):
        self.post("materials", self.material())
        flour = Item.all_objects.get(company=self.company, name="Flour")
        body = self.post("items_delete", {"done": reverse("inventory:materials")}, flour.pk).json()
        self.assertEqual((body["ok"], body["redirect"]), (True, reverse("inventory:materials")))
        self.assertFalse(Item.all_objects.filter(pk=flour.pk).exists())
        body = self.client.post(reverse("inventory:items_delete", args=[self.pulp.pk]), {}, **FETCH).json()
        self.assertFalse(body["ok"])
        self.assertIn("Deactivate it instead", body["message"])
        here = reverse("inventory:material", args=[self.pulp.pk])
        body = self.post("items_status", {"status": "inactive", "next": here}, self.pulp.pk).json()
        self.assertEqual((body["ok"], body["redirect"]), (True, here))
        self.assertEqual(self.reload(self.pulp).status, "inactive")

    def test_a_bigger_unit_is_added_and_removed_in_place(self):
        body = self.post("pack_unit_add", {"uom": self.carton.pk, "factor": "24", "sale_price": "450"},
                         self.juice.pk).json()
        self.assertEqual(body["redirect"], reverse("inventory:product", args=[self.juice.pk]))
        pack = ItemUom.all_objects.get(item=self.juice)
        body = self.post("pack_unit_add", {"uom": self.carton.pk, "factor": "12"}, self.juice.pk).json()
        self.assertFalse(body["ok"])
        body = self.post("pack_unit_remove", {}, self.juice.pk, pack.pk).json()
        self.assertTrue(body["ok"])
        self.assertFalse(ItemUom.all_objects.filter(pk=pack.pk).exists())
