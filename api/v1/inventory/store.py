"""Store documents: requisitions, issues, material returns, transfers,
adjustments, counts and opening stock (the panel's Inventory menu)."""

from rest_framework import serializers

from api.core.serializers import StrictSerializer
from api.v1.inventory.engine import Action, DocSpec, InventoryNoteSerializer, InventoryReasonSerializer
from inventory import access, adjustments, issues, opening, transfers
from inventory import store_forms as f
from inventory.forms import OpeningLineForm, OpeningStockForm
from inventory.models import (
    MaterialReturn,
    OpeningStock,
    StockAdjustment,
    StockCount,
    StockTransfer,
    StoreIssue,
    StoreRequisition,
)
from inventory.views import branch_ids

SCOPES = ("inventory:read", "inventory:write")
VIEWERS = ["Anyone with View stock (or the document's own permission) in the store's branch; owner and "
           "administrator everywhere"]
ISSUERS = ["Store requisitions, issues and returns (inventory.issue) in the store's branch"]
APPROVERS = ["Approve store requisitions (inventory.issue_approve) - not their own"]
MOVERS = ["Transfer stock between stores (inventory.transfer): the sending store's branch to send, the "
          "receiving store's to receive"]
ADJUSTERS = ["Enter stock adjustments, counts and opening stock (inventory.adjust) in the store's branch"]
ADJUST_APPROVERS = ["Approve and post adjustments and opening stock (inventory.adjust_approve) - not their own"]


def _scoped(model, related, path):
    def rows(request, company_id):
        return access.scope_warehouses(model.objects.select_related(*related), request.user, company_id,
                                       "inventory.view", field=path)
    return rows


def _branches(code):
    return lambda request, company_id: {"branch_ids": branch_ids(request.user, company_id, code)}


def _panel(key):
    return {"list": f"inventory:store_{key}", "detail": f"inventory:store_{key}_detail",
            "create": f"inventory:store_{key}", "lines": "inventory:store_line_add",
            "action": "inventory:store_action"}


def _reason(data):
    return data.get("reason", "")


def _note(data):
    return data.get("note", "")


REASON = {"reason": "Booked twice by mistake"}
NOTE = {"note": "Not needed this week"}

REQUISITIONS = DocSpec(
    key="store-requisitions", area="inventory", path="inventory/requisitions", title="Store requisitions",
    singular="store requisition",
    about="A department asks a store for materials; once approved by someone else the store issues it, first "
          "expiry first.",
    model=StoreRequisition, get=issues.get_requisition, view_code="inventory.view",
    queryset=_scoped(StoreRequisition, ("department", "warehouse"), "department__branch"),
    read_roles=VIEWERS, write_roles=ISSUERS, scopes=SCOPES, panel=_panel("requisitions"),
    date_field="request_date", store_field="warehouse", party_field="department", parent_field="requisition",
    search=("number", "purpose", "department__name"), example_number="SR-2026-00004",
    create_form=f.RequisitionForm, create_form_kwargs=_branches("inventory.issue"),
    create_call=lambda actor, cid, values: issues.create_requisition(actor=actor, company_id=cid, values=values),
    create_example={"department_id": 2, "warehouse_id": 3, "request_date": "2026-10-16",
                    "needed_by": "2026-10-18", "purpose": "Line 2 production"},
    line_form=f.RequisitionLineForm,
    add_line=lambda actor, cid, pk, values: issues.add_requisition_line(actor=actor, company_id=cid,
                                                                        requisition_id=pk, values=values),
    line_example={"item_id": 7, "qty": "50", "uom_id": 1, "note": ""},
    remove_line=lambda actor, cid, pk, line_id: issues.remove_requisition_line(actor=actor, company_id=cid,
                                                                               requisition_id=pk, line_id=line_id),
    actions=[
        Action("submit", "Send a requisition for approval", "The approver sees it; it cannot change while it waits.",
               ["Status: waiting for approval."], "Needs at least one line.",
               lambda r, c, pk, d: issues.submit_requisition(actor=r.user, company_id=c, requisition_id=pk),
               ISSUERS, statuses=("draft",)),
        Action("approve", "Approve a requisition", "The store can then issue it.", ["Status: approved."],
               "Not your own requisition (unless owner or administrator).",
               lambda r, c, pk, d: issues.decide_requisition(actor=r.user, company_id=c, requisition_id=pk,
                                                             approve=True),
               APPROVERS, statuses=("submitted",)),
        Action("reject", "Reject a requisition", "Sends it back with a note.", ["Status: rejected."],
               "The note says why.",
               lambda r, c, pk, d: issues.decide_requisition(actor=r.user, company_id=c, requisition_id=pk,
                                                             approve=False, note=_note(d)),
               APPROVERS, body=InventoryNoteSerializer, body_example=NOTE, statuses=("submitted",)),
        Action("cancel", "Cancel a requisition", "Drops it while nothing is issued.", ["Status: cancelled."],
               "Not once something was issued: close it instead.",
               lambda r, c, pk, d: issues.cancel_requisition(actor=r.user, company_id=c, requisition_id=pk),
               ISSUERS, statuses=("draft", "submitted", "approved", "rejected")),
        Action("close", "Close a requisition", "The rest will not be issued.", ["Status: closed."],
               "For a partly issued requisition; the note says why.",
               lambda r, c, pk, d: issues.close_requisition(actor=r.user, company_id=c, requisition_id=pk,
                                                            note=_note(d)),
               ISSUERS, body=InventoryNoteSerializer, body_example=NOTE, statuses=("partly_issued",)),
    ],
)

