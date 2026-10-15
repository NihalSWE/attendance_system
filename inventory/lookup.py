"""One lookup for every scan: code -> label / item / batch / bin.

A keyboard-wedge scanner types the code and presses Enter; the phone camera
hands over the same text. Whatever comes in, in this order:

1. a **QR from our labels** (``code|item|batch|...``): its first part is the
   label code;
2. a **GS1-128** scan (``]C1`` prefix, a leading group separator, or the
   ``(01)...`` written form): the item by its GTIN, the batch by number,
   the quantity from (30) or (310n);
3. a **label code** in the label register (package, batch, bin or item);
4. an item's **product barcode**, a **pack barcode** (the outer carton), or
   the **item code** itself.

The answer says what was found and what it means for a document (item,
batch, expiry, quantity in the base unit, store and bin), plus warnings
(quarantined or expired batch, label used up) and refusals (void label,
another company's label, unknown code).
"""

import datetime
import re
from decimal import Decimal, InvalidOperation

from common.tenant import use_company
from inventory.masters import units_for
from inventory.models import Batch, BarcodeLabel, Item, ItemUom
from tenants.models import Company

GS = "\x1d"
AIM = re.compile(r"^\][A-Za-z][0-9]")
#: GS1 application identifiers we read: fixed length, or None for variable.
AI_LENGTH = {"01": 14, "02": 14, "17": 6, "11": 6, "15": 6, "10": None, "21": None, "30": None,
             "37": None, **{f"310{d}": 6 for d in range(6)}}


def parse_gs1(raw):
    """``{ai: value}`` from a GS1 element string, or None if it is not one."""
    text = raw.strip()
    bracketed = re.findall(r"\((\d{2,4})\)([^(]*)", text)
    if bracketed and text.startswith("("):
        return {ai: value.strip() for ai, value in bracketed}
    if text.startswith("]C1") or text.startswith("]Q3") or text.startswith("]d2"):
        text = text[3:]
    elif text.startswith(GS):
        text = text[1:]
    elif not (text.startswith("01") and len(text) >= 16 and text[2:16].isdigit()):
        return None
    found = {}
    while text:
        ai = next((a for a in sorted(AI_LENGTH, key=len, reverse=True) if text.startswith(a)), None)
        if ai is None:
            return found or None
        text = text[len(ai):]
        length = AI_LENGTH[ai]
        if length:
            found[ai], text = text[:length], text[length:]
        else:
            value, _, text = text.partition(GS)
            found[ai] = value
        text = text.lstrip(GS)
    return found


def _batch_info(batch, today):
    if batch is None:
        return None
    return {"id": batch.pk, "batch_no": batch.batch_no, "status": batch.status,
            "status_label": batch.get_status_display(),
            "mfg_date": batch.mfg_date.isoformat() if batch.mfg_date else "",
            "expiry_date": batch.expiry_date.isoformat() if batch.expiry_date else "",
            "usable": batch.usable_on(today)}


def _item_info(item):
    return {"id": item.pk, "code": item.code, "name": item.name, "name_bn": item.name_bn,
            "base_uom": item.base_uom.code, "track_batch": item.track_batch}


def _warnings(result, batch, today):
    if batch is None:
        return
    if batch.status != Batch.Status.RELEASED:
        result["warnings"].append(f"Batch {batch.batch_no} is {batch.get_status_display().lower()}: "
                                  "it cannot be issued or sold.")
    elif batch.expiry_date and batch.expiry_date < today:
        result["warnings"].append(f"Batch {batch.batch_no} expired on {batch.expiry_date:%d %b %Y}.")


def _qty(value):
    try:
        return Decimal(value)
    except (InvalidOperation, TypeError):
        return None


def _result(kind, code, **values):
    return {"found": True, "kind": kind, "code": code, "label": None, "item": None, "batch": None,
            "quantity": "", "uom": "", "quantity_base": "", "warehouse": None, "location": None,
            "warnings": [], "message": "", **values}


def lookup(company_id, raw, today=None):
    """What ``raw`` (one scan) is in this company. Always a dict; ``found`` False
    with ``message`` when it is not something we know."""
    today = today or datetime.date.today()
    raw = (raw or "").replace("\r", "").replace("\n", "").strip()
    if not raw:
        return {"found": False, "message": "Nothing was scanned.", "code": ""}
    if AIM.match(raw) and not raw.startswith(("]C1", "]Q3", "]d2")):
        raw = raw[3:]
    with use_company(company_id):
        gs1 = parse_gs1(raw)
        if gs1 and ("01" in gs1 or "02" in gs1):
            return _from_gs1(company_id, raw, gs1, today)
        code = raw.split("|", 1)[0].strip()
        label = (BarcodeLabel.objects.filter(code=code)
                 .select_related("item__base_uom", "batch", "uom", "warehouse", "location").first())
        if label is not None:
            return _from_label(label, today)
        item = (Item.objects.filter(barcode=code).select_related("base_uom").first()
                or Item.objects.filter(code__iexact=code).select_related("base_uom").first())
        if item is not None:
            return _result("item", code, item=_item_info(item),
                           message=f"{item.code} · {item.name}")
        pack = ItemUom.objects.filter(barcode=code).select_related("item__base_uom", "uom").first()
        if pack is not None:
            return _result("pack", code, item=_item_info(pack.item), quantity="1",
                           uom=pack.uom.code, quantity_base=format(pack.factor.normalize(), "f"),
                           message=f"1 {pack.uom.code} of {pack.item.code} "
                                   f"({pack.factor.normalize():f} {pack.item.base_uom.code})")
    other = _other_company(code)
    return {"found": False, "code": code,
            "message": other or f"“{code}” is not a label, item or barcode in this company."}


