"""Purchase pages: every form POSTed, through the whole flow, as the owner;
and the gates (module, permission, branch)."""

import datetime

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from access_control.branch_access import grant_access
from common.tenant import use_company
from employees.models import Employee
from inventory.models import BarcodeLabel, StockBalance
from purchasing.models import (
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseRequisition,
    PurchaseReturn,
)
from purchasing.tests_flow import PurchaseFixture
from inventory.testing import DAY, D

MENU = 'data-menu="purchase"'


class PurchasePageTests(PurchaseFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def test_every_list_opens_and_the_menu_shows(self):
        for name in ("requisitions", "orders", "receipts", "qc", "returns", "order_from_requisitions"):
            page = self.client.get(reverse(f"purchasing:{name}"))
            self.assertEqual(page.status_code, 200, name)
        self.assertContains(page, MENU)

    @override_settings(MAIL_CONFIGURED=True, DEFAULT_FROM_EMAIL="erp@inv.test",
                       EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_the_whole_flow_through_the_pages(self):
        c = self.client
        # Requisition.
        r = c.post(reverse("purchasing:requisitions"), {"branch": self.branch.pk,
                                                        "request_date": "2026-10-01", "reason": "Stock"})
        requisition = PurchaseRequisition.all_objects.get()
        self.assertRedirects(r, reverse("purchasing:requisition_detail", args=[requisition.pk]))
        c.post(reverse("purchasing:requisition_line_add", args=[requisition.pk]),
               {"item": self.sugar.pk, "qty": "4", "uom": self.bag.pk})
        c.post(reverse("purchasing:requisition_submit", args=[requisition.pk]))
        c.post(reverse("purchasing:requisition_decide", args=[requisition.pk]), {"decision": "approve"})
        requisition.refresh_from_db()
        self.assertEqual(requisition.status, "approved")

        # An order from the requisition, plus a line added directly.
        with use_company(self.company):
            pr_line = requisition.lines.get()
        r = c.post(reverse("purchasing:order_from_requisitions"), {
            "supplier": self.supplier.pk, "branch": self.branch.pk, "order_date": "2026-10-01",
            "requisition_lines": [pr_line.pk]})
        order = PurchaseOrder.all_objects.get()
        self.assertRedirects(r, reverse("purchasing:order_detail", args=[order.pk]))
        c.post(reverse("purchasing:order_line_add", args=[order.pk]), {
            "item": self.bearing.pk, "qty": "2", "uom": self.pcs.pk, "unit_price": "450",
            "discount_pct": "0", "vat_rate": "15"})
        c.post(reverse("purchasing:order_action", args=[order.pk, "submit"]))
        c.post(reverse("purchasing:order_action", args=[order.pk, "approve"]))
        order.refresh_from_db()
        self.assertEqual(order.status, "approved")
        pdf = c.get(reverse("purchasing:order_pdf", args=[order.pk]))
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        c.post(reverse("purchasing:order_email", args=[order.pk]), {"to": "", "message": "Urgent"})
        self.assertEqual(mail.outbox[0].to, ["orders@sugar.test"])
        self.assertEqual(mail.outbox[0].attachments[0][0], f"{order.number}.pdf")
        order.refresh_from_db()
        self.assertEqual(order.sent_to, "orders@sugar.test")

        # Goods receipt from the order (filled), sugar lines get dates and packages.
        r = c.post(reverse("purchasing:receipts"), {"order": order.pk, "warehouse": self.rms.pk,
                                                    "received_date": "2026-10-03", "challan_no": "C-7"})
        receipt = GoodsReceipt.all_objects.get()
        self.assertRedirects(r, reverse("purchasing:receipt_detail", args=[receipt.pk]))
        with use_company(self.company):
            sugar_line = receipt.lines.get(item=self.sugar)
            order_line = sugar_line.order_line
        c.post(reverse("purchasing:receipt_line_remove", args=[receipt.pk, sugar_line.pk]))
        c.post(reverse("purchasing:receipt_line_add", args=[receipt.pk]), {
            "order_line": order_line.pk, "qty_received": "4", "qty_rejected": "0", "uom": self.bag.pk,
            "unit_cost": "6500", "mfg_date": "2026-09-25", "packages": "4"})
        page = c.get(reverse("purchasing:receipt_detail", args=[receipt.pk]))
        self.assertContains(page, "data-scan-box")
        c.post(reverse("purchasing:receipt_action", args=[receipt.pk, "post"]))
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, "posted")
        self.assertEqual(PurchaseOrder.all_objects.get(pk=order.pk).status, "received")
        labels_pdf = c.post(reverse("purchasing:receipt_labels", args=[receipt.pk]), {"copies": "1"})
        self.assertEqual(labels_pdf["Content-Type"], "application/pdf")
        self.assertEqual(BarcodeLabel.all_objects.filter(kind="package", printed_count=1).count(), 4)

        # QC fails, the goods go back.
        with use_company(self.company):
            line = GoodsReceiptLine.objects.get(receipt=receipt, item=self.sugar)
        self.assertContains(c.get(reverse("purchasing:qc")), line.batch_no)
        c.post(reverse("purchasing:qc_record", args=[line.pk]), {"result": "failed", "notes": "Wet"})
        line.refresh_from_db()
        self.assertEqual((line.qc_status, line.batch.status), ("failed", "blocked"))
        r = c.post(reverse("purchasing:returns"), {"receipt": receipt.pk, "return_date": "2026-10-04",
                                                   "reason": "Failed QC"})
        back = PurchaseReturn.all_objects.get()
        self.assertRedirects(r, reverse("purchasing:return_detail", args=[back.pk]))
        c.post(reverse("purchasing:return_line_add", args=[back.pk]),
               {"receipt_line": line.pk, "qty": "4", "uom": self.bag.pk})
        c.post(reverse("purchasing:return_action", args=[back.pk, "post"]))
        back.refresh_from_db()
        self.assertEqual(back.status, "posted")
        self.assertEqual(StockBalance.all_objects.get(item=self.sugar).qty_on_hand, D("0"))

    def test_refused_line_comes_back_in_its_modal(self):
        r = self.client.post(reverse("purchasing:orders"), {
            "supplier": self.supplier.pk, "branch": self.branch.pk, "order_date": "2026-10-01"})
        order = PurchaseOrder.all_objects.get()
        page = self.client.post(reverse("purchasing:order_line_add", args=[order.pk]), {
            "item": self.sugar.pk, "qty": "1", "uom": self.pcs.pk, "unit_price": "1",
            "discount_pct": "0", "vat_rate": "0"})
        self.assertContains(page, 'data-open-on-load="line-dialog"')
        self.assertContains(page, "counted in KG or its pack units")

    def test_mail_not_set_up_is_said_plainly(self):
        _, order, _ = self.approved_order()
        page = self.client.post(reverse("purchasing:order_email", args=[order.pk]), {"to": ""})
        self.assertContains(page, "Email is not set up")


class PurchaseGateTests(PurchaseFixture, TestCase):
    def test_without_the_module_nothing_opens(self):
        self.enable_module(False)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("purchasing:orders")).status_code, 404)

    def test_a_requester_cannot_approve(self):
        user = self.member("asker@inv.test", "employee")
        with use_company(self.company):
            employee = Employee.objects.create(company=self.company, user=user, first_name="A")
        grant_access(actor=self.owner, company_id=self.company.pk, employee_id=employee.pk,
                     code="purchase.request", branch_ids=[self.branch.pk])
        self.client.force_login(user)
        page = self.client.get(reverse("purchasing:requisitions"))
        self.assertContains(page, MENU)
        self.assertNotContains(page, "Quality check")
        self.client.post(reverse("purchasing:requisitions"), {"branch": self.branch.pk,
                                                              "request_date": "2026-10-01"})
        requisition = PurchaseRequisition.all_objects.get()
        self.client.post(reverse("purchasing:requisition_line_add", args=[requisition.pk]),
                         {"item": self.bearing.pk, "qty": "2", "uom": self.pcs.pk})
        self.client.post(reverse("purchasing:requisition_submit", args=[requisition.pk]))
        response = self.client.post(reverse("purchasing:requisition_decide", args=[requisition.pk]),
                                    {"decision": "approve"})
        self.assertEqual(response.status_code, 403)
        requisition.refresh_from_db()
        self.assertEqual(requisition.status, "submitted")
