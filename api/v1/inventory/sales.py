"""Sales: orders, deliveries (challans), invoices and returns (the panel's
Sales menu) - and the order intake for the client's other systems."""

from rest_framework import serializers
from rest_framework.response import Response

from api.core.docs import endpoint
from api.core.errors import ApiError
from api.core.forms import service_errors
from api.core.serializers import StrictSerializer
from api.v1.inventory.engine import (
    MODULE_NOTE,
    WRITE_ERRORS,
    Action,
    DocSpec,
    InventoryDocumentSerializer,
    InventoryNoteSerializer,
    InventoryReasonSerializer,
    document_example,
    document_out,
    validated,
    view_class,
)
from inventory import access
from inventory.views import branch_ids
from sales import deliveries, orders
from sales import forms as f
from sales.models import Delivery, SalesInvoice, SalesOrder, SalesReturn

SCOPES = ("sales:read", "sales:write")
VIEW = ("inventory.view", "sales.order", "sales.approve", "sales.deliver", "sales.invoice")
VIEWERS = ["Anyone with a sales permission or View stock in the order's store branch; owner and administrator "
           "everywhere"]
SELLERS = ["Enter sales orders and price lists (sales.order) in the store's branch"]
CONFIRMERS = ["Confirm sales orders (sales.approve) - not the person who entered it"]
DELIVERERS = ["Deliver goods to customers (sales.deliver) in the challan's store branch"]
INVOICERS = ["Invoice customers and record payments (sales.invoice) in the order's branch"]
REASON = {"reason": "Wrong customer"}


def _scoped(model, related, path):
    def rows(request, company_id):
        return access.scope_warehouses(model.objects.select_related(*related), request.user, company_id, VIEW,
                                       field=path)
    return rows


def _sales_branches(request, company_id):
    return {"branch_ids": branch_ids(request.user, company_id, "sales.order")}


class DeliveredSerializer(serializers.Serializer):
    made = InventoryDocumentSerializer(many=True, help_text="The delivery challans drafted: one per store.")
    skipped = serializers.ListField(child=serializers.CharField(),
                                    help_text="Stores that got no challan, and why (a draft open, no permission).")


def _deliver(r, c, pk, d):
    from inventory.doc_common import local_today

    order = SalesOrder.objects.get(pk=pk)
    return deliveries.deliver_order(actor=r.user, company_id=c, order_id=pk,
                                    delivery_date=d.get("delivery_date") or max(order.order_date,
                                                                                local_today(order.company)))


class DeliverSerializer(StrictSerializer):
    delivery_date = serializers.DateField(required=False, help_text="The challans' date (YYYY-MM-DD); left out: "
                                                                    "today.")


class CreditNoteSerializer(StrictSerializer):
    credit_note = serializers.CharField(max_length=255, required=False, allow_blank=True,
                                        help_text="Needed only when the order takes the customer over the credit "
                                                  "limit: why it is confirmed anyway.")


