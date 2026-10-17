"""Excel import of the Phase 0 templates (docs/inventory/templates/).

The same steps as the employee import: upload, a **preview** that writes
nothing and names every bad row with its line and the reason, then
**confirm**, which checks everything again and writes all rows or none, each
through its service (so permissions, validation and the audit log apply).

- The headings are the templates' (``docs/scripts/build_inventory_templates.py``);
  a trailing ``*`` and spaces/case are ignored. The "How to fill" and
  "Example" sheets are never read.
- A code already in the software is **skipped**, not changed: uploading the
  file again after adding rows adds only the new ones. Editing stays on the
  pages, one row at a time.
- Items: a category named in the file and not yet set up is created (its code
  made from its name) and listed in the preview. Units must exist already.
- Opening stock: the rows become the lines of a new **draft** opening stock;
  posting it stays a separate, deliberate step on its page.
"""

import datetime
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from common.choices import ActiveStatus
from common.tenant import use_company
from inventory import access, masters, opening
from inventory.models import (
    Item,
    ItemCategory,
    ItemKind,
    ItemUom,
    Location,
    OpeningStock,
    Uom,
    Warehouse,
)
from organization.import_services import (
    BIFF_SIGNATURES,
    _looks_like_html,
    _rows_from_csv,
    _rows_from_xls,
    _rows_from_xlsx,
    _text,
)
from organization.models import Branch
from purchasing.models import Supplier
from purchasing.services import SUPPLIER
from sales.models import Customer
from sales.services import CUSTOMER

MAX_ROWS = 5000
SKIPPED_SHEETS = {"howtofill", "example"}


def _word(text):
    return re.sub(r"[\W_]+", "", str(text or "").replace("*", "").casefold())


# --- reading cells -------------------------------------------------------------

def _decimal(text, label, problems, *, required=False, positive=False):
    text = (text or "").replace(",", "").replace("৳", "").strip()
    if not text:
        if required:
            problems.append(f"{label} is missing.")
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        problems.append(f"{label} “{text}” is not a number.")
        return None
    if value < 0 or (positive and value == 0):
        problems.append(f"{label} must be {'above' if positive else 'at least'} zero.")
        return None
    return value


def _whole(text, label, problems):
    value = _decimal(text, label, problems)
    if value is not None and value != value.to_integral_value():
        problems.append(f"{label} must be a whole number.")
        return None
    return None if value is None else int(value)


def _yes(text, label, problems, default=False):
    word = _word(text)
    if not word:
        return default
    if word in ("yes", "y", "true", "1", "হ্যাঁ"):
        return True
    if word in ("no", "n", "false", "0", "না"):
        return False
    problems.append(f"{label} must be Yes or No.")
    return default


def _date(text, label, problems):
    text = (text or "").strip()
    if not text:
        return None
    for pattern in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y/%m/%d"):
        try:
            return datetime.datetime.strptime(text[:10], pattern).date()
        except ValueError:
            continue
    problems.append(f"{label} “{text}” is not a date (DD-MM-YYYY).")
    return None


def _choice(text, choices, label, problems, aliases=None):
    word = _word(text)
    if not word:
        problems.append(f"{label} is missing.")
        return None
    lookup = {**{_word(v): v for v, _ in choices}, **{_word(l): v for v, l in choices},
              **(aliases or {})}
    if word not in lookup:
        problems.append(f"{label} “{text}” is not one of: {', '.join(l for _, l in choices)}.")
        return None
    return lookup[word]


# --- the file ------------------------------------------------------------------

@dataclass(frozen=True)
class SheetSpec:
    name: str
    columns: tuple  # (key, heading, required)


