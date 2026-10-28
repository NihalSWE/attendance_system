"""Products and stock - the step-by-step tutorial of the redesigned screens
(2026-10-28, docs/inventory/PRODUCTION_PLAN.md §15 phase 9.3).

    venv/Scripts/python docs/scripts/build_products_tutorial.py

writes docs/inventory/PRODUCTS-AND-STOCK-TUTORIAL.pdf. It follows the flow bar
of the new pages - Start here, Materials, Products (recipe and cost), Stock in,
Make, Stock, Move stock, Sell, Warranty - with every box: what to write and what
the software does with it. Rebuild it when those screens or their rules change.
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
    labels = [("Start here", "checklist"), ("1 Materials", "what you use"), ("2 Products", "recipe, cost"),
              ("3 Stock in", "buy, count"), ("4 Make", "plan, start, finish"), ("5 Stock", "where it is"),
              ("6 Move", "store to store"), ("7 Sell", "deliver, invoice")]
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
    P("From materials to a sale: adding materials and products, the recipe and what one batch costs, "
      "stock in, making, stock, moving stock between stores, selling, and warranty.", "subtitle"),
    Spacer(1, 6 * mm),
    P("For owners, store managers, store keepers, production and sales staff. Read it once from top to "
      "bottom; after that, use each step while you work. Every page ends with a <b>Next step</b> box: "
      "it always says what to do next."),
    Spacer(1, 6 * mm), flow_diagram(), Spacer(1, 6 * mm),
    box("How every screen behaves", [
        "&bull; <b>The flow bar</b> at the top of every page is the menu in the order of the work. "
        "Materials and Make show only when your company has Production; Warranty only when it is on.",
        "&bull; <b>Saving never reloads the page.</b> The answer appears just above the Save button: green "
        "when saved, red when not - and each wrong box is outlined red with its reason under it. "
        "What you typed stays.",
        "&bull; <b>Each value is asked once.</b> The unit is set on the item; every quantity after that shows "
        "it beside the box. Lists offer only what fits: a store's batches, an item's own units.",
        "&bull; <b>Every list</b> has SL numbers, Show N, search and pages. Click a name to open it.",
        "&bull; <b>Money</b> (costs, values, margins) shows only to people given \"See stock costs and "
        "values\".",
    ]),
    PageBreak(),
]

# ------------------------------------------------------------------ words
story += [
    P("Words used on the screens", "h1"),
    grid(("Word", "What it means"), [
        ("Store", "A warehouse or room: raw store, packing store, main store, depot. A branch can have many."),
        ("Material", "Something you buy and use up making products: flour, sugar, wrappers, cartons."),
        ("Product", "Something you sell. It is bought ready, made here, or both."),
        ("Unit", "What stock is counted in, set once per item: packet, kg, piece."),
        ("Bigger unit", "1 carton = 24 packets; 1 bag = 50 kg. A carton can have its own price."),
        ("Batch (one mix)", "The recipe is written for one batch: \"1 batch = 10 cartons\". Making 50 cartons "
                            "is 5 batches."),
        ("Batch number", "Stock that came in together (one delivery, one production), with its own dates, "
                         "expiry and cost. The batch that expires first always goes first."),
        ("Production", "One making of a product, in three steps: Plan, Start, Finish."),
        ("Stock to keep", "Below it an item shows Low. A quantity, or (products) days of sales."),
    ], (34, 136)),
    Spacer(1, 6 * mm),
    P("Before you start (once)", "h2"),
    *steps([
        "<b>Switches</b> (the platform owner): Store &amp; Inventory on; Production on for a company that makes "
        "things. A company that only buys and sells never sees materials, recipes or production.",
        "<b>Inventory settings</b>: average cost for the whole company or per store, expiry warning days, "
        "label sizes, and <b>Warranty</b> (off by default).",
        "<b>Units</b> (Add standard units adds KG, PCS, CARTON, BAG ...), <b>Item categories</b>, <b>Stores</b> "
        "(each in a branch, with its type: raw, packaging, finished goods, quarantine, rejected), <b>Suppliers</b> "
        "and <b>Customers</b>. These pages are in the Inventory menu.",
        "<b>Access</b>: who may set up items, plan, start and finish production, receive goods, move stock, "
        "sell, see costs, record warranty claims (Organisation &rarr; Access).",
    ]),
]

# ------------------------------------------------------------------ start here
story += section(0, "Start here", "Inventory &rarr; Start here (the flag in the flow bar)",
                 "The first page: tiles (materials and products low or out, the value in stock, set-up steps "
                 "done), the <b>What to do</b> checklist and the latest productions.")
story += [
    P("The checklist is worked out from your own records, and each line opens the page where it is done:"),
    *bullets(["Stores set up", "Materials added (Production)", "Products added", "Recipes saved (Production)",
              "Stock in", "First production finished (Production)", "First sale delivered",
              "Below stock to keep - the items to buy or make now"]),
    box("Next step", ["The box at the bottom always names the first thing not done yet, with one button to it."], "ok"),
]

# ------------------------------------------------------------------ materials
story += section(1, "Materials", "Inventory &rarr; Materials (flow bar 1)",
                 "What you buy and use up in production: raw materials and packaging. The list shows each one "
                 "with what is in stock (in the stores you see), stock to keep, value and a status chip: "
                 "<font color='#2f6b34'><b>OK</b></font>, <font color='#7d5510'><b>Low</b></font>, "
                 "<font color='#a32a1e'><b>Out</b></font>. Filters: category, store, status.",
                 "units, categories and stores.", "Only with Production on.")
story += [P("Add material", "h2"), fields([
    ("Type", "Yes", "Raw material or Packaging.", "Raw material", "Decides which categories are offered and where it shows."),
    ("Name", "Yes", "What everyone calls it.", "Flour", "Shown on every list, document and label."),
    ("Code", "No", "Leave it empty, or write your own.", "RM-0012",
     "Empty: the next number is given (RM-0012 after RM-0011). Must be unique."),
    ("Category", "Yes", "One of the material categories.", "Dry goods", "Groups it in lists and reports."),
    ("Unit", "Yes", "What stock is counted in.", "KG",
     "Every quantity of it is in this unit. It cannot change once stock has moved."),
    ("Bought in / One of those holds", "No", "A bigger unit you buy it in, and its size.", "BAG, 50",
     "Purchases can be entered in bags; the stock stays in kg."),
    ("Stock to keep", "No", "The least you want in stock.", "200",
     "Below it the material shows Low, is in the morning email and the reorder report."),
    ("Usual store", "No", "Where it is normally kept.", "Raw store A",
     "Production takes it from this store first, then the other stores of the branch."),
    ("Keeps batches / Has an expiry date / Shelf life", "No", "Switch on for food, chemicals ...", "on, 180 days",
     "Each delivery becomes a batch number with its dates; expired stock can never be used."),
    ("Quality check before use", "No", "Switch on when new stock must be checked.", "",
     "New stock waits in quarantine until it passes."),
]), Spacer(1, 4), P("<b>Material page</b> (click its name): in stock, average cost, how many products use it; "
                    "stock by store and batch; <b>Used in</b>; its units. <b>Edit</b> changes it; <b>Delete</b> "
                    "appears only while it has never moved - after that it is <b>Deactivate</b>.")]

# ------------------------------------------------------------------ products
story += section(2, "Products", "Inventory &rarr; Products (flow bar 2)",
                 "What you sell. The list shows how you get each one (Buy / Make / Both), what is in stock (also in "
                 "cartons), the sale price, the cost per unit, the margin and a status chip.")
story += [P("Add product", "h2"), fields([
    ("How you get it", "Some", "Make it here, Buy it ready, or Both.", "Make it here",
     "Only with Production. Make and Both get a recipe, a cost of one batch and the Make button."),
    ("Name, Code, Category", "Yes", "As for materials (code empty: FG-0001 ...).", "Biscuit 100 g", ""),
    ("Smallest unit you sell", "Yes", "The unit stock is counted in.", "PCS (packet)", "Fixed once stock moves."),
    ("Sale price", "Yes", "For one of that unit.", "20", "Used on sales unless the customer's price list says else."),
    ("Also sold as / One of those holds / Its sale price", "No", "A bigger unit, its size and its own price.",
     "CARTON, 24, 450", "A carton need not cost 24 x the packet price: sales use 450."),
    ("Stock to keep as", "No", "A quantity, or days of sales.", "Days of sales: 15",
     "Days: worked out from the last 30 days of deliveries (less returns). Used for Low and to suggest how much to make."),
    ("New stock goes to", "No", "The finished goods store.", "Main store", "Offered first when planning a production."),
    ("Keeps batches / expiry / shelf life", "No", "As for materials.", "on, 180 days",
     "A made batch expires made date + shelf life."),
    ("New batch number for every batch made", "No", "On: one batch number per mix.", "",
     "Off: one batch number per production. On: one per mix, into one store."),
    ("Warranty (months) / A serial number for each unit", "No", "Only when Warranty is on.", "12",
     "See step 8."),
]), Spacer(1, 4),
    P("<b>Product page</b>: tiles (cost per unit - the expected one for a made product - sale price, margin, in "
      "stock, sold in the last 30 days and per day); the suggestion how many batches to make; the recipe; the cost "
      "of one batch; stock by store and batch; its productions (or purchases); units and prices."),
]

# ------------------------------------------------------------------ recipe and cost
story += section("2b", "Recipe and cost of one batch", "Products &rarr; open the product &rarr; Recipe / Cost of one batch",
                 "What one batch makes and uses, and what one batch costs: materials plus people, machines and "
                 "other costs. It gives the expected cost per unit, per carton and the margin.",
                 "the product (Make or Both) and its materials.")
story += steps([
    "With no recipe yet the page asks <b>how much one batch makes</b> (in the product's unit): write it and Save.",
    "<b>Add material</b>: one already added, a <b>new</b> one made right there (type, name, code, category, unit), "
    "or a <b>product made here</b> (semi-finished, like cream for cream biscuits). Write how much <b>one batch</b> "
    "uses - the material's own unit is shown - and its normal wastage %.",
    "Each line shows its cost, what is in stock and how many batches that is enough for. <b>Remove</b> takes it off; "
    "<b>Batch size</b> changes what one batch makes.",
    "<b>Cost rates</b> (Inventory &rarr; Cost rates): what an hour costs - Line worker 75, Oven 300. "
    "A change counts for productions after it.",
    "<b>Add cost</b> on the product: people x hours x rate, machine hours x rate, a fixed amount per batch (gas), "
    "or a % of the materials (rent share). Only the boxes of the chosen way show.",
])
story += [box("What you read", [
    "Materials (the recipe x each material's average cost, wastage included) + the costs = <b>one batch</b>; "
    "divided by what one batch makes = <b>one unit</b>; x 24 = one carton. Margin = sale price - cost per unit.",
    "A material with no cost yet is named in red: the total is short until it has one.",
]), box("Refused", [
    "The product itself, a product that is made with this one (a loop), a product bought ready, the same "
    "material twice.",
], "bad")]

# ------------------------------------------------------------------ stock in
story += section(3, "Stock in", "Inventory &rarr; Stock in (flow bar 3) &rarr; Stock in",
                 "Everything that comes into a store. The list shows purchases, first counts and customer returns "
                 "together; click a number to open the document.",
                 "suppliers and customers; the items.")
story += [P("The form", "h2"), fields([
    ("Where it comes from", "Yes", "A purchase, the first count, or a customer return.", "A purchase",
     "Decides the boxes below and the document it makes."),
    ("Supplier / Customer", "Some", "Who it came from (purchase / return).", "Sweet Mills", ""),
    ("Into store, Date", "Yes", "The store it arrived at, and the day.", "Raw store A", "A closed month is refused."),
    ("Why it came back", "Some", "For a return.", "Wrong order", ""),
    ("Item, Quantity, Unit", "Yes", "One line per item; Add a line for more.", "Flour, 10, BAG",
     "Only the item's own units are offered; stock is counted in its unit."),
    ("Cost", "Some", "Purchase and count: per the unit chosen.", "2,600 per BAG", "Sets the average cost."),
    ("Batch number, Expiry date", "Some", "Only for items that keep them.", "FL-231026, 1 Apr 2027",
     "Batch empty: one is made. Expiry needed for items that expire."),
    ("Condition", "Some", "Return: good or damaged.", "Damaged", "Good: back to stock. Damaged: the rejected store."),
]), Spacer(1, 4), box("What happens on Put in stock", [
    "A purchase is received and posted at once (items needing a quality check wait in quarantine). A first count is "
    "posted when you may approve opening stock; otherwise it waits for whoever may. A return is posted. "
    "If any line is refused, nothing is saved - the line and box are named.",
])]

# ------------------------------------------------------------------ make
story += section(4, "Make: Plan, Start, Finish", "Inventory &rarr; Make (flow bar 4), or <b>Make this</b> on the product",
                 "A production makes a number of batches of a product. Plan it, Start it when the materials leave the "
                 "stores, Finish it with what came out.", "the recipe, and the materials in stock.",
                 "Only with Production on.")
story += [P("Plan", "h2")] + steps([
    "The product page suggests: <b>stock to keep - in stock - what is planned = batches wanted</b>, capped by what "
    "the materials allow. <b>Make N batches</b> opens the plan with that number.",
    "Write how many batches, the date and the store the products go into. The materials table follows as you type: "
    "needed (wastage included), usable in the branch, taken from which stores (usual store first, the batch that "
    "expires first), Enough or <b>Short by</b>.",
    "Short: <b>Make fewer</b> sets the most the materials allow; <b>Buy the rest</b> makes a purchase requisition for "
    "exactly what is missing; a semi-finished product offers <b>Make it first</b>. Another planned production needing "
    "the same material is named.",
    "<b>Plan it</b>. A short plan can be saved; it cannot start until the materials are in.",
]) + [P("Start", "h2")] + steps([
    "The production page lists what leaves each store. Change a quantity only if a store gave more or less; more "
    "than 5 % over the plan needs a reason.",
    "<b>Start: materials out</b>: stock goes down in each store at its real cost. Refused while anything is short.",
]) + [P("Finish", "h2"), fields([
    ("Good / Damaged", "Yes", "What came out.", "1,980 / 20",
     "Good below 90 % or above 105 % of the plan needs a reason. 0 good = a failed production (reason; its cost is a loss)."),
    ("Into which store", "Yes", "One store, or split.", "Main 1,950 + Depot 30", "Must add up to Good."),
    ("Back to the store / Wasted / Why", "No", "Leftover material.", "Pulp 5 back; sugar 1 wasted, spilled",
     "Back: into the store and batch it came from, at the cost used. Wasted needs a reason."),
    ("Other costs", "No", "Filled from the cost of one batch x batches; correct them.", "Line worker 900, Gas 240",
     "Added to the materials actually used."),
    ("Batch number, Date, Why", "Some", "Batch empty: one is made.", "", ""),
]), Spacer(1, 4), box("The result", [
    "Cost per unit = (materials used + other costs) / good; damaged carry no cost of their own. The new batch is "
    "valued at it; the page shows it against the expected cost.",
    "<b>Reverse</b> (a reason) while nothing made was sold, moved or used: everything goes back. <b>Return the "
    "materials and cancel</b> after Start. <b>Quality check</b> when the product needs one.",
])]

# ------------------------------------------------------------------ stock, move, sell
story += section(5, "Stock", "Inventory &rarr; Stock (flow bar 5)",
                 "Every item in stock: how much (also in cartons), in which stores, stock to keep, value and status; and "
                 "the batches that expire first (within the warning days of Inventory settings). <b>By store, bin and "
                 "batch</b> opens the detailed page.")
story += section(6, "Move stock", "Inventory &rarr; Move stock (flow bar 6) &rarr; Move stock",
                 "Stock sent from one store to another.", "the stores.")
story += steps([
    "From store, To store (never the same), date.",
    "Each line: item, quantity, unit, and the batch if you want a given one (else the one that expires first). "
    "\"In the store\" shows what the From store has.",
    "<b>Send it</b>: the stock leaves and waits <b>in transit</b>.",
    "When it arrives, the other store presses <b>Receive</b> on the Move stock list.",
])
story += section(7, "Sell", "Inventory &rarr; Sell (flow bar 7) &rarr; New sale",
                 "A sale to a customer, delivered at once from one store, and invoiced when asked.",
                 "customers (and their price list, if any), stock in the store.")
story += steps([
    "Customer, From store, date. Tick <b>Make the invoice now</b> if you invoice.",
    "Each line: product, quantity, unit (packet or carton). Price: leave it empty for the customer's price list (else "
    "the carton's own price, else the product's price) - the product's price is shown as a hint.",
    "<b>Sell and deliver</b>: the sale is confirmed and delivered, the batch that expires first going first. Over the "
    "credit limit: say why in the box. More than the store has: refused, nothing saved.",
    "Someone who may not confirm their own sales leaves it saved for whoever confirms.",
])

# ------------------------------------------------------------------ warranty
story += section(8, "Warranty", "Inventory &rarr; Warranty (only when switched on)",
                 "Items with a warranty, their serial numbers and claims.",
                 "Inventory settings &rarr; Warranty on; Warranty (months) on the items.")
story += steps([
    "<b>Serial numbers</b>: made by themselves when a production of an item with serial numbers finishes "
    "(Print serial labels on the production); <b>Serial numbers in</b> for units you received.",
    "<b>Serial numbers out</b>: choose the delivery and scan each unit as it leaves - the warranty runs from that day "
    "for that customer.",
    "<b>New claim</b>: scan the serial - the form says <i>covered until ...</i> or <i>out of warranty since ...</i>. "
    "Without a serial: the item and when it was delivered.",
    "What you do: replace, repair (its cost), refund (the amount) or refuse (why). Covering one out of warranty needs a "
    "reason. The faulty unit can go into the rejected store; a new unit leaves a store.",
])
story += [box("Reports", [
    "Inventory &rarr; Reports: <b>Production cost</b> (cost per unit against expected), <b>Material use</b>, "
    "<b>Waste and failed productions</b>, <b>Shortages</b>, plus stock on hand, valuation, batch and expiry, reorder, "
    "purchase and sales registers. Each with filters, Excel and PDF.",
])]

# ------------------------------------------------------------------ questions
story += [CondPageBreak(80 * mm), Spacer(1, 6 * mm), P("Questions people ask", "h1"), grid(("When", "What happens"), [
    ("A material is in two stores of the branch", "Production takes from both: the usual store first."),
    ("A material is only in another branch", "It shows as short: move it first (Move stock)."),
    ("Two plans need the same stock", "Both are warned; the first to start takes it."),
    ("A batch expires before Start", "It is not taken; the next batch is."),
    ("A batch waits for its quality check", "It is never used or sold until it passes."),
    ("Nothing good came out", "Finish with 0 good and a reason: a failed production, its cost a loss."),
    ("A mistake after Finish", "Reverse it (a reason) while nothing made was touched; else a stock adjustment."),
    ("The rate of a worker changes", "Change it on Cost rates: future productions use it, finished ones keep theirs."),
    ("Delete an item", "Only while it has never moved and is in no recipe; else Deactivate."),
    ("Someone from another branch", "Sees and works only in their branches' stores."),
    ("No \"See costs\"", "Quantities only: no money on any screen."),
], (60, 110))]


def build():
    doc = SimpleDocTemplate(OUT, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm,
                            bottomMargin=18 * mm, title="Products and stock - step by step", author="IGL Web Ltd.",
                            subject="Materials, products, recipe and cost, stock in, make, stock, move, sell, warranty")
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)


if __name__ == "__main__":
    build()
    print("written", OUT)
