"""Phase 7 of the redesign (2026-10-27, docs/inventory/PRODUCTION_PLAN.md §15):
Start takes materials out of several stores; Finish records good and damaged,
splits the output, returns or wastes leftovers, records a failed production;
the full cost (materials + the day's costs) ÷ good; one batch per mix;
Reverse while nothing made was touched; Cancel after Start returns everything."""

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse

from inventory import posting
from inventory.models import Batch, Item, MoveType, StockBalance, Warehouse
from inventory.testing import D
from inventory.tests_phase6 import FETCH, PlanFixture
from production import making, orders, planning
from production.models import Consumption, ProductionOrder

# The fixture recipe: one batch = 1,000 bottles; 2 batches use pulp 90 kg (P1
# 30 + P2 60 at 210), sugar 44 kg (125), bottles 2,030 (3.20), caps 2,020
# (0.90) = 18,900 + 5,500 + 6,496 + 1,818 = 32,714.
MATERIALS = D("32714")


class RunFixture(PlanFixture):
    def setUp(self):
        super().setUp()
        self.order, _plan = planning.save(actor=self.owner, company_id=self.company.pk, product=self.juice,
                                          batches=2, into=self.fgw, day=self.today)

    def start(self, **extra):
        return making.start(actor=self.owner, company_id=self.company.pk, order_id=self.order.pk,
                            work_date=self.today, **extra)

    def finish(self, **values):
        values = {"output_date": self.today, "good": D("2000"), "damaged": D("0"), **values}
        return making.finish(actor=self.owner, company_id=self.company.pk, order_id=self.order.pk, values=values)

    def have(self, item, store=None):
        rows = StockBalance.all_objects.filter(company=self.company, item=item)
        if store is not None:
            rows = rows.filter(warehouse=store)
        return rows.aggregate(total=Sum("qty_on_hand"))["total"] or D("0")


class StartTests(RunFixture, TestCase):
    def test_materials_leave_each_store_at_their_cost(self):
        second = self.store("RSB", "Raw store B", Warehouse.Kind.RAW)
        self.opening(second, self.pulp, "50", self.pulp_batch("P3", self.today.replace(year=self.today.year + 1)))
        Item.all_objects.filter(pk=self.pulp.pk).update(default_warehouse=second)
        order = self.start()
        self.assertEqual(order.get_status_display(), "Started")
        self.assertEqual((self.have(self.pulp, second), self.have(self.pulp, self.rms)), (D("0"), D("90")))
        uses = Consumption.all_objects.filter(order=order)
        self.assertEqual(sorted(use.warehouse.code for use in uses), ["RMS", "RSB"])
        self.assertIsNotNone(order.expected_unit_cost)

    def test_start_is_refused_while_short(self):
        short, _plan = planning.save(actor=self.owner, company_id=self.company.pk, product=self.juice,
                                     batches=3, into=self.fgw, day=self.today)
        with self.assertRaisesMessage(ValidationError, "Mango pulp is short by 5 KG"):
            making.start(actor=self.owner, company_id=self.company.pk, order_id=short.pk, work_date=self.today)

    def test_a_store_keeper_gives_more_with_a_reason(self):
        found = making.proposal(self.company.pk, self.order, self.today)
        sugar = next(need for need in found.needs if need.item == self.sugar)
        key = (self.sugar.pk, sugar.takes[0].key)
        with self.assertRaises(ValidationError) as caught:
            self.start(changes={key: D("50")})
        self.assertIn("reason", caught.exception.message_dict)
        with self.assertRaises(ValidationError) as caught:
            self.start(changes={key: D("101")}, reason="spilled")
        self.assertIn(f"qty-{self.sugar.pk}-{sugar.takes[0].key}", caught.exception.message_dict)
        self.start(changes={key: D("50")}, reason="Second mix was thin")
        self.assertEqual(self.have(self.sugar), D("50"))


