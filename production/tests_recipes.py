"""A finished good's materials on its own page (production.recipes, 2026-10-26)."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from common.tenant import use_company
from inventory.models import Item
from production import orders, recipes
from production.models import Bom
from production.tests_flow import DAY, ProductionFixture


class RecipeTests(ProductionFixture, TestCase):
    def test_in_use_as_soon_as_it_has_a_material_with_no_unit_to_choose(self):
        recipes.set_batch(**self.kw, item=self.juice, batch_size=Decimal("1000"))
        bom = recipes.current(self.company.pk, self.juice)
        self.assertEqual((bom.status, bom.batch_uom_id), ("draft", self.pcs.pk))
        recipes.add_material(**self.kw, item=self.juice, material=self.pulp, qty=Decimal("120"),
                             scrap_pct=Decimal("2"))
        recipes.add_material(**self.kw, item=self.juice, material=self.bottle, qty=Decimal("1000"))
        bom = recipes.current(self.company.pk, self.juice)
        self.assertEqual(bom.status, "active")
        with use_company(self.company):
            lines = {line.item_id: line for line in bom.lines.all()}
        self.assertEqual(lines[self.pulp.pk].uom_id, self.pulp.base_uom_id)
        self.assertFalse(lines[self.pulp.pk].is_packaging)
        self.assertTrue(lines[self.bottle.pk].is_packaging)

    def test_only_raw_materials_and_packaging(self):
        recipes.set_batch(**self.kw, item=self.juice, batch_size=Decimal("1000"))
        with self.assertRaises(ValidationError):
            recipes.add_material(**self.kw, item=self.juice, material=self.juice, qty=Decimal("1"))

    def test_changes_in_place_until_production_uses_it_then_a_new_version(self):
        bom = self.recipe()      # the fixture's recipe in use; no order yet
        recipes.remove_material(**self.kw, item=self.juice, material_id=self.cap.pk)
        self.assertEqual(recipes.current(self.company.pk, self.juice).pk, bom.pk)
        orders.create_order(**self.kw, values={   # a production order uses it now
            "item": self.juice, "bom": None, "planned_qty": Decimal("1000"), "uom": self.pcs,
            "planned_date": DAY, "material_warehouse": self.rms, "output_warehouse": self.fgw,
            "line_name": "", "shift_name": "", "note": ""})
        recipes.add_material(**self.kw, item=self.juice, material=self.cap, qty=Decimal("1000"))
        newer = recipes.current(self.company.pk, self.juice)
        self.assertNotEqual(newer.pk, bom.pk)
        self.assertEqual((newer.status, newer.version), ("active", bom.version + 1))
        with use_company(self.company):
            self.assertEqual(Bom.objects.get(pk=bom.pk).status, "archived")
            self.assertTrue(newer.lines.filter(item=self.cap).exists())
            self.assertFalse(Bom.objects.get(pk=bom.pk).lines.filter(item=self.cap).exists())

    def test_the_last_material_of_a_recipe_in_use_stays(self):
        recipes.set_batch(**self.kw, item=self.juice, batch_size=Decimal("1000"))
        recipes.add_material(**self.kw, item=self.juice, material=self.pulp, qty=Decimal("120"))
        with self.assertRaises(ValidationError):
            recipes.remove_material(**self.kw, item=self.juice, material_id=self.pulp.pk)

    def test_the_card_on_the_finished_goods_page(self):
        self.client.force_login(self.owner)
        url = reverse("inventory:item_detail", args=[self.juice.pk])
        self.assertContains(self.client.get(url), "First say how much one batch makes")
        self.client.post(reverse("production:recipe_batch", args=[self.juice.pk]), {"batch_size": "1000"})
        response = self.client.post(reverse("production:recipe_add", args=[self.juice.pk]),
                                    {"material": self.pulp.pk, "qty": "120", "scrap_pct": ""})
        self.assertRedirects(response, url + "#materials", fetch_redirect_response=False)
        page = self.client.get(url)
        self.assertContains(page, "per batch. The recipe is in use.")
        self.assertContains(page, "120 KG")
        self.assertContains(page, f'data-unit="{self.kg.code}"')
        # Pulp is on the recipe: the Add material list no longer offers it.
        self.assertNotIn(self.pulp, list(page.context["material_form"].fields["material"].queryset))
        self.assertIn(self.sugar, list(page.context["material_form"].fields["material"].queryset))
        # A raw material has no Materials card.
        self.assertNotContains(self.client.get(reverse("inventory:item_detail", args=[self.pulp.pk])),
                               "Materials (recipe)")

    def test_a_new_raw_material_made_from_add_material(self):
        self.client.force_login(self.owner)
        self.client.post(reverse("production:recipe_batch", args=[self.juice.pk]), {"batch_size": "1000"})
        response = self.client.post(reverse("production:recipe_add", args=[self.juice.pk]), {
            "source": "new", "new_kind": "raw", "new_code": "rm-0009", "new_name": "Citric acid",
            "new_category": self.pulp.category_id, "new_uom": self.kg.pk, "qty": "2"})
        self.assertRedirects(response, reverse("inventory:item_detail", args=[self.juice.pk]) + "#materials",
                             fetch_redirect_response=False)
        with use_company(self.company):
            acid = Item.objects.get(code="RM-0009")
            self.assertEqual((acid.kind, acid.base_uom), ("raw", self.kg))
            self.assertTrue(recipes.current(self.company.pk, self.juice).lines.filter(item=acid).exists())

    def test_a_refused_new_material_opens_again_with_what_was_written(self):
        self.client.force_login(self.owner)
        self.client.post(reverse("production:recipe_batch", args=[self.juice.pk]), {"batch_size": "1000"})
        page = self.client.post(reverse("production:recipe_add", args=[self.juice.pk]), {
            "source": "new", "new_kind": "raw", "new_name": "Citric acid", "qty": "2"})
        self.assertContains(page, 'data-open-on-load="material-dialog"')
        self.assertContains(page, 'value="Citric acid"')
        self.assertContains(page, "Give this for the new material.")
        self.assertFalse(Item.all_objects.filter(name="Citric acid").exists())

    def test_no_version_on_the_screens(self):
        self.client.force_login(self.owner)
        self.recipe()
        page = self.client.get(reverse("inventory:item_detail", args=[self.juice.pk]))
        self.assertNotContains(page, "version")