ORDERS = DocSpec(
    key="sales-orders", area="sales", path="sales/orders", title="Sales orders", singular="sales order",
    about="An order from a customer: lines at the customer's price list (or the item's sale price), each from its "
          "own store. Confirming reserves stock first expiry first; delivery challans take it out.",
    model=SalesOrder, get=orders.get_order, view_code=VIEW,
    queryset=_scoped(SalesOrder, ("customer", "warehouse"), "warehouse__branch"),
    read_roles=VIEWERS, write_roles=SELLERS, scopes=SCOPES,
    panel={"list": "sales:orders", "detail": "sales:order_detail", "create": "sales:orders",
           "lines": "sales:order_line_add", "action": "sales:order_action"},
    date_field="order_date", store_field="warehouse", party_field="customer", parent_field="order",
    totals=orders.totals, search=("number", "customer__name", "customer__code", "customer_ref"),
    example_number="SO-2026-00021",
    create_form=f.OrderForm, create_form_kwargs=_sales_branches,
    create_call=lambda actor, cid, values: orders.create_order(actor=actor, company_id=cid, values=values),
    create_example={"customer_id": 6, "warehouse_id": 5, "order_date": "2026-10-16", "source": "manual",
                    "customer_ref": "PO-7781"},
    line_form=f.OrderLineForm, line_form_kwargs=lambda r, c, row: _sales_branches(r, c),
    add_line=lambda actor, cid, pk, values: orders.add_line(actor=actor, company_id=cid, order_id=pk, values=values),
    line_example={"item_id": 30, "qty": "50", "uom_id": 4},
    line_notes={"warehouse_id": "Left out: the order's store (raw materials may come from their own store).",
                "unit_price": "Left out: the customer's price list, else the item's sale price.",
                "discount_pct": "Left out: 0.", "vat_rate": "Left out: the item's VAT."},
    remove_line=lambda actor, cid, pk, line_id: orders.remove_line(actor=actor, company_id=cid, order_id=pk,
                                                                   line_id=line_id),
    actions=[
        Action("confirm", "Confirm a sales order", "Reserves the stock, line by line, first expiry first.",
               ["Status: confirmed; what could not be reserved stays short."],
               "Not by the person who entered it (unless owner or administrator). Over the customer's credit limit "
               "it needs credit_note.",
               lambda r, c, pk, d: orders.confirm(actor=r.user, company_id=c, order_id=pk,
                                                  credit_note=d.get("credit_note", "")),
               CONFIRMERS, body=CreditNoteSerializer, body_example={"credit_note": ""}, statuses=("draft",)),
        Action("reserve", "Reserve again", "Tries to reserve what was short (after production).",
               ["Reserves what is free now."], "For a confirmed or partly delivered order.",
               lambda r, c, pk, d: orders.reserve_again(actor=r.user, company_id=c, order_id=pk),
               SELLERS, statuses=("confirmed", "partly_delivered")),
        Action("deliver", "Draft the deliveries", "One delivery challan per store, from the reserved stock.",
               ["Drafts a challan for every store with something left to deliver."],
               "Check or scan each challan, then post it.",
               _deliver, DELIVERERS, body=DeliverSerializer, body_example={"delivery_date": "2026-10-17"},
               statuses=("confirmed", "partly_delivered"), response=DeliveredSerializer, status_code=201),
        Action("cancel", "Cancel a sales order", "Frees its reservation.", ["Status: cancelled."],
               "Not once a delivery was made: close it instead.",
               lambda r, c, pk, d: orders.cancel(actor=r.user, company_id=c, order_id=pk, note=d.get("note", "")),
               SELLERS, body=InventoryNoteSerializer, body_example={"note": "Customer cancelled"},
               statuses=("draft", "confirmed")),
        Action("close", "Close a sales order", "The rest will not be delivered; frees what is reserved.",
               ["Status: closed."], "For a partly delivered order; the note says why.",
               lambda r, c, pk, d: orders.close(actor=r.user, company_id=c, order_id=pk, note=d.get("note", "")),
               SELLERS, body=InventoryNoteSerializer, body_example={"note": "Customer took the rest elsewhere"},
               statuses=("partly_delivered",)),
    ],
)

