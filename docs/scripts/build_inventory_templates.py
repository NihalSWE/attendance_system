"""Build the Phase 0 inventory Excel templates the client fills in.

    venv/Scripts/python docs/scripts/build_inventory_templates.py

Writes six workbooks to docs/inventory/templates/ (see
docs/inventory/01-PHASE-0-DECISIONS.md, section 7). Each workbook has:

- **How to fill** - every column: required or not, what to write, allowed values;
- **Example** - the same columns with real-looking rows (never imported);
- the sheet(s) to fill - headings only, with dropdowns and number/date checks.

The headings are the contract with the Phase 1 importers: change a heading
here and the importer must accept it too. Run the script again after any
change; the files are rebuilt from scratch.
"""

import datetime
import pathlib
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

OUT_DIR = pathlib.Path(__file__).resolve().parents[1] / "inventory" / "templates"

#: Rows that get dropdowns and checks on a fill sheet.
FILL_ROWS = 2000

# Warm Paper Ink colours (WARM_PAPER_INK_SPEC.md).
HEADER_FILL = PatternFill("solid", fgColor="F0EDE7")
REQUIRED_FILL = PatternFill("solid", fgColor="E6DFD2")
EXAMPLE_FONT = Font(color="6F6B63", italic=True)
LINE = Side(style="thin", color="DED8CC")
HEADER_BORDER = Border(bottom=LINE)

YES_NO = ["Yes", "No"]
ITEM_TYPES = ["Raw material", "Packaging", "Finished goods", "Spare part", "General"]
STORE_TYPES = [
    "Raw material", "Packaging", "Finished goods", "Spare parts", "General",
    "Production floor", "Quarantine", "Rejected",
]
UNITS = ["KG", "G", "L", "ML", "PCS", "BOX", "CARTON", "BAG", "DRUM", "ROLL", "M", "SET", "PAIR"]
CUSTOMER_TYPES = ["Distributor", "Dealer", "Retailer", "Corporate"]


class Col:
    """One column of a fill sheet.

    kind: text | code | number | decimal | date | choice. ``code`` is text
    that Excel must not turn into a number (0012 stays 0012).
    """

    def __init__(self, heading, help, kind="text", required=False, choices=None, width=16):
        self.heading = heading
        self.help = help
        self.kind = kind
        self.required = required
        self.choices = choices
        self.width = width

    @property
    def title(self):
        return f"{self.heading} *" if self.required else self.heading

    @property
    def allowed(self):
        if self.choices:
            return ", ".join(self.choices)
        return {
            "code": "Text; letters, numbers and - only",
            "number": "Whole number, 0 or more",
            "decimal": "Number, 0 or more (decimals allowed)",
            "date": "Date, DD-MM-YYYY",
        }.get(self.kind, "Text")


class Sheet:
    def __init__(self, name, intro, columns, examples):
        self.name = name
        self.intro = intro
        self.columns = columns
        self.examples = examples


# --------------------------------------------------------------------------
# The six templates
# --------------------------------------------------------------------------

STORES = Sheet(
    "Stores",
    "One row per store (warehouse). A store belongs to a branch (the factory, a depot).",
    [
        Col("Branch", "Branch name exactly as in the software (e.g. Factory)", required=True, width=18),
        Col("Store code", "Short unique code, e.g. RMS", kind="code", required=True, width=14),
        Col("Store name", "Name people use", required=True, width=28),
        Col("Store type", "What is kept there", kind="choice", required=True, choices=STORE_TYPES, width=18),
        Col("Notes", "Anything we should know", width=30),
    ],
    [
        ["Factory", "RMS", "Raw material store", "Raw material", ""],
        ["Factory", "PKS", "Packaging store", "Packaging", "Bottles, caps, labels, cartons"],
        ["Factory", "FGW", "Finished goods warehouse", "Finished goods", ""],
        ["Factory", "SPS", "Spare parts store", "Spare parts", ""],
        ["Factory", "GEN", "General store", "General", ""],
        ["Factory", "PRD", "Production floor", "Production floor", ""],
        ["Factory", "QRN", "Quarantine area", "Quarantine", "Waiting for QC"],
        ["Factory", "REJ", "Rejected goods", "Rejected", ""],
    ],
)

BINS = Sheet(
    "Bins",
    "Optional: racks/shelves/bins inside a store, so a scan says exactly where. Leave empty if the store has no bins.",
    [
        Col("Store code", "A store code from the Stores sheet", kind="code", required=True, width=14),
        Col("Bin code", "Unique inside the store, e.g. R1-S2-B3 (rack 1, shelf 2, bin 3)", kind="code", required=True, width=16),
        Col("Description", "Where it is", width=30),
    ],
    [
        ["RMS", "R1-S1", "Rack 1, shelf 1 (sugar)"],
        ["RMS", "R1-S2", "Rack 1, shelf 2 (concentrates)"],
        ["PKS", "R2-S1", "Rack 2, shelf 1 (caps)"],
    ],
)

