"""Sales pages: every form POSTed through order -> delivery -> invoice -> return."""

import datetime

from django.test import TestCase
from django.urls import reverse

from access_control.branch_access import grant_access
from common.tenant import use_company
from employees.models import Employee
from inventory import labels
from inventory.models import StockBalance
from inventory.testing import D
from sales.models import Delivery, PriceList, SalesInvoice, SalesOrder, SalesReturn
from sales.tests_flow import SalesFixture

MENU = 'data-menu="sales"'


class SalesPageTests(SalesFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def test_lists_open_and_the_menu_shows(self):
        for name in ("orders", "deliveries", "invoices", "returns", "price_lists"):
            page = self.client.get(reverse(f"sales:{name}"))
            self.assertEqual(page.status_code, 200, name)
        self.assertContains(page, MENU)

    def test_order_to_return_through_the_pages(self):
        c = self.client
        c.post(reverse("sales:orders"), {"customer": self.customer.pk, "warehouse": self.fgw.pk,
                                         "order_date": "2026-10-01", "source": "manual"})
        order = SalesOrder.all_objects.get()
        c.post(reverse("sales:order_line_add", args=[order.pk]), {"item": self.juice.pk, "qty": "120",
                                                                   "uom": self.pcs.pk})
        page = c.get(reverse("sales:order_detail", args=[order.pk]))
        self.assertContains(page, "Available now")
        c.post(reverse("sales:order_action", args=[order.pk, "confirm"]))
        self.assertEqual(SalesOrder.all_objects.get().status, "confirmed")
        r = c.post(reverse("sales:order_action", args=[order.pk, "deliver"]))
        delivery = Delivery.all_objects.get()
        self.assertRedirects(r, reverse("sales:delivery_detail", args=[delivery.pk]))
        self.assertContains(c.get(reverse("sales:delivery_detail", args=[delivery.pk])), "Scan a carton label")
        self.assertEqual(c.get(reverse("sales:delivery_pdf", args=[delivery.pk]))["Content-Type"], "application/pdf")
        c.post(reverse("sales:delivery_action", args=[delivery.pk, "post"]))
        self.assertEqual(SalesOrder.all_objects.get().status, "delivered")
        r = c.post(reverse("sales:delivery_action", args=[delivery.pk, "invoice"]))
        invoice = SalesInvoice.all_objects.get()
        self.assertRedirects(r, reverse("sales:invoice_detail", args=[invoice.pk]))
        self.assertEqual(invoice.total, D("3036"))          # 120 x 22 = 2,640 + 15 % VAT
        c.post(reverse("sales:invoice_action", args=[invoice.pk, "issue"]))
        self.assertEqual(c.get(reverse("sales:invoice_pdf", args=[invoice.pk]))["Content-Type"], "application/pdf")
        c.post(reverse("sales:invoice_action", args=[invoice.pk, "paid"]), {"paid_on": "2026-10-20", "note": "Cheque 991"})
        self.assertEqual(SalesInvoice.all_objects.get().status, "paid")

        c.post(reverse("sales:returns"), {"delivery": delivery.pk, "return_date": "2026-10-21", "reason": "Leaking"})
        back = SalesReturn.all_objects.get()
        with use_company(self.company):
            delivered = delivery.lines.order_by("pk").first()
        c.post(reverse("sales:return_line_add", args=[back.pk]), {
            "delivery_line": delivered.pk, "condition": "good", "qty": "4", "uom": self.pcs.pk})
        c.post(reverse("sales:return_action", args=[back.pk, "post"]))
        self.assertEqual(SalesReturn.all_objects.get().status, "posted")

    def test_scanning_into_a_delivery_and_price_lists(self):
        order = self.order(qty="100")
        from sales import orders

        orders.confirm(**self.kw, order_id=order.pk)
        [carton] = labels.package_labels(**self.kw, item_id=self.juice.pk, batch_id=self.j1.pk, uom_id=self.pcs.pk,
                                         quantity=D("50"), count=1, warehouse_id=self.fgw.pk)
        self.client.post(reverse("sales:deliveries"), {"order": order.pk, "delivery_date": "2026-10-01"})
        delivery = Delivery.all_objects.get()
        with use_company(self.company):
            delivery.lines.all().delete()
        page = self.client.post(reverse("sales:delivery_scan", args=[delivery.pk]), {"code": carton.code}, follow=True)
        self.assertContains(page, f"{carton.code}: 50 PCS FG-0001 batch J1 added.")
        self.client.post(reverse("sales:price_lists"), {"name": "Retailer"})
        retailer = PriceList.all_objects.get(name="Retailer")
        self.client.post(reverse("sales:price_list_detail", args=[retailer.pk]),
                         {"item": self.juice.pk, "uom": self.pcs.pk, "price": "24"})
        self.assertContains(self.client.get(reverse("sales:price_list_detail", args=[retailer.pk])), "24.00")

    def test_a_mixed_store_order_through_the_pages(self):
        from inventory.models import Batch, Item, MoveType
        from inventory import posting

        Item.all_objects.filter(pk=self.sugar.pk).update(sale_price=D("95"))
        s1 = Batch.all_objects.create(company=self.company, item=self.sugar, batch_no="S1", source="opening")
        posting.post(company_id=self.company.pk, actor=self.owner, document_type="opening", document_id=2,
                     move_type=MoveType.OPENING, work_date=datetime.date(2026, 9, 1), lines=[
                         posting.Line(item=self.sugar, warehouse=self.rms, batch=s1, qty=D("50"), unit_cost=D("80"))])
        order = self.order(qty="10")
        self.client.post(reverse("sales:order_line_add", args=[order.pk]), {
            "item": self.sugar.pk, "warehouse": self.rms.pk, "qty": "20", "uom": self.kg.pk})
        page = self.client.get(reverse("sales:order_detail", args=[order.pk]))
        self.assertContains(page, "<th>From</th>", html=False)
        self.client.post(reverse("sales:order_action", args=[order.pk, "confirm"]))
        page = self.client.post(reverse("sales:order_action", args=[order.pk, "deliver"]), follow=True)
        made = list(Delivery.all_objects.order_by("pk"))
        self.assertEqual([d.warehouse.code for d in made], ["FGW", "RMS"])
        self.assertContains(page, f"{made[0].number} (FGW), {made[1].number} (RMS) drafted")
        self.assertContains(self.client.get(reverse("sales:delivery_detail", args=[made[1].pk])), "from RMS")

    def test_over_the_credit_limit_asks_why(self):
        self.customer.credit_limit = D("1000")
        self.customer.save()
        order = self.order(qty="100")
        page = self.client.get(reverse("sales:order_detail", args=[order.pk]))
        self.assertContains(page, "over the credit limit")
        page = self.client.post(reverse("sales:order_action", args=[order.pk, "confirm"]), follow=True)
        self.assertContains(page, "Say why it is confirmed anyway")
        self.client.post(reverse("sales:order_action", args=[order.pk, "confirm"]), {"note": "Owner approved"})
        self.assertEqual(SalesOrder.all_objects.get().status, "confirmed")


class SalesGateTests(SalesFixture, TestCase):
    def test_without_the_module_nothing_opens(self):
        self.enable_module(False)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("sales:orders")).status_code, 404)

    def test_a_deliverer_cannot_confirm(self):
        order = self.order()
        user = self.member("driver@inv.test", "employee")
        with use_company(self.company):
            employee = Employee.objects.create(company=self.company, user=user, first_name="D")
        grant_access(actor=self.owner, company_id=self.company.pk, employee_id=employee.pk,
                     code="sales.deliver", branch_ids=[self.branch.pk])
        self.client.force_login(user)
        page = self.client.get(reverse("sales:order_detail", args=[order.pk]))
        self.assertContains(page, MENU)
        self.assertEqual(self.client.post(reverse("sales:order_action", args=[order.pk, "confirm"])).status_code, 403)
        self.assertEqual(StockBalance.all_objects.get(batch=self.j1).qty_reserved, D("0"))
