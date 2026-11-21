"""Build the Inventory operator manual: python docs/scripts/build_inventory_workflow.py."""

from pathlib import Path

from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import CondPageBreak, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


OUT = Path(__file__).resolve().parents[1] / "inventory" / "INVENTORY-WORKFLOW.pdf"
INK = colors.HexColor("#182b40")
BLUE = colors.HexColor("#155391")
MUTED = colors.HexColor("#485c70")
LINE = colors.HexColor("#d7e1eb")
STYLES = {
    "title": ParagraphStyle("Title", fontName="Helvetica-Bold", fontSize=29, leading=34, textColor=INK, spaceAfter=7),
    "heading": ParagraphStyle("Heading", fontName="Helvetica-Bold", fontSize=15, leading=19,
                              textColor=INK, spaceBefore=10, spaceAfter=3),
    "body": ParagraphStyle("Body", fontSize=11.8, leading=16.5, textColor=INK, spaceAfter=6),
    "path": ParagraphStyle("Path", fontName="Helvetica-Bold", fontSize=10.7, leading=14.3,
                           textColor=BLUE, spaceAfter=4),
    "role": ParagraphStyle("Role", fontSize=10.6, leading=14, textColor=MUTED, spaceAfter=4),
    "check": ParagraphStyle("Check", fontSize=11.3, leading=15.8, textColor=INK, backColor=colors.HexColor("#eaf3fb"),
                            borderPadding=7, spaceBefore=6, spaceAfter=6),
    "example": ParagraphStyle("Example", fontSize=11.3, leading=15.8, textColor=INK,
                              backColor=colors.HexColor("#eaf3e9"), borderPadding=7,
                              spaceBefore=9, spaceAfter=7),
}


def para(value, style="body"):
    return Paragraph(value, STYLES[style])


def field_table(rows, headings=("Field name", "What to enter")):
    header = ParagraphStyle("Table header", parent=STYLES["body"], fontName="Helvetica-Bold",
                            fontSize=11, leading=14, textColor=colors.white)
    cell = ParagraphStyle("Table cell", parent=STYLES["body"], fontSize=11, leading=14.4)
    label = ParagraphStyle("Field label", parent=cell, fontName="Helvetica-Bold")
    data = [[Paragraph(heading, header) for heading in headings]]
    data += [[Paragraph(name, label), Paragraph(explanation, cell)] for name, explanation in rows]
    table = Table(data, colWidths=[53 * mm, 117 * mm], repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BLUE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f1ea")]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return table


def form(story, title, path, rows, *, who="", use="", headings=("Field name", "What to enter")):
    table = field_table(rows, headings)
    lead = [para(title, "heading"), para(path, "path")]
    if who:
        lead.append(para(f"<b>Who:</b> {who}<br/><b>Use it to:</b> {use}", "role"))
    story.append(CondPageBreak(70))
    story.extend([*lead, table])


def link(key, title):
    return f'<link href="#{key}" color="#155391"><u>{title}</u></link>'


def steps_diagram(steps):
    drawing = Drawing(480, 49)
    gap = 17
    width = (480 - gap * (len(steps) - 1)) / len(steps)
    for i, (title, result) in enumerate(steps):
        x = i * (width + gap)
        fill = colors.HexColor("#e5f3ea" if i == len(steps) - 1 else "#edf5fb")
        drawing.add(Rect(x, 5, width, 37, rx=5, ry=5, fillColor=fill, strokeColor=LINE))
        drawing.add(String(x + width / 2, 28, title, fontName="Helvetica-Bold", fontSize=11.2,
                           fillColor=INK, textAnchor="middle"))
        drawing.add(String(x + width / 2, 15, result, fontName="Helvetica", fontSize=8,
                           fillColor=MUTED, textAnchor="middle"))
        if i < len(steps) - 1:
            start = x + width + 2
            drawing.add(Line(start, 23, start + 9, 23, strokeColor=BLUE))
            drawing.add(Polygon([start+8, 20, start+8, 26, start+12, 23], fillColor=BLUE, strokeColor=None))
    return drawing


def section(story, key, title, intro, steps):
    story.extend([PageBreak(), para(f'<a name="{key}"/>{title}', "title"),
                  steps_diagram(steps), para(intro)])


def workflow():
    width, height = 480, 302
    drawing = Drawing(width, height)

    def node(x, y, w, title, subtitle):
        drawing.add(Rect(x, y, w, 34, rx=5, ry=5, fillColor=colors.HexColor("#edf5fb"), strokeColor=LINE))
        drawing.add(String(x + w / 2, y + 20, title, fontName="Helvetica-Bold",
                           fontSize=11.5, fillColor=INK, textAnchor="middle"))
        drawing.add(String(x + w / 2, y + 8, subtitle, fontName="Helvetica",
                           fontSize=9.4, fillColor=MUTED, textAnchor="middle"))

    def line(x1, y1, x2, y2):
        drawing.add(Line(x1, y1, x2, y2, strokeColor=BLUE, strokeWidth=1.15))

    def down(x, y1, y2):
        line(x, y1, x, y2 + 4)
        drawing.add(Polygon([x-3.5, y2+5, x+3.5, y2+5, x, y2], fillColor=BLUE, strokeColor=None))

    node(114, 266, 252, "1. Set up Inventory", "Stores, units, categories, suppliers and customers")
    down(240, 266, 250)
    node(114, 216, 252, "2. Add the items", "Add products and any raw goods you use")
    line(240, 216, 240, 209)
    line(115, 209, 365, 209)
    down(115, 209, 199)
    down(365, 209, 199)
    node(3, 165, 224, "3A. Buy and resell", "Receive the product in Stock in")
    node(253, 165, 224, "3B. Make products", "Receive raw goods, add a recipe, then Make")
    line(115, 165, 115, 157)
    line(365, 165, 365, 157)
    line(115, 157, 365, 157)
    down(240, 157, 146)
    node(114, 112, 252, "4. Check available stock", "Pass quality checks if required; move stock if needed")
    down(240, 112, 96)
    node(114, 62, 252, "5. Set the price, sell and deliver", "Invoice and record serial numbers where required")
    down(240, 62, 46)
    node(101, 12, 278, "6. Handle exceptions and check reports", "Returns, warranty claims, stock counts and corrections")
    return drawing


def done(story, text):
    story.append(para(f"<b>After saving:</b> {text}", "check"))


def example(story, title, text):
    story.append(para(f"<b>{title}</b><br/>{text}", "example"))


def footer(canvas, doc):
    width, height = A4
    canvas.saveState()
    canvas.setFillColor(BLUE)
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(19 * mm, height - 12 * mm, "INVENTORY  /  OPERATOR MANUAL")
    canvas.setStrokeColor(LINE)
    canvas.line(19 * mm, height - 15 * mm, width - 19 * mm, height - 15 * mm)
    canvas.line(19 * mm, 14 * mm, width - 19 * mm, 14 * mm)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 9)
    canvas.drawString(19 * mm, 9 * mm, "Inventory menu map")
    canvas.linkRect("", "menu", (19 * mm, 7 * mm, 54 * mm, 13 * mm), relative=0, thickness=0)
    canvas.drawRightString(width - 19 * mm, 9 * mm, str(doc.page))
    canvas.restoreState()


