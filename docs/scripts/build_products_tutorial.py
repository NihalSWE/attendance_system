"""Products and stock - the step-by-step tutorial of the redesigned screens
(2026-10-28, docs/inventory/PRODUCTION_PLAN.md §15 phase 9.3).

    venv/Scripts/python docs/scripts/build_products_tutorial.py

writes docs/inventory/PRODUCTS-AND-STOCK-TUTORIAL.pdf. It follows the
Inventory menu (2026-10-31: Setup, Materials, Products, Stock, Production,
Sales, ...) - recipe and cost, Stock in, the sale price, Make, Sell, Warranty -
with every box of each form: what to write and what the software does with it.
Rebuild it when those screens or their rules change.
The look is the one of build_inventory_tutorial.py (Part 1, the setting-up lists).
"""

import math
import pathlib
import sys

from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    CondPageBreak, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

OUT = sys.argv[1] if len(sys.argv) > 1 else str(
    pathlib.Path(__file__).resolve().parents[1] / "inventory" / "PRODUCTS-AND-STOCK-TUTORIAL.pdf")

INK = colors.HexColor("#17171a")
MUTED = colors.HexColor("#6f6b63")
ACCENT = colors.HexColor("#1d5fb4")          # the action blue of the new pages
ACCENT_SOFT = colors.HexColor("#e8f0fa")
PAPER = colors.HexColor("#f5f1ea")
LINE = colors.HexColor("#ded8cc")
OK_SOFT, OK = colors.HexColor("#e9f3e9"), colors.HexColor("#2f6b34")
WARN_SOFT, WARN = colors.HexColor("#f8f0dd"), colors.HexColor("#7d5510")
BAD_SOFT, BAD = colors.HexColor("#fbeae7"), colors.HexColor("#a32a1e")

S = {
    "title": ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=30, leading=36, textColor=INK,
                            spaceAfter=10),
    "subtitle": ParagraphStyle("subtitle", fontName="Helvetica", fontSize=14, leading=20, textColor=MUTED,
                               spaceAfter=6),
    "h1": ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=20, leading=25, textColor=INK,
                         spaceBefore=4, spaceAfter=8),
    "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=13.5, leading=18, textColor=ACCENT,
                         spaceBefore=10, spaceAfter=5),
    "body": ParagraphStyle("body", fontName="Helvetica", fontSize=10, leading=14.5, textColor=INK,
                           spaceAfter=5),
    "small": ParagraphStyle("small", fontName="Helvetica", fontSize=8.6, leading=11.5, textColor=MUTED),
    "cell": ParagraphStyle("cell", fontName="Helvetica", fontSize=8.8, leading=11.6, textColor=INK),
    "cellb": ParagraphStyle("cellb", fontName="Helvetica-Bold", fontSize=8.8, leading=11.6, textColor=INK),
    "head": ParagraphStyle("head", fontName="Helvetica-Bold", fontSize=8.4, leading=11, textColor=colors.white),
    "step": ParagraphStyle("step", fontName="Helvetica", fontSize=10, leading=14.5, textColor=INK,
                           leftIndent=16, firstLineIndent=-16, spaceAfter=3),
    "bullet": ParagraphStyle("bullet", fontName="Helvetica", fontSize=10, leading=14.2, textColor=INK,
                             leftIndent=12, firstLineIndent=-9, spaceAfter=2),
}


def P(text, style="body"):
    return Paragraph(text, S[style])


def bullets(items):
    return [P(f"&bull;&nbsp;&nbsp;{item}", "bullet") for item in items]


def steps(items):
    return [P(f"<b>{n}.</b>&nbsp;&nbsp;{item}", "step") for n, item in enumerate(items, 1)]


def box(title, lines, tone="info"):
    bg, fg = {"info": (ACCENT_SOFT, ACCENT), "ok": (OK_SOFT, OK), "warn": (WARN_SOFT, WARN),
              "bad": (BAD_SOFT, BAD)}[tone]
    content = [Paragraph(f"<font color='{fg.hexval()}'><b>{title}</b></font>", S["cell"])]
    content += [Paragraph(line, S["cell"]) for line in lines]
    table = Table([[content]], colWidths=[170 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg), ("LINEBEFORE", (0, 0), (0, -1), 3, fg),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    return KeepTogether([Spacer(1, 3), table, Spacer(1, 6)])


def grid(head, rows, widths):
    data = [[Paragraph(h, S["head"]) for h in head]]
    data += [[Paragraph(str(c), S["cellb"] if i == 0 else S["cell"]) for i, c in enumerate(row)] for row in rows]
    table = Table(data, colWidths=[w * mm for w in widths], repeatRows=1)
    style = [("BACKGROUND", (0, 0), (-1, 0), ACCENT), ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE), ("BOX", (0, 0), (-1, -1), 0.6, LINE),
             ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
             ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]
    style += [("BACKGROUND", (0, r), (-1, r), PAPER) for r in range(2, len(data), 2)]
    table.setStyle(TableStyle(style))
    return table


MUST = {"Yes": ("Required", BAD), "No": ("Optional", MUTED), "Some": ("Sometimes", WARN)}


def fields(rows):
    """Box (+ required) | What to write (+ example) | What the software does with it."""
    data = [[Paragraph(h, S["head"]) for h in ("Box on screen", "What to write", "What it does")]]
    for name, must, write, example, does in rows:
        label, tone = MUST[must]
        data.append([
            Paragraph(f"<b>{name}</b><br/><font size='7.4' color='{tone.hexval()}'>{label}</font>", S["cell"]),
            Paragraph(write + (f"<br/><font color='{MUTED.hexval()}'>e.g. {example}</font>" if example else ""),
                      S["cell"]),
            Paragraph(does, S["cell"])])
    table = Table(data, colWidths=[33 * mm, 64 * mm, 73 * mm], repeatRows=1)
    style = [("BACKGROUND", (0, 0), (-1, 0), ACCENT), ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE), ("BOX", (0, 0), (-1, -1), 0.6, LINE),
             ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
             ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
             ("BACKGROUND", (2, 1), (2, -1), ACCENT_SOFT)]
    style += [("BACKGROUND", (0, r), (1, r), PAPER) for r in range(2, len(data), 2)]
    table.setStyle(TableStyle(style))
    return table


