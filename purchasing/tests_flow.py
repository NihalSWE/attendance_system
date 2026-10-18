"""Phase 3 "done when": PR -> PO -> two partial GRNs -> QC -> labels -> stock
and average cost correct; a purchase return reduces both."""

import datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from access_control.branch_access import grant_access
from common.tenant import use_company
from employees.models import Employee
from inventory import masters, opening, posting
from inventory.models import Batch, BarcodeLabel, ItemCost, MoveType, StockBalance
from inventory.testing import DAY, D, InventoryFixture
from purchasing import orders, receipts, requisitions, returns
from purchasing.models import (
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseRequisition,
    QualityCheck,
    Supplier,
)
from purchasing.services import SUPPLIER


class PurchaseFixture(InventoryFixture):
    def setUp(self):
        super().setUp()
        kw = {"actor": self.owner, "company_id": self.company.pk}
        self.kw = kw
        self.sugar.qc_required = True
        self.sugar.save()
        masters.add_pack_unit(**kw, item_id=self.sugar.pk,
                              values={"uom": self.bag, "factor": D("50"), "barcode": ""})
        self.supplier = masters.create(SUPPLIER, **kw, values={
            "code": "SUP-1", "name": "Sugar Traders", "email": "orders@sugar.test",
            "payment_terms_days": 30})

    def on_hand(self, batch=None):
        rows = StockBalance.all_objects.filter(item=self.sugar)
        if batch is not None:
            rows = rows.filter(batch=batch)
        return sum((r.qty_on_hand for r in rows), D("0"))

    def average(self):
        return ItemCost.all_objects.get(item=self.sugar, warehouse__isnull=True).avg_cost

    def approved_order(self, bags="6", price="6500"):
        requisition = requisitions.create_requisition(**self.kw, values={
            "branch": self.branch, "department": None, "request_date": DAY, "needed_by": None,
            "reason": "Next month's production"})
        requisitions.add_line(**self.kw, requisition_id=requisition.pk,
                              values={"item": self.sugar, "qty": D(bags), "uom": self.bag, "note": ""})
        requisitions.submit(**self.kw, requisition_id=requisition.pk)
        requisitions.decide(**self.kw, requisition_id=requisition.pk, approve=True)
        with use_company(self.company):
            line = requisition.lines.get()
        order = orders.order_from_requisitions(
            **self.kw, values={"supplier": self.supplier, "branch": self.branch, "order_date": DAY,
                               "expected_date": None, "terms": "", "note": ""},
            requisition_line_ids=[line.pk])
        with use_company(self.company):
            order_line = order.lines.get()
        order_line.unit_price = D(price)
        order_line.save()
        orders.submit(**self.kw, order_id=order.pk)
        orders.approve(**self.kw, order_id=order.pk)
        return requisition, PurchaseOrder.all_objects.get(pk=order.pk), order_line

    def receive(self, order, order_line, bags, *, rejected="0", cost="6500", packages=None,
                day=DAY + datetime.timedelta(days=2)):
        receipt = receipts.create_receipt(**self.kw, values={
            "order": order, "supplier": None, "warehouse": self.rms, "received_date": day,
            "challan_no": "CH-1", "invoice_no": "", "vehicle_no": "", "note": ""},
            fill_from_order=False)
        receipts.add_line(**self.kw, receipt_id=receipt.pk, values={
            "order_line": order_line, "item": None, "location": None, "qty_received": D(bags),
            "qty_rejected": D(rejected), "uom": self.bag, "unit_cost": D(cost), "batch_no": "",
            "supplier_batch_no": "SB-9", "mfg_date": day - datetime.timedelta(days=10),
            "expiry_date": None, "packages": packages, "note": ""})
        return receipts.post_receipt(**self.kw, receipt_id=receipt.pk)


