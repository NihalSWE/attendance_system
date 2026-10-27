"""The redesigned inventory screens on sample data (plan §15 phase 2), for
approval before they are connected to real records. Nothing is saved: each
form is checked the way the real one will be and answers in place
(base_template/fetch.py), then goes on to the next screen."""

from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_POST

from base_template import fetch
from base_template.views import company_admin_required
from inventory import preview_data as data


CHOICES = {
    "types": [("raw", "Raw material"), ("packaging", "Packaging")],
    "units": [("", "Choose a unit"), ("kg", "kg"), ("g", "g"), ("litre", "litre"),
              ("pcs", "pcs"), ("packet", "packet")],
    "store_options": [("", "No usual store")] + list(data.STORES.items()),
    "how_options": [("make", "Make it here"), ("buy", "Buy it ready"), ("both", "Both")],
    "product_options": [("FG-0001", "FG-0001 · Biscuit 100 g")],
    "leftover_options": [("return", "Return to their stores"), ("waste", "Mark as wasted (give a reason)"),
                         ("none", "Nothing left over")],
}


def _page(request, template, **context):
    return render(request, f"inventory/preview/{template}",
                  {"stores": data.STORES, **CHOICES, **context})


def _gate(view):
    return login_required(company_admin_required(view))


@_gate
def home(request):
    summary = data.product_summary()
    low = [m for m in data.MATERIALS.values() if sum(m["stock"].values(), Decimal("0")) < m["keep"]]
    steps = [
        {"done": True, "what": "Materials added", "detail": f"{len(data.MATERIALS)} materials: flour, sugar, oil, wrappers, cartons.",
         "url": reverse("inventory:preview_materials"), "button": "See materials"},
        {"done": True, "what": "Biscuit 100 g: recipe and cost", "detail": "1 batch = 10 cartons.",
         "url": reverse("inventory:preview_product", args=["FG-0001"]), "button": "See product"},
        {"done": False, "what": "Wrappers are short for the next production",
         "detail": "412 more wrappers needed for 5 batches.", "url": "#", "button": "Buy the rest"},
        {"done": False, "what": "Make the next production", "detail": f"Suggested: {summary['suggest']} batches.",
         "url": reverse("inventory:preview_plan"), "button": "Make"},
    ]
    return _page(request, "home.html", steps=steps, summary=summary, low=low,
                 productions=data.PRODUCTIONS, active="home")


@_gate
def materials(request):
    rows = []
    for code, m in data.MATERIALS.items():
        stock = sum(m["stock"].values(), Decimal("0"))
        tone, status = ("ok", "OK") if stock >= m["keep"] else (("bad", "Out") if not stock else ("wait", "Low"))
        rows.append({"code": code, **m, "in_stock": stock, "value": data.money(stock * m["cost"]),
                     "tone": tone, "status": status})
    return _page(request, "materials.html", rows=rows, active="materials",
                 low=sum(1 for r in rows if r["tone"] != "ok"),
                 value=sum((r["value"] for r in rows), Decimal("0")))


@_gate
def material(request, code):
    m = data.MATERIALS.get(code)
    if m is None:
        raise Http404
    stock = sum(m["stock"].values(), Decimal("0"))
    used_in = [(data.PRODUCT["name"], qty, waste) for c, qty, waste in data.RECIPE if c == code]
    return _page(request, "material.html", code=code, m=m, in_stock=stock,
                 value=data.money(stock * m["cost"]), used_in=used_in, active="materials")


@_gate
def products(request):
    return _page(request, "products.html", product=data.PRODUCT, summary=data.product_summary(),
                 active="products")


@_gate
def product(request, code):
    if code != data.PRODUCT["code"]:
        raise Http404
    rows, materials_total = data.recipe_lines(1)
    return _page(request, "product.html", product=data.PRODUCT, summary=data.product_summary(),
                 recipe=rows, sheet=data.cost_sheet(1), productions=data.PRODUCTIONS,
                 rates=data.COST_RATES, materials=data.MATERIALS, active="products")


@_gate
def make(request):
    return _page(request, "make.html", productions=data.PRODUCTIONS, summary=data.product_summary(),
                 active="make")


@_gate
def plan(request):
    summary = data.product_summary()
    return _page(request, "plan.html", summary=summary, lines=data.plan_lines(5), active="make")


