"""Inventory pages: every form is POSTed (not just opened), the module and
permission gates hold, and the menu follows them."""

from django.test import TestCase
from django.urls import reverse

from access_control.branch_access import grant_access
from auditlog.models import AuditLog
from common.tenant import use_company
from employees.models import Employee
from inventory.models import Item, ItemUom, Location, Uom, Warehouse
from inventory.testing import InventoryFixture
from purchasing.models import Supplier
from sales.models import Customer

MENU = 'data-menu="inventory"'


class MasterPageTests(InventoryFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def test_every_list_opens(self):
        for key in ("units", "categories", "items", "stores", "bins", "suppliers", "customers"):
            page = self.client.get(reverse(f"inventory:{key}"))
            self.assertEqual(page.status_code, 200, key)
        for name in ("inventory:stock", "inventory:opening", "inventory:settings"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_server_table_draw_answers_json(self):
        page = self.client.get(reverse("inventory:items"), {"table": "1", "draw": "1",
                                                            "start": "0", "length": "10"})
        data = page.json()
        self.assertEqual(data["recordsTotal"], 2)
        self.assertIn("RM-0001", data["data"][0]["0"] + data["data"][1]["0"])

    def test_add_an_item_by_posting_the_form(self):
        response = self.client.post(reverse("inventory:items"), {
            "code": "rm-0002", "name": "Mango pulp", "name_bn": "আমের পাল্প", "kind": "raw",
            "category": self.raw.pk, "base_uom": self.kg.pk, "track_batch": "on",
            "track_expiry": "on", "shelf_life_days": "540", "min_stock": "200",
            "reorder_point": "500", "max_stock": "5000", "default_warehouse": self.rms.pk,
            "standard_cost": "210", "vat_rate": "15",
        })
        self.assertRedirects(response, reverse("inventory:items"))
        item = Item.all_objects.get(company=self.company, code="RM-0002")
        self.assertEqual((item.name_bn, item.track_expiry, item.default_warehouse), ("আমের পাল্প", True, self.rms))
        self.assertTrue(AuditLog.objects.filter(action="inventory.item.created", object_id=str(item.pk)).exists())

    def test_a_refused_form_comes_back_in_its_modal(self):
        response = self.client.post(reverse("inventory:items"), {
            "code": "X-1", "name": "Wrong", "kind": "raw", "category": self.spares.pk,
            "base_uom": self.kg.pk, "track_expiry": "on",
        })
        self.assertContains(response, 'data-open-on-load="new-dialog"')
        self.assertContains(response, "must also keep batches")
        self.assertContains(response, "This category is for another item type")
        self.assertFalse(Item.all_objects.filter(code="X-1").exists())

    def test_edit_opens_in_a_modal_and_saves(self):
        page = self.client.get(reverse("inventory:items"), {"edit": self.bearing.pk})
        self.assertContains(page, 'data-open-on-load="edit-dialog"')
        response = self.client.post(reverse("inventory:items_edit", args=[self.bearing.pk]), {
            "code": "SP-0001", "name": "Bearing 6205 ZZ", "kind": "spare", "category": self.spares.pk,
            "base_uom": self.pcs.pk, "reorder_point": "6",
        })
        self.assertRedirects(response, reverse("inventory:items"))
        self.assertEqual(self.reload(self.bearing).name, "Bearing 6205 ZZ")

    def test_deactivate_and_activate(self):
        url = reverse("inventory:units_status", args=[self.bag.pk])
        self.client.post(url, {"status": "inactive"})
        self.assertEqual(self.reload(self.bag).status, "inactive")
        self.client.post(url, {"status": "active"})
        self.assertEqual(self.reload(self.bag).status, "active")

    def test_store_bin_supplier_customer_and_unit_forms(self):
        self.client.post(reverse("inventory:stores"), {"branch": self.branch.pk, "code": "pks",
                                                       "name": "Packaging store", "kind": "packaging"})
        store = Warehouse.all_objects.get(code="PKS")
        self.client.post(reverse("inventory:bins"), {"warehouse": store.pk, "code": "r1-s1",
                                                     "description": "Caps"})
        self.assertTrue(Location.all_objects.filter(warehouse=store, code="R1-S1").exists())
        self.client.post(reverse("inventory:suppliers"), {
            "code": "SUP-1", "name": "Sugar Traders", "email": "sales@sugar.test",
            "payment_terms_days": "30"})
        self.assertEqual(Supplier.all_objects.get(code="SUP-1").payment_terms_days, 30)
        self.client.post(reverse("inventory:customers"), {
            "code": "CUS-1", "name": "Sylhet House", "customer_type": "distributor",
            "whatsapp": "8801911000000", "credit_limit": "500000", "payment_terms_days": "30"})
        self.assertEqual(Customer.all_objects.get(code="CUS-1").customer_type, "distributor")
        self.client.post(reverse("inventory:units"), {"code": "tin", "name": "Tin", "kind": "count",
                                                      "decimals": "0"})
        self.assertTrue(Uom.all_objects.filter(company=self.company, code="TIN").exists())

    def test_pack_units_on_the_item_page(self):
        response = self.client.post(reverse("inventory:pack_unit_add", args=[self.sugar.pk]),
                                    {"uom": self.bag.pk, "factor": "50"})
        self.assertRedirects(response, reverse("inventory:item_detail", args=[self.sugar.pk]))
        pack = ItemUom.all_objects.get(item=self.sugar)
        self.assertContains(self.client.get(reverse("inventory:item_detail", args=[self.sugar.pk])),
                            "1 BAG")
        refused = self.client.post(reverse("inventory:pack_unit_add", args=[self.sugar.pk]),
                                   {"uom": self.kg.pk, "factor": "1"})
        self.assertContains(refused, "base unit already")
        self.client.post(reverse("inventory:pack_unit_remove", args=[self.sugar.pk, pack.pk]))
        self.assertFalse(ItemUom.all_objects.filter(item=self.sugar).exists())

    def test_settings_by_posting_the_form(self):
        self.client.post(reverse("inventory:settings"), {"valuation_scope": "warehouse",
                                                         "expiry_alert_days": "45",
                                                         "slow_moving_days": "120",
                                                         "alert_emails": "store@inv.test, gm@inv.test",
                                                         "small_label": "40x30",
                                                         "large_label": "100x75"})
        from inventory.posting import settings_for

        saved = settings_for(self.company.pk)
        self.assertEqual((saved.expiry_alert_days, saved.small_label, saved.large_label, saved.gs1_labels),
                         (45, "40x30", "100x75", False))
        self.assertEqual((saved.slow_moving_days, saved.alert_emails), (120, "store@inv.test\ngm@inv.test"))

    def test_standard_units_button(self):
        with use_company(self.company):
            Uom.objects.filter(code="ROLL").delete()
        self.client.post(reverse("inventory:standard_units"))
        self.assertTrue(Uom.all_objects.filter(company=self.company, code="ROLL").exists())


class GateTests(InventoryFixture, TestCase):
    def test_without_the_module_nothing_opens_and_no_menu(self):
        self.enable_module(False)
        self.client.force_login(self.owner)
        page = self.client.get(reverse("inventory:stock"))
        self.assertEqual(page.status_code, 404)
        self.assertContains(page, "not enabled", status_code=404)
        self.assertNotContains(self.client.get(reverse("employee_list"), follow=True), MENU)

    def test_owner_sees_the_menu(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("inventory:stock")), MENU)

    def test_permissions_are_offered_only_with_the_module(self):
        from access_control.branch_access import offered

        self.assertIn("inventory.view", offered(self.company.pk))
        self.enable_module(False)
        self.assertNotIn("inventory.view", offered(self.company.pk))

    def test_an_employee_login_needs_access_given(self):
        user = self.member("keeper@inv.test", "employee")
        with use_company(self.company):
            Employee.objects.create(company=self.company, user=user, first_name="Store",
                                    last_name="Keeper")
            employee = Employee.objects.get(user=user)
        self.client.force_login(user)
        self.assertRedirects(self.client.get(reverse("inventory:stock")), reverse("me:home"),
                             fetch_redirect_response=False)
        grant_access(actor=self.owner, company_id=self.company.pk, employee_id=employee.pk,
                     code="inventory.view", branch_ids=[self.branch.pk])
        page = self.client.get(reverse("inventory:stock"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, MENU)
        # Viewing is not setting up.
        response = self.client.post(reverse("inventory:items"), {"code": "Z", "name": "Z"})
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(page, "Inventory settings")

    def test_a_store_in_another_branch_is_out_of_reach(self):
        from organization.services import create_branch

        other = create_branch(actor=self.owner, company_id=self.company.pk, values={
            "code": "DEP", "name": "Depot", "timezone": "Asia/Dhaka", "status": "active"})
        depot = self.store("DPT", "Depot store", Warehouse.Kind.FINISHED, branch=other)
        user = self.member("depot@inv.test", "employee")
        with use_company(self.company):
            employee = Employee.objects.create(company=self.company, user=user, first_name="A",
                                               last_name="B")
        for code in ("inventory.view", "inventory.items"):
            grant_access(actor=self.owner, company_id=self.company.pk, employee_id=employee.pk,
                         code=code, branch_ids=[self.branch.pk])
        self.client.force_login(user)
        page = self.client.get(reverse("inventory:stores"))
        self.assertContains(page, "RMS")
        self.assertNotContains(page, "DPT")
        response = self.client.post(reverse("inventory:stores_edit", args=[depot.pk]), {
            "branch": other.pk, "code": "DPT", "name": "Mine now", "kind": "finished"})
        self.assertEqual(response.status_code, 403)
