"""Purchase: requisitions, orders, goods receipts (with quality check) and
returns to suppliers (the panel's Purchase menu)."""

from rest_framework import serializers

from api.core.serializers import StrictSerializer
from api.v1.inventory.engine import Action, DocSpec, InventoryNoteSerializer, InventoryReasonSerializer
from inventory import access
from inventory.views import branch_ids
from purchasing import forms as f
from purchasing import orders, receipts, requisitions, returns
from purchasing.models import GoodsReceipt, PurchaseOrder, PurchaseRequisition, PurchaseReturn, QualityCheck

SCOPES = ("purchase:read", "purchase:write")
VIEWERS = ["Anyone with a purchase permission (or View stock, for receipts) in the document's branch; owner and "
           "administrator everywhere"]
REQUESTERS = ["Raise purchase requisitions (purchase.request) in the branch"]
APPROVERS = ["Approve purchase requisitions, orders and returns (purchase.approve) - not their own"]
BUYERS = ["Make purchase orders and send them to suppliers (purchase.order) in the branch"]
RECEIVERS = ["Receive goods (GRN) and enter purchase returns (purchase.receive) in the store's branch"]
CHECKERS = ["Quality check received goods (purchase.qc) in the store's branch"]
REASON = {"reason": "Wrong items delivered"}
NOTE = {"note": "Supplier cannot deliver this month"}


def _any_of(model, related, codes, path="branch"):
    def rows(request, company_id):
        found = None
        for code in codes:
            part = access.scope_warehouses(model.objects.select_related(*related), request.user, company_id, code,
                                           field=path)
            found = part if found is None else found | part
        return found.distinct()
    return rows


def _branches(code):
    return lambda request, company_id: {"branch_ids": branch_ids(request.user, company_id, code)}


def _note(data):
    return data.get("note", "")


REQUISITIONS = DocSpec(
    key="purchase-requisitions", area="purchase", path="purchase/requisitions", title="Purchase requisitions",
    singular="purchase requisition",
    about="A branch or department asks for goods to be bought; approved by someone else, its lines become "
          "purchase orders.",
    model=PurchaseRequisition, get=requisitions.get_requisition, view_code=("purchase.request", "purchase.approve",
                                                                            "purchase.order"),
    queryset=_any_of(PurchaseRequisition, ("branch", "department"), ("purchase.request", "purchase.approve")),
    read_roles=VIEWERS, write_roles=REQUESTERS, scopes=SCOPES,
    panel={"list": "purchasing:requisitions", "detail": "purchasing:requisition_detail",
           "create": "purchasing:requisitions", "lines": "purchasing:requisition_line_add",
           "action": "purchasing:requisition_submit"},
    date_field="request_date", party_field="department", parent_field="requisition",
    search=("number", "reason", "department__name"), example_number="PR-2026-00006",
    create_form=f.RequisitionForm, create_form_kwargs=_branches("purchase.request"),
    create_call=lambda actor, cid, values: requisitions.create_requisition(actor=actor, company_id=cid,
                                                                           values=values),
    create_example={"branch_id": 1, "department_id": 2, "request_date": "2026-10-16", "needed_by": "2026-10-25",
                    "reason": "Sugar for November"},
    line_form=f.RequisitionLineForm,
    add_line=lambda actor, cid, pk, values: requisitions.add_line(actor=actor, company_id=cid, requisition_id=pk,
                                                                  values=values),
    line_example={"item_id": 7, "qty": "2000", "uom_id": 1},
    remove_line=lambda actor, cid, pk, line_id: requisitions.remove_line(actor=actor, company_id=cid,
                                                                         requisition_id=pk, line_id=line_id),
    actions=[
        Action("submit", "Send a purchase requisition for approval", "It waits for an approver.",
               ["Status: waiting for approval."], "Needs at least one line.",
               lambda r, c, pk, d: requisitions.submit(actor=r.user, company_id=c, requisition_id=pk),
               REQUESTERS, statuses=("draft",)),
        Action("approve", "Approve a purchase requisition", "Its lines can be ordered.", ["Status: approved."],
               "Not your own (unless owner or administrator).",
               lambda r, c, pk, d: requisitions.decide(actor=r.user, company_id=c, requisition_id=pk, approve=True),
               APPROVERS, statuses=("submitted",), panel="purchasing:requisition_decide"),
        Action("reject", "Reject a purchase requisition", "Sends it back with a note.", ["Status: rejected."],
               "The note says why.",
               lambda r, c, pk, d: requisitions.decide(actor=r.user, company_id=c, requisition_id=pk, approve=False,
                                                       note=_note(d)),
               APPROVERS, body=InventoryNoteSerializer, body_example=NOTE, statuses=("submitted",),
               panel="purchasing:requisition_decide"),
        Action("cancel", "Cancel a purchase requisition", "Drops it.", ["Status: cancelled."],
               "Not once ordered.",
               lambda r, c, pk, d: requisitions.cancel(actor=r.user, company_id=c, requisition_id=pk),
               REQUESTERS, statuses=("draft", "submitted", "approved", "rejected"),
               panel="purchasing:requisition_cancel"),
    ],
)

