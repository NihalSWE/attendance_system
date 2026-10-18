"""Phase 4 "done when": each store document posts the right moves, refuses
negative stock and expired batches, and a count difference becomes an
approved adjustment."""

import datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from access_control.branch_access import grant_access
from common.tenant import use_company
from employees.models import Employee
from inventory import adjustments, issues, labels, masters, posting, transfers
from inventory.models import (
    Batch,
    BarcodeLabel,
    ItemCost,
    MoveType,
    StockAdjustment,
    StockBalance,
    StockMove,
    StoreRequisition,
    Warehouse,
)
from inventory.testing import DAY, D, InventoryFixture
from organization.models import Department


class StoreFixture(InventoryFixture):
    """Sugar in RMS: batch A (expires Mar 2027) 80 kg at 100, batch B (Jun 2027) 100 kg at 130."""

    def setUp(self):
        super().setUp()
        self.kw = {"actor": self.owner, "company_id": self.company.pk}
        with use_company(self.company):
            self.production = Department.objects.create(company=self.company, branch=self.branch,
                                                        code="PRD", name="Production")
        self.a = self.batch("A", datetime.date(2027, 3, 31))
        self.b = self.batch("B", datetime.date(2027, 6, 30))
        self.old = self.batch("OLD", datetime.date(2026, 9, 1))
        self.rejected = self.store("REJ", "Rejected", Warehouse.Kind.REJECTED)
        posting.post(company_id=self.company.pk, actor=self.owner, document_type="opening", document_id=1,
                     move_type=MoveType.OPENING, work_date=datetime.date(2026, 8, 1), lines=[
                         self.line(self.a, "80", "100"), self.line(self.b, "100", "130"),
                         self.line(self.old, "5", "90")])

    def batch(self, number, expiry):
        return Batch.all_objects.create(company=self.company, item=self.sugar, batch_no=number,
                                        source="opening", expiry_date=expiry)

    def line(self, batch, qty, cost, store=None):
        return posting.Line(item=self.sugar, warehouse=store or self.rms, batch=batch, qty=D(qty),
                            unit_cost=D(cost))

    def on_hand(self, batch=None, store=None):
        rows = StockBalance.all_objects.filter(item=self.sugar, warehouse=store or self.rms)
        if batch is not None:
            rows = rows.filter(batch=batch)
        return sum((r.qty_on_hand for r in rows), D("0"))

    def moves(self, doc_type, doc_id):
        return list(StockMove.all_objects.filter(document_type=doc_type, document_id=doc_id)
                    .order_by("pk").values_list("move_type", "warehouse__code", "batch__batch_no", "qty"))

    def new_issue(self, **extra):
        return issues.create_issue(**self.kw, values={"warehouse": self.rms, "department": self.production,
                                                      "requisition": None, "issue_date": DAY,
                                                      "received_by": "Line 1", "note": "", **extra})


