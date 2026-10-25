"""What each field does in the software, checked in the code (2026-10-19).

"Later part" = a page built but not shown in the menu yet (purchase, sales,
reports, alerts).
"""

EFFECTS = {
    "settings": {
        "Average cost": "Decides how every stock value is worked out: one moving average cost "
                        "per item for the whole company, or a separate one in each store. "
                        "<b>Fixed once stock has moved.</b>",
        "Warn before expiry (days)": "Batches that expire within this many days are listed as "
                                     "\"expiring\" in the morning email (and the Batch and "
                                     "expiry report, a later part).",
        "Slow-moving days": "The \"Slow and non-moving stock\" report (a later part) lists items "
                            "that have not moved for this many days.",
        "Alert emails": "These people get the morning stock email: items to reorder, expired "
                        "and expiring batches. Starts once the daily email is switched on on "
                        "the server.",
        "Small label": "The size used when small labels are printed (items, batches).",
        "Large label": "The size used when carton (package) labels are printed.",
        "GS1 labels": "Adds a GS1-128 barcode (item, batch, expiry, quantity) to large labels, "
                      "for items with a 13 or 14 digit barcode.",
    },
    "units": {
        "Code": "Shown next to every quantity in this unit, e.g. \"500 KG\".",
        "Name": "Shown in lists, so people know what the code means.",
        "Kind": "Describes the unit. It does not change any calculation.",
        "Decimals": "<b>Checked on every quantity</b> in this unit: with 0, \"2.5 PCS\" is "
                    "refused; with 3, \"1.2505 KG\" is refused.",
    },
    "categories": {
        "Code": "Identifies the folder in lists and Excel imports.",
        "Name": "Shown in the Items list and reports.",
        "Item type": "An item can only be put in a category of <b>its own</b> item type.",
        "Parent category": "Builds folders inside folders. Only for grouping; the parent "
                           "must have the same item type.",
    },
    "stores": {
        "Branch": "Stock in this store belongs to that branch. People see and work with the "
                  "store only if they have permissions in that branch.",
        "Code": "Identifies the store on lines, labels, reports and Excel imports.",
        "Name": "Shown in lists and on Stock on hand.",
        "Store type": "<b>Quarantine, Rejected and In transit</b> are \"not for use\": stock "
                      "there can never be issued, used in production or sold. The other types "
                      "are normal stores.",
        "Notes": "For reference only.",
    },
    "bins": {
        "Store": "The shelf belongs to this store. On a line, the bin must be in that line's "
                 "store.",
        "Bin code": "Shown on Stock on hand, so people know exactly which shelf to go to.",
        "Description": "For reference only.",
    },
    "items": {
        "Code": "Identifies the item everywhere: lines, labels, reports, Excel imports. "
                "Search and scan find it by code.",
        "Name": "Shown everywhere. Search finds the item by name too.",
        "Name in Bangla": "Shown with the item; search also finds it by the Bangla name.",
        "Item type": "Must match the category's type. Stock on hand can be filtered by it.",
        "Category": "Groups the item in lists and reports. Must be of the same item type.",
        "Base unit": "<b>All stock of this item is kept in this unit</b>; every report shows "
                     "it in this unit; its Decimals rule applies. Fixed once stock has moved.",
        "Barcode": "Scanning the product's printed barcode finds this item.",
        "Keeps batches": "<b>Every stock entry needs a batch number.</b> Stock on hand shows "
                         "each batch separately. Later, issues take the batch that expires "
                         "first.",
        "Has an expiry date": "<b>Every batch needs an expiry date.</b> Expired batches can "
                              "never be issued, used or sold, and appear in the warnings. "
                              "Needs \"Keeps batches\" ticked too.",
        "Shelf life (days)": "When only the manufacture date is given, the expiry date is "
                             "worked out by itself: made 1 Oct + 365 days = expires 1 Oct.",
        "QC before use": "Goods of this item that arrive later (from a purchase or from "
                         "production) are held until a quality check passes them. Opening "
                         "stock counts as already checked.",
        "Min stock": "If what is available falls <b>below</b> this, the item shows as "
                     "\"Below minimum\" in the morning email and the Reorder report.",
        "Reorder point": "If what is available falls <b>to or below</b> this, the item is "
                         "listed \"to reorder\" in the morning email and the Reorder report.",
        "Reorder qty": "The quantity the Reorder report suggests buying (when no Max stock is "
                       "set).",
        "Max stock": "If set, the suggested order is Max minus what is available minus what is "
                     "already on order. Min and Reorder point cannot be above it.",
        "Usual store": "On the count day, the item is listed on this store's count sheet. "
                       "Shown on the item's page.",
        "Standard cost": "Pre-filled as the unit cost on the count sheets, and as the price "
                         "on new purchase orders (a later part). Does not change stock value.",
        "Sale price": "The selling price used on a sales order when the customer's price list "
                      "has no price for it (a later part).",
        "VAT rate": "Filled in on purchase and sales order lines, and printed on invoices "
                    "(later parts).",
        "HS code": "For reference only (customs).",
        "Notes": "For reference only.",
    },
    "packs": {
        "Pack unit": "Lets people enter quantities in this unit on any line.",
        "Factor": "<b>The conversion</b>: 10 BAG &times; 50 = 500 KG is what goes into stock. "
                  "A cost per bag becomes a cost per kg the same way (6,000 &divide; 50 = 120).",
        "Barcode": "Scanning the carton's own barcode finds this item and this pack.",
    },
    "suppliers": {
        "Code": "Identifies the supplier on purchase documents and Excel imports.",
        "Name": "Printed on purchase orders (a later part).",
        "Contact person": "For reference; shown on the supplier's row.",
        "Phone": "For reference; shown on the supplier's row.",
        "Email": "Where \"Email to supplier\" sends the purchase order (a later part).",
        "Address": "For reference.",
        "BIN": "Printed on the purchase order PDF.",
        "TIN": "For reference only.",
        "Payment terms (days)": "Fills in the order's terms: \"Payment 30 days after delivery\" "
                                "(a later part).",
        "Items supplied": "For reference only: a note of what they sell.",
        "Notes": "For reference only.",
    },
    "customers": {
        "Code": "Identifies the customer on sales documents and Excel imports.",
        "Name": "Printed on challans and invoices (a later part).",
        "Customer type": "Groups customers in lists and reports.",
        "Contact person": "For reference.",
        "Phone": "For reference; shown on the row.",
        "WhatsApp": "Lets the AI order assistant recognise orders from this number (a later "
                    "part).",
        "Email": "Lets the AI order assistant recognise orders from this address (a later "
                 "part).",
        "Address": "For reference.",
        "Territory": "Groups customers by area; shown on the row.",
        "Credit limit": "When a sales order is confirmed (a later part), the software adds up "
                        "what is unpaid, on order and this order; above the limit it asks why. "
                        "<b>0 = no credit check.</b>",
        "Payment terms (days)": "Printed on the invoice (\"Payment within 15 days\") and sets "
                                "its due date (a later part).",
        "Price list name": "Sales order prices come from the price list with this name; if it "
                           "has no price, the item's Sale price is used (a later part).",
        "Notes": "For reference only.",
    },
    "opening_header": {
        "Opening day": "Every stock movement of this opening stock is dated this day.",
        "Note": "For reference only.",
    },
    "opening_line": {
        "Store": "The stock goes into this store.",
        "Bin": "The stock goes onto this shelf; Stock on hand shows it there.",
        "Item": "The item whose stock this is.",
        "Batch no.": "Creates the batch with this number. Stock on hand and labels show it.",
        "Supplier batch no.": "Kept on the batch, to trace it back to the supplier.",
        "Manufacture date": "Kept on the batch; with the item's shelf life it sets the expiry.",
        "Expiry date": "Kept on the batch: drives the expiry warnings, and blocks the batch "
                       "from use once expired.",
        "Quantity": "With the unit, becomes the stock in the base unit (shown as \"In base "
                    "unit\").",
        "Unit": "Base unit or a pack unit; a pack is converted with its factor.",
        "Unit cost": "Sets the item's <b>first average cost</b> (per base unit = unit cost "
                     "&divide; pack factor) and the value of this stock.",
        "Note": "For reference only.",
    },

    # 2026-10-25: Labels, Scan and BOMs (checked in inventory/labels.py,
    # inventory/lookup.py and production/boms.py).
    "print": {
        "Copies of each": "Every label in the PDF is printed this many times (1 to 20). Each "
                          "copy carries the same code, so any copy scans to the same record. At "
                          "most 500 labels in one PDF.",
        "Why print again": "Asked only when a label was printed before. Saved with the reprint "
                           "in the audit trail; the label's <b>Printed</b> count goes up.",
    },
    "void": {
        "Why it is void": "From now on a scan of this label is <b>refused with this reason</b>, "
                          "and it can no longer be printed. It cannot be made active again: "
                          "print a new label instead.",
    },
    "package_labels": {
        "Batch": "Printed on every label. Scanning one then fills the item, the batch and its "
                 "expiry at once. Required for an item that keeps batches.",
        "Packages": "How many labels to make: one per bag, drum or carton (1 to 500). Each gets "
                    "its own code (P...), so each package can be told apart.",
        "In each package": "The quantity printed on each label. A scan fills this quantity in, "
                           "so it must be what one package really holds.",
        "Unit": "The unit of that quantity: the item's base unit or one of its pack units. "
                "Shown on the label and converted to the base unit when scanned.",
        "Store": "Recorded on the label as where the packages were put. Only stores of your "
                 "branches are offered.",
        "Bin": "The shelf inside that store, recorded on the label. Choosing a bin alone also "
               "sets its store.",
    },
    "bom": {
        "Finished product": "Only items of type <b>Finished goods</b> are offered. Each product "
                            "has numbered versions of its recipe (v1, v2 ...); only one is "
                            "active at a time.",
        "Batch size": "How much one batch of this recipe makes. Every material quantity is for "
                      "this amount; making more or less scales them up or down.",
        "Batch unit": "The unit of the batch size: the product's base unit or one of its pack "
                      "units. Stored in the base unit as well.",
        "Effective from": "Kept with the version for your records. It does not switch the "
                          "recipe on by itself: <b>Activate</b> does.",
        "Note": "Shown on the recipe's page under its name.",
    },
    "bom_line": {
        "Material": "Any active item except the product itself: raw material, packaging, even "
                    "another finished good. Each material can be on a recipe once.",
        "Quantity per batch": "How much of it one batch uses. Planning multiplies it by "
                              "(quantity to make &divide; batch size).",
        "Unit": "The material's base unit or one of its pack units (e.g. BAG); stored in the "
                "base unit too.",
        "Scrap %": "Normal wastage, added on top when production is planned: 2% on 120 kg plans "
                   "122.4 kg.",
        "Packaging": "Listed after the ingredients and marked <b>Packaging</b> on the recipe "
                     "and in production. It changes no quantity.",
        "Note": "Shown on the material's line.",
    },
}
