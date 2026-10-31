"""The Add and Edit forms of the Materials and Products pages (2026-10-27,
docs/inventory/PRODUCTION_PLAN.md §4.2, §7.1, §7.2).

Each field is asked once: the unit is chosen once and every quantity box after
it shows that unit beside it; a code is suggested when left empty; the bigger
unit it is bought or sold in (1 bag = 50 kg, 1 carton = 24 packets at its own
price) is given on the same form. The services check everything again
(``inventory.masters``).
"""

from django import forms

from inventory import hub
from inventory.forms import ItemForm
from inventory.models import ItemKind, Uom

PACK_FIELDS = ("pack_uom", "pack_factor", "pack_price")


def _unit_attrs(rows):
    """Each unit option says its own code, for the boxes that show it."""
    return {pk: {"data-unit": code} for pk, code in rows.values_list("pk", "code")}


class _HubItemForm(ItemForm):
    #: The boxes on the form, in order; the rest go under "More details".
    MAIN_BOXES = ()
    #: On/off settings, drawn as switches.
    SWITCHES = ("track_batch", "track_expiry", "shelf_life_days", "qc_required", "batch_per_mix",
                "warranty_months", "serial_numbers")
    #: ``{box: name of the box whose unit is shown beside it}``.
    UNIT_FROM = {"min_stock": "base_uom", "pack_factor": "base_uom", "sale_price": "base_uom",
                 "pack_price": "pack_uom"}
    #: ``{box: fixed words beside it}``.
    UNIT_TEXT = {"shelf_life_days": "days", "keep_days": "days", "vat_rate": "%", "warranty_months": "months"}
    BATCH_HINT = "Each delivery gets its own batch number."
    PACK_LABELS = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        adding = not self.instance.pk
        if adding:
            self.fields["pack_uom"] = forms.ModelChoiceField(
                queryset=Uom.all_objects.filter(company_id=self.company_id, status="active").order_by("code"),
                required=False, label=self.PACK_LABELS.get("pack_uom", "Bigger unit (optional)"))
            self.fields["pack_factor"] = forms.DecimalField(
                required=False, min_value=0, max_digits=18, decimal_places=4,
                label="One of those holds", help_text=self.PACK_LABELS.get("pack_factor_help", ""))
            self.fields["pack_uom"].widget.attrs["class"] = "input select js-select"
            self.fields["pack_factor"].widget.attrs.update({"class": "input", "step": "any"})
        self.fields["base_uom"].widget.option_attrs = _unit_attrs
        if "pack_uom" in self.fields:
            self.fields["pack_uom"].widget.option_attrs = _unit_attrs
        # A code is suggested, not demanded.
        self.fields["code"].required = False
        self.fields["code"].help_text = "Left empty, the next number is given."
        if adding:
            self.fields["code"].widget.attrs["placeholder"] = hub.suggest_code(
                self.company_id, self.kind or ItemKind.RAW)
        hints = {"track_batch": self.BATCH_HINT,
                 "track_expiry": "Turns batches on; expired stock can never be used.",
                 "qc_required": "New stock waits until it is checked."}
        for name, hint in hints.items():
            self.fields[name].help_text = hint
        self.fields["shelf_life_days"].label = "Shelf life"
        self.fields["shelf_life_days"].help_text = ""
        for name, other in self.UNIT_FROM.items():
            if name in self.fields:
                self.fields[name].unit_from = other
                if other == "base_uom" and self.instance.pk:
                    self.fields[name].unit_text = self.instance.base_uom.code
        for name, text in self.UNIT_TEXT.items():
            if name in self.fields:
                self.fields[name].unit_text = text
        self.fields["qc_required"].label = "Quality check before use"

    #: ``[(section title, one-line hint, (box names...))]``, in order (2026-10-31);
    #: a box not named and not left out goes under "More details".
    SECTIONS = ()
    #: Never on these forms (the columns stay: imports and the API still fill them).
    LEFT_OUT = ("name_bn", "reorder_point", "max_stock", "hs_code")
    #: Short, standard words (2026-10-31).
    LABELS = {
        "code": ("Item code", "Empty: the next number is given."),
        "category": ("Category", ""),
        "pack_factor": ("Contains", "How many base units are in one."),
        "min_stock": ("Minimum stock", "Below this the item shows Low."),
        "reorder_qty": ("Reorder quantity", "How much you usually order at once."),
        "default_warehouse": ("Default store", "Where it is normally kept."),
        "track_batch": ("Batch tracking", "Each delivery or production gets its own batch number."),
        "track_expiry": ("Expiry tracking", "Expired stock can never be used or sold."),
        "shelf_life_days": ("Shelf life", ""),
        "qc_required": ("QC before use", "New stock waits until it passes a quality check."),
        "batch_per_mix": ("Batch number per mix", "Off: one batch number per production."),
        "warranty_months": ("Warranty", "From the delivery date (sold) or received date (bought)."),
        "serial_numbers": ("Serial number per unit", "Each unit is scanned out on delivery."),
        "keep_by": ("Minimum stock as", ""),
        "keep_days": ("Days of sales", "From the average sold per day over the last 30 days."),
        "vat_rate": ("VAT", ""),
        "barcode": ("Barcode", "Only one already printed on the goods."),
        "notes": ("Notes", ""),
    }

    def lay_out(self):
        """The boxes in their sections, called last, once every label is set (a
        box is drawn with the label its field had when it was first taken)."""
        for name in self.LEFT_OUT:
            self.fields.pop(name, None)
        for name, (label, hint) in self.LABELS.items():
            if name in self.fields:
                self.fields[name].label = label
                self.fields[name].help_text = hint
        self._bound_fields_cache.clear()
        placed = {name for _title, _hint, names in self.SECTIONS for name in names}
        self.sections = []
        for title, hint, names in self.SECTIONS:
            boxes = [self[name] for name in names if name in self.fields]
            if boxes:
                self.sections.append({"title": title, "hint": hint, "boxes": boxes})
        self.more_fields = [name for name in self.fields if name not in placed and name != "kind"
                            and not self.fields[name].widget.is_hidden]
        self.more_boxes = [self[name] for name in self.more_fields]

    def clean(self):
        data = super().clean()
        pack, size = data.get("pack_uom"), data.get("pack_factor")
        if "pack_uom" in self.fields:
            if pack and not size:
                self.add_error("pack_factor", "Say how many one of those holds.")
            elif size and not pack:
                self.add_error("pack_uom", "Choose the bigger unit, or empty the size.")
            elif pack and size is not None and size <= 1:
                self.add_error("pack_factor", "A bigger unit holds more than one.")
            if pack and pack == data.get("base_uom"):
                self.add_error("pack_uom", "Choose a unit other than the item's own unit.")
        if "pack_price" in self.fields and data.get("pack_price") and not pack:
            self.add_error("pack_uom", "Choose the unit this price is for.")
        if not data.get("code") and data.get("kind"):
            data["code"] = hub.suggest_code(self.company_id, data["kind"])
        return data

    def item_values(self):
        """What the item itself saves (not the bigger unit)."""
        return {name: value for name, value in self.cleaned_data.items() if name not in PACK_FIELDS}

    def pack_values(self):
        """The bigger unit, or None."""
        data = self.cleaned_data
        if not data.get("pack_uom"):
            return None
        values = {"uom": data["pack_uom"], "factor": data["pack_factor"]}
        if data.get("pack_price") is not None:
            values["sale_price"] = data["pack_price"]
        return values