class IssueTests(StoreFixture, TestCase):
    def test_requisition_then_issue_first_expiry_first(self):
        requisition = issues.create_requisition(**self.kw, values={
            "department": self.production, "warehouse": self.rms, "request_date": DAY,
            "needed_by": None, "purpose": "Batch 42"})
        issues.add_requisition_line(**self.kw, requisition_id=requisition.pk,
                                    values={"item": self.sugar, "qty": D("120"), "uom": self.kg, "note": ""})
        issues.submit_requisition(**self.kw, requisition_id=requisition.pk)
        issues.decide_requisition(**self.kw, requisition_id=requisition.pk, approve=True)
        issue = issues.create_issue(**self.kw, values={
            "warehouse": None, "department": None, "requisition": requisition, "issue_date": DAY,
            "received_by": "", "note": ""})
        with use_company(self.company):
            lines = list(issue.lines.order_by("pk").values_list("batch__batch_no", "qty"))
        # The expired batch is skipped; A (first to expire) before B.
        self.assertEqual(lines, [("A", D("80")), ("B", D("40"))])
        issues.post_issue(**self.kw, issue_id=issue.pk)
        self.assertEqual(self.moves("store_issue", issue.pk),
                         [("issue", "RMS", "A", D("-80")), ("issue", "RMS", "B", D("-40"))])
        self.assertEqual(StoreRequisition.all_objects.get(pk=requisition.pk).status, "issued")
        # Issued at the average: (80x100 + 100x130 + 5x90) / 185 = 115.94595, kept to
        # 4 places after each receipt (100, then 116.6667, then 115.9460).
        self.assertEqual(ItemCost.all_objects.get(item=self.sugar).avg_cost, D("115.9460"))

    def test_refuses_expired_quarantine_negative_and_unusable_stores(self):
        issue = self.new_issue()
        with self.assertRaisesMessage(ValidationError, "Batch OLD is expired"):
            issues.add_issue_line(**self.kw, issue_id=issue.pk, values={
                "item": self.sugar, "batch": self.old, "qty": D("1"), "uom": self.kg})
        with self.assertRaisesMessage(ValidationError, "Not enough usable RM-0001 in RMS: 180"):
            issues.add_issue_line(**self.kw, issue_id=issue.pk, values={
                "item": self.sugar, "qty": D("200"), "uom": self.kg})
        self.b.status = Batch.Status.QUARANTINE
        self.b.save()
        with self.assertRaisesMessage(ValidationError, "Batch B is quarantine"):
            issues.add_issue_line(**self.kw, issue_id=issue.pk, values={
                "item": self.sugar, "batch": self.b, "qty": D("1"), "uom": self.kg})
        posting.post(company_id=self.company.pk, actor=self.owner, document_type="t", document_id=2,
                     move_type=MoveType.OPENING, work_date=DAY,
                     lines=[posting.Line(item=self.bearing, warehouse=self.rejected, qty=D("2"),
                                         unit_cost=D("1"))])
        with self.assertRaisesMessage(ValidationError, "is a rejected store"):
            posting.post(company_id=self.company.pk, actor=self.owner, document_type="t", document_id=3,
                         move_type=MoveType.ISSUE, work_date=DAY,
                         lines=[posting.Line(item=self.bearing, warehouse=self.rejected, qty=D("-1"))])

    def test_a_later_batch_needs_a_reason(self):
        issue = self.new_issue()
        with self.assertRaisesMessage(ValidationError, "Batch A expires first"):
            issues.add_issue_line(**self.kw, issue_id=issue.pk, values={
                "item": self.sugar, "batch": self.b, "qty": D("10"), "uom": self.kg})
        issues.add_issue_line(**self.kw, issue_id=issue.pk, values={
            "item": self.sugar, "batch": self.b, "qty": D("10"), "uom": self.kg,
            "not_fefo_reason": "A is at the back of the store"})

    def test_scanning_a_package_issues_it_and_uses_the_label_up(self):
        [package] = labels.package_labels(**self.kw, item_id=self.sugar.pk, batch_id=self.b.pk,
                                          uom_id=self.kg.pk, quantity=D("50"), count=1,
                                          warehouse_id=self.rms.pk)
        issue = self.new_issue()
        issues.scan_into_issue(**self.kw, issue_id=issue.pk, code=package.code)
        with self.assertRaisesMessage(ValidationError, "on this issue already"):
            issues.scan_into_issue(**self.kw, issue_id=issue.pk, code=package.code)
        issues.post_issue(**self.kw, issue_id=issue.pk)
        self.assertEqual(self.on_hand(self.b), D("50"))
        self.assertEqual(BarcodeLabel.all_objects.get(pk=package.pk).status, "consumed")
        issues.reverse_issue(**self.kw, issue_id=issue.pk, reason="Wrong department")
        self.assertEqual(BarcodeLabel.all_objects.get(pk=package.pk).status, "active")
        self.assertEqual(self.on_hand(self.b), D("100"))

    def test_material_return_good_and_damaged(self):
        issue = self.new_issue()
        issues.add_issue_line(**self.kw, issue_id=issue.pk, values={
            "item": self.sugar, "batch": self.a, "qty": D("30"), "uom": self.kg})
        issues.post_issue(**self.kw, issue_id=issue.pk)
        with use_company(self.company):
            issue_line = issue.lines.get()
        back = issues.create_return(**self.kw, values={"department": None, "warehouse": None, "issue": issue,
                                                      "return_date": DAY, "note": ""})
        issues.add_return_line(**self.kw, return_id=back.pk, values={
            "issue_line": issue_line, "qty": D("10"), "uom": self.kg, "condition": "good"})
        issues.add_return_line(**self.kw, return_id=back.pk, values={
            "issue_line": issue_line, "qty": D("5"), "uom": self.kg, "condition": "damaged"})
        with self.assertRaisesMessage(ValidationError, "More than issued"):
            issues.add_return_line(**self.kw, return_id=back.pk, values={
                "issue_line": issue_line, "qty": D("20"), "uom": self.kg, "condition": "good"})
        issues.post_return(**self.kw, return_id=back.pk)
        self.assertEqual(self.moves("material_return", back.pk),
                         [("issue_return", "RMS", "A", D("10")), ("issue_return", "REJ", "A", D("5"))])
        self.assertEqual(self.on_hand(self.a), D("60"))
        self.assertEqual(posting.ledger_differences(self.company.pk), [])


