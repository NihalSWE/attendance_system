"""Phase 6 "done when": an order reserves stock, a delivery picks FEFO
batches by scan and releases the reservation, and the invoice matches the
delivery."""

import datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from access_control.branch_access import grant_access
from common.tenant import use_company
from employees.models import Employee
from inventory import labels, masters, posting
from inventory.models import (
    Batch,
    BarcodeLabel,
    Item,
    ItemKind,
    MoveType,
    StockBalance,
    StockMove,
    Warehouse,
)
from inventory.testing import DAY, D, InventoryFixture
from sales import deliveries, orders
from sales.models import Delivery, Reservation, SalesInvoice, SalesOrder
from sales.services import CUSTOMER


class SalesFixture(InventoryFixture):
    """Mango juice in FGW: batch J1 (expires Mar 2027) 100, J2 (Jun 2027) 200, cost 16."""

    def setUp(self):
        super().setUp()
        self.kw = {"actor": self.owner, "company_id": self.company.pk}
        juice = self.category("JCE", "Juice", ItemKind.FINISHED)
        self.juice = self.item("FG-0001", "Mango juice 250 ml", ItemKind.FINISHED, juice, self.pcs,
                               track_batch=True, track_expiry=True, shelf_life_days=270, vat_rate=D("15"),
                               sale_price=D("25"))
        self.j1 = Batch.all_objects.create(company=self.company, item=self.juice, batch_no="J1", source="production",
                                           expiry_date=datetime.date(2027, 3, 31))
        self.j2 = Batch.all_objects.create(company=self.company, item=self.juice, batch_no="J2", source="production",
                                           expiry_date=datetime.date(2027, 6, 30))
        self.rejected = self.store("REJ", "Rejected", Warehouse.Kind.REJECTED)
        posting.post(company_id=self.company.pk, actor=self.owner, document_type="opening", document_id=1,
                     move_type=MoveType.OPENING, work_date=datetime.date(2026, 9, 1), lines=[
                         posting.Line(item=self.juice, warehouse=self.fgw, batch=self.j1, qty=D("100"), unit_cost=D("16")),
                         posting.Line(item=self.juice, warehouse=self.fgw, batch=self.j2, qty=D("200"), unit_cost=D("16"))])
        self.customer = masters.create(CUSTOMER, **self.kw, values={
            "code": "CUS-1", "name": "Sylhet Distribution", "customer_type": "distributor", "credit_limit": D("100000"),
            "payment_terms_days": 30, "price_list_name": "Distributor"})
        distributor = orders.create_price_list(**self.kw, values={"name": "Distributor", "valid_from": None,
                                                                  "valid_to": None, "note": ""})
        orders.set_price(**self.kw, price_list_id=distributor.pk,
                         values={"item": self.juice, "uom": self.pcs, "price": D("22"), "min_qty": D("0")})
        orders.set_price(**self.kw, price_list_id=distributor.pk,
                         values={"item": self.juice, "uom": self.pcs, "price": D("21"), "min_qty": D("500")})

    def on_hand(self, batch, store=None):
        row = StockBalance.all_objects.filter(batch=batch, warehouse=store or self.fgw).first()
        return (row.qty_on_hand, row.qty_reserved) if row else (D("0"), D("0"))

    def order(self, qty="150", customer=None):
        order = orders.create_order(**self.kw, values={
            "customer": customer or self.customer, "warehouse": self.fgw, "order_date": DAY, "delivery_date": None,
            "source": "manual", "customer_ref": "", "delivery_address": "", "note": ""})
        orders.add_line(**self.kw, order_id=order.pk, values={"item": self.juice, "qty": D(qty), "uom": self.pcs})
        return order