def _other_company(code):
    """A label code names its company (P7-000123); say so when it is another's."""
    match = re.match(r"^[PBL](\d+)-\d{6,}$", code)
    if not match:
        return ""
    if Company.objects.filter(pk=int(match.group(1))).exists():
        return f"Label {code} belongs to another company."
    return ""


def _from_label(label, today):
    if label.status == BarcodeLabel.Status.VOID:
        return {"found": False, "code": label.code, "kind": label.kind,
                "message": f"Label {label.code} is void: {label.void_reason}"}
    result = _result(label.kind, label.code, label={
        "id": label.pk, "status": label.status, "status_label": label.get_status_display(),
        "printed_count": label.printed_count})
    if label.status == BarcodeLabel.Status.CONSUMED:
        result["warnings"].append(f"Label {label.code} is marked used up.")
    if label.item_id:
        result["item"] = _item_info(label.item)
    if label.batch_id:
        result["batch"] = _batch_info(label.batch, today)
        _warnings(result, label.batch, today)
    if label.quantity is not None:
        factor = units_for(label.item).get(label.uom_id, Decimal("1"))
        result.update(quantity=format(label.quantity.normalize(), "f"), uom=label.uom.code,
                      quantity_base=format((label.quantity * factor).normalize(), "f"))
    if label.warehouse_id:
        result["warehouse"] = {"id": label.warehouse_id, "code": label.warehouse.code,
                               "name": label.warehouse.name}
    if label.location_id:
        result["location"] = {"id": label.location_id, "code": label.location.code}
    if label.kind == BarcodeLabel.Kind.LOCATION:
        result["message"] = f"Bin {label.location.code} in {label.warehouse.code}"
    else:
        parts = [label.item.code, label.item.name]
        if label.batch_id:
            parts.append(f"batch {label.batch.batch_no}")
        if label.quantity is not None:
            parts.append(f"{result['quantity']} {result['uom']}")
        result["message"] = " · ".join(parts)
    return result


def _from_gs1(company_id, raw, gs1, today):
    gtin = gs1.get("01") or gs1.get("02")
    item = (Item.objects.filter(barcode__in={gtin, gtin.lstrip("0"), gtin[1:]})
            .select_related("base_uom").first())
    pack = None
    if item is None:
        pack = ItemUom.objects.filter(barcode__in={gtin, gtin[1:]}).select_related(
            "item__base_uom", "uom").first()
        item = pack.item if pack else None
    if item is None:
        return {"found": False, "code": raw, "kind": "gs1",
                "message": f"No item has the barcode {gtin}."}
    result = _result("gs1", raw, item=_item_info(item))
    # What the supplier's barcode says, even for a batch not in the software yet
    # (a goods receipt fills its new line from it).
    expiry = ""
    if len(gs1.get("17", "")) == 6 and gs1["17"].isdigit():
        yy, mm, dd = int(gs1["17"][:2]), int(gs1["17"][2:4]), int(gs1["17"][4:])
        try:
            expiry = datetime.date(2000 + yy, mm, dd or 1).isoformat()
        except ValueError:
            expiry = ""
    result["gs1"] = {"batch": gs1.get("10", ""), "expiry": expiry}
    batch = Batch.objects.filter(item=item, batch_no=gs1["10"]).first() if gs1.get("10") else None
    if gs1.get("10"):
        if batch is None:
            result["warnings"].append(f"Batch {gs1['10']} of {item.code} is not in the software.")
        else:
            result["batch"] = _batch_info(batch, today)
            _warnings(result, batch, today)
    qty, uom = None, item.base_uom.code
    for ai in (f"310{d}" for d in range(6)):
        if ai in gs1:
            qty = _qty(gs1[ai])
            qty = qty.scaleb(-int(ai[3])) if qty is not None else None
            uom = "KG"
    if qty is None and gs1.get("30"):
        qty = _qty(gs1["30"])
    if qty is None and gs1.get("37"):
        qty = _qty(gs1["37"])
    if qty is not None:
        factor = units_for(item).get(pack.uom_id, Decimal("1")) if pack and uom != "KG" else Decimal("1")
        result.update(quantity=format(qty.normalize(), "f"), uom=pack.uom.code if pack and uom != "KG" else uom,
                      quantity_base=format((qty * factor).normalize(), "f"))
    result["message"] = " · ".join(
        [item.code, item.name] + ([f"batch {gs1['10']}"] if gs1.get("10") else [])
        + ([f"{result['quantity']} {result['uom']}"] if qty is not None else []))
    return result