ORDERS = DocSpec(
    key="purchase-orders", area="purchase", path="purchase/orders", title="Purchase orders",
    singular="purchase order",
    about="An order to a supplier: lines with price, discount and VAT; approved by someone else, then received "
          "on goods receipts (partly or fully).",
    model=PurchaseOrder, get=orders.get_order, view_code=("purchase.order", "purchase.approve", "purchase.receive"),
    queryset=_any_of(PurchaseOrder, ("supplier", "branch"), ("purchase.order", "purchase.approve",
                                                             "purchase.receive")),
    read_roles=VIEWERS, write_roles=BUYERS, scopes=SCOPES,
    panel={"list": "purchasing:orders", "detail": "purchasing:order_detail", "create": "purchasing:orders",
           "lines": "purchasing:order_line_add", "action": "purchasing:order_action"},
    date_field="order_date", party_field="supplier", parent_field="order", totals=orders.totals,
    search=("number", "supplier__name", "supplier__code", "note"), example_number="PO-2026-00009",
    create_form=f.OrderForm, create_form_kwargs=_branches("purchase.order"),
    create_call=lambda actor, cid, values: orders.create_order(actor=actor, company_id=cid, values=values),
    create_example={"supplier_id": 4, "branch_id": 1, "order_date": "2026-10-16", "expected_date": "2026-10-23",
                    "terms": "30 days"},
    line_form=f.OrderLineForm,
    add_line=lambda actor, cid, pk, values: orders.add_line(actor=actor, company_id=cid, order_id=pk, values=values),
    line_example={"item_id": 7, "qty": "40", "uom_id": 3, "unit_price": "4100", "discount_pct": "0",
                  "vat_rate": "0"},
    remove_line=lambda actor, cid, pk, line_id: orders.remove_line(actor=actor, company_id=cid, order_id=pk,
                                                                   line_id=line_id),
    actions=[
        Action("submit", "Send a purchase order for approval", "It waits for an approver.",
               ["Status: waiting for approval."], "Needs at least one line.",
               lambda r, c, pk, d: orders.submit(actor=r.user, company_id=c, order_id=pk),
               BUYERS, statuses=("draft",)),
        Action("approve", "Approve a purchase order", "It can be sent and received.", ["Status: approved."],
               "Not your own (unless owner or administrator).",
               lambda r, c, pk, d: orders.approve(actor=r.user, company_id=c, order_id=pk),
               APPROVERS, statuses=("submitted",)),
        Action("send-back", "Send a purchase order back", "Back to draft with a note.", ["Status: draft."],
               "The note says what to change.",
               lambda r, c, pk, d: orders.send_back(actor=r.user, company_id=c, order_id=pk, note=_note(d)),
               APPROVERS, body=InventoryNoteSerializer, body_example={"note": "Price too high"}, statuses=("submitted",)),
        Action("cancel", "Cancel a purchase order", "Drops it before anything is received.", ["Status: cancelled."],
               "Not once goods came in: close it instead.",
               lambda r, c, pk, d: orders.cancel(actor=r.user, company_id=c, order_id=pk, note=_note(d)),
               BUYERS, body=InventoryNoteSerializer, body_example=NOTE, statuses=("draft", "submitted", "approved")),
        Action("close", "Close a purchase order", "The rest will not come.", ["Status: closed."],
               "For a partly received order; the note says why.",
               lambda r, c, pk, d: orders.close(actor=r.user, company_id=c, order_id=pk, note=_note(d)),
               BUYERS, body=InventoryNoteSerializer, body_example=NOTE, statuses=("partly_received",)),
    ],
)