ISSUES = DocSpec(
    key="store-issues", area="inventory", path="inventory/issues", title="Store issues", singular="store issue",
    about="Goods out of a store to a department. Batches are taken first expiry first; expired and quarantined "
          "stock is refused. Started from an approved requisition it is filled from it.",
    model=StoreIssue, get=issues.get_issue, view_code="inventory.view",
    queryset=_scoped(StoreIssue, ("department", "warehouse", "requisition"), "warehouse__branch"),
    read_roles=VIEWERS, write_roles=ISSUERS, scopes=SCOPES, panel=_panel("issues"),
    date_field="issue_date", store_field="warehouse", party_field="department", reference_field="requisition",
    parent_field="issue", search=("number", "department__name", "requisition__number", "received_by"),
    example_number="SI-2026-00012",
    create_form=f.IssueForm, create_form_kwargs=_branches("inventory.issue"),
    create_call=lambda actor, cid, values: issues.create_issue(actor=actor, company_id=cid, values=values),
    create_example={"requisition_id": 4, "issue_date": "2026-10-16", "received_by": "Karim (Line 2)"},
    create_description="Give requisition_id to issue an approved requisition (store and department come from "
                       "it), or warehouse_id and department_id for an issue without one.",
    line_form=f.IssueLineForm, line_form_kwargs=lambda r, c, row: {"issue": row},
    add_line=lambda actor, cid, pk, values: issues.add_issue_line(actor=actor, company_id=cid, issue_id=pk,
                                                                  values=values),
    line_example={"item_id": 7, "qty": "20", "uom_id": 1},
    line_description="Without batch_id the quantity is split over batches, first expiry first. Another batch "
                     "than the first to expire needs not_fefo_reason.",
    remove_line=lambda actor, cid, pk, line_id: issues.remove_issue_line(actor=actor, company_id=cid, issue_id=pk,
                                                                         line_id=line_id),
    scan=lambda actor, cid, pk, code: issues.scan_into_issue(actor=actor, company_id=cid, issue_id=pk, code=code),
    actions=[
        Action("post", "Post a store issue", "The goods leave the store.",
               ["Takes the stock out at its average cost; updates the requisition."],
               "Refused if a batch has run out, expired or is quarantined, or the month is closed.",
               lambda r, c, pk, d: issues.post_issue(actor=r.user, company_id=c, issue_id=pk),
               ISSUERS, statuses=("draft",)),
        Action("cancel", "Cancel a store issue", "Drops the draft.", ["Status: cancelled."], "Only a draft.",
               lambda r, c, pk, d: issues.cancel_issue(actor=r.user, company_id=c, issue_id=pk),
               ISSUERS, statuses=("draft",)),
        Action("reverse", "Reverse a store issue", "Brings the goods back with counter-moves dated today.",
               ["Status: reversed; the requisition's issued quantities go back."], "The reason is kept.",
               lambda r, c, pk, d: issues.reverse_issue(actor=r.user, company_id=c, issue_id=pk, reason=_reason(d)),
               ISSUERS, body=InventoryReasonSerializer, body_example=REASON, statuses=("posted",)),
    ],
)