def where(menu, note=""):
    t = Table([[Paragraph(f"<b>Where:</b>&nbsp; {menu}", S["cell"]), Paragraph(note, S["small"])]],
              colWidths=[100 * mm, 70 * mm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), PAPER), ("BOX", (0, 0), (-1, -1), 0.5, LINE),
                           ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 8),
                           ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    return KeepTogether([t, Spacer(1, 6)])


def section(number, title, menu, what, before="", note=""):
    out = [CondPageBreak(90 * mm), Spacer(1, 6 * mm), P(f"Step {number} &nbsp;&middot;&nbsp; {title}", "h1"),
           where(menu, note), P(f"<b>What it is:</b> {what}")]
    if before:
        out.append(P(f"<b>You need first:</b> {before}"))
    return out


def flow_diagram():
    d = Drawing(170 * mm, 44 * mm)
    labels = [("Setup", "units, stores"), ("1 Materials", "what you use"), ("2 Products", "what you sell"),
              ("3 Recipe", "one batch, its cost"), ("4 Stock in", "buy price in"), ("5 Sale price", "cost + margin"),
              ("6 Make", "plan, start, finish"), ("7 Sell", "deliver, invoice")]
    w, gap, rows = 38 * mm, 6 * mm, 2
    for i, (title, sub) in enumerate(labels):
        row, col = divmod(i, 4)
        x, y = col * (w + gap), (rows - 1 - row) * 22 * mm + 2 * mm
        d.add(Rect(x, y, w, 15 * mm, rx=6, ry=6, fillColor=ACCENT_SOFT, strokeColor=ACCENT, strokeWidth=1))
        d.add(String(x + w / 2, y + 8.5 * mm, title, fontName="Helvetica-Bold", fontSize=9, fillColor=INK,
                     textAnchor="middle"))
        d.add(String(x + w / 2, y + 3.6 * mm, sub, fontName="Helvetica", fontSize=7, fillColor=MUTED,
                     textAnchor="middle"))
        if col < 3:
            x1, x2, ym = x + w, x + w + gap, y + 7.5 * mm
            d.add(Line(x1, ym, x2, ym, strokeColor=MUTED, strokeWidth=1))
            a = 2.2 * mm
            d.add(Polygon([x2, ym, x2 - a * math.cos(0.4), ym - a * math.sin(0.4),
                           x2 - a * math.cos(-0.4), ym - a * math.sin(-0.4)], fillColor=MUTED, strokeColor=MUTED))
    return d


