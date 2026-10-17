"""Inventory Phase 9: the Store & Inventory API (docs/api/130-inventory.md).

Each flow goes through the API only, as an app or the ERP would: masters,
opening stock, stock and labels, a store issue, purchase to GRN, production,
sales from order to paid invoice, the order intake, reports and month-end -
and the security checks every area has (no login, no permission, another
company's records invisible, the module off, API key scopes).
"""

import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model

from api.tests.test_company import CompanyApiTestCase
from common.tenant import use_company
from inventory import labels, masters
from inventory.models import Item, ItemKind, StockBalance, Uom, Warehouse
from organization.catalogue import adopt_department
from purchasing.services import SUPPLIER
from sales.services import CUSTOMER
from tenants import platform_services
from tenants.models import Feature

D = Decimal


class InventoryApiTestCase(CompanyApiTestCase):
    def setUp(self):
        super().setUp()
        self.root = get_user_model().objects.create_superuser(email="root@acme.test", password="pw-Root-12345")
        self.enable(True)
        kw = {"actor": self.admin_user, "company_id": self.company.pk}
        masters.add_standard_units(**kw)
        with use_company(self.company):
            self.kg, self.pcs = Uom.objects.get(code="KG"), Uom.objects.get(code="PCS")
            self.department = adopt_department(self.hq, "PRD", "Production")
        self.raw = masters.create(masters.CATEGORY, **kw, values={"code": "SWT", "name": "Sweetener",
                                                                  "kind": ItemKind.RAW, "parent": None})
        self.juices = masters.create(masters.CATEGORY, **kw, values={"code": "JCE", "name": "Juice",
                                                                     "kind": ItemKind.FINISHED, "parent": None})
        self.rms = masters.create(masters.WAREHOUSE, **kw, values={"branch": self.hq, "code": "RMS", "name": "Raw",
                                                                   "kind": Warehouse.Kind.RAW, "notes": ""})
        self.fgw = masters.create(masters.WAREHOUSE, **kw, values={"branch": self.hq, "code": "FGW",
                                                                   "name": "Finished", "kind": Warehouse.Kind.FINISHED,
                                                                   "notes": ""})
        self.sugar = masters.create(masters.ITEM, **kw, values={
            "code": "RM-0001", "name": "Sugar", "kind": ItemKind.RAW, "category": self.raw, "base_uom": self.kg,
            "track_batch": True, "track_expiry": True, "shelf_life_days": 365, "default_warehouse": self.rms,
            "sale_price": D("95")})
        self.juice = masters.create(masters.ITEM, **kw, values={
            "code": "FG-0001", "name": "Mango juice", "kind": ItemKind.FINISHED, "category": self.juices,
            "base_uom": self.pcs, "track_batch": True, "track_expiry": True, "shelf_life_days": 270,
            "default_warehouse": self.fgw, "sale_price": D("25"), "vat_rate": D("15")})
        self.customer = masters.create(CUSTOMER, **kw, values={"code": "CUS-1", "name": "Sylhet Distribution",
                                                               "customer_type": "distributor",
                                                               "payment_terms_days": 30})
        self.supplier = masters.create(SUPPLIER, **kw, values={"code": "SUP-1", "name": "Sugar Mills",
                                                               "payment_terms_days": 30})

    def enable(self, on):
        feature = Feature.objects.get(code="inventory")
        platform_services.set_company_feature(actor=self.root, company_id=self.company.pk, feature_id=feature.pk,
                                              effect="enable" if on else "disable", reason="API tests")

    def ok(self, response, status=200):
        self.assertEqual(response.status_code, status, response.content)
        return response.json()

    def opening(self):
        """500 kg sugar (batch S1) in RMS and 300 juice (batch J1) in FGW, through the API."""
        doc = self.ok(self.api("POST", "/api/v1/inventory/opening", {"work_date": "2026-09-01"}), 201)
        path = f"/api/v1/inventory/opening/{doc['id']}"
        self.ok(self.api("POST", f"{path}/lines", {
            "warehouse_id": self.rms.pk, "item_id": self.sugar.pk, "batch_no": "S1", "expiry_date": "2027-09-30",
            "qty": "500", "uom_id": self.kg.pk, "unit_cost": "80"}))
        self.ok(self.api("POST", f"{path}/lines", {
            "warehouse_id": self.fgw.pk, "item_id": self.juice.pk, "batch_no": "J1", "expiry_date": "2027-06-30",
            "qty": "300", "uom_id": self.pcs.pk, "unit_cost": "16"}))
        return self.ok(self.api("POST", f"{path}/post"))

    def held(self, item, store):
        return sum((b.qty_on_hand for b in StockBalance.all_objects.filter(item=item, warehouse=store)), D("0"))


