"""Production: BOMs (recipes), production orders with their output and QC,
and the materials they use (the panel's Production menu)."""

from rest_framework import serializers

from api.core.forms import checked
from api.core.serializers import StrictSerializer
from api.v1.inventory.engine import (
    Action,
    DocSpec,
    InventoryDocumentSerializer,
    document_example,
    document_out,
    input_serializer,
    plain,
    ref,
    to_form,
)
from inventory import access
from inventory.views import branch_ids
from production import boms, orders
from production import forms as f
from production.models import Bom, Consumption, ProductionOrder

SCOPES = ("production:read", "production:write")
VIEWERS = ["Anyone with View stock or a production permission in the finished-goods store's branch; owner and "
           "administrator everywhere"]
BOM_KEEPERS = ["Set up BOMs (production.bom)"]
PLANNERS = ["Plan and release production orders (production.plan) in the finished-goods store's branch"]
RUNNERS = ["Use materials and record production output (production.execute) in that branch"]
CHECKERS = ["Quality check (purchase.qc) in that branch"]

BOMS = DocSpec(
    key="boms", area="production", path="production/boms", title="BOMs (recipes)", singular="BOM",
    about="What one batch of a product needs: materials and packaging with their scrap %. In versions: a draft "
          "is changed, then activated (the old active version is archived); a running order keeps its version.",
    model=Bom, get=boms.get_bom, view_code=("production.bom", "production.plan", "inventory.view"),
    queryset=lambda request, company_id: Bom.objects.select_related("item", "batch_uom"),
    read_roles=VIEWERS, write_roles=BOM_KEEPERS, scopes=SCOPES,
    panel={"list": "production:boms", "detail": "production:bom_detail", "create": "production:boms",
           "lines": "production:bom_line_add", "action": "production:bom_action"},
    date_field="effective_from", party_field="item", parent_field="bom", search=("item__code", "item__name"),
    example_number="",
    create_form=f.BomForm,
    create_call=lambda actor, cid, values: boms.create_bom(actor=actor, company_id=cid, values=values),
    create_example={"item_id": 30, "batch_size": "1000", "batch_uom_id": 1, "effective_from": "2026-11-01"},
    line_form=f.BomLineForm,
    add_line=lambda actor, cid, pk, values: boms.add_line(actor=actor, company_id=cid, bom_id=pk, values=values),
    line_example={"item_id": 7, "qty": "120", "uom_id": 2, "scrap_pct": "1.5", "is_packaging": False},
    remove_line=lambda actor, cid, pk, line_id: boms.remove_line(actor=actor, company_id=cid, bom_id=pk,
                                                                 line_id=line_id),
    actions=[
        Action("activate", "Activate a BOM version", "New production orders use it.",
               ["Status: active; the product's previous active version is archived."], "Needs at least one line.",
               lambda r, c, pk, d: boms.activate(actor=r.user, company_id=c, bom_id=pk),
               BOM_KEEPERS, statuses=("draft",)),
        Action("new-version", "Start a new BOM version", "A draft copy of this version, to change.",
               ["Copies the lines into a new draft version and answers it."], "From an active or archived version.",
               lambda r, c, pk, d: boms.new_version(actor=r.user, company_id=c, bom_id=pk),
               BOM_KEEPERS, statuses=("active", "archived"), status_code=201),
    ],
)
_NEW_VERSION = BOMS.actions[1]
_NEW_VERSION.response = InventoryDocumentSerializer
_NEW_VERSION.response_build = lambda bom: document_out(BOMS, bom)
_NEW_VERSION.response_example = document_example(BOMS, status="draft")


def _usage(order):
    rows = orders.usage(order)
    unit = lambda item: item.base_uom.code  # noqa: E731
    return [{"id": index, "item": ref(row["item"]), "batch": None, "store": None, "location": None,
             "qty": plain(row["used"]), "unit": unit(row["item"]), "qty_base": plain(row["used"]),
             "base_unit": unit(row["item"]), "unit_price": None, "amount": None, "note": "",
             "details": {"planned": plain(row["planned"]), "used": plain(row["used"]), "left": plain(row["left"]),
                         "variance": plain(row["variance"]), "packaging": row["packaging"]}}
            for index, row in enumerate(rows, start=1)]