SPECS = {
    "stores": (
        SheetSpec("Stores", (("branch", "Branch", True), ("code", "Store code", True),
                             ("name", "Store name", True), ("kind", "Store type", True),
                             ("notes", "Notes", False))),
        SheetSpec("Bins", (("store", "Store code", True), ("code", "Bin code", True),
                           ("description", "Description", False))),
    ),
    "items": (
        SheetSpec("Items", (
            ("code", "Item code", True), ("name", "Item name", True),
            ("name_bn", "Name in Bangla", False), ("kind", "Item type", True),
            ("category", "Category", True), ("base_uom", "Base unit", True),
            ("barcode", "Barcode", False), ("track_batch", "Track batch", False),
            ("track_expiry", "Track expiry", False), ("shelf_life_days", "Shelf life (days)", False),
            ("qc_required", "QC required", False), ("min_stock", "Min stock", False),
            ("reorder_point", "Reorder point", False), ("reorder_qty", "Reorder qty", False),
            ("max_stock", "Max stock", False), ("default_warehouse", "Default store code", False),
            ("standard_cost", "Standard cost (BDT)", False), ("sale_price", "Sale price (BDT)", False),
            ("vat_rate", "VAT %", False), ("hs_code", "HS code", False), ("notes", "Notes", False),
        )),
        SheetSpec("Pack units", (("item", "Item code", True), ("uom", "Pack unit", True),
                                 ("factor", "Base units in one pack", True),
                                 ("barcode", "Pack barcode", False))),
    ),
    "suppliers": (
        SheetSpec("Suppliers", (
            ("code", "Supplier code", True), ("name", "Supplier name", True),
            ("contact_person", "Contact person", False), ("phone", "Phone", False),
            ("email", "Email", False), ("address", "Address", False), ("bin", "BIN", False),
            ("tin", "TIN", False), ("payment_terms_days", "Payment terms (days)", False),
            ("items_supplied", "Items supplied", False), ("notes", "Notes", False),
        )),
    ),
    "customers": (
        SheetSpec("Customers", (
            ("code", "Customer code", True), ("name", "Customer name", True),
            ("customer_type", "Customer type", True), ("contact_person", "Contact person", False),
            ("phone", "Phone", False), ("whatsapp", "WhatsApp number", False),
            ("email", "Email", False), ("address", "Address", False),
            ("territory", "Territory", False), ("credit_limit", "Credit limit (BDT)", False),
            ("payment_terms_days", "Payment terms (days)", False),
            ("price_list_name", "Price list", False), ("notes", "Notes", False),
        )),
    ),
    "opening": (
        SheetSpec("Opening stock", (
            ("store", "Store code", True), ("bin", "Bin code", False), ("item", "Item code", True),
            ("batch_no", "Batch no.", False), ("supplier_batch_no", "Supplier batch no.", False),
            ("mfg_date", "Manufacture date", False), ("expiry_date", "Expiry date", False),
            ("qty", "Quantity", True), ("uom", "Unit", True),
            ("unit_cost", "Unit cost (BDT)", True), ("note", "Notes", False),
        )),
    ),
}

TITLES = {"stores": "Stores and bins", "items": "Items and pack units", "suppliers": "Suppliers",
          "customers": "Customers", "opening": "Opening stock"}
TEMPLATE_FILES = {"stores": "01-stores.xlsx", "items": "02-items.xlsx",
                  "suppliers": "03-suppliers.xlsx", "customers": "04-customers.xlsx",
                  "opening": "06-opening-stock.xlsx"}


def _sheets(upload):
    name = (getattr(upload, "name", "") or "").lower()
    data = upload.read()
    if not data:
        raise ValidationError({"upload": "That file is empty."})
    if data.startswith(b"PK\x03\x04"):
        return _rows_from_xlsx(data)
    if data.startswith(BIFF_SIGNATURES):
        return _rows_from_xls(data)
    if name.endswith((".csv", ".txt", ".xls", ".xlsx")) and not _looks_like_html(data):
        return [("", _rows_from_csv(data))]
    raise ValidationError({"upload": "Upload the filled template (.xlsx), or a .xls or .csv "
                                     "file with the same headings."})


def _find_heading_row(rows, spec):
    wanted = {_word(heading): key for key, heading, _ in spec.columns}
    required = {_word(heading) for _, heading, req in spec.columns if req}
    for index, row in enumerate(rows[:15]):
        words = [_word(cell) for cell in row]
        if required <= set(words):
            return index, {wanted[w]: i for i, w in enumerate(words) if w in wanted}
    return None, None


