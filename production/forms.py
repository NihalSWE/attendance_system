"""Production forms. Convenience only: ``production`` services re-check everything."""

from django import forms

from common.forms import StyledFormMixin, date_widget
from inventory.forms import CompanyForm
from inventory.models import Batch, Item, ItemKind, Location, Uom, Warehouse
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
    item = _choice(Item, "Product")
    uom = _choice(Uom, "Unit")
    material_warehouse = _choice(Warehouse, "Materials from store")
    output_warehouse = _choice(Warehouse, "Finished goods into store")

    class Meta:
        model = ProductionOrder
        fields = ("item", "planned_qty", "uom", "planned_date", "material_warehouse", "output_warehouse",
                  "line_name", "shift_name", "note")
        widgets = {"planned_date": date_widget()}
        help_texts = {"item": "Its active BOM version is used."}

    def __init__(self, *args, branch_ids=None, **kwargs):
        self.branch_ids = branch_ids
        super().__init__(*args, **kwargs)

    def limit_choices(self):
        products = Bom.all_objects.filter(company_id=self.company_id, status=Bom.Status.ACTIVE).values("item_id")
        self.fields["item"].queryset = self._active(Item).filter(pk__in=products).order_by("code")
        self.fields["uom"].queryset = self._active(Uom).order_by("code")
        stores = self._active(Warehouse).exclude(kind__in=["quarantine", "rejected", "transit"]).order_by("name")
        self.fields["material_warehouse"].queryset = stores
        out = stores if self.branch_ids is None else stores.filter(branch_id__in=self.branch_ids)
        self.fields["output_warehouse"].queryset = out


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
    uom = _choice(Uom, "Unit")
    location = _choice(Location, "Bin", required=False)

    class Meta:
        model = Output
        fields = ("output_date", "qty_good", "qty_rejected", "uom", "batch_no", "mfg_date", "expiry_date",
                  "location", "packages", "note")
        widgets = {"output_date": date_widget(), "mfg_date": date_widget(), "expiry_date": date_widget()}
        help_texts = {"mfg_date": "Left empty: the output date."}

    def __init__(self, *args, order=None, **kwargs):
        self.order = order
        super().__init__(*args, **kwargs)

    def limit_choices(self):
        self.fields["uom"].queryset = self._active(Uom).order_by("code")
        self.fields["location"].queryset = (self._active(Location).filter(warehouse=self.order.output_warehouse)
                                            if self.order else Location.all_objects.none())
        self.fields["mfg_date"].required = False
        self.fields["qty_rejected"].required = False


class QcForm(StyledFormMixin, forms.Form):
    result = forms.ChoiceField(choices=(("passed", "Passed: release the batch"),
                                        ("failed", "Failed: block the batch")), label="Result")
    notes = forms.CharField(max_length=500, required=False, label="Notes",
                            help_text="Needed when it fails: what was wrong.")