class MasterTests(InventoryApiTestCase):
    def test_items_added_changed_listed_and_turned_off(self):
        made = self.ok(self.api("POST", "/api/v1/inventory/items", {
            "code": "rm-0002", "name": "Citric acid", "kind": "raw", "category_id": self.raw.pk,
            "base_uom_id": self.kg.pk, "reorder_point": "50"}), 201)
        self.assertEqual((made["code"], made["category"]["code"], made["base_uom"]["code"]), ("RM-0002", "SWT", "KG"))
        changed = self.ok(self.api("PATCH", f"/api/v1/inventory/items/{made['id']}", {"name": "Citric acid (food)"}))
        self.assertEqual((changed["name"], changed["reorder_point"]), ("Citric acid (food)", "50"))
        twice = self.api("POST", "/api/v1/inventory/items", {"code": "RM-0002", "name": "Again", "kind": "raw",
                                                             "category_id": self.raw.pk, "base_uom_id": self.kg.pk})
        self.assertEqual(twice.status_code, 422)
        off = self.ok(self.api("POST", f"/api/v1/inventory/items/{made['id']}/status", {"status": "inactive"}))
        self.assertEqual(off["status"], "inactive")
        listed = self.ok(self.api("GET", "/api/v1/inventory/items", query="search=citric"))
        self.assertEqual(listed["count"], 1)
        for path in ("units", "categories", "stores", "bins", "suppliers", "customers"):
            self.ok(self.api("GET", f"/api/v1/inventory/{path}"))

    def test_another_companys_records_are_invisible(self):
        with use_company(self.other):
            other_unit = Uom.objects.create(company=self.other, code="KG", name="Kilogram")
        refused = self.api("POST", "/api/v1/inventory/items", {"code": "X-1", "name": "X", "kind": "raw",
                                                               "category_id": self.raw.pk, "base_uom_id": other_unit.pk})
        self.assertEqual(refused.status_code, 422)
        self.assertIn("base_uom_id", refused.json()["error"]["fields"])
        with use_company(self.other):
            other_item = Item.objects.filter().first()
        self.assertEqual(self.api("GET", "/api/v1/inventory/items/999999").status_code, 404)
        self.assertIsNone(other_item)


