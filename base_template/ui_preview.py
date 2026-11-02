"""The layout pieces with sample data (2026-10-27, plan §15 phase 1), for
approval before screens are built from them. Nothing here is saved: the demo
form only checks what was typed and answers the way a real form will."""

from decimal import Decimal

from django import forms
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.urls import reverse

from base_template import fetch
from base_template.views import company_admin_required
from common.forms import StyledFormMixin

UNITS = (("", "Choose a unit"), ("kg", "kg"), ("bag", "bag (50 kg)"), ("pcs", "piece"))


class DemoMaterialForm(StyledFormMixin, forms.Form):
    name = forms.CharField(label="Name", max_length=80)
    unit = forms.ChoiceField(label="Unit", choices=UNITS)
    stock_to_keep = forms.DecimalField(label="Stock to keep", min_value=Decimal("0"),
                                       required=False, help_text="Below this it shows as low.")
    has_expiry = forms.BooleanField(label="Has an expiry date", required=False)
    shelf_life = forms.IntegerField(label="Shelf life (days)", min_value=1, required=False)

    def clean(self):
        data = super().clean()
        if data.get("has_expiry") and not data.get("shelf_life"):
            self.add_error("shelf_life", "Give the shelf life, or switch expiry off.")
        if (data.get("name") or "").strip().lower() == "flour":
            raise forms.ValidationError("Flour is added already (sample check).")
        return data


SAMPLE_ROWS = (
    ("RM-0001", "Flour", "kg", "120", "ok", "OK"),
    ("RM-0002", "Sugar", "kg", "40", "wait", "Low"),
    ("PK-0001", "Wrapper", "piece", "800", "bad", "Short for next production"),
    ("PK-0002", "Carton", "piece", "300", "ok", "OK"),
)


@login_required
@company_admin_required
def ui_preview(request):
    if request.method == "POST":
        form = DemoMaterialForm(request.POST)
        if form.is_valid():
            if fetch.wants_json(request):
                return fetch.saved(f"{form.cleaned_data['name']} added (sample: nothing was saved).")
        elif fetch.wants_json(request):
            return fetch.refused(form)
    else:
        form = DemoMaterialForm()
    steps = [
        {"done": True, "what": "Materials added", "detail": "4 materials: flour, sugar, wrapper, carton.",
         "url": "#materials", "button": "See"},
        {"done": True, "what": "Biscuit recipe saved", "detail": "1 batch = 10 cartons.",
         "url": "#materials", "button": "See"},
        {"done": False, "what": "Put materials in stock", "detail": "No flour in stock yet.",
         "url": "#form", "button": "Stock in"},
        {"done": False, "what": "Make the first production", "detail": "", "url": "#form",
         "button": "Make"},
    ]
    return render(request, "base_template/ui_preview.html", {
        "form": form, "modal_form": DemoMaterialForm(auto_id="modal_%s"), "rows": SAMPLE_ROWS,
        "steps": steps, "action": reverse("ui_preview"),
    })
