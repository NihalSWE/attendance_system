"""Sample data for the redesigned screens (plan §15 phase 2): a biscuit factory.

Nothing here touches the database. The screens are approved on this data
first, then connected to the real records in phases 3-7. Every number shown
is worked out here, so all screens agree with each other.
"""

from decimal import ROUND_HALF_UP, Decimal

D = Decimal


def money(value):
    return D(value).quantize(D("0.01"), rounding=ROUND_HALF_UP)


STORES = {
    "RSA": "Raw store A", "RSB": "Raw store B", "PKS": "Packing store",
    "MAIN": "Main store", "DEP": "Depot",
}

# code: name, type, unit, other unit, average cost, stock by store, stock to keep
MATERIALS = {
    "RM-0001": {"name": "Flour", "type": "Raw material", "unit": "kg", "pack": "1 bag = 50 kg",
                "cost": D("52"), "stock": {"RSA": D("60"), "RSB": D("60")}, "keep": D("100"),
                "batches": [("FL-231026", "23 Oct", "23 Apr", "RSA", D("60")),
                            ("FL-241026", "24 Oct", "24 Apr", "RSB", D("60"))]},
    "RM-0002": {"name": "Sugar", "type": "Raw material", "unit": "kg", "pack": "1 bag = 50 kg",
                "cost": D("95"), "stock": {"RSA": D("40")}, "keep": D("50"),
                "batches": [("SG-201026", "20 Oct", "20 Oct 2027", "RSA", D("40"))]},
    "RM-0003": {"name": "Palm oil", "type": "Raw material", "unit": "litre", "pack": "1 drum = 200 litre",
                "cost": D("180"), "stock": {"RSA": D("30")}, "keep": D("20"),
                "batches": [("OL-151026", "15 Oct", "15 Apr", "RSA", D("30"))]},
    "PK-0001": {"name": "Wrapper 100 g", "type": "Packaging", "unit": "pcs", "pack": "1 roll = 2,000 pcs",
                "cost": D("0.90"), "stock": {"PKS": D("800")}, "keep": D("3000"), "batches": []},
    "PK-0002": {"name": "Carton 24", "type": "Packaging", "unit": "pcs", "pack": "",
                "cost": D("12"), "stock": {"PKS": D("300")}, "keep": D("100"), "batches": []},
}

PRODUCT = {
    "code": "FG-0001", "name": "Biscuit 100 g", "how": "Make", "unit": "packet",
    "other_unit": ("carton", 24), "prices": [("packet", D("20")), ("carton", D("450"))],
    "keep_cartons": 200, "batch_makes_cartons": 10, "shelf_life": 180,
    "stock": [("BIS-201026-1", "20 Oct", "18 Apr", "MAIN", 120, D("245.10")),
              ("BIS-261026-1", "26 Oct", "24 Apr", "DEP", 30, D("248.33"))],
    "sold_30_days_cartons": 540,
}

# One batch (10 cartons): material code, quantity, wastage %
RECIPE = [("RM-0001", D("18"), D("2")), ("RM-0002", D("5"), D("0")), ("RM-0003", D("3"), D("0")),
          ("PK-0001", D("240"), D("1")), ("PK-0002", D("10"), D("0"))]

# Cost lines per batch: label, how it is worked out (words), amount or None for % of materials
COST_RATES = [("Line worker", "per hour", D("75")), ("Supervisor", "per hour", D("150")),
              ("Oven", "per machine hour", D("300"))]


def recipe_lines(batches=1):
    rows, total = [], D("0")
    for code, qty, waste in RECIPE:
        material = MATERIALS[code]
        need = (qty * batches * (1 + waste / 100)).quantize(D("0.01"))
        cost = money(need * material["cost"])
        in_stock = sum(material["stock"].values(), D("0"))
        per_batch = qty * (1 + waste / 100)
        rows.append({"code": code, "name": material["name"], "type": material["type"],
                     "unit": material["unit"], "qty": qty * batches, "waste": waste, "need": need,
                     "cost": cost, "in_stock": in_stock, "enough": int(in_stock // per_batch),
                     "short": max(need - in_stock, D("0"))})
        total += cost
    return rows, total


def cost_sheet(batches=1):
    _, materials = recipe_lines(batches)
    lines = [
        ("Materials", "recipe × average cost (with wastage)", materials),
        ("Labour", f"4 people × {D('1.2') * batches} h × 75", money(4 * D("1.2") * batches * 75)),
        ("Machines", f"Oven {D('0.5') * batches} h × 300", money(D("0.5") * batches * 300)),
        ("Gas", "fixed per batch", money(120 * batches)),
        ("Rent share", "5 % of materials", money(materials * D("0.05"))),
    ]
    total = sum((amount for _, _, amount in lines), D("0"))
    cartons = PRODUCT["batch_makes_cartons"] * batches
    packets = cartons * PRODUCT["other_unit"][1]
    return {"lines": lines, "total": total, "per_carton": money(total / cartons),
            "per_packet": money(total / packets), "cartons": cartons, "packets": packets}


def product_summary():
    sheet = cost_sheet(1)
    price = PRODUCT["prices"][0][1]
    margin = price - sheet["per_packet"]
    in_stock = sum(row[4] for row in PRODUCT["stock"])
    rows, _ = recipe_lines(1)
    possible = min(row["enough"] for row in rows)
    short_by = min(rows, key=lambda row: row["enough"])
    needed = max(PRODUCT["keep_cartons"] - in_stock, 0)
    batches = -(-needed // PRODUCT["batch_makes_cartons"])
    return {"sheet": sheet, "price": price, "margin": margin,
            "margin_pct": int((margin / price * 100).quantize(D("1"))),
            "in_stock": in_stock, "in_stock_packets": in_stock * PRODUCT["other_unit"][1],
            "suggest": min(batches, possible), "wanted": batches, "possible": possible,
            "short_by": short_by["name"], "per_day": PRODUCT["sold_30_days_cartons"] // 30}


# Productions: number, date, batches, status, good, damaged
PRODUCTIONS = [
    {"number": "PRD-0003", "date": "28 Oct", "batches": 5, "status": "Planned", "tone": "info"},
    {"number": "PRD-0002", "date": "27 Oct", "batches": 3, "status": "Started", "tone": "wait"},
    {"number": "PRD-0001", "date": "26 Oct", "batches": 5, "status": "Finished", "tone": "ok",
     "good": 48, "damaged": 2},
]


def plan_lines(batches):
    """Where each material comes from: usual store first, then the others."""
    rows, _ = recipe_lines(batches)
    for row in rows:
        left, taken = row["need"], []
        for store, qty in MATERIALS[row["code"]]["stock"].items():
            if left <= 0:
                break
            take = min(qty, left)
            taken.append((STORES[store], take))
            left -= take
        row["taken"] = taken
    return rows


def finished_cost():
    """PRD-0001: 5 batches, 48 good cartons of 50, a little more flour used."""
    sheet = cost_sheet(5)
    actual = [(label, how, amount + (D("120") if label == "Materials" else D("0")))
              for label, how, amount in sheet["lines"]]
    total = sum((amount for _, _, amount in actual), D("0"))
    good_cartons = 48
    return {"planned": sheet, "actual": actual, "total": total,
            "per_carton": money(total / good_cartons),
            "per_packet": money(total / (good_cartons * 24)),
            "rows": [(label, planned, act, act - planned) for (label, _, planned), (_, _, act)
                     in zip(sheet["lines"], actual)]}
