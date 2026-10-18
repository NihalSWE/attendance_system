"""Phase 5 "done when": producing 1,000 bottles consumes the BOM's materials,
creates a finished-goods batch with expiry and labels, and its unit cost
equals materials ÷ good quantity."""

import datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from access_control.branch_access import grant_access
from common.tenant import use_company
from employees.models import Employee
from inventory import masters, posting
from inventory.models import (
    Batch,
    BarcodeLabel,
    ItemCategory,
    ItemCost,
    ItemKind,
    MoveType,
    StockBalance,
    Warehouse,
)
from inventory.testing import DAY, D, InventoryFixture
from production import boms, orders
from production.models import Bom, Consumption, ProductionOrder


class ProductionFixture(InventoryFixture):
    def setUp(self):
        super().setUp()
        self.kw = {"actor": self.owner, "company_id": self.company.pk}
        packing = self.category("BTL", "Bottles and caps", ItemKind.PACKAGING)
        juice = self.category("JCE", "Juice", ItemKind.FINISHED)
        self.pulp = self.item("RM-0002", "Mango pulp", ItemKind.RAW, self.raw, self.kg,
                              track_batch=True, track_expiry=True, shelf_life_days=540)
        self.bottle = self.item("PM-0001", "PET bottle 250 ml", ItemKind.PACKAGING, packing, self.pcs)
        self.cap = self.item("PM-0002", "Cap 28 mm", ItemKind.PACKAGING, packing, self.pcs)
        self.juice = self.item("FG-0001", "Mango juice 250 ml", ItemKind.FINISHED, juice, self.pcs,
                               track_batch=True, track_expiry=True, shelf_life_days=270)
        self.pks = self.store("PKS", "Packaging store", Warehouse.Kind.PACKAGING)
        # Materials in stock: pulp in two batches (FEFO), sugar, bottles, caps.
        self.p1 = Batch.all_objects.create(company=self.company, item=self.pulp, batch_no="P1", source="opening",
                                           expiry_date=datetime.date(2027, 1, 31))
        self.p2 = Batch.all_objects.create(company=self.company, item=self.pulp, batch_no="P2", source="opening",
                                           expiry_date=datetime.date(2027, 6, 30))
        self.s1 = Batch.all_objects.create(company=self.company, item=self.sugar, batch_no="S1", source="opening",
                                           expiry_date=datetime.date(2027, 9, 30))
        posting.post(company_id=self.company.pk, actor=self.owner, document_type="opening", document_id=1,
                     move_type=MoveType.OPENING, work_date=datetime.date(2026, 9, 1), lines=[
                         posting.Line(item=self.pulp, warehouse=self.rms, batch=self.p1, qty=D("30"), unit_cost=D("210")),
                         posting.Line(item=self.pulp, warehouse=self.rms, batch=self.p2, qty=D("100"), unit_cost=D("210")),
                         posting.Line(item=self.sugar, warehouse=self.rms, batch=self.s1, qty=D("100"), unit_cost=D("125")),
                         posting.Line(item=self.bottle, warehouse=self.rms, qty=D("5000"), unit_cost=D("3.20")),
                         posting.Line(item=self.cap, warehouse=self.rms, qty=D("5000"), unit_cost=D("0.90"))])

    def recipe(self):
        bom = boms.create_bom(**self.kw, values={"item": self.juice, "batch_size": D("1000"),
                                                 "batch_uom": self.pcs, "effective_from": None, "note": ""})
        for item, qty, uom, scrap, packaging in ((self.pulp, "45", self.kg, "0", False),
                                                 (self.sugar, "22", self.kg, "0", False),
                                                 (self.bottle, "1000", self.pcs, "1.5", True),
                                                 (self.cap, "1000", self.pcs, "1", True)):
            boms.add_line(**self.kw, bom_id=bom.pk, values={"item": item, "qty": D(qty), "uom": uom,
                                                            "scrap_pct": D(scrap), "is_packaging": packaging,
                                                            "note": ""})
        return boms.activate(**self.kw, bom_id=bom.pk)

    def released(self, qty="1000"):
        self.recipe()
        order = orders.create_order(**self.kw, values={
            "item": self.juice, "bom": None, "planned_qty": D(qty), "uom": self.pcs, "planned_date": DAY,
            "material_warehouse": self.rms, "output_warehouse": self.fgw, "line_name": "L1",
            "shift_name": "Day", "note": ""})
        orders.release(**self.kw, order_id=order.pk)
        return ProductionOrder.all_objects.get(pk=order.pk)