class DoneWhenTests(PurchaseFixture, TestCase):
    def test_pr_po_two_grns_qc_labels_return(self):
        # 100 kg already in stock at 120 a kg (opening stock).
        document = opening.create_document(**self.kw, values={"work_date": DAY, "note": ""})
        opening.add_line(**self.kw, document_id=document.pk, values={
            "warehouse": self.rms, "location": None, "item": self.sugar, "batch_no": "OLD",
            "supplier_batch_no": "", "mfg_date": datetime.date(2026, 9, 1), "expiry_date": None,
            "qty": D("100"), "uom": self.kg, "unit_cost": D("120"), "note": ""})
        opening.post_document(**self.kw, document_id=document.pk)

        # PR for 6 bags, ordered at 6,500 a bag (130 a kg).
        requisition, order, order_line = self.approved_order()
        self.assertEqual(PurchaseRequisition.all_objects.get(pk=requisition.pk).status, "ordered")
        self.assertEqual((order.number, order.status), ("PO-2026-00001", "approved"))

        # GRN 1: 2 bags in 2 packages -> quarantine, 2 labels, average 125.
        grn1 = self.receive(order, order_line, "2", packages=2)
        line1 = GoodsReceiptLine.all_objects.get(receipt=grn1)
        self.assertEqual((line1.batch.status, line1.qc_status, line1.batch.batch_no),
                         ("quarantine", "pending", "261003-01"))
        self.assertEqual(line1.batch.expiry_date, datetime.date(2027, 9, 23))  # shelf life 365
        self.assertEqual(self.on_hand(), D("200"))
        self.assertEqual(self.average(), D("125"))
        self.assertEqual(PurchaseOrder.all_objects.get(pk=order.pk).status, "partly_received")
        made = list(receipts.receipt_labels(grn1))
        self.assertEqual([(m.kind, m.quantity, m.uom.code, m.batch_id) for m in made],
                         [("package", D("1"), "BAG", line1.batch_id)] * 2)
        with self.assertRaisesMessage(ValidationError, "is quarantine"):
            posting.post(company_id=self.company.pk, actor=self.owner, document_type="test",
                         document_id=1, move_type=MoveType.ISSUE, work_date=DAY,
                         lines=[posting.Line(item=self.sugar, warehouse=self.rms, qty=D("-1"),
                                             batch=line1.batch)])

        # QC passes it.
        receipts.record_check(**self.kw, line_id=line1.pk, result=QualityCheck.Result.PASSED)
        self.assertEqual(Batch.all_objects.get(pk=line1.batch_id).status, "released")

        # GRN 2: 4 bags, 1 refused at the gate, landed at 7,000 a bag (140 a kg).
        grn2 = self.receive(order, order_line, "4", rejected="1", cost="7000")
        line2 = GoodsReceiptLine.all_objects.get(receipt=grn2)
        self.assertEqual(line2.batch.batch_no, "261003-02")
        self.assertEqual(self.on_hand(), D("350"))
        # (200 x 125 + 150 x 140) / 350 = 131.4286
        self.assertEqual(self.average(), D("131.4286"))
        order_line.refresh_from_db()
        self.assertEqual((order_line.qty_received_base, order_line.qty_open_base), (D("250"), D("50")))
        with self.assertRaisesMessage(ValidationError, "More than ordered"):
            self.receive(order, order_line, "2")

        # QC fails the second batch: blocked, sent back.
        receipts.record_check(**self.kw, line_id=line2.pk, result=QualityCheck.Result.FAILED,
                              notes="Moisture too high")
        self.assertEqual(Batch.all_objects.get(pk=line2.batch_id).status, "blocked")
        back = returns.create_return(**self.kw, values={"supplier": None, "receipt": grn2,
                                                       "return_date": DAY + datetime.timedelta(days=3),
                                                       "reason": "Failed QC: moisture"})
        returns.add_line(**self.kw, return_id=back.pk, values={
            "receipt_line": line2, "item": None, "batch": None, "warehouse": None, "location": None,
            "qty": D("3"), "uom": self.bag, "note": ""})
        with self.assertRaisesMessage(ValidationError, "More than received"):
            returns.add_line(**self.kw, return_id=back.pk, values={
                "receipt_line": line2, "item": None, "batch": None, "warehouse": None,
                "location": None, "qty": D("1"), "uom": self.bag, "note": ""})
        returns.post_return(**self.kw, return_id=back.pk)
        # Stock and average back to before GRN 2.
        self.assertEqual(self.on_hand(), D("200"))
        self.assertEqual(self.on_hand(line2.batch), D("0"))
        self.assertAlmostEqual(self.average(), D("125"), delta=D("0.001"))
        self.assertEqual(posting.ledger_differences(self.company.pk), [])

        # The rest will not come.
        orders.close(**self.kw, order_id=order.pk, note="Supplier out of stock")
        self.assertEqual(PurchaseOrder.all_objects.get(pk=order.pk).status, "closed")