def rows(text):
    """Read short field/explanation pairs without adding markup to the copy."""
    return [tuple(line.strip().split("|", 1)) for line in text.strip().splitlines()]


def build():
    story = [para("Inventory, step by step", "title"),
             para("Choose your path, then find your job below."),
             workflow(), Spacer(1, 8)]

    form(story, "Jump to your job", "In this PDF, click an underlined link.", [
        ("Owner / admin", link("setup", "Set up Inventory") + " or " + link("reports", "review reports")),
        ("Store or quality staff", link("receive", "Receive goods") + ", "
         + link("stock", "move or count stock") + ", or " + link("make", "check batches")),
        ("Factory team", link("raw", "Add raw goods") + " and " + link("make", "plan and make products")),
        ("Purchasing staff", link("buy", "Request, order and receive goods")),
        ("Sales / service staff", link("sell", "Sell and deliver") + " or " + link("warranty", "handle claims")),
    ], headings=("Who are you?", "Where to start"))
    story.append(para(link("menu", "See every Inventory menu and what it does"), "heading"))
    story.append(para("A star means the field is required. If a field turns red, read the message below it."))

    story.extend([PageBreak(), para('<a name="menu"/>Inventory menu map', "title"),
                  steps_diagram([("Open Inventory", "Use the left sidebar"),
                                 ("Open a menu", "Find your job below"),
                                 ("Choose a screen", "Open its form when needed")]),
                  para("Click a menu to jump to its guide. Your permissions decide which menus you see in Inventory.")])
    form(story, "Find the right screen", "Screens in the left sidebar", [
        (link("settings", "Setup"), "Start here shows the next setup task. Inventory settings controls company rules. "
         + link("rates", "Price lists and Cost rates") + " hold agreed prices and work rates."),
        (link("setup", "Stores &amp; Bins"), "Stores/Warehouses creates where stock is held. Bins/Racks names places inside a store."),
        (link("units", "Categories &amp; Units"), "Units defines how you count stock. Item categories groups similar items."),
        (link("settings", "Suppliers"), "Add and find the people you buy from."),
        (link("settings", "Customers"), "Add and find buyers, their contact details and agreed terms."),
        (link("raw", "Materials"), "Add raw goods, packaging, spare parts and general items."),
        (link("product", "Products"), "Add what you sell. Open a product to see price, stock and its recipe if it is made."),
        (link("stock", "Stock"), "Stock in receives goods. Stock shows balances. Move stock transfers goods. "
         "Adjustments correct known errors. Stock counts record a physical count. Quality check releases or blocks batches."),
        (link("make", "Production"), "Make plans a run, takes materials at Start and records output at Finish."),
        (link("sell", "Sales"), "Sell records a sale. Invoices shows bills. Sales returns records goods coming back."),
        (link("buy", "Purchase"), "Requisitions requests approval. Purchase orders records the order. "
         "Goods receipts records what arrived. Quality check records the inspection."),
        (link("reports", "Reports"), "Reports shows stock and activity. Month-end close locks a period when authorized."),
        (link("warranty", "Tools"), "Scan finds a code. Labels prints labels. Warranty records serials and claims."),
    ], headings=("Menu", "What it does"))
    section(story, "setup", "1. Set up stores and bins",
            "Create the locations where goods will be received, kept and counted.",
            [("Choose a branch", "The site that owns the store"), ("Add a store", "The room or warehouse"),
             ("Name bins or racks", "The exact places inside it")])
    form(story, "Add a store or warehouse", "Inventory > Stores &amp; Bins > Stores/Warehouses > Add store", rows("""
    Branch|Choose the office or site that owns this store.
    Code|Use a short unique code, e.g. RAW.
    Store/warehouse name|Use a name staff recognize, e.g. Main Warehouse.
    Store/warehouse type|Choose what the store is mainly used for.
    Notes|Optional. Add directions or handling instructions.
    """), who="Inventory admin", use="create a place that holds stock.")
    form(story, "Add an optional bin or rack", "Inventory > Stores &amp; Bins > Bins/Racks > Add bin", rows("""
    Store/warehouse|Choose the store containing the rack.
    Bin/rack code|Enter its location code, e.g. R1-S2.
    Description|Optional. Say where to find it, e.g. Rack 1, shelf 2.
    """), who="Storekeeper", use="tell staff exactly where items sit inside the store.")
    story.extend([para("Example: where is the flour?", "heading"),
                  steps_diagram([("Head Office", "Branch"), ("Raw Store", "Store or warehouse"),
                                 ("Rack 1, shelf 2", "Bin code R1-S2")])])
    done(story, f"Confirm you can select the store, then {link('units', 'set up units and categories')}.")

    section(story, "units", "1. Set up units and categories",
            "Give staff consistent names and units before they add items.",
            [("Add a unit", "How quantity is counted"), ("Add a category", "Where the item belongs"),
             ("Add an item", "Pick both on its form")])
    form(story, "Add a unit", "Inventory > Categories &amp; Units > Units", rows("""
    Code|Enter the short name, e.g. KG or PCS. Use Add standard units if it already exists in the standard list.
    Name|Enter the full name, e.g. Kilogram or Pieces.
    Kind|Choose weight, count, volume or another matching type.
    Decimals|Use 0 for whole pieces or a larger number for fractions.
    """), who="Inventory admin", use="decide how staff enter and count a quantity.")
    form(story, "Add a category", "Inventory > Categories &amp; Units > Item categories", rows("""
    Code|Use a short category code, e.g. DRY.
    Name|Use a name such as Dry goods.
    Item type|Choose what goes in the group, e.g. raw materials.
    Parent|Optional. Choose a larger category to put this one inside it.
    """), who="Inventory admin", use="group items so they are easier to find.")
    story.extend([para("Example: where does flour belong?", "heading"),
                  steps_diagram([("Flour", "Item name"), ("Dry goods", "Category"), ("KG", "Unit for counting stock")])])
    done(story, f"These choices appear when you {link('raw', 'add raw goods')} or {link('product', 'add products')}.")

    section(story, 'settings', "1. Set up Inventory (continued)", "Create the people you buy from and sell to. Ask an administrator about company-wide settings.", [('Add suppliers', 'Who sends goods'), ('Add customers', 'Who buys goods'), ('Check settings', 'Rules for the company')])
    form(story, "Add a supplier or customer", "Inventory > Suppliers > Suppliers or Inventory > Customers > Customers", rows("""
    Code|Use a unique code, e.g. SUP-001 or CUS-001.
    Company / shop name|Enter the business name. For a customer buying personally, leave it blank to use the contact name.
    Customer type / Territory|For customers, choose their type and enter the sales area if known.
    Contact person|Enter the name of the person who handles orders.
    Phone number / WhatsApp|Use the business contact's Bangladesh mobile number. Enter WhatsApp on the customer form.
    Email / Address|Enter the address for messages and the physical business or delivery address.
    Payment terms (days)|Enter the agreed time to pay. Leave blank if there is no agreement.
    Credit limit / Price list|For customers, enter their agreed credit limit or price list, if any.
    BIN / TIN|For suppliers, add the business tax numbers if applicable.
    Notes|Optional. Add relevant ordering or delivery details.
    """), who="Purchasing staff for suppliers. Sales staff for customers.", use="choose the right business on orders, receipts and sales.")
    section(story, 'company-settings', 'Company settings', 'These switches apply to your whole company.', [('Open settings', 'Company admin'), ('Choose the rules', 'Confirm with accounts'), ('Save', 'Rules apply company-wide')])
    form(story, "Company settings", "Inventory > Setup > Inventory settings", rows("""
    Average cost / Price cost|Ask the administrator or accounts team how costs should be calculated and shown.
    Purchase VAT|Ask accounts whether purchase VAT should be included in stock cost.
    Warn before expiry / Slow moving days|Set when expiry and slow-stock warnings should start.
    Alert emails|Enter the addresses that should get stock alerts.
    Small label / Large label / GS1 labels|Choose the sizes and barcode format your printer and scanners use.
    Warranty|Turn this on if the company tracks warranty, serials or claims.
    """), who="Company admin", use="set rules for all staff in this company.")

    section(story, "rates", "1. Set up prices and work rates",
            "Use price lists for agreed customer prices. Use cost rates when work time forms part of a recipe cost.",
            [("Set an agreed price", "What the customer pays"), ("Set a work rate", "What one hour costs"),
             ("Use it on the form", "Sell or cost a recipe")])
    form(story, "Price lists", "Inventory > Setup > Price lists", rows("""
    Name|Give the list a name staff recognize, e.g. Wholesale.
    Valid from / Valid to|Set the dates the prices apply, if known.
    Note|Record any agreed conditions that staff should know.
    Item / Unit|Choose what the price is for and the unit it covers, e.g. one carton.
    Price|Enter the amount you will charge for one of that unit.
    Minimum quantity|If the price applies only from a certain quantity, enter it here.
    """), who="Sales manager", use="set agreed prices that can apply when selling.")
    form(story, "Cost rates", "Inventory > Setup > Cost rates", rows("""
    Name|Name the rate, e.g. Oven or Line worker.
    For|Choose the kind of work, such as labour or machine time.
    Tk per hour|Enter the agreed cost of one hour of that work.
    """), who="Production lead or accounts staff", use="give recipes a rate for labour or machine time.")
    done(story, "Check the selling price on a sale or select the work rate in the recipe cost form.")

    section(story, 'raw', "2. Add raw goods", "Raw goods are materials or packaging you use to make products. Adding the item creates its record.", [('Describe the goods', 'Name, type and category'), ('Set units and tracking', 'How the goods are counted'), ('Save the raw good', 'Ready for Stock in or Recipe')])
    form(story, "Add Raw Goods form", "Inventory > Materials > Materials > Add Raw Goods", rows("""
    How you get it|Choose Buy Finish Goods if you buy it, Made here if you make it, or Both.
    Type|Choose Raw material for ingredients or parts. Choose Packaging for bottles, cartons or wrappers.
    Material name|Enter the name staff use, e.g. Flour.
    Item code|Enter your own code or leave it blank for an automatic one.
    Category|Choose the group that matches the item and its type.
    Base unit|Choose the unit used to count stock, e.g. KG.
    Purchase unit|Optional. Choose a bigger unit you buy in, e.g. BAG.
    Contains|Enter how many base units one purchase unit holds, e.g. 50 KG per BAG.
    Minimum stock|Enter the quantity below which the stock should show Low.
    Reorder quantity|Enter how much you usually buy in one order.
    Default store|Choose where you usually keep this item.
    Expected buy price|Optional. Enter an estimated cost per base unit. Enter the real cost in Stock in.
    """), who="Inventory staff", use="enter the item details before setting tracking.")

    story.append(para(link("raw-tracking", "Next: set raw goods tracking and warranty"), "path"))

    section(story, "raw-tracking", "Raw goods: tracking and warranty",
            "Continue in the same form. Turn on only the options the item needs.",
            [("Batches and expiry", "Track dates when needed"), ("Has warranty", "Enter days, if covered"),
             ("Save the item", "Ready to receive or make")])
    form(story, "Tracking fields", "Add Raw Goods > Batches &amp; expiry / Warranty", rows("""
    Batch tracking|Turn on if you need a lot number for each delivery or production run.
    Expiry tracking|Turn on if this item has an expiry date.
    Shelf life|Enter how many days it keeps after it was made, when known.
    QC before use|Turn on if stock must pass an inspection before use.
    Has warranty|Turn on if the supplier covers this item. This shows the two fields below.
    Warranty duration|Enter the number of days covered from receipt, e.g. 90.
    Serial number per unit|Turn on only if you need to track each individual unit by its own number.
    Barcode / Notes|Under More details, enter an existing barcode or any useful notes.
    """), who="Inventory admin or production lead", use="create ingredients or packaging you can receive or use in a recipe.")
    done(story, "Use Put it in stock to record a delivery. You can also choose the raw good when making a recipe.")

    section(story, 'product', "2. Add products", "A product is something you sell. Its available quantity comes from Stock in or Make.", [('Choose how you get it', 'Buy, make or both'), ('Set units and coverage', 'Packs, warranty and serials'), ('Save the product', 'Ready to receive or make')])
    form(story, "Add product form", "Inventory > Products > Products > Add product", rows("""
    How you get it|Choose Buy Finish Goods, Make it here, or Both.
    Product name|Enter the name staff and customers recognize.
    Item code|Enter your own code or leave it blank for an automatic one.
    Category|Choose the correct product group.
    Base unit|Choose the smallest unit you sell, e.g. PCS.
    Selling pack|Optional. Choose a bigger pack you also sell, e.g. CARTON.
    Contains|Enter how many base units are in one pack, e.g. 24.
    VAT|Enter the VAT percentage if it applies.
    Minimum stock as|Choose a fixed quantity or a number of days of sales.
    Minimum stock / Days of sales|Enter the quantity or number of days you want to keep.
    Reorder quantity|Enter the usual quantity you buy or make at once.
    New stock goes to|Choose the store that usually receives the product.
    """), who="Inventory staff", use="enter the item details before setting tracking.")

    story.append(para(link("product-tracking", "Next: set product tracking and warranty"), "path"))

    section(story, "product-tracking", "Products: tracking and warranty",
            "Continue in the same form. Turn on only the options the item needs.",
            [("Batches and expiry", "Track dates when needed"), ("Has warranty", "Enter days, if covered"),
             ("Save the item", "Ready to receive or make")])
    form(story, "Tracking fields", "Add product > Batches &amp; expiry / Warranty", rows("""
    Batch tracking|Turn on when you need to track each delivery or production lot.
    Expiry tracking / Shelf life|Turn on expiry tracking for dated products. Enter shelf life in days if known.
    QC before use|Turn on when the stock must pass inspection before sale or use.
    Has warranty|Turn on to give the customer warranty coverage. The fields below then appear.
    Warranty duration|Enter the number of days covered from delivery, e.g. 180.
    Serial number per unit|Turn on if you need to scan the exact unit sold. Older items can keep their existing warranty terms.
    Barcode / Notes|Under More details, enter an existing barcode or useful notes.
    """), who="Inventory admin or product lead", use="create an item you can receive or make, then sell.")
    done(story, "For a bought product, open Stock in. If you make the product, open its Recipe first.")

    section(story, 'receive', "3. Receive or enter stock", "Use Stock in when goods arrive. Choose First count for goods already on hand when you start using Inventory.", [('Choose the source', 'Purchase, first count or return'), ('Choose store and items', 'Actual quantity and cost'), ('Put in stock', 'Check the receipt and balance')])
    form(story, "Stock in header", "Inventory > Stock > Stock in > Stock in", rows("""
    Where it comes from|Choose A purchase for a delivery, First count for opening stock, or A customer returned it for returned goods.
    Supplier / Customer|Select the supplier for a purchase or the customer for a return.
    Into store|Choose the store receiving the goods. Choose it before adding items.
    Date|Enter the date the goods arrived or were counted.
    Why it came back|If this is a return, say what happened.
    Sell Under Rate|Turn on beside the label only if you intend to set a sale price below cost.
    Why below cost|Give the reason if Sell Under Rate is on, e.g. a clearance sale.
    """), who="Receiving staff or storekeeper", use="say where goods came from and which store gets them.")
    story.append(para(link("receive-items", "Next: enter the items and receipt totals"), "path"))

    example(story, 'Example: choose the right source', 'Flour arrived from a supplier today: choose <b>A purchase</b>. Flour was already in your store before you started using Inventory: choose <b>First count</b>.')

    section(story, 'receive-items', 'Stock in: items and totals', 'Stay on the Stock in form. Add a line for each item or batch.', [('Pick each item', 'Use the correct unit'), ('Enter quantity and cost', 'Match the supplier bill'), ('Put in stock', 'Check the saved receipt')])
    form(story, "Items and receipt totals", "Stock in > Items > Add a line for each item or batch", rows("""
    Item|Choose the goods that arrived.
    Quantity / Unit|Enter how many and its unit, e.g. 2 BAG.
    Cost|Enter the price for one of the unit selected, e.g. Tk 2,500 per BAG.
    Sale price|Optional. Enter the new price only if you want to set it.
    Batch number / Condition|Copy the batch number if asked. Choose the actual condition for returned goods when shown.
    Made on / Expiry date|Use the dates printed on the goods or confirmed by the supplier.
    Bin/rack|Choose the exact location if the line asks for one.
    VAT % / VAT amount|Enter one of the two when VAT was charged. Check the amount the form fills in.
    Delivery / carrying cost|Enter what you actually paid to bring the goods in.
    """), who="Receiving staff or storekeeper", use="record what actually arrived and what you paid.")
    done(story, "Open Stock to check the quantity. Opening stock may need approval. Goods marked for QC wait for inspection.")

    example(story, 'Example: check the unit before saving', 'If 1 BAG holds 50 KG, enter <b>2 BAG</b> at <b>Tk 2,500 per BAG</b>. Stock increases by 100 KG once posted, and the line costs Tk 5,000 before extras.')

    section(story, 'make', "3. Check quality and make products", "Use these forms when goods need inspection or you manufacture the product.", [('Check the inputs', 'Pass quality checks if needed'), ('Write recipe and plan', 'Materials for your output'), ('Start, then finish', 'Use materials and add output')])
    form(story, "Quality check", "Inventory > Stock > Quality check > Select a batch", rows("""
    Result|Choose Passed to release usable stock. Choose Failed to block the batch.
    Notes|Describe what you checked and why you passed or failed it.
    """), who="Quality inspector", use="release a good batch or keep a failed batch out of use.")
    form(story, "Recipe", "Open the product > Recipe > Set recipe quantity > Add material", rows("""
    The recipe is for|Enter what the recipe makes, e.g. 240 packets.
    Material|Choose an existing item, or create a new one when the form offers it.
    Type / Code / Name / Category / Unit|Fill these in only when adding a new raw or packaging item from the recipe.
    Keeps batches / Has an expiry date|For a new material, turn on only what it needs.
    How much the recipe uses|Enter the amount for that recipe, e.g. 18 KG of flour for 240 packets.
    Normal wastage %|Enter the normal extra allowance, if any.
    Rate / Time or quantity|For additional labour or machine cost, choose the rate and enter how much you use.
    """), who="Production lead", use="list the materials needed for a set amount of product.")
    story.append(para(link("make-run", "Next: plan, start and finish the production run"), "path"))

    section(story, 'make-run', 'Run a production job', 'Use the recipe you saved before starting a production run.', [('Plan', 'Check material availability'), ('Start', 'Take materials from stock'), ('Finish', 'Record the actual output')])
    form(story, "Plan, start and finish", "Inventory > Production > Make > Plan", rows("""
    Product / How much to make|Choose the product and enter the planned output.
    Date / Products go into|Choose the work date and the store for good output.
    Material source|Review where stock comes from. Move missing goods or resolve shortages before Start.
    Good / Damaged / Waste|At Finish, enter the actual output and any damaged or wasted amount shown.
    Reason / Batch / Dates|Explain unexpected waste and enter the batch and dates if prompted.
    """), who="Production planner and operator", use="check materials, run production and record actual output.")
    done(story, "Check the finished stock after Finish. Release the batch in Quality check first if the product needs inspection.")

    section(story, 'stock', "4. Move, count or adjust stock", "Use the reason and source document so another person can see why stock changed.", [('Check the balance', 'Look at the right store'), ('Pick the right form', 'Move, count or adjust'), ('Post or approve', 'Check the new balance')])
    form(story, "Move stock", "Inventory > Stock > Move stock > New move", rows("""
    From store / To store|Choose where stock leaves and where it will arrive.
    Date|Enter the day it moved.
    Item / Quantity / Unit|Choose what moved and how much.
    Batch / Bin|Enter the batch or location when asked. Check it against the goods.
    Receive|The destination confirms receipt when the transfer asks for it.
    """), who="Sending and receiving storekeepers", use="move a quantity from one store to another.")
    form(story, "Stock count", "Inventory > Stock > Stock counts", rows("""
    Store / Date|Choose where and when the stock was physically counted.
    Item / Batch / Bin|Identify what you counted and where you found it.
    Counted quantity|Enter the real amount you counted. Review any difference before approval.
    Notes / Reason|Explain a difference when you know why it happened.
    """), who="Storekeeper and approver", use="compare a physical count with what Inventory shows.")
    story.append(para(link("adjust", "See the adjustment form"), "path"))

    section(story, 'adjust', 'Correct a stock difference', 'Use an adjustment when you know what changed and why.', [('Identify the stock', 'Store, item and batch'), ('Enter the difference', 'Give the reason'), ('Post or approve', 'Check the new balance')])
    form(story, "Adjustment", "Inventory > Stock > Adjustments", rows("""
    Store / Date|Choose where the correction applies and enter the date.
    Item / Batch / Bin|Identify the goods and their exact location when needed.
    Quantity / Direction|Enter how much stock to add or take away as shown on the form.
    Reason|Say why, e.g. damaged cartons found during a count.
    """), who="Stock controller and approver", use="correct a known stock difference and record the reason.")
    done(story, "Check whether the document was posted or is waiting for approval. Compare the new balance with the actual stock.")

    section(story, 'sell', "5. Set the price and sell", "Check the available stock and the agreed price before you confirm a delivery.", [('Check cost and price', 'Use the right unit'), ('Record the sale', 'Customer and goods'), ('Deliver and invoice', 'Record what was sent')])
    form(story, "Sale price", "Open the product > Sale price", rows("""
    Cost of one|Read this first so you know what one unit costs.
    Sale price|Enter the price for one base unit. Use Units and prices to set a pack price.
    Sell below cost / Why|Use only for an intentional loss and explain why.
    """), who="Sales manager or authorized pricing staff", use="set the price customers should be charged.")
    form(story, "New sale", "Inventory > Sales > Sell > New sale", rows("""
    Customer|Choose the person or business buying the goods.
    From store|Choose where the goods leave.
    Date|Enter the sale or delivery date shown on the document.
    Item / Quantity / Unit|Choose what you sold and how much.
    Price / VAT|Check the agreed unit price and tax when shown.
    Make the invoice now|Turn this on if an invoice is needed now and you are allowed to issue it.
    Why, if over the credit limit|Explain any approved credit exception.
    Sell below cost / Why below cost|Give the reason for an intentional below-cost sale when the form asks.
    """), who="Sales staff", use="record what a customer buys and the agreed price.")
    story.append(para(link("dispatch", "Next: record delivery and invoice"), "path"))

    section(story, 'dispatch', 'Deliver and invoice', 'Open the sale after saving it to record delivery or issue the invoice.', [('Open the sale', 'Find the customer and order'), ('Record delivery', 'Goods and serials'), ('Issue the invoice', 'Check dates and totals')])
    form(story, "Delivery and invoice", "Open the sales document", rows("""
    Delivery date / Vehicle / Driver|Enter the actual delivery date and transport details when requested.
    Serial numbers|Scan the exact units sent if the product tracks serials.
    Invoice date / Payment terms|Check dates, agreed terms and totals before issuing the invoice.
    """), who="Dispatch and accounts staff", use="record what leaves and issue the customer's bill.")
    done(story, "Open the saved sale and check the customer, goods, quantity, delivery and invoice.")

    section(story, 'buy', "6. Buy with orders", f"Use a purchase order when an approval or order match is needed. {link('returns', 'Returning goods?')}", [('Ask and approve', 'Requisition if required'), ('Order from supplier', 'Record price and quantity'), ('Receive the goods', 'Check against the order')])
    form(story, "Purchase requisition", "Inventory > Purchase > Requisitions", rows("""
    Date / Department / Needed by|Enter who needs the goods and when.
    Warehouse / Store|Choose where the goods should go.
    Item / Quantity / Unit|List what you need and how much.
    Notes / Reason|Explain why the purchase is needed. Submit it for approval.
    """), who="Staff requesting goods and the approver", use="get permission to buy.")
    form(story, "Purchase order and receipt", "Inventory > Purchase > Purchase orders / Goods receipts", rows("""
    Supplier / Requisition|Choose who you buy from and link the approved requisition if applicable.
    Order date / Expected date|Enter the order date and expected delivery date.
    Currency / Payment terms|Check the currency and terms agreed with the supplier.
    Item / Quantity / Unit / Rate|List what you ordered and use the supplier's agreed rate.
    Purchase order / Received date|Open the order at receipt and enter the actual arrival date.
    Into store / Received quantity|Choose the receiving store and enter only what arrived.
    Batch / Expiry / Cost|Copy the batch and dates and verify the supplier's actual cost where shown.
    """), who="Buyer places the order. Receiving staff records arrival.", use="compare the order with what the supplier actually delivered.")
    section(story, 'returns', 'Return goods', 'Link the return to the original sale or receipt when you can.', [('Find the document', 'Sale or purchase receipt'), ('Enter what went back', 'Quantity and condition'), ('Post the return', 'Check the stock effect')])
    form(story, "Return goods", "Inventory > Sales > Sales returns, or open the related purchase receipt", rows("""
    Original delivery or receipt|Link the document the return belongs to.
    Return date / Store|Enter the actual return date and the store affected.
    Item / Quantity / Condition|Enter what came back or went back, how much, and its condition.
    Reason / Notes|Explain why it was returned.
    """), who="Sales, purchasing or store staff", use="link goods sent back to the original delivery or receipt.")
    done(story, "Check the posted receipt or return. Avoid entering the same delivery again using Stock in.")

    section(story, 'warranty', "6. Record serials and warranty claims", "Turn on Warranty in Inventory settings and set the warranty on the item first.", [('Record the serial', 'Match it to the unit'), ('Look up coverage', 'Use the claim date'), ('Record what you did', 'Repair, replace or other result')])
    form(story, "Serial numbers in and out", "Inventory > Tools > Warranty", rows("""
    Item / Received on|For numbers coming in, choose the item and actual date it arrived.
    Serial numbers|Scan or type one number per unit. One per line is easiest to check.
    Supplier / Batch|Link the supplier and batch when known.
    Delivery|For numbers going out, choose the posted delivery and scan the units sent.
    """), who="Storekeeper and dispatch staff", use="match individual units to receipts and customer deliveries.")
    story.append(para(link("claim", "Next: record the claim"), "path"))

    example(story, 'Example: which serial goes out?', 'If you ship unit <b>MX-004</b>, scan MX-004 on that delivery. Staff can then find that exact unit when the customer calls.')

    section(story, 'claim', 'Record a warranty claim', 'Look up coverage first and record what you did for the customer.', [('Find the unit', 'By serial or item'), ('Check coverage', 'Use the claim date'), ('Save the outcome', 'Repair, replace or other')])
    form(story, "New claim", "Inventory > Tools > Warranty > New claim", rows("""
    Serial number|Scan or type the number on the unit. Check the coverage shown.
    Or the item|Choose this if the item has no serial number.
    Delivered on|Enter the original delivery date when there is no serial.
    Customer|Choose who brought the item back.
    Claim date|Enter when the complaint was received.
    What is wrong|Describe the fault in a few words.
    What you do|Choose repair, replace, refund or refuse.
    Cost (repair or refund)|Enter the actual cost or amount paid back.
    Faulty unit into|Choose where the faulty unit goes, or leave it with the customer.
    New unit from|For a replacement, choose the store supplying the new unit.
    Why|Explain refused claims, exceptions or coverage outside the warranty.
    """), who="Customer service or warranty staff", use="check coverage and record the action taken.")
    done(story, "Open the saved claim and verify the item, customer, outcome and any stock or money recorded.")

    section(story, 'reports', "6. Find stock and check reports", "Use the same dates and stores when comparing numbers.", [('Pick the question', 'Stock, movement or a batch'), ('Choose filters', 'Dates, store and item'), ('Check the result', 'Trace back to the document')])
    form(story, "Scan, label and import", "Inventory > Tools > Scan / Labels; or Import from Excel on a list", rows("""
    Scan or enter a code|Use a label code to find the item, batch, package or bin.
    Label type / Size / Copies|Choose what to print, pick the paper size and enter the number of copies.
    Void reason|If retiring a label, explain why it is no longer valid.
    File|Download the available Excel template, fill it in, upload it and check the preview before confirming.
    """), who="Storekeeper or inventory admin", use="look up or label stock, or load a prepared list from Excel.")
    form(story, "Reports and month-end", "Inventory > Reports > Reports or Month-end close", rows("""
    From / To date|Choose the period for the stock movement, purchase or sales report.
    Branch / Store|Limit the report to the location you are checking.
    Item / Category / Batch|Choose what you want to find. Use Batch trace to follow a lot through receipt, production and sales.
    Report type|Use stock reports for balances, reorder reports for shortages and movement reports for the document history.
    Period / Reason|An authorized user chooses the period and gives a reason when the close or reopen form asks.
    """), who="Manager, stock controller or accounts staff", use="check balances and review or close a reporting period.")
    done(story, "If a quantity looks wrong, open its source document before making a correction. Use the two companion tutorials for longer examples and less common forms.")

    doc = SimpleDocTemplate(str(OUT), pagesize=A4, leftMargin=19 * mm, rightMargin=19 * mm,
                            topMargin=21 * mm, bottomMargin=20 * mm, title="Inventory operator manual")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


if __name__ == "__main__":
    build()