class QcSerializer(StrictSerializer):
    line_id = serializers.IntegerField(help_text="The receipt line waiting for QC (lines[].id, "
                                                 "details.qc_status = pending).")
    result = serializers.ChoiceField(choices=QualityCheck.Result.values,
                                     help_text="passed (releases the batch) or failed (blocks it).")
    notes = serializers.CharField(max_length=500, required=False, allow_blank=True,
                                  help_text="Needed when it fails: what was wrong.")


RECEIPTS = DocSpec(
    key="goods-receipts", area="purchase", path="purchase/receipts", title="Goods receipts (GRN)",
    singular="goods receipt",
    about="Goods arriving from a supplier, against an order or without one: quantities received and rejected, "
          "batch and expiry. Items needing quality check go to quarantine until passed.",
    model=GoodsReceipt, get=receipts.get_receipt, view_code=("purchase.receive", "inventory.view", "purchase.qc"),
    queryset=lambda request, company_id: access.scope_warehouses(
        GoodsReceipt.objects.select_related("supplier", "warehouse", "order"), request.user, company_id,
        "inventory.view", field="warehouse__branch"),
    read_roles=VIEWERS, write_roles=RECEIVERS, scopes=SCOPES,
    panel={"list": "purchasing:receipts", "detail": "purchasing:receipt_detail", "create": "purchasing:receipts",
           "lines": "purchasing:receipt_line_add", "action": "purchasing:receipt_action"},
    date_field="received_date", store_field="warehouse", party_field="supplier", reference_field="order",
    parent_field="receipt", search=("number", "supplier__name", "challan_no", "invoice_no", "order__number"),
    example_number="GRN-2026-00015",
    create_form=f.ReceiptForm, create_form_kwargs=_branches("purchase.receive"),
    create_call=lambda actor, cid, values: receipts.create_receipt(actor=actor, company_id=cid, values=values),
    create_example={"order_id": 9, "warehouse_id": 3, "received_date": "2026-10-20", "challan_no": "CH-5521"},
    create_description="Against an order (order_id: its open lines are filled in) or without one (supplier_id).",
    line_form=f.ReceiptLineForm, line_form_kwargs=lambda r, c, row: {"receipt": row},
    add_line=lambda actor, cid, pk, values: receipts.add_line(actor=actor, company_id=cid, receipt_id=pk,
                                                              values=values),
    line_example={"item_id": 7, "qty_received": "40", "qty_rejected": "0", "uom_id": 3, "unit_cost": "4100",
                  "supplier_batch_no": "LOT-88", "expiry_date": "2027-09-30"},
    remove_line=lambda actor, cid, pk, line_id: receipts.remove_line(actor=actor, company_id=cid, receipt_id=pk,
                                                                     line_id=line_id),
    actions=[
        Action("post", "Post a goods receipt", "The goods are in stock.",
               ["Makes the batches, posts the stock at its cost, updates the order."],
               "Items needing QC go to the quarantine store until checked.",
               lambda r, c, pk, d: receipts.post_receipt(actor=r.user, company_id=c, receipt_id=pk),
               RECEIVERS, statuses=("draft",)),
        Action("qc", "Record a quality check", "Passed releases the line's batch; failed blocks it.",
               ["Records the check; passed moves the goods out of quarantine."],
               "Only for a posted line waiting for QC.",
               lambda r, c, pk, d: receipts.record_check(actor=r.user, company_id=c, line_id=d["line_id"],
                                                         result=d["result"], notes=d.get("notes", "")),
               CHECKERS, body=QcSerializer, body_example={"line_id": 52, "result": "passed", "notes": ""},
               statuses=("posted",), panel="purchasing:qc_record"),
        Action("cancel", "Cancel a goods receipt", "Drops the draft.", ["Status: cancelled."], "Only a draft.",
               lambda r, c, pk, d: receipts.cancel_receipt(actor=r.user, company_id=c, receipt_id=pk),
               RECEIVERS, statuses=("draft",)),
        Action("reverse", "Reverse a goods receipt", "Takes the goods back out.", ["Status: reversed."],
               "Refused once any of it was used. The reason is kept.",
               lambda r, c, pk, d: receipts.reverse_receipt(actor=r.user, company_id=c, receipt_id=pk,
                                                            reason=d.get("reason", "")),
               RECEIVERS, body=InventoryReasonSerializer, body_example=REASON, statuses=("posted",)),
    ],
)

