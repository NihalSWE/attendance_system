"""Phase 5 of the redesign (2026-10-27, docs/inventory/PRODUCTION_PLAN.md §15):
the recipe on the product's page, cost rates, the cost of one batch (people,
machines, fixed, % of materials), the expected cost per unit and per pack with
the margin, and semi-finished products in a recipe without loops."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from access_control.branch_access import grant_access
from common.tenant import use_company
from employees.models import Employee
from inventory import hub
from inventory.models import Item, ItemKind, ItemUom, Uom
from inventory.testing import D
from production import costing, recipes
from production.models import CostLine
from production.tests_flow import ProductionFixture

FETCH = {"HTTP_X_REQUESTED_WITH": "fetch"}
# The fixture's recipe, one batch = 1,000 bottles: pulp 45 kg × 210, sugar
# 22 kg × 125, bottles 1,015 (1.5 % wastage) × 3.20, caps 1,010 (1 %) × 0.90.
MATERIALS = D("9450") + D("2750") + D("3248") + D("909")


class CostFixture(ProductionFixture):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)
        self.recipe()
        Item.all_objects.filter(pk=self.juice.pk).update(sale_price=D("25"))
        self.juice.refresh_from_db()
        self.worker = costing.add_rate(**self.kw, values={"name": "Line worker", "kind": "labour", "rate": D("75")})
        self.oven = costing.add_rate(**self.kw, values={"name": "Pasteuriser", "kind": "machine", "rate": D("300")})

    def costs(self):
        for values in ({"how": "labour", "rate": self.worker, "people": D("4"), "hours": D("1.5")},
                       {"how": "machine", "rate": self.oven, "hours": D("2")},
                       {"how": "fixed", "label": "Gas", "amount": D("120")},
                       {"how": "percent", "label": "Rent share", "percent": D("5")}):
            costing.add_line(**self.kw, item=self.juice, values=values)


class CostSheetTests(CostFixture, TestCase):
    def test_one_batch_one_unit_and_one_carton(self):
        self.costs()
        sheet = costing.sheet(self.company.pk, self.juice)
        self.assertEqual(sheet.materials_total, MATERIALS)
        self.assertEqual([(row.name, row.amount) for row in sheet.costs], [
            ("Line worker", D("450.00")), ("Pasteuriser", D("600.00")), ("Gas", D("120.00")),
            ("Rent share", D("817.85"))])
        self.assertEqual(sheet.total, D("18344.85"))
        self.assertEqual(sheet.per_unit, (D("18344.85") / 1000).quantize(D("0.0001")))
        self.assertEqual(sheet.margin, (D("25") - sheet.per_unit).quantize(D("0.01")))
        with use_company(self.company):
            carton = ItemUom.objects.create(company=self.company, item=self.juice,
                                            uom=Uom.objects.get(code="CARTON"), factor=24)
        self.assertEqual(sheet.per_pack([carton]), [(carton, (sheet.per_unit * 24).quantize(D("0.01")))])

    def test_vat_is_worked_out_last_on_everything_above_it(self):
        """VAT as a cost of one batch (2026-11-03): its % is of the materials
        and every other cost, wherever it was added."""
        costing.add_line(**self.kw, item=self.juice, values={"how": "vat", "percent": D("15")})
        self.costs()
        sheet = costing.sheet(self.company.pk, self.juice)
        self.assertEqual([row.name for row in sheet.costs][-1], "VAT")
        self.assertEqual(sheet.costs[-1].amount, D("2751.73"))      # 15 % of 18,344.85
        self.assertIn("15 % of 18,344.85", sheet.costs[-1].how)
        self.assertEqual(sheet.total, D("21096.58"))
        with self.assertRaises(ValidationError) as caught:
            costing.add_line(**self.kw, item=self.juice, values={"how": "vat"})
        self.assertIn("percent", caught.exception.message_dict)

    def test_a_new_rate_counts_from_then_on(self):
        self.costs()
        costing.update_rate(**self.kw, pk=self.worker.pk, values={"rate": D("100")})
        sheet = costing.sheet(self.company.pk, self.juice)
        self.assertEqual(sheet.costs[0].amount, D("600.00"))
        # A rate a product uses stays the kind it is.
        with self.assertRaises(ValidationError):
            costing.update_rate(**self.kw, pk=self.worker.pk, values={"kind": "machine"})

    def test_each_way_needs_its_own_boxes_and_keeps_only_them(self):
        with self.assertRaises(ValidationError) as caught:
            costing.add_line(**self.kw, item=self.juice, values={"how": "labour", "rate": self.worker})
        self.assertEqual(set(caught.exception.message_dict), {"people", "hours"})
        with self.assertRaises(ValidationError) as caught:
            costing.add_line(**self.kw, item=self.juice,
                             values={"how": "labour", "rate": self.oven, "people": D("1"), "hours": D("1")})
        self.assertIn("rate", caught.exception.message_dict)
        line = costing.add_line(**self.kw, item=self.juice,
                                values={"how": "fixed", "amount": D("50"), "hours": D("3")})
        self.assertIsNone(CostLine.all_objects.get(pk=line.pk).hours)

    def test_a_bought_product_has_no_cost_of_making(self):
        Item.all_objects.filter(pk=self.juice.pk).update(sourcing="buy")
        self.juice.refresh_from_db()
        with self.assertRaises(ValidationError):
            costing.add_line(**self.kw, item=self.juice, values={"how": "fixed", "amount": D("50")})

    def test_a_material_without_a_cost_is_named(self):
        salt = self.item("RM-0010", "Salt", ItemKind.RAW, self.raw, self.kg)
        recipes.add_material(**self.kw, item=self.juice, material=salt, qty=D("1"))
        sheet = costing.sheet(self.company.pk, self.juice)
        self.assertEqual(sheet.missing, ["Salt"])
        self.assertIsNone(costing.expected_unit_cost(self.company.pk, self.juice))


class SemiFinishedTests(CostFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.concentrate = self.item("FG-0002", "Mango concentrate", ItemKind.FINISHED,
                                     self.juice.category, self.kg, sourcing="make")
        recipes.set_batch(**self.kw, item=self.concentrate, batch_size=D("10"))
        recipes.add_material(**self.kw, item=self.concentrate, material=self.pulp, qty=D("20"))

    def test_a_product_made_here_goes_into_another_and_costs_what_making_it_costs(self):
        recipes.add_material(**self.kw, item=self.juice, material=self.concentrate, qty=D("5"))
        # Concentrate: 20 kg pulp × 210 for 10 kg = 420 a kg; none in stock.
        self.assertEqual(costing.unit_cost(self.company.pk, self.concentrate), D("420"))
        sheet = costing.sheet(self.company.pk, self.juice)
        self.assertEqual(sheet.materials_total, MATERIALS + D("2100"))

    def test_a_loop_is_refused(self):
        recipes.add_material(**self.kw, item=self.juice, material=self.concentrate, qty=D("5"))
        with self.assertRaisesMessage(ValidationError, "that is a loop"):
            recipes.add_material(**self.kw, item=self.concentrate, material=self.juice, qty=D("1"))

    def test_a_bought_product_is_not_a_material(self):
        Item.all_objects.filter(pk=self.concentrate.pk).update(sourcing="buy")
        self.concentrate.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "bought ready"):
            recipes.add_material(**self.kw, item=self.juice, material=self.concentrate, qty=D("5"))

    def test_the_add_list_offers_products_made_here(self):
        page = self.client.get(reverse("inventory:product", args=[self.juice.pk]))
        self.assertContains(page, "FG-0002 · Mango concentrate (made here)")


class PagesTests(CostFixture, TestCase):
    def post(self, name, data, *args):
        return self.client.post(reverse(name, args=args), data, **FETCH)

    def test_the_product_page_shows_the_recipe_and_the_cost_of_one_batch(self):
        self.costs()
        page = self.client.get(reverse("inventory:product", args=[self.juice.pk]))
        self.assertContains(page, "Recipe for 1,000 PCS")
        self.assertContains(page, "Cost of 1,000 PCS (the recipe)")
        self.assertContains(page, "18,344.85")
        self.assertContains(page, "4 × 1.5 h × 75")
        self.assertContains(page, "expected: the recipe ÷ 1,000")
        # The products list uses the expected cost too.
        line = hub.lines(self.company.pk, self.owner, [ItemKind.FINISHED], only=self.juice.pk)[0]
        self.assertEqual(line.cost, (D("18344.85") / 1000).quantize(D("0.01")))

    def test_recipe_changes_save_in_place(self):
        body = self.post("production:recipe_add", {"material": self.sugar.pk, "qty": "1"}, self.juice.pk).json()
        self.assertIn("material", body["errors"])        # sugar is on it already: not offered
        body = self.post("production:recipe_remove", {}, self.juice.pk, self.cap.pk).json()
        self.assertEqual(body["redirect"], reverse("inventory:product", args=[self.juice.pk]) + "#recipe")
        body = self.post("production:recipe_add", {"material": self.cap.pk, "qty": "1000", "scrap_pct": "1"},
                         self.juice.pk).json()
        self.assertTrue(body["ok"], body)
        body = self.post("production:recipe_batch", {"batch_size": ""}, self.juice.pk)
        self.assertEqual(body.status_code, 400)

    def test_costs_and_rates_save_in_place(self):
        body = self.post("production:cost_line_add", {"how": "labour", "rate": self.worker.pk, "people": "2"},
                         self.juice.pk).json()
        self.assertEqual(set(body["errors"]), {"hours"})
        body = self.post("production:cost_line_add", {"how": "fixed", "amount": "120", "label": "Gas"},
                         self.juice.pk).json()
        self.assertEqual(body["redirect"], reverse("inventory:product", args=[self.juice.pk]) + "#costs")
        line = CostLine.all_objects.get(item=self.juice)
        self.assertTrue(self.post("production:cost_line_remove", {}, self.juice.pk, line.pk).json()["ok"])
        body = self.post("production:cost_rates", {"name": "line worker", "kind": "labour", "rate": "80"}).json()
        self.assertIn("name", body["errors"])
        body = self.post("production:cost_rates", {"name": "Supervisor", "kind": "labour", "rate": "150"}).json()
        self.assertTrue(body["ok"], body)
        body = self.post("production:cost_rate_save", {f"r{self.worker.pk}-name": "Line worker",
                                                       f"r{self.worker.pk}-kind": "labour",
                                                       f"r{self.worker.pk}-rate": "90"}, self.worker.pk).json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(self.reload(self.worker).rate, D("90"))
        self.assertTrue(self.post("production:cost_rate_status", {"status": "inactive"}, self.worker.pk).json()["ok"])
        page = self.client.get(reverse("production:cost_rates"))
        self.assertContains(page, "Supervisor")
        self.assertContains(page, "Switch on")

    def test_cost_rates_need_production(self):
        self.assertContains(self.client.get(reverse("inventory:start")), f'href="{reverse("production:cost_rates")}"')
        self.enable_module(True, production=False)
        self.assertEqual(self.client.get(reverse("production:cost_rates")).status_code, 404)

    def test_a_store_keeper_given_access_finds_the_new_pages(self):
        user = self.member("keeper@inv.test", "employee")
        with use_company(self.company):
            employee = Employee.objects.create(company=self.company, user=user, first_name="K")
        grant_access(**self.kw, employee_id=employee.pk, code="inventory.view", branch_ids=[self.branch.pk])
        self.client.force_login(user)
        page = self.client.get(reverse("inventory:products"))
        self.assertEqual(page.status_code, 200)
        for name in ("inventory:start", "inventory:materials", "inventory:products"):
            self.assertContains(page, f'href="{reverse(name)}"')
        # Viewing is not setting up: no Add, and no money without "See costs".
        self.assertNotContains(page, "Add product")
        self.assertNotContains(self.client.get(reverse("inventory:product", args=[self.juice.pk])),
                               "Cost of 1,000 PCS (the recipe)")
