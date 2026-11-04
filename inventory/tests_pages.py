"""Inventory pages: every form is POSTed (not just opened), the module and
permission gates hold, and the menu follows them."""

from django.test import TestCase
from django.urls import reverse

from access_control.branch_access import grant_access
from auditlog.models import AuditLog
from base_template.testing import assert_inventory_menu
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
            "code": "SUP-1", "name": "Sugar Traders", "contact_person": "Rafiq", "phone": "01711000000",
            "email": "sales@sugar.test", "address": "Khatunganj, Chattogram", "payment_terms_days": "30"})
        self.assertEqual(Supplier.all_objects.get(code="SUP-1").payment_terms_days, 30)
        self.client.post(reverse("inventory:customers"), {
            "code": "CUS-1", "name": "Sylhet House", "customer_type": "distributor", "contact_person": "Mitu",
            "whatsapp": "8801911000000", "credit_limit": "500000", "payment_terms_days": "30"})
        self.assertEqual(Customer.all_objects.get(code="CUS-1").customer_type, "distributor")
        self.client.post(reverse("inventory:units"), {"code": "tin", "name": "Tin", "kind": "count",
                                                      "decimals": "0"})
        self.assertTrue(Uom.all_objects.filter(company=self.company, code="TIN").exists())

    def test_supplier_form_asks_for_what_is_needed(self):
        # 2026-11-02: Company / shop name; code, name, contact, phone, email,
        # address and terms required; "Items supplied" gone; a sectioned modal.
        page = self.client.get(reverse("inventory:suppliers"))
        self.assertContains(page, "Company / shop name")
        self.assertContains(page, "ui-form-section__title")
        self.assertContains(page, "A company or shop you buy from.")
        self.assertNotContains(page, "items_supplied")
        form = page.context["new_form"]
        # Payment terms optional (2026-11-04): left empty, 0 - no terms on a purchase order.
        self.assertEqual([name for name, box in form.fields.items() if box.required],
                         ["code", "name", "contact_person", "phone", "email", "address"])
        refused = self.client.post(reverse("inventory:suppliers"), {"code": "SUP-2", "name": "Half filled"})
        self.assertContains(refused, 'data-open-on-load="new-dialog"')
        self.assertEqual(set(refused.context["new_form"].errors),
                         {"contact_person", "phone", "email", "address"})
        self.assertFalse(Supplier.all_objects.filter(code="SUP-2").exists())
        self.client.post(reverse("inventory:suppliers"), {
            "code": "SUP-3", "name": "No terms", "contact_person": "Rafiq", "phone": "01711000001",
            "email": "x@terms.test", "address": "Dhaka", "payment_terms_days": ""})
        self.assertEqual(Supplier.all_objects.get(code="SUP-3").payment_terms_days, 0)

    def test_customer_form_asks_for_what_is_needed(self):
        page = self.client.get(reverse("inventory:customers"))
        self.assertContains(page, "Company / shop name")
        self.assertContains(page, "Credit and prices")
        form = page.context["new_form"]
        self.assertEqual([name for name, box in form.fields.items() if box.required],
                         ["code", "customer_type", "contact_person", "whatsapp"])
        refused = self.client.post(reverse("inventory:customers"), {"code": "CUS-2", "name": "Corner shop"})
        self.assertEqual(set(refused.context["new_form"].errors), {"customer_type", "contact_person", "whatsapp"})
        # Buying in their own name: no shop name, the contact person's is saved.
        self.client.post(reverse("inventory:customers"), {
            "code": "CUS-3", "name": "", "customer_type": "retailer", "contact_person": "Abdul Karim",
            "whatsapp": "8801911222333"})
        self.assertEqual(Customer.all_objects.get(code="CUS-3").name, "Abdul Karim")
        # Phone and WhatsApp: Bangladeshi 11-digit numbers, kept as 11 digits.
        self.assertEqual(Customer.all_objects.get(code="CUS-3").whatsapp, "01911222333")
        refused = self.client.post(reverse("inventory:customers"), {
            "code": "CUS-4", "customer_type": "retailer", "contact_person": "Rana", "whatsapp": "0171100",
            "phone": "12345678901"})
        self.assertEqual(set(refused.context["new_form"].errors), {"whatsapp", "phone"})
        self.assertContains(refused, "Write an 11-digit Bangladeshi number, e.g. 01711000000.")

    def test_bangladeshi_numbers(self):
        from inventory.forms import bd_number

        # The boxes take at most 11 digits and are checked as typed (bd_phone.js).
        page = self.client.get(reverse("inventory:customers"))
        self.assertContains(page, 'maxlength="11"', count=2)       # the Add modal's phone and WhatsApp
        self.assertContains(page, "inventory/js/bd_phone.js")
        refused = self.client.post(reverse("inventory:customers"), {
            "code": "CUS-9", "customer_type": "retailer", "contact_person": "Zero", "whatsapp": "00000000000"})
        self.assertContains(refused, "Write an 11-digit Bangladeshi number, e.g. 01711000000.")

        for typed in ("01711000000", "017-1100 0000", "+8801711000000", "8801711000000", "০১৭১১০০০০০০"):
            self.assertEqual(bd_number(typed), "01711000000", typed)
        self.assertEqual(bd_number(""), "")
        for wrong in ("0171100000", "017110000000", "02711000000", "01211000000", "00000000000", "01011111111"):
            with self.assertRaises(Exception, msg=wrong):
                bd_number(wrong)
        self.client.post(reverse("inventory:customers"), {
            "code": "CUS-2", "name": "Corner shop", "customer_type": "retailer", "contact_person": "Babu",
            "whatsapp": "8801811000000"})
        saved = Customer.all_objects.get(code="CUS-2")
        self.assertEqual((saved.credit_limit, saved.payment_terms_days, saved.email), (0, 0, ""))
        # Editing opens the same sectioned modal.
        page = self.client.get(reverse("inventory:customers"), {"edit": saved.pk})
        self.assertContains(page, 'data-open-on-load="edit-dialog"')
        self.assertContains(page, "Edit Corner shop")

    def test_pack_units_on_the_item_page(self):
        response = self.client.post(reverse("inventory:pack_unit_add", args=[self.sugar.pk]),
                                    {"uom": self.bag.pk, "factor": "50"})
        self.assertRedirects(response, reverse("inventory:item_detail", args=[self.sugar.pk]))
        pack = ItemUom.all_objects.get(item=self.sugar)
        self.assertContains(self.client.get(reverse("inventory:item_detail", args=[self.sugar.pk])),
                            "1 BAG")
        # The base unit is not offered as a pack (2026-10-26), and is refused if posted.
        page = self.client.get(reverse("inventory:item_detail", args=[self.sugar.pk]))
        form = page.context["pack_form"]
        self.assertNotIn(self.kg, form.fields["uom"].queryset)
        self.assertNotIn(self.bag, form.fields["uom"].queryset)
        refused = self.client.post(reverse("inventory:pack_unit_add", args=[self.sugar.pk]),
                                   {"uom": self.kg.pk, "factor": "1"})
        self.assertContains(refused, "Select a valid choice")
        self.client.post(reverse("inventory:pack_unit_remove", args=[self.sugar.pk, pack.pk]))
        self.assertFalse(ItemUom.all_objects.filter(item=self.sugar).exists())

    def test_settings_by_posting_the_form(self):
        self.client.post(reverse("inventory:settings"), {"valuation_scope": "warehouse",
                                                         "price_cost": "average",
                                                         "purchase_vat": "claim",
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
        self.assertEqual((saved.price_cost, saved.purchase_vat), ("average", "claim"))

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
        assert_inventory_menu(self, self.client.get(reverse("inventory:stock")), MENU)

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
        assert_inventory_menu(self, page, MENU)
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