RETURNS = DocSpec(
    key="material-returns", area="inventory", path="inventory/returns", title="Material returns",
    singular="material return",
    about="Goods back to a store from a department or production. A line from an issue comes back at that "
          "issue's cost; damaged goods go to the branch's Rejected store.",
    model=MaterialReturn, get=issues.get_return, view_code="inventory.view",
    queryset=_scoped(MaterialReturn, ("department", "warehouse", "issue"), "warehouse__branch"),
    read_roles=VIEWERS, write_roles=ISSUERS, scopes=SCOPES, panel=_panel("returns"),
    date_field="return_date", store_field="warehouse", party_field="department", reference_field="issue",
    parent_field="material_return", search=("number", "department__name", "issue__number"),
    example_number="MR-2026-00003",
    create_form=f.ReturnForm, create_form_kwargs=_branches("inventory.issue"),
    create_call=lambda actor, cid, values: issues.create_return(actor=actor, company_id=cid, values=values),
    create_example={"issue_id": 12, "return_date": "2026-10-17", "note": "Left over"},
    line_form=f.ReturnLineForm, line_form_kwargs=lambda r, c, row: {"document": row},
    add_line=lambda actor, cid, pk, values: issues.add_return_line(actor=actor, company_id=cid, return_id=pk,
                                                                   values=values),
    line_example={"issue_line_id": 41, "condition": "good", "qty": "5", "uom_id": 1},
    remove_line=lambda actor, cid, pk, line_id: issues.remove_return_line(actor=actor, company_id=cid,
                                                                          return_id=pk, line_id=line_id),
    actions=[
        Action("post", "Post a material return", "The goods come back into stock.",
               ["Good goods to the store, damaged to the Rejected store."], "At the issue's cost when from an issue.",
               lambda r, c, pk, d: issues.post_return(actor=r.user, company_id=c, return_id=pk),
               ISSUERS, statuses=("draft",)),
        Action("cancel", "Cancel a material return", "Drops the draft.", ["Status: cancelled."], "Only a draft.",
               lambda r, c, pk, d: issues.cancel_return(actor=r.user, company_id=c, return_id=pk),
               ISSUERS, statuses=("draft",)),
        Action("reverse", "Reverse a material return", "Takes the goods back out.", ["Status: reversed."],
               "The reason is kept.",
               lambda r, c, pk, d: issues.reverse_return(actor=r.user, company_id=c, return_id=pk, reason=_reason(d)),
               ISSUERS, body=InventoryReasonSerializer, body_example=REASON, statuses=("posted",)),
    ],
)


class ReceivedLineSerializer(StrictSerializer):
    line_id = serializers.IntegerField(help_text="The transfer line (lines[].id).")
    qty_received_base = serializers.DecimalField(max_digits=18, decimal_places=4,
                                                 help_text="How much arrived, in the item's base unit.")
    shortage_reason = serializers.CharField(max_length=255, required=False, allow_blank=True,
                                            help_text="Why the rest did not arrive (needed for any shortage).")


class ReceiveSerializer(StrictSerializer):
    received_date = serializers.DateField(help_text="The day it arrived (YYYY-MM-DD), not before it was sent.")
    lines = ReceivedLineSerializer(many=True, required=False,
                                   help_text="Only lines where less arrived; left out: everything arrived.")


def _receive(r, c, pk, d):
    lines = d.get("lines") or []
    return transfers.receive(actor=r.user, company_id=c, transfer_id=pk, received_date=d["received_date"],
                             received={x["line_id"]: x["qty_received_base"] for x in lines},
                             reasons={x["line_id"]: x.get("shortage_reason", "") for x in lines})


