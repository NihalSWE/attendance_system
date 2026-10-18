"""Stock: what is where now, an item's ledger, batches with their expiry, and
what a scanned label stands for."""

import datetime
from decimal import Decimal

from rest_framework import serializers
from rest_framework.response import Response

from api.core.docs import PATH, QUERY, Param, endpoint
from api.core.errors import ApiError
from api.core.forms import refuse
from api.v1.inventory.engine import ERRORS, MODULE_NOTE, ONE, InventoryRefSerializer, page, plain, ref, view_class
from inventory import access, lookup, valuation
from inventory.models import Batch, Item, ItemKind, StockBalance, Warehouse

VIEWERS = ["Anyone with View stock and items (inventory.view), in the stores' branches; owner and administrator "
           "everywhere. Costs and values only with See stock costs and values."]


def _int(request, name):
    value = (request.query_params.get(name) or "").strip()
    if not value:
        return None
    if not value.isdigit():
        refuse({name: ["A whole number."]})
    return int(value)


def _day(request, name, default):
    value = (request.query_params.get(name) or "").strip()
    if not value:
        return default
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        refuse({name: ["YYYY-MM-DD."]})


def _cost_branches(request):
    from access_control.branch_access import ALL_BRANCHES, branches_for

    found = branches_for(request.user, request.company_id, "inventory.valuation")
    return found, (lambda branch_id: found is ALL_BRANCHES or branch_id in found)


# --- stock on hand ------------------------------------------------------------------------

class StockRowSerializer(serializers.Serializer):
    item = InventoryRefSerializer(help_text="The item.")
    batch = InventoryRefSerializer(allow_null=True, help_text="The batch (code = batch number), when it keeps batches.")
    expiry_date = serializers.CharField(allow_null=True, help_text="The batch's expiry date (YYYY-MM-DD).")
    batch_status = serializers.CharField(allow_null=True, help_text="released, quarantine or blocked.")
    store = InventoryRefSerializer(help_text="The store.")
    location = InventoryRefSerializer(allow_null=True, help_text="The bin, when one is used.")
    qty_on_hand = serializers.CharField(help_text="On hand, in the item's base unit.")
    qty_reserved = serializers.CharField(help_text="Reserved for confirmed sales orders.")
    qty_available = serializers.CharField(help_text="On hand less reserved.")
    base_unit = serializers.CharField(help_text="The item's base unit, e.g. KG.")
    value = serializers.CharField(allow_null=True, help_text="On hand at the average cost - null without See stock "
                                                             "costs and values in that branch.")


STOCK_EXAMPLE = {"item": {"id": 7, "code": "RM-0001", "name": "Sugar (refined)"},
                 "batch": {"id": 18, "code": "S-261012-01", "name": "S-261012-01"}, "expiry_date": "2027-09-30",
                 "batch_status": "released", "store": {"id": 3, "code": "RMS", "name": "Raw material store"},
                 "location": None, "qty_on_hand": "450", "qty_reserved": "0", "qty_available": "450",
                 "base_unit": "KG", "value": "37125.00"}


@endpoint(id="inventory-stock", area="inventory", title="Stock on hand",
          summary="Every item, batch, store and bin with stock now.",
          what_it_does=["Lists stock rows in the stores you may see, by item.",
                        "Filters: store_id, item_id, batch_id, kind (item type), search."],
          description=f"The same figures as the panel's Stock on hand page. {MODULE_NOTE}", roles=VIEWERS,
          scopes=["inventory:read"],
          params=[Param("store_id", QUERY, "integer", "Only this store.", example=3),
                  Param("item_id", QUERY, "integer", "Only this item.", example=7),
                  Param("batch_id", QUERY, "integer", "Only this batch.", example=18),
                  Param("kind", QUERY, "string", "Only this item type: " + ", ".join(ItemKind.values) + ".",
                        example="raw"),
                  Param("search", QUERY, "string", "Text in the item's code or name, or the batch number.",
                        example="sugar")],
          paginated=True, response=StockRowSerializer,
          response_example={"count": 1, "next": None, "previous": None, "results": [STOCK_EXAMPLE]},
          errors=ERRORS + ["validation_error"])
