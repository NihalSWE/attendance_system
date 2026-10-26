"""Production forms. Convenience only: ``production`` services re-check everything."""

from decimal import Decimal

from django import forms

from common.forms import StyledFormMixin, date_widget
from inventory.forms import CompanyForm
from inventory.models import Batch, Item, ItemKind, Location, Uom, Warehouse
from production import recipes
from production.models import Bom, BomLine, ConsumptionLine, Output, ProductionOrder


def _choice(model, label, required=True):
    return forms.ModelChoiceField(queryset=model.all_objects.none(), label=label, required=required)


class ServiceForm(CompanyForm):
    """The service resolves and validates; the form only collects."""

    def _post_clean(self):
        pass


class BomForm(ServiceForm):
    item = _choice(Item, "Finished product")
    batch_uom = _choice(Uom, "Batch unit")

    class Meta:
        model = Bom
        fields = ("item", "batch_size", "batch_uom", "effective_from", "note")
        widgets = {"effective_from": date_widget()}

    def limit_choices(self):
        self.fields["item"].queryset = self._active(Item).filter(kind=ItemKind.FINISHED).order_by("code")
        self.fields["batch_uom"].queryset = self._active(Uom).order_by("code")


class BomLineForm(ServiceForm):
    item = _choice(Item, "Material")
    uom = _choice(Uom, "Unit")

    class Meta:
        model = BomLine
        fields = ("item", "qty", "uom", "scrap_pct", "is_packaging", "note")

    def limit_choices(self):
        self.fields["item"].queryset = self._active(Item).order_by("code")
        self.fields["uom"].queryset = self._active(Uom).order_by("code")
        self.fields["scrap_pct"].required = False


class OrderForm(ServiceForm):
    """A production order (2026-10-26: no Unit - the product's own; no
    production line or shift; the stores chosen already)."""

    item = _choice(Item, "Product")
    material_warehouse = _choice(Warehouse, "Materials from store")
    output_warehouse = _choice(Warehouse, "Finished goods into store")

    class Meta:
        model = ProductionOrder
        fields = ("item", "planned_qty", "planned_date", "material_warehouse", "output_warehouse", "note")
        widgets = {"planned_date": date_widget()}
        labels = {"planned_qty": "How many to make"}
        help_texts = {"item": "Finished goods with materials set; their recipe in use is followed.",
                      "planned_qty": "In the product's own unit."}

    def __init__(self, *args, branch_ids=None, **kwargs):
        self.branch_ids = branch_ids
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            # The usual stores, already chosen: the first raw material store
            # for materials, the first finished goods store for the output.
            for field, kind in (("material_warehouse", "raw"), ("output_warehouse", "finished")):
                store = self.fields[field].queryset.filter(kind=kind).first()
                if store is not None:
                    self.initial.setdefault(field, store.pk)

    def clean(self):
        data = super().clean()
        if data.get("item") is not None:
            data["uom"] = data["item"].base_uom
        return data

    def limit_choices(self):
        products = Bom.all_objects.filter(company_id=self.company_id, status=Bom.Status.ACTIVE).values("item_id")
        self.fields["item"].queryset = (self._active(Item).filter(pk__in=products)
                                        .select_related("base_uom").order_by("code"))
        self.fields["item"].label_from_instance = lambda item: f"{item.code} · {item.name} ({item.base_uom.code})"
        stores = self._active(Warehouse).exclude(kind__in=["quarantine", "rejected", "transit"]).order_by("name")
        self.fields["material_warehouse"].queryset = stores
        out = stores if self.branch_ids is None else stores.filter(branch_id__in=self.branch_ids)
        self.fields["output_warehouse"].queryset = out


class ApiOrderForm(OrderForm):
    """The API's production order (api/v1/inventory/production.py): Unit,
    production line and shift are still accepted, as before 2026-10-26."""

    uom = _choice(Uom, "Unit")

    class Meta(OrderForm.Meta):
        fields = ("item", "planned_qty", "uom", "planned_date", "material_warehouse", "output_warehouse",
                  "line_name", "shift_name", "note")

    def clean(self):
        given = self.cleaned_data.get("uom")
        data = super().clean()
        if given is not None:
            data["uom"] = given   # the service checks it is the product's unit or pack
        return data

    def limit_choices(self):
        super().limit_choices()
        self.fields["uom"].queryset = self._active(Uom).order_by("code")


class ConsumptionLineForm(ServiceForm):
    item = _choice(Item, "Material")
    batch = _choice(Batch, "Batch (empty = first to expire)", required=False)
    location = _choice(Location, "Bin", required=False)
    uom = _choice(Uom, "Unit")

    class Meta:
        model = ConsumptionLine
        fields = ("item", "batch", "location", "qty", "uom", "not_fefo_reason", "note")

    def __init__(self, *args, consumption=None, **kwargs):
        self.consumption = consumption
        super().__init__(*args, **kwargs)

    def limit_choices(self):
        self.fields["item"].queryset = self._active(Item).order_by("code")
        self.fields["uom"].queryset = self._active(Uom).order_by("code")
        self.fields["batch"].queryset = Batch.all_objects.filter(company_id=self.company_id).select_related(
            "item").order_by("item__code", "expiry_date")
        self.fields["batch"].label_from_instance = lambda b: (
            f"{b.item.code} · {b.batch_no}" + (f" · exp {b.expiry_date:%d %b %Y}" if b.expiry_date else ""))
        store = self.consumption.order.material_warehouse if self.consumption else None
        self.fields["location"].queryset = (self._active(Location).filter(warehouse=store)
                                            if store else Location.all_objects.none())


