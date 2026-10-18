"""Going live (docs/inventory/00-PLAN.md Phase 10; the runbook:
docs/inventory/11-GO-LIVE.md).

The software side of the go-live weeks:

- ``readiness``: is the company ready? Settings decided, masters loaded,
  people given their permissions (with someone else to approve), opening
  stock posted and labelled, the ledger in agreement, mail working. Each
  check says what is wrong and where to fix it (Inventory → Go-live, and
  ``python manage.py golive_check``).
- ``count_sheet``: the stock count day's sheet for a store, as Excel in the
  very layout of the opening stock import (count, fill in, import) or as a
  PDF for a clipboard.
- ``opening_labels``: a label for every batch (or item) an opening stock
  document brought in, to print and stick on the count day.
- ``compare``: the parallel run - the old system's stock list against the
  software's, line by line.
"""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError

from access_control.branch_access import can
from common.choices import ActiveStatus
from common.tenant import use_company
from inventory import access
from inventory.models import (
    Batch,
    BarcodeLabel,
    InventorySettings,
    Item,
    ItemCategory,
    ItemKind,
    OpeningStock,
    StockBalance,
    Uom,
    Warehouse,
)

OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"
ZERO = Decimal("0")


@dataclass
class Check:
    status: str           # ok, warn (should be fixed), fail (must be fixed), info
    title: str
    detail: str = ""
    url: str = ""         # the page to fix it on (a URL name)
    url_args: tuple = ()


def _holders(company_id, code):
    """Active people who hold ``code`` somewhere, owner and administrators apart."""
    from accounts.models import CompanyMembership

    found = []
    for membership in CompanyMembership.all_objects.filter(company_id=company_id, status="active").exclude(
            role__in=("owner", "company_admin")).select_related("user"):
        if can(membership.user, company_id, code):
            found.append(membership.user)
    return found


#: (code, what it is for, needed only when ...)
ROLES = (
    ("inventory.issue", "store keepers who issue goods", None),
    ("inventory.issue_approve", "department heads who approve store requisitions", None),
    ("inventory.adjust", "people who enter counts and adjustments", None),
    ("inventory.adjust_approve", "someone to approve adjustments and opening stock", None),
    ("purchase.order", "buyers", None),
    ("purchase.approve", "someone to approve purchase orders", None),
    ("purchase.receive", "people who receive goods (GRN)", None),
    ("purchase.qc", "quality checkers", "qc"),
    ("sales.order", "people who enter sales orders", None),
    ("sales.approve", "someone to confirm sales orders", None),
    ("sales.deliver", "people who deliver goods", None),
    ("inventory.period_close", "accounts, to close each month", None),
)


