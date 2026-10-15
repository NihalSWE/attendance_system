"""Store operation pages: every list opens, every form is POSTed through the
whole life of each document, and the scan box adds lines."""

import datetime

from django.test import TestCase
from django.urls import reverse

from common.tenant import use_company
from inventory import labels
from inventory.models import (
    MaterialReturn,
    StockAdjustment,
    StockBalance,
    StockCount,
    StockTransfer,
    StoreIssue,
    StoreRequisition,
)
from inventory.testing import D
from inventory.tests_store import StoreFixture

KEYS = ("requisitions", "issues", "returns", "transfers", "adjustments", "counts")


class StorePageTests(StoreFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def url(self, name, *args):
        return reverse(f"inventory:{name}", args=args)

    def test_every_list_opens_and_the_menu_marks_it(self):
        for key in KEYS:
            page = self.client.get(self.url("store_list", key))
            self.assertEqual(page.status_code, 200, key)
            self.assertContains(page, f'href="{self.url(f"store_{key}")}"')

    def test_requisition_issue_and_return_through_the_pages(self):
        c = self.client
        c.post(self.url("store_list", "requisitions"), {
            "department": self.production.pk, "warehouse": self.rms.pk, "request_date": "2026-10-01"})
        requisition = StoreRequisition.all_objects.get()
        c.post(self.url("store_line_add", "requisitions", requisition.pk),
               {"item": self.sugar.pk, "qty": "100", "uom": self.kg.pk})
        c.post(self.url("store_action", "requisitions", requisition.pk, "submit"))
        page = c.get(self.url("store_detail", "requisitions", requisition.pk))
        self.assertContains(page, "Approve")
        c.post(self.url("store_action", "requisitions", requisition.pk, "approve"))
        self.assertEqual(StoreRequisition.all_objects.get().status, "approved")

        c.post(self.url("store_list", "issues"), {"requisition": requisition.pk, "issue_date": "2026-10-01"})
        issue = StoreIssue.all_objects.get()
        page = c.get(self.url("store_detail", "issues", issue.pk))
        self.assertContains(page, "Scan a package label")
        c.post(self.url("store_action", "issues", issue.pk, "post"))
        issue.refresh_from_db()
        self.assertEqual(issue.status, "posted")
        self.assertEqual(StoreRequisition.all_objects.get().status, "issued")
        self.assertEqual(StockBalance.all_objects.get(batch=self.a).qty_on_hand, D("0"))

        c.post(self.url("store_list", "returns"), {"issue": issue.pk, "return_date": "2026-10-02"})
        back = MaterialReturn.all_objects.get()
        with use_company(self.company):
            issue_line = issue.lines.get(batch=self.a)
        c.post(self.url("store_line_add", "returns", back.pk), {
            "issue_line": issue_line.pk, "condition": "good", "qty": "10", "uom": self.kg.pk})
        c.post(self.url("store_action", "returns", back.pk, "post"))
        self.assertEqual(StockBalance.all_objects.get(batch=self.a, warehouse=self.rms).qty_on_hand, D("10"))
        page = c.post(self.url("store_action", "returns", back.pk, "reverse"), {"reason": "Counted twice"},
                      follow=True)
        self.assertContains(page, "reversed")

    def test_scanning_into_an_issue(self):
        [package] = labels.package_labels(actor=self.owner, company_id=self.company.pk, item_id=self.sugar.pk,
                                          batch_id=self.b.pk, uom_id=self.kg.pk, quantity=D("25"), count=1,
                                          warehouse_id=self.rms.pk)
        self.client.post(self.url("store_list", "issues"), {
            "warehouse": self.rms.pk, "department": self.production.pk, "issue_date": "2026-10-01"})
        issue = StoreIssue.all_objects.get()
        page = self.client.post(self.url("store_scan", "issues", issue.pk), {"code": package.code}, follow=True)
        self.assertContains(page, f"{package.code}: 25 KG RM-0001 batch B added.")
        page = self.client.post(self.url("store_scan", "issues", issue.pk), {"code": "NOPE"}, follow=True)
        self.assertContains(page, "is not a label, item or barcode")

    def test_transfer_receive_with_shortage_through_the_pages(self):
        c = self.client
        c.post(self.url("store_list", "transfers"), {
            "from_warehouse": self.rms.pk, "to_warehouse": self.fgw.pk, "transfer_date": "2026-10-01"})
        transfer = StockTransfer.all_objects.get()
        c.post(self.url("store_line_add", "transfers", transfer.pk),
               {"item": self.sugar.pk, "batch": self.b.pk, "qty": "20", "uom": self.kg.pk})
        c.post(self.url("store_action", "transfers", transfer.pk, "dispatch"))
        with use_company(self.company):
            line = transfer.lines.get()
        page = c.get(self.url("store_detail", "transfers", transfer.pk))
        self.assertContains(page, "receive-dialog")
        c.post(self.url("transfer_receive", transfer.pk), {
            "received_date": "2026-10-02", f"got_{line.pk}": "19", f"why_{line.pk}": "Spilled"})
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, "received")
        self.assertEqual(StockBalance.all_objects.get(batch=self.b, warehouse=self.fgw).qty_on_hand, D("19"))

    def test_adjustment_and_count_through_the_pages(self):
        c = self.client
        c.post(self.url("store_list", "adjustments"), {
            "warehouse": self.rms.pk, "adjustment_date": "2026-10-01", "reason": "expiry"})
        adjustment = StockAdjustment.all_objects.get()
        c.post(self.url("store_line_add", "adjustments", adjustment.pk), {
            "item": self.sugar.pk, "batch": self.old.pk, "qty_change": "-5", "uom": self.kg.pk})
        c.post(self.url("store_action", "adjustments", adjustment.pk, "submit"))
        c.post(self.url("store_action", "adjustments", adjustment.pk, "approve"))
        self.assertEqual(StockAdjustment.all_objects.get().status, "posted")
        self.assertEqual(StockBalance.all_objects.get(batch=self.old).qty_on_hand, D("0"))

        c.post(self.url("store_list", "counts"), {"warehouse": self.rms.pk, "count_date": "2026-10-02"})
        count = StockCount.all_objects.get()
        page = c.get(self.url("store_detail", "counts", count.pk))
        self.assertContains(page, "hidden")
        with use_company(self.company):
            lines = {line.batch.batch_no: line.pk for line in count.lines.select_related("batch")}
        c.post(self.url("count_save", count.pk), {f"counted_{lines['A']}": "81", f"counted_{lines['B']}": "100"})
        c.post(self.url("store_action", "counts", count.pk, "submit"))
        c.post(self.url("store_action", "counts", count.pk, "approve"))
        count.refresh_from_db()
        self.assertEqual(count.status, "approved")
        self.assertEqual(StockBalance.all_objects.get(batch=self.a).qty_on_hand, D("81"))
        page = c.get(self.url("store_detail", "counts", count.pk))
        self.assertContains(page, count.adjustment.number)

    def test_a_viewer_cannot_post(self):
        user = self.member("viewer@inv.test", "employee")
        from access_control.branch_access import grant_access
        from employees.models import Employee

        with use_company(self.company):
            employee = Employee.objects.create(company=self.company, user=user, first_name="V")
        grant_access(actor=self.owner, company_id=self.company.pk, employee_id=employee.pk,
                     code="inventory.view", branch_ids=[self.branch.pk])
        self.client.force_login(user)
        page = self.client.get(self.url("store_list", "issues"))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "New store issue")
        self.assertEqual(self.client.post(self.url("store_list", "issues"), {}).status_code, 403)