class DoneWhenTests(SalesFixture, TestCase):
    def test_reserve_deliver_by_scan_and_invoice(self):
        order = self.order()
        with use_company(self.company):
            line = order.lines.get()
        self.assertEqual((line.unit_price, line.vat_rate), (D("22"), D("15")))   # the customer's price list
        orders.confirm(**self.kw, order_id=order.pk)
        # Reserved first expiry first: all of J1, 50 of J2.
        self.assertEqual(self.on_hand(self.j1), (D("100"), D("100")))
        self.assertEqual(self.on_hand(self.j2), (D("200"), D("50")))
        # Reserved stock cannot be issued to production or a department.
        with self.assertRaisesMessage(ValidationError, "is reserved for sales orders"):
            posting.post(company_id=self.company.pk, actor=self.owner, document_type="t", document_id=2,
                         move_type=MoveType.ISSUE, work_date=DAY,
                         lines=[posting.Line(item=self.juice, warehouse=self.fgw, batch=self.j1, qty=D("-1"))])

        # The delivery: scan the J1 carton label (100), then the rest first expiry first.
        [carton] = labels.package_labels(**self.kw, item_id=self.juice.pk, batch_id=self.j1.pk, uom_id=self.pcs.pk,
                                         quantity=D("100"), count=1, warehouse_id=self.fgw.pk)
        delivery = deliveries.create_delivery(**self.kw, values={"order": order, "delivery_date": DAY,
                                                                 "vehicle_no": "DHA-1", "driver": "", "note": ""},
                                              fill=False)
        deliveries.scan_into_delivery(**self.kw, delivery_id=delivery.pk, code=carton.code)
        deliveries.add_line(**self.kw, delivery_id=delivery.pk, values={
            "order_line": line, "qty": D("50"), "uom": self.pcs})
        with use_company(self.company):
            picked = list(delivery.lines.order_by("pk").values_list("batch__batch_no", "qty"))
        self.assertEqual(picked, [("J1", D("100")), ("J2", D("50"))])
        deliveries.post_delivery(**self.kw, delivery_id=delivery.pk)
        # Stock out, reservation released, label used up, order delivered.
        self.assertEqual(self.on_hand(self.j1), (D("0"), D("0")))
        self.assertEqual(self.on_hand(self.j2), (D("150"), D("0")))
        self.assertFalse(Reservation.all_objects.exists())
        self.assertEqual(BarcodeLabel.all_objects.get(pk=carton.pk).status, "consumed")
        self.assertEqual(SalesOrder.all_objects.get(pk=order.pk).status, "delivered")
        self.assertEqual(sorted(StockMove.all_objects.filter(document_type="delivery").values_list("qty", flat=True)),
                         [D("-100"), D("-50")])

        # The invoice matches the delivery.
        invoice = deliveries.create_invoice(**self.kw, delivery_id=delivery.pk)
        with use_company(self.company):
            invoiced = sorted(invoice.lines.values_list("qty", "unit_price", "amount", "vat_amount"))
        self.assertEqual(invoiced, [(D("50"), D("22"), D("1100"), D("165")), (D("100"), D("22"), D("2200"), D("330"))])
        self.assertEqual((invoice.amount, invoice.vat, invoice.total), (D("3300"), D("495"), D("3795")))
        deliveries.issue_invoice(**self.kw, invoice_id=invoice.pk)
        invoice.refresh_from_db()
        self.assertEqual((invoice.status, invoice.due_date), ("issued", DAY + datetime.timedelta(days=30)))
        with self.assertRaisesMessage(ValidationError, "cancel the invoice first"):
            deliveries.reverse_delivery(**self.kw, delivery_id=delivery.pk, reason="x")
        deliveries.mark_paid(**self.kw, invoice_id=invoice.pk, paid_on=DAY, note="bKash 77AB")
        self.assertEqual(SalesInvoice.all_objects.get(pk=invoice.pk).status, "paid")
        self.assertEqual(posting.ledger_differences(self.company.pk), [])


