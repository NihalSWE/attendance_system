"""Masters: items, units, categories, stores, bins, suppliers and customers -
the panel's own forms and ``inventory.masters``."""

from api.v1.inventory.engine import MasterSpec
from inventory.views import PAGES

VIEWERS = ["Anyone with View stock and items (inventory.view); owner and administrator"]
KEEPERS = ["Set up items, stores, suppliers and customers (inventory.items) - in the store's branch for stores "
           "and bins"]


def _spec(key, path, title, singular, about, example, out_example, scopes=("inventory:read", "inventory:write")):
    return MasterSpec(key=key, path=path, title=title, singular=singular, page=PAGES[key], about=about,
                      read_roles=VIEWERS, write_roles=KEEPERS, scopes=scopes, panel=f"inventory:{key}",
                      create_example=example, out_example=out_example)


SPECS = (
    _spec("items", "inventory/items", "Items", "item",
          "Everything the factory buys, makes, stores or sells: code, names (English and Bangla), type, category, "
          "base unit, batches and expiry, QC, reorder levels, cost, sale price and VAT.",
          {"code": "RM-0001", "name": "Sugar (refined)", "name_bn": "চিনি", "kind": "raw", "category_id": 2,
           "base_uom_id": 1, "track_batch": True, "track_expiry": True, "shelf_life_days": 365,
           "reorder_point": "500", "reorder_qty": "2000", "sale_price": "95", "vat_rate": "5"},
          {"id": 7, "status": "active", "code": "RM-0001", "name": "Sugar (refined)", "name_bn": "চিনি",
           "kind": "raw", "category": {"id": 2, "code": "SWT", "name": "Sweetener"},
           "base_uom": {"id": 1, "code": "KG", "name": "Kilogram"}, "barcode": "", "track_batch": True,
           "track_expiry": True, "shelf_life_days": 365, "qc_required": False, "min_stock": None,
           "reorder_point": "500", "reorder_qty": "2000", "max_stock": None, "default_warehouse": None,
           "standard_cost": None, "sale_price": "95", "vat_rate": "5", "hs_code": "", "notes": ""}),
    _spec("units", "inventory/units", "Units", "unit",
          "The units stock is counted in (KG, PCS, CARTON ...) and how many decimals a quantity may have.",
          {"code": "DOZ", "name": "Dozen", "kind": "count", "decimals": 0},
          {"id": 15, "status": "active", "code": "DOZ", "name": "Dozen", "kind": "count", "decimals": 0}),
    _spec("categories", "inventory/categories", "Item categories", "category",
          "The client's own grouping of items, each under one of the five item types.",
          {"code": "SWT", "name": "Sweetener", "kind": "raw"},
          {"id": 2, "status": "active", "code": "SWT", "name": "Sweetener", "kind": "raw", "parent": None}),
    _spec("stores", "inventory/stores", "Stores", "store",
          "Each store belongs to a branch; stock, documents and permissions follow its branch.",
          {"branch_id": 1, "code": "RMS", "name": "Raw material store", "kind": "raw"},
          {"id": 3, "status": "active", "branch": {"id": 1, "code": "HQ", "name": "Head office"}, "code": "RMS",
           "name": "Raw material store", "kind": "raw", "notes": ""}),
    _spec("bins", "inventory/bins", "Bins", "bin",
          "Racks, shelves and bins inside a store, so a scan says exactly where. Optional.",
          {"warehouse_id": 3, "code": "A-01-02", "description": "Rack A, shelf 1"},
          {"id": 9, "status": "active", "warehouse": {"id": 3, "code": "RMS", "name": "Raw material store"},
           "code": "A-01-02", "description": "Rack A, shelf 1"}),
    _spec("suppliers", "inventory/suppliers", "Suppliers", "supplier",
          "Who the company buys from: contact, BIN/TIN and payment terms.",
          {"code": "SUP-0004", "name": "Deshbandhu Sugar Mills", "phone": "01711000000", "payment_terms_days": 30},
          {"id": 4, "status": "active", "code": "SUP-0004", "name": "Deshbandhu Sugar Mills", "contact_person": "",
           "phone": "01711000000", "email": "", "address": "", "bin": "", "tin": "", "payment_terms_days": 30,
           "items_supplied": "", "notes": ""}),
    _spec("customers", "inventory/customers", "Customers", "customer",
          "Who the company sells to: type, contact, credit limit, payment terms and price list. Their email and "
          "WhatsApp number let the AI order assistant recognise them.",
          {"code": "CUS-0012", "name": "Sylhet Distribution House", "customer_type": "distributor",
           "email": "orders@sylhet.example", "credit_limit": "500000", "payment_terms_days": 30,
           "price_list_name": "Distributor"},
          {"id": 6, "status": "active", "code": "CUS-0012", "name": "Sylhet Distribution House",
           "customer_type": "distributor", "contact_person": "", "phone": "", "whatsapp": "",
           "email": "orders@sylhet.example", "address": "", "territory": "", "credit_limit": "500000",
           "payment_terms_days": 30, "price_list_name": "Distributor", "notes": ""}),
)