def on_page(canvas, doc):
    canvas.saveState()
    if doc.page > 1:
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(20 * mm, 287 * mm, "Products and stock  |  Step by step")
        canvas.drawRightString(190 * mm, 287 * mm, "Workforce (workforce.iglweb.com)")
        canvas.setStrokeColor(LINE)
        canvas.line(20 * mm, 285 * mm, 190 * mm, 285 * mm)
        canvas.drawCentredString(105 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


story = []

# ------------------------------------------------------------------ cover
story += [
    Spacer(1, 34 * mm), P("Products and stock", "title"),
    P("From the first material to the sale, step by step: every screen, every box, and what the software does "
      "with it.", "subtitle"),
    Spacer(1, 6 * mm),
    P("For owners, store keepers, production and sales staff. Read it once from top to bottom; after that, open the "
      "step you need. Every page of the software ends with a <b>Next step</b> box that says what to do next."),
    Spacer(1, 6 * mm), flow_diagram(), Spacer(1, 6 * mm),
    box("How every screen works", [
        "&bull; <b>The menu</b>: Inventory, then one dropdown per kind of work: Setup (Start here is its first "
        "link), Materials, Products, Stock, Production, Sales, Purchase, Reports, Tools.",
        "&bull; <b>Saving</b>: the answer shows just above the Save button - green when saved, red when not, with "
        "each wrong box outlined red and its reason under it. What you typed stays.",
        "&bull; <b>Dates</b>: every date box opens the same calendar. A date that cannot be right is refused.",
        "&bull; <b>Lists</b>: SL numbers, Show N, search, pages, and an <b>Edit</b> button on each row.",
        "&bull; <b>Money</b> (costs, values, margins) shows only to people given \"See stock costs and values\".",
    ]),
    PageBreak(),
]

# ------------------------------------------------------------------ words
story += [
    P("Words used on the screens", "h1"),
    grid(("Word", "What it means"), [
        ("Store", "A warehouse or room: raw store, packing store, main store, depot."),
        ("Material", "Something you use up making products: flour, sugar, wrappers. Bought, made here, or both."),
        ("Product", "Something you sell. Bought ready, made here, or both."),
        ("Base unit", "What stock is counted in, set once per item: packet, kg, piece."),
        ("Bigger unit", "1 bag = 50 kg; 1 carton = 24 packets. A pack can have its own price."),
        ("Batch (one mix)", "The recipe is for one batch: \"1 batch = 240 packets\". 1,200 packets = 5 batches."),
        ("Batch number", "Stock that came in together, with its own dates and cost. The one that expires "
                         "first always goes out first."),
        ("Cost of one", "What one unit really costs: the average buy price, or for a made item, its recipe "
                        "and costs."),
        ("Margin", "Sale price - cost of one. Below cost = a loss on each one sold."),
        ("Guarantee / Warranty", "Guarantee: the first months, a faulty unit is replaced. Warranty: after that, "
                                 "to its end, it is repaired."),
    ], (36, 134)),
    Spacer(1, 6 * mm),
    box("The one rule about prices", [
        "You do not type a buy price or a sale price when you add a material or a product. "
        "The <b>buy price</b> comes from <b>Stock in</b> (what you really paid). The <b>sale price</b> is set once "
        "the cost is known: on a Stock in line, or on the item's page, where the cost and the margin are shown. "
        "A price below cost is refused unless you switch on <b>Sell below cost</b> and say why.",
    ], "ok"),
]

# ------------------------------------------------------------------ the whole workflow
def walk(rows):
    """Step | Where you click | What you type or choose | What you see."""
    return grid(("#", "Where you click", "What you type or choose", "What you see"), rows, (10, 46, 62, 52))


story += [
    PageBreak(), P("The whole workflow in one example", "h1"),
    P("A biscuit factory. <b>One batch</b> makes <b>240 packets</b> and uses flour 18 kg, sugar 5 kg, 240 wrappers and "
      "10 cartons. Part A is done <b>once</b>; Part B <b>every time</b> you buy, make and sell."),

    P("Part A &middot; Once: set up", "h2"),
    walk([
        ("A1", "Inventory &rarr; <b>Setup</b> &rarr; Units, Stores",
         "Units: Add standard units. Stores: Raw store, Packing store, Main store, each in its branch.",
         "The units (KG, PCS, BAG, CARTON ...) and the rooms stock is kept in."),
        ("A2", "Inventory &rarr; <b>Materials</b> &rarr; Materials &rarr; <b>Add material</b>",
         "How you get it: Bought. Type: Raw material. Name: Flour. Base unit KG; Purchase unit BAG, Contains 50. "
         "Minimum stock 200. Batch tracking + Expiry tracking, shelf life 180 days. <b>No price.</b>",
         "\"RM-0001 Flour added\". Next step: <b>Put it in stock</b>."),
        ("A3", "Inventory &rarr; <b>Products</b> &rarr; Products &rarr; <b>Add product</b>",
         "How you get it: Made here. Name: Biscuit 100 g. Base unit PCS; Selling pack CARTON, Contains 24. "
         "Minimum stock 5,000. Shelf life 180 days. New stock goes to: Main store. <b>No price.</b>",
         "The product page opens and asks for its recipe."),
        ("A4", "Product page &rarr; card <b>Recipe</b>",
         "One batch makes 240. Add material: Flour 18, Sugar 5, Wrapper 240, Carton 10.",
         "\"Recipe: one batch makes 240 PCS\", each line with its cost and \"enough for N batches\"."),
        ("A5", "Inventory &rarr; <b>Setup</b> &rarr; Cost rates (optional)",
         "Line worker 75 per hour; Oven 300 per hour. On the product: Add cost (people, machine, gas).",
         "The full cost of one batch and of one packet."),
    ]),

    P("Part B &middot; Every time: buy, price, make, sell", "h2"),
    walk([
        ("B1", "Inventory &rarr; <b>Stock</b> &rarr; Stock in &rarr; <b>Stock in</b>",
         "A purchase; supplier; Into store: Raw store; date. Line: Flour 10 BAG, cost 2,600 per BAG, "
         "manufacture date and/or expiry. Sale price: empty (flour is not sold).",
         "\"GRN-...: received into Raw store\". The buy price is now known; Flour shows OK."),
        ("B2", "Product page &rarr; card <b>Sale price</b>",
         "Cost of one PCS is shown (e.g. 14.20). Margin you want: 30 % &rarr; the price fills (20.29). "
         "Change it to 20 and <b>Save price</b>.",
         "\"Now 20.00 per PCS\" with its margin. The carton's own price: Units and prices &rarr; Add a unit."),
        ("B3", "Product page &rarr; <b>Make this</b>",
         "How many batches: 5; date; into Main store. <b>Plan it</b>.",
         "Each material: needed, in stock, Enough or <b>Short by</b>."),
        ("B4", "Production page &rarr; <b>Start: materials out</b>", "Change a quantity only if the store gave more or less.",
         "The materials leave the stores."),
        ("B5", "Production page &rarr; <b>Finish: into stock</b>",
         "Good 1,180, Damaged 20, into Main store. Expiry empty: made date + shelf life.",
         "A new batch in Main store, at its real cost per packet."),
        ("B6", "Inventory &rarr; <b>Sales</b> &rarr; Sell &rarr; <b>New sale</b>",
         "Customer, From store, date. Line: Biscuit 10 CARTON, price empty. <b>Sell and deliver</b>.",
         "\"SO-... delivered as DC-...\". Stock goes down from the batch that expires first."),
    ]),
    box("Next time", ["Only Part B: Stock in when goods arrive, Make when you produce, Sell when you sell. Change "
                      "a price whenever the cost changes - the item's page always shows the cost and the margin."], "ok"),
]

# ------------------------------------------------------------------ start here
story += section(0, "Start here", "Inventory &rarr; Setup &rarr; Start here",
                 "The first page: tiles (low or out, value in stock, set-up steps done), the <b>What to do</b> "
                 "checklist and the latest productions.")
story += [
    P("The checklist is worked out from your own records - nobody ticks it - and each line opens its page: stores "
      "set up, materials added, products added, recipes saved, stock in, first production finished, first sale "
      "delivered. It is for setting up only: after that you go straight to Stock in, Make and Sell."),
]

# ------------------------------------------------------------------ setup
story += section(1, "Setup", "Inventory &rarr; Setup",
                 "The lists everything else uses. Done once; changed rarely.")
story += [grid(("Page", "What you add"), [
    ("Units", "<b>Add standard units</b> adds KG, PCS, BAG, CARTON ... ; or add your own (code, name, decimals)."),
    ("Item categories", "Groups for lists and reports, one type each: Dry goods (raw), Wrappers (packaging), "
                        "Biscuits (products)."),
    ("Stores", "Each store: its branch, code, name and type - raw, packaging, finished goods, quarantine, rejected."),
    ("Bins", "Optional shelves or racks inside a store."),
    ("Inventory settings", "Average cost for the whole company or per store; expiry warning days; label sizes; "
                           "<b>Warranty</b> on or off (off by default)."),
    ("Suppliers, Customers", "Who you buy from and sell to (code, name, payment terms, credit limit)."),
    ("Price lists", "Optional: a customer's own prices; Sell uses them first."),
    ("Cost rates", "What an hour of a worker or a machine costs (Production)."),
], (36, 134))]

# ------------------------------------------------------------------ materials
story += section(2, "Materials", "Inventory &rarr; Materials &rarr; Materials &rarr; Add material",
                 "What you use up making products: raw materials and packaging. The list shows each one with what "
                 "is in stock, minimum stock, value and a chip: <font color='#2f6b34'><b>OK</b></font>, "
                 "<font color='#7d5510'><b>Low</b></font>, <font color='#a32a1e'><b>Out</b></font>, and "
                 "<font color='#a32a1e'><b>Below cost</b></font> when it is sold for less than it costs.",
                 "units, categories and stores (Setup).", "Only with Production on.")
story += [P("Item", "h2"), fields([
    ("How you get it", "Yes", "Bought, Made here, or Both.", "Bought",
     "Made here or Both: it gets its own recipe and cost, and is made with Make (a cream made in-house)."),
    ("Type", "Yes", "Raw material or Packaging.", "Raw material", "Decides the categories offered."),
    ("Material name", "Yes", "What everyone calls it.", "Flour", "Shown on every list, document and label."),
    ("Item code", "No", "Leave it empty, or write your own.", "RM-0012", "Empty: the next number is given."),
    ("Category", "Yes", "One of the material categories.", "Dry goods", "Groups it in lists and reports."),
]), P("Units", "h2"), fields([
    ("Base unit", "Yes", "What stock is counted in.", "KG", "Fixed once stock has moved."),
    ("Purchase unit / Contains", "No", "A bigger unit you buy in, and how many base units it holds.", "BAG, 50",
     "Stock in can take bags; stock stays in kg."),
]), P("Stock control", "h2"), fields([
    ("Minimum stock", "No", "The least you want in stock.", "200", "Below it: Low, in the reorder report."),
    ("Reorder quantity", "No", "How much you usually order at once.", "500", "Suggested on purchases."),
    ("Default store", "No", "Where it is normally kept.", "Raw store", "Production takes from this store first."),
    ("Expected buy price", "No", "Only an estimate, before the first purchase.", "52 per KG",
     "Lets a product be costed early. The real price always comes from Stock in."),
]), P("Batches &amp; expiry", "h2"), fields([
    ("Batch tracking", "No", "On for food, chemicals ...", "on", "Each delivery gets its own batch number."),
    ("Expiry tracking / Shelf life", "No", "On when it expires; the days it keeps.", "on, 180 days",
     "Expired stock is never used. Shelf life fills the expiry from the manufacture date."),
    ("QC before use", "No", "On when new stock must be checked.", "", "New stock waits until it passes."),
]), P("Warranty (only when Warranty is on)", "h2"), fields([
    ("Warranty (repair)", "No", "The supplier's warranty, in months.", "12", "Runs from the day it was received."),
    ("Guarantee (replace)", "No", "Its first months, when the supplier replaces.", "3",
     "Never longer than the warranty."),
    ("Serial number per unit", "No", "On when each unit has its own number.", "", "Units are entered one by one."),
]), Spacer(1, 4), box("After Add material", [
    "The material's page opens with <b>Put it in stock</b>: Stock in, with this material already on line 1. "
    "<b>Edit</b> (on its page or its row in the list) opens the same form again.",
], "ok")]

# ------------------------------------------------------------------ products
story += section(3, "Products", "Inventory &rarr; Products &rarr; Products &rarr; Add product",
                 "What you sell. The list shows how you get each one, what is in stock (also in packs), the sale "
                 "price, the cost of one, the margin and a chip - <font color='#a32a1e'><b>Below cost</b></font> "
                 "when its price is under its cost.")
story += [P("Item", "h2"), fields([
    ("How you get it", "Some", "Made here, Bought ready, or Both.", "Made here",
     "Only with Production. Made here and Both get a recipe, a cost of one batch and Make."),
    ("Product name, Item code, Category", "Yes", "As for materials (code empty: FG-0001 ...).", "Biscuit 100 g", ""),
]), P("Units", "h2"), fields([
    ("Base unit", "Yes", "The smallest unit you sell.", "PCS (packet)", "Fixed once stock moves."),
    ("Selling pack / Contains", "No", "A bigger unit you also sell, and its size.", "CARTON, 24",
     "Its own price is set on the product's page (Units and prices)."),
    ("VAT", "No", "The VAT %, if any.", "15", "Used on invoices."),
]), P("Stock control", "h2"), fields([
    ("Minimum stock as", "No", "A quantity, or days of sales.", "Days of sales", ""),
    ("Minimum stock / Days of sales", "No", "The quantity, or how many days of sales to keep.", "5,000 / 15",
     "Below it: Low; also used to suggest how many batches to make."),
    ("Reorder quantity", "No", "How much you usually make or buy at once.", "", ""),
    ("New stock goes to", "No", "The finished goods store.", "Main store", "Offered first when you make it."),
]), P("Batches &amp; expiry", "h2"), fields([
    ("Batch tracking / Expiry tracking / Shelf life", "No", "As for materials.", "on, on, 180 days",
     "A made batch expires on the made date + shelf life (or earlier, never later)."),
    ("QC before use", "No", "New stock is checked first.", "", ""),
    ("Batch number per mix", "No", "Off: one batch number per production. On: one per mix.", "", ""),
]), P("Warranty (only when Warranty is on)", "h2"), fields([
    ("Warranty (repair)", "No", "Your warranty to the customer, in months.", "12", "Runs from the delivery date."),
    ("Guarantee (replace)", "No", "Its first months, when a faulty unit is replaced.", "3",
     "Never longer than the warranty. After it, to the warranty's end: repair."),
    ("Serial number per unit", "No", "On when each unit is scanned out.", "", "See step 10."),
]), Spacer(1, 4), P("<b>Product page</b>: tiles (cost of one, sale price, margin, in stock, sold per day); the "
                    "<b>Sale price</b> card; how many batches to make; the recipe; the cost of one batch; stock by "
                    "store and batch; units and prices.")]

story += [box("The product's page, step by step", [
    "A bar at the top shows the steps in the order the work is done and stays in view as you scroll: "
    "<b>1 Details and units</b> &rarr; <b>2 Recipe</b> &rarr; <b>3 Cost of one batch</b> &rarr; <b>4 Sale price</b> "
    "&rarr; <b>5 Make</b> &rarr; <b>6 Stock and sell</b>. Done steps are green, the next one is blue; click a step "
    "to jump to it. A product that is only bought: Details &rarr; Stock in &rarr; Sale price &rarr; Stock and sell.",
    "A step that needs an earlier one says so and points to it: the <b>Sale price</b> waits until the cost is "
    "known, <b>Make</b> waits for the recipe, and the recipe asks first what one batch makes, then its materials.",
], "ok")]

# ------------------------------------------------------------------ recipe and cost
story += section(4, "Recipe and cost of one batch", "The product (or a made material) &rarr; Recipe / Cost of one batch",
                 "What one batch makes and uses, and what it costs. It gives the cost of one unit.",
                 "the product or material is Made here or Both.")
story += steps([
    "With no recipe yet the page asks <b>how much one batch makes</b>: write it and Save.",
    "<b>Add material</b>: any material, a new one made right there, or something else made here (a cream). Write how "
    "much <b>one batch</b> uses and its normal wastage %.",
    "<b>Cost rates</b> (Inventory &rarr; Setup &rarr; Cost rates): what an hour of a worker or a machine costs.",
    "<b>Add cost</b>: people x hours x rate, machine hours x rate, a fixed amount per batch (gas), or a % of the "
    "materials.",
])
story += [box("What you read", [
    "Materials (recipe x each material's average cost, or its expected buy price before the first purchase) + "
    "the costs = <b>one batch</b>; divided by what one batch makes = <b>one unit</b>.",
    "A material with no cost yet is named in red: buy it (Stock in) or give its expected buy price.",
])]

# ------------------------------------------------------------------ stock in
story += section(5, "Stock in", "Inventory &rarr; Stock &rarr; Stock in &rarr; Stock in",
                 "Everything that comes into a store: a purchase, the first count, or a customer return.",
                 "suppliers or customers, and the items.")
story += [fields([
    ("Where it comes from", "Yes", "A purchase, First count (opening stock), or A customer returned it.",
     "A purchase", "Decides the boxes below."),
    ("Supplier / Customer", "Some", "Who it came from.", "Sweet Mills", ""),
    ("Into store, Date", "Yes", "The store and the day it arrived.", "Raw store", "A closed month is refused."),
    ("Why it came back", "Some", "For a return.", "Wrong order", ""),
    ("Item, Quantity, Unit", "Yes", "One line per item; <b>Add a line</b> for more.", "Flour, 10, BAG",
     "Only the item's own units are offered."),
    ("Cost", "Some", "What you paid, per the unit chosen.", "2,600 per BAG", "The buy price: sets the average cost."),
    ("Sale price (optional)", "No", "A new sale price, now that the buy price is in. It shows the price now.",
     "now 60", "Empty: the price stays. Below the new cost: refused unless Sell below cost."),
    ("Batch number", "Some", "From the label (items with batches).", "FL-231026", "Empty: one is made."),
    ("Manufacture date", "Some", "From the label (items that expire).", "1 Oct 2026",
     "Not after the day it came in, and not a day still to come."),
    ("Expiry date", "Some", "From the label.", "1 Apr 2027",
     "Empty: manufacture date + shelf life. Never before the manufacture date."),
    ("Condition", "Some", "Return: good or damaged.", "Damaged", "Damaged goes to the rejected store."),
    ("Sell below cost / Why below cost", "No", "Only if a sale price below cost is meant.", "on, clearing old stock",
     "Kept with the price change in the audit log."),
]), Spacer(1, 4), box("On Put in stock", [
    "A purchase is received and posted at once; a first count is posted if you may approve opening stock, otherwise "
    "it waits; a return is posted. If any line is refused, nothing is saved - the line and the box are named.",
])]

story += [box("The line fills itself", [
    "Choose the item and the line is filled from what the software already knows: the <b>unit it is bought in</b> "
    "(a bag, a carton), its <b>cost</b> - the last price paid, else its expected buy price, for that unit - and "
    "above, its <b>usual store</b> and <b>last supplier</b>. Under the expiry box it says the date the shelf life "
    "will give. Usually only the <b>quantity</b> is left to type. Change anything that differs on the bill: what "
    "you type is never replaced.",
], "ok")]

# ------------------------------------------------------------------ price
story += section(6, "Sale price", "The item's page &rarr; card Sale price",
                 "Set or change the price once the cost is known. The card shows the cost of one and the margin now.",
                 "a cost: a Stock in (bought) or a recipe (made).")
story += [fields([
    ("Margin you want", "No", "A %.", "30", "Fills the sale price: cost / (1 - 30 %)."),
    ("Sale price", "Yes", "For one base unit.", "20", "The margin shows as you type; below cost shows red."),
    ("Sell below cost / Why", "Some", "Only when a loss is meant.", "on, expires next week",
     "Without them a price below cost is refused. Kept in the audit log."),
]), Spacer(1, 4), P("<b>A pack's own price</b> (a carton): Units and prices &rarr; Add a unit &rarr; Sale price of one "
                    "pack - the same below-cost check, against the pack's cost.")]

# ------------------------------------------------------------------ make
story += section(7, "Make: Plan, Start, Finish", "Inventory &rarr; Production &rarr; Make, or <b>Make this</b> on the item",
                 "Making a number of batches: Plan it, Start it when the materials leave the stores, Finish it "
                 "with what came out.", "the recipe, and the materials in stock.", "Only with Production on.")
story += steps([
    "<b>Plan</b>: how many batches, the date and the store. Each material shows needed, in stock, Enough or <b>Short "
    "by</b>. Short: <b>Make fewer</b>, <b>Buy the rest</b>, or <b>Make it first</b> (something made here). "
    "<b>Plan it</b>.",
    "<b>Start: materials out</b>: change a quantity only if a store gave more or less (over 5 % needs a reason). "
    "Stock goes down at its real cost.",
    "<b>Finish: into stock</b>: Good and Damaged; the store (or a split); leftovers back; other costs (filled from "
    "the cost of one batch). Batch number empty: one is made.",
])
story += [box("Dates of what is made", [
    "The manufacture date is the day it is made - never a day still to come. Expiry empty: made date + shelf life; "
    "typed: not before the made date and not after made date + shelf life.",
]), box("The result", [
    "Cost of one = (materials used + other costs) / good. <b>Reverse</b> (with a reason) while nothing made was sold, "
    "moved or used.",
])]

# ------------------------------------------------------------------ stock, move
story += section(8, "Stock and Move stock", "Inventory &rarr; Stock &rarr; Stock / Move stock",
                 "<b>Stock</b>: every item, how much, where, its value and status, and what expires first. "
                 "<b>Move stock</b>: send stock from one store to another. Adjustments and Stock counts (same "
                 "dropdown) correct stock, with approval.")
story += steps([
    "Move stock: From store, To store, date; each line item, quantity, unit and (if you want) the batch.",
    "<b>Send it</b>: the stock waits <b>in transit</b>. The other store presses <b>Receive</b> on the Move stock list.",
])

story += [P("<b>What is in a store:</b> Inventory &rarr; Setup &rarr; Stores shows how many items each store holds; "
             "<b>What's in it</b> opens Stock for that store only (\"What's in Raw store\").")]

# ------------------------------------------------------------------ sell
story += section(9, "Sell", "Inventory &rarr; Sales &rarr; Sell &rarr; New sale",
                 "A sale, delivered at once from one store, invoiced when asked.", "customers, and stock in the store.")
story += [fields([
    ("Customer, From store, Date", "Yes", "Who, from where, when.", "City Shop, Main store", ""),
    ("Make the invoice now", "No", "Tick to invoice at once.", "on", ""),
    ("Item, Quantity, Unit", "Yes", "Packet or carton.", "Biscuit, 10, CARTON", ""),
    ("Price", "No", "Empty: the customer's price list, else the pack's price, else the product's.", "",
     "Below the cost of what goes out: refused unless Sell below cost."),
    ("Why, if over the credit limit", "Some", "Only when the customer is over their limit.", "", ""),
    ("Sell below cost / Why below cost", "No", "Only when a loss is meant.", "on, damaged boxes",
     "The reason is written on the sale."),
]), Spacer(1, 4), P("<b>Sell and deliver</b>: confirmed and delivered, the batch that expires first going first. More "
                    "than the store has: refused, nothing saved.")]

# ------------------------------------------------------------------ warranty
story += section(10, "Warranty", "Inventory &rarr; Tools &rarr; Warranty (only when switched on)",
                 "Serial numbers and claims.", "Warranty on (Setup &rarr; Inventory settings), and months on the items.")
story += steps([
    "<b>Serial numbers</b>: made when a production finishes; <b>Serial numbers in</b> for units you received; "
    "<b>Serial numbers out</b>: scan each unit on its delivery - the dates run from that day.",
    "<b>New claim</b>: scan the serial (or choose the item and the delivery date). The form says which applies.",
])
story += [grid(("The form says", "What you do"), [
    ("In guarantee until ...: replace", "<b>Replace it</b>. Anything else (repair, refund) needs a reason."),
    ("In warranty until ...: repair", "<b>Repair it</b> (its cost). Replace or refund needs a reason."),
    ("Out of warranty since ...", "<b>Refuse</b> (say why), or cover it with a reason."),
], (60, 110)), Spacer(1, 4), P("The faulty unit can go into the rejected store; a new unit leaves a store."),
    box("Reports", ["Inventory &rarr; Reports: production cost, material use, waste, shortages, stock on hand, "
                    "valuation, batch and expiry, reorder, purchase and sales registers - each with filters, "
                    "Excel and PDF; Month-end close beside it. Tools: Scan, Labels, Warranty."]),
]

# ------------------------------------------------------------------ quality check
story += section(11, "Quality check", "Inventory &rarr; Stock &rarr; Quality check",
                 "An item with <b>Quality check before use</b> puts every new batch - made or bought - in "
                 "quarantine: it is counted in stock but cannot be used, moved or sold until it passes.",
                 "an item with Quality check before use, and someone with \"Record quality checks\".")
story += steps([
    "Open <b>Stock &rarr; Quality check</b> (Start here and the item's page also show what waits). Each row: the "
    "item, the batch, where it came from (Made &middot; PRD-... or Bought &middot; GRN-...), how much, the store and "
    "the expiry.",
    "<b>Pass</b>: the batch is released at once - it can be used and sold.",
    "<b>Fail</b>: write what failed; the batch is blocked. Bought goods go back with a purchase return; made goods "
    "are written off with a stock adjustment.",
])
story += [box("\"I have 500 in stock but cannot sell it\"", [
    "That stock waits for its quality check. Sell says so: \"500 PCS of Orange juice in Main store waits for a "
    "quality check\". Pass it on Stock &rarr; Quality check, then sell.",
], "warn")]

# ------------------------------------------------------------------ purchase
story += section(12, "Buying with orders", "Inventory &rarr; Purchase &rarr; Requisitions / Purchase orders / "
                 "Goods receipts / Quality check / Purchase returns",
                 "For buying that needs approval and a paper trail. Every purchase page shows the flow at the top "
                 "- <b>Ask (optional) &rarr; Order &rarr; Receive &rarr; Quality check &rarr; In stock</b>, and "
                 "<b>Return to supplier</b> - with its own step lit and, on a document, the next thing to press.",
                 "suppliers, items, stores.", "Small purchase, no approval? Stock in &rarr; A purchase is one step.")
story += [P("1 &middot; Ask to buy (optional): New requisition", "h2"), fields([
    ("Branch, Department", "Yes", "Who needs it.", "Head Office, Production", "The approver of that branch decides."),
    ("Date, Needed by", "Some", "Today, and when it is needed.", "5 Nov", ""),
    ("Why it is needed", "No", "One line.", "Stock below minimum", ""),
    ("Lines: Item, Qty, Unit, Note", "Yes", "Add line for each item.", "Sugar 500 KG", ""),
]), P("<b>Submit</b> &rarr; the approver presses <b>Approve</b> or <b>Reject</b>. Approved lines become an order: "
      "Purchase orders &rarr; <b>From requisitions</b>."),
    P("2 &middot; Order: New order", "h2"), fields([
    ("Supplier", "Yes", "Who you buy from.", "Sweet Mills", "One order per supplier."),
    ("For branch", "Yes", "Where it is delivered.", "Head Office", ""),
    ("Order date, Expected date", "Some", "Today; when it should arrive.", "", "Late orders show as overdue."),
    ("Terms, Note", "No", "Payment terms, anything for the supplier.", "30 days", "Printed on the order PDF."),
    ("Lines: Item, Qty, Unit, Unit price, Discount %, VAT %, Note", "Yes", "Add line for each item.",
     "Sugar, 500, KG, 125", "The total shows on the order."),
]), P("<b>Send for approval</b> &rarr; <b>Approve</b> &rarr; <b>Email to supplier</b> (or the PDF). Nothing is in "
      "stock yet. When the goods come: <b>Receive goods</b>."),
    P("3 &middot; Receive: Goods receipt (GRN)", "h2"), fields([
    ("Order", "No", "The order it comes against.", "PO-2026-00012", "Lines fill from the order; the order shows what is still due."),
    ("Supplier, Store, Received on", "Yes", "Who sent it, into which store, the day.", "Raw store", ""),
    ("Challan no, Invoice no, Vehicle no", "No", "From the supplier's papers.", "", "Kept with the receipt."),
    ("Received / Refused at the gate", "Yes", "What came, what you sent back at once.", "500 / 10", "Only the accepted part goes in."),
    ("Unit cost", "Yes", "What you pay per unit.", "125", "Sets the average cost."),
    ("Batch no, Supplier batch no", "Some", "Items that keep batches.", "SG-1101", "Empty: one is made."),
    ("Manufacture date, Expiry date", "Some", "From the label.", "", "Expiry empty: manufacture date + shelf life."),
    ("Packages, Note", "No", "Bags or cartons; anything.", "10 bags", "Package labels can be printed."),
]), P("<b>Post</b>: the stock goes into the store. Items with a quality check wait on Stock &rarr; Quality check."),
    P("4 &middot; Send back: Purchase return", "h2"), fields([
    ("Receipt, Supplier, Returned on", "Yes", "Which receipt it came on.", "GRN-2026-00007", ""),
    ("Why it goes back", "Yes", "Failed check, damaged, wrong item.", "Damp bags", ""),
    ("Lines: Received line, Item, Batch, Store, Bin, Qty, Unit, Note", "Yes", "What goes back.", "Sugar, 20 KG",
     "Posting takes it out at the receipt's cost."),
])]

# ------------------------------------------------------------------ sales
story += section(13, "Selling with orders", "Inventory &rarr; Sales &rarr; Sell / Customers / Price lists, and "
                 "Sales orders, Deliveries, Invoices, Sales returns",
                 "<b>Sell</b> (step 9) does order, delivery and invoice in one go. For an order that is delivered "
                 "later or in parts, the documents one by one:", "customers, stock, prices.")
story += [P("Sales order", "h2"), fields([
    ("Customer, Store, Order date", "Yes", "Who, from where, when.", "City Shop, Main store", ""),
    ("Delivery date, Source, Customer ref.", "No", "When to deliver, how it came, their PO number.", "PO-7781",
     "The ref. prints on the invoice and challan."),
    ("Delivery address", "No", "Where it goes.", "", "Empty: the customer's address."),
    ("Lines: Item, Store, Qty, Unit, Unit price, Discount %, VAT %, Note", "Yes", "Add line for each item.",
     "Juice, 10, CARTON", "Price empty: the customer's price list, else the item's price. Store empty: the order's."),
]), P("<b>Confirm</b> (over the credit limit needs a reason) &rarr; <b>Deliver</b>."),
    P("Delivery (challan)", "h2"), fields([
    ("Order, Store, Delivery date", "Yes", "Filled from the order.", "", "One challan per store."),
    ("Vehicle no, Driver, Note", "No", "Who carries it.", "Dhaka Metro-T 11-2233", "Printed on the challan."),
    ("Lines: Batch, Bin, Qty", "Some", "Filled: the batch that expires first.", "", "Another batch needs a reason."),
]), P("<b>Challan</b> prints the delivery challan; <b>Post</b> takes the stock out; <b>Invoice</b> makes the invoice."),
    P("Invoice", "h2"),
    P("Made from the delivery with its prices and VAT. <b>Issue</b> it, print the <b>PDF</b>, and mark it "
      "<b>Paid</b> (date and reference) when the money comes."),
    box("The invoice and the challan", [
        "Both print on your letterhead: logo, name, address, phone, email, BIN/TIN and registration number "
        "(Organisation &rarr; Company profile), the document's number, date and references in a box, Bill to and "
        "Ship to (or Deliver to), and the lines.",
        "The invoice adds the subtotal, VAT and the total, the <b>amount in words</b> (Taka ... in lakh and crore), "
        "the payment terms and signature lines. A draft, cancelled or paid invoice says so across the page.",
        "The challan adds batch and expiry, the total quantity and three signatures: store, driver and the "
        "customer (with seal).",
    ], "ok"),
    P("Sales return", "h2"), fields([
    ("Delivery, Customer, Store, Date, Reason", "Yes", "What it came on and why.", "Leaking", ""),
    ("Lines: Delivery line, Item, Batch, Condition, Qty, Unit", "Yes", "Good or Damaged.", "Damaged, 2",
     "Good goes back to stock; damaged to the rejected store."),
]), P("Price lists", "h2"), fields([
    ("Name, Valid from, Valid to", "Yes", "One list, for some customers.", "Wholesale 2026", ""),
    ("Prices: Item, Unit, Price, Min qty", "Yes", "A price per unit, from a quantity.", "Juice, CARTON, 430, 10",
     "Sell and sales orders take this price first."),
])]

# ------------------------------------------------------------------ adjust
story += section(14, "Adjust stock", "Inventory &rarr; Stock &rarr; Adjustments &rarr; New",
                 "When you know the change for a few items: 5 damaged, 2 found, 10 expired. Each page shows which "
                 "one you need, the steps with the current one lit, and what to press next.",
                 "someone else who may approve adjustments.")
story += [fields([
    ("Store, Date", "Yes", "Where and when.", "Raw store", ""),
    ("Reason", "Yes", "Damaged, Expired, Found, Lost or stolen, Sample, Correction, Transfer shortage.", "Damaged", ""),
    ("Note", "No", "Anything.", "Torn bags", ""),
    ("Lines: Item, Batch, Bin", "Yes", "Which stock.", "Sugar, SG-1101", ""),
    ("Change (+ found, - out)", "Yes", "Plus for found, minus for written off.", "-5", ""),
    ("Unit, Unit cost (found stock), New batch no, Expiry", "Some", "For stock found: its cost and batch.", "",
     "Empty cost: the average cost."),
]), Spacer(1, 3), box("See the effect first", [
    "While it is a draft or waiting, each line shows <b>In store now</b> and <b>After this</b> - for example 30 KG "
    "&rarr; 25 KG. <b>Send for approval</b>; nothing changes until the approver presses <b>Approve and post</b>. "
    "A mistake after that: <b>Reverse</b> it with a reason.",
])]

# ------------------------------------------------------------------ count
story += section(15, "Stock count", "Inventory &rarr; Stock &rarr; Stock counts &rarr; New",
                 "To make the software match the shelves of a whole store.", "the store.")
story += steps([
    "<b>Start</b>: the store and the date. The software's quantities are frozen on the lines.",
    "<b>Count</b>: type the counted quantity on each line (in the base unit) or scan the labels; <b>Save</b>. "
    "Found something not on the list: <b>Add</b> (item, batch, bin, counted).",
    "<b>Not found = 0</b> sets every line not counted to zero.",
    "<b>Send for approval</b>; the approver sees every difference and presses <b>Approve</b>: the differences are "
    "posted as one stock adjustment.",
])

# ------------------------------------------------------------------ reports
story += section(16, "Reports", "Inventory &rarr; Reports",
                 "Every report has filters, Excel and PDF; money only with \"See stock costs and values\".")
story += [grid(("Report", "What it shows"), [
    ("Stock on hand", "What is in each store now, by item and batch."),
    ("Stock ledger (item card)", "Every movement of one item in a period, with the running balance."),
    ("Movement summary", "Opening, in, out and closing per item for a period."),
    ("Stock valuation", "What the stock is worth on a date, at average cost."),
    ("Batch and expiry", "Batches with their expiry; what expires soon."),
    ("Stock aging", "How long stock has been sitting."),
    ("Slow and non-moving stock", "Items that have not moved for a while."),
    ("Reorder report", "Items below the stock to keep, and how much to buy or make."),
    ("Purchase register / GRN register", "Orders and goods received in a period."),
    ("Consumption by department", "What each department took from the stores."),
    ("Production yield and wastage", "Planned against good and damaged."),
    ("Production cost / Material use", "Cost per unit against expected; materials used against the recipe."),
    ("Waste and failed productions", "What was lost, and its cost."),
    ("Shortages", "Materials short for the planned productions."),
    ("Sales register / Customer and item sales", "Invoices in a period; who bought what."),
], (60, 110))]

# ------------------------------------------------------------------ questions
story += [CondPageBreak(80 * mm), Spacer(1, 6 * mm), P("Questions people ask", "h1"), grid(("When", "What happens"), [
    ("I don't know the price when adding", "Right: no price is asked. Buy it (Stock in), then set the price."),
    ("I typed a price below cost", "Refused, with the loss shown. Fix it, or Sell below cost with a reason."),
    ("A material is only in another branch", "It shows as short: move it first (Move stock)."),
    ("A batch expires before Start", "It is not taken; the next batch is."),
    ("Nothing good came out", "Finish with 0 good and a reason: a failed production, its cost a loss."),
    ("A mistake after Finish", "Reverse it (a reason) while nothing made was touched."),
    ("Delete an item", "Only while it has never moved and is in no recipe; else Deactivate."),
    ("Where is Opening stock?", "Stock in &rarr; Where it comes from: First count (opening stock)."),
    ("No \"See costs\"", "Quantities only: no money on any screen."),
], (60, 110))]


def build():
    doc = SimpleDocTemplate(OUT, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm,
                            bottomMargin=18 * mm, title="Products and stock - step by step", author="IGL Web Ltd.",
                            subject="Materials, products, recipe and cost, stock in, sale price, make, stock, sell, warranty")
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)


if __name__ == "__main__":
    build()
    print("written", OUT)