class RawMaterialSaleTests(SalesFixture, TestCase):
    def test_raw_material_is_sold_from_its_store_like_finished_goods(self):
        """Sugar sold to a customer straight from the raw material store, at the item's sale price."""
        Item.all_objects.filter(pk=self.sugar.pk).update(sale_price=D("95"), vat_rate=D("5"))
        sugar = Item.all_objects.get(pk=self.sugar.pk)
        s1 = Batch.all_objects.create(company=self.company, item=sugar, batch_no="S1", source="opening",
                                      expiry_date=datetime.date(2027, 9, 30))
        posting.post(company_id=self.company.pk, actor=self.owner, document_type="opening", document_id=2,
                     move_type=MoveType.OPENING, work_date=datetime.date(2026, 9, 1), lines=[
                         posting.Line(item=sugar, warehouse=self.rms, batch=s1, qty=D("500"), unit_cost=D("80"))])
        order = orders.create_order(**self.kw, values={
            "customer": self.customer, "warehouse": self.rms, "order_date": DAY, "delivery_date": None,
            "source": "manual", "customer_ref": "", "delivery_address": "", "note": ""})
        orders.add_line(**self.kw, order_id=order.pk, values={"item": sugar, "qty": D("200"), "uom": self.kg})
        orders.confirm(**self.kw, order_id=order.pk)
        self.assertEqual(StockBalance.all_objects.get(batch=s1).qty_reserved, D("200"))
        delivery = deliveries.create_delivery(**self.kw, values={"order": order, "delivery_date": DAY,
                                                                 "vehicle_no": "", "driver": "", "note": ""})
        deliveries.post_delivery(**self.kw, delivery_id=delivery.pk)
        self.assertEqual(StockBalance.all_objects.get(batch=s1).qty_on_hand, D("300"))
        invoice = deliveries.create_invoice(**self.kw, delivery_id=delivery.pk)
        self.assertEqual((invoice.amount, invoice.vat, invoice.total), (D("19000"), D("950"), D("19950")))


class MixedStoreTests(SalesFixture, TestCase):
    """Phase 8: one order, juice from finished goods and sugar from the raw material store."""

    def setUp(self):
        super().setUp()
        Item.all_objects.filter(pk=self.sugar.pk).update(sale_price=D("95"), vat_rate=D("5"))
        self.sugar = Item.all_objects.get(pk=self.sugar.pk)
        self.s1 = Batch.all_objects.create(company=self.company, item=self.sugar, batch_no="S1", source="opening",
                                           expiry_date=datetime.date(2027, 9, 30))
        posting.post(company_id=self.company.pk, actor=self.owner, document_type="opening", document_id=2,
                     move_type=MoveType.OPENING, work_date=datetime.date(2026, 9, 1), lines=[
                         posting.Line(item=self.sugar, warehouse=self.rms, batch=self.s1, qty=D("500"),
                                      unit_cost=D("80"))])
        self.mixed = self.order(qty="120")                       # juice, from the order's store FGW
        orders.add_line(**self.kw, order_id=self.mixed.pk, values={
            "item": self.sugar, "warehouse": self.rms, "qty": D("200"), "uom": self.kg})

    def test_one_order_two_stores_two_challans(self):
        with use_company(self.company):
            stores = dict(self.mixed.lines.values_list("item__code", "warehouse__code"))
        self.assertEqual(stores, {"FG-0001": "FGW", "RM-0001": "RMS"})
        orders.confirm(**self.kw, order_id=self.mixed.pk)
        # Each line reserved in its own store.
        self.assertEqual(self.on_hand(self.j1), (D("100"), D("100")))
        self.assertEqual(self.on_hand(self.s1, self.rms), (D("500"), D("200")))

        with self.assertRaisesMessage(ValidationError, "goes from FGW, RMS: choose the store"):
            deliveries.create_delivery(**self.kw, values={"order": self.mixed, "delivery_date": DAY})
        made, skipped = deliveries.deliver_order(**self.kw, order_id=self.mixed.pk, delivery_date=DAY)
        self.assertEqual(([d.warehouse.code for d in made], skipped), (["FGW", "RMS"], []))
        juice_dc, sugar_dc = made
        with use_company(self.company):
            self.assertEqual(set(juice_dc.lines.values_list("item__code", flat=True)), {"FG-0001"})
            self.assertEqual(list(sugar_dc.lines.values_list("item__code", "qty")), [("RM-0001", D("200"))])
            sugar_line = self.mixed.lines.get(item=self.sugar)
        # A line goes out only on its own store's challan.
        with self.assertRaisesMessage(ValidationError, "goes from RMS; this challan is from FGW"):
            deliveries.add_line(**self.kw, delivery_id=juice_dc.pk,
                                values={"order_line": sugar_line, "qty": D("1"), "uom": self.kg})
        with self.assertRaisesMessage(ValidationError, "A draft delivery of this order from RMS is open"):
            deliveries.create_delivery(**self.kw, values={"order": self.mixed, "warehouse": self.rms,
                                                          "delivery_date": DAY})

        deliveries.post_delivery(**self.kw, delivery_id=sugar_dc.pk)
        self.assertEqual(SalesOrder.all_objects.get(pk=self.mixed.pk).status, "partly_delivered")
        self.assertEqual(self.on_hand(self.s1, self.rms), (D("300"), D("0")))
        deliveries.post_delivery(**self.kw, delivery_id=juice_dc.pk)
        self.assertEqual(SalesOrder.all_objects.get(pk=self.mixed.pk).status, "delivered")
        self.assertEqual(self.on_hand(self.j1), (D("0"), D("0")))
        self.assertEqual(set(StockMove.all_objects.filter(document_type="delivery").values_list(
            "document_id", "warehouse__code")), {(juice_dc.pk, "FGW"), (sugar_dc.pk, "RMS")})

        # One invoice per challan; a return goes back to the challan's store.
        sugar_bill = deliveries.create_invoice(**self.kw, delivery_id=sugar_dc.pk)
        juice_bill = deliveries.create_invoice(**self.kw, delivery_id=juice_dc.pk)
        self.assertEqual((sugar_bill.total, juice_bill.total), (D("19950"), D("3036")))   # 200 x 95 + 5 %; 120 x 22 + 15 %
        back = deliveries.create_return(**self.kw, values={"delivery": sugar_dc, "return_date": DAY,
                                                           "reason": "Wet bags"})
        self.assertEqual(back.warehouse, self.rms)
        self.assertEqual(posting.ledger_differences(self.company.pk), [])

    def test_a_line_store_must_be_one_goods_are_sold_from(self):
        quarantine = self.store("QRN", "Quarantine", Warehouse.Kind.QUARANTINE)
        with self.assertRaisesMessage(ValidationError, "QRN is not a store goods are sold from"):
            orders.add_line(**self.kw, order_id=self.mixed.pk, values={
                "item": self.sugar, "warehouse": quarantine, "qty": D("1"), "uom": self.kg})

    def test_reversing_a_challan_reserves_again_in_its_store(self):
        orders.confirm(**self.kw, order_id=self.mixed.pk)
        made, _ = deliveries.deliver_order(**self.kw, order_id=self.mixed.pk, delivery_date=DAY)
        sugar_dc = next(d for d in made if d.warehouse == self.rms)
        deliveries.post_delivery(**self.kw, delivery_id=sugar_dc.pk)
        deliveries.reverse_delivery(**self.kw, delivery_id=sugar_dc.pk, reason="Wrong truck")
        self.assertEqual(self.on_hand(self.s1, self.rms), (D("500"), D("200")))