class OutputForm(ServiceForm):
    """The finished run (2026-10-26: no Unit - the product's own; no
    manufacture date - the output date; batch number and expiry automatic)."""

    location = _choice(Location, "Bin", required=False)

    class Meta:
        model = Output
        fields = ("output_date", "qty_good", "qty_rejected", "batch_no", "expiry_date",
                  "location", "packages", "note")
        widgets = {"output_date": date_widget(), "expiry_date": date_widget()}
        help_texts = {"batch_no": "Leave empty: made automatically.",
                      "expiry_date": "Leave empty: worked out from the product's shelf life."}

    def __init__(self, *args, order=None, **kwargs):
        self.order = order
        super().__init__(*args, **kwargs)
        self.more_fields = ["batch_no", "expiry_date", "location", "packages", "note"]
        if order is not None:
            unit = order.item.base_uom.code
            self.fields["qty_good"].label = f"Good quantity ({unit})"
            self.fields["qty_rejected"].label = f"Rejected ({unit})"

    @property
    def more_has_errors(self):
        return any(name in self.errors for name in self.more_fields)

    def clean(self):
        data = super().clean()
        if self.order is not None:
            data["uom"] = self.order.item.base_uom
        return data

    def limit_choices(self):
        self.fields["location"].queryset = (self._active(Location).filter(warehouse=self.order.output_warehouse)
                                            if self.order else Location.all_objects.none())
        self.fields["qty_rejected"].required = False


class ApiOutputForm(OutputForm):
    """The API's output (api/v1/inventory/production.py): Unit and manufacture
    date are still accepted, as before 2026-10-26."""

    uom = _choice(Uom, "Unit")

    class Meta(OutputForm.Meta):
        fields = ("output_date", "qty_good", "qty_rejected", "uom", "batch_no", "mfg_date", "expiry_date",
                  "location", "packages", "note")
        widgets = {"output_date": date_widget(), "mfg_date": date_widget(), "expiry_date": date_widget()}

    def clean(self):
        given = self.cleaned_data.get("uom")
        data = super().clean()
        if given is not None:
            data["uom"] = given
        return data

    def limit_choices(self):
        super().limit_choices()
        self.fields["uom"].queryset = self._active(Uom).order_by("code")


class QcForm(StyledFormMixin, forms.Form):
    result = forms.ChoiceField(choices=(("passed", "Passed: release the batch"),
                                        ("failed", "Failed: block the batch")), label="Result")
    notes = forms.CharField(max_length=500, required=False, label="Notes",
                            help_text="Needed when it fails: what was wrong.")


class RecipeBatchForm(StyledFormMixin, forms.Form):
    """How much one batch of a finished good makes (production.recipes)."""

    batch_size = forms.DecimalField(label="One batch makes", min_value=Decimal("0.0001"),
                                    max_digits=18, decimal_places=4)


class RecipeMaterialForm(StyledFormMixin, forms.Form):
    """A material on a finished good's recipe, in the material's own unit."""

    material = forms.ModelChoiceField(queryset=Item.all_objects.none(), label="Material",
                                      empty_label="Choose a raw material or packaging")
    qty = forms.DecimalField(label="How much one batch uses", min_value=Decimal("0.0001"),
                             max_digits=18, decimal_places=4,
                             help_text="In the material's own unit, shown when you choose it.")
    scrap_pct = forms.DecimalField(label="Normal wastage %", required=False, min_value=0,
                                   max_value=100, max_digits=5, decimal_places=2,
                                   help_text="Optional: added on top when production is planned.")

    def __init__(self, *args, company_id, product, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["material"].queryset = (
            Item.all_objects.filter(company_id=company_id, status="active",
                                    kind__in=(ItemKind.RAW, ItemKind.PACKAGING))
            .exclude(pk=product.pk).exclude(pk__in=self._on_recipe(company_id, product))
            .select_related("base_uom").order_by("code"))
        self.fields["material"].label_from_instance = lambda item: f"{item.code} · {item.name}"
        # The material's unit beside the quantity (production/recipe_card.html).
        self.fields["material"].widget.option_attrs = lambda rows: {
            pk: {"data-unit": code} for pk, code in rows.values_list("pk", "base_uom__code")}

    @staticmethod
    def _on_recipe(company_id, product):
        """Materials already on the recipe are not offered again."""
        bom = recipes.current(company_id, product)
        return BomLine.all_objects.filter(bom=bom).values("item_id") if bom else []