class MaterialForm(_HubItemForm):
    """A raw material or packaging item (Production on)."""

    SECTIONS = (
        ("Item", "What it is.", ("kind", "name", "code", "category")),
        ("Units", "How it is counted and bought.", ("base_uom", "pack_uom", "pack_factor")),
        ("Stock control", "When it runs low, and where it is kept.",
         ("min_stock", "reorder_qty", "default_warehouse", "standard_cost")),
        ("Batches & expiry", "For food, chemicals and anything that expires.",
         ("track_batch", "track_expiry", "shelf_life_days", "qc_required")),
        ("Warranty", "The supplier's warranty on what you buy.", ("warranty_months", "serial_numbers")),
    )
    PACK_LABELS = {"pack_uom": "Purchase unit", "pack_factor_help": ""}

    def __init__(self, *args, **kwargs):
        kwargs.pop("kind", None)
        super().__init__(*args, kind=None, **kwargs)
        self.fields["kind"].choices = [(k, label) for k, label in ItemKind.choices if k in hub.MATERIAL_KINDS]
        self.fields["kind"].label = "Type"
        self.fields["kind"].widget.attrs["class"] = "input select js-select"
        self.fields["category"].queryset = self.fields["category"].queryset.filter(kind__in=hub.MATERIAL_KINDS)
        self.fields["name"].label = "Material name"
        self.fields["base_uom"].label = "Base unit"
        self.fields["base_uom"].help_text = "Stock is counted in this unit; fixed once stock moves."
        if "pack_uom" in self.fields:
            self.fields["pack_uom"].help_text = "Optional: the unit you buy in, e.g. BAG."
        for name in ("sourcing", "keep_by", "keep_days", "sale_price", "vat_rate", "batch_per_mix"):
            self.fields.pop(name, None)
        # A material keeps a quantity: no "as days of sales" choice to wait for.
        self.fields["min_stock"].widget.attrs.pop("data-show-when", None)
        # An estimate only (2026-10-31): costs a product before the first purchase;
        # the real buy price always comes from Stock in.
        self.fields["standard_cost"].label = "Expected buy price"
        self.fields["standard_cost"].help_text = "Optional estimate; the real price comes from Stock in."
        self.fields["standard_cost"].unit_text = (
            f"Tk / {self.instance.base_uom.code}" if self.instance.pk else "Tk / base unit")
        self.lay_out()


