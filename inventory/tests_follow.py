"""Lists that follow another field (2026-10-26): a Unit list carries each
item's own units, a Batch list its item, a Category list its item type, so the
browser (base_template/js/forms.js) offers only what saving will accept."""

from django.test import TestCase
from django.urls import reverse

from inventory import forms
from inventory.store_forms import IssueForm
from inventory.models import Item, ItemKind, ItemUom, OpeningStock
from inventory.testing import InventoryFixture


class FollowTests(InventoryFixture, TestCase):
    def test_an_items_units_are_on_its_option(self):
        ItemUom.all_objects.create(company=self.company, item=self.sugar, uom=self.bag, factor=50)
        html = str(forms.OpeningLineForm(company_id=self.company.pk)["item"])
        self.assertIn(f'value="{self.sugar.pk}" data-units="{self.kg.pk},{self.bag.pk}" '
                      f'data-base-unit="{self.kg.pk}"', html)
        self.assertIn(f'data-units="{self.pcs.pk}"', html)
        unit = str(forms.OpeningLineForm(company_id=self.company.pk)["uom"])
        self.assertIn('data-units-of="item"', unit)

    def test_batch_boxes_follow_the_item(self):
        form = forms.OpeningLineForm(company_id=self.company.pk)
        html = str(form["item"])
        self.assertIn(f'data-base-unit="{self.kg.pk}" data-batch="1" data-expiry="1"', html)
        self.assertIn(f'data-base-unit="{self.pcs.pk}" data-batch="" data-expiry=""', html)
        self.assertIn('data-item-needs="batch"', str(form["batch_no"]))
        self.assertIn('data-item-needs="expiry"', str(form["expiry_date"]))
        # Ticking "Has an expiry date" ticks "Keeps batches".
        self.assertIn('data-ticks="track_batch"', str(forms.ItemForm(company_id=self.company.pk)["track_expiry"]))

    def test_the_opening_manufacture_date_stops_at_the_opening_day(self):
        self.client.force_login(self.owner)
        self.client.post(reverse("inventory:opening"), {"work_date": "2026-09-30"})
        document = OpeningStock.all_objects.get(company=self.company)
        page = self.client.get(reverse("inventory:opening_detail", args=[document.pk]))
        self.assertContains(page, 'max="2026-09-30"')

    def test_categories_follow_the_item_type(self):
        form = forms.ItemForm(company_id=self.company.pk)
        html = str(form["category"])
        self.assertIn('data-follows="kind"', html)
        self.assertIn(f'value="{self.raw.pk}" data-kind="{ItemKind.RAW}"', html)
        self.assertIn(f'value="{self.spares.pk}" data-kind="{ItemKind.SPARE}"', html)


class ItemTypePageTests(InventoryFixture, TestCase):
    """One page per item type (2026-10-26)."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def test_the_raw_materials_page_lists_and_adds_only_raw_materials(self):
        url = reverse("inventory:raw_materials")
        self.assertEqual(url, "/inventory/raw-materials/")
        page = self.client.get(url)
        self.assertContains(page, "RM-0001")
        self.assertNotContains(page, "SP-0001")
        form = page.context["new_form"]
        self.assertEqual(form.fields["kind"].widget.input_type, "hidden")
        self.assertEqual(list(form.fields["category"].queryset), [self.raw])
        self.assertIn("hs_code", form.more_fields)
        self.assertContains(page, "More details (optional)")
        # Whatever is posted for the type, a raw material is made.
        response = self.client.post(url, {"code": "RM-0002", "name": "Salt", "kind": "finished",
                                          "category": self.raw.pk, "base_uom": self.kg.pk})
        self.assertRedirects(response, url, fetch_redirect_response=False)
        self.assertEqual(Item.all_objects.get(code="RM-0002").kind, ItemKind.RAW)

    def test_every_modal_form_without_more_details_still_shows_its_fields(self):
        page = self.client.get(reverse("inventory:stores"))
        self.assertContains(page, 'name="code"')
        self.assertContains(page, 'name="branch"')


class EitherTests(InventoryFixture, TestCase):
    """"This one OR that one" (2026-10-26): a choice first, one side's fields."""

    def test_the_hidden_side_is_ignored(self):
        form = IssueForm(company_id=self.company.pk)
        self.assertEqual(list(form.fields)[0], "source")
        self.assertIn('data-show-when="source:direct"', str(form["warehouse"]))
        posted = IssueForm({"source": "direct", "requisition": "999", "warehouse": self.rms.pk,
                            "issue_date": "2026-10-01"}, company_id=self.company.pk)
        with self.tenant():
            posted.is_valid()
        self.assertIsNone(posted.cleaned_data.get("requisition"))
        self.assertNotIn("source", posted.cleaned_data)
        # The stale hidden requisition is no error; what is missing is said at the top.
        self.assertNotIn("requisition", posted.errors)
        self.assertIn("Choose the requisition, or the store and the department.", posted.non_field_errors())