@_gate
def production(request, number):
    found = next((p for p in data.PRODUCTIONS if p["number"] == number), None)
    if found is None:
        raise Http404
    lines = data.plan_lines(found["batches"])
    return _page(request, "production.html", p=found, lines=lines,
                 short=[row for row in lines if row["short"]],
                 sheet=data.cost_sheet(found["batches"]),
                 result=data.finished_cost() if found["status"] == "Finished" else None,
                 active="make")


# --- saving (checked, never stored) ------------------------------------------

def _number(value):
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


class _Answer:
    """Collects reasons by box name, like a form's errors."""

    def __init__(self, post):
        self.post, self.errors, self.form_errors = post, {}, []

    def need(self, *names):
        for name in names:
            if not self.post.get(name, "").strip():
                self.errors.setdefault(name, []).append("Fill this in.")

    def positive(self, name, whole=False):
        value = _number(self.post.get(name, ""))
        if self.post.get(name, "").strip() and (value is None or value <= 0 or (whole and value != value.to_integral())):
            self.errors.setdefault(name, []).append("Write a whole number above 0." if whole else "Write a number above 0.")
        return value

    def reply(self, message, redirect=None):
        if not (self.errors or self.form_errors):
            return fetch.saved(message, redirect)
        if self.form_errors and not self.errors:
            text, rest = self.form_errors[0], self.form_errors[1:]
        else:
            text, rest = fetch.FIX_THE_RED, self.form_errors
        return JsonResponse({"ok": False, "message": text, "errors": self.errors,
                             "form_errors": rest}, status=400)


@_gate
@require_POST
def save(request, screen):
    post, answer = request.POST, _Answer(request.POST)
    if screen == "material":
        answer.need("name", "unit")
        if post.get("has_expiry"):
            answer.need("shelf_life")
            answer.positive("shelf_life", whole=True)
        answer.positive("keep")
        if post.get("name", "").strip().lower() in {m["name"].lower() for m in data.MATERIALS.values()}:
            answer.errors.setdefault("name", []).append("A material with this name exists already.")
        return answer.reply(f"{post.get('name', '').strip()} added (sample: nothing was saved).",
                            reverse("inventory:preview_materials"))
    if screen == "product":
        answer.need("name", "unit", "price_unit")
        answer.positive("price_unit")
        if post.get("other_unit", "").strip():
            answer.need("other_size", "price_other")
            answer.positive("other_size", whole=True)
            answer.positive("price_other")
        return answer.reply(f"{post.get('name', '').strip()} added (sample).",
                            reverse("inventory:preview_product", args=["FG-0001"]))
    if screen == "recipe_line":
        answer.need("qty")
        answer.positive("qty")
        if post.get("source") == "new":
            answer.need("new_name", "new_unit")
        else:
            answer.need("material")
        return answer.reply("Material added to the recipe (sample).")
    if screen == "plan":
        answer.need("batches", "date")
        answer.positive("batches", whole=True)
        return answer.reply("Production PRD-0004 planned (sample). Start it when the materials leave the stores.",
                            reverse("inventory:preview_production", args=["PRD-0003"]))
    if screen == "start":
        found = next(p for p in data.PRODUCTIONS if p["number"] == post.get("number"))
        short = [row for row in data.plan_lines(found["batches"]) if row["short"]]
        if short:
            answer.form_errors = [f"Can't start: {row['name']} is short by {row['short'].normalize():f} {row['unit']}."
                                  for row in short] + ["Make fewer, or buy the rest first."]
        return answer.reply("Started: the materials left the stores (sample).",
                            reverse("inventory:preview_production", args=[found["number"]]))
    if screen == "finish":
        answer.need("good")
        good = answer.positive("good", whole=True)
        damaged = _number(post.get("damaged") or "0")
        split = [_number(post.get(f"into_{code}") or "0") for code in ("MAIN", "DEP")]
        if good is not None and None not in split and sum(split) != good:
            answer.errors["into_MAIN"] = [f"The stores add up to {sum(split):f}, not {good:f}."]
        planned = data.PRODUCT["batch_makes_cartons"] * 3
        if good and damaged is not None and (good / planned) < Decimal("0.9") and not post.get("reason", "").strip():
            answer.errors["reason"] = ["Good is below 90 % of the plan: say why."]
        return answer.reply("Finished: 30 cartons into stock as batch BIS-271026-1 (sample).",
                            reverse("inventory:preview_production", args=["PRD-0001"]))
    raise Http404