TRANSFERS = DocSpec(
    key="stock-transfers", area="inventory", path="inventory/transfers", title="Transfers", singular="transfer",
    about="Stock moved between stores: dispatched (in transit, not usable), then received by the other store. "
          "Shortages are written off with their reason.",
    model=StockTransfer, get=transfers.get_transfer, view_code="inventory.view",
    queryset=_scoped(StockTransfer, ("from_warehouse", "to_warehouse"), "from_warehouse__branch"),
    read_roles=VIEWERS, write_roles=MOVERS, scopes=SCOPES, panel=_panel("transfers"),
    date_field="transfer_date", store_field="from_warehouse", other_store_field="to_warehouse",
    parent_field="transfer", search=("number", "from_warehouse__code", "to_warehouse__code", "vehicle_no"),
    example_number="TR-2026-00002",
    create_form=f.TransferForm, create_form_kwargs=_branches("inventory.transfer"),
    create_call=lambda actor, cid, values: transfers.create_transfer(actor=actor, company_id=cid, values=values),
    create_example={"from_warehouse_id": 3, "to_warehouse_id": 5, "transfer_date": "2026-10-16",
                    "vehicle_no": "DHA-11-2233"},
    line_form=f.TransferLineForm, line_form_kwargs=lambda r, c, row: {"transfer": row},
    add_line=lambda actor, cid, pk, values: transfers.add_line(actor=actor, company_id=cid, transfer_id=pk,
                                                               values=values),
    line_example={"item_id": 7, "qty": "100", "uom_id": 1},
    remove_line=lambda actor, cid, pk, line_id: transfers.remove_line(actor=actor, company_id=cid, transfer_id=pk,
                                                                      line_id=line_id),
    scan=lambda actor, cid, pk, code: transfers.scan_into_transfer(actor=actor, company_id=cid, transfer_id=pk,
                                                                   code=code),
    actions=[
        Action("dispatch", "Dispatch a transfer", "The goods leave: in transit, not usable on the way.",
               ["Moves the stock to the branch's transit store."], "Needs at least one line.",
               lambda r, c, pk, d: transfers.dispatch(actor=r.user, company_id=c, transfer_id=pk),
               MOVERS, statuses=("draft",)),
        Action("receive", "Receive a transfer", "The other store takes the goods in.",
               ["Moves what arrived into the receiving store; writes off the rest with its reason."],
               "By the receiving store's branch. Everything arrived unless lines say less.",
               _receive, MOVERS, body=ReceiveSerializer,
               body_example={"received_date": "2026-10-17",
                             "lines": [{"line_id": 41, "qty_received_base": "98", "shortage_reason": "2 bags torn"}]},
               statuses=("in_transit",), panel="inventory:transfer_receive"),
        Action("cancel", "Cancel a transfer", "Drops the draft.", ["Status: cancelled."], "Only a draft.",
               lambda r, c, pk, d: transfers.cancel_transfer(actor=r.user, company_id=c, transfer_id=pk),
               MOVERS, statuses=("draft",)),
        Action("reverse", "Reverse a transfer", "Everything goes back to the sending store.", ["Status: reversed."],
               "The reason is kept.",
               lambda r, c, pk, d: transfers.reverse_transfer(actor=r.user, company_id=c, transfer_id=pk,
                                                              reason=_reason(d)),
               MOVERS, body=InventoryReasonSerializer, body_example=REASON, statuses=("in_transit", "received")),
    ],
)