class DoneWhenTests(ProductionFixture, TestCase):
    def test_a_thousand_bottles(self):
        order = self.released()
        self.assertEqual((order.number, order.bom.version), ("PRD-2026-00001", 1))
        use = orders.start_consumption(**self.kw, order_id=order.pk)
        with use_company(self.company):
            lines = sorted((l.item.code, l.batch.batch_no if l.batch_id else "", l.qty)
                           for l in use.lines.select_related("item", "batch"))
        # 45 kg pulp first expiry first (30 of P1, 15 of P2); bottles and caps with their scrap.
        self.assertEqual(lines, [("PM-0001", "", D("1015")), ("PM-0002", "", D("1010")),
                                 ("RM-0001", "S1", D("22")), ("RM-0002", "P1", D("30")),
                                 ("RM-0002", "P2", D("15"))])
        orders.post_consumption(**self.kw, consumption_id=use.pk)
        # 9,450 + 2,750 + 3,248 + 909 = 16,357
        self.assertEqual(orders.material_cost(order), D("16357"))

        output = orders.post_output(**self.kw, order_id=order.pk, values={
            "output_date": DAY, "qty_good": D("990"), "qty_rejected": D("10"), "uom": self.pcs,
            "batch_no": "", "mfg_date": None, "expiry_date": None, "location": None, "packages": 33,
            "note": ""})
        order.refresh_from_db()
        self.assertEqual((order.status, order.good_qty_base, order.rejected_qty_base),
                         ("completed", D("990"), D("10")))
        self.assertEqual(order.unit_cost, D("16.5222"))           # 16,357 / 990
        batch = output.batch
        self.assertEqual((batch.batch_no, batch.source, batch.status, batch.mfg_date, batch.expiry_date),
                         ("FG0001-261001-L1", "production", "released", DAY, DAY + datetime.timedelta(days=270)))
        self.assertEqual(StockBalance.all_objects.get(item=self.juice, warehouse=self.fgw).qty_on_hand, D("990"))
        self.assertEqual(ItemCost.all_objects.get(item=self.juice).avg_cost, D("16.5222"))
        made = list(orders.output_labels(output))
        self.assertEqual(len(made), 33)
        self.assertEqual({(m.quantity, m.batch_id) for m in made}, {(D("30"), batch.pk)})
        # Planned vs used.
        usage = {row["item"].code: row for row in orders.usage(order)}
        self.assertEqual((usage["PM-0001"]["planned"], usage["PM-0001"]["used"]), (D("1015"), D("1015")))
        self.assertEqual(posting.ledger_differences(self.company.pk), [])
        with self.assertRaisesMessage(ValidationError, "is completed"):
            orders.start_consumption(**self.kw, order_id=order.pk)
        orders.close(**self.kw, order_id=order.pk)

    def test_unused_material_goes_back_at_its_cost_and_lowers_the_product_cost(self):
        order = self.released()
        use = orders.start_consumption(**self.kw, order_id=order.pk)
        orders.post_consumption(**self.kw, consumption_id=use.pk)
        back = orders.start_consumption(**self.kw, order_id=order.pk, kind=Consumption.Kind.RETURN)
        orders.add_line(**self.kw, consumption_id=back.pk, values={
            "item": self.bottle, "qty": D("15"), "uom": self.pcs})
        with self.assertRaisesMessage(ValidationError, "More than used"):
            orders.add_line(**self.kw, consumption_id=back.pk, values={
                "item": self.cap, "qty": D("2000"), "uom": self.pcs})
        orders.post_consumption(**self.kw, consumption_id=back.pk)
        self.assertEqual(orders.material_cost(order), D("16309"))     # 16,357 - 15 x 3.20
        output = orders.post_output(**self.kw, order_id=order.pk, values={
            "output_date": DAY, "qty_good": D("1000"), "qty_rejected": D("0"), "uom": self.pcs, "batch_no": "",
            "mfg_date": None, "expiry_date": None, "location": None, "packages": None, "note": ""})
        self.assertEqual(ProductionOrder.all_objects.get(pk=order.pk).unit_cost, D("16.3090"))
        self.assertEqual(output.batch.batch_no, "FG0001-261001-L1")