def readiness(company_id):
    """``[(section, [Check])]`` for the company, in the order the go-live weeks go."""
    from inventory.posting import ledger_differences, settings_for
    from organization import mail_settings
    from purchasing.models import Supplier
    from sales.models import Customer, PriceList

    with use_company(company_id):
        own = InventorySettings.objects.first()
        settings = settings_for(company_id)
        items = Item.objects.filter(status=ActiveStatus.ACTIVE)
        stores = Warehouse.objects.filter(status=ActiveStatus.ACTIVE)
        sections = []

        # 1. Decisions -------------------------------------------------------------
        checks = []
        if own is None:
            checks.append(Check(FAIL, "Inventory settings are not saved yet",
                                "Decide how stock is valued (one average for the company, or per store) - it is "
                                "fixed once stock moves - and the label sizes, then save.", "inventory:settings"))
        else:
            checks.append(Check(OK, "Inventory settings saved",
                                f"{settings.get_valuation_scope_display()}; labels {settings.small_label} and "
                                f"{settings.large_label} mm; expiry warning {settings.expiry_alert_days} days.",
                                "inventory:settings"))
        sections.append(("Decisions", checks))

        # 2. Masters ---------------------------------------------------------------
        checks = []
        units = Uom.objects.filter(status=ActiveStatus.ACTIVE).count()
        checks.append(Check(OK if units else FAIL, f"{units} units",
                            "" if units else "Add the standard units (Units page).", "inventory:units"))
        categories = ItemCategory.objects.filter(status=ActiveStatus.ACTIVE).count()
        checks.append(Check(OK if categories else FAIL, f"{categories} item categories", "", "inventory:categories"))
        count = items.count()
        checks.append(Check(OK if count else FAIL, f"{count} items",
                            "" if count else "Import the items template (Items → Import).", "inventory:items"))
        no_shelf = items.filter(track_expiry=True, shelf_life_days__isnull=True).count()
        if no_shelf:
            checks.append(Check(WARN, f"{no_shelf} item(s) keep expiry but have no shelf life",
                                "Expiry must then be typed on every receipt and output.", "inventory:items"))
        no_price = items.filter(kind=ItemKind.FINISHED, sale_price__isnull=True).count()
        if no_price:
            checks.append(Check(WARN, f"{no_price} finished good(s) without a sale price",
                                "Orders need a price list price or a typed price for them.", "inventory:items"))
        no_reorder = items.filter(reorder_point__isnull=True, min_stock__isnull=True).count()
        if count and no_reorder == count:
            checks.append(Check(INFO, "No item has a reorder point or minimum",
                                "The reorder report and the morning alert stay empty until some do.",
                                "inventory:items"))
        store_count = stores.count()
        checks.append(Check(OK if store_count else FAIL, f"{store_count} stores", "", "inventory:stores"))
        if items.filter(qc_required=True).exists() and not stores.filter(kind=Warehouse.Kind.QUARANTINE).exists():
            checks.append(Check(WARN, "Items need QC but there is no quarantine store",
                                "Goods waiting for QC are kept apart there.", "inventory:stores"))
        suppliers = Supplier.objects.filter(status=ActiveStatus.ACTIVE).count()
        checks.append(Check(OK if suppliers else WARN, f"{suppliers} suppliers", "", "inventory:suppliers"))
        customers = Customer.objects.filter(status=ActiveStatus.ACTIVE)
        checks.append(Check(OK if customers.exists() else WARN, f"{customers.count()} customers", "",
                            "inventory:customers"))
        named = set(customers.exclude(price_list_name="").values_list("price_list_name", flat=True))
        missing = sorted(named - set(PriceList.objects.values_list("name", flat=True)))
        if missing:
            checks.append(Check(WARN, "Price lists named on customers are not set up",
                                ", ".join(missing) + ": those customers get the item's sale price.",
                                "sales:price_lists"))
        sections.append(("Masters", checks))

        # 3. People ----------------------------------------------------------------
        checks = []
        needs_qc = items.filter(qc_required=True).exists()
        for code, what, when in ROLES:
            if when == "qc" and not needs_qc:
                continue
            people = _holders(company_id, code)
            if people:
                names = ", ".join(sorted(u.get_full_name() or u.email for u in people)[:4])
                checks.append(Check(OK, f"{code}: {len(people)} person(s)", names, "organization:access"))
            else:
                checks.append(Check(WARN, f"Nobody has {code} yet",
                                    f"Only the owner and administrators can act as {what}. Give it on the Access "
                                    "page.", "organization:access"))
        sections.append(("People and permissions", checks))

        # 4. Stock -------------------------------------------------------------------
        checks = []
        posted = OpeningStock.objects.filter(status="posted").count()
        drafts = OpeningStock.objects.filter(status="draft").count()
        checks.append(Check(OK if posted else FAIL, f"{posted} opening stock document(s) posted",
                            "" if posted else "Count every store, then load the count as opening stock.",
                            "inventory:opening"))
        if drafts:
            checks.append(Check(WARN, f"{drafts} opening stock draft(s) not posted", "Post or cancel them.",
                                "inventory:opening"))
        held = StockBalance.objects.filter(qty_on_hand__gt=0, batch__isnull=False).values_list("batch_id", flat=True)
        labelled = set(BarcodeLabel.objects.filter(batch_id__in=held, printed_count__gt=0).values_list("batch_id",
                                                                                                      flat=True))
        unlabelled = len(set(held) - labelled)
        if unlabelled:
            checks.append(Check(WARN, f"{unlabelled} batch(es) in stock have no printed label",
                                "Print them from the opening stock document (Print labels) and stick them on.",
                                "inventory:opening"))
        differences = len(ledger_differences(company_id))
        checks.append(Check(OK if not differences else FAIL,
                            "The ledger and the balances agree" if not differences
                            else f"{differences} difference(s) between the ledger and the balances",
                            "" if not differences else "Ask the developers to look (check_stock).",
                            "inventory:dashboard"))
        sections.append(("Stock", checks))

        # 5. Mail and alerts ----------------------------------------------------------
        checks = []
        how, _from, _name = mail_settings.sender(company_id)
        checks.append(Check(OK if how else WARN, "Email can be sent" if how else "Email is not set up",
                            "" if how else "Purchase orders by email, the stock alert and order summaries need it.",
                            "organization:mail_settings"))
        checks.append(Check(OK if settings.alert_emails else WARN,
                            "The morning stock alert has recipients" if settings.alert_emails
                            else "Nobody gets the morning stock alert", "", "inventory:settings"))
        from ai_assistant.claude import settings_for as ai_settings

        ai = ai_settings(company_id)
        checks.append(Check(INFO, "AI order assistant: " + ("on" if ai.ready else "off"),
                            "" if ai.ready else "Optional: needs the client's written consent and an API key.",
                            "ai_assistant:settings"))
        sections.append(("Mail and alerts", checks))
    return sections