class StockFlowTests(InventoryApiTestCase):
    def test_opening_stock_issue_ledger_batches_and_lookup(self):
        posted = self.opening()
        self.assertEqual((posted["status"], posted["kind"], len(posted["lines"])), ("posted", "opening-stock", 2))
        stock = self.ok(self.api("GET", "/api/v1/inventory/stock", query=f"item_id={self.sugar.pk}"))
        [row] = stock["results"]
        self.assertEqual((row["qty_on_hand"], row["batch"]["code"], row["value"]), ("500", "S1", "40000"))

        issue = self.ok(self.api("POST", "/api/v1/inventory/issues", {
            "warehouse_id": self.rms.pk, "department_id": self.department.pk, "issue_date": "2026-10-01"}), 201)
        self.assertEqual((issue["status"], issue["actions"]), ("draft", ["post", "cancel"]))
        path = f"/api/v1/inventory/issues/{issue['id']}"
        with_line = self.ok(self.api("POST", f"{path}/lines", {"item_id": self.sugar.pk, "qty": "120",
                                                               "uom_id": self.kg.pk}))
        self.assertEqual([(l["batch"]["code"], l["qty"]) for l in with_line["lines"]], [("S1", "120")])
        done = self.ok(self.api("POST", f"{path}/post"))
        self.assertEqual((done["status"], done["actions"]), ("posted", ["reverse"]))
        self.assertEqual(self.held(self.sugar, self.rms), D("380"))
        no_reason = self.api("POST", f"{path}/reverse", {})
        self.assertEqual(no_reason.status_code, 422)
        self.ok(self.api("POST", f"{path}/reverse", {"reason": "Wrong department"}))
        self.assertEqual(self.held(self.sugar, self.rms), D("500"))

        ledger = self.ok(self.api("GET", f"/api/v1/inventory/items/{self.sugar.pk}/ledger",
                                  query="date_from=2026-09-01&date_to=2026-12-31"))
        self.assertEqual([m["kind"] for m in ledger["results"]], ["opening", "issue", "reversal"])
        batches = self.ok(self.api("GET", "/api/v1/inventory/batches", query=f"item_id={self.juice.pk}"))
        self.assertEqual(batches["results"][0]["qty_on_hand"], "300")
        with use_company(self.company):
            batch = self.juice.batches.get()
        [carton] = labels.package_labels(actor=self.admin_user, company_id=self.company.pk, item_id=self.juice.pk,
                                         batch_id=batch.pk, uom_id=self.pcs.pk, quantity=D("24"), count=1,
                                         warehouse_id=self.fgw.pk)
        found = self.ok(self.api("GET", "/api/v1/inventory/labels/lookup", query=f"code={carton.code}"))
        self.assertEqual((found["found"], found["kind"], found["quantity"]), (True, "package", "24"))


class PurchaseTests(InventoryApiTestCase):
    def test_order_to_goods_receipt(self):
        po = self.ok(self.api("POST", "/api/v1/purchase/orders", {
            "supplier_id": self.supplier.pk, "branch_id": self.hq.pk, "order_date": "2026-10-01"}), 201)
        path = f"/api/v1/purchase/orders/{po['id']}"
        self.ok(self.api("POST", f"{path}/lines", {"item_id": self.sugar.pk, "qty": "1000", "uom_id": self.kg.pk,
                                                   "unit_price": "82", "discount_pct": "0", "vat_rate": "0"}))
        self.ok(self.api("POST", f"{path}/submit"))
        approved = self.ok(self.api("POST", f"{path}/approve"))
        self.assertEqual((approved["status"], approved["total"]), ("approved", "82000"))
        grn = self.ok(self.api("POST", "/api/v1/purchase/receipts", {
            "order_id": po["id"], "warehouse_id": self.rms.pk, "received_date": "2026-10-05"}), 201)
        self.assertEqual(len(grn["lines"]), 1)
        posted = self.ok(self.api("POST", f"/api/v1/purchase/receipts/{grn['id']}/post"))
        self.assertEqual(posted["status"], "posted")
        self.assertEqual(self.held(self.sugar, self.rms), D("1000"))
        self.assertEqual(self.ok(self.api("GET", path))["status"], "received")