ITEMS = Sheet(
    "Items",
    "One row per item the factory buys, makes, stores or sells. Quantities are in the base unit.",
    [
        Col("Item code", "Unique code. No codes yet? Use RM-0001, PM-0001, FG-0001, SP-0001, GS-0001", kind="code", required=True, width=14),
        Col("Item name", "Name in English", required=True, width=30),
        Col("Name in Bangla", "Optional", width=24),
        Col("Item type", "Decides the store and the rules", kind="choice", required=True, choices=ITEM_TYPES, width=16),
        Col("Category", "Your own group, e.g. Sweetener, Bottle, Bearing", required=True, width=18),
        Col("Base unit", "The unit stock is counted in", kind="choice", required=True, choices=UNITS, width=11),
        Col("Barcode", "Only if the item already has one printed (EAN-13 on a finished product)", kind="code", width=16),
        Col("Track batch", "Keep batch/lot numbers? Raw, packaging and finished goods: Yes", kind="choice", required=True, choices=YES_NO, width=12),
        Col("Track expiry", "Has an expiry date? Raw materials and finished goods usually Yes", kind="choice", required=True, choices=YES_NO, width=12),
        Col("Shelf life (days)", "Days from manufacture to expiry; fills the expiry date when only manufacture date is known", kind="number", width=14),
        Col("QC required", "New batches wait in quarantine until QC passes", kind="choice", choices=YES_NO, width=12),
        Col("Min stock", "Never go below (base unit)", kind="decimal", width=12),
        Col("Reorder point", "Warn to buy/make when stock falls to this", kind="decimal", width=13),
        Col("Reorder qty", "Usual quantity to order", kind="decimal", width=12),
        Col("Max stock", "Do not stock more than", kind="decimal", width=12),
        Col("Default store code", "Store code where it is normally kept", kind="code", width=14),
        Col("Standard cost (BDT)", "Last known purchase cost per base unit", kind="decimal", width=15),
        Col("Sale price (BDT)", "Finished goods only, per base unit", kind="decimal", width=14),
        Col("VAT %", "Accountant to confirm", kind="decimal", width=8),
        Col("HS code", "Customs code, if imported", kind="code", width=12),
        Col("Notes", "", width=30),
    ],
    [
        ["RM-0001", "Sugar (refined)", "চিনি", "Raw material", "Sweetener", "KG", "", "Yes", "Yes", 365, "Yes", 500, 1000, 5000, 10000, "RMS", 125, "", "", "1701.99", ""],
        ["RM-0002", "Mango pulp", "আমের পাল্প", "Raw material", "Fruit pulp", "KG", "", "Yes", "Yes", 540, "Yes", 200, 500, 2000, 5000, "RMS", 210, "", "", "2008.99", "Imported in drums"],
        ["RM-0003", "Citric acid", "সাইট্রিক এসিড", "Raw material", "Acidulant", "KG", "", "Yes", "Yes", 730, "Yes", 25, 50, 200, 500, "RMS", 260, "", "", "2918.14", ""],
        ["PM-0001", "PET bottle 250 ml", "", "Packaging", "Bottle", "PCS", "", "Yes", "No", "", "Yes", 20000, 50000, 200000, 400000, "PKS", 3.2, "", "", "", ""],
        ["PM-0002", "Cap 28 mm orange", "", "Packaging", "Cap", "PCS", "", "Yes", "No", "", "Yes", 20000, 50000, 200000, 400000, "PKS", 0.9, "", "", "", ""],
        ["PM-0003", "Carton 24 x 250 ml", "", "Packaging", "Carton", "PCS", "", "Yes", "No", "", "No", 1000, 2000, 10000, 20000, "PKS", 28, "", "", "", ""],
        ["FG-0001", "Mango juice 250 ml", "ম্যাঙ্গো জুস ২৫০ মি.লি.", "Finished goods", "Juice", "PCS", "8941234567890", "Yes", "Yes", 270, "No", 2400, 4800, 24000, 72000, "FGW", "", 25, 15, "", ""],
        ["SP-0001", "Bearing 6205", "", "Spare part", "Bearing", "PCS", "", "No", "No", "", "No", 4, 6, 10, 20, "SPS", 450, "", "", "", "Filler motor"],
        ["GS-0001", "A4 paper 80 gsm", "", "General", "Stationery", "BOX", "", "No", "No", "", "No", 2, 4, 10, 20, "GEN", 2400, "", "", "", "Box of 5 reams"],
    ],
)