def read(kind, upload):
    """``{sheet name: [{"line": n, key: text, ...}]}``: plain text only, so it
    can wait in the session between the preview and Confirm."""
    sheets = [(title, rows) for title, rows in _sheets(upload) if _word(title) not in SKIPPED_SHEETS]
    if not sheets:
        raise ValidationError({"upload": "That workbook has only the How to fill and Example sheets."})
    found, total = {}, 0
    for spec in SPECS[kind]:
        candidates = [s for s in sheets if _word(s[0]) == _word(spec.name)] or (
            sheets if len(SPECS[kind]) == 1 or not sheets[0][0] else [])
        for _title, rows in candidates:
            index, columns = _find_heading_row(rows, spec)
            if index is None:
                continue
            body = []
            for number, cells in enumerate(rows[index + 1:], start=index + 2):
                values = {key: _text(cells[i]) if i < len(cells) else ""
                          for key, i in columns.items()}
                if any(values.values()):
                    body.append({"line": number, **values})
            found[spec.name] = body
            total += len(body)
            break
    if not found:
        headings = ", ".join(h for _, h, req in SPECS[kind][0].columns if req)
        raise ValidationError({"upload": f"No sheet with the headings {headings} was found. "
                                         "Use the template and keep its heading row."})
    if total == 0:
        raise ValidationError({"upload": "The file has the headings but no rows."})
    if total > MAX_ROWS:
        raise ValidationError({"upload": f"That file has {total} rows. Split it into files of "
                                         f"{MAX_ROWS} or fewer."})
    return found


# --- checking ------------------------------------------------------------------

@dataclass
class Row:
    sheet: str
    line: int
    label: str
    action: str = "add"   # add | skip | error
    problems: list = None
    values: dict = None
    note: str = ""

    def refuse(self, *problems):
        self.problems = (self.problems or []) + list(problems)
        self.action = "error"


def _require_permission(kind, actor, company_id):
    code = "inventory.adjust" if kind == "opening" else masters.CODE
    access.require(actor, company_id, code)


def check(kind, actor, company_id, data, work_date=None):
    """``(rows, extra)``: one ``Row`` per file row, and anything made on the way
    (new categories) for the preview. Writes nothing."""
    _require_permission(kind, actor, company_id)
    with use_company(company_id):
        return CHECKS[kind](actor, company_id, data, work_date)


def _code_rows(sheet, rows, existing, *, label_key="name"):
    """Rows with their code checked: missing, repeated in the file, already here."""
    seen, out = set(), []
    for raw in rows:
        code = raw.get("code", "").strip().upper()
        row = Row(sheet, raw["line"], f"{code} · {raw.get(label_key, '')}".strip(" ·"))
        if not code:
            row.refuse("The code is missing.")
        elif code in seen:
            row.refuse(f"{code} appears twice in the file.")
        elif code in existing:
            row.action, row.note = "skip", "Already here: skipped."
        seen.add(code)
        out.append((row, raw, code))
    return out


def _check_stores(actor, company_id, data, _day):
    branches = {_word(b.name): b for b in Branch.objects.all()}
    branches.update({_word(b.code): b for b in Branch.objects.all()})
    stores = {w.code: w for w in Warehouse.objects.all()}
    rows, new_stores = [], {}
    for row, raw, code in _code_rows("Stores", data.get("Stores", []), stores):
        if row.action == "add":
            problems = []
            branch = branches.get(_word(raw.get("branch")))
            if branch is None:
                problems.append(f"Branch “{raw.get('branch')}” is not set up in the software.")
            elif not access.may(actor, company_id, masters.CODE, branch.pk):
                problems.append(f"You cannot set up stores in {branch.name}.")
            kind = _choice(raw.get("kind"), Warehouse.Kind.choices, "Store type", problems)
            if not raw.get("name"):
                problems.append("The store name is missing.")
            if problems:
                row.refuse(*problems)
            else:
                row.values = {"branch": branch, "code": code, "name": raw["name"], "kind": kind,
                              "notes": raw.get("notes", "")[:255]}
                new_stores[code] = row.values
        rows.append(row)
    seen = set()
    for raw in data.get("Bins", []):
        store_code, code = raw.get("store", "").upper(), raw.get("code", "").upper()
        row = Row("Bins", raw["line"], f"{store_code} / {code}")
        store = stores.get(store_code)
        if (store_code, code) in seen:
            row.refuse("This bin appears twice in the file.")
        elif not code:
            row.refuse("The bin code is missing.")
        elif store is None and store_code not in new_stores:
            row.refuse(f"Store {store_code or '(empty)'} is not in the software or the Stores sheet.")
        elif store is not None and Location.objects.filter(warehouse=store, code=code).exists():
            row.action, row.note = "skip", "Already here: skipped."
        elif store is not None and not access.may(actor, company_id, masters.CODE, store.branch_id):
            row.refuse(f"You cannot set up bins in {store.code}.")
        else:
            row.values = {"store": store_code, "code": code,
                          "description": raw.get("description", "")[:255]}
        seen.add((store_code, code))
        rows.append(row)
    return rows, {}