DELIVERIES = DocSpec(
    key="deliveries", area="sales", path="sales/deliveries", title="Deliveries (challans)",
    singular="delivery challan",
    about="Goods out to a customer against a confirmed order, from one store: filled from the order's reservation "
          "first expiry first, or by scanning cartons. Never takes another order's reservation.",
    model=Delivery, get=deliveries.get_delivery, view_code=VIEW,
    queryset=_scoped(Delivery, ("order__customer", "warehouse"), "warehouse__branch"),
    read_roles=VIEWERS, write_roles=DELIVERERS, scopes=SCOPES,
    panel={"list": "sales:deliveries", "detail": "sales:delivery_detail", "create": "sales:deliveries",
           "lines": "sales:delivery_line_add", "action": "sales:delivery_action"},
    date_field="delivery_date", store_field="warehouse", reference_field="order", parent_field="delivery",
    search=("number", "order__number", "order__customer__name", "vehicle_no"), example_number="DC-2026-00018",
    create_form=f.DeliveryForm,
    create_call=lambda actor, cid, values: deliveries.create_delivery(actor=actor, company_id=cid, values=values),
    create_example={"order_id": 21, "warehouse_id": 5, "delivery_date": "2026-10-17", "vehicle_no": "DHA-11-2233"},
    create_notes={"warehouse_id": "Needed only when the order goes from more than one store."},
    line_form=f.DeliveryLineForm, line_form_kwargs=lambda r, c, row: {"delivery": row},
    add_line=lambda actor, cid, pk, values: deliveries.add_line(actor=actor, company_id=cid, delivery_id=pk,
                                                                values=values),
    line_example={"order_line_id": 61, "qty": "20", "uom_id": 4},
    remove_line=lambda actor, cid, pk, line_id: deliveries.remove_line(actor=actor, company_id=cid, delivery_id=pk,
                                                                       line_id=line_id),
    scan=lambda actor, cid, pk, code: deliveries.scan_into_delivery(actor=actor, company_id=cid, delivery_id=pk,
                                                                    code=code),
    actions=[
        Action("post", "Post a delivery", "The goods leave for the customer.",
               ["Takes the stock out, releases the order's reservation, counts it delivered."],
               "Refused if it would take another order's reservation.",
               lambda r, c, pk, d: deliveries.post_delivery(actor=r.user, company_id=c, delivery_id=pk),
               DELIVERERS, statuses=("draft",)),
        Action("invoice", "Invoice a delivery", "The draft invoice: exactly what was delivered, at the order's prices.",
               ["Answers the new draft invoice."], "One live invoice per delivery.",
               lambda r, c, pk, d: deliveries.create_invoice(actor=r.user, company_id=c, delivery_id=pk),
               INVOICERS, statuses=("posted",), status_code=201),
        Action("cancel", "Cancel a delivery", "Drops the draft.", ["Status: cancelled."], "Only a draft.",
               lambda r, c, pk, d: deliveries.cancel_delivery(actor=r.user, company_id=c, delivery_id=pk),
               DELIVERERS, statuses=("draft",)),
        Action("reverse", "Reverse a delivery", "The goods come back and are reserved again.", ["Status: reversed."],
               "Before it is invoiced (cancel the invoice first). The reason is kept.",
               lambda r, c, pk, d: deliveries.reverse_delivery(actor=r.user, company_id=c, delivery_id=pk,
                                                               reason=d.get("reason", "")),
               DELIVERERS, body=InventoryReasonSerializer, body_example=REASON, statuses=("posted",)),
    ],
)


class PaidSerializer(StrictSerializer):
    paid_on = serializers.DateField(help_text="The day it was paid (YYYY-MM-DD).")
    note = serializers.CharField(max_length=255, required=False, allow_blank=True,
                                 help_text="Payment reference: cheque number, bKash transaction ...")


INVOICES = DocSpec(
    key="sales-invoices", area="sales", path="sales/invoices", title="Sales invoices", singular="sales invoice",
    about="The invoice of one delivery: its quantities at the order's prices, discount and VAT. Draft → issued "
          "(due date from the customer's payment terms) → paid. Made from a delivery (POST "
          "/sales/deliveries/{id}/invoice).",
    model=SalesInvoice, get=deliveries.get_invoice, view_code=VIEW,
    queryset=_scoped(SalesInvoice, ("customer", "delivery"), "delivery__order__warehouse__branch"),
    read_roles=VIEWERS, write_roles=INVOICERS, scopes=SCOPES,
    panel={"list": "sales:invoices", "detail": "sales:invoice_detail", "lines": "sales:invoice_action",
           "action": "sales:invoice_action"},
    date_field="invoice_date", party_field="customer", reference_field="delivery", parent_field="invoice",
    search=("number", "customer__name", "delivery__number"), example_number="INV-2026-00017",
    actions=[
        Action("issue", "Issue an invoice", "It is sent; the due date follows the customer's terms.",
               ["Status: issued; sets the due date."], "Only a draft.",
               lambda r, c, pk, d: deliveries.issue_invoice(actor=r.user, company_id=c, invoice_id=pk),
               INVOICERS, statuses=("draft",)),
        Action("paid", "Mark an invoice paid", "The date and reference of the payment.", ["Status: paid."],
               "Only an issued invoice.",
               lambda r, c, pk, d: deliveries.mark_paid(actor=r.user, company_id=c, invoice_id=pk,
                                                        paid_on=d["paid_on"], note=d.get("note", "")),
               INVOICERS, body=PaidSerializer, body_example={"paid_on": "2026-11-10", "note": "bKash 8A77X"},
               statuses=("issued",)),
        Action("cancel", "Cancel an invoice", "Then the delivery can be reversed, or invoiced again.",
               ["Status: cancelled."], "A draft or issued invoice; the reason is kept.",
               lambda r, c, pk, d: deliveries.cancel_invoice(actor=r.user, company_id=c, invoice_id=pk,
                                                             reason=d.get("reason", "")),
               INVOICERS, body=InventoryReasonSerializer, body_example={"reason": "Wrong price"},
               statuses=("draft", "issued")),
    ],
)