ADJUSTMENTS = DocSpec(
    key="stock-adjustments", area="inventory", path="inventory/adjustments", title="Stock adjustments",
    singular="stock adjustment",
    about="Stock written off or found, with a reason. Someone else approves; approving posts it.",
    model=StockAdjustment, get=adjustments.get_adjustment, view_code="inventory.view",
    queryset=_scoped(StockAdjustment, ("warehouse", "count"), "warehouse__branch"),
    read_roles=VIEWERS, write_roles=ADJUSTERS, scopes=SCOPES, panel=_panel("adjustments"),
    date_field="adjustment_date", store_field="warehouse", reference_field="count", parent_field="adjustment",
    search=("number", "reason", "note"), example_number="ADJ-2026-00001",
    create_form=f.AdjustmentForm, create_form_kwargs=_branches("inventory.adjust"),
    create_call=lambda actor, cid, values: adjustments.create_adjustment(actor=actor, company_id=cid, values=values),
    create_example={"warehouse_id": 3, "adjustment_date": "2026-10-16", "reason": "damaged",
                    "note": "Rain leak"},
    line_form=f.AdjustmentLineForm, line_form_kwargs=lambda r, c, row: {"adjustment": row},
    add_line=lambda actor, cid, pk, values: adjustments.add_line(actor=actor, company_id=cid, adjustment_id=pk,
                                                                 values=values),
    line_example={"item_id": 7, "batch_id": 18, "qty_change": "-5", "uom_id": 1},
    line_description="qty_change below zero writes stock off; above zero adds found stock (give its cost).",
    remove_line=lambda actor, cid, pk, line_id: adjustments.remove_line(actor=actor, company_id=cid,
                                                                        adjustment_id=pk, line_id=line_id),
    actions=[
        Action("submit", "Send an adjustment for approval", "It waits for someone else.",
               ["Status: waiting for approval."], "Needs at least one line.",
               lambda r, c, pk, d: adjustments.submit(actor=r.user, company_id=c, adjustment_id=pk),
               ADJUSTERS, statuses=("draft",)),
        Action("approve", "Approve an adjustment", "Approving posts it.", ["Posts the stock change."],
               "Not your own (unless owner or administrator).",
               lambda r, c, pk, d: adjustments.decide(actor=r.user, company_id=c, adjustment_id=pk, approve=True),
               ADJUST_APPROVERS, statuses=("submitted",)),
        Action("reject", "Reject an adjustment", "Sends it back with a note.", ["Status: rejected."], "The note says why.",
               lambda r, c, pk, d: adjustments.decide(actor=r.user, company_id=c, adjustment_id=pk, approve=False,
                                                      note=_note(d)),
               ADJUST_APPROVERS, body=InventoryNoteSerializer, body_example=NOTE, statuses=("submitted",)),
        Action("cancel", "Cancel an adjustment", "Drops it.", ["Status: cancelled."], "Before it is posted.",
               lambda r, c, pk, d: adjustments.cancel_adjustment(actor=r.user, company_id=c, adjustment_id=pk),
               ADJUSTERS, statuses=("draft", "submitted", "rejected")),
        Action("reverse", "Reverse an adjustment", "Undoes the posted change.", ["Status: reversed."],
               "The reason is kept.",
               lambda r, c, pk, d: adjustments.reverse_adjustment(actor=r.user, company_id=c, adjustment_id=pk,
                                                                  reason=_reason(d)),
               ADJUST_APPROVERS, body=InventoryReasonSerializer, body_example=REASON, statuses=("posted",)),
    ],
)


class CountedQuantityLineSerializer(StrictSerializer):
    line_id = serializers.IntegerField(help_text="The count line (lines[].id).")
    counted_qty = serializers.DecimalField(max_digits=18, decimal_places=4, allow_null=True,
                                           help_text="What was counted, in the item's base unit; null: not counted.")


class CountedQuantitiesSerializer(StrictSerializer):
    lines = CountedQuantityLineSerializer(many=True, help_text="The lines counted.")


COUNTS = DocSpec(
    key="stock-counts", area="inventory", path="inventory/counts", title="Stock counts", singular="stock count",
    about="Count a store: the software's quantities are frozen when it starts; enter what was counted (or scan "
          "labels); approving turns every difference into a posted adjustment.",
    model=StockCount, get=adjustments.get_count, view_code="inventory.view",
    queryset=_scoped(StockCount, ("warehouse",), "warehouse__branch"),
    read_roles=VIEWERS, write_roles=ADJUSTERS, scopes=SCOPES, panel=_panel("counts"),
    date_field="count_date", store_field="warehouse", parent_field="count", search=("number", "note"),
    example_number="CNT-2026-00001",
    create_form=f.CountForm, create_form_kwargs=_branches("inventory.adjust"),
    create_call=lambda actor, cid, values: adjustments.start_count(actor=actor, company_id=cid, values=values),
    create_example={"warehouse_id": 3, "count_date": "2026-10-31", "note": "Month end"},
    line_form=f.FoundForm, line_form_kwargs=lambda r, c, row: {"count": row},
    add_line=lambda actor, cid, pk, values: adjustments.add_found(actor=actor, company_id=cid, count_id=pk,
                                                                  values=values),
    line_example={"item_id": 9, "counted_qty": "12"},
    line_description="For stock found that the count had no line for.",
    scan=lambda actor, cid, pk, code: adjustments.scan_into_count(actor=actor, company_id=cid, count_id=pk,
                                                                  code=code),
    actions=[
        Action("counted", "Enter counted quantities", "What was counted, line by line.",
               ["Saves the counted quantities (base units)."], "While counting.",
               lambda r, c, pk, d: adjustments.set_counted(actor=r.user, company_id=c, count_id=pk, counted={
                   x["line_id"]: x["counted_qty"] for x in d["lines"]}),
               ADJUSTERS, body=CountedQuantitiesSerializer, body_example={"lines": [{"line_id": 41, "counted_qty": "48"}]},
               statuses=("counting",), panel="inventory:count_save"),
        Action("zero", "Set uncounted lines to zero", "Everything not counted counts as none.",
               ["Every line without a count gets 0."], "While counting.",
               lambda r, c, pk, d: adjustments.zero_uncounted(actor=r.user, company_id=c, count_id=pk),
               ADJUSTERS, statuses=("counting",)),
        Action("submit", "Send a count for approval", "Every line must be counted.", ["Status: waiting for approval."],
               "Lines without a count are refused.",
               lambda r, c, pk, d: adjustments.submit_count(actor=r.user, company_id=c, count_id=pk),
               ADJUSTERS, statuses=("counting",)),
        Action("approve", "Approve a count", "Posts every difference as an adjustment.",
               ["Makes and posts the adjustment for the differences."], "Not your own (unless owner or administrator).",
               lambda r, c, pk, d: adjustments.approve_count(actor=r.user, company_id=c, count_id=pk),
               ADJUST_APPROVERS, statuses=("submitted",)),
        Action("cancel", "Cancel a count", "Drops it.", ["Status: cancelled."], "Before it is approved.",
               lambda r, c, pk, d: adjustments.cancel_count(actor=r.user, company_id=c, count_id=pk),
               ADJUSTERS, statuses=("counting", "submitted")),
    ],
)