ITEM_KIND_ALIASES = {"general": ItemKind.GENERAL, "spareparts": ItemKind.SPARE,
                     "rawmaterials": ItemKind.RAW, "finished": ItemKind.FINISHED,
                     "packagingmaterial": ItemKind.PACKAGING}


def _category_code(name, taken):
    base = re.sub(r"[^A-Z0-9]+", "-", name.upper()).strip("-")[:28] or "CAT"
    code, n = base, 1
    while code in taken:
        n += 1
        code = f"{base}-{n}"
    taken.add(code)
    return code


def _check_items(actor, company_id, data, _day):
    items = {i.code: i for i in Item.objects.all()}
    units = {u.code: u for u in Uom.objects.filter(status=ActiveStatus.ACTIVE)}
    stores = {w.code: w for w in Warehouse.objects.filter(status=ActiveStatus.ACTIVE)}
    categories = list(ItemCategory.objects.filter(status=ActiveStatus.ACTIVE))
    category_codes = set(ItemCategory.objects.values_list("code", flat=True))
    new_categories = {}
    barcodes = set(Item.objects.exclude(barcode="").values_list("barcode", flat=True))
    rows = []
    for row, raw, code in _code_rows("Items", data.get("Items", []), items):
        if row.action != "add":
            rows.append(row)
            continue
        p = []
        kind = _choice(raw.get("kind"), ItemKind.choices, "Item type", p, ITEM_KIND_ALIASES)
        name = raw.get("name", "")
        if not name:
            p.append("The item name is missing.")
        unit = units.get(raw.get("base_uom", "").upper())
        if unit is None:
            p.append(f"Unit “{raw.get('base_uom')}” is not set up (Inventory → Units).")
        category, category_name = None, raw.get("category", "").strip()
        if not category_name:
            p.append("The category is missing.")
        elif kind:
            category = next((c for c in categories if c.kind == kind and _word(category_name) in
                             (_word(c.name), _word(c.code))), None)
            if category is None:
                key = (kind, _word(category_name))
                if key not in new_categories:
                    new_categories[key] = {"code": _category_code(category_name, category_codes),
                                           "name": category_name[:128], "kind": kind}
                category = key
        track_batch = _yes(raw.get("track_batch"), "Track batch", p)
        track_expiry = _yes(raw.get("track_expiry"), "Track expiry", p)
        if track_expiry and not track_batch:
            p.append("An item with an expiry date must also keep batches (Track batch: Yes).")
        barcode = raw.get("barcode", "").strip()
        if barcode and barcode in barcodes:
            p.append(f"Barcode {barcode} belongs to another item.")
        store_code = raw.get("default_warehouse", "").upper()
        store = stores.get(store_code) if store_code else None
        if store_code and store is None:
            p.append(f"Store {store_code} is not set up.")
        values = {
            "code": code, "name": name[:255], "name_bn": raw.get("name_bn", "")[:255],
            "kind": kind, "category": category, "base_uom": unit, "barcode": barcode[:64],
            "track_batch": track_batch, "track_expiry": track_expiry,
            "shelf_life_days": _whole(raw.get("shelf_life_days"), "Shelf life", p),
            "qc_required": _yes(raw.get("qc_required"), "QC required", p),
            "min_stock": _decimal(raw.get("min_stock"), "Min stock", p),
            "reorder_point": _decimal(raw.get("reorder_point"), "Reorder point", p),
            "reorder_qty": _decimal(raw.get("reorder_qty"), "Reorder qty", p),
            "max_stock": _decimal(raw.get("max_stock"), "Max stock", p),
            "default_warehouse": store,
            "standard_cost": _decimal(raw.get("standard_cost"), "Standard cost", p),
            "sale_price": _decimal(raw.get("sale_price"), "Sale price", p),
            "vat_rate": _decimal(raw.get("vat_rate"), "VAT %", p),
            "hs_code": raw.get("hs_code", "")[:32], "notes": raw.get("notes", "")[:255],
        }
        if values["vat_rate"] is not None and values["vat_rate"] > 100:
            p.append("VAT % cannot be above 100.")
        if p:
            row.refuse(*p)
        else:
            row.values = values
            if barcode:
                barcodes.add(barcode)
        rows.append(row)
    in_file = {r.values["code"]: r.values for r in rows if r.action == "add"}
    seen = set()
    for raw in data.get("Pack units", []):
        item_code, uom_code = raw.get("item", "").upper(), raw.get("uom", "").upper()
        row = Row("Pack units", raw["line"], f"{item_code}: 1 {uom_code}")
        p = []
        item = items.get(item_code)
        base = item.base_uom if item else (in_file.get(item_code) or {}).get("base_uom")
        if item is None and item_code not in in_file:
            p.append(f"Item {item_code or '(empty)'} is not in the software or the Items sheet.")
        unit = units.get(uom_code)
        if unit is None:
            p.append(f"Unit “{raw.get('uom')}” is not set up.")
        elif base is not None and unit.pk == base.pk:
            p.append("That is the item's base unit already.")
        factor = _decimal(raw.get("factor"), "Base units in one pack", p, required=True, positive=True)
        if (item_code, uom_code) in seen:
            p.append("This pack appears twice in the file.")
        seen.add((item_code, uom_code))
        if p:
            row.refuse(*p)
        elif item is not None and ItemUom.objects.filter(item=item, uom=unit).exists():
            row.action, row.note = "skip", "Already here: skipped."
        else:
            row.values = {"item": item_code, "uom": unit, "factor": factor,
                          "barcode": raw.get("barcode", "")[:64]}
            row.label = f"{item_code}: 1 {uom_code} = {factor.normalize():f} {base.code if base else ''}"
        rows.append(row)
    return rows, {"categories": list(new_categories.values()), "category_keys": new_categories}


