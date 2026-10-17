"""Writes for the inventory masters: units, categories, items, pack units,
stores, bins, settings - and, through the same helper, suppliers
(``purchasing.services``) and customers (``sales.services``).

Every write: the module is on, the actor holds "Set up items, stores,
suppliers and customers" (``inventory.items``) - anywhere for a company
master, in the store's branch for a store or bin - the values pass the
form's whitelist, the row is fully validated (``full_clean``, which also
refuses another company's rows), and an audit line is written in the same
transaction.
"""

import datetime
from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction

from auditlog.services import record_company_event
from common.choices import ActiveStatus
from common.services import create_validated
from common.tenant import use_company
from inventory import access
from inventory.models import (
    InventorySettings,
    Item,
    ItemCategory,
    ItemUom,
    Location,
    StockMove,
    Uom,
    Warehouse,
)

CODE = "inventory.items"


def plain(value):
    """A value as the audit log stores it."""
    if isinstance(value, models.Model):
        return value.pk
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    return value


def _writable(values, allowed):
    """Whitelist. A crafted POST must not reach a column the form omits."""
    unsupported = set(values) - set(allowed)
    if unsupported:
        raise ValidationError(f"Unsupported field: {', '.join(sorted(unsupported))}")
    values = dict(values)
    for key in ("code",):
        if isinstance(values.get(key), str):
            values[key] = values[key].strip().upper()
    for key, value in values.items():
        if isinstance(value, str) and key != "code":
            values[key] = value.strip()
    return values


@dataclass(frozen=True)
class Master:
    """One kind of master row and how its writes are checked and audited."""

    model: type
    fields: tuple
    action: str
    #: Path from the row to its branch, for a store-level row; None for a company master.
    branch_path: str = None

    def branch_of(self, row_or_values):
        if self.branch_path is None:
            return None
        obj = row_or_values
        for part in self.branch_path.split("__"):
            obj = obj.get(part) if isinstance(obj, dict) else getattr(obj, part, None)
            if obj is None:
                return None
        return getattr(obj, "pk", obj)

    def snapshot(self, row):
        return {field: plain(getattr(row, field)) for field in self.fields}


def _require(master, actor, company_id, branch_id=None):
    if master.branch_path is None:
        return access.require(actor, company_id, CODE)
    if branch_id is None:
        raise ValidationError("Choose a branch.")
    return access.require(actor, company_id, CODE, branch_id)


def get_for_edit(master, *, actor, company_id, pk):
    """``(membership, row)`` when the actor may change this row."""
    membership = access.require(actor, company_id, CODE)
    with use_company(company_id):
        row = master.model.objects.filter(pk=pk).first()
    if row is None:
        raise PermissionDenied("Not found in this company.")
    if master.branch_path is not None:
        _require(master, actor, company_id, master.branch_of(row))
    return membership, row


@transaction.atomic
def create(master, *, actor, company_id, values):
    values = _writable(values, master.fields)
    membership = _require(master, actor, company_id, master.branch_of(values))
    with use_company(company_id):
        row = create_validated(master.model, company=membership.company,
                               created_by=actor, updated_by=actor, **values)
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action=f"{master.action}.created", obj=row,
                             after=master.snapshot(row))
    return row


@transaction.atomic
def update(master, *, actor, company_id, pk, values):
    membership, row = get_for_edit(master, actor=actor, company_id=company_id, pk=pk)
    values = _writable(values, master.fields)
    if master.branch_path is not None:
        # Moving a store to another branch needs the permission there too.
        new_branch = master.branch_of(values)
        if new_branch is not None:
            _require(master, actor, company_id, new_branch)
    with use_company(company_id):
        before = master.snapshot(row)
        for field, value in values.items():
            setattr(row, field, value)
        row.updated_by = actor
        row.full_clean()
        row.save()
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action=f"{master.action}.updated", obj=row, before=before,
                             after=master.snapshot(row))
    return row


@transaction.atomic
def set_status(master, *, actor, company_id, pk, status):
    membership, row = get_for_edit(master, actor=actor, company_id=company_id, pk=pk)
    if status not in ActiveStatus.values:
        raise ValidationError({"status": "Unknown status."})
    with use_company(company_id):
        before = {"status": row.status}
        row.status = status
        row.updated_by = actor
        row.full_clean()
        row.save()
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action=f"{master.action}.status_changed", obj=row,
                             before=before, after={"status": status})
    return row


# --------------------------------------------------------------------------
# The inventory masters
# --------------------------------------------------------------------------

UOM = Master(Uom, ("code", "name", "kind", "decimals"), "inventory.uom")
CATEGORY = Master(ItemCategory, ("code", "name", "kind", "parent"), "inventory.category")
ITEM = Master(Item, (
    "code", "name", "name_bn", "kind", "category", "base_uom", "barcode", "track_batch",
    "track_expiry", "shelf_life_days", "qc_required", "min_stock", "reorder_point",
    "reorder_qty", "max_stock", "default_warehouse", "standard_cost", "sale_price",
    "vat_rate", "hs_code", "notes",
), "inventory.item")
WAREHOUSE = Master(Warehouse, ("branch", "code", "name", "kind", "notes"),
                   "inventory.warehouse", branch_path="branch")
LOCATION = Master(Location, ("warehouse", "code", "description"),
                  "inventory.location", branch_path="warehouse__branch")