class TransferTests(StoreFixture, TestCase):
    def test_dispatch_transit_receive_with_a_shortage(self):
        transfer = transfers.create_transfer(**self.kw, values={
            "from_warehouse": self.rms, "to_warehouse": self.fgw, "transfer_date": DAY,
            "vehicle_no": "", "note": ""})
        [line] = transfers.add_line(**self.kw, transfer_id=transfer.pk, values={
            "item": self.sugar, "batch": self.b, "qty": D("50"), "uom": self.kg})
        transfers.dispatch(**self.kw, transfer_id=transfer.pk)
        transit = Warehouse.all_objects.get(kind="transit")
        self.assertEqual((self.on_hand(self.b), self.on_hand(self.b, transit)), (D("50"), D("50")))
        with self.assertRaisesMessage(ValidationError, "did not arrive - say why"):
            transfers.receive(**self.kw, transfer_id=transfer.pk, received_date=DAY, received={line.pk: D("48")})
        transfers.receive(**self.kw, transfer_id=transfer.pk, received_date=DAY,
                          received={line.pk: D("48")}, reasons={line.pk: "Bag torn"})
        self.assertEqual((self.on_hand(self.b, transit), self.on_hand(self.b, self.fgw)), (D("0"), D("48")))
        self.assertIn(("adjustment", transit.code, "B", D("-2")), self.moves("stock_transfer", transfer.pk))
        # Reversing puts everything back in the sending store.
        transfers.reverse_transfer(**self.kw, transfer_id=transfer.pk, reason="Sent by mistake")
        self.assertEqual((self.on_hand(self.b), self.on_hand(self.b, self.fgw)), (D("100"), D("0")))
        self.assertEqual(posting.ledger_differences(self.company.pk), [])

    def test_a_line_without_batch_is_taken_first_expiry_first(self):
        transfer = transfers.create_transfer(**self.kw, values={
            "from_warehouse": self.rms, "to_warehouse": self.fgw, "transfer_date": DAY,
            "vehicle_no": "", "note": ""})
        transfers.add_line(**self.kw, transfer_id=transfer.pk, values={
            "item": self.sugar, "qty": D("90"), "uom": self.kg})
        with use_company(self.company):
            self.assertEqual(list(transfer.lines.order_by("pk").values_list("batch__batch_no", "qty")),
                             [("A", D("80")), ("B", D("10"))])

    def test_each_store_keeps_its_own_average(self):
        from inventory.models import InventorySettings

        # Valuation per store is chosen before stock moves (the bearing has none).
        InventorySettings.all_objects.create(company=self.company, valuation_scope="warehouse")
        posting.post(company_id=self.company.pk, actor=self.owner, document_type="o", document_id=9,
                     move_type=MoveType.OPENING, work_date=DAY, lines=[
                         posting.Line(item=self.bearing, warehouse=self.rms, qty=D("10"), unit_cost=D("100")),
                         posting.Line(item=self.bearing, warehouse=self.fgw, qty=D("10"), unit_cost=D("200"))])
        transfer = transfers.create_transfer(**self.kw, values={
            "from_warehouse": self.rms, "to_warehouse": self.fgw, "transfer_date": DAY,
            "vehicle_no": "", "note": ""})
        transfers.add_line(**self.kw, transfer_id=transfer.pk, values={
            "item": self.bearing, "qty": D("10"), "uom": self.pcs})
        transfers.dispatch(**self.kw, transfer_id=transfer.pk)
        transfers.receive(**self.kw, transfer_id=transfer.pk, received_date=DAY)
        # 10 at 100 arrive in FGW (10 at 200): (10 x 200 + 10 x 100) / 20 = 150
        self.assertEqual(ItemCost.all_objects.get(item=self.bearing, warehouse=self.fgw).avg_cost, D("150"))
        # (No ledger check here: the fixture's sugar moved before the switch, which
        # the settings page refuses and the nightly check rightly reports.)