def _check_simple(model, sheet, numeric):
    def check_rows(actor, company_id, data, _day):
        existing = set(model.objects.values_list("code", flat=True))
        rows = []
        for row, raw, code in _code_rows(sheet, data.get(sheet, []), existing):
            if row.action == "add":
                p = []
                values = {key: raw.get(key, "") for key in raw if key != "line"}
                values["code"] = code
                if not values.get("name"):
                    p.append("The name is missing.")
                for key, label in numeric.items():
                    if key == "payment_terms_days":
                        values[key] = _whole(raw.get(key), label, p) or 0
                    else:
                        values[key] = _decimal(raw.get(key), label, p) or Decimal("0")
                if model is Customer:
                    values["customer_type"] = _choice(raw.get("customer_type"), Customer.Type.choices,
                                                      "Customer type", p)
                instance = model(company_id=company_id, **values)
                try:
                    instance.full_clean(exclude=["company", "created_by", "updated_by"],
                                        validate_unique=False, validate_constraints=False)
                except ValidationError as exc:
                    p.extend(f"{model._meta.get_field(f).verbose_name.capitalize()}: {m}"
                             if f in values else m for f, ms in exc.message_dict.items() for m in ms)
                if p:
                    row.refuse(*p)
                else:
                    row.values = values
            rows.append(row)
        return rows, {}
    return check_rows