class SalesTests(InventoryApiTestCase):
    def test_order_confirm_deliver_invoice_paid(self):
        self.opening()
        order = self.ok(self.api("POST", "/api/v1/sales/orders", {
            "customer_id": self.customer.pk, "warehouse_id": self.fgw.pk, "order_date": "2026-10-01",
            "source": "manual"}), 201)
        path = f"/api/v1/sales/orders/{order['id']}"
        self.ok(self.api("POST", f"{path}/lines", {"item_id": self.juice.pk, "qty": "100", "uom_id": self.pcs.pk}))
        mixed = self.ok(self.api("POST", f"{path}/lines", {"item_id": self.sugar.pk, "warehouse_id": self.rms.pk,
                                                           "qty": "50", "uom_id": self.kg.pk}))
        self.assertEqual(sorted(l["store"]["code"] for l in mixed["lines"]), ["FGW", "RMS"])
        confirmed = self.ok(self.api("POST", f"{path}/confirm", {}))
        self.assertEqual(confirmed["status"], "confirmed")
        made = self.ok(self.api("POST", f"{path}/deliver", {"delivery_date": "2026-10-02"}), 201)
        self.assertEqual(sorted(d["store"]["code"] for d in made["made"]), ["FGW", "RMS"])
        juice_dc = next(d for d in made["made"] if d["store"]["code"] == "FGW")
        self.ok(self.api("POST", f"/api/v1/sales/deliveries/{juice_dc['id']}/post"))
        invoice = self.ok(self.api("POST", f"/api/v1/sales/deliveries/{juice_dc['id']}/invoice"), 201)
        self.assertEqual((invoice["kind"], invoice["total"]), ("sales-invoices", "2875"))   # 100 x 25 + 15 %
        inv = f"/api/v1/sales/invoices/{invoice['id']}"
        self.ok(self.api("POST", f"{inv}/issue"))
        paid = self.ok(self.api("POST", f"{inv}/paid", {"paid_on": "2026-10-20", "note": "bKash 77"}))
        self.assertEqual(paid["status"], "paid")
        self.assertEqual(self.ok(self.api("GET", path))["status"], "partly_delivered")


class IntakeTests(InventoryApiTestCase):
    def test_the_erp_sends_an_order_in_once(self):
        key = self.key("sales:write", "sales:read")
        body = {"customer_code": "cus-1", "customer_ref": "WEB-1", "lines": [
            {"item_code": "FG-0001", "qty": "10"}, {"item_code": "RM-0001", "qty": "5", "unit": "KG"}]}
        sent = self.as_key(key, "POST", "/api/v1/sales/intake", body)
        self.assertEqual(sent.status_code, 201, sent.content)
        order = sent.json()["order"]
        self.assertEqual((order["status"], order["details"]["source"], order["details"]["customer_ref"]),
                         ("draft", "api", "WEB-1"))
        self.assertEqual(sorted((l["item"]["code"], l["store"]["code"]) for l in order["lines"]),
                         [("FG-0001", "FGW"), ("RM-0001", "RMS")])
        again = self.as_key(key, "POST", "/api/v1/sales/intake", body)
        self.assertEqual((again.status_code, again.json()["error"]["code"]), (409, "conflict"))
        unknown = self.as_key(key, "POST", "/api/v1/sales/intake", {"customer_code": "CUS-1", "lines": [
            {"item_code": "NOPE", "qty": "1"}]})
        self.assertIn("lines[0].item_code", unknown.json()["error"]["fields"])
        text = self.as_key(key, "POST", "/api/v1/sales/intake", {"text": "20 ctn mango juice", "sender": "Karim"})
        self.assertEqual((text.status_code, text.json()["kind"], text.json()["message_status"]),
                         (201, "message", "waiting"))

    def test_a_key_without_the_scope_is_refused(self):
        key = self.key("inventory:read")
        refused = self.as_key(key, "POST", "/api/v1/sales/intake", {"customer_code": "CUS-1", "lines": []})
        self.assertEqual((refused.status_code, refused.json()["error"]["code"]), (403, "scope_missing"))
        stock = self.as_key(key, "GET", "/api/v1/inventory/stock")
        self.assertEqual(stock.status_code, 200, stock.content)