def _stock_get(self, request):
    from django.db.models import Q

    from inventory.stock_views import _cost_subquery

    access.require(request.user, request.company_id, "inventory.view")
    rows = access.scope_warehouses(
        StockBalance.objects.filter(qty_on_hand__gt=0).select_related("item__base_uom", "batch", "warehouse",
                                                                       "location"),
        request.user, request.company_id, "inventory.view", field="warehouse__branch",
    ).annotate(avg_cost=_cost_subquery(request.company_id))
    for name, column in (("store_id", "warehouse_id"), ("item_id", "item_id"), ("batch_id", "batch_id")):
        value = _int(request, name)
        if value is not None:
            rows = rows.filter(**{column: value})
    kind = request.query_params.get("kind")
    if kind:
        if kind not in ItemKind.values:
            refuse({"kind": ["One of: " + ", ".join(ItemKind.values) + "."]})
        rows = rows.filter(item__kind=kind)
    text = (request.query_params.get("search") or "").strip()
    if text:
        rows = rows.filter(Q(item__code__icontains=text) | Q(item__name__icontains=text)
                           | Q(batch__batch_no__icontains=text))
    _found, sees = _cost_branches(request)

    def build(row):
        batch = row.batch
        value = None
        if sees(row.warehouse.branch_id):
            value = plain((row.qty_on_hand * (row.avg_cost or Decimal("0"))).quantize(Decimal("0.01")))
        return {"item": ref(row.item), "batch": ref(batch), "expiry_date": plain(batch.expiry_date) if batch else None,
                "batch_status": batch.status if batch else None, "store": ref(row.warehouse),
                "location": ref(row.location), "qty_on_hand": plain(row.qty_on_hand),
                "qty_reserved": plain(row.qty_reserved), "qty_available": plain(row.qty_on_hand - row.qty_reserved),
                "base_unit": row.item.base_uom.code, "value": value}

    return page(self, request, rows.order_by("item__code", "warehouse__code", "batch__expiry_date", "pk"), build,
                StockRowSerializer)


StockView = view_class("InventoryStockView", get=_stock_get, panel_page="inventory:stock")


# --- an item's ledger -----------------------------------------------------------------------

class MoveSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The move's id in the ledger.")
    date = serializers.CharField(help_text="Its work date (YYYY-MM-DD) - what the valuation at a date counts.")
    posted_at = serializers.CharField(help_text="When it was posted (ISO 8601).")
    kind = serializers.CharField(help_text="The move type, e.g. receipt, issue, delivery, reversal.")
    kind_label = serializers.CharField(help_text="The move type as the panel shows it.")
    document = serializers.CharField(help_text="The document's number (or its type and id).")
    store = InventoryRefSerializer(help_text="The store.")
    batch = InventoryRefSerializer(allow_null=True, help_text="The batch.")
    qty = serializers.CharField(help_text="In (+) or out (-), in the item's base unit.")
    unit_cost = serializers.CharField(allow_null=True, help_text="The cost it moved at - null without See stock "
                                                                 "costs and values.")
    value = serializers.CharField(allow_null=True, help_text="qty × unit cost - null without See stock costs and "
                                                             "values.")


ITEM_PARAM = Param("item_id", PATH, "integer", "The item's id.", required=True, example=7)


@endpoint(id="inventory-item-ledger", area="inventory", title="An item's stock ledger",
          summary="Every move of one item over dates, oldest first.",
          what_it_does=["Lists the item's moves (receipts, issues, transfers, deliveries, reversals ...) in the "
                        "stores you may see."],
          description="The ledger is never changed: a mistake is undone by a reversal, which shows here too. "
                      "Dates default to this month.",
          roles=VIEWERS, scopes=["inventory:read"],
          params=[ITEM_PARAM, Param("date_from", QUERY, "string (date)", "From (YYYY-MM-DD).", example="2026-10-01"),
                  Param("date_to", QUERY, "string (date)", "To (YYYY-MM-DD).", example="2026-10-31"),
                  Param("store_id", QUERY, "integer", "Only this store.", example=3)],
          paginated=True, response=MoveSerializer,
          response_example={"count": 1, "next": None, "previous": None, "results": [{
              "id": 901, "date": "2026-10-16", "posted_at": "2026-10-16T10:02:00+06:00", "kind": "issue",
              "kind_label": "Store issue", "document": "SI-2026-00012",
              "store": {"id": 3, "code": "RMS", "name": "Raw material store"},
              "batch": {"id": 18, "code": "S-261012-01", "name": "S-261012-01"}, "qty": "-20",
              "unit_cost": "82.5", "value": "-1650"}]},
          errors=ERRORS + ONE + ["validation_error"])
