"""Phase 6 of the redesign (2026-10-27, docs/inventory/PRODUCTION_PLAN.md §15):
how many to make, materials from several stores of the branch (usual store
first, first expiry first), shortages with Make fewer and Buy the rest, and
the plan page on the product."""

import datetime

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from common.tenant import use_company
from inventory import posting
from inventory.models import Batch, Item, MoveType, Warehouse
from inventory.testing import D
from organization.models import Branch
from production import planning
from production.models import ProductionOrder
from production.tests_flow import ProductionFixture
from purchasing.models import PurchaseRequisition

FETCH = {"HTTP_X_REQUESTED_WITH": "fetch"}
# The fixture: one batch = 1,000 bottles of juice: pulp 45 kg, sugar 22 kg,
# bottles 1,015, caps 1,010. In the raw store: pulp 130 kg (P1 30, P2 100),
# sugar 100 kg, bottles and caps 5,000 each - enough for 2 batches (pulp).


class PlanFixture(ProductionFixture):
    def setUp(self):
        super().setUp()
        self.recipe()
        self.today = timezone.localdate()
        self.client.force_login(self.owner)

    def opening(self, store, item, qty, batch=None, number=9):
        posting.post(company_id=self.company.pk, actor=self.owner, document_type="opening", document_id=number,
                     move_type=MoveType.OPENING, work_date=datetime.date(2026, 9, 2), lines=[
                         posting.Line(item=item, warehouse=store, batch=batch, qty=D(qty), unit_cost=D("200"))])

    def pulp_batch(self, number, expiry):
        return Batch.all_objects.create(company=self.company, item=self.pulp, batch_no=number, source="opening",
                                        expiry_date=expiry)

    def plan(self, qty, day=None):
        return planning.plan(self.company.pk, self.juice, D(qty), self.fgw, day or self.today)


class WhereFromTests(PlanFixture, TestCase):
    def test_two_batches_take_pulp_first_expiry_first(self):
        found = self.plan("2000")
        pulp = next(need for need in found.needs if need.item == self.pulp)
        self.assertEqual(pulp.need, D("90"))
        self.assertEqual([(take.batch.batch_no, take.qty) for take in pulp.takes], [("P1", D("30")), ("P2", D("60"))])
        self.assertEqual(found.short, [])
        # Pulp 130 KG at 45 a 1,000: 2,888 bottles.
        self.assertEqual(found.possible, D("2888"))
        bottles = next(need for need in found.needs if need.item == self.bottle)
        self.assertEqual(bottles.need, D("2030"))           # 1.5 % wastage

    def test_the_usual_store_first_then_the_others_of_the_branch(self):
        second = self.store("RSB", "Raw store B", Warehouse.Kind.RAW)
        self.opening(second, self.pulp, "50", self.pulp_batch("P3", datetime.date(2027, 12, 31)))
        Item.all_objects.filter(pk=self.pulp.pk).update(default_warehouse=second)
        self.juice.refresh_from_db()
        pulp = next(need for need in self.plan("3000").needs if need.item.pk == self.pulp.pk)
        self.assertEqual([(take.store.code, take.batch.batch_no, take.qty) for take in pulp.takes],
                         [("RSB", "P3", D("50")), ("RMS", "P1", D("30")), ("RMS", "P2", D("55"))])
        self.assertEqual(pulp.stores, [(second, D("50")), (self.rms, D("85"))])

    def test_another_branch_expired_and_waiting_batches_are_not_counted(self):
        with use_company(self.company):
            north = Branch.objects.create(code="NTH", name="North")
        far = self.store("NRS", "North raw store", Warehouse.Kind.RAW, branch=north)
        self.opening(far, self.pulp, "500", self.pulp_batch("N1", datetime.date(2027, 12, 31)))
        waiting = self.pulp_batch("Q1", datetime.date(2027, 12, 31))
        self.opening(self.rms, self.pulp, "70", waiting, number=10)
        Batch.all_objects.filter(pk=waiting.pk).update(status=Batch.Status.QUARANTINE)
        pulp = next(need for need in self.plan("2000").needs if need.item == self.pulp)
        self.assertEqual(pulp.available, D("130"))
        # P1 expires 31 Jan 2027: a plan for February cannot use it.
        later = next(need for need in self.plan("2000", datetime.date(2027, 2, 1)).needs if need.item == self.pulp)
        self.assertEqual((later.available, later.short), (D("100"), D("0")))


class ShortTests(PlanFixture, TestCase):
    def test_three_batches_are_short_of_pulp_and_two_are_possible(self):
        found = self.plan("3000")
        self.assertEqual([(need.item.code, need.short) for need in found.short], [("RM-0002", D("5"))])
        # Pulp 130 KG at 45 a 1,000: 2,888 bottles.
        self.assertEqual(found.possible, D("2888"))

    def test_buy_the_rest_asks_for_what_is_missing(self):
        requisition = planning.buy_the_rest(actor=self.owner, company_id=self.company.pk, product=self.juice,
                                            qty=D("3000"), into=self.fgw, day=self.today)
        with use_company(self.company):
            lines = list(requisition.lines.values_list("item__code", "qty"))
        self.assertEqual(lines, [("RM-0002", D("5"))])
        self.assertEqual(requisition.status, PurchaseRequisition.Status.DRAFT)
        with self.assertRaisesMessage(ValidationError, "Nothing is short"):
            planning.buy_the_rest(actor=self.owner, company_id=self.company.pk, product=self.juice,
                                  qty=D("1000"), into=self.fgw, day=self.today)

    def test_a_short_plan_is_saved_and_named_to_the_next_plan(self):
        order, found = planning.save(actor=self.owner, company_id=self.company.pk, product=self.juice,
                                     qty=D("3000"), into=self.fgw, day=self.today)
        self.assertTrue(found.short)
        self.assertEqual((order.planned_qty, order.status, order.material_warehouse), (D("3000"), "planned", self.rms))
        pulp = next(need for need in self.plan("1000").needs if need.item == self.pulp)
        self.assertEqual(pulp.others, [order.number])