CONSUMPTIONS = DocSpec(
    key="material-use", area="production", path="production/materials", title="Material use",
    singular="material use",
    about="Materials taken from the material store for a released production order (or unused material "
          "returned), first expiry first or by scanning labels. Started from the order (POST "
          "/production/orders/{id}/use or …/return-materials).",
    model=Consumption, get=lambda actor, company_id, pk, code: orders.get_consumption(actor=actor,
                                                                                      company_id=company_id, pk=pk),
    view_code="production.execute",
    queryset=lambda request, company_id: access.scope_warehouses(
        Consumption.objects.select_related("order__output_warehouse"), request.user, company_id, "inventory.view",
        field="order__output_warehouse__branch"),
    read_roles=VIEWERS, write_roles=RUNNERS, scopes=SCOPES,
    panel={"list": "production:orders", "detail": "production:consumption_detail",
           "lines": "production:consumption_line_add", "action": "production:consumption_action"},
    date_field="work_date", reference_field="order", parent_field="consumption", search=("number", "order__number"),
    example_number="MU-2026-00007",
    line_form=f.ConsumptionLineForm, line_form_kwargs=lambda r, c, row: {"consumption": row},
    add_line=lambda actor, cid, pk, values: orders.add_line(actor=actor, company_id=cid, consumption_id=pk,
                                                            values=values),
    line_example={"item_id": 7, "qty": "60", "uom_id": 2},
    remove_line=lambda actor, cid, pk, line_id: orders.remove_line(actor=actor, company_id=cid, consumption_id=pk,
                                                                   line_id=line_id),
    scan=lambda actor, cid, pk, code: orders.scan_into_consumption(actor=actor, company_id=cid, consumption_id=pk,
                                                                   code=code),
    actions=[
        Action("post", "Post material use", "The materials leave the store for the order.",
               ["Takes the stock out at its cost; adds it to the order's material cost."],
               "Refused if a batch ran out, expired or is quarantined.",
               lambda r, c, pk, d: orders.post_consumption(actor=r.user, company_id=c, consumption_id=pk),
               RUNNERS, statuses=("draft",)),
        Action("cancel", "Cancel material use", "Drops the draft.", ["Status: cancelled."], "Only a draft.",
               lambda r, c, pk, d: orders.cancel_consumption(actor=r.user, company_id=c, consumption_id=pk),
               RUNNERS, statuses=("draft",)),
    ],
)

OutputInput = input_serializer(f.OutputForm, "ProductionOutputSerializer")


def _post_output(r, c, pk, d):
    _m, order = orders.get_order(actor=r.user, company_id=c, pk=pk, code="production.execute")
    form = checked(f.OutputForm, to_form(d, OutputInput.form_names), OutputInput.form_names, company_id=c,
                   order=order)
    return orders.post_output(actor=r.user, company_id=c, order_id=pk, values=form.cleaned_data)


class OrderQcSerializer(StrictSerializer):
    result = serializers.ChoiceField(choices=["passed", "failed"],
                                     help_text="passed (releases the new batch) or failed (blocks it).")
    notes = serializers.CharField(max_length=500, required=False, allow_blank=True,
                                  help_text="Needed when it fails: what was wrong.")


def _start(kind):
    return lambda r, c, pk, d: orders.start_consumption(actor=r.user, company_id=c, order_id=pk, kind=kind)