OPENING = DocSpec(
    key="opening-stock", area="inventory", path="inventory/opening", title="Opening stock",
    singular="opening stock document",
    about="The stock counted on the opening day, line by line (store, item, batch, quantity, cost), then posted "
          "by someone else. Reversed with counter-moves if it was wrong.",
    model=OpeningStock, get=opening.get_document, view_code="inventory.view",
    queryset=lambda request, company_id: OpeningStock.objects.all(),
    read_roles=VIEWERS, write_roles=ADJUSTERS, scopes=SCOPES,
    panel={"list": "inventory:opening", "detail": "inventory:opening_detail", "create": "inventory:opening",
           "lines": "inventory:opening_line_add", "action": "inventory:opening_post"},
    date_field="work_date", parent_field="document", search=("number", "note"), example_number="OS-2026-00001",
    create_form=OpeningStockForm,
    create_call=lambda actor, cid, values: opening.create_document(actor=actor, company_id=cid, values=values),
    create_example={"work_date": "2026-11-01", "note": "Stock count day"},
    line_form=OpeningLineForm, line_form_kwargs=lambda r, c, row: {
        "branch_ids": branch_ids(r.user, c, "inventory.adjust")},
    add_line=lambda actor, cid, pk, values: opening.add_line(actor=actor, company_id=cid, document_id=pk,
                                                             values=values),
    line_example={"warehouse_id": 3, "item_id": 7, "batch_no": "S-261001", "expiry_date": "2027-09-30",
                  "qty": "500", "uom_id": 1, "unit_cost": "82.50"},
    remove_line=lambda actor, cid, pk, line_id: opening.remove_line(actor=actor, company_id=cid, document_id=pk,
                                                                    line_id=line_id),
    actions=[
        Action("post", "Post opening stock", "The stock is in.", ["Posts every line at its cost."],
               "By someone else than who entered it (unless owner or administrator).",
               lambda r, c, pk, d: opening.post_document(actor=r.user, company_id=c, document_id=pk),
               ADJUST_APPROVERS, statuses=("draft",)),
        Action("cancel", "Cancel opening stock", "Drops the draft.", ["Status: cancelled."], "Only a draft.",
               lambda r, c, pk, d: opening.cancel_document(actor=r.user, company_id=c, document_id=pk),
               ADJUSTERS, statuses=("draft",), panel="inventory:opening_cancel"),
        Action("reverse", "Reverse opening stock", "Takes it all back out.", ["Status: reversed."],
               "Refused once any of it was used. The reason is kept.",
               lambda r, c, pk, d: opening.reverse_document(actor=r.user, company_id=c, document_id=pk,
                                                            reason=_reason(d)),
               ADJUST_APPROVERS, body=InventoryReasonSerializer, body_example=REASON, statuses=("posted",),
               panel="inventory:opening_reverse"),
    ],
)

SPECS = (REQUISITIONS, ISSUES, RETURNS, TRANSFERS, ADJUSTMENTS, COUNTS, OPENING)
