"""Production pages: every form POSTed through a whole run, and the gates."""

from django.test import TestCase
from django.urls import reverse

from access_control.branch_access import grant_access
from common.tenant import use_company
from employees.models import Employee
from inventory.models import BarcodeLabel, StockBalance
from inventory.testing import D
from production.models import Bom, Consumption, ProductionOrder
from production.tests_flow import ProductionFixture

MENU = 'data-menu="production"'


class ProductionPageTests(ProductionFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def test_lists_open_and_the_menu_shows(self):
        for name in ("boms", "orders", "report"):
            page = self.client.get(reverse(f"production:{name}"))
            self.assertEqual(page.status_code, 200, name)
        self.assertContains(page, MENU)

    def test_a_whole_run_through_the_pages(self):
        c = self.client
        c.post(reverse("production:boms"), {"item": self.juice.pk, "batch_size": "1000", "batch_uom": self.pcs.pk})
        bom = Bom.all_objects.get()
        for item, qty, uom, scrap, packaging in ((self.pulp, "45", self.kg, "", False), (self.sugar, "22", self.kg, "", False),
                                                 (self.bottle, "1000", self.pcs, "1.5", True),
                                                 (self.cap, "1000", self.pcs, "1", True)):
            data = {"item": item.pk, "qty": qty, "uom": uom.pk, "scrap_pct": scrap}
            if packaging:
                data["is_packaging"] = "on"
            c.post(reverse("production:bom_line_add", args=[bom.pk]), data)
        c.post(reverse("production:bom_action", args=[bom.pk, "activate"]))
        self.assertEqual(Bom.all_objects.get().status, "active")

        c.post(reverse("production:orders"), {
            "item": self.juice.pk, "planned_qty": "1000", "uom": self.pcs.pk, "planned_date": "2026-10-01",
            "material_warehouse": self.rms.pk, "output_warehouse": self.fgw.pk, "line_name": "L1"})
        order = ProductionOrder.all_objects.get()
        c.post(reverse("production:order_action", args=[order.pk, "release"]))
        r = c.post(reverse("production:order_action", args=[order.pk, "use"]))
        use = Consumption.all_objects.get()
        self.assertRedirects(r, reverse("production:consumption_detail", args=[use.pk]))
        self.assertContains(c.get(reverse("production:consumption_detail", args=[use.pk])), "Scan a package label")
        c.post(reverse("production:consumption_action", args=[use.pk, "post"]))
        self.assertEqual(Consumption.all_objects.get().status, "posted")

        page = c.post(reverse("production:order_output", args=[order.pk]), {
            "output_date": "2026-10-01", "qty_good": "990", "qty_rejected": "10", "uom": self.pcs.pk,
            "packages": "33"}, follow=True)
        self.assertContains(page, "is in stock")
        order.refresh_from_db()
        self.assertEqual((order.status, order.unit_cost), ("completed", D("16.5222")))
        self.assertEqual(StockBalance.all_objects.get(item=self.juice).qty_on_hand, D("990"))
        pdf = c.post(reverse("production:order_labels", args=[order.pk]), {"copies": "1"})
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertEqual(BarcodeLabel.all_objects.filter(kind="package", printed_count=1).count(), 33)
        report = c.get(reverse("production:report"))
        self.assertContains(report, order.number)
        self.assertContains(report, "16.5222")
        c.post(reverse("production:order_action", args=[order.pk, "close"]))
        self.assertEqual(ProductionOrder.all_objects.get().status, "closed")

    def test_a_refused_output_comes_back_in_its_modal(self):
        order = self.released()
        page = self.client.post(reverse("production:order_output", args=[order.pk]), {
            "output_date": "2026-10-01", "qty_good": "10", "uom": self.pcs.pk})
        self.assertContains(page, 'data-open-on-load="output-dialog"')
        self.assertContains(page, "No materials were used")


class ProductionGateTests(ProductionFixture, TestCase):
    def test_without_the_module_nothing_opens(self):
        self.enable_module(False)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("production:orders")).status_code, 404)

    def test_a_viewer_sees_but_does_not_run(self):
        order = self.released()
        user = self.member("viewer@inv.test", "employee")
        with use_company(self.company):
            employee = Employee.objects.create(company=self.company, user=user, first_name="V")
        grant_access(actor=self.owner, company_id=self.company.pk, employee_id=employee.pk,
                     code="inventory.view", branch_ids=[self.branch.pk])
        self.client.force_login(user)
        page = self.client.get(reverse("production:order_detail", args=[order.pk]))
        self.assertContains(page, MENU)
        self.assertNotContains(page, "Use planned materials")
        self.assertNotContains(page, "16.5")
        self.assertEqual(self.client.post(reverse("production:order_action", args=[order.pk, "use"])).status_code, 403)
