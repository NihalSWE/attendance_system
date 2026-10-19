"""Store & Inventory, Part 1 - a step-by-step tutorial PDF (2026-10-19).

    venv/Scripts/python docs/scripts/build_inventory_tutorial.py

writes docs/inventory/INVENTORY-PART1-TUTORIAL.pdf: the 10 Inventory pages shown in
the sidebar today, every field with what to write and what it does in the
software (the "what it does" texts are in inventory_tutorial_effects.py, checked
against the code). Rebuild it when those pages or their rules change.
"""

import pathlib
import sys

from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    CondPageBreak, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
    TableStyle,
)

OUT = sys.argv[1] if len(sys.argv) > 1 else str(
    pathlib.Path(__file__).resolve().parents[1] / "inventory" / "INVENTORY-PART1-TUTORIAL.pdf")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
INK = colors.HexColor("#17171a")
MUTED = colors.HexColor("#6f6b63")
ACCENT = colors.HexColor("#2f5d7c")
ACCENT_SOFT = colors.HexColor("#e8f0f6")
PAPER = colors.HexColor("#f5f1ea")
LINE = colors.HexColor("#ded8cc")
OK_SOFT = colors.HexColor("#e9f3e9")
OK = colors.HexColor("#2f6b34")
WARN_SOFT = colors.HexColor("#f8f0dd")
WARN = colors.HexColor("#7d5510")
BAD_SOFT = colors.HexColor("#fbeae7")
BAD = colors.HexColor("#a32a1e")

S = {
    "title": ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=30, leading=36,
                            textColor=INK, spaceAfter=10),
    "subtitle": ParagraphStyle("subtitle", fontName="Helvetica", fontSize=14, leading=20,
                               textColor=MUTED, spaceAfter=6),
    "h1": ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=20, leading=25, textColor=INK,
                         spaceBefore=4, spaceAfter=8),
    "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=13.5, leading=18,
                         textColor=ACCENT, spaceBefore=10, spaceAfter=5),
    "h3": ParagraphStyle("h3", fontName="Helvetica-Bold", fontSize=11, leading=15, textColor=INK,
                         spaceBefore=6, spaceAfter=3),
    "body": ParagraphStyle("body", fontName="Helvetica", fontSize=10, leading=14.5,
                           textColor=INK, spaceAfter=5),
    "small": ParagraphStyle("small", fontName="Helvetica", fontSize=8.6, leading=11.5,
                            textColor=MUTED),
    "cell": ParagraphStyle("cell", fontName="Helvetica", fontSize=8.8, leading=11.6, textColor=INK),
    "cellb": ParagraphStyle("cellb", fontName="Helvetica-Bold", fontSize=8.8, leading=11.6,
                            textColor=INK),
    "head": ParagraphStyle("head", fontName="Helvetica-Bold", fontSize=8.4, leading=11,
                           textColor=colors.white),
    "step": ParagraphStyle("step", fontName="Helvetica", fontSize=10, leading=14.5, textColor=INK,
                           leftIndent=16, firstLineIndent=-16, spaceAfter=3),
    "bullet": ParagraphStyle("bullet", fontName="Helvetica", fontSize=10, leading=14.2,
                             textColor=INK, leftIndent=12, firstLineIndent=-9, spaceAfter=2),
    "toc": ParagraphStyle("toc", fontName="Helvetica", fontSize=10.5, leading=17, textColor=INK),
    "center": ParagraphStyle("center", fontName="Helvetica", fontSize=9, leading=12,
                             textColor=MUTED, alignment=TA_CENTER),
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
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("LINEBEFORE", (0, 0), (0, -1), 3, fg),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return KeepTogether([Spacer(1, 3), table, Spacer(1, 6)])


