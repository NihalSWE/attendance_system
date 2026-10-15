"""Phase 1 "done when": a company with the module on sets up every master
from the templates, loads opening stock, and stock on hand matches the file
exactly. Plus the opening stock life cycle on its own."""

import datetime

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from common.tenant import use_company
from inventory import opening, posting
from inventory.models import (
    Batch,
    DocumentStatus,
    Item,
    ItemCategory,
    ItemCost,
    ItemUom,
    Location,
    OpeningStock,
    StockBalance,
    StockMove,
    Warehouse,
)
from inventory.testing import DAY, D, InventoryFixture, filled_template
from purchasing.models import Supplier
from sales.models import Customer


class ImportTests(InventoryFixture, TestCase):
    """The example rows of each template, uploaded through the pages."""

    def setUp(self):
        super().setUp()
        # The fixture's own stores and items are not in the templates.
        with use_company(self.company):
            Item.objects.all().delete()
            Warehouse.objects.all().delete()
            ItemCategory.objects.all().delete()
            self.branch.name = "Factory"
            self.branch.save()
        self.client.force_login(self.owner)

    def upload(self, kind, filename, **extra):
        return self.client.post(reverse("inventory:import", args=[kind]),
                                {"upload": filled_template(filename), **extra})

    def confirm(self, kind):
        return self.client.post(reverse("inventory:import_confirm", args=[kind]))

    def test_the_whole_phase_one_flow_from_the_templates(self):
        preview = self.upload("stores", "01-stores.xlsx")
        self.assertContains(preview, "11 to add")
        self.assertEqual(Warehouse.all_objects.count(), 0, "the preview writes nothing")
        self.confirm("stores")
        self.assertEqual(Warehouse.all_objects.filter(company=self.company).count(), 8)
        self.assertEqual(Location.all_objects.filter(company=self.company).count(), 3)

        preview = self.upload("items", "02-items.xlsx")
        self.assertContains(preview, "12 to add")
        self.assertContains(preview, "New categories will be made")
        self.confirm("items")
        with use_company(self.company):
            juice = Item.objects.get(code="FG-0001")
            self.assertEqual((juice.kind, juice.base_uom.code, juice.track_expiry, juice.barcode),
                             ("finished", "PCS", True, "8941234567890"))
            self.assertEqual(ItemUom.objects.get(item=juice).factor, D("24"))
            self.assertEqual(Item.objects.count(), 9)
            self.assertEqual(ItemCategory.objects.get(name="Fruit pulp").kind, "raw")

        self.upload("suppliers", "03-suppliers.xlsx")
        self.confirm("suppliers")
        self.upload("customers", "04-customers.xlsx")
        self.confirm("customers")
        self.assertEqual(Supplier.all_objects.filter(company=self.company).count(), 2)
        self.assertEqual(Customer.all_objects.get(code="CUS-001").credit_limit, D("500000"))

        preview = self.upload("opening", "06-opening-stock.xlsx", work_date="2026-10-31")
        self.assertContains(preview, "5 to add")
        response = self.confirm("opening")
        document = OpeningStock.all_objects.get(company=self.company)
        self.assertRedirects(response, reverse("inventory:opening_detail", args=[document.pk]))
        self.assertEqual(document.status, DocumentStatus.DRAFT)
        self.assertEqual(StockMove.all_objects.count(), 0, "importing does not post")

        self.client.post(reverse("inventory:opening_post", args=[document.pk]))
        document.refresh_from_db()
        self.assertEqual(document.status, DocumentStatus.POSTED)
        with use_company(self.company):
            on_hand = {(b.warehouse.code, b.item.code, b.batch.batch_no if b.batch else ""): b.qty_on_hand
                       for b in StockBalance.objects.select_related("warehouse", "item", "batch")}
            mango = ItemCost.objects.get(item__code="RM-0002")
        # Stock on hand equals the file exactly (packs converted to base units).
        self.assertEqual(on_hand, {
            ("RMS", "RM-0001", "251001-01"): D("1250"),
            ("RMS", "RM-0002", "251003-01"): D("1760"),   # 8 drums x 220 kg
            ("PKS", "PM-0002", "251005-01"): D("85000"),
            ("FGW", "FG-0001", "FG0001-251010-L1"): D("7200"),  # 300 cartons x 24
            ("SPS", "SP-0001", ""): D("7"),
        })
        # 46,200 a drum of 220 kg = 210 a kg.
        self.assertEqual(mango.avg_cost, D("210"))
        self.assertEqual(posting.ledger_differences(self.company.pk), [])
        page = self.client.get(reverse("inventory:stock"))
        self.assertContains(page, "FG0001-251010-L1")

    def test_uploading_again_skips_what_is_here(self):
        self.upload("suppliers", "03-suppliers.xlsx")
        self.confirm("suppliers")
        preview = self.upload("suppliers", "03-suppliers.xlsx")
        self.assertContains(preview, "Already here: skipped.")
        self.assertContains(preview, "Everything in the file is in the software already")

    def test_bad_rows_are_named_and_nothing_is_written(self):
        preview = self.upload("opening", "06-opening-stock.xlsx", work_date="2026-10-31")
        self.assertContains(preview, "Store RMS is not set up.")
        self.assertContains(preview, "Fix the rows with problems")
        self.assertEqual(self.confirm("opening").status_code, 302)
        self.assertFalse(OpeningStock.all_objects.exists())

    def test_the_template_downloads(self):
        response = self.client.get(reverse("inventory:import_template", args=["items"]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("02-items.xlsx", response["Content-Disposition"])


class OpeningStockTests(InventoryFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)
        self.document = opening.create_document(actor=self.owner, company_id=self.company.pk,
                                                values={"work_date": DAY, "note": ""})

    def add(self, **values):
        base = {"warehouse": self.rms, "location": None, "item": self.sugar, "batch_no": "B1",
                "supplier_batch_no": "", "mfg_date": datetime.date(2026, 9, 1),
                "expiry_date": None, "qty": D("100"), "uom": self.kg, "unit_cost": D("120"),
                "note": ""}
        return opening.add_line(actor=self.owner, company_id=self.company.pk,
                                document_id=self.document.pk, values={**base, **values})

    def test_numbered_per_year(self):
        self.assertEqual(self.document.number, "OS-2026-00001")

    def test_add_a_line_by_posting_the_form(self):
        response = self.client.post(reverse("inventory:opening_line_add", args=[self.document.pk]), {
            "warehouse": self.rms.pk, "item": self.bearing.pk, "qty": "7", "uom": self.pcs.pk,
            "unit_cost": "450"})
        self.assertRedirects(response, reverse("inventory:opening_detail", args=[self.document.pk]))
        with use_company(self.company):
            self.assertEqual(self.document.lines.get().qty, D("7"))

    def test_expiry_from_shelf_life_and_rules_per_item(self):
        line = self.add()
        self.assertEqual(line.expiry_date, datetime.date(2027, 9, 1))
        with self.assertRaisesMessage(ValidationError, "keeps batches"):
            self.add(batch_no="")
        with self.assertRaisesMessage(ValidationError, "counted in whole PCS"):
            self.add(item=self.bearing, batch_no="", uom=self.pcs, qty=D("1.5"), mfg_date=None)

    def test_post_then_reverse(self):
        self.add()
        self.client.post(reverse("inventory:opening_post", args=[self.document.pk]))
        self.document.refresh_from_db()
        self.assertEqual(self.document.status, DocumentStatus.POSTED)
        batch = Batch.all_objects.get(item=self.sugar, batch_no="B1")
        self.assertEqual((batch.source, batch.expiry_date), ("opening", datetime.date(2027, 9, 1)))
        with self.assertRaisesMessage(ValidationError, "only a draft can change"):
            self.add(batch_no="B2")
        self.client.post(reverse("inventory:opening_reverse", args=[self.document.pk]),
                         {"reason": "Counted twice"})
        self.document.refresh_from_db()
        self.assertEqual(self.document.status, DocumentStatus.REVERSED)
        self.assertEqual(StockBalance.all_objects.get(item=self.sugar).qty_on_hand, D("0"))
        self.assertEqual(posting.ledger_differences(self.company.pk), [])

    def test_posting_needs_approval_permission(self):
        from access_control.branch_access import grant_access
        from employees.models import Employee

        self.add()
        user = self.member("clerk@inv.test", "employee")
        with use_company(self.company):
            employee = Employee.objects.create(company=self.company, user=user, first_name="C")
        for code in ("inventory.view", "inventory.adjust"):
            grant_access(actor=self.owner, company_id=self.company.pk, employee_id=employee.pk,
                         code=code, branch_ids=[self.branch.pk])
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("inventory:opening_detail",
                                                 args=[self.document.pk])).status_code, 200)
        response = self.client.post(reverse("inventory:opening_post", args=[self.document.pk]))
        self.assertEqual(response.status_code, 403)
        self.document.refresh_from_db()
        self.assertEqual(self.document.status, DocumentStatus.DRAFT)

    def test_cancel_a_draft(self):
        self.client.post(reverse("inventory:opening_cancel", args=[self.document.pk]))
        self.document.refresh_from_db()
        self.assertEqual(self.document.status, DocumentStatus.CANCELLED)

    def test_check_stock_command(self):
        from io import StringIO

        from django.core.management import call_command

        self.add()
        opening.post_document(actor=self.owner, company_id=self.company.pk,
                              document_id=self.document.pk)
        out = StringIO()
        call_command("check_stock", stdout=out)
        self.assertIn("no differences", out.getvalue())