class RuleTests(SalesFixture, TestCase):
    def test_price_from_quantity_and_item_price(self):
        order = self.order(qty="600")
        with use_company(self.company):
            self.assertEqual(order.lines.get().unit_price, D("21"))
        retail = masters.create(CUSTOMER, **self.kw, values={"code": "CUS-2", "name": "Shop",
                                                             "customer_type": "retailer"})
        other = self.order(customer=retail)
        with use_company(self.company):
            self.assertEqual(other.lines.get().unit_price, D("25"))   # no list: the item's sale price

    def test_another_orders_reservation_is_out_of_reach(self):
        first = self.order(qty="250")
        orders.confirm(**self.kw, order_id=first.pk)
        second = self.order(qty="100")
        confirmed = orders.confirm(**self.kw, order_id=second.pk)
        self.assertEqual(confirmed.shortages, ["FG-0001 short by 50 PCS"])
        with use_company(self.company):
            line = second.lines.get()
        delivery = deliveries.create_delivery(**self.kw, values={"order": second, "delivery_date": DAY,
                                                                 "vehicle_no": "", "driver": "", "note": ""},
                                              fill=False)
        with self.assertRaisesMessage(ValidationError, "Only 0 PCS of that batch is free for this order"):
            deliveries.add_line(**self.kw, delivery_id=delivery.pk, values={
                "order_line": line, "batch": self.j1, "qty": D("10"), "uom": self.pcs,
                "not_fefo_reason": "x"})
        deliveries.add_line(**self.kw, delivery_id=delivery.pk, values={
            "order_line": line, "qty": D("50"), "uom": self.pcs})
        deliveries.post_delivery(**self.kw, delivery_id=delivery.pk)
        # The first order still has all 250 reserved.
        with use_company(self.company):
            self.assertEqual(first.lines.get().qty_reserved_base, D("250"))

    def test_credit_limit_warns_and_a_reason_confirms(self):
        self.customer.credit_limit = D("3000")
        self.customer.save()
        order = self.order()
        with self.assertRaisesMessage(ValidationError, "over the credit limit of 3,000.00"):
            orders.confirm(**self.kw, order_id=order.pk)
        orders.confirm(**self.kw, order_id=order.pk, credit_note="Owner agreed: pays on Friday")
        self.assertEqual(SalesOrder.all_objects.get(pk=order.pk).credit_note, "Owner agreed: pays on Friday")

    def test_someone_else_confirms_and_cancel_releases(self):
        user = self.member("sales@inv.test", "employee")
        with use_company(self.company):
            employee = Employee.objects.create(company=self.company, user=user, first_name="S")
        for code in ("sales.order", "sales.approve"):
            grant_access(**self.kw, employee_id=employee.pk, code=code, branch_ids=[self.branch.pk])
        mine = {"actor": user, "company_id": self.company.pk}
        order = orders.create_order(**mine, values={
            "customer": self.customer, "warehouse": self.fgw, "order_date": DAY, "delivery_date": None,
            "source": "manual", "customer_ref": "", "delivery_address": "", "note": ""})
        orders.add_line(**mine, order_id=order.pk, values={"item": self.juice, "qty": D("10"), "uom": self.pcs})
        with self.assertRaisesMessage(PermissionDenied, "someone else approves it"):
            orders.confirm(**mine, order_id=order.pk)
        orders.confirm(**self.kw, order_id=order.pk)
        self.assertEqual(self.on_hand(self.j1), (D("100"), D("10")))
        orders.cancel(**mine, order_id=order.pk, note="Customer changed mind")
        self.assertEqual(self.on_hand(self.j1), (D("100"), D("0")))

    def test_partial_delivery_close_and_reversal_reserves_again(self):
        order = self.order()
        orders.confirm(**self.kw, order_id=order.pk)
        delivery = deliveries.create_delivery(**self.kw, values={"order": order, "delivery_date": DAY,
                                                                 "vehicle_no": "", "driver": "", "note": ""})
        with use_company(self.company):
            j2_line = delivery.lines.get(batch=self.j2)
        deliveries.remove_line(**self.kw, delivery_id=delivery.pk, line_id=j2_line.pk)   # only J1's 100 go
        deliveries.post_delivery(**self.kw, delivery_id=delivery.pk)
        self.assertEqual(SalesOrder.all_objects.get(pk=order.pk).status, "partly_delivered")
        deliveries.reverse_delivery(**self.kw, delivery_id=delivery.pk, reason="Truck broke down")
        self.assertEqual(SalesOrder.all_objects.get(pk=order.pk).status, "confirmed")
        self.assertEqual(self.on_hand(self.j1), (D("100"), D("100")))     # reserved again

    def test_sales_return_damaged_to_rejected_at_delivery_cost(self):
        order = self.order(qty="20")
        orders.confirm(**self.kw, order_id=order.pk)
        delivery = deliveries.create_delivery(**self.kw, values={"order": order, "delivery_date": DAY,
                                                                 "vehicle_no": "", "driver": "", "note": ""})
        deliveries.post_delivery(**self.kw, delivery_id=delivery.pk)
        with use_company(self.company):
            delivered = delivery.lines.get()
        back = deliveries.create_return(**self.kw, values={"customer": None, "delivery": delivery, "warehouse": None,
                                                          "return_date": DAY, "reason": "Leaking bottles"})
        deliveries.add_return_line(**self.kw, return_id=back.pk, values={
            "delivery_line": delivered, "condition": "damaged", "qty": D("5"), "uom": self.pcs})
        with self.assertRaisesMessage(ValidationError, "More than was delivered"):
            deliveries.add_return_line(**self.kw, return_id=back.pk, values={
                "delivery_line": delivered, "condition": "good", "qty": D("16"), "uom": self.pcs})
        deliveries.post_return(**self.kw, return_id=back.pk)
        self.assertEqual(self.on_hand(self.j1, self.rejected), (D("5"), D("0")))
        move = StockMove.all_objects.get(document_type="sales_return")
        self.assertEqual((move.qty, move.unit_cost), (D("5"), D("16")))
        self.assertEqual(posting.ledger_differences(self.company.pk), [])
        self.assertTrue(Delivery.all_objects.filter(pk=delivery.pk, status="posted").exists())