RETURNS = DocSpec(
    key="sales-returns", area="sales", path="sales/returns", title="Sales returns", singular="sales return",
    about="Goods back from a customer, at the cost they went out at: good goods to the store, damaged ones to the "
          "branch's Rejected store.",
    model=SalesReturn, get=deliveries.get_return, view_code=VIEW,
    queryset=_scoped(SalesReturn, ("customer", "warehouse", "delivery"), "warehouse__branch"),
    read_roles=VIEWERS, write_roles=DELIVERERS, scopes=SCOPES,
    panel={"list": "sales:returns", "detail": "sales:return_detail", "create": "sales:returns",
           "lines": "sales:return_line_add", "action": "sales:return_action"},
    date_field="return_date", store_field="warehouse", party_field="customer", reference_field="delivery",
    parent_field="sales_return", search=("number", "customer__name", "reason"), example_number="SRT-2026-00003",
    create_form=f.ReturnForm, create_form_kwargs=_sales_branches,
    create_call=lambda actor, cid, values: deliveries.create_return(actor=actor, company_id=cid, values=values),
    create_example={"delivery_id": 18, "return_date": "2026-10-21", "reason": "Leaking bottles"},
    line_form=f.ReturnLineForm, line_form_kwargs=lambda r, c, row: {"document": row},
    add_line=lambda actor, cid, pk, values: deliveries.add_return_line(actor=actor, company_id=cid, return_id=pk,
                                                                       values=values),
    line_example={"delivery_line_id": 71, "condition": "damaged", "qty": "4", "uom_id": 4},
    remove_line=lambda actor, cid, pk, line_id: deliveries.remove_return_line(actor=actor, company_id=cid,
                                                                              return_id=pk, line_id=line_id),
    actions=[
        Action("post", "Post a sales return", "The goods come back.",
               ["Good goods to the store, damaged to the Rejected store, at the delivery's cost."],
               "Needs at least one line.",
               lambda r, c, pk, d: deliveries.post_return(actor=r.user, company_id=c, return_id=pk),
               DELIVERERS, statuses=("draft",)),
        Action("cancel", "Cancel a sales return", "Drops the draft.", ["Status: cancelled."], "Only a draft.",
               lambda r, c, pk, d: deliveries.cancel_return(actor=r.user, company_id=c, return_id=pk),
               DELIVERERS, statuses=("draft",)),
        Action("reverse", "Reverse a sales return", "Takes the goods back out.", ["Status: reversed."],
               "The reason is kept.",
               lambda r, c, pk, d: deliveries.reverse_return(actor=r.user, company_id=c, return_id=pk,
                                                             reason=d.get("reason", "")),
               DELIVERERS, body=InventoryReasonSerializer, body_example=REASON, statuses=("posted",)),
    ],
)

# Actions that answer another document.
_DELIVER = next(a for a in ORDERS.actions if a.name == "deliver")
_DELIVER.response_build = lambda result: {"made": [document_out(DELIVERIES, d) for d in result[0]],
                                          "skipped": result[1]}
_DELIVER.response_example = {"made": [document_example(DELIVERIES)], "skipped": []}
_INVOICE = next(a for a in DELIVERIES.actions if a.name == "invoice")
_INVOICE.response = InventoryDocumentSerializer
_INVOICE.response_build = lambda invoice: document_out(INVOICES, invoice)
_INVOICE.response_example = document_example(INVOICES)