def _check_opening(actor, company_id, data, work_date):
    if work_date is None:
        raise ValidationError({"work_date": "Give the opening day."})
    stores = {w.code: w for w in Warehouse.objects.select_related("branch")}
    bins = {(b.warehouse_id, b.code): b for b in Location.objects.filter(status=ActiveStatus.ACTIVE)}
    items = {i.code: i for i in Item.objects.select_related("base_uom")}
    units = {u.code: u for u in Uom.objects.filter(status=ActiveStatus.ACTIVE)}
    document = OpeningStock(company_id=company_id, work_date=work_date)
    rows, batch_dates = [], {}
    for raw in data.get("Opening stock", []):
        item_code, store_code = raw.get("item", "").upper(), raw.get("store", "").upper()
        row = Row("Opening stock", raw["line"],
                  f"{store_code} · {item_code} {raw.get('batch_no', '')}".strip())
        p = []
        if not (raw.get("qty") or "").strip():
            # A count sheet's row nobody wrote a quantity on: not counted (Phase 10).
            row.action, row.note = "skip", "No quantity: not counted, left out."
            rows.append(row)
            continue
        store, item = stores.get(store_code), items.get(item_code)
        if store is None:
            p.append(f"Store {store_code or '(empty)'} is not set up.")
        elif not access.may(actor, company_id, "inventory.adjust", store.branch_id):
            p.append(f"You cannot enter stock in {store.code}.")
        if item is None:
            p.append(f"Item {item_code or '(empty)'} is not set up.")
        location = None
        if raw.get("bin") and store is not None:
            location = bins.get((store.pk, raw["bin"].upper()))
            if location is None:
                p.append(f"Bin {raw['bin']} is not set up in {store_code}.")
        unit = units.get(raw.get("uom", "").upper())
        if unit is None:
            p.append(f"Unit “{raw.get('uom')}” is not set up.")
        qty = _decimal(raw.get("qty"), "Quantity", p, required=True, positive=True)
        cost = _decimal(raw.get("unit_cost"), "Unit cost", p, required=True)
        mfg = _date(raw.get("mfg_date"), "Manufacture date", p)
        expiry = _date(raw.get("expiry_date"), "Expiry date", p)
        if not p:
            values = {"warehouse": store, "location": location, "item": item,
                      "batch_no": raw.get("batch_no", "")[:64],
                      "supplier_batch_no": raw.get("supplier_batch_no", "")[:64],
                      "mfg_date": mfg, "expiry_date": expiry, "qty": qty, "uom": unit,
                      "unit_cost": cost, "note": raw.get("note", "")[:255]}
            try:
                values = opening.clean_line(document, values)
            except ValidationError as exc:
                p.extend(m for ms in exc.message_dict.values() for m in ms)
            else:
                key = (item.pk, values["batch_no"])
                dates = (values["mfg_date"], values["expiry_date"])
                if values["batch_no"] and batch_dates.setdefault(key, dates) != dates:
                    p.append(f"Batch {values['batch_no']} has other dates on another line.")
                values.pop("factor", None)
                row.values = values
        if p:
            row.refuse(*p)
        rows.append(row)
    return rows, {}


CHECKS = {
    "stores": _check_stores,
    "items": _check_items,
    "suppliers": _check_simple(Supplier, "Suppliers", {"payment_terms_days": "Payment terms"}),
    "customers": _check_simple(Customer, "Customers", {"credit_limit": "Credit limit",
                                                       "payment_terms_days": "Payment terms"}),
    "opening": _check_opening,
}


def summary(rows):
    return {"add": sum(r.action == "add" for r in rows), "skip": sum(r.action == "skip" for r in rows),
            "error": sum(r.action == "error" for r in rows)}


# --- writing -------------------------------------------------------------------

@transaction.atomic
def commit(kind, *, actor, company_id, data, work_date=None):
    """Check again, then write every new row or none. Returns what was made:
    a count, or the new draft opening stock."""
    rows, extra = check(kind, actor, company_id, data, work_date)
    if any(row.action == "error" for row in rows):
        raise ValidationError("The file has rows that cannot be imported. Fix them and upload again.")
    adding = [row for row in rows if row.action == "add"]
    if not adding:
        raise ValidationError("Every row is in the software already; nothing to import.")
    kw = {"actor": actor, "company_id": company_id}
    if kind == "opening":
        document = opening.create_document(**kw, values={"work_date": work_date,
                                                         "note": "Imported from Excel"})
        for row in adding:
            opening.add_line(**kw, document_id=document.pk, values=row.values)
        return document
    if kind == "stores":
        made = {}
        for row in adding:
            if row.sheet == "Stores":
                made[row.values["code"]] = masters.create(masters.WAREHOUSE, **kw, values=row.values)
        with use_company(company_id):
            stores = {w.code: w for w in Warehouse.objects.all()}
        for row in adding:
            if row.sheet == "Bins":
                values = dict(row.values, warehouse=stores[row.values.pop("store")])
                masters.create(masters.LOCATION, **kw, values=values)
        return len(adding)
    if kind == "items":
        categories = {key: masters.create(masters.CATEGORY, **kw, values=dict(values, parent=None))
                      for key, values in extra["category_keys"].items()}
        for row in adding:
            if row.sheet == "Items":
                values = dict(row.values)
                if isinstance(values["category"], tuple):
                    values["category"] = categories[values["category"]]
                masters.create(masters.ITEM, **kw, values=values)
        with use_company(company_id):
            items = {i.code: i for i in Item.objects.all()}
        for row in adding:
            if row.sheet == "Pack units":
                values = dict(row.values)
                masters.add_pack_unit(**kw, item_id=items[values.pop("item")].pk, values=values)
        return len(adding)
    master = {"suppliers": SUPPLIER, "customers": CUSTOMER}[kind]
    for row in adding:
        masters.create(master, **kw, values=row.values)
    return len(adding)