class RuleTests(ProductionFixture, TestCase):
    def test_bom_versions(self):
        v1 = self.recipe()
        with self.assertRaisesMessage(ValidationError, "start a new version"):
            boms.add_line(**self.kw, bom_id=v1.pk, values={"item": self.bearing, "qty": D("1"),
                                                           "uom": self.pcs, "scrap_pct": None,
                                                           "is_packaging": False, "note": ""})
        v2 = boms.new_version(**self.kw, bom_id=v1.pk)
        with use_company(self.company):
            self.assertEqual((v2.version, v2.lines.count()), (2, 4))
        boms.activate(**self.kw, bom_id=v2.pk)
        self.assertEqual(Bom.all_objects.get(pk=v1.pk).status, "archived")
        with self.assertRaisesMessage(ValidationError, "is not a finished product"):
            boms.create_bom(**self.kw, values={"item": self.sugar, "batch_size": D("1"), "batch_uom": self.kg,
                                               "effective_from": None, "note": ""})

    def test_no_active_bom_no_order(self):
        with self.assertRaisesMessage(ValidationError, "has no active BOM"):
            orders.create_order(**self.kw, values={
                "item": self.juice, "bom": None, "planned_qty": D("10"), "uom": self.pcs, "planned_date": DAY,
                "material_warehouse": self.rms, "output_warehouse": self.fgw, "line_name": "", "shift_name": "",
                "note": ""})

    def test_someone_else_releases_and_materials_need_a_released_order(self):
        user = self.member("planner@inv.test", "employee")
        with use_company(self.company):
            employee = Employee.objects.create(company=self.company, user=user, first_name="P")
        for code in ("production.plan", "production.execute"):
            grant_access(**self.kw, employee_id=employee.pk, code=code, branch_ids=[self.branch.pk])
        self.recipe()
        mine = {"actor": user, "company_id": self.company.pk}
        order = orders.create_order(**mine, values={
            "item": self.juice, "bom": None, "planned_qty": D("100"), "uom": self.pcs, "planned_date": DAY,
            "material_warehouse": self.rms, "output_warehouse": self.fgw, "line_name": "", "shift_name": "",
            "note": ""})
        with self.assertRaisesMessage(ValidationError, "only on a released order"):
            orders.start_consumption(**mine, order_id=order.pk)
        with self.assertRaisesMessage(PermissionDenied, "someone else approves it"):
            orders.release(**mine, order_id=order.pk)
        orders.release(**self.kw, order_id=order.pk)
        orders.start_consumption(**mine, order_id=order.pk)

    def test_expired_or_quarantined_material_is_refused(self):
        order = self.released()
        self.p1.status = Batch.Status.QUARANTINE
        self.p1.save()
        use = orders.start_consumption(**self.kw, order_id=order.pk, fill_planned=False)
        with self.assertRaisesMessage(ValidationError, "cannot be used (quarantine)"):
            orders.add_line(**self.kw, consumption_id=use.pk, values={
                "item": self.pulp, "batch": self.p1, "qty": D("5"), "uom": self.kg})
        orders.add_line(**self.kw, consumption_id=use.pk, values={"item": self.pulp, "qty": D("45"), "uom": self.kg})
        with use_company(self.company):
            self.assertEqual(list(use.lines.values_list("batch__batch_no", "qty")), [("P2", D("45"))])

    def test_output_needs_materials_and_qc_items_wait(self):
        order = self.released()
        with self.assertRaisesMessage(ValidationError, "No materials were used"):
            orders.post_output(**self.kw, order_id=order.pk, values={
                "output_date": DAY, "qty_good": D("10"), "qty_rejected": D("0"), "uom": self.pcs, "batch_no": "",
                "mfg_date": None, "expiry_date": None, "location": None, "packages": None, "note": ""})
        self.juice.qc_required = True
        self.juice.save()
        use = orders.start_consumption(**self.kw, order_id=order.pk)
        orders.post_consumption(**self.kw, consumption_id=use.pk)
        output = orders.post_output(**self.kw, order_id=order.pk, values={
            "output_date": DAY, "qty_good": D("1000"), "qty_rejected": D("0"), "uom": self.pcs, "batch_no": "X1",
            "mfg_date": None, "expiry_date": None, "location": None, "packages": None, "note": ""})
        self.assertEqual(output.batch.status, "quarantine")
        orders.record_qc(**self.kw, order_id=order.pk, passed=True)
        self.assertEqual(Batch.all_objects.get(pk=output.batch_id).status, "released")