class RuleTests(PurchaseFixture, TestCase):
    def test_numbers_and_requisition_rules(self):
        requisition = requisitions.create_requisition(**self.kw, values={
            "branch": self.branch, "department": None, "request_date": DAY, "needed_by": None,
            "reason": ""})
        self.assertEqual(requisition.number, "PR-2026-00001")
        with self.assertRaisesMessage(ValidationError, "Add what is needed"):
            requisitions.submit(**self.kw, requisition_id=requisition.pk)
        with self.assertRaisesMessage(ValidationError, "counted in KG or its pack units"):
            requisitions.add_line(**self.kw, requisition_id=requisition.pk,
                                  values={"item": self.sugar, "qty": D("1"), "uom": self.pcs, "note": ""})
        requisitions.add_line(**self.kw, requisition_id=requisition.pk,
                              values={"item": self.sugar, "qty": D("1"), "uom": self.bag, "note": ""})
        requisitions.submit(**self.kw, requisition_id=requisition.pk)
        with self.assertRaisesMessage(ValidationError, "Say why it is rejected"):
            requisitions.decide(**self.kw, requisition_id=requisition.pk, approve=False)
        requisitions.decide(**self.kw, requisition_id=requisition.pk, approve=False, note="Not now")
        self.assertEqual(PurchaseRequisition.all_objects.get(pk=requisition.pk).status, "rejected")

    def test_someone_else_approves_what_you_submitted(self):
        user = self.member("buyer@inv.test", "employee")
        with use_company(self.company):
            employee = Employee.objects.create(company=self.company, user=user, first_name="B")
        for code in ("purchase.order", "purchase.approve", "inventory.view"):
            grant_access(**self.kw, employee_id=employee.pk, code=code, branch_ids=[self.branch.pk])
        order = orders.create_order(actor=user, company_id=self.company.pk, values={
            "supplier": self.supplier, "branch": self.branch, "order_date": DAY,
            "expected_date": None, "terms": "", "note": ""})
        orders.add_line(actor=user, company_id=self.company.pk, order_id=order.pk, values={
            "item": self.bearing, "qty": D("4"), "uom": self.pcs, "unit_price": D("450")})
        self.assertEqual(order.terms, "Payment 30 days after delivery.")
        orders.submit(actor=user, company_id=self.company.pk, order_id=order.pk)
        with self.assertRaisesMessage(PermissionDenied, "someone else approves it"):
            orders.approve(actor=user, company_id=self.company.pk, order_id=order.pk)
        orders.approve(**self.kw, order_id=order.pk)

    def test_totals_discount_and_vat(self):
        order = orders.create_order(**self.kw, values={
            "supplier": self.supplier, "branch": self.branch, "order_date": DAY,
            "expected_date": None, "terms": "", "note": ""})
        orders.add_line(**self.kw, order_id=order.pk, values={
            "item": self.bearing, "qty": D("4"), "uom": self.pcs, "unit_price": D("500"),
            "discount_pct": D("10"), "vat_rate": D("15")})
        self.assertEqual(orders.totals(order), {"amount": D("1800.00"), "vat": D("270.00"),
                                                "total": D("2070.00"), "lines": 1})

    def test_grn_filled_from_the_order_and_reversed(self):
        _, order, order_line = self.approved_order(bags="2")
        receipt = receipts.create_receipt(**self.kw, values={
            "order": order, "supplier": None, "warehouse": self.rms, "received_date": DAY,
            "challan_no": "", "invoice_no": "", "vehicle_no": "", "note": ""})
        with use_company(self.company):
            line = receipt.lines.get()
        self.assertEqual((line.qty_received, line.uom, line.unit_cost), (D("2"), self.bag, D("6500")))
        receipts.update_line(**self.kw, receipt_id=receipt.pk, line_id=line.pk, values={
            "mfg_date": DAY, "packages": 2})
        receipts.post_receipt(**self.kw, receipt_id=receipt.pk)
        self.assertEqual(PurchaseOrder.all_objects.get(pk=order.pk).status, "received")
        receipts.reverse_receipt(**self.kw, receipt_id=receipt.pk, reason="Wrong store")
        self.assertEqual(PurchaseOrder.all_objects.get(pk=order.pk).status, "approved")
        self.assertEqual(self.on_hand(), D("0"))
        self.assertEqual(set(receipts.receipt_labels(receipt).values_list("status", flat=True)), {"void"})

    def test_a_cancelled_order_gives_the_requisition_back(self):
        requisition, order, _ = self.approved_order()
        orders.cancel(**self.kw, order_id=order.pk, note="Wrong supplier")
        self.assertEqual(PurchaseRequisition.all_objects.get(pk=requisition.pk).status, "approved")

    def test_receiving_needs_the_permission_in_the_store_branch(self):
        _, order, order_line = self.approved_order()
        user = self.member("gate@inv.test", "employee")
        with use_company(self.company):
            employee = Employee.objects.create(company=self.company, user=user, first_name="G")
        with self.assertRaises(PermissionDenied):
            receipts.create_receipt(actor=user, company_id=self.company.pk, values={
                "order": order, "supplier": None, "warehouse": self.rms, "received_date": DAY,
                "challan_no": "", "invoice_no": "", "vehicle_no": "", "note": ""})
        grant_access(**self.kw, employee_id=employee.pk, code="purchase.receive",
                     branch_ids=[self.branch.pk])
        receipt = receipts.create_receipt(actor=user, company_id=self.company.pk, values={
            "order": order, "supplier": None, "warehouse": self.rms, "received_date": DAY,
            "challan_no": "", "invoice_no": "", "vehicle_no": "", "note": ""})
        with use_company(self.company):
            line = receipt.lines.get()
        receipts.update_line(actor=user, company_id=self.company.pk, receipt_id=receipt.pk,
                             line_id=line.pk, values={"mfg_date": DAY, "packages": 6})
        receipts.post_receipt(actor=user, company_id=self.company.pk, receipt_id=receipt.pk)
        # Labels made under "Receive goods", without the labels permission.
        self.assertEqual(BarcodeLabel.all_objects.filter(kind="package").count(), 6)
        with use_company(self.company):
            qc_line = GoodsReceiptLine.objects.get(receipt=receipt)
        with self.assertRaises(PermissionDenied):
            receipts.record_check(actor=user, company_id=self.company.pk, line_id=qc_line.pk,
                                  result="passed")