PACK_UNITS = Sheet(
    "Pack units",
    "Optional: bigger packs an item comes in, so documents can say '10 cartons' and the software counts 240 bottles.",
    [
        Col("Item code", "An item code from the Items sheet", kind="code", required=True, width=14),
        Col("Pack unit", "The pack's name", kind="choice", required=True, choices=UNITS, width=12),
        Col("Base units in one pack", "How many base units in one pack (1 carton = 24 PCS, 1 bag = 50 KG)", kind="decimal", required=True, width=20),
        Col("Pack barcode", "Only if the pack has its own barcode printed (outer carton)", kind="code", width=16),
    ],
    [
        ["FG-0001", "CARTON", 24, "18941234567897"],
        ["RM-0001", "BAG", 50, ""],
        ["RM-0002", "DRUM", 220, ""],
    ],
)

SUPPLIERS = Sheet(
    "Suppliers",
    "One row per supplier.",
    [
        Col("Supplier code", "Unique code, e.g. SUP-001", kind="code", required=True, width=14),
        Col("Supplier name", "Company name", required=True, width=30),
        Col("Contact person", "", width=20),
        Col("Phone", "", kind="code", width=16),
        Col("Email", "Purchase orders can be emailed here", width=26),
        Col("Address", "", width=34),
        Col("BIN", "VAT registration number", kind="code", width=14),
        Col("TIN", "Tax number", kind="code", width=14),
        Col("Payment terms (days)", "0 = cash on delivery", kind="number", width=12),
        Col("Items supplied", "Item codes, separated by commas (optional)", width=24),
        Col("Notes", "", width=24),
    ],
    [
        ["SUP-001", "Deshbandhu Sugar Traders", "Mr. Karim", "01711000000", "sales@example.com", "Chattogram", "000000000-0101", "", 30, "RM-0001", ""],
        ["SUP-002", "Example PET Packaging Ltd.", "Ms. Rahman", "01811000000", "orders@example.com", "Gazipur", "", "", 45, "PM-0001, PM-0002", "Preforms too"],
    ],
)

CUSTOMERS = Sheet(
    "Customers",
    "One row per customer. Email and WhatsApp numbers let the AI assistant recognise who sent an order.",
    [
        Col("Customer code", "Unique code, e.g. CUS-001", kind="code", required=True, width=14),
        Col("Customer name", "", required=True, width=30),
        Col("Customer type", "", kind="choice", required=True, choices=CUSTOMER_TYPES, width=14),
        Col("Contact person", "", width=20),
        Col("Phone", "", kind="code", width=16),
        Col("WhatsApp number", "With country code, e.g. 8801711000000", kind="code", width=17),
        Col("Email", "", width=26),
        Col("Address", "", width=34),
        Col("Territory", "Area / route, e.g. Sylhet", width=14),
        Col("Credit limit (BDT)", "0 = cash only", kind="decimal", width=15),
        Col("Payment terms (days)", "", kind="number", width=12),
        Col("Price list", "Name of the price list (Distributor, Dealer…)", width=16),
        Col("Notes", "", width=24),
    ],
    [
        ["CUS-001", "Sylhet Distribution House", "Distributor", "Mr. Ahmed", "01911000000", "8801911000000", "order@example.com", "Zindabazar, Sylhet", "Sylhet", 500000, 30, "Distributor", ""],
        ["CUS-002", "Sreemangal Super Shop", "Retailer", "Mr. Das", "01611000000", "", "", "Station Road, Sreemangal", "Moulvibazar", 0, 0, "Retailer", ""],
    ],
)

BOMS = Sheet(
    "BOMs",
    "One row per material of a product's recipe. Repeat the product code, batch size and unit on every row of that product.",
    [
        Col("Product code", "Finished goods item code", kind="code", required=True, width=14),
        Col("Batch size", "How much one batch makes", kind="decimal", required=True, width=11),
        Col("Batch unit", "Unit of the batch size", kind="choice", required=True, choices=UNITS, width=10),
        Col("Material code", "Raw material or packaging item code", kind="code", required=True, width=14),
        Col("Quantity per batch", "Material needed for one batch", kind="decimal", required=True, width=16),
        Col("Unit", "Unit of that quantity", kind="choice", required=True, choices=UNITS, width=9),
        Col("Scrap %", "Normal wastage of this material", kind="decimal", width=9),
        Col("Packaging", "Is it packaging (added at filling)?", kind="choice", choices=YES_NO, width=11),
        Col("Notes", "", width=24),
    ],
    [
        ["FG-0001", 1000, "PCS", "RM-0002", 45, "KG", 2, "No", "Example figures only"],
        ["FG-0001", 1000, "PCS", "RM-0001", 22, "KG", 1, "No", ""],
        ["FG-0001", 1000, "PCS", "RM-0003", 0.5, "KG", 0, "No", ""],
        ["FG-0001", 1000, "PCS", "PM-0001", 1000, "PCS", 1.5, "Yes", ""],
        ["FG-0001", 1000, "PCS", "PM-0002", 1000, "PCS", 1, "Yes", ""],
        ["FG-0001", 1000, "PCS", "PM-0003", 42, "PCS", 0.5, "Yes", "24 bottles per carton"],
    ],
)