def totals(sections):
    found = defaultdict(int)
    for _title, checks in sections:
        for check in checks:
            found[check.status] += 1
    return dict(found)


# --------------------------------------------------------------------------
# The count day
# --------------------------------------------------------------------------

COUNT_HEADINGS = ("Store code", "Bin code", "Item code", "Item name", "Batch no.", "Supplier batch no.",
                  "Manufacture date", "Expiry date", "Quantity", "Unit", "Unit cost (BDT)", "Notes")


def count_rows(company_id, store, *, kind="", everything=False):
    """One row per item to count in ``store``: items with stock there (each batch),
    else items whose usual store it is - or every active item."""
    with use_company(company_id):
        items = Item.objects.filter(status=ActiveStatus.ACTIVE).select_related("base_uom", "category")
        if kind:
            items = items.filter(kind=kind)
        held = list(StockBalance.objects.filter(warehouse=store, qty_on_hand__gt=0, item__in=items)
                    .select_related("item__base_uom", "batch", "location").order_by("item__code", "batch__expiry_date"))
        rows = []
        seen = set()
        for balance in held:
            item, batch = balance.item, balance.batch
            seen.add(item.pk)
            rows.append([store.code, balance.location.code if balance.location_id else "", item.code, item.name,
                         batch.batch_no if batch else "", batch.supplier_batch_no if batch else "",
                         batch.mfg_date if batch else None, batch.expiry_date if batch else None, None,
                         item.base_uom.code, item.standard_cost, ""])
        others = items.exclude(pk__in=seen)
        if not everything:
            # The items whose usual store it is; on a first count with no usual
            # stores set yet, every item.
            usual = others.filter(default_warehouse=store)
            if held or usual.exists():
                others = usual
        for item in others.order_by("category__name", "code"):
            rows.append([store.code, "", item.code, item.name, "", "", None, None, None, item.base_uom.code,
                         item.standard_cost, ""])
    return rows


def count_sheet(company_id, store, *, kind="", everything=False, fmt="xlsx"):
    """``(content, filename)``: the count sheet for ``store``."""
    from common import exports

    rows = count_rows(company_id, store, kind=kind, everything=everything)
    lines = [f"Store {store.code} · {store.name} - count sheet",
             "Write what is there: Quantity in the unit shown; batch number and expiry for batch items. "
             "A row left without a quantity is skipped when imported. Add rows for anything not listed.",
             "Import it as opening stock: Inventory → Opening stock → Import (keep the heading row)."]
    if fmt == "pdf":
        content = exports.table_pdf(title=f"Count sheet - {store.code}", lines=lines, headers=list(COUNT_HEADINGS),
                                    rows=[[exports._cell_text(v) for v in row] for row in rows],
                                    numeric=[8, 10], compact=True)
        return content, f"count-sheet-{store.code}.pdf"
    content = exports.table_xlsx(title="Opening stock", lines=lines, headers=list(COUNT_HEADINGS), rows=rows,
                                 numeric=[8, 10], widths={0: 12, 1: 10, 2: 14, 3: 34, 4: 16, 5: 16, 6: 16, 7: 14,
                                                          8: 12, 9: 8, 10: 14, 11: 24})
    return content, f"count-sheet-{store.code}.xlsx"