RETURNS = DocSpec(
    key="purchase-returns", area="purchase", path="purchase/returns", title="Purchase returns",
    singular="purchase return",
    about="Goods sent back to a supplier - usually failed QC or damaged - at the cost they came in at.",
    model=PurchaseReturn, get=returns.get_return, view_code=("purchase.receive", "purchase.approve"),
    queryset=lambda request, company_id: PurchaseReturn.objects.select_related("supplier", "receipt"),
    read_roles=VIEWERS, write_roles=RECEIVERS, scopes=SCOPES,
    panel={"list": "purchasing:returns", "detail": "purchasing:return_detail", "create": "purchasing:returns",
           "lines": "purchasing:return_line_add", "action": "purchasing:return_action"},
    date_field="return_date", party_field="supplier", reference_field="receipt", parent_field="purchase_return",
    search=("number", "supplier__name", "reason"), example_number="PRT-2026-00002",
    create_form=f.ReturnForm,
    create_call=lambda actor, cid, values: returns.create_return(actor=actor, company_id=cid, values=values),
    create_example={"receipt_id": 15, "return_date": "2026-10-21", "reason": "Failed QC: moisture"},
    line_form=f.ReturnLineForm,
    line_form_kwargs=lambda r, c, row: {"returnable": returns.returnable_lines(c, row)},
    add_line=lambda actor, cid, pk, values: returns.add_line(actor=actor, company_id=cid, return_id=pk,
                                                             values=values),
    line_example={"receipt_line_id": 52, "qty": "2", "uom_id": 3},
    remove_line=lambda actor, cid, pk, line_id: returns.remove_line(actor=actor, company_id=cid, return_id=pk,
                                                                    line_id=line_id),
    actions=[
        Action("post", "Post a purchase return", "The goods leave for the supplier.",
               ["Takes the stock out at the cost it came in at."], "Needs at least one line.",
               lambda r, c, pk, d: returns.post_return(actor=r.user, company_id=c, return_id=pk),
               RECEIVERS, statuses=("draft",)),
        Action("cancel", "Cancel a purchase return", "Drops the draft.", ["Status: cancelled."], "Only a draft.",
               lambda r, c, pk, d: returns.cancel_return(actor=r.user, company_id=c, return_id=pk),
               RECEIVERS, statuses=("draft",)),
        Action("reverse", "Reverse a purchase return", "Brings the goods back.", ["Status: reversed."],
               "The reason is kept.",
               lambda r, c, pk, d: returns.reverse_return(actor=r.user, company_id=c, return_id=pk,
                                                          reason=d.get("reason", "")),
               RECEIVERS, body=InventoryReasonSerializer, body_example=REASON, statuses=("posted",)),
    ],
)

SPECS = (REQUISITIONS, ORDERS, RECEIPTS, RETURNS)