ORDERS = DocSpec(
    key="production-orders", area="production", path="production/orders", title="Production orders",
    singular="production order",
    about="Make a quantity of a product with its active BOM: planned, released by someone else, materials used, "
          "output posted once at the end (a new finished-goods batch at materials ÷ good quantity). Its lines are "
          "the BOM's materials: planned against used (details.planned / used / left / variance).",
    model=ProductionOrder, get=orders.get_order, view_code=("production.plan", "production.execute",
                                                            "inventory.view"),
    queryset=lambda request, company_id: access.scope_warehouses(
        ProductionOrder.objects.select_related("item", "bom", "output_warehouse"), request.user, company_id,
        "inventory.view", field="output_warehouse__branch"),
    read_roles=VIEWERS, write_roles=PLANNERS, scopes=SCOPES,
    panel={"list": "production:orders", "detail": "production:order_detail", "create": "production:orders",
           "lines": "production:order_detail", "action": "production:order_action"},
    date_field="planned_date", store_field="material_warehouse", other_store_field="output_warehouse",
    party_field="item", reference_field="bom", search=("number", "item__code", "item__name", "line_name"),
    lines_of=_usage, example_number="MO-2026-00011",
    create_form=f.OrderForm,
    create_form_kwargs=lambda request, company_id: {"branch_ids": branch_ids(request.user, company_id,
                                                                             "production.plan")},
    create_call=lambda actor, cid, values: orders.create_order(actor=actor, company_id=cid, values=values),
    create_example={"item_id": 30, "planned_qty": "1000", "uom_id": 1, "planned_date": "2026-10-20",
                    "material_warehouse_id": 3, "output_warehouse_id": 5, "line_name": "Line 2"},
    actions=[
        Action("release", "Release a production order", "Materials can then be used.", ["Status: released."],
               "By someone else than who planned it (unless owner or administrator).",
               lambda r, c, pk, d: orders.release(actor=r.user, company_id=c, order_id=pk),
               PLANNERS, statuses=("planned",)),
        Action("use", "Use materials", "Drafts the material use the BOM still needs.",
               ["Answers the new material use document (first expiry first)."], "Only a released order.",
               _start(Consumption.Kind.ISSUE), RUNNERS, statuses=("released",), status_code=201),
        Action("return-materials", "Return unused materials", "Drafts a return of unused material.",
               ["Answers the new material return document."], "Only a released order.",
               _start(Consumption.Kind.RETURN), RUNNERS, statuses=("released",), status_code=201),
        Action("output", "Record production output", "The good and rejected quantity, once, at the end.",
               ["Posts the new finished-goods batch at materials ÷ good quantity; status: completed."],
               "QC-required products go to quarantine until passed.",
               _post_output, RUNNERS, body=OutputInput,
               body_example={"output_date": "2026-10-21", "qty_good": "990", "qty_rejected": "10", "uom_id": 1,
                             "mfg_date": "2026-10-21"},
               statuses=("released",), panel="production:order_output"),
        Action("qc", "Record the output's quality check", "Passed releases the batch; failed blocks it.",
               ["Records the check."], "Only when the output waits for QC.",
               lambda r, c, pk, d: orders.record_qc(actor=r.user, company_id=c, order_id=pk,
                                                    passed=d["result"] == "passed", notes=d.get("notes", "")),
               CHECKERS, body=OrderQcSerializer, body_example={"result": "passed", "notes": ""},
               statuses=("completed",)),
        Action("cancel", "Cancel a production order", "Drops it before materials are used.", ["Status: cancelled."],
               "Not once materials were used.",
               lambda r, c, pk, d: orders.cancel(actor=r.user, company_id=c, order_id=pk),
               PLANNERS, statuses=("planned", "released")),
        Action("close", "Close a production order", "Done with: nothing more is used or made.", ["Status: closed."],
               "After its output.",
               lambda r, c, pk, d: orders.close(actor=r.user, company_id=c, order_id=pk),
               PLANNERS, statuses=("completed",)),
    ],
)
for _action in ORDERS.actions:
    if _action.name in ("use", "return-materials"):
        _action.response = InventoryDocumentSerializer
        _action.response_build = lambda use: document_out(CONSUMPTIONS, use)
        _action.response_example = document_example(CONSUMPTIONS)

SPECS = (BOMS, ORDERS, CONSUMPTIONS)