class ProductForm(_HubItemForm):
    """Something you sell: made here, bought ready, or both."""

    SECTIONS = (
        ("Item", "What it is, and how you get it.", ("sourcing", "name", "code", "category")),
        ("Units and price", "The unit you sell, its price, and a bigger pack.",
         ("base_uom", "sale_price", "pack_uom", "pack_factor", "pack_price", "vat_rate")),
        ("Stock control", "When it runs low, and where new stock goes.",
         ("keep_by", "min_stock", "keep_days", "reorder_qty", "default_warehouse")),
        ("Batches & expiry", "For food, medicine and anything that expires.",
         ("track_batch", "track_expiry", "shelf_life_days", "qc_required", "batch_per_mix")),
        ("Warranty", "Your warranty to the customer.", ("warranty_months", "serial_numbers")),
    )
    BATCH_HINT = "Each production or delivery gets its own batch number."
    PACK_LABELS = {"pack_uom": "Selling pack", "pack_factor_help": ""}
    LABELS = {**_HubItemForm.LABELS,
              "min_stock": ("Minimum stock", "Below this it shows Low; used to suggest how much to make."),
              "default_warehouse": ("New stock goes to", "The store new stock goes into.")}

    def __init__(self, *args, **kwargs):
        kwargs.pop("kind", None)
        super().__init__(*args, kind=ItemKind.FINISHED, **kwargs)
        if not self.instance.pk:
            self.fields["pack_price"] = forms.DecimalField(
                required=False, min_value=0, max_digits=18, decimal_places=4, label="Pack price",
                help_text="Its own price: a carton need not cost 24 x the packet price.",
                widget=forms.NumberInput(attrs={"class": "input", "step": "any"}))
            self.fields["pack_price"].unit_from = "pack_uom"
            self.fields["pack_uom"].help_text = "Optional: a bigger unit you also sell, e.g. CARTON."
        self.fields["name"].label = "Product name"
        self.fields["base_uom"].label = "Base unit"
        self.fields["base_uom"].help_text = "The smallest unit you sell (packet, piece)."
        self.fields["sale_price"].required = True
        self.fields["sale_price"].label = "Sale price"
        self.fields["sale_price"].help_text = "For one base unit."
        self.fields.pop("standard_cost", None)
        self.lay_out()



def form_for(item, *args, **kwargs):
    """The form an existing item is edited with."""
    form = ProductForm if item.kind == ItemKind.FINISHED else MaterialForm
    return form(*args, instance=item, **kwargs)


def is_material(item):
    return item.kind in hub.MATERIAL_KINDS