OPENING = Sheet(
    "Opening stock",
    "What was counted on the opening day: one row per store + bin + item + batch. Items without batches leave Batch no. empty.",
    [
        Col("Store code", "", kind="code", required=True, width=12),
        Col("Bin code", "Optional", kind="code", width=12),
        Col("Item code", "", kind="code", required=True, width=14),
        Col("Batch no.", "Required when the item tracks batches", kind="code", width=16),
        Col("Supplier batch no.", "Optional", kind="code", width=16),
        Col("Manufacture date", "", kind="date", width=14),
        Col("Expiry date", "Required when the item tracks expiry", kind="date", width=14),
        Col("Quantity", "Counted quantity", kind="decimal", required=True, width=12),
        Col("Unit", "Base unit or a pack unit of the item", kind="choice", required=True, choices=UNITS, width=9),
        Col("Unit cost (BDT)", "Cost per unit written in Unit", kind="decimal", required=True, width=14),
        Col("Notes", "", width=24),
    ],
    [
        ["RMS", "R1-S1", "RM-0001", "251001-01", "SB-7781", datetime.date(2026, 9, 20), datetime.date(2027, 9, 19), 1250, "KG", 125, ""],
        ["RMS", "R1-S2", "RM-0002", "251003-01", "MP-2026-44", datetime.date(2026, 8, 1), datetime.date(2028, 1, 22), 8, "DRUM", 46200, "8 drums x 220 kg"],
        ["PKS", "R2-S1", "PM-0002", "251005-01", "", "", "", 85000, "PCS", Decimal("0.90"), ""],
        ["FGW", "", "FG-0001", "FG0001-251010-L1", "", datetime.date(2026, 10, 10), datetime.date(2027, 7, 7), 300, "CARTON", 480, ""],
        ["SPS", "", "SP-0001", "", "", "", "", 7, "PCS", 450, ""],
    ],
)

TEMPLATES = [
    ("01-stores.xlsx", "Stores and bins", [STORES, BINS]),
    ("02-items.xlsx", "Items and pack units", [ITEMS, PACK_UNITS]),
    ("03-suppliers.xlsx", "Suppliers", [SUPPLIERS]),
    ("04-customers.xlsx", "Customers", [CUSTOMERS]),
    ("05-boms.xlsx", "Bills of materials (recipes)", [BOMS]),
    ("06-opening-stock.xlsx", "Opening stock", [OPENING]),
]

GENERAL_NOTES = [
    "Fill the sheet(s) named in the table below. The Example sheet shows the idea and is never imported.",
    "Columns marked * are required. Codes must be unique and must not change later (labels and documents use them).",
    "Do not rename, move or delete the heading row. Add as many rows as you need.",
    "Dates as DD-MM-YYYY. Money in Taka (BDT), without the ৳ sign or commas.",
]


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------

def _write_headings(ws, sheet):
    for index, col in enumerate(sheet.columns, start=1):
        cell = ws.cell(row=1, column=index, value=col.title)
        cell.font = Font(bold=True)
        cell.fill = REQUIRED_FILL if col.required else HEADER_FILL
        cell.border = HEADER_BORDER
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(index)].width = col.width
    ws.row_dimensions[1].height = 32
    ws.freeze_panes = "A2"


def _format_cells(ws, sheet, last_row):
    for index, col in enumerate(sheet.columns, start=1):
        letter = get_column_letter(index)
        for row in range(2, last_row + 1):
            cell = ws[f"{letter}{row}"]
            if col.kind == "code":
                cell.number_format = "@"
            elif col.kind == "date":
                cell.number_format = "DD-MM-YYYY"
            elif col.kind == "decimal":
                cell.number_format = "#,##0.####"


