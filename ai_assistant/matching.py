"""Matching what the AI read to our records - done in our code, so a wrong
guess by the AI is caught here: the customer by the sender's email or phone
first, then the code the AI suggests, then the name; each line to an item by
its code, a learned alias, its name or Bangla name; units to the item's own.
"""

import re
import unicodedata
from decimal import Decimal, InvalidOperation

from inventory.masters import units_for
from inventory.models import Item, Uom
from sales.models import Customer

#: Unit words customers use -> our unit codes (standard units, D2/Phase 1).
UNIT_WORDS = {
    "pc": "PCS", "pcs": "PCS", "piece": "PCS", "pieces": "PCS", "bottle": "PCS", "bottles": "PCS",
    "pis": "PCS", "পিস": "PCS", "টি": "PCS",
    "kg": "KG", "kgs": "KG", "kilo": "KG", "kilogram": "KG", "কেজি": "KG",
    "g": "G", "gm": "G", "gram": "G", "গ্রাম": "G",
    "l": "L", "ltr": "L", "litre": "L", "liter": "L", "লিটার": "L",
    "ml": "ML",
    "ctn": "CARTON", "carton": "CARTON", "cartons": "CARTON", "কার্টন": "CARTON",
    "box": "BOX", "boxes": "BOX", "বক্স": "BOX",
    "bag": "BAG", "bags": "BAG", "sack": "BAG", "বস্তা": "BAG",
    "drum": "DRUM", "drums": "DRUM", "roll": "ROLL", "rolls": "ROLL",
}


def normalize(text):
    """Lower case, Bangla digits as Latin, punctuation as spaces, single spaced."""
    text = unicodedata.normalize("NFC", str(text or "")).casefold()
    text = text.translate(str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789"))
    # Letters, digits and marks stay (Bangla vowel signs are marks, which
    # ``\w`` would drop); everything else is a space.
    text = "".join(c if unicodedata.category(c)[0] in "LMN" else " " for c in text)
    return " ".join(text.split())


def digits(text):
    found = re.sub(r"\D", "", str(text or "").translate(str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")))
    return found[-10:] if len(found) >= 10 else ""


def match_customer(company_id, sender, data):
    """``(customer or None, how, problem)``."""
    customers = list(Customer.all_objects.filter(company_id=company_id, status="active"))
    emails = {e.lower() for e in re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", f"{sender} {data.get('customer_email', '')}")}
    for customer in customers:
        if customer.email and customer.email.lower() in emails:
            return customer, "email", ""
    phones = {digits(data.get("customer_phone")), digits(sender)} - {""}
    for customer in customers:
        if phones & ({digits(customer.phone), digits(customer.whatsapp)} - {""}):
            return customer, "phone", ""
    code = (data.get("matched_customer_code") or "").strip()
    by_code = next((c for c in customers if c.code == code), None) if code else None
    if by_code is not None:
        return by_code, "ai", f"Customer {by_code.name} was matched by name only: check it."
    name = normalize(data.get("customer_name"))
    if name:
        same = [c for c in customers if normalize(c.name) == name]
        if len(same) == 1:
            return same[0], "name", f"Customer {same[0].name} was matched by name only: check it."
    return None, "", "The customer was not found: choose them."


def _items(company_id):
    from ai_assistant.models import ItemAlias

    items = {i.pk: i for i in Item.all_objects.filter(company_id=company_id, status="active").select_related(
        "base_uom", "default_warehouse")}
    by_key = {}
    for item in items.values():
        for key in (normalize(item.code), normalize(item.name), normalize(item.name_bn)):
            if key:
                by_key.setdefault(key, set()).add(item.pk)
    aliases = {alias.normalized: alias.item_id
               for alias in ItemAlias.all_objects.filter(company_id=company_id, item_id__in=items)}
    return items, by_key, aliases


def match_item(company_id, line, cache=None):
    """``(item or None, how)`` for one line the AI read."""
    items, by_key, aliases = cache or _items(company_id)
    text = normalize(line.get("product_text"))
    if text in aliases:
        return items[aliases[text]], "alias"
    code = (line.get("suggested_item_code") or "").strip()
    suggested = next((i for i in items.values() if i.code == code), None) if code else None
    if text in by_key and len(by_key[text]) == 1:
        return items[next(iter(by_key[text]))], "name"
    if suggested is not None:
        return suggested, "ai"
    contains = {pk for key, pks in by_key.items() if text and (text in key or key in text) and len(key) > 3
                for pk in pks}
    if len(contains) == 1:
        return items[contains.pop()], "partial"
    return None, ""


def match_unit(company_id, item, unit_text):
    """``(uom, problem)``: the item's unit the customer meant, else its base unit with a problem."""
    allowed = units_for(item)
    units = {u.pk: u for u in Uom.all_objects.filter(company_id=company_id, pk__in=allowed)}
    word = normalize(unit_text)
    code = UNIT_WORDS.get(word, word.upper())
    for uom in units.values():
        if uom.code.upper() == code or normalize(uom.name) == word:
            return uom, ""
    if word:
        return item.base_uom, (f"{item.code}: unit \"{unit_text}\" is not one of its units; "
                               f"{item.base_uom.code} was used - check the quantity.")
    return item.base_uom, ""


def quantity(value):
    try:
        qty = Decimal(str(value)).quantize(Decimal("0.0001"))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return qty if qty > 0 else None