SPECS = (ORDERS, DELIVERIES, INVOICES, RETURNS)


# --------------------------------------------------------------------------
# Order intake: the client's other systems send orders in
# --------------------------------------------------------------------------

class IntakeLineSerializer(StrictSerializer):
    item_code = serializers.CharField(max_length=64, help_text="The item's code, e.g. FG-0001.")
    qty = serializers.DecimalField(max_digits=18, decimal_places=4, help_text="How many, in unit.")
    unit = serializers.CharField(max_length=16, required=False, allow_blank=True,
                                 help_text="A unit code of the item, e.g. CARTON; left out: its base unit.")
    unit_price = serializers.DecimalField(max_digits=18, decimal_places=4, required=False, allow_null=True,
                                          help_text="Left out: the customer's price list, else the item's sale price.")
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, help_text="A note for the line.")


class IntakeSerializer(StrictSerializer):
    customer_code = serializers.CharField(max_length=32, required=False, allow_blank=True,
                                          help_text="The customer's code in this software, e.g. CUS-0012. Needed with "
                                                    "lines.")
    customer_ref = serializers.CharField(max_length=64, required=False, allow_blank=True,
                                         help_text="Your own order number. An order with the same customer and number "
                                                   "is not taken twice (conflict).")
    order_date = serializers.DateField(required=False, help_text="YYYY-MM-DD; left out: today.")
    delivery_date = serializers.DateField(required=False, allow_null=True, help_text="Deliver by (YYYY-MM-DD).")
    delivery_address = serializers.CharField(max_length=500, required=False, allow_blank=True,
                                             help_text="Left out: the customer's address.")
    note = serializers.CharField(max_length=500, required=False, allow_blank=True, help_text="A note for the order.")
    lines = IntakeLineSerializer(many=True, required=False, help_text="What is ordered. Give lines, or text.")
    text = serializers.CharField(max_length=20000, required=False, allow_blank=True,
                                 help_text="Instead of lines: the order as free text (Bangla or English). It goes to "
                                           "the AI order inbox, which drafts the order for a person to confirm.")
    sender = serializers.CharField(max_length=255, required=False, allow_blank=True,
                                   help_text="With text: who sent it (name, phone or email), to find the customer.")


class IntakeResultSerializer(serializers.Serializer):
    kind = serializers.CharField(help_text="order (a draft sales order was made) or message (the text went to the AI "
                                           "order inbox).")
    order = InventoryDocumentSerializer(allow_null=True, help_text="The draft sales order, when one was made.")
    message_id = serializers.IntegerField(allow_null=True, help_text="The AI inbox message, for text.")
    message_status = serializers.CharField(allow_null=True, help_text="Its status: needs_review, waiting, failed ...")
    problems = serializers.ListField(child=serializers.CharField(), help_text="What a person should check.")