def _add_checks(ws, sheet):
    for index, col in enumerate(sheet.columns, start=1):
        letter = get_column_letter(index)
        cells = f"{letter}2:{letter}{FILL_ROWS + 1}"
        if col.kind == "choice":
            check = DataValidation(type="list", formula1='"' + ",".join(col.choices) + '"', allow_blank=True)
            check.error = "Pick one of: " + ", ".join(col.choices)
        elif col.kind == "number":
            check = DataValidation(type="whole", operator="greaterThanOrEqual", formula1="0", allow_blank=True)
            check.error = "A whole number, 0 or more."
        elif col.kind == "decimal":
            check = DataValidation(type="decimal", operator="greaterThanOrEqual", formula1="0", allow_blank=True)
            check.error = "A number, 0 or more."
        elif col.kind == "date":
            check = DataValidation(type="date", operator="greaterThan", formula1="1", allow_blank=True)
            check.error = "A date, DD-MM-YYYY."
        else:
            continue
        check.errorTitle = col.heading
        check.showErrorMessage = True
        check.prompt = col.help
        check.showInputMessage = bool(col.help)
        ws.add_data_validation(check)
        check.add(cells)


def _fill_sheet(book, sheet):
    ws = book.create_sheet(sheet.name)
    _write_headings(ws, sheet)
    _format_cells(ws, sheet, FILL_ROWS + 1)
    _add_checks(ws, sheet)
    return ws


def _example_sheet(book, sheets):
    ws = book.create_sheet("Example")
    row = 1
    for sheet in sheets:
        if len(sheets) > 1:
            ws.cell(row=row, column=1, value=f"{sheet.name} (example)").font = Font(bold=True, size=12)
            row += 1
        for index, col in enumerate(sheet.columns, start=1):
            cell = ws.cell(row=row, column=index, value=col.title)
            cell.font = Font(bold=True)
            cell.fill = REQUIRED_FILL if col.required else HEADER_FILL
            cell.alignment = Alignment(wrap_text=True)
            width = ws.column_dimensions[get_column_letter(index)].width or 0
            ws.column_dimensions[get_column_letter(index)].width = max(width, col.width)
        for values in sheet.examples:
            row += 1
            for index, (col, value) in enumerate(zip(sheet.columns, values), start=1):
                cell = ws.cell(row=row, column=index, value=value if value != "" else None)
                cell.font = EXAMPLE_FONT
                if col.kind == "code":
                    cell.number_format = "@"
                elif col.kind == "date":
                    cell.number_format = "DD-MM-YYYY"
        row += 3


def _how_to_sheet(book, title, sheets):
    ws = book.active
    ws.title = "How to fill"
    ws.column_dimensions["A"].width = 24
    ws.column_dimensions["B"].width = 11
    ws.column_dimensions["C"].width = 70
    ws.column_dimensions["D"].width = 48
    ws["A1"] = f"Healthy Choice inventory — {title}"
    ws["A1"].font = Font(bold=True, size=14)
    row = 3
    for note in GENERAL_NOTES:
        ws.cell(row=row, column=1, value="• " + note)
        row += 1
    for sheet in sheets:
        row += 1
        ws.cell(row=row, column=1, value=f"Sheet “{sheet.name}”").font = Font(bold=True, size=12)
        row += 1
        ws.cell(row=row, column=1, value=sheet.intro).alignment = Alignment(wrap_text=False)
        row += 1
        for index, heading in enumerate(["Column", "Required", "What to write", "Allowed values"], start=1):
            cell = ws.cell(row=row, column=index, value=heading)
            cell.font = Font(bold=True)
            cell.fill = HEADER_FILL
            cell.border = HEADER_BORDER
        for col in sheet.columns:
            row += 1
            ws.cell(row=row, column=1, value=col.heading)
            ws.cell(row=row, column=2, value="Yes" if col.required else "")
            ws.cell(row=row, column=3, value=col.help).alignment = Alignment(wrap_text=True, vertical="top")
            ws.cell(row=row, column=4, value=col.allowed).alignment = Alignment(wrap_text=True, vertical="top")
        row += 1


def build(filename, title, sheets):
    book = Workbook()
    _how_to_sheet(book, title, sheets)
    _example_sheet(book, sheets)
    for sheet in sheets:
        _fill_sheet(book, sheet)
    book.properties.title = f"Inventory template — {title}"
    path = OUT_DIR / filename
    book.save(path)
    return path


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for filename, title, sheets in TEMPLATES:
        for sheet in sheets:
            for values in sheet.examples:
                assert len(values) == len(sheet.columns), (sheet.name, values)
        print("wrote", build(filename, title, sheets).relative_to(OUT_DIR.parents[2]))


if __name__ == "__main__":
    main()