#: The units a factory usually needs; "Add standard units" adds the missing ones.
STANDARD_UNITS = (
    ("KG", "Kilogram", Uom.Kind.WEIGHT, 3),
    ("G", "Gram", Uom.Kind.WEIGHT, 2),
    ("L", "Litre", Uom.Kind.VOLUME, 3),
    ("ML", "Millilitre", Uom.Kind.VOLUME, 0),
    ("PCS", "Pieces", Uom.Kind.COUNT, 0),
    ("BOX", "Box", Uom.Kind.COUNT, 0),
    ("CARTON", "Carton", Uom.Kind.COUNT, 0),
    ("BAG", "Bag", Uom.Kind.COUNT, 0),
    ("DRUM", "Drum", Uom.Kind.COUNT, 0),
    ("ROLL", "Roll", Uom.Kind.COUNT, 0),
    ("M", "Metre", Uom.Kind.LENGTH, 2),
    ("SET", "Set", Uom.Kind.COUNT, 0),
    ("PAIR", "Pair", Uom.Kind.COUNT, 0),
)


@transaction.atomic
def add_standard_units(*, actor, company_id):
    """Add the standard units the company lacks; returns those added."""
    membership = access.require(actor, company_id, CODE)
    with use_company(company_id):
        existing = set(Uom.objects.values_list("code", flat=True))
        added = [
            create_validated(Uom, company=membership.company, code=code, name=name, kind=kind,
                             decimals=decimals, created_by=actor, updated_by=actor)
            for code, name, kind, decimals in STANDARD_UNITS if code not in existing
        ]
        for uom in added:
            record_company_event(actor=actor, membership=membership, company=membership.company,
                                 action="inventory.uom.created", obj=uom, after=UOM.snapshot(uom))
    return added


def _item_has_moved(item):
    return StockMove.all_objects.filter(company_id=item.company_id, item=item).exists()


@transaction.atomic
def update_item(*, actor, company_id, pk, values):
    """Edit an item. Once stock has moved, its unit, type and batch/expiry
    tracking stay as they are: the ledger is counted in them."""
    _, item = get_for_edit(ITEM, actor=actor, company_id=company_id, pk=pk)
    if _item_has_moved(item):
        fixed = {"base_uom": "unit", "kind": "item type", "track_batch": "batch tracking",
                 "track_expiry": "expiry tracking"}
        for field, label in fixed.items():
            if field in values and plain(values[field]) != plain(getattr(item, field)):
                raise ValidationError({field: f"Stock of this item has moved, so its {label} "
                                              "cannot change."})
    return update(ITEM, actor=actor, company_id=company_id, pk=pk, values=values)


@transaction.atomic
def add_pack_unit(*, actor, company_id, item_id, values):
    """1 CARTON = 24 PCS: a pack the item can be entered in."""
    membership, item = get_for_edit(ITEM, actor=actor, company_id=company_id, pk=item_id)
    values = _writable(values, ("uom", "factor", "barcode"))
    with use_company(company_id):
        pack = create_validated(ItemUom, company=membership.company, item=item,
                                created_by=actor, updated_by=actor, **values)
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action="inventory.item.pack_unit_added", obj=item,
                             after={"uom": pack.uom.code, "factor": plain(pack.factor),
                                    "barcode": pack.barcode})
    return pack


@transaction.atomic
def remove_pack_unit(*, actor, company_id, item_id, pack_id):
    membership, item = get_for_edit(ITEM, actor=actor, company_id=company_id, pk=item_id)
    with use_company(company_id):
        pack = ItemUom.objects.filter(pk=pack_id, item=item).select_related("uom").first()
        if pack is None:
            raise PermissionDenied("Pack unit not found.")
        before = {"uom": pack.uom.code, "factor": plain(pack.factor)}
        pack.delete()
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action="inventory.item.pack_unit_removed", obj=item, before=before)


def units_for(item):
    """``{uom_id: factor}``: the item's base unit (1) and its pack units."""
    factors = {item.base_uom_id: Decimal("1")}
    for pack in ItemUom.all_objects.filter(item=item):
        factors[pack.uom_id] = pack.factor
    return factors


SETTINGS_FIELDS = ("valuation_scope", "expiry_alert_days", "slow_moving_days", "alert_emails", "small_label",
                   "large_label", "gs1_labels")


@transaction.atomic
def update_settings(*, actor, company_id, values):
    """The company's inventory settings (owner or company admin)."""
    membership = access.require_module(actor, company_id)
    if membership.role not in ("owner", "company_admin"):
        raise PermissionDenied("Inventory settings are kept by the owner or company administrator.")
    values = _writable(values, SETTINGS_FIELDS)
    with use_company(company_id):
        row = InventorySettings.objects.select_for_update().first()
        if row is None:
            row = InventorySettings(company=membership.company, created_by=actor)
        before = {field: getattr(row, field) for field in SETTINGS_FIELDS}
        if ("valuation_scope" in values and values["valuation_scope"] != row.valuation_scope
                and StockMove.objects.exists()):
            raise ValidationError({"valuation_scope": "Stock has moved, so how it is valued "
                                                      "cannot change any more."})
        for field, value in values.items():
            setattr(row, field, value)
        row.updated_by = actor
        row.full_clean()
        row.save()
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action="inventory.settings.updated", obj=row, before=before,
                             after={field: getattr(row, field) for field in SETTINGS_FIELDS})
    return row