def _ledger_get(self, request, item_id):
    from inventory.doc_common import local_today

    access.require(request.user, request.company_id, "inventory.view")
    if not Item.objects.filter(pk=item_id).exists():
        raise ApiError("not_found", "No such item in this company.")
    today = local_today(access.require_module(request.user, request.company_id).company)
    date_from = _day(request, "date_from", today.replace(day=1))
    date_to = _day(request, "date_to", today)
    stores = list(access.scope_warehouses(
        Warehouse.objects.all(), request.user,
        request.company_id, "inventory.view").values_list("pk", flat=True))
    store = _int(request, "store_id")
    if store is not None:
        stores = [s for s in stores if s == store]
    rows = (valuation.moves(request.company_id, store_ids=stores, item_ids=[item_id])
            .filter(work_date__gte=date_from, work_date__lte=date_to)
            .select_related("warehouse", "batch").order_by("work_date", "posted_at", "pk"))
    _found, sees = _cost_branches(request)
    numbers = {}

    def build(move):
        if (move.document_type, move.document_id) not in numbers:
            numbers.update(valuation.document_numbers([(move.document_type, move.document_id)]))
        shown = sees(move.warehouse.branch_id)
        return {"id": move.pk, "date": plain(move.work_date), "posted_at": plain(move.posted_at),
                "kind": move.move_type, "kind_label": move.get_move_type_display(),
                "document": numbers.get((move.document_type, move.document_id))
                or f"{move.document_type} #{move.document_id}",
                "store": ref(move.warehouse), "batch": ref(move.batch), "qty": plain(move.qty),
                "unit_cost": plain(move.unit_cost) if shown else None, "value": plain(move.value) if shown else None}

    return page(self, request, rows, build, MoveSerializer)


LedgerView = view_class("InventoryItemLedgerView", get=_ledger_get, panel_page="inventory:item_detail")


# --- batches -------------------------------------------------------------------------------

class BatchSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The batch id.")
    batch_no = serializers.CharField(help_text="Its number.")
    item = InventoryRefSerializer(help_text="The item.")
    supplier_batch_no = serializers.CharField(help_text="The supplier's own batch number (may be empty).")
    mfg_date = serializers.CharField(allow_null=True, help_text="Made on (YYYY-MM-DD).")
    expiry_date = serializers.CharField(allow_null=True, help_text="Expires on (YYYY-MM-DD).")
    days_left = serializers.IntegerField(allow_null=True, help_text="Days until it expires (below 0: expired).")
    status = serializers.CharField(help_text="released, quarantine or blocked.")
    qty_on_hand = serializers.CharField(help_text="On hand in the stores you may see, base unit.")


@endpoint(id="inventory-batches", area="inventory", title="Batches",
          summary="Batches with their expiry and what is left of them.",
          what_it_does=["Lists batches, first to expire first.",
                        "Filters: item_id, in_stock (true: only with stock), expiring_within (days)."],
          description="A batch is made when goods are received, produced or loaded as opening stock.",
          roles=VIEWERS, scopes=["inventory:read"],
          params=[Param("item_id", QUERY, "integer", "Only this item.", example=7),
                  Param("in_stock", QUERY, "boolean", "true: only batches with stock (the default); false: all.",
                        example="true"),
                  Param("expiring_within", QUERY, "integer", "Only those expiring within this many days (expired "
                                                             "included).", example=30)],
          paginated=True, response=BatchSerializer,
          response_example={"count": 1, "next": None, "previous": None, "results": [{
              "id": 18, "batch_no": "S-261012-01", "item": {"id": 7, "code": "RM-0001", "name": "Sugar (refined)"},
              "supplier_batch_no": "LOT-88", "mfg_date": None, "expiry_date": "2027-09-30", "days_left": 348,
              "status": "released", "qty_on_hand": "450"}]},
          errors=ERRORS + ["validation_error"])
def _batches_get(self, request):
    from django.db.models import Q, Sum

    from inventory.doc_common import local_today

    membership = access.require(request.user, request.company_id, "inventory.view")
    today = local_today(membership.company)
    stores = access.scope_warehouses(Warehouse.objects.all(),
                                     request.user, request.company_id, "inventory.view").values_list("pk", flat=True)
    rows = Batch.objects.select_related("item").annotate(
        held=Sum("balances__qty_on_hand", filter=Q(balances__warehouse_id__in=list(stores))))
    item = _int(request, "item_id")
    if item is not None:
        rows = rows.filter(item_id=item)
    if (request.query_params.get("in_stock") or "true").lower() != "false":
        rows = rows.filter(held__gt=0)
    within = _int(request, "expiring_within")
    if within is not None:
        rows = rows.filter(expiry_date__lte=today + datetime.timedelta(days=within))

    def build(batch):
        return {"id": batch.pk, "batch_no": batch.batch_no, "item": ref(batch.item),
                "supplier_batch_no": batch.supplier_batch_no or "", "mfg_date": plain(batch.mfg_date),
                "expiry_date": plain(batch.expiry_date),
                "days_left": (batch.expiry_date - today).days if batch.expiry_date else None,
                "status": batch.status, "qty_on_hand": plain(batch.held or Decimal("0"))}

    return page(self, request, rows.order_by("expiry_date", "item__code", "pk"), build, BatchSerializer)


