"""Production forms. Convenience only: ``production`` services re-check everything."""

from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q

from common.forms import StyledFormMixin, date_widget
from inventory.forms import CompanyForm
from inventory.models import Batch, Item, ItemCategory, ItemKind, Location, Uom, Warehouse
from production import recipes
from production.models import Bom, BomLine, ConsumptionLine, CostLine, CostRate, Output, ProductionOrder


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
                                        .exclude(sourcing=Item.Sourcing.BUY)
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

    batch_size = forms.DecimalField(label="The recipe is for", min_value=Decimal("0.0001"),
                                    max_digits=18, decimal_places=4)


class RecipeMaterialForm(StyledFormMixin, forms.Form):
    """A material on a finished good's recipe, in the material's own unit:
    one already added, or (2026-10-26, for someone who may set up items) a new
    raw material or packaging item made right here."""

    NEW = ("new_kind", "new_code", "new_name", "new_category", "new_uom", "new_track_batch",
           "new_track_expiry")

    # Not said: one already added.
    source = forms.ChoiceField(label="Material", initial="existing", widget=forms.RadioSelect,
                               required=False,
                               choices=(("existing", "One already added"),
                                        ("new", "A new raw material or packaging item")))
    material = forms.ModelChoiceField(queryset=Item.all_objects.none(), label="Material",
                                      required=False, empty_label="Choose a material")
    new_kind = forms.ChoiceField(label="Type", required=False,
                                 choices=((ItemKind.RAW, "Raw material"), (ItemKind.PACKAGING, "Packaging")))
    new_code = forms.CharField(label="Code", required=False, max_length=50,
                               help_text="Short and unique, e.g. RM-0004.")
    new_name = forms.CharField(label="Name", required=False, max_length=200)
    new_category = forms.ModelChoiceField(queryset=ItemCategory.all_objects.none(), label="Category",
                                          required=False)
    new_uom = forms.ModelChoiceField(queryset=Uom.all_objects.none(), label="Unit", required=False,
                                     help_text="Stock is counted in this unit.")
    new_track_batch = forms.BooleanField(label="Keeps batches", required=False)
    new_track_expiry = forms.BooleanField(label="Has an expiry date", required=False)
    qty = forms.DecimalField(label="How much the recipe uses", min_value=Decimal("0.0001"),
                             max_digits=18, decimal_places=4,
                             help_text="In the material's own unit, shown when you choose it.")
    scrap_pct = forms.DecimalField(label="Normal wastage %", required=False, min_value=0,
                                   max_value=100, max_digits=5, decimal_places=2,
                                   help_text="Optional: added on top when production is planned.")

    def __init__(self, *args, company_id, product, can_create=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.can_create = can_create
        self.fields["material"].queryset = (
            Item.all_objects.filter(company_id=company_id, status="active")
            .filter(Q(kind__in=(ItemKind.RAW, ItemKind.PACKAGING))
                    # A product made here (semi-finished, 2026-10-27); a loop is refused on save.
                    | Q(kind=ItemKind.FINISHED, sourcing__in=(Item.Sourcing.MAKE, Item.Sourcing.BOTH)))
            .exclude(pk=product.pk).exclude(pk__in=self._on_recipe(company_id, product))
            .select_related("base_uom").order_by("kind", "code"))
        self.fields["material"].label_from_instance = lambda item: f"{item.code} · {item.name}" + (
            " (made here)" if item.sourcing != Item.Sourcing.BUY else "")
        # The material's unit beside the quantity (inventory/hub/_recipe.html).
        self.fields["material"].widget.option_attrs = lambda rows: {
            pk: {"data-unit": code} for pk, code in rows.values_list("pk", "base_uom__code")}
        if not can_create:
            for name in ("source", *self.NEW):
                del self.fields[name]
            self.fields["material"].required = True
            return
        self.fields["material"].label = "Which one"
        self.fields["material"].widget.attrs["data-show-when"] = "source:existing"
        # Ones already added are chosen several at once, each with its own
        # quantity (inventory/hub/_recipe.html); these two are for a new one.
        for name in ("qty", "scrap_pct"):
            self.fields[name].widget.attrs["data-show-when"] = "source:new"
        for name in self.NEW:
            self.fields[name].widget.attrs["data-show-when"] = "source:new"
        for name in ("new_code", "new_name", "new_category", "new_uom"):
            self.fields[name].show_required = True    # needed for a new one (clean)
        # Only the chosen type's categories (base_template/js/forms.js follow).
        category = self.fields["new_category"]
        category.queryset = (ItemCategory.all_objects.filter(
            company_id=company_id, status="active", kind__in=(ItemKind.RAW, ItemKind.PACKAGING))
            .order_by("name"))
        category.widget.option_attrs = lambda rows: {
            pk: {"data-kind": kind} for pk, kind in rows.values_list("pk", "kind")}
        category.widget.attrs.update({"data-follows": "new_kind", "data-follow-by": "kind"})
        self.fields["new_uom"].queryset = Uom.all_objects.filter(
            company_id=company_id, status="active").order_by("code")
        self.fields["new_track_expiry"].widget.attrs["data-ticks"] = "new_track_batch"

    def clean(self):
        data = super().clean()
        if self.can_create and data.get("source") == "new":
            data.pop("material", None)
            self.errors.pop("material", None)
            for name in ("new_code", "new_name", "new_category", "new_uom"):
                if not data.get(name) and name not in self.errors:
                    self.add_error(name, "Give this for the new material.")
            if data.get("new_track_expiry"):
                data["new_track_batch"] = True
        else:
            for name in self.NEW:
                data.pop(name, None)
                self.errors.pop(name, None)
            if data.get("material") is None and "material" not in self.errors:
                self.add_error("material", "Choose the material.")
        data.pop("source", None)
        return data

    def new_item_values(self):
        """The new material's values for ``inventory.masters.create``."""
        data = self.cleaned_data
        return {"kind": data["new_kind"], "code": data["new_code"].strip().upper(),
                "name": data["new_name"].strip(), "category": data["new_category"],
                "base_uom": data["new_uom"], "track_batch": data["new_track_batch"],
                "track_expiry": data["new_track_expiry"]}

    @staticmethod
    def _on_recipe(company_id, product):
        """Materials already on the recipe are not offered again."""
        bom = recipes.current(company_id, product)
        return BomLine.all_objects.filter(bom=bom).values("item_id") if bom else []


# --- Cost rates and a product's costs (2026-10-27, plan §7.4) -----------------

class CostRateForm(CompanyForm):
    class Meta:
        model = CostRate
        fields = ("name", "kind", "rate")
        labels = {"name": "Name", "kind": "For", "rate": "Tk per hour"}
        help_texts = {"name": "e.g. Line worker, Supervisor, Oven.",
                      "rate": "A change counts for productions after it."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["rate"].unit_text = "Tk / hour"


class CostLineForm(CompanyForm):
    """One cost of a batch besides its materials: only the boxes its way of
    working out needs are shown (and kept)."""

    rate = forms.ModelChoiceField(queryset=CostRate.all_objects.none(), required=False, label="Rate",
                                  empty_label="Choose a rate")

    class Meta:
        model = CostLine
        fields = ("how", "rate", "people", "hours", "amount", "percent", "label")
        labels = {"how": "How it is worked out", "people": "People", "hours": "Hours per batch",
                  "amount": "Amount", "percent": "Percent",
                  "label": "Name (optional)"}
        help_texts = {"label": "Left empty: the rate's name (VAT for VAT).",
                      "hours": "For the recipe's quantity.",
                      "amount": "For the recipe's quantity: a production of 12.5 × the recipe counts it 12.5 times.",
                      "percent": "A % of the materials: of the materials only. VAT: of the materials and every "
                                 "other cost (worked out last)."}

    SHOW = {"rate": "how:labour,machine", "people": "how:labour", "hours": "how:labour,machine",
            "amount": "how:fixed", "percent": "how:percent,vat"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, when in self.SHOW.items():
            self.fields[name].widget.attrs["data-show-when"] = when
            self.fields[name].show_required = True   # needed when shown (CostLine.clean)
        self.fields["how"].widget.attrs["class"] = "input select js-select"
        self.fields["amount"].unit_text = "Tk"
        self.fields["percent"].unit_text = "%"
        self.fields["hours"].unit_text = "h"
        self.fields["people"].unit_text = "people"

    def limit_choices(self):
        rates = CostRate.all_objects.filter(company_id=self.company_id, status="active").order_by("kind", "name")
        self.fields["rate"].queryset = rates
        self.fields["rate"].label_from_instance = lambda rate: f"{rate.name} · {rate.rate.normalize():f} / h"
        # People rates for people, machine rates for machines (forms.js follow).
        self.fields["rate"].widget.option_attrs = lambda rows: {
            pk: {"data-how": kind} for pk, kind in rows.values_list("pk", "kind")}
        self.fields["rate"].widget.attrs.update({"data-follows": "how", "data-follow-by": "how"})


class PlanForm(StyledFormMixin, forms.Form):
    """Plan a production from the product's page (2026-10-27, plan §8.2): how
    much to make (2026-11-03: any quantity, in the product's unit - one
    production is one batch), when, and which store the products go into. The
    materials are the recipe's, scaled to the quantity (production.planning)."""

    qty = forms.DecimalField(label="How much to make", max_digits=18, decimal_places=4)
    planned_date = forms.DateField(label="Date", widget=date_widget())
    into = forms.ModelChoiceField(queryset=Warehouse.all_objects.none(), label="Products go into",
                                  empty_label=None)

    def __init__(self, *args, stores, product, recipe_for="", **kwargs):
        super().__init__(*args, **kwargs)
        self.product = product
        self.fields["into"].queryset = Warehouse.all_objects.filter(pk__in=[store.pk for store in stores])
        self.fields["into"].label_from_instance = lambda store: f"{store.name} ({store.branch.name})"
        self.fields["qty"].unit_text = product.base_uom.code
        self.fields["qty"].help_text = (f"Any amount: the recipe is for {recipe_for}, and every material is worked "
                                        "out for this quantity." if recipe_for else "")
        self.fields["into"].help_text = "Materials come from the stores of the same branch."

    def clean_qty(self):
        from production import planning

        qty = self.cleaned_data["qty"]
        try:
            planning.check_qty(self.product, qty)
        except ValidationError as exc:
            raise forms.ValidationError(exc.message_dict["qty"]) from None
        return qty


class BottleSizeForm(StyledFormMixin, forms.Form):
    """A bottle size of a made product (2026-11-03, production.bottling): how
    much one holds, its bottle, cap and label, and optionally its bottling
    cost, sale price and carton."""

    size = forms.DecimalField(label="One bottle holds", min_value=Decimal("0.0001"), max_digits=18,
                              decimal_places=4)
    containers = forms.ModelMultipleChoiceField(
        queryset=Item.all_objects.none(), label="Empty bottle, cap, label", required=False,
        help_text="The empty bottle you fill (its cost is what you paid for it), its cap and label: one of "
                  "each goes into every bottle. More can be added on its recipe later.")
    unit = forms.ModelChoiceField(queryset=Uom.all_objects.none(), label="Counted in", empty_label=None)
    name = forms.CharField(label="Name", max_length=255, required=False)
    code = forms.CharField(label="Code", max_length=64, required=False,
                           help_text="Left empty, the next free code.")
    bottling_cost = forms.DecimalField(label="Bottling cost (optional)", min_value=Decimal("0"),
                                       max_digits=18, decimal_places=2, required=False,
                                       help_text="Filling and sealing one bottle: people, machine, power.")
    sale_price = forms.DecimalField(label="Sale price of one filled bottle (optional)", min_value=Decimal("0"),
                                    max_digits=18, decimal_places=2, required=False,
                                    help_text="What a customer pays for one bottle of juice. Look at its cost "
                                              "above first, or set it later on the bottle's own page.")
    carton_factor = forms.DecimalField(label="Bottles in a carton (optional)", min_value=Decimal("1"),
                                       max_digits=18, decimal_places=0, required=False)

    #: Unit codes taken for a carton, the first that the company has.
    CARTONS = ("CARTON", "CTN", "BOX", "CASE")

    def __init__(self, *args, company_id, product, **kwargs):
        # Its own ids: the product's Edit form on the same page has a sale price and a code too.
        kwargs.setdefault("prefix", "bottle")
        super().__init__(*args, **kwargs)
        self.product = product
        units = Uom.all_objects.filter(company_id=company_id, status="active", kind=Uom.Kind.COUNT)
        self.fields["unit"].queryset = units.exclude(code__in=self.CARTONS)
        pcs = units.filter(code__iexact="PCS").first()
        if pcs is not None:
            self.fields["unit"].initial = pcs.pk
        by_code = {uom.code.upper(): uom for uom in units.filter(code__in=self.CARTONS)}
        self.carton = next((by_code[code] for code in self.CARTONS if code in by_code), None)
        if self.carton is None:
            del self.fields["carton_factor"]
        else:
            self.fields["carton_factor"].label = f"Bottles in a {self.carton.code.lower()} (optional)"
        self.fields["containers"].queryset = (
            Item.all_objects.filter(company_id=company_id, status="active", kind__in=recipes.MATERIAL_KINDS)
            .select_related("base_uom").order_by("kind", "name"))
        self.fields["containers"].label_from_instance = lambda item: f"{item.code} · {item.name}"
        self.fields["containers"].widget.attrs["data-placeholder"] = "Search: bottle 250 ml, cap, label"
        self.fields["size"].unit_text = product.base_uom.code
        self.fields["size"].help_text = f"How much {product.name} goes into one bottle: 250, 500, 1000."
        self.fields["name"].help_text = f"Left empty: {product.name} and the size, e.g. " \
                                        f"\"{product.name} 250 {product.base_uom.code.lower()}\"."
        for name in ("bottling_cost", "sale_price"):
            self.fields[name].unit_text = "Tk"
        # The cost of one filled bottle as you choose (2026-11-03): the same
        # costs the bottle's own cost sheet uses (production.costing.unit_cost).
        from production.costing import unit_cost

        self.bulk_cost = unit_cost(company_id, product)
        self.costs = {}
        for item in self.fields["containers"].queryset:
            cost = unit_cost(company_id, item)
            if cost is not None:
                self.costs[str(item.pk)] = f"{cost:f}"

    @property
    def costs_json(self):
        import json

        return json.dumps(self.costs)

    def service_values(self):
        data = self.cleaned_data
        return {"size": data["size"], "unit": data["unit"], "containers": list(data["containers"]),
                "name": data["name"], "code": data["code"], "bottling_cost": data["bottling_cost"],
                "sale_price": data["sale_price"], "carton": self.carton,
                "carton_factor": data.get("carton_factor")}