def opening_labels(*, actor, company_id, document_id):
    """A label for every batch (an item label for items without batches) the
    posted opening stock document brought in. Returns the labels, made if new."""
    from inventory import labels as label_service

    with use_company(company_id):
        document = OpeningStock.objects.filter(pk=document_id).first()
        if document is None:
            raise ValidationError("Opening stock not found.")
        if document.status != "posted":
            raise ValidationError("Labels are printed once the opening stock is posted.")
        lines = list(document.lines.select_related("item").order_by("pk"))
    access.require(actor, company_id, "inventory.labels")
    made, seen = [], set()
    for line in lines:
        if line.batch_no:
            batch = Batch.all_objects.filter(company_id=company_id, item=line.item, batch_no=line.batch_no).first()
            if batch is not None and ("b", batch.pk) not in seen:
                seen.add(("b", batch.pk))
                made.append(label_service.batch_label(actor=actor, company_id=company_id, batch_id=batch.pk))
                continue
        if ("i", line.item_id) not in seen and not line.batch_no:
            seen.add(("i", line.item_id))
            made.append(label_service.item_label(actor=actor, company_id=company_id, item_id=line.item_id))
    return made


# --------------------------------------------------------------------------
# The parallel run
# --------------------------------------------------------------------------

@dataclass
class Difference:
    item: str
    name: str
    store: str
    old: Decimal
    ours: Decimal
    unit: str

    @property
    def difference(self):
        return self.ours - self.old

    @property
    def status(self):
        if self.old == self.ours:
            return "Same"
        if self.ours == 0:
            return "Only in the old system"
        if self.old == 0:
            return "Only in the software"
        return "Different"


def compare(company_id, upload):
    """The old system's stock list (Item code, Quantity; Store code and Unit if it
    has them) against what the software holds now. ``(differences, problems)``."""
    from inventory.imports import SheetSpec, _decimal, _find_heading_row, _sheets, _text
    from inventory.masters import units_for

    spec = SheetSpec("Old stock", (("item", "Item code", True), ("store", "Store code", False),
                                   ("qty", "Quantity", True), ("unit", "Unit", False)))
    rows = None
    for _title, sheet in _sheets(upload):
        index, columns = _find_heading_row(sheet, spec)
        if index is not None:
            rows = [{key: _text(cells[i]) if i < len(cells) else "" for key, i in columns.items()}
                    for cells in sheet[index + 1:]]
            break
    if rows is None:
        raise ValidationError({"upload": "No sheet with the headings Item code and Quantity was found."})
    problems = []
    with use_company(company_id):
        items = {i.code: i for i in Item.objects.select_related("base_uom")}
        stores = {w.code: w for w in Warehouse.objects.all()}
        units = {u.code: u for u in Uom.objects.all()}
        by_store = any(row.get("store") for row in rows)
        old = defaultdict(lambda: ZERO)
        for number, row in enumerate(rows, start=1):
            if not any(row.values()):
                continue
            code = row.get("item", "").strip().upper()
            item = items.get(code)
            if item is None:
                problems.append(f"Row {number}: item {code or '(empty)'} is not in the software.")
                continue
            store = None
            if by_store:
                store = stores.get(row.get("store", "").strip().upper())
                if store is None:
                    problems.append(f"Row {number}: store {row.get('store') or '(empty)'} is not in the software.")
                    continue
            found = []
            qty = _decimal(row.get("qty"), "Quantity", found, required=True)
            if found:
                problems.append(f"Row {number}: {found[0]}")
                continue
            if row.get("unit"):
                unit = units.get(row["unit"].strip().upper())
                factor = units_for(item).get(unit.pk) if unit else None
                if factor is None:
                    problems.append(f"Row {number}: {row['unit']} is not a unit of {item.code}.")
                    continue
                qty = qty * factor
            old[(item.pk, store.pk if store else None)] += qty
        ours = defaultdict(lambda: ZERO)
        for item_id, store_id, qty in StockBalance.objects.values_list("item_id", "warehouse_id", "qty_on_hand"):
            ours[(item_id, store_id if by_store else None)] += qty
        by_id = {i.pk: i for i in items.values()}
        store_codes = {w.pk: w.code for w in stores.values()}
        differences = []
        for key in sorted(set(old) | {k for k, v in ours.items() if v}, key=lambda k: (by_id[k[0]].code,
                                                                                      store_codes.get(k[1], ""))):
            item = by_id[key[0]]
            differences.append(Difference(item.code, item.name, store_codes.get(key[1], "All stores"),
                                          old.get(key, ZERO), ours.get(key, ZERO), item.base_uom.code))
    return differences, problems
