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
    SWITCHES = ("track_batch", "track_expiry", "shelf_life_days", "qc_required", "batch_per_mix")
    #: ``{box: name of the box whose unit is shown beside it}``.
    UNIT_FROM = {"min_stock": "base_uom", "pack_factor": "base_uom", "sale_price": "base_uom",
                 "pack_price": "pack_uom"}
    #: ``{box: fixed words beside it}``.
    UNIT_TEXT = {"shelf_life_days": "days", "keep_days": "days", "vat_rate": "%"}
    BATCH_HINT = "Each delivery gets its own batch number."
    PACK_LABELS = {}
    #: Shown under the "Stock to keep as" choice, not in the main grid.
    KEEP_BOXES = ()

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

    def lay_out(self):
        """The boxes in their places; called last, once every label is set (a
        box is drawn with the label its field had when it was first taken)."""
        self._bound_fields_cache.clear()
        self.main_boxes = [self[name] for name in self.MAIN_BOXES if name in self.fields
                           and name not in ("sourcing", "keep_by", *self.KEEP_BOXES)]
        self.keep_boxes = [self[name] for name in self.KEEP_BOXES if name in self.fields]
        self.switch_boxes = [self[name] for name in self.SWITCHES if name in self.fields]
        self.more_fields = [name for name in self.fields if name not in self.MAIN_BOXES
                            and name not in self.SWITCHES and name != "kind"]
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

    MAIN_BOXES = ("kind", "name", "code", "category", "base_uom", "pack_uom", "pack_factor",
                  "min_stock", "default_warehouse")
    PACK_LABELS = {"pack_uom": "Bought in (optional)", "pack_factor_help": "1 bag = 50 kg: write 50."}

    def __init__(self, *args, **kwargs):
        kwargs.pop("kind", None)
        super().__init__(*args, kind=None, **kwargs)
        self.fields["kind"].choices = [(k, label) for k, label in ItemKind.choices if k in hub.MATERIAL_KINDS]
        self.fields["kind"].label = "Type"
        self.fields["kind"].widget.attrs["class"] = "input select js-select"
        self.fields["category"].queryset = self.fields["category"].queryset.filter(kind__in=hub.MATERIAL_KINDS)
        self.fields["base_uom"].label = "Unit"
        self.fields["min_stock"].help_text = "Below this it shows as low and is in the morning email."
        for name in ("sourcing", "keep_by", "keep_days", "sale_price", "vat_rate", "batch_per_mix"):
            self.fields.pop(name, None)
        # A material keeps a quantity: no "as days of sales" choice to wait for.
        self.fields["min_stock"].widget.attrs.pop("data-show-when", None)
        self.lay_out()


class ProductForm(_HubItemForm):
    """Something you sell: made here, bought ready, or both."""

    MAIN_BOXES = ("sourcing", "name", "code", "category", "base_uom", "sale_price", "pack_uom",
                  "pack_factor", "pack_price", "vat_rate", "default_warehouse", "keep_by",
                  "min_stock", "keep_days")
    BATCH_HINT = "Each production or delivery gets its own batch number."
    PACK_LABELS = {"pack_uom": "Also sold as (optional)", "pack_factor_help": "1 carton = 24 packets: write 24."}
    KEEP_BOXES = ("min_stock", "keep_days")

    def __init__(self, *args, **kwargs):
        kwargs.pop("kind", None)
        super().__init__(*args, kind=ItemKind.FINISHED, **kwargs)
        if not self.instance.pk:
            self.fields["pack_price"] = forms.DecimalField(
                required=False, min_value=0, max_digits=18, decimal_places=4, label="Its sale price",
                help_text="Its own price: a carton need not cost 24 × the packet price.",
                widget=forms.NumberInput(attrs={"class": "input", "step": "any"}))
            self.fields["pack_price"].unit_from = "pack_uom"
        self.fields["base_uom"].label = "Smallest unit you sell"
        self.fields["sale_price"].required = True
        self.fields["sale_price"].label = "Sale price"
        self.fields["sale_price"].help_text = "For one of the smallest unit."
        self.fields["default_warehouse"].label = "New stock goes to"
        self.fields["min_stock"].help_text = "Below this it shows as low; used to suggest how much to make."
        # Stock to keep sits under its own choice: a quantity, or days of sales.
        self.lay_out()

    @property
    def sourcing_box(self):
        return self["sourcing"] if "sourcing" in self.fields else None

    @property
    def keep_box(self):
        return self["keep_by"] if "keep_by" in self.fields else None


def form_for(item, *args, **kwargs):
    """The form an existing item is edited with."""
    form = ProductForm if item.kind == ItemKind.FINISHED else MaterialForm
    return form(*args, instance=item, **kwargs)


def is_material(item):
    return item.kind in hub.MATERIAL_KINDS

