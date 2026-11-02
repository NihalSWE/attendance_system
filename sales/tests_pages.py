"""Sales pages: every form POSTed through order -> delivery -> invoice -> return."""

import datetime

from django.test import TestCase
from django.urls import reverse

from access_control.branch_access import grant_access
from base_template.testing import assert_inventory_menu
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
        assert_inventory_menu(self, page, MENU, menu="sales")

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
        challan = c.get(reverse("sales:delivery_pdf", args=[delivery.pk]))
        self.assertEqual(challan["Content-Type"], "application/pdf")
        from common.tests_exports import pdf_text

        text = pdf_text(challan.content)
        for words in ("DELIVERY CHALLAN", delivery.number, "DELIVER TO", "Total quantity", "Received by"):
            self.assertIn(words, text)
        c.post(reverse("sales:delivery_action", args=[delivery.pk, "post"]))
        self.assertEqual(SalesOrder.all_objects.get().status, "delivered")
        r = c.post(reverse("sales:delivery_action", args=[delivery.pk, "invoice"]))
        invoice = SalesInvoice.all_objects.get()
        self.assertRedirects(r, reverse("sales:invoice_detail", args=[invoice.pk]))
        self.assertEqual(invoice.total, D("3036"))          # 120 x 22 = 2,640 + 15 % VAT
        c.post(reverse("sales:invoice_action", args=[invoice.pk, "issue"]))
        bill = c.get(reverse("sales:invoice_pdf", args=[invoice.pk]))
        self.assertEqual(bill["Content-Type"], "application/pdf")
        text = pdf_text(bill.content)
        for words in ("TAX INVOICE", invoice.number, "BILL TO", "SHIP TO", "AMOUNT IN WORDS", "Taka Three Thousand",
                      "3,036.00", "Prepared by"):
            self.assertIn(words, text)
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

    def test_edit_and_delete_a_price_list(self):
        # 2026-11-02: Edit and Delete on the price list, Edit on each price.
        from sales.models import Customer, PriceListLine

        distributor = PriceList.all_objects.get(name="Distributor")
        page = self.client.get(reverse("sales:price_lists"))
        self.assertContains(page, f"?edit={distributor.pk}")
        self.assertContains(page, reverse("sales:price_list_delete", args=[distributor.pk]))
        page = self.client.get(reverse("sales:price_lists"), {"edit": distributor.pk})
        self.assertContains(page, 'data-open-on-load="edit-dialog"')
        # Renaming keeps its customers: their record moves to the new name.
        response = self.client.post(reverse("sales:price_list_edit", args=[distributor.pk]), {
            "name": "Distributor 2027", "valid_from": "", "valid_to": "", "note": ""}, follow=True)
        self.assertContains(response, "1 customer moved to the new name.")
        self.assertEqual(Customer.all_objects.get(pk=self.customer.pk).price_list_name, "Distributor 2027")
        # From the list's own page it goes back there; a bad date comes back in its modal.
        response = self.client.post(reverse("sales:price_list_edit", args=[distributor.pk]), {
            "name": "Distributor 2027", "valid_from": "2026-12-01", "valid_to": "2026-11-01", "note": "",
            "next": "detail"})
        self.assertContains(response, 'data-open-on-load="list-dialog"')
        self.assertContains(response, "Ends before it starts?")

        # Edit one price: the modal opens filled in, and saving changes it.
        line = PriceListLine.all_objects.get(price_list=distributor, min_qty=0)
        page = self.client.get(reverse("sales:price_list_detail", args=[distributor.pk]), {"edit_line": line.pk})
        self.assertContains(page, 'data-open-on-load="line-dialog"')
        self.assertContains(page, f"Edit the price of {self.juice.code}")
        self.client.post(reverse("sales:price_edit", args=[distributor.pk, line.pk]), {
            "item": self.juice.pk, "uom": self.pcs.pk, "price": "21.50", "min_qty": "0"})
        self.assertEqual(PriceListLine.all_objects.get(pk=line.pk).price, D("21.50"))
        # Changing it onto a price the list already has is refused (from 500 to 0).
        other = PriceListLine.all_objects.get(price_list=distributor, min_qty=500)
        refused = self.client.post(reverse("sales:price_edit", args=[distributor.pk, other.pk]), {
            "item": self.juice.pk, "uom": self.pcs.pk, "price": "19", "min_qty": "0"})
        self.assertContains(refused, "already has a price for that item, unit and from quantity")
        self.assertEqual(PriceListLine.all_objects.get(pk=other.pk).price, D("21"))

        # Delete: its prices go, and its customers fall back to each item's sale price.
        response = self.client.post(reverse("sales:price_list_delete", args=[distributor.pk]), follow=True)
        self.assertContains(response, "deleted with its 2 prices. 1 customer now get each item&#x27;s own sale price.")
        self.assertFalse(PriceList.all_objects.filter(pk=distributor.pk).exists())
        self.assertFalse(PriceListLine.all_objects.filter(price_list_id=distributor.pk).exists())
        self.assertEqual(Customer.all_objects.get(pk=self.customer.pk).price_list_name, "")

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
    def test_a_deliverer_cannot_change_price_lists(self):
        user = self.member("seller@inv.test", "employee")
        with use_company(self.company):
            employee = Employee.objects.create(company=self.company, user=user, first_name="S")
        grant_access(actor=self.owner, company_id=self.company.pk, employee_id=employee.pk,
                     code="sales.deliver", branch_ids=[self.branch.pk])
        self.client.force_login(user)
        distributor = PriceList.all_objects.get(name="Distributor")
        page = self.client.get(reverse("sales:price_lists"))
        self.assertNotContains(page, reverse("sales:price_list_delete", args=[distributor.pk]))
        self.assertEqual(self.client.post(reverse("sales:price_list_delete", args=[distributor.pk])).status_code, 403)
        self.assertTrue(PriceList.all_objects.filter(pk=distributor.pk).exists())

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
        assert_inventory_menu(self, page, MENU, menu="sales")
        self.assertEqual(self.client.post(reverse("sales:order_action", args=[order.pk, "confirm"])).status_code, 403)
        self.assertEqual(StockBalance.all_objects.get(batch=self.j1).qty_reserved, D("0"))



class AmountInWordsTests(TestCase):
    def test_lakh_crore_and_paisa(self):
        from sales.invoice_pdf import in_words

        self.assertEqual(in_words(D("3036")), "Taka Three Thousand Thirty-Six Only")
        self.assertEqual(in_words(D("12500000.50")), "Taka One Crore Twenty-Five Lakh and Paisa Fifty Only")
        self.assertEqual(in_words(D("0")), "Taka Zero Only")