def _intake_order(request, data):
    from ai_assistant.intake import _order_store, _sellable
    from inventory.doc_common import local_today
    from inventory.masters import units_for
    from inventory.models import Item, Uom
    from sales.models import Customer

    company_id = request.company_id
    customer = Customer.objects.filter(code=(data.get("customer_code") or "").strip().upper(), status="active").first()
    if customer is None:
        raise ApiError("validation_error", None, {"customer_code": ["No active customer with this code."]})
    ref = (data.get("customer_ref") or "").strip()
    if ref:
        twin = SalesOrder.objects.filter(customer=customer, customer_ref=ref).exclude(status="cancelled").first()
        if twin is not None:
            raise ApiError("conflict", f"This order is already in as {twin.number}.")
    items, lines, fields = [], [], {}
    for index, line in enumerate(data["lines"]):
        item = Item.objects.filter(code=line["item_code"].strip().upper(), status="active").select_related(
            "base_uom", "default_warehouse").first()
        if item is None:
            fields[f"lines[{index}].item_code"] = ["No active item with this code."]
            continue
        uom = item.base_uom
        if line.get("unit"):
            uom = Uom.objects.filter(code=line["unit"].strip().upper(), pk__in=units_for(item)).first()
            if uom is None:
                fields[f"lines[{index}].unit"] = [f"Not a unit of {item.code}."]
                continue
        items.append(item)
        lines.append((item, uom, line))
    if fields:
        raise ApiError("validation_error", None, fields)
    store = _order_store(company_id, items)
    if store is None:
        raise ApiError("validation_error", "No store goods are sold from was found.")
    today = local_today(customer.company)
    with service_errors():
        order = orders.create_order(actor=request.user, company_id=company_id, values={
            "customer": customer, "warehouse": store, "order_date": data.get("order_date") or today,
            "delivery_date": data.get("delivery_date"), "source": SalesOrder.Source.API, "customer_ref": ref,
            "delivery_address": data.get("delivery_address", ""), "note": data.get("note", "")})
        for item, uom, line in lines:
            values = {"item": item, "warehouse": item.default_warehouse if _sellable(item.default_warehouse) else store,
                      "qty": line["qty"], "uom": uom, "note": line.get("note", "")}
            if line.get("unit_price") is not None:
                values["unit_price"] = line["unit_price"]
            orders.add_line(actor=request.user, company_id=company_id, order_id=order.pk, values=values)
    return order


@endpoint(id="sales-intake", area="sales", title="Send an order in",
          summary="An order from another system: a draft sales order, or text for the AI order inbox.",
          what_it_does=["With lines: makes a draft sales order (customer and items by code, prices from the "
                        "customer's price list) - a person confirms it in the panel or with …/confirm.",
                        "With text: saves it in the AI order inbox, which drafts the order when the assistant is on."],
          description=("For the client's other systems (web shop, distributor app, ERP). Each line goes from its "
                       "item's usual store. Send customer_ref with Idempotency-Key: the same order is never taken "
                       "twice. Nothing is confirmed or reserved here. " + MODULE_NOTE),
          roles=SELLERS, scopes=["sales:write"], request=IntakeSerializer, response=IntakeResultSerializer,
          response_status=201,
          request_example={"customer_code": "CUS-0012", "customer_ref": "WEB-10045", "delivery_date": "2026-10-20",
                           "lines": [{"item_code": "FG-0001", "qty": "20", "unit": "CARTON"},
                                     {"item_code": "RM-0001", "qty": "200", "unit": "KG"}]},
          response_example={"kind": "order", "order": document_example(ORDERS, status="draft"), "message_id": None,
                            "message_status": None, "problems": []},
          errors=WRITE_ERRORS + ["conflict"])
def _intake_post(self, request):
    from ai_assistant import intake
    from ai_assistant.models import InboundMessage

    data = validated(IntakeSerializer, request)
    if data.get("lines"):
        order = _intake_order(request, data)
        _m, order = orders.get_order(actor=request.user, company_id=request.company_id, pk=order.pk, code=VIEW)
        problems = []
        over, message = orders.credit_check(order)
        if over:
            problems.append(message)
        return Response(IntakeResultSerializer({"kind": "order", "order": document_out(ORDERS, order),
                                                "message_id": None, "message_status": None,
                                                "problems": problems}).data, status=201)
    if not (data.get("text") or "").strip():
        raise ApiError("validation_error", None, {"lines": ["Give lines, or the order as text."]})
    access.require(request.user, request.company_id, "sales.order")
    with service_errors():
        message = intake.receive(company_id=request.company_id, channel=InboundMessage.Channel.API,
                                 body=data["text"], sender=data.get("sender", ""),
                                 subject=f"API order {data.get('customer_ref', '')}".strip(), actor=request.user)
        message = intake.process(message)
    order = None
    if message.sales_order_id:
        order = document_out(ORDERS, SalesOrder.objects.get(pk=message.sales_order_id))
    return Response(IntakeResultSerializer({"kind": "message", "order": order, "message_id": message.pk,
                                            "message_status": message.status,
                                            "problems": list(message.problems)}).data, status=201)


IntakeView = view_class("SalesIntakeView", post=_intake_post, panel_page="sales:orders",
                        read_scope="sales:read", write_scope="sales:write")
