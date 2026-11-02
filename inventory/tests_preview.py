"""The redesigned screens on sample data (plan §15 phase 2): every screen
opens, only administrators see them, and every form answers in place the way
the real one will (base_template/fetch.py) without saving anything."""

from django.test import TestCase
from django.urls import reverse

from inventory.models import Item
from inventory.testing import InventoryFixture

FETCH = {"HTTP_X_REQUESTED_WITH": "fetch"}


class PreviewScreenTests(InventoryFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def save(self, screen, data):
        return self.client.post(reverse("inventory:preview_save", args=[screen]), data, **FETCH)

    def test_every_screen_opens_with_the_new_pieces(self):
        pages = [reverse("inventory:preview_home"), reverse("inventory:preview_materials"),
                 reverse("inventory:preview_material", args=["RM-0001"]),
                 reverse("inventory:preview_products"),
                 reverse("inventory:preview_product", args=["FG-0001"]),
                 reverse("inventory:preview_make"), reverse("inventory:preview_plan")]
        pages += [reverse("inventory:preview_production", args=[n]) for n in ("PRD-0001", "PRD-0002", "PRD-0003")]
        for url in pages:
            page = self.client.get(url)
            self.assertEqual(page.status_code, 200, url)
            for piece in ("ui-flow", "ui-head__title", "ui-next"):
                self.assertContains(page, piece, msg_prefix=url)

    def test_the_product_hub_shows_cost_suggestion_and_recipe(self):
        page = self.client.get(reverse("inventory:preview_product", args=["FG-0001"]))
        self.assertContains(page, "Suggested: make 3 batches")
        self.assertContains(page, "Wrapper 100 g")
        self.assertContains(page, "Cost of one batch")
        self.assertContains(page, 'data-unit-from="material new_unit"')

    def test_a_material_is_checked_and_nothing_is_saved(self):
        reply = self.save("material", {"name": "", "unit": "", "has_expiry": "on"})
        self.assertEqual(reply.status_code, 400)
        self.assertEqual(set(reply.json()["errors"]), {"name", "unit", "shelf_life"})
        self.assertIn("name", self.save("material", {"name": "Flour", "unit": "kg"}).json()["errors"])
        before = Item.all_objects.count()
        reply = self.save("material", {"name": "Salt", "unit": "kg"})
        self.assertEqual(reply.json()["ok"], True)
        self.assertEqual(Item.all_objects.count(), before)

    def test_a_short_production_cannot_start(self):
        body = self.save("start", {"number": "PRD-0003"}).json()
        self.assertEqual(body["ok"], False)
        self.assertIn("Wrapper 100 g is short by 412", body["message"])

    def test_finishing_checks_the_split_and_the_yield(self):
        body = self.save("finish", {"good": "30", "into_MAIN": "20", "into_DEP": "5"}).json()
        self.assertIn("into_MAIN", body["errors"])
        body = self.save("finish", {"good": "20", "into_MAIN": "20", "into_DEP": "0"}).json()
        self.assertIn("reason", body["errors"])
        self.assertEqual(self.save("finish", {"good": "30", "into_MAIN": "30", "into_DEP": "0"}).json()["ok"], True)

    def test_planning_goes_on_to_the_production(self):
        body = self.save("plan", {"batches": "3", "date": "2026-10-28"}).json()
        self.assertEqual(body["redirect"], reverse("inventory:preview_production", args=["PRD-0003"]))
        self.assertIn("batches", self.save("plan", {"batches": "2.5", "date": "2026-10-28"}).json()["errors"])

    def test_only_administrators_see_the_preview(self):
        self.client.force_login(self.member("keeper@inv.test", "employee"))
        self.assertNotEqual(self.client.get(reverse("inventory:preview_home")).status_code, 200)


class PreviewStockScreenTests(InventoryFixture, TestCase):
    """2.5 and 2.6: stock in, stock, moves, sales, warranty (2026-10-27)."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def save(self, screen, data):
        return self.client.post(reverse("inventory:preview_save", args=[screen]), data, **FETCH).json()

    def test_every_new_screen_opens(self):
        for name in ("preview_stock_in", "preview_stock_in_new", "preview_stock", "preview_moves",
                     "preview_move_new", "preview_sales", "preview_sale_new", "preview_warranty"):
            page = self.client.get(reverse(f"inventory:{name}"))
            self.assertEqual(page.status_code, 200, name)
            self.assertContains(page, "ui-next", msg_prefix=name)
            self.assertNotContains(page, "ui-flow__item--later", msg_prefix=name)

    def test_stock_in_asks_batch_only_for_items_that_keep_batches(self):
        body = self.save("stock_in", {"source": "purchase", "store": "RSA", "date": "2026-10-27",
                                      "supplier": "SUP-001", "item_1": "RM-0001", "qty_1": "50",
                                      "cost_1": "52", "item_2": "PK-0001", "qty_2": "1000", "cost_2": "0.9"})
        self.assertEqual(set(body["errors"]), {"batch_1", "expiry_1"})
        body = self.save("stock_in", {"source": "purchase", "store": "RSA", "date": "2026-10-27"})
        self.assertIn("supplier", body["errors"])
        self.assertIn("Add at least one line", " ".join(body["form_errors"]))
        body = self.save("stock_in", {"source": "count", "store": "PKS", "date": "2026-10-27",
                                      "item_1": "PK-0001", "qty_1": "1000", "cost_1": "0.9"})
        self.assertEqual(body["ok"], True)

    def test_a_move_needs_two_stores_and_enough_stock(self):
        body = self.save("move", {"from_store": "RSA", "to_store": "RSA", "item": "RM-0001", "qty": "10"})
        self.assertIn("to_store", body["errors"])
        body = self.save("move", {"from_store": "RSA", "to_store": "RSB", "item": "RM-0001", "qty": "61"})
        self.assertEqual(body["errors"]["qty"], ["Only 60 there."])
        self.assertEqual(self.save("move", {"from_store": "RSA", "to_store": "RSB", "item": "RM-0001",
                                            "qty": "10"})["ok"], True)

    def test_a_sale_cannot_take_more_than_the_store_has(self):
        body = self.save("sale", {"customer": "CUS-001", "store": "DEP", "unit_1": "carton", "qty_1": "31"})
        self.assertIn("Only 30 cartons", body["message"])
        body = self.save("sale", {"customer": "CUS-001", "store": "DEP", "unit_1": "carton", "qty_1": "29",
                                  "unit_2": "packet", "qty_2": "24"})
        self.assertEqual(body["ok"], True)

    def test_a_claim_checks_the_serial_and_the_cover(self):
        self.assertIn("serial", self.save("claim", {"serial": "NOPE", "problem": "x", "outcome": "replace"})["errors"])
        body = self.save("claim", {"serial": "FAN56-000017", "problem": "Wobbles", "outcome": "replace"})
        self.assertIn("reason", body["errors"])
        self.assertEqual(self.save("claim", {"serial": "FAN56-000418", "problem": "Noisy",
                                             "outcome": "repair"})["ok"], True)