class FinishTests(RunFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.start()

    def test_full_cost_into_one_store(self):
        order, output = self.finish(costs=[("Line workers", D("900")), ("Gas", D("240"))])
        self.assertEqual(order.status, ProductionOrder.Status.COMPLETED)
        self.assertEqual((order.material_cost, order.other_cost), (MATERIALS, D("1140")))
        self.assertEqual(order.unit_cost, ((MATERIALS + 1140) / 2000).quantize(D("0.0001")))
        self.assertEqual(self.have(self.juice, self.fgw), D("2000"))
        self.assertEqual(output.batch.unit_cost, order.unit_cost)

    def test_split_into_two_stores_must_add_up(self):
        depot = self.store("DEP", "Depot", Warehouse.Kind.FINISHED)
        with self.assertRaises(ValidationError) as caught:
            self.finish(parts=[(self.fgw, D("1200")), (depot, D("700"))])
        self.assertIn("parts", caught.exception.message_dict)
        _order, output = self.finish(parts=[(self.fgw, D("1200")), (depot, D("800"))])
        self.assertEqual((self.have(self.juice, self.fgw), self.have(self.juice, depot)), (D("1200"), D("800")))
        self.assertEqual(set(StockBalance.all_objects.filter(item=self.juice).values_list("batch_id", flat=True)),
                         {output.batch_id})

    def test_far_from_the_plan_needs_a_reason(self):
        with self.assertRaises(ValidationError) as caught:
            self.finish(good=D("1500"))
        self.assertIn("reason", caught.exception.message_dict)
        order, _output = self.finish(good=D("1500"), damaged=D("500"), reason="Oven stopped")
        self.assertEqual((order.good_qty_base, order.rejected_qty_base), (D("1500"), D("500")))
        # The damaged carry no cost: the good ones carry it all.
        self.assertEqual(order.unit_cost, (MATERIALS / 1500).quantize(D("0.0001")))

    def test_leftovers_go_back_or_are_wasted_with_a_reason(self):
        with self.assertRaises(ValidationError) as caught:
            self.finish(leftovers={self.sugar.pk: (D("0"), D("2"), "")})
        self.assertIn(f"reason-{self.sugar.pk}", caught.exception.message_dict)
        order, _output = self.finish(leftovers={self.pulp.pk: (D("10"), D("0"), ""),
                                                self.sugar.pk: (D("0"), D("2"), "spilled")})
        self.assertEqual(self.have(self.pulp, self.rms), D("50"))       # 130 − 90 + 10
        self.assertEqual(order.material_cost, MATERIALS - D("2100"))     # 10 kg × 210 back
        p2 = Batch.all_objects.get(item=self.pulp, batch_no="P2")
        self.assertEqual(StockBalance.all_objects.get(batch=p2).qty_on_hand, D("50"))   # back into its batch

    def test_nothing_good_is_a_failed_production(self):
        with self.assertRaises(ValidationError) as caught:
            self.finish(good=D("0"))
        self.assertIn("reason", caught.exception.message_dict)
        order, output = self.finish(good=D("0"), damaged=D("2000"), reason="Burnt")
        self.assertIsNone(output)
        self.assertEqual((order.status, order.material_cost), (ProductionOrder.Status.FAILED, MATERIALS))
        self.assertEqual(self.have(self.juice), D("0"))

    def test_one_batch_number_per_mix(self):
        Item.all_objects.filter(pk=self.juice.pk).update(batch_per_mix=True)
        depot = self.store("DEP", "Depot", Warehouse.Kind.FINISHED)
        with self.assertRaisesMessage(ValidationError, "one store"):
            self.finish(parts=[(self.fgw, D("1000")), (depot, D("1000"))])
        _order, output = self.finish(good=D("1999"))
        batches = sorted((batch.batch_no, StockBalance.all_objects.get(batch=batch).qty_on_hand)
                         for batch in making.made_batches(output))
        self.assertEqual([qty for _no, qty in batches], [D("999"), D("1000")])
        self.assertEqual(len({number for number, _qty in batches}), 2)


class ReverseAndCancelTests(RunFixture, TestCase):
    def test_reverse_while_untouched_puts_everything_back(self):
        self.start()
        self.finish(leftovers={self.pulp.pk: (D("10"), D("0"), "")})
        order = making.reverse(actor=self.owner, company_id=self.company.pk, order_id=self.order.pk,
                               reason="Wrong product entered", work_date=self.today)
        self.assertEqual(order.status, ProductionOrder.Status.REVERSED)
        self.assertEqual((self.have(self.juice), self.have(self.pulp)), (D("0"), D("130")))

    def test_no_reverse_once_some_was_sold(self):
        self.start()
        _order, output = self.finish()
        posting.post(company_id=self.company.pk, actor=self.owner, document_type="delivery", document_id=1,
                     move_type=MoveType.DELIVERY, work_date=self.today, lines=[
                         posting.Line(item=self.juice, warehouse=self.fgw, batch=output.batch, qty=D("-10"))])
        self.assertFalse(making.untouched(self.reload(self.order)))
        with self.assertRaisesMessage(ValidationError, "already sold, moved or used"):
            making.reverse(actor=self.owner, company_id=self.company.pk, order_id=self.order.pk,
                           reason="x", work_date=self.today)

    def test_cancel_after_start_returns_every_material(self):
        self.start()
        with self.assertRaises(ValidationError):
            making.cancel_started(actor=self.owner, company_id=self.company.pk, order_id=self.order.pk,
                                  reason="", work_date=self.today)
        order = making.cancel_started(actor=self.owner, company_id=self.company.pk, order_id=self.order.pk,
                                      reason="Machine broke", work_date=self.today)
        self.assertEqual(order.status, ProductionOrder.Status.CANCELLED)
        self.assertEqual((self.have(self.pulp), self.have(self.sugar)), (D("130"), D("100")))


class RunPageTests(RunFixture, TestCase):
    def url(self, name="production:run", *more):
        return reverse(name, args=[self.order.pk, *more])

    def test_plan_start_finish_and_reverse_on_one_page(self):
        self.assertContains(self.client.get(reverse("production:runs")), self.order.number)
        self.assertContains(self.client.get(self.url()), "Start: materials out")
        body = self.client.post(self.url("production:run_start"), {"work_date": self.today.isoformat()},
                                **FETCH).json()
        self.assertTrue(body["ok"], body)
        page = self.client.get(self.url())
        self.assertContains(page, "Finish: into stock")
        body = self.client.post(self.url("production:run_finish"), {
            "good": "1000", "output_date": self.today.isoformat(), f"into-{self.fgw.pk}": "1000"}, **FETCH).json()
        self.assertEqual(body["errors"]["reason"][0][:7], "Good is")
        body = self.client.post(self.url("production:run_finish"), {
            "good": "2000", "output_date": self.today.isoformat(), f"into-{self.fgw.pk}": "2000",
            "cost-name-0": "Gas", "cost-amount-0": "240"}, **FETCH).json()
        self.assertTrue(body["ok"], body)
        page = self.client.get(self.url())
        self.assertContains(page, "What it cost")
        self.assertContains(page, "Gas")
        body = self.client.post(self.url("production:run_action", "reverse"), {"reason": "Test run"}, **FETCH).json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(self.reload(self.order).status, ProductionOrder.Status.REVERSED)

    def test_a_batch_waiting_for_its_check_is_passed_from_the_page(self):
        Item.all_objects.filter(pk=self.juice.pk).update(qc_required=True)
        self.start()
        _order, output = self.finish()
        self.assertEqual(output.batch.status, Batch.Status.QUARANTINE)
        self.assertContains(self.client.get(self.url()), "Quality check")
        body = self.client.post(self.url("production:run_action", "qc-pass"), {}, **FETCH).json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(self.reload(output.batch).status, Batch.Status.RELEASED)

    def test_old_orders_still_finish_their_old_way(self):
        # A production started before this phase keeps working on its old page.
        order = orders.release(actor=self.owner, company_id=self.company.pk, order_id=self.order.pk)
        self.assertEqual(order.get_status_display(), "Started")
        self.assertEqual(self.client.get(reverse("production:order_detail", args=[order.pk])).status_code, 200)
