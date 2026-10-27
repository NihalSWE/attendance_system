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