def grid(head, rows, widths, bold_first=True):
    data = [[Paragraph(h, S["head"]) for h in head]]
    for row in rows:
        data.append([Paragraph(str(c), S["cellb"] if (i == 0 and bold_first) else S["cell"])
                     for i, c in enumerate(row)])
    table = Table(data, colWidths=[w * mm for w in widths], repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("BOX", (0, 0), (-1, -1), 0.6, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for r in range(1, len(data)):
        if r % 2 == 0:
            style.append(("BACKGROUND", (0, r), (-1, r), PAPER))
    table.setStyle(TableStyle(style))
    return table


from inventory_tutorial_effects import EFFECTS

MUST = {"Yes": ("Required", BAD), "No": ("Optional", MUTED), "No (tick)": ("Optional (tick)", MUTED),
        "Sometimes": ("Sometimes required", WARN)}


def fields(key, rows):
    """Field (+ required) | What to write (+ example) | What it does in the software."""
    effects = EFFECTS[key]
    data = [[Paragraph(h, S["head"]) for h in
             ("Field on screen", "What to write", "What it does in the software")]]
    for name, must, write, example in rows:
        label, tone = MUST[must]
        data.append([
            Paragraph(f"<b>{name}</b><br/><font size='7.4' color='{tone.hexval()}'>{label}</font>",
                      S["cell"]),
            Paragraph(write + (f"<br/><font color='{MUTED.hexval()}'>e.g. {example}</font>"
                               if example else ""), S["cell"]),
            Paragraph(effects[name], S["cell"]),
        ])
    table = Table(data, colWidths=[33 * mm, 66 * mm, 71 * mm], repeatRows=1)
    style = [("BACKGROUND", (0, 0), (-1, 0), ACCENT), ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE), ("BOX", (0, 0), (-1, -1), 0.6, LINE),
             ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
             ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
             ("BACKGROUND", (2, 1), (2, -1), ACCENT_SOFT)]
    for r in range(2, len(data), 2):
        style.append(("BACKGROUND", (0, r), (1, r), PAPER))
    table.setStyle(TableStyle(style))
    return table


def where(menu, note=""):
    t = Table([[Paragraph(f"<b>Where:</b>&nbsp; {menu}", S["cell"]),
                Paragraph(note, S["small"])]], colWidths=[100 * mm, 70 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PAPER), ("BOX", (0, 0), (-1, -1), 0.5, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return KeepTogether([t, Spacer(1, 6)])


def section(number, title, menu, what, before, menu_note=""):
    out = [CondPageBreak(90 * mm), Spacer(1, 8 * mm),
           P(f"Step {number} &nbsp;&middot;&nbsp; {title}", "h1"),
           where(menu, menu_note), P(f"<b>What it is:</b> {what}")]
    if before:
        out.append(P(f"<b>You need first:</b> {before}"))
    return out


def flow_diagram():
    d = Drawing(170 * mm, 76 * mm)

    def node(x, y, w, label, sub, fill=ACCENT_SOFT, stroke=ACCENT):
        d.add(Rect(x, y, w, 15 * mm, rx=6, ry=6, fillColor=fill, strokeColor=stroke,
                   strokeWidth=1))
        d.add(String(x + w / 2, y + 8.5 * mm, label, fontName="Helvetica-Bold", fontSize=8.6,
                     fillColor=INK, textAnchor="middle"))
        d.add(String(x + w / 2, y + 3.6 * mm, sub, fontName="Helvetica", fontSize=7,
                     fillColor=MUTED, textAnchor="middle"))

    def arrow(x1, y1, x2, y2):
        d.add(Line(x1, y1, x2, y2, strokeColor=MUTED, strokeWidth=1))
        import math
        angle = math.atan2(y2 - y1, x2 - x1)
        a = 2.4 * mm
        d.add(Polygon([x2, y2, x2 - a * math.cos(angle - 0.4), y2 - a * math.sin(angle - 0.4),
                       x2 - a * math.cos(angle + 0.4), y2 - a * math.sin(angle + 0.4)],
                      fillColor=MUTED, strokeColor=MUTED))

    w, gap = 31 * mm, 3.75 * mm
    top = 58 * mm
    labels = [("1 Settings", "rules, once"), ("2 Units", "KG, PCS, BAG"),
              ("3 Categories", "folders"), ("4 Stores", "rooms"), ("5 Bins", "shelves")]
    xs = [i * (w + gap) for i in range(5)]
    for x, (a, b) in zip(xs, labels):
        node(x, top, w, a, b)
    for i in range(4):
        arrow(xs[i] + w, top + 7.5 * mm, xs[i + 1], top + 7.5 * mm)
    mid = 31 * mm
    node(xs[2], mid, w, "6 Items", "+ pack units", fill=OK_SOFT, stroke=OK)
    arrow(xs[2] + w / 2, top, xs[2] + w / 2, mid + 15 * mm)
    node(xs[0], mid, w, "7 Suppliers", "who we buy from")
    node(xs[4], mid, w, "8 Customers", "who we sell to")
    bottom = 4 * mm
    node(xs[1] + (w + gap) / 2, bottom, w * 1.35, "9 Opening stock", "count -> draft -> Post",
         fill=WARN_SOFT, stroke=WARN)
    node(xs[3] + (w + gap) / 2 - 4 * mm, bottom, w * 1.2, "10 Stock on hand", "the result",
         fill=OK_SOFT, stroke=OK)
    ox = xs[1] + (w + gap) / 2
    arrow(xs[2] + w / 2, mid, ox + w * 0.67, bottom + 15 * mm)
    arrow(ox + w * 1.35, bottom + 7.5 * mm, xs[3] + (w + gap) / 2 - 4 * mm, bottom + 7.5 * mm)
    return d


def on_page(canvas, doc):
    canvas.saveState()
    if doc.page > 1:
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(20 * mm, 287 * mm, "Store & Inventory  |  Part 1: Setting up and opening stock")
        canvas.drawRightString(190 * mm, 287 * mm, "Workforce (workforce.iglweb.com)")
        canvas.setStrokeColor(LINE)
        canvas.line(20 * mm, 285 * mm, 190 * mm, 285 * mm)
        canvas.drawCentredString(105 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


story = []

# ---------------------------------------------------------------- cover
story += [Spacer(1, 40 * mm),
          P("Store &amp; Inventory", "title"),
          P("Part 1: Setting up your lists, entering opening stock, and seeing stock on hand",
            "subtitle"),
          Spacer(1, 8 * mm),
          P("A step-by-step guide for store managers, store keepers, accounts and administrators. "
            "Read it from top to bottom the first time; after that, use each step as a "
            "checklist while you enter data."),
          Spacer(1, 10 * mm),
          grid(("Page in the Inventory menu", "What it is for"), [
              ("Inventory settings", "The rules of the house (once)"),
              ("Units", "How things are measured"),
              ("Item categories", "Folders to group items"),
              ("Stores", "The rooms where stock is kept"),
              ("Bins", "Shelves inside a store (optional)"),
              ("Items", "Every single thing you keep"),
              ("Suppliers", "Who you buy from"),
              ("Customers", "Who you sell to"),
              ("Opening stock", "The count on day one"),
              ("Stock on hand", "What you have, where, and its value"),
          ], (55, 115)),
          Spacer(1, 20 * mm),
          P("Version: 19 October 2026. Example company in this guide: a juice factory "
            "(sugar, mango pulp, bottles).", "small")]
story.append(PageBreak())

# ---------------------------------------------------------------- contents
story += [P("What is inside", "h1")]
toc = [
    "How to use this guide",
    "The big picture: the idea and the order",
    "Before you start: switch it on, give permissions, prepare your lists",
    "Buttons you will see on every page",
    "Step 1 &middot; Inventory settings",
    "Step 2 &middot; Units",
    "Step 3 &middot; Item categories",
    "Step 4 &middot; Stores",
    "Step 5 &middot; Bins",
    "Step 6 &middot; Items (and pack units)",
    "Step 7 &middot; Suppliers",
    "Step 8 &middot; Customers",
    "Faster: filling lists from Excel",
    "Step 9 &middot; Opening stock (the count on day one)",
    "Step 10 &middot; Stock on hand",
    "A full worked example from start to finish",
    "Messages you may see, and what to do",
    "Checklist",
    "Words used in this guide",
]
story += [P(f"{i}.&nbsp;&nbsp;{t}", "toc") for i, t in enumerate(toc, 1)]
story.append(Spacer(1, 6 * mm))
story.append(box("How to use this guide", [
    "Each step has: <b>Where</b> to find the page, <b>what it is</b>, what you <b>need "
    "first</b>, <b>what to click</b>, and a table of <b>every field</b>: what to write, an "
    "example, and <b>what the field does in the software</b> (the blue column).",
    "<b>Must fill?</b> &nbsp;<b>Yes</b> = the form will not save without it. &nbsp;"
    "<b>No</b> = optional. &nbsp;<b>Sometimes</b> = needed only in the case explained.",
    "Follow the steps <b>in order</b>. Each page uses what the pages before it created.",
]))
story.append(PageBreak())

# ---------------------------------------------------------------- big picture
story += [P("The big picture", "h1"),
          P("<b>The idea in one sentence:</b> first you tell the software <b>what exists</b> "
            "(units, groups, stores, shelves, items, suppliers, customers); then you <b>count "
            "what is in each store on day one</b> and enter that count; from then on the "
            "software shows <b>exactly what you have and where</b>."),
          P("Think of opening a new shop's stock book: you write the headings, you count "
            "everything on opening day, and from then on you read the book to know what is "
            "there."),
          P("The order", "h2"),
          flow_diagram(),
          P("Each page needs the ones above it: you cannot add an item without a unit and a "
            "category, and you cannot count stock without a store and an item.", "small"),
          Spacer(1, 4),
          grid(("Step", "Page", "Why it comes here"), [
              ("1", "Inventory settings", "General rules; set once before anything moves."),
              ("2", "Units", "Items are counted in a unit, so units come before items."),
              ("3", "Item categories", "Every item belongs to a category."),
              ("4", "Stores", "Stock always sits in a store."),
              ("5", "Bins", "Shelves belong to a store (optional)."),
              ("6", "Items", "Uses units, categories and (optionally) a usual store."),
              ("7", "Suppliers", "Needed later for buying; set them up now."),
              ("8", "Customers", "Needed later for selling; set them up now."),
              ("9", "Opening stock", "Uses stores, bins and items. Stock starts here."),
              ("10", "Stock on hand", "Shows the result of everything above."),
          ], (14, 40, 116), bold_first=True)]
story.append(PageBreak())

# ---------------------------------------------------------------- before you start
story += [P("Before you start", "h1"),
          P("1. Switch Store &amp; Inventory on (platform owner)", "h2"),
          *steps([
              "Sign in as the <b>platform owner (root)</b>.",
              "Open <b>Companies</b> &rarr; the company &rarr; <b>Feature access</b>.",
              "Turn on <b>Store &amp; Inventory</b> and save.",
              "Sign in as the company's owner or administrator: the <b>Inventory</b> menu now "
              "appears in the sidebar.",
          ]),
          box("Until this is on", ["The company sees no Inventory menu and no inventory "
                                   "permissions on the Access page."], "warn"),
          P("2. Give people their permissions (company owner)", "h2"),
          P("Open <b>Organisation &rarr; Access</b>, choose each person, and tick what they need. "
            "The owner and company administrator can do everything already."),
          grid(("Permission (as written on the Access page)", "What it allows in this part"), [
              ("View stock and items", "Open all 10 pages and look."),
              ("Set up items, stores, suppliers and customers", "Add and edit steps 2 to 8."),
              ("Enter stock adjustments, counts and opening stock",
               "Create an opening stock draft, add or remove its lines, import it."),
              ("Approve and post adjustments and opening stock",
               "Post the opening stock (this puts it into stock), and reverse it."),
              ("See stock costs and values", "See average cost and value on Stock on hand."),
          ], (75, 95)),
          box("Good practice: two people", [
              "Let one person <b>enter</b> the opening stock and a <b>different person post</b> "
              "it. The software allows the same person if they hold both permissions, so make "
              "it a team rule."]),
          P("3. Prepare your lists (on paper or Excel)", "h2"),
          *bullets([
              "Your <b>stores</b> (rooms) and, if you use them, the <b>shelves</b> in each.",
              "Your <b>items</b>: code, name, the unit you count in, and pack sizes "
              "(1 bag = 50 kg, 1 carton = 24 bottles).",
              "Which items have <b>batch numbers</b>, which <b>expire</b>, and which need a "
              "<b>quality check</b> before use.",
              "Your <b>suppliers</b> and <b>customers</b>.",
              "A <b>count day</b>: the date you will count every store, and who counts which "
              "store.",
          ])]
story.append(PageBreak())

# ---------------------------------------------------------------- common buttons
story += [P("Buttons you will see on every page", "h1"),
          P("Steps 2 to 8 are <b>lists</b>. Every list page works the same way:"),
          grid(("Button / control", "What it does"), [
              ("Add &lt;something&gt;", "Opens a small form to add one new row (for example "
                                       "<b>Add unit</b>, <b>Add item</b>). Fill it and click "
                                       "<b>Add</b>."),
              ("Edit", "On each row. Opens the same form with the row's values; change and "
                       "click <b>Save</b>."),
              ("Activate / Deactivate", "Switches a row off when you no longer use it, or on "
                                        "again. Nothing is ever deleted, so old records stay "
                                        "correct. The software asks you to confirm first."),
              ("Search box, filters", "Find rows quickly. Lists show a count and page through "
                                      "long results."),
              ("Import from Excel", "On Items, Stores, Suppliers, Customers and Opening stock: "
                                    "fill a ready template and upload many rows at once (see "
                                    "\"Faster: filling lists from Excel\")."),
          ], (45, 125)),
          box("Codes", ["Every list has a <b>Code</b>: a short, unique name such as "
                        "<b>KG</b>, <b>RMS</b> or <b>RM-SUGAR</b>. The software turns it into "
                        "CAPITAL letters for you. Keep codes short, without spaces, and never "
                        "reuse one for something else."]),
          box("If a form will not save", ["The reason appears in red under the field that needs "
                                          "fixing. Fix it and save again. Nothing is half-saved."],
              "warn")]
story.append(PageBreak())

# ---------------------------------------------------------------- step 1
story += section(1, "Inventory settings", "Inventory &rarr; Inventory settings",
                 "the general rules for the whole company. You set them once, before any "
                 "stock is entered.", "", "Only the owner or company administrator.")
story += steps(["Open the page.", "Check each field below (most can stay as they are).",
                "Click <b>Save</b>."])
story.append(fields("settings", [
    ("Average cost", "Yes", "<b>One average cost per item for the whole company</b> (usual), "
                            "or one per store if stores really buy at different prices.",
     "Whole company"),
    ("Warn before expiry (days)", "Yes", "How many days before a batch expires the software "
                                         "starts warning.", "30"),
    ("Slow-moving days", "Yes", "An item not used, issued or sold for this many days is "
                                "called slow-moving in reports.", "90"),
    ("Alert emails", "No", "Who gets the morning email about low and expiring stock. One "
                           "email per line. Empty = no email.", "store@company.com"),
    ("Small label", "Yes", "Size of small stickers for the label printer.", "50 &times; 25 mm"),
    ("Large label", "Yes", "Size of carton stickers.", "100 &times; 50 mm"),
    ("GS1 labels", "No (tick)", "A special barcode some big buyers ask for. Leave unticked "
                                "unless a customer asks.", "Unticked"),
]))
story.append(box("Important", ["<b>Average cost cannot be changed once stock has moved.</b> "
                               "Decide it before Step 9. After that the page says: \"Stock has "
                               "moved, so the average cost choice is fixed now.\""], "bad"))

# ---------------------------------------------------------------- step 2
story += section(2, "Units", "Inventory &rarr; Units",
                 "the units you measure and count in: kilogram, litre, piece, bag, carton...",
                 "nothing.")
story += steps([
    "Click <b>Add standard units</b> once. It adds KG, G, L, ML, PCS, BOX, CARTON, BAG, DRUM, "
    "ROLL, M, SET and PAIR. Units you already have are kept.",
    "For anything missing, click <b>Add unit</b>, fill the form, click <b>Add</b>.",
])
story.append(fields("units", [
    ("Code", "Yes", "A short, unique code.", "KG"),
    ("Name", "Yes", "The full name.", "Kilogram"),
    ("Kind", "Yes", "Weight, Volume, Count, Length or Other.", "Weight"),
    ("Decimals", "Yes", "How many digits after the point a quantity may have. "
                        "<b>0</b> = whole numbers only.", "KG: 3 (1.250 kg)<br/>PCS: 0"),
]))
story.append(box("Pack sizes are not set here", [
    "\"1 carton = 24 bottles\" or \"1 bag = 50 kg\" is set on <b>each item</b> (Step 6, "
    "Pack units), because a carton of one item is not a carton of another."]))

# ---------------------------------------------------------------- step 3
story += section(3, "Item categories", "Inventory &rarr; Item categories",
                 "folders to group items, so lists and reports are easy to read. They do not "
                 "change stock or costs.", "nothing.")
story += steps([
    "Click <b>Add category</b>.",
    "Make the <b>main</b> folders first, with Parent category = <b>None (main category)</b>.",
    "Then add <b>sub-folders</b>, choosing their main folder as the Parent category.",
])
story.append(fields("categories", [
    ("Code", "Yes", "A short, unique code.", "SWT"),
    ("Name", "Yes", "The folder's name.", "Sweetener"),
    ("Item type", "Yes", "One of five: Raw material, Packaging, Finished goods, Spare part, "
                         "General store.", "Raw material"),
    ("Parent category", "No", "<b>None (main category)</b> for a main folder; or choose one to "
                              "put this inside it. It must have the <b>same item type</b>.",
     "Sugar &rarr; parent Sweetener"),
]))
story.append(grid(("Example tree", ""), [
    ("Raw material", "Sweetener &rarr; Sugar, Glucose; Fruit &rarr; Mango pulp"),
    ("Packaging", "Bottle &rarr; PET 500 ml; Carton"),
    ("Finished goods", "Juice &rarr; Mango juice 250 ml"),
    ("Spare part", "Bearing; Belt"),
], (45, 125)))
story.append(Spacer(1, 4))
story.append(box("The list page", ["In the list, the column <b>Parent</b> shows each "
                                   "category's parent folder."]))

# ---------------------------------------------------------------- step 4
story += section(4, "Stores", "Inventory &rarr; Stores",
                 "each room or warehouse where stock is kept. Every store belongs to one "
                 "branch.", "the branches (Organisation &rarr; Branches), which already exist.")
story += steps(["Click <b>Add store</b>.", "Fill the form, click <b>Add</b>.",
                "Repeat for every store. Or import them all (see \"Faster: filling lists from "
                "Excel\")."])
story.append(fields("stores", [
    ("Branch", "Yes", "The branch the store is in.", "HC Factory"),
    ("Code", "Yes", "A short, unique code.", "RMS"),
    ("Name", "Yes", "The store's full name.", "Raw Material Store"),
    ("Store type", "Yes", "Raw material, Packaging, Finished goods, Spare parts, General, "
                          "Production floor, Quarantine, In transit, or Rejected.",
     "Raw material"),
    ("Notes", "No", "Anything extra.", "Ground floor, gate 2"),
]))
story.append(box("Tip", ["Make a store for each real room. Typical factory: Raw Material "
                         "Store (RMS), Packaging Store (PKS), Finished Goods Store (FGS), Spare "
                         "Parts Store (SPS), plus a Quarantine store if goods wait for a "
                         "quality check."], "ok"))

# ---------------------------------------------------------------- step 5
story += section(5, "Bins", "Inventory &rarr; Bins",
                 "shelves, racks or bins inside a store, so the software can say exactly "
                 "where something is. <b>Optional</b>: skip this step if you do not track "
                 "shelves.", "the stores (Step 4).")
story += steps(["Click <b>Add bin</b>.", "Choose the store, write the bin code, click "
                "<b>Add</b>."])
story.append(fields("bins", [
    ("Store", "Yes", "The store the shelf is in.", "RMS"),
    ("Bin code", "Yes", "The shelf's address, unique inside that store.",
     "R1-S2-B3 (rack 1, shelf 2, bin 3)"),
    ("Description", "No", "Words that help people find it.", "Left wall, top shelf"),
]))

# ---------------------------------------------------------------- step 6
story += section(6, "Items (and pack units)", "Inventory &rarr; Items",
                 "every single thing you buy, make, store or sell. This is the most "
                 "important list: take your time with it.",
                 "units (Step 2) and categories (Step 3); stores (Step 4) if you want to set "
                 "a usual store.")
story += steps([
    "Click <b>Add item</b>.",
    "Fill the form below and click <b>Add</b>.",
    "If you also buy or count it in packs, open the item (click its row's open button) and "
    "add its <b>pack units</b> (below).",
])
story.append(P("The item form", "h2"))
story.append(fields("items", [
    ("Code", "Yes", "A short, unique code.", "RM-SUGAR"),
    ("Name", "Yes", "The item's name.", "Sugar"),
    ("Name in Bangla", "No", "The Bangla name, if you want it shown.", "(Bangla name)"),
    ("Item type", "Yes", "Raw material, Packaging, Finished goods, Spare part, General store.",
     "Raw material"),
    ("Category", "Yes", "Its folder from Step 3.", "Sugar"),
    ("Base unit", "Yes", "The unit stock is counted in. <b>Cannot change once stock has "
                         "moved.</b>", "KG"),
    ("Barcode", "No", "Only a barcode <b>already printed</b> on the product (EAN-13).", "(empty)"),
    ("Keeps batches", "No (tick)", "Tick if each delivery has a batch or lot number you want to "
                                   "track (food, chemicals).", "Ticked"),
    ("Has an expiry date", "No (tick)", "Tick if it can go bad.", "Ticked"),
    ("Shelf life (days)", "No", "How long it lasts after it is made. Lets the software work "
                                "out the expiry date from the manufacture date.", "365"),
    ("QC before use", "No (tick)", "Tick if new stock must pass a quality check before "
                                   "anyone may use it.", "Ticked"),
    ("Min stock", "No", "Never let stock fall below this.", "500"),
    ("Reorder point", "No", "When stock falls to this, buy more (shown in the morning "
                            "email).", "1000"),
    ("Reorder qty", "No", "How much to buy each time.", "2000"),
    ("Max stock", "No", "Do not keep more than this.", "5000"),
    ("Usual store", "No", "Where it is normally kept.", "RMS"),
    ("Standard cost", "No", "The expected cost of <b>one base unit</b>. A guide for the "
                            "count day.", "120"),
    ("Sale price", "No", "Selling price, mainly for finished goods.", "(empty)"),
    ("VAT rate", "No", "VAT percentage, if it applies.", "(empty)"),
    ("HS code", "No", "Customs code, for imported items.", "(empty)"),
    ("Notes", "No", "Anything extra.", "Supplier packs in 50 kg bags"),
]))
story.append(box("Rules the item form checks", [
    "<b>Has an expiry date</b> needs <b>Keeps batches</b> ticked too (each batch has its own "
    "expiry).",
    "The <b>Category</b> must be of the same <b>Item type</b> as the item.",
    "<b>Min stock</b> and <b>Reorder point</b> cannot be above <b>Max stock</b>.",
    "<b>Base unit</b> and the batch / expiry ticks should be right from the start: once stock "
    "has moved, the base unit is fixed."], "warn"))
story.append(P("Pack units (on the item's own page)", "h2"))
story.append(P("Open the item, then <b>Pack units &rarr; Add pack unit</b>. A pack unit lets "
               "people count in bags or cartons while the software keeps the stock in the "
               "base unit."))
story.append(fields("packs", [
    ("Pack unit", "Yes", "The bigger unit (from Step 2).", "BAG"),
    ("Factor", "Yes", "How many <b>base units</b> are in one pack.", "50 (1 bag = 50 kg)"),
    ("Barcode", "No", "Only if the pack has its own printed barcode.", "(empty)"),
]))
story.append(box("Example", ["Sugar: base unit KG; pack unit BAG with factor 50. On the count "
                             "day you can write <b>10 BAG</b>; the software stores "
                             "<b>500 kg</b>."], "ok"))
story.append(P("What the item page shows", "h3"))
story += bullets(["<b>Details</b>: the values above.",
                  "<b>Pack units</b>: add or remove packs.",
                  "<b>Batches</b>: stock of this item in each store, by batch.",
                  "<b>Stock card</b>: every movement in and out, newest first."])

# ---------------------------------------------------------------- step 7
story += section(7, "Suppliers", "Inventory &rarr; Suppliers",
                 "the companies you buy from. Set them up now; they are used fully when "
                 "buying is switched on later.", "nothing.")
story += steps(["Click <b>Add supplier</b>.", "Fill the form, click <b>Add</b>."])
story.append(fields("suppliers", [
    ("Code", "Yes", "A short, unique code.", "SUP-ABC"),
    ("Name", "Yes", "Company name.", "ABC Sugar Mills Ltd."),
    ("Contact person", "No", "Who you talk to.", "Mr. Rahman"),
    ("Phone", "No", "Their phone.", "01700-000000"),
    ("Email", "No", "Their email.", "sales@abcsugar.com"),
    ("Address", "No", "Their address.", "Tongi, Gazipur"),
    ("BIN", "No", "Their VAT registration number.", "000123456-0101"),
    ("TIN", "No", "Their tax ID.", "123456789012"),
    ("Payment terms (days)", "Yes", "Days they give you to pay. <b>0</b> = cash on delivery.",
     "30"),
    ("Items supplied", "No", "Item codes they sell, separated by commas.", "RM-SUGAR, RM-GLU"),
    ("Notes", "No", "Anything extra.", ""),
]))

# ---------------------------------------------------------------- step 8
story += section(8, "Customers", "Inventory &rarr; Customers",
                 "the people and companies you sell to. Set them up now; they are used fully "
                 "when selling is switched on later.", "nothing.")
story += steps(["Click <b>Add customer</b>.", "Fill the form, click <b>Add</b>."])
story.append(fields("customers", [
    ("Code", "Yes", "A short, unique code.", "CUS-SYL01"),
    ("Name", "Yes", "Customer's name.", "Sylhet Traders"),
    ("Customer type", "Yes", "Distributor, Dealer, Retailer or Corporate.", "Distributor"),
    ("Contact person", "No", "Who you talk to.", "Mr. Karim"),
    ("Phone", "No", "Their phone.", "01800-000000"),
    ("WhatsApp", "No", "Their WhatsApp number (lets the future order assistant recognise "
                       "them).", "01800-000000"),
    ("Email", "No", "Their email.", "orders@sylhettraders.com"),
    ("Address", "No", "Their address.", "Zindabazar, Sylhet"),
    ("Territory", "No", "Their sales area.", "Sylhet"),
    ("Credit limit", "Yes", "The most they may owe. <b>0</b> = no limit is checked.", "200000"),
    ("Payment terms (days)", "Yes", "Days given to pay.", "15"),
    ("Price list name", "No", "Which price list they buy at (used later in Sales).",
     "Distributor"),
    ("Notes", "No", "Anything extra.", ""),
]))

# ---------------------------------------------------------------- import
story += [CondPageBreak(90 * mm), P("Faster: filling lists from Excel", "h1"),
          P("Instead of typing rows one by one, you can fill a ready Excel template and upload "
            "it. Available for <b>Stores (with their bins)</b>, <b>Items (with their pack "
            "units)</b>, <b>Suppliers</b>, <b>Customers</b> and <b>Opening stock</b>."),
          *steps([
              "On the list page, click <b>Import from Excel</b>.",
              "Click <b>Download the template</b>. Fill it in Excel. Keep the column headings "
              "as they are.",
              "Back on the import page: <b>1. Upload</b> the filled file (.xlsx, .xls or "
              ".csv).",
              "<b>2. Preview</b>: every row is shown with its result: <b>Add</b> (will be "
              "added), skipped (already in the software or empty), or refused <b>with the "
              "reason</b>.",
              "Fix any refused rows in the file and upload again, or confirm if all is well.",
              "<b>Confirm</b>. Only now are the rows written.",
          ]),
          box("Safe to try", ["<b>Nothing is written until you confirm.</b> Uploading and "
                              "previewing changes nothing, so you can upload as often as you "
                              "need."], "ok"),
          grid(("Template", "What it fills", "Fill it after"), [
              ("01-stores.xlsx", "Stores and their bins", "Branches exist"),
              ("02-items.xlsx", "Items and their pack units", "Units and categories"),
              ("03-suppliers.xlsx", "Suppliers", "-"),
              ("04-customers.xlsx", "Customers", "-"),
              ("06-opening-stock.xlsx", "Opening stock lines (as a draft)", "Stores and items"),
          ], (45, 70, 55))]

# ---------------------------------------------------------------- step 9
story.append(PageBreak())
story += section(9, "Opening stock (the count on day one)", "Inventory &rarr; Opening stock",
                 "how the real stock gets into the software. <b>Until an opening stock is "
                 "posted, every item has zero stock.</b>",
                 "stores (Step 4), items (Step 6), and bins (Step 5) if you use them.")
story += [P("It happens in three stages", "h2"),
          grid(("Stage", "What it means", "Who"), [
              ("Draft", "Being entered and checked. <b>Nothing is in stock yet.</b> Lines can "
                        "be added, removed or imported.",
               "\"Enter stock adjustments, counts and opening stock\""),
              ("Posted", "Final. The stock now exists, batches are made, each item gets its "
                         "first average cost. <b>Never edited after this.</b>",
               "\"Approve and post adjustments and opening stock\""),
              ("Cancelled / Reversed", "Cancelled = a draft thrown away. Reversed = a posted "
                                       "one undone (with a reason).", "As above"),
          ], (30, 90, 50)),
          P("Stage A: count every store", "h2"),
          *steps([
              "Choose the <b>opening day</b>: the date you count.",
              "Count store by store: the quantity, and for batch items the batch number and "
              "expiry (and manufacture date if known), and the shelf if you use bins.",
              "Write the cost of each item (accounts checks it; the last purchase price is a "
              "good basis).",
          ]),
          P("Stage B: enter it as a draft", "h2"),
          P("<b>Way 1, by hand:</b> click <b>New opening stock</b>, fill the header, then add "
            "one line per item, per store, per batch with <b>Add line</b>."),
          P("<b>Way 2, from Excel:</b> click <b>Import from Excel</b>, give the <b>Opening "
            "day</b>, upload the filled opening stock template, check the preview, confirm. The "
            "rows become the lines of a <b>new draft</b>."),
          P("The header", "h3"),
          fields("opening_header", [
              ("Opening day", "Yes", "The date you counted. All the stock is dated this day.",
               "01 Nov 2026"),
              ("Note", "No", "Anything extra.", "Full count, all stores"),
          ]),
          P("Each line (Add line)", "h3"),
          fields("opening_line", [
              ("Store", "Yes", "Where it was counted.", "RMS"),
              ("Bin", "No", "The shelf, if you use bins. It must be in that store.", "R1-S2-B3"),
              ("Item", "Yes", "What was counted.", "RM-SUGAR"),
              ("Batch no.", "Sometimes", "<b>Required</b> if the item keeps batches. <b>Must "
                                         "be empty</b> if it does not.", "SUG-2610"),
              ("Supplier batch no.", "No", "The supplier's own batch number, if any.", ""),
              ("Manufacture date", "No", "When it was made. Not after the opening day.",
               "01 Oct 2026"),
              ("Expiry date", "Sometimes", "<b>Required</b> if the item has an expiry date. "
                                           "Leave empty to have it worked out from the shelf "
                                           "life. Not before the manufacture date.",
               "01 Oct 2027"),
              ("Quantity", "Yes", "How many were counted (more than 0).", "10"),
              ("Unit", "Yes", "The item's base unit, <b>or one of its pack units</b>.", "BAG"),
              ("Unit cost", "Yes", "The cost of <b>one</b> of the unit you chose.",
               "6000 (one bag)"),
              ("Note", "No", "Anything extra.", ""),
          ]),
          box("How the software counts it", [
              "10 BAG at 6,000 a bag, with 1 BAG = 50 KG, becomes <b>500 kg at 120 a kg</b> "
              "(value 60,000). The line shows both: the quantity you wrote and the quantity "
              "<b>In base unit</b>."], "ok"),
          P("Stage C: check, then post", "h2"),
          *steps([
              "Open the draft. Check every line: store, item, batch, expiry, quantity, cost, "
              "and the total value.",
              "Wrong line? Click <b>Remove</b> on it and add it again correctly.",
              "When everything is right, the person who posts clicks <b>Post</b> and confirms.",
              "After posting, <b>Print all labels</b> gives one label per batch (or per item "
              "without batches) to stick on the stock.",
          ]),
          box("After posting", [
              "A posted opening stock is <b>never edited</b>. If something was wrong, click "
              "<b>Reverse</b>, write why, then enter and post a correct one.",
              "<b>Reverse is refused</b> if some of that stock has already been used. In that "
              "case the correction is made with a stock adjustment (a later part of the "
              "module)."], "warn"),
          box("Other buttons", [
              "<b>Cancel draft</b>: throw away a draft that will not be posted (it stays listed "
              "as cancelled)."])]

# ---------------------------------------------------------------- step 10
story += section(10, "Stock on hand", "Inventory &rarr; Stock on hand",
                 "the answer to <b>what do we have, where, and what is it worth</b>.",
                 "a posted opening stock (Step 9).")
story += [grid(("Column", "Meaning"), [
              ("Code, Item", "The item."),
              ("Store, Bin", "Where it is."),
              ("Batch, Expiry", "Which batch, and when it expires."),
              ("On hand", "How much is physically there, in the base unit."),
              ("Reserved", "Kept aside for a sales order (used in a later part)."),
              ("Available", "On hand minus reserved: what can be used now."),
              ("Average cost, Value", "Cost per base unit, and on hand &times; cost. Shown only to "
                                      "people with \"See stock costs and values\"."),
          ], (45, 125)),
          Spacer(1, 4),
          P("<b>Filters:</b> choose a <b>store</b> or an <b>item type</b> to narrow the list. "
            "Click an item to open its page and see its <b>stock card</b> (every movement)."),
          box("Check after posting", ["Compare Stock on hand, store by store, with your count "
                                      "sheets. They must match exactly."], "ok")]

# ---------------------------------------------------------------- worked example
story.append(PageBreak())
story += [P("A full worked example", "h1"),
          P("A juice factory sets up three items and enters its count of 1 November 2026."),
          P("1. Settings and lists", "h2"),
          grid(("Page", "What they entered"), [
              ("Inventory settings", "Average cost: whole company. Warn before expiry: 30. "
                                     "Alert emails: store@company.com"),
              ("Units", "Clicked Add standard units (KG, PCS, BAG, CARTON, DRUM ...)."),
              ("Item categories", "SWT Sweetener (Raw material, main); SUG Sugar (parent "
                                  "Sweetener); FRT Fruit (Raw material); BTL Bottle "
                                  "(Packaging)."),
              ("Stores", "RMS Raw Material Store (Raw material) and PKS Packaging Store "
                         "(Packaging), both in HC Factory."),
              ("Bins", "RMS: R1-S1, R1-S2."),
              ("Items", "RM-SUGAR Sugar: base KG, keeps batches, has expiry, shelf life 365, "
                        "pack BAG = 50.<br/>RM-MANGO Mango pulp: base KG, keeps batches, has "
                        "expiry, QC before use, pack DRUM = 220.<br/>PK-BTL500 PET bottle 500 ml: "
                        "base PCS, no batches, pack CARTON = 200."),
              ("Suppliers", "SUP-ABC ABC Sugar Mills, terms 30 days."),
              ("Customers", "CUS-SYL01 Sylhet Traders, Distributor, credit limit 200,000."),
          ], (40, 130)),
          P("2. The count (opening stock draft, opening day 01 Nov 2026)", "h2"),
          grid(("Store / Bin", "Item", "Batch", "Expiry", "Qty + Unit", "Unit cost",
                "In base unit"), [
              ("RMS / R1-S1", "RM-SUGAR", "SUG-2610", "empty; made 01 Oct 2026, so 01 Oct 2027 "
               "from the shelf life", "10 BAG", "6,000",
               "500 kg at 120"),
              ("RMS / R1-S2", "RM-MANGO", "MP-0915", "15 Sep 2027", "8 DRUM", "46,200",
               "1,760 kg at 210"),
              ("PKS / -", "PK-BTL500", "(empty)", "(empty)", "12 CARTON", "1,600",
               "2,400 pcs at 8"),
          ], (22, 21, 19, 34, 21, 17, 36)),
          P("Sugar: the expiry was left empty and the manufacture date given; with a shelf life "
            "of 365 days the software filled in 01 Oct 2027. Mango pulp has no shelf life set, "
            "so its expiry was written. Bottles keep no batches, so batch and expiry stay "
            "empty.", "small"),
          P("3. Post and check", "h2"),
          *steps([
              "The store keeper entered the lines; the store manager checked them and clicked "
              "<b>Post</b>.",
              "Stock on hand now shows: Sugar 500 kg (value 60,000), Mango pulp 1,760 kg "
              "(value 369,600), Bottles 2,400 pcs (value 19,200).",
              "They printed all labels from the posted opening stock and stuck them on.",
          ])]

# ---------------------------------------------------------------- messages
story.append(PageBreak())
story += [P("Messages you may see, and what to do", "h1"),
          grid(("The message says", "Why", "What to do"), [
              ("... keeps batches: give the batch number.", "The item is set to keep batches.",
               "Write the batch number on the line."),
              ("... does not keep batches; leave it empty.", "A batch was written for an item "
                                                              "without batches.",
               "Clear the Batch no. field."),
              ("... has an expiry date: give it.", "The item expires and no expiry (or shelf "
                                                   "life) was given.",
               "Write the expiry date, or the manufacture date if the item has a shelf life."),
              ("The expiry date is before the manufacture date.", "Dates swapped or mistyped.",
               "Correct the dates."),
              ("Made after the opening day?", "Manufacture date is later than the opening day.",
               "Correct the manufacture date or the opening day."),
              ("Bin ... is not in ....", "The shelf belongs to another store.",
               "Choose a bin of that store, or the right store."),
              ("... is counted in KG or its pack units.", "The unit chosen is not the item's "
                                                          "base unit or one of its packs.",
               "Choose the base unit, or add that pack unit to the item first (Step 6)."),
              ("... is counted in whole PCS.", "A part of a piece was written (2.5 PCS).",
               "Write a whole number."),
              ("... takes at most 3 digits after the point in KG.", "Too many decimals.",
               "Round the quantity."),
              ("Give a quantity above zero.", "Quantity is 0 or empty.",
               "Write the counted quantity. Skip items that were not there."),
              ("... is inactive. / The store ... is inactive.", "The item or store was "
                                                                "deactivated.",
               "Activate it on its list page, or choose another."),
              ("Add the counted stock before posting.", "Post was clicked on an empty draft.",
               "Add or import the lines first."),
              ("Say why it is being reversed.", "Reverse needs a reason.",
               "Write the reason."),
              ("An item with an expiry date must also keep batches.", "Item form: expiry "
               "ticked without batches.", "Tick Keeps batches as well."),
              ("This category is for another item type.", "Item form: the category is of a "
               "different item type.", "Choose a category of the item's type, or change the "
               "item type."),
              ("... is above the maximum.", "Item form: Min stock or Reorder point is bigger "
               "than Max stock.", "Lower it, or raise Max stock."),
              ("The parent category is for another item type.", "Category form: the parent has a "
               "different item type.", "Choose a parent of the same type, or None."),
              ("That is the item's base unit already.", "Pack unit: the base unit was chosen as a "
               "pack.", "Choose a bigger unit (BAG, CARTON ...)."),
              ("Stock has moved, so the average cost choice is fixed now.",
               "Settings: stock already exists.", "Nothing to do; the choice stays."),
          ], (58, 52, 60))]

# ---------------------------------------------------------------- checklist
story.append(PageBreak())
story += [P("Checklist", "h1"),
          P("Tick each line as you go.")]
check = [
    "Store &amp; Inventory switched on for the company (platform owner).",
    "Permissions given on Organisation &rarr; Access (two different people for enter and "
    "post).",
    "Inventory settings checked and saved; average cost decided.",
    "Units: Add standard units clicked; any missing units added.",
    "Item categories: main folders, then sub-folders.",
    "Stores: every room, each in its branch.",
    "Bins: added (only if you track shelves).",
    "Items: every item, with the right base unit, batch / expiry / QC ticks.",
    "Pack units added on items bought or counted in packs.",
    "Suppliers and customers added (or imported).",
    "Count day done; every store counted.",
    "Opening stock draft entered or imported; every line checked.",
    "Opening stock posted by a second person.",
    "Labels printed and stuck on.",
    "Stock on hand matches the count sheets, store by store.",
]
rows = [[Paragraph("&#9744;", ParagraphStyle("cb", fontSize=13, leading=14)), P(c, "cell")]
        for c in check]
t = Table(rows, colWidths=[10 * mm, 160 * mm])
t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                       ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
                       ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
story.append(t)

# ---------------------------------------------------------------- glossary
story += [Spacer(1, 8 * mm), P("Words used in this guide", "h1"),
          grid(("Word", "Meaning"), [
              ("Base unit", "The unit an item's stock is always kept in (KG for sugar, PCS for "
                            "bottles)."),
              ("Pack unit", "A bigger unit for the same item, with a factor (1 BAG = 50 KG)."),
              ("Batch", "A group of stock made or delivered together, with its own number and "
                        "expiry date."),
              ("Bin", "A shelf, rack or place inside a store."),
              ("Draft", "Being entered; nothing is in stock yet; can be changed."),
              ("Post", "Make it final: the stock really changes. A posted document is never "
                       "edited."),
              ("Reverse", "Undo a posted document with a reason, by moving the stock back. "
                          "Then post a correct one."),
              ("Average cost", "The cost of one base unit, worked out from what the stock cost."),
              ("Value", "On hand quantity &times; average cost."),
              ("Active / Inactive", "Inactive rows are kept for history but cannot be chosen "
                                    "for new work."),
          ], (35, 135))]


def build():
    doc = SimpleDocTemplate(OUT, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=18 * mm, bottomMargin=18 * mm,
                            title="Store & Inventory - Part 1 tutorial",
                            author="IGL Web Ltd.", subject="Setting up and opening stock")
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)


build()
print("written", OUT)