BatchesView = view_class("InventoryBatchesView", get=_batches_get, panel_page="inventory:stock")


# --- label lookup -----------------------------------------------------------------------------

class LookupSerializer(serializers.Serializer):
    found = serializers.BooleanField(help_text="Whether the code is something this company knows.")
    code = serializers.CharField(help_text="The code read (the label code part of a QR value).")
    kind = serializers.CharField(allow_null=True, help_text="item, batch, package, location (bin), pack (a pack "
                                                            "barcode) or gs1.")
    message = serializers.CharField(help_text="What it is, in one line, e.g. \"RM-0001 · Sugar · batch S1 · 50 KG\" - "
                                              "or why it is not found.")
    warnings = serializers.ListField(child=serializers.CharField(), help_text="E.g. expired, quarantined, used up.")
    item = serializers.DictField(allow_null=True, help_text="The item: id, code, name, base unit.")
    batch = serializers.DictField(allow_null=True, help_text="The batch: id, number, expiry, status.")
    quantity = serializers.CharField(allow_null=True, help_text="The package's quantity, in uom.")
    uom = serializers.CharField(allow_null=True, help_text="The unit of quantity.")
    quantity_base = serializers.CharField(allow_null=True, help_text="The same in the item's base unit.")
    warehouse = serializers.DictField(allow_null=True, help_text="The store a package or bin label belongs to.")
    location = serializers.DictField(allow_null=True, help_text="The bin.")
    label = serializers.DictField(allow_null=True, help_text="The label itself: id, status, how often printed.")
    gs1 = serializers.DictField(allow_null=True, help_text="For a GS1-128 code: the values it carries (batch, "
                                                           "expiry, quantity).")


@endpoint(id="inventory-labels-lookup", area="inventory", title="What is this label?",
          summary="One scanned code: a package, batch, item or bin label, an item or pack barcode, or GS1-128.",
          what_it_does=["Answers what the code stands for, with warnings (expired, quarantined, used up)."],
          description="The same lookup as the panel's Scan page and every scan box. A code of another company is "
                      "never shown - it answers found: false.",
          roles=VIEWERS, scopes=["inventory:read"],
          params=[Param("code", QUERY, "string", "What the scanner read.", required=True, example="P12-000045")],
          response=LookupSerializer,
          response_example={"found": True, "code": "P12-000045", "kind": "package",
                            "message": "RM-0001 · Sugar (refined) · batch S-261012-01 · 50 KG", "warnings": [],
                            "item": {"id": 7, "code": "RM-0001", "name": "Sugar (refined)", "base_uom": "KG"},
                            "batch": {"id": 18, "batch_no": "S-261012-01", "expiry_date": "2027-09-30",
                                      "status": "released"}, "quantity": "50", "uom": "KG", "quantity_base": "50",
                            "warehouse": {"id": 3, "code": "RMS", "name": "Raw material store"}, "location": None,
                            "label": {"id": 45, "status": "active", "status_label": "Active", "printed_count": 1},
                            "gs1": None},
          errors=ERRORS + ["validation_error"])
def _lookup_get(self, request):
    from inventory.doc_common import local_today

    membership = access.require(request.user, request.company_id, "inventory.view")
    code = (request.query_params.get("code") or "").strip()[:500]
    if not code:
        refuse({"code": ["Give the code that was scanned."]})
    found = lookup.lookup(request.company_id, code, today=local_today(membership.company))
    keys = ("kind", "item", "batch", "quantity", "uom", "quantity_base", "warehouse", "location", "label", "gs1")
    return Response(LookupSerializer({"found": found.get("found", False), "code": found.get("code", code),
                                      "message": found.get("message", ""), "warnings": found.get("warnings", []),
                                      **{k: found.get(k) for k in keys}}).data)


LookupView = view_class("InventoryLabelLookupView", get=_lookup_get, panel_page="inventory:scan_lookup")