class AdjustmentTests(StoreFixture, TestCase):
    def test_adjustment_needs_someone_else_to_approve(self):
        user = self.member("keeper@inv.test", "employee")
        with use_company(self.company):
            employee = Employee.objects.create(company=self.company, user=user, first_name="K")
        for code in ("inventory.adjust", "inventory.adjust_approve"):
            grant_access(**self.kw, employee_id=employee.pk, code=code, branch_ids=[self.branch.pk])
        mine = {"actor": user, "company_id": self.company.pk}
        adjustment = adjustments.create_adjustment(**mine, values={
            "warehouse": self.rms, "adjustment_date": DAY, "reason": "damage", "note": ""})
        adjustments.add_line(**mine, adjustment_id=adjustment.pk, values={
            "item": self.sugar, "batch": self.a, "qty_change": D("-3"), "uom": self.kg})
        adjustments.submit(**mine, adjustment_id=adjustment.pk)
        with self.assertRaisesMessage(PermissionDenied, "someone else approves it"):
            adjustments.decide(**mine, adjustment_id=adjustment.pk, approve=True)
        adjustments.decide(**self.kw, adjustment_id=adjustment.pk, approve=True)
        self.assertEqual(self.on_hand(self.a), D("77"))

    def test_found_stock_in_a_new_batch_and_no_negative(self):
        adjustment = adjustments.create_adjustment(**self.kw, values={
            "warehouse": self.rms, "adjustment_date": DAY, "reason": "found", "note": ""})
        with self.assertRaisesMessage(ValidationError, "give the new batch's"):
            adjustments.add_line(**self.kw, adjustment_id=adjustment.pk, values={
                "item": self.sugar, "batch_no": "N1", "qty_change": D("4"), "uom": self.kg})
        adjustments.add_line(**self.kw, adjustment_id=adjustment.pk, values={
            "item": self.sugar, "batch_no": "N1", "expiry_date": datetime.date(2027, 1, 1),
            "qty_change": D("4"), "uom": self.kg, "unit_cost": D("120")})
        adjustments.add_line(**self.kw, adjustment_id=adjustment.pk, values={
            "item": self.sugar, "batch": self.old, "qty_change": D("-9"), "uom": self.kg})
        adjustments.submit(**self.kw, adjustment_id=adjustment.pk)
        with self.assertRaisesMessage(ValidationError, "Not enough RM-0001 (batch OLD)"):
            adjustments.decide(**self.kw, adjustment_id=adjustment.pk, approve=True)
        self.assertFalse(Batch.all_objects.filter(batch_no="N1").exists(), "nothing written")


class CountTests(StoreFixture, TestCase):
    def test_count_difference_becomes_an_approved_adjustment(self):
        count = adjustments.start_count(**self.kw, values={"warehouse": self.rms, "count_date": DAY, "note": ""})
        with use_company(self.company):
            lines = {line.batch.batch_no: line for line in count.lines.select_related("batch")}
        self.assertEqual({k: v.system_qty for k, v in lines.items()},
                         {"A": D("80"), "B": D("100"), "OLD": D("5")})
        with self.assertRaisesMessage(ValidationError, "has a count going on already"):
            adjustments.start_count(**self.kw, values={"warehouse": self.rms, "count_date": DAY, "note": ""})
        [package] = labels.package_labels(**self.kw, item_id=self.sugar.pk, batch_id=self.b.pk,
                                          uom_id=self.kg.pk, quantity=D("50"), count=2,
                                          warehouse_id=self.rms.pk)[:1]
        adjustments.scan_into_count(**self.kw, count_id=count.pk, code=package.code)
        with self.assertRaisesMessage(ValidationError, "counted already"):
            adjustments.scan_into_count(**self.kw, count_id=count.pk, code=package.code)
        adjustments.set_counted(**self.kw, count_id=count.pk, counted={lines["A"].pk: D("78")})
        with self.assertRaisesMessage(ValidationError, "1 line is not counted yet"):
            adjustments.submit_count(**self.kw, count_id=count.pk)
        adjustments.zero_uncounted(**self.kw, count_id=count.pk)
        adjustments.submit_count(**self.kw, count_id=count.pk)
        adjustments.approve_count(**self.kw, count_id=count.pk)
        adjustment = StockAdjustment.all_objects.get(count=count)
        self.assertEqual((adjustment.status, adjustment.reason), ("posted", "count"))
        with use_company(self.company):
            changes = sorted(adjustment.lines.values_list("batch__batch_no", "qty_change"))
        self.assertEqual(changes, [("A", D("-2")), ("B", D("-50")), ("OLD", D("-5"))])
        self.assertEqual((self.on_hand(self.a), self.on_hand(self.b), self.on_hand(self.old)),
                         (D("78"), D("50"), D("0")))
        self.assertEqual(posting.ledger_differences(self.company.pk), [])
