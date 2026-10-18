"""Stock on hand and the item page (its packs, stock and ledger card)."""

from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import F, OuterRef, Subquery
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from access_control.branch_access import ALL_BRANCHES, branches_for
from base_template.tables import paginate, render
from common.forms import apply_service_errors
from common.tenant import use_company
from inventory import access, forms, masters
from inventory.models import (
    InventorySettings,
    Item,
    ItemCost,
    ItemKind,
    ItemUom,
    StockBalance,
    StockMove,
    Warehouse,
)
from inventory.posting import settings_for
from inventory.views import company_or_bail


def _cost_subquery(company_id):
    """The average cost that values a balance row: per item, or per item and store."""
    per_store = settings_for(company_id).valuation_scope == InventorySettings.ValuationScope.WAREHOUSE
    costs = ItemCost.all_objects.filter(company_id=company_id, item_id=OuterRef("item_id"))
    costs = costs.filter(warehouse_id=OuterRef("warehouse_id")) if per_store else costs.filter(
        warehouse__isnull=True)
    return Subquery(costs.values("avg_cost")[:1])


def _valuation_branches(user, company_id):
    branches = branches_for(user, company_id, "inventory.valuation")
    return branches


@login_required
def stock(request):
    """Stock on hand: one row per item x batch x store x bin with stock."""
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require(request.user, company_id, "inventory.view")
    costs_in = _valuation_branches(request.user, company_id)
    with use_company(company_id):
        rows = access.scope_warehouses(
            StockBalance.objects.filter(qty_on_hand__gt=0).select_related(
                "item__base_uom", "batch", "warehouse__branch", "location"),
            request.user, company_id, "inventory.view", field="warehouse__branch",
        ).annotate(avg_cost=_cost_subquery(company_id))
        stores = access.scope_warehouses(Warehouse.objects.order_by("name"), request.user,
                                         company_id, "inventory.view")
        store = request.GET.get("store", "")
        if store.isdigit():
            rows = rows.filter(warehouse_id=int(store))
        kind = request.GET.get("kind", "")
        if kind in ItemKind.values:
            rows = rows.filter(item__kind=kind)
        table = paginate(
            request, rows.order_by("item__code", "warehouse__code", "batch__expiry_date"),
            search=("item__code", "item__name", "item__name_bn", "batch__batch_no",
                    "warehouse__code", "warehouse__name", "location__code"),
            order=("item__code", "item__name", "warehouse__code", "location__code",
                   "batch__batch_no", "batch__expiry_date", "qty_on_hand", "qty_reserved",
                   None, None, None),
        )
        stock_rows = list(table.object_list)
        for row in stock_rows:
            row.shows_cost = costs_in is ALL_BRANCHES or row.warehouse.branch_id in costs_in
            row.value = (row.qty_on_hand * (row.avg_cost or Decimal("0"))).quantize(Decimal("0.01"))
    return render(request, "inventory/stock.html", {
        "rows": table, "stock_rows": stock_rows, "stores": stores, "store": store,
        "kind": kind, "kinds": ItemKind.choices, "sees_costs": bool(costs_in),
    })


def _item_context(request, company_id, item, *, pack_form=None, open_dialog=""):
    costs_in = _valuation_branches(request.user, company_id)
    with use_company(company_id):
        balances = list(access.scope_warehouses(
            StockBalance.objects.filter(item=item, qty_on_hand__gt=0).select_related(
                "batch", "warehouse", "location"),
            request.user, company_id, "inventory.view", field="warehouse__branch",
        ).order_by("warehouse__code", "batch__expiry_date"))
        moves = paginate(request, access.scope_warehouses(
            StockMove.objects.filter(item=item).select_related("batch", "warehouse", "location"),
            request.user, company_id, "inventory.view", field="warehouse__branch",
        ).order_by("-posted_at", "-pk"),
            search=("batch__batch_no", "warehouse__code", "document_type", "note"),
            order=("posted_at", "work_date", "move_type", "warehouse__code", "batch__batch_no",
                   "qty", None, None))
        packs = list(ItemUom.objects.filter(item=item).select_related("uom"))
        cost = ItemCost.objects.filter(item=item, warehouse__isnull=True).first()
    can_manage = access.may(request.user, company_id, masters.CODE)
    if can_manage and pack_form is None:
        pack_form = forms.PackUnitForm(company_id=company_id)
    return {
        "item": item, "balances": balances, "moves": moves, "packs": packs,
        "on_hand": sum((b.qty_on_hand for b in balances), Decimal("0")),
        "cost": cost, "sees_costs": costs_in is ALL_BRANCHES,
        "can_manage": can_manage, "pack_form": pack_form, "open_dialog": open_dialog,
        "can_print": access.may(request.user, company_id, "inventory.labels"),
        "print_form": forms.PrintForm(),
        **_printed(company_id, item, balances),
    }


def _printed(company_id, item, balances):
    """Which of the item's labels were printed before (their Print asks why)."""
    from inventory.models import BarcodeLabel

    with use_company(company_id):
        printed = BarcodeLabel.objects.filter(item=item, status=BarcodeLabel.Status.ACTIVE,
                                              printed_count__gt=0)
        return {
            "item_label_printed": printed.filter(kind=BarcodeLabel.Kind.ITEM).exists(),
            "printed_batches": set(printed.filter(
                kind=BarcodeLabel.Kind.BATCH,
                batch__in=[b.batch_id for b in balances if b.batch_id]).values_list("batch_id", flat=True)),
        }


@login_required
def item_detail(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require(request.user, company_id, "inventory.view")
    with use_company(company_id):
        item = Item.objects.select_related("category", "base_uom", "default_warehouse").filter(
            pk=pk).first()
    if item is None:
        raise PermissionDenied("Item not found in this company.")
    return render(request, "inventory/item_detail.html", _item_context(request, company_id, item))


@login_required
@require_POST
def pack_unit_add(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    _, item = masters.get_for_edit(masters.ITEM, actor=request.user, company_id=company_id, pk=pk)
    form = forms.PackUnitForm(request.POST, company_id=company_id)
    if form.is_valid():
        try:
            masters.add_pack_unit(actor=request.user, company_id=company_id, item_id=item.pk,
                                  values=form.cleaned_data)
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, "Pack unit added.")
            return redirect("inventory:item_detail", pk=item.pk)
    return render(request, "inventory/item_detail.html",
                  _item_context(request, company_id, item, pack_form=form, open_dialog="pack-dialog"))


@login_required
@require_POST
def pack_unit_remove(request, pk, pack_pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    masters.remove_pack_unit(actor=request.user, company_id=company_id, item_id=pk, pack_id=pack_pk)
    messages.success(request, "Pack unit removed.")
    return redirect("inventory:item_detail", pk=pk)