class AnyQuantityTests(PlanFixture, TestCase):
    """Any amount, not whole batches (2026-11-03): the recipe is for 1,000
    bottles; 2,500 is one production with every material x 2.5."""

    def test_two_and_a_half_times_the_recipe(self):
        found = self.plan("2500")
        self.assertEqual(found.times, D("2.5"))
        pulp = next(need for need in found.needs if need.item == self.pulp)
        self.assertEqual(pulp.need, D("112.5"))              # 45 KG x 2.5
        bottles = next(need for need in found.needs if need.item.code == self.bottle.code)
        self.assertEqual(bottles.need, D("2538"))            # 1,015 x 2.5 = 2,537.5, a whole bottle more
        order, _found = planning.save(actor=self.owner, company_id=self.company.pk, product=self.juice,
                                      qty=D("2500"), into=self.fgw, day=self.today)
        self.assertEqual(order.planned_qty, D("2500"))
        page = self.client.get(reverse("production:plan", args=[self.juice.pk]), {"qty": "2500"})
        self.assertContains(page, "every material is × 2.5")
        self.assertContains(page, "The recipe is for 1,000 PCS")


class SuggestTests(PlanFixture, TestCase):
    def test_keep_minus_stock_minus_planned_capped_by_the_materials(self):
        self.assertIsNone(planning.suggest(self.company.pk, self.juice, self.fgw))   # nothing to keep
        Item.all_objects.filter(pk=self.juice.pk).update(min_stock=D("3000"))
        self.juice.refresh_from_db()
        found = planning.suggest(self.company.pk, self.juice, self.fgw)
        self.assertEqual((found.wanted, found.possible, found.qty), (D("3000"), D("2888"), D("2888")))
        planning.save(actor=self.owner, company_id=self.company.pk, product=self.juice, qty=D("1000"),
                      into=self.fgw, day=self.today)
        found = planning.suggest(self.company.pk, self.juice, self.fgw)
        self.assertEqual((found.coming, found.wanted, found.qty), (D("1000"), D("2000"), D("2000")))
        page = self.client.get(reverse("inventory:product", args=[self.juice.pk]))
        self.assertContains(page, "Suggested: make 2,000 PCS")
        self.assertContains(page, reverse("production:plan", args=[self.juice.pk]) + "?qty=2000")


class PlanPageTests(PlanFixture, TestCase):
    def url(self):
        return reverse("production:plan", args=[self.juice.pk])

    def test_the_page_shows_where_from_and_what_is_short(self):
        page = self.client.get(self.url(), {"qty": "3000", "planned_date": self.today.isoformat(),
                                            "into": self.fgw.pk})
        self.assertContains(page, "Short by 5 KG")
        self.assertContains(page, "Make less: 2,888 PCS")
        self.assertContains(page, "every material is × 3")
        self.assertContains(page, "Raw material store 130")       # all it has; 5 short
        part = self.client.get(self.url(), {"qty": "1000", "planned_date": self.today.isoformat(),
                                            "into": self.fgw.pk, "partial": "1"})
        self.assertNotContains(part, "<html")
        self.assertNotContains(part, "Short by")
        # From the product's "Make 3,000 PCS" link: the date and store keep their start.
        page = self.client.get(self.url(), {"qty": "3000"})
        self.assertContains(page, f'value="{self.today.isoformat()}"')
        self.assertContains(page, "Short by 5 KG")

    def test_plan_it_and_buy_the_rest_answer_in_place(self):
        data = {"qty": "0", "planned_date": self.today.isoformat(), "into": self.fgw.pk, "action": "save"}
        body = self.client.post(self.url(), data, **FETCH).json()
        self.assertEqual(body["errors"]["qty"], ["Write how much to make, above 0."])
        # Bottles are counted whole.
        body = self.client.post(self.url(), {**data, "qty": "1500.5"}, **FETCH).json()
        self.assertEqual(body["errors"]["qty"], ["Mango juice 250 ml is counted in whole PCS."])
        body = self.client.post(self.url(), {**data, "qty": "2000"}, **FETCH).json()
        order = ProductionOrder.all_objects.get(company=self.company)
        self.assertEqual(body["redirect"], reverse("production:run", args=[order.pk]))
        self.assertEqual((order.planned_qty, order.note), (D("2000"), ""))
        body = self.client.post(self.url(), {**data, "qty": "3000", "action": "buy"}, **FETCH).json()
        requisition = PurchaseRequisition.all_objects.get(company=self.company)
        self.assertEqual(body["redirect"], reverse("purchasing:requisition_detail", args=[requisition.pk]))

    def test_no_recipe_or_bought_products_are_sent_back(self):
        from production.models import Bom

        Bom.all_objects.filter(item=self.juice).update(status="archived")
        self.assertContains(self.client.get(self.url()), "has no recipe yet")
        Item.all_objects.filter(pk=self.juice.pk).update(sourcing="buy")
        self.assertRedirects(self.client.get(self.url()), reverse("inventory:product", args=[self.juice.pk]))

    def test_planning_needs_its_permission(self):
        self.client.force_login(self.member("viewer@inv.test", "employee"))
        self.assertNotEqual(self.client.get(self.url()).status_code, 200)