class ProductionTests(InventoryApiTestCase):
    def test_bom_order_materials_and_output(self):
        self.opening()
        bom = self.ok(self.api("POST", "/api/v1/production/boms", {
            "item_id": self.juice.pk, "batch_size": "1000", "batch_uom_id": self.pcs.pk}), 201)
        self.ok(self.api("POST", f"/api/v1/production/boms/{bom['id']}/lines", {
            "item_id": self.sugar.pk, "qty": "100", "uom_id": self.kg.pk, "scrap_pct": "0"}))
        self.assertEqual(self.ok(self.api("POST", f"/api/v1/production/boms/{bom['id']}/activate"))["status"],
                         "active")
        order = self.ok(self.api("POST", "/api/v1/production/orders", {
            "item_id": self.juice.pk, "planned_qty": "1000", "uom_id": self.pcs.pk, "planned_date": "2026-10-03",
            "material_warehouse_id": self.rms.pk, "output_warehouse_id": self.fgw.pk}), 201)
        path = f"/api/v1/production/orders/{order['id']}"
        self.ok(self.api("POST", f"{path}/release"))
        use = self.ok(self.api("POST", f"{path}/use"), 201)
        self.assertEqual((use["kind"], use["lines"][0]["qty"]), ("material-use", "100"))
        self.ok(self.api("POST", f"/api/v1/production/materials/{use['id']}/post"))
        done = self.ok(self.api("POST", f"{path}/output", {"output_date": "2026-10-03", "qty_good": "1000",
                                                           "qty_rejected": "0", "uom_id": self.pcs.pk,
                                                           "mfg_date": "2026-10-03"}))
        self.assertEqual(done["status"], "completed")
        self.assertEqual(done["lines"][0]["details"]["used"], "100")
        self.assertEqual(self.held(self.juice, self.fgw), D("1300"))


class ReportAndPeriodTests(InventoryApiTestCase):
    def test_reports_dashboard_and_month_end(self):
        self.opening()
        listed = self.ok(self.api("GET", "/api/v1/inventory/reports"))
        self.assertIn("valuation", [r["slug"] for r in listed])
        valuation = self.ok(self.api("GET", "/api/v1/inventory/reports/valuation", query="as_of=2026-09-30"))
        self.assertIn({"label": "Value", "value": "44,800.00"}, valuation["summary"])     # 40,000 + 4,800
        file = self.api("GET", "/api/v1/inventory/reports/stock-on-hand/export", query="file_type=xlsx")
        self.assertIn("spreadsheetml", file["Content-Type"])
        dashboard = self.ok(self.api("GET", "/api/v1/inventory/dashboard"))
        self.assertEqual((dashboard["items_in_stock"], dashboard["stock_value"]), (2, "44800"))
        periods = self.ok(self.api("GET", "/api/v1/inventory/periods"))
        month = periods["next_to_close"]
        closed = self.ok(self.api("POST", "/api/v1/inventory/periods", {"month": month}), 201)
        self.assertEqual(closed["month"], month)
        reopened = self.ok(self.api("POST", f"/api/v1/inventory/periods/{closed['id']}/reopen",
                                    {"reason": "Late GRN"}))
        self.assertEqual(reopened["month"], month)


class SecurityTests(InventoryApiTestCase):
    def test_without_login_or_module_nothing_opens(self):
        self.assertEqual(self.call("GET", "/api/v1/inventory/stock").status_code, 401)
        self.enable(False)
        refused = self.api("GET", "/api/v1/inventory/items")
        self.assertEqual((refused.status_code, refused.json()["error"]["code"]), (403, "permission_denied"))

    def test_an_employee_without_the_permission_is_refused(self):
        clerk = self.person("clerk@acme.test", role="employee")
        session = self.logged_in("clerk@acme.test")
        refused = self.call("GET", "/api/v1/inventory/stock", session=session)
        self.assertEqual(refused.status_code, 403)
        self.assertIsNotNone(clerk)

    def test_another_companys_document_is_not_found(self):
        posted = self.opening()
        other_admin = self.person("boss@oth.test", role="company_admin", company=self.other)
        other = self.admin_session(other_admin, "boss@oth.test")
        feature = Feature.objects.get(code="inventory")
        platform_services.set_company_feature(actor=self.root, company_id=self.other.pk, feature_id=feature.pk,
                                              effect="enable", reason="API tests")
        hidden = self.call("GET", f"/api/v1/inventory/opening/{posted['id']}", session=other)
        self.assertEqual(hidden.status_code, 404)
        self.assertEqual(datetime.date.fromisoformat(posted["date"]), datetime.date(2026, 9, 1))
