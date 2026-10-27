"""The 2026 layout pieces (plan §15 phase 1): the preview page and the
save-without-reloading contract every new form follows (base_template/fetch.py)."""

from django.test import TestCase
from django.urls import reverse

from inventory.testing import InventoryFixture

FETCH = {"HTTP_X_REQUESTED_WITH": "fetch"}


class UiKitTests(InventoryFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)
        self.url = reverse("ui_preview")

    def test_the_preview_shows_every_piece(self):
        page = self.client.get(self.url)
        for piece in ("ui-tiles", "ui-card__head", "ui-btn--main", "ui-btn--danger", "ui-chip--bad",
                      "ui-switch", "ui-checklist", "ui-next", "data-local-table", "data-fetch",
                      "data-form-message", 'class="ui-icon'):
            self.assertContains(page, piece, msg_prefix=piece)
        # The modal saves without reloading too, its message above its buttons.
        self.assertContains(page, 'id="demo-dialog"')

    def test_saved_answers_ok_with_the_message(self):
        reply = self.client.post(self.url, {"name": "Salt", "unit": "kg"}, **FETCH)
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(reply.json()["ok"], True)
        self.assertIn("Salt added", reply.json()["message"])

    def test_refused_names_each_box_and_keeps_the_rest(self):
        reply = self.client.post(self.url, {"name": "", "unit": "", "has_expiry": "on"}, **FETCH)
        self.assertEqual(reply.status_code, 400)
        body = reply.json()
        self.assertEqual(body["ok"], False)
        self.assertEqual(set(body["errors"]), {"name", "unit", "shelf_life"})
        self.assertEqual(body["message"], "Not saved: fix the boxes marked in red.")
        self.assertEqual(body["errors"]["name"], ["Fill this in."])

    def test_a_reason_for_the_whole_form_is_the_message(self):
        body = self.client.post(self.url, {"name": "Flour", "unit": "kg"}, **FETCH).json()
        self.assertEqual(body["message"], "Flour is added already (sample check).")
        self.assertEqual(body["errors"], {})

    def test_only_company_administrators_open_it(self):
        user = self.member("keeper@inv.test", "employee")
        self.client.force_login(user)
        self.assertNotEqual(self.client.get(self.url).status_code, 200)
