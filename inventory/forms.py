"""Inventory forms. Convenience only: the services re-check everything.

Database-backed choices start empty (the default managers are
tenant-scoped); each form is given the company and fills its own lists.
"""

from decimal import Decimal

from django import forms

from common.choices import ActiveStatus
from common.forms import StyledFormMixin, date_widget
from inventory.models import (
    Batch,
    InventorySettings,
    Item,
    ItemCategory,
    ItemKind,
    ItemUom,
    Location,
    OpeningStock,
    OpeningStockLine,
    Uom,
    Warehouse,
)
from organization.models import Branch
from purchasing.models import Supplier
from sales.models import Customer


class CompanyForm(StyledFormMixin, forms.ModelForm):
    """A ModelForm whose choice lists are limited to one company's active rows."""

    def __init__(self, *args, company_id, **kwargs):
        self.company_id = company_id
        super().__init__(*args, **kwargs)
        self.limit_choices()
        # StyledFormMixin ran before the querysets were set; nothing to redo.
        self.link_choices()
        self.setup_either()

    #: "This one OR that one" (2026-10-26): ``(radio name, label, ((value,
    #: label, (fields...)), ...))``. A choice comes first and shows only that
    #: side's fields; the other side's values are dropped when the form is
    #: read, so a hidden value never decides anything.
    EITHER = ()

    def setup_either(self):
        self.either = []
        for name, label, options in self.EITHER:
            options = [(value, text, [f for f in fields if f in self.fields])
                       for value, text, fields in options]
            options = [option for option in options if option[2]]
            if len(options) < 2:
                continue
            self.fields[name] = forms.ChoiceField(
                label=label, choices=[(value, text) for value, text, _ in options],
                required=False, widget=forms.RadioSelect(attrs={"class": "check__input"}),
                initial=self._either_start(options))
            for value, _, fields in options:
                for field in fields:
                    self.fields[field].widget.attrs["data-show-when"] = f"{self.add_prefix(name)}:{value}"
            self.order_fields([name] + [f for f in self.fields if f != name])
            self.either.append((name, options))

    def _either_start(self, options):
        """The side already filled (editing, or posted without the choice), else the first."""
        values = self.data if self.is_bound else self.initial
        for value, _, fields in options:
            if any(values.get(self.add_prefix(f) if self.is_bound else f) for f in fields):
                return value
        return options[0][0]

    def clean(self):
        data = super().clean()
        for name, options in getattr(self, "either", ()):
            chosen = data.get(name) or self._either_start(options)
            for value, _, fields in options:
                if value != chosen:
                    for field in fields:
                        data[field] = None
                        self.errors.pop(field, None)
            data.pop(name, None)  # the choice is the form's, not a value to save
        return data

    def limit_choices(self):
        pass

    def link_choices(self):
        """Lists that follow another field (2026-10-26): a Unit only the
        chosen item's own units, a Batch only its batches, a Bin only the
        chosen store's - so a list never offers what saving then refuses.
        The services still check every one (base_template/js/forms.js)."""
        item = self.fields.get("item")
        if (item is not None and getattr(item, "queryset", None) is not None
                and item.queryset.model is Item):
            item.widget.option_attrs = item_choice_attrs
            for unit in ("uom", "batch_uom"):
                if unit in self.fields:
                    self.fields[unit].widget.attrs["data-units-of"] = "item"
            # Batch boxes only for an item that keeps batches or expires.
            for name, needs in BATCH_ENTRY.items():
                if name in self.fields:
                    self.fields[name].widget.attrs["data-item-needs"] = needs
            if "batch" in self.fields and hasattr(self.fields["batch"], "queryset"):
                self.fields["batch"].widget.option_attrs = lambda rows: {
                    pk: {"data-item": str(item_id)} for pk, item_id in rows.values_list("pk", "item_id")}
                self.fields["batch"].widget.attrs.update({"data-follows": "item", "data-follow-by": "item"})
        for store, bins in (("warehouse", "location"), ("from_warehouse", "from_location"),
                            ("to_warehouse", "to_location")):
            if store in self.fields and bins in self.fields and hasattr(self.fields[bins], "queryset"):
                self.fields[bins].widget.option_attrs = lambda rows: {
                    pk: {"data-store": str(store_id)}
                    for pk, store_id in rows.values_list("pk", "warehouse_id")}
                self.fields[bins].widget.attrs.update({"data-follows": store, "data-follow-by": "store"})

    def _active(self, model, **filters):
        rows = model.all_objects.filter(company_id=self.company_id, **filters)
        if hasattr(model, "status"):
            current = getattr(self.instance, "pk", None)
            rows = rows.filter(status=ActiveStatus.ACTIVE) if not current else rows
        return rows

    def clean_code(self):
        return (self.cleaned_data.get("code") or "").strip().upper()


def _follow_kind(field):
    """A category list that shows only the categories of the chosen item type."""
    field.widget.option_attrs = lambda rows: {
        pk: {"data-kind": kind} for pk, kind in rows.values_list("pk", "kind")}
    field.widget.attrs.update({"data-follows": "kind", "data-follow-by": "kind"})


# A document line's batch boxes, and what the chosen item needs to show them.
BATCH_ENTRY = {"batch_no": "batch", "supplier_batch_no": "batch", "mfg_date": "batch",
               "expiry_date": "expiry"}


def item_choice_attrs(items):
    """``{item id: {data-units, data-base-unit, data-batch, data-expiry}}``:
    each item's own units - its base unit and its pack units - for the Unit
    list beside it, and whether it keeps batches and expires."""
    units, flags = {}, {}
    for pk, base, batch, expiry in items.values_list("pk", "base_uom_id", "track_batch", "track_expiry"):
        units[pk] = [base]
        flags[pk] = {"data-batch": "1" if batch or expiry else "", "data-expiry": "1" if expiry else ""}
    for item_id, uom_id in ItemUom.all_objects.filter(item_id__in=list(units)).values_list(
            "item_id", "uom_id"):
        units[item_id].append(uom_id)
    return {pk: {"data-units": ",".join(map(str, ids)), "data-base-unit": str(ids[0]), **flags[pk]}
            for pk, ids in units.items()}


class UomForm(CompanyForm):
    class Meta:
        model = Uom
        fields = ("code", "name", "kind", "decimals")
        help_texts = {"code": "Short and unique, e.g. KG, CARTON."}


class CategoryForm(CompanyForm):
    parent = forms.ModelChoiceField(
        queryset=ItemCategory.all_objects.none(), label="Parent category", required=False,
        empty_label="None (main category)",
        help_text="Leave as None for a main category. Choose one to make this a "
                  "sub-category of it. It must have the same item type.")

    class Meta:
        model = ItemCategory
        fields = ("code", "name", "kind", "parent")
        labels = {"kind": "Item type", "parent": "Parent category"}

    def limit_choices(self):
        rows = self._active(ItemCategory)
        if self.instance.pk:
            rows = rows.exclude(pk=self.instance.pk)
        self.fields["parent"].queryset = rows.order_by("kind", "name")
        _follow_kind(self.fields["parent"])
        self.fields["parent"].required = False


class ItemForm(CompanyForm):
    category = forms.ModelChoiceField(queryset=ItemCategory.all_objects.none(), label="Category")
    base_uom = forms.ModelChoiceField(queryset=Uom.all_objects.none(), label="Base unit",
                                      help_text="Stock is counted in this unit. Fixed once stock has moved.")
    default_warehouse = forms.ModelChoiceField(queryset=Warehouse.all_objects.none(), label="Usual store", required=False)

    class Meta:
        model = Item
        fields = (
            "code", "name", "name_bn", "kind", "category", "base_uom", "barcode",
            "track_batch", "track_expiry", "shelf_life_days", "qc_required", "batch_per_mix",
            "sourcing", "keep_by", "min_stock", "keep_days", "reorder_point", "reorder_qty",
            "max_stock", "default_warehouse", "standard_cost", "sale_price", "vat_rate", "hs_code",
            "notes",
        )
        labels = {
            "kind": "Item type", "base_uom": "Base unit", "track_batch": "Keeps batches",
            "track_expiry": "Has an expiry date", "shelf_life_days": "Shelf life (days)",
            "qc_required": "QC before use", "default_warehouse": "Usual store",
            "standard_cost": "Standard cost", "sale_price": "Sale price",
            "min_stock": "Stock to keep", "keep_days": "Days of sales to keep",
        }
        help_texts = {
            "barcode": "Only a barcode already printed on the product (EAN-13).",
            "shelf_life_days": "Fills the expiry from the manufacture date when only that is known.",
            "base_uom": "Stock is counted in this unit. Fixed once stock has moved.",
            "min_stock": "In the base unit. Below this it shows as low.",
            "keep_days": "Worked out from the average sold per day over the last 30 days.",
            "sale_price": "For one of the base unit; packs get their own price on the item's page.",
        }

    #: Shown on the Add form of each item type's page (2026-10-26); the rest
    #: are under "More details". Shelf life only once "Has an expiry date".
    MAIN = ("code", "name", "category", "base_uom", "track_batch", "track_expiry",
            "shelf_life_days", "default_warehouse", "min_stock")
    MAIN_FOR = {ItemKind.RAW: ("qc_required",), ItemKind.PACKAGING: ("qc_required",),
                ItemKind.FINISHED: ("sourcing", "sale_price", "vat_rate", "keep_by", "keep_days")}

    def __init__(self, *args, kind=None, **kwargs):
        self.kind = kind
        super().__init__(*args, **kwargs)
        self._for_kind(kind)
        main = set(self.MAIN) | set(self.MAIN_FOR.get(kind, ())) | {"kind"}
        self.more_fields = [name for name in self.fields if name not in main]
        self.fields["shelf_life_days"].widget.attrs["data-show-when"] = "track_expiry:on"
        self.fields["track_expiry"].widget.attrs["data-ticks"] = self.add_prefix("track_batch")
        if kind:
            # The page decides the type: no Item type box.
            self.fields["kind"].widget = forms.HiddenInput()
            self.fields["kind"].initial = kind
            if self.data:
                self.data = self.data.copy()
                self.data[self.add_prefix("kind")] = kind

    def _for_kind(self, kind):
        """How you get it and stock-to-keep-as-days are for finished goods only
        (2026-10-27); "How you get it" also only with Production on - without it
        everything is bought."""
        from inventory.access import production_on

        finished_possible = kind in (None, ItemKind.FINISHED)
        if not finished_possible or not production_on(self.company_id):
            del self.fields["sourcing"]
        else:
            self.fields["sourcing"].widget = forms.RadioSelect(choices=Item.Sourcing.choices,
                                                               attrs={"class": "check__input"})
            if not self.instance.pk:
                self.initial.setdefault("sourcing", Item.Sourcing.MAKE)   # a manufacturer's product
            if kind is None:
                self.fields["sourcing"].widget.attrs["data-show-when"] = "kind:finished"
        for name in ("sourcing", "keep_by"):
            if name in self.fields:
                self.fields[name].required = False   # the default when not said (clean)
        if not finished_possible or "sourcing" not in self.fields:
            # One batch number per mix: only for a product made here (2026-10-27).
            del self.fields["batch_per_mix"]
        else:
            self.fields["batch_per_mix"].widget.attrs["data-show-when"] = "track_batch:on"
            self.fields["batch_per_mix"].help_text = ("Off: one batch number per production. On: one per "
                                                      "mix, and the products go into one store.")
        if not finished_possible:
            del self.fields["keep_by"], self.fields["keep_days"]
        else:
            self.fields["keep_by"].widget = forms.RadioSelect(choices=Item.KeepBy.choices,
                                                              attrs={"class": "check__input"})
            self.fields["min_stock"].widget.attrs["data-show-when"] = "keep_by:quantity"
            self.fields["keep_days"].widget.attrs["data-show-when"] = "keep_by:days"

    @property
    def more_has_errors(self):
        return any(name in self.errors for name in self.more_fields)

    def clean(self):
        data = super().clean()
        # Only a finished good is made or kept by days of sales; anything else
        # (or a finished good that did not say) is bought and kept by quantity.
        finished = (data.get("kind") or self.kind) == ItemKind.FINISHED
        if "sourcing" in self.fields and (not finished or not data.get("sourcing")):
            data["sourcing"] = Item.Sourcing.BUY if not finished else (
                self.instance.sourcing if self.instance.pk else Item.Sourcing.MAKE)
        if "batch_per_mix" in self.fields and not finished:
            data["batch_per_mix"] = False
        if "keep_by" in self.fields and (not finished or not data.get("keep_by")):
            data["keep_by"] = Item.KeepBy.QUANTITY
            if not finished:
                data["keep_days"] = None
        return data

    def limit_choices(self):
        categories = self._active(ItemCategory).order_by("kind", "name")
        if getattr(self, "kind", None):
            self.fields["category"].queryset = categories.filter(kind=self.kind)
        else:
            self.fields["category"].queryset = categories
            # Only the chosen item type's categories (2026-10-26).
            _follow_kind(self.fields["category"])
        self.fields["base_uom"].queryset = self._active(Uom).order_by("code")
        self.fields["default_warehouse"].queryset = self._active(Warehouse).order_by("name")
        self.fields["default_warehouse"].required = False


class PackUnitForm(CompanyForm):
    uom = forms.ModelChoiceField(queryset=Uom.all_objects.none(), label="Pack unit")

    class Meta:
        model = ItemUom
        fields = ("uom", "factor", "sale_price", "barcode")
        labels = {"uom": "Pack unit", "sale_price": "Sale price of one pack"}
        help_texts = {"factor": "How many base units in one pack, e.g. 24.",
                      "sale_price": "Optional: its own price (a carton need not cost 24 packets). "
                                    "Empty: the item's price × the pack size.",
                      "barcode": "Only if the pack has its own barcode printed."}

    def __init__(self, *args, item=None, **kwargs):
        self.item = item
        super().__init__(*args, **kwargs)

    def limit_choices(self):
        units = self._active(Uom).order_by("code")
        if self.item is not None:
            # Not its own base unit, nor a pack it has already (2026-10-26).
            units = units.exclude(pk=self.item.base_uom_id).exclude(
                pk__in=ItemUom.all_objects.filter(item=self.item).values("uom_id"))
        self.fields["uom"].queryset = units


class WarehouseForm(CompanyForm):
    branch = forms.ModelChoiceField(queryset=Branch.all_objects.none(), label="Branch")

    class Meta:
        model = Warehouse
        fields = ("branch", "code", "name", "kind", "notes")
        labels = {"kind": "Store type"}

    def __init__(self, *args, branch_ids=None, **kwargs):
        self.branch_ids = branch_ids
        super().__init__(*args, **kwargs)

    def limit_choices(self):
        branches = Branch.all_objects.filter(company_id=self.company_id)
        if self.branch_ids is not None:
            branches = branches.filter(pk__in=self.branch_ids)
        self.fields["branch"].queryset = branches.order_by("-is_default", "name")


class LocationForm(CompanyForm):
    warehouse = forms.ModelChoiceField(queryset=Warehouse.all_objects.none(), label="Store")

    class Meta:
        model = Location
        fields = ("warehouse", "code", "description")
        labels = {"warehouse": "Store", "code": "Bin code"}
        help_texts = {"code": "Unique in the store, e.g. R1-S2-B3 (rack 1, shelf 2, bin 3)."}

    def __init__(self, *args, branch_ids=None, **kwargs):
        self.branch_ids = branch_ids
        super().__init__(*args, **kwargs)

    def limit_choices(self):
        stores = self._active(Warehouse)
        if self.branch_ids is not None:
            stores = stores.filter(branch_id__in=self.branch_ids)
        self.fields["warehouse"].queryset = stores.order_by("name")


class SupplierForm(CompanyForm):
    class Meta:
        model = Supplier
        fields = ("code", "name", "contact_person", "phone", "email", "address", "bin", "tin",
                  "payment_terms_days", "items_supplied", "notes")


class CustomerForm(CompanyForm):
    class Meta:
        model = Customer
        fields = ("code", "name", "customer_type", "contact_person", "phone", "whatsapp", "email",
                  "address", "territory", "credit_limit", "payment_terms_days", "price_list_name",
                  "notes")
        labels = {"customer_type": "Customer type"}


class StatusForm(StyledFormMixin, forms.Form):
    status = forms.ChoiceField(choices=ActiveStatus.choices)


class SettingsForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = InventorySettings
        fields = ("valuation_scope", "expiry_alert_days", "slow_moving_days", "alert_emails",
                  "small_label", "large_label", "gs1_labels")
        labels = {"valuation_scope": "Average cost", "expiry_alert_days": "Warn before expiry (days)"}
        widgets = {"alert_emails": forms.Textarea(attrs={"rows": 3})}

    def clean_alert_emails(self):
        from inventory.alerts import recipients

        text = self.cleaned_data.get("alert_emails", "")
        addresses = recipients(text)
        for address in addresses:
            forms.EmailField().clean(address)
        return "\n".join(addresses)


class OpeningStockForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = OpeningStock
        fields = ("work_date", "note")
        labels = {"work_date": "Opening day"}
        widgets = {"work_date": date_widget()}


class OpeningLineForm(CompanyForm):
    warehouse = forms.ModelChoiceField(queryset=Warehouse.all_objects.none(), label="Store")
    location = forms.ModelChoiceField(queryset=Location.all_objects.none(), label="Bin", required=False)
    item = forms.ModelChoiceField(queryset=Item.all_objects.none(), label="Item")
    uom = forms.ModelChoiceField(queryset=Uom.all_objects.none(), label="Unit")

    class Meta:
        model = OpeningStockLine
        fields = ("warehouse", "location", "item", "batch_no", "supplier_batch_no", "mfg_date",
                  "expiry_date", "qty", "uom", "unit_cost", "note")
        labels = {"warehouse": "Store", "location": "Bin", "batch_no": "Batch no.",
                  "supplier_batch_no": "Supplier batch no.", "uom": "Unit",
                  "mfg_date": "Manufacture date"}
        widgets = {"mfg_date": date_widget(), "expiry_date": date_widget()}
        help_texts = {"unit_cost": "Cost of 1 of the item's unit (e.g. 1 KG), or of 1 pack when a pack is chosen.",
                      "expiry_date": "Or give the manufacture date: the expiry is worked out from the item's shelf life."}

    def __init__(self, *args, branch_ids=None, **kwargs):
        self.branch_ids = branch_ids
        super().__init__(*args, **kwargs)

    def limit_choices(self):
        stores = self._active(Warehouse)
        if self.branch_ids is not None:
            stores = stores.filter(branch_id__in=self.branch_ids)
        self.fields["warehouse"].queryset = stores.order_by("name")
        self.fields["location"].queryset = self._active(Location, warehouse__in=stores).select_related(
            "warehouse").order_by("warehouse__code", "code")
        self.fields["location"].label_from_instance = lambda b: f"{b.warehouse.code} / {b.code}"
        self.fields["location"].required = False
        self.fields["item"].queryset = self._active(Item).order_by("code")
        self.fields["uom"].queryset = self._active(Uom).order_by("code")


class ReasonForm(StyledFormMixin, forms.Form):
    reason = forms.CharField(max_length=255, label="Why")


class ImportForm(StyledFormMixin, forms.Form):
    upload = forms.FileField(label="Filled template (.xlsx, .xls or .csv)")


class OpeningImportForm(StyledFormMixin, forms.Form):
    work_date = forms.DateField(label="Opening day", widget=date_widget())
    upload = forms.FileField(label="Filled opening stock template (.xlsx, .xls or .csv)")


class PrintForm(StyledFormMixin, forms.Form):
    copies = forms.IntegerField(min_value=1, max_value=20, initial=1, label="Copies of each")
    reason = forms.CharField(max_length=255, required=False, label="Why print again",
                             help_text="Needed when a label was printed before: torn, lost, unreadable.")


class VoidForm(StyledFormMixin, forms.Form):
    reason = forms.CharField(max_length=255, label="Why it is void",
                             help_text="A scan of it is refused with this reason.")


class PackageLabelForm(StyledFormMixin, forms.Form):
    """Labels for packages of one item (and batch): 25 bags of 50 KG."""

    batch = forms.ModelChoiceField(queryset=Batch.all_objects.none(), label="Batch", required=False)
    count = forms.IntegerField(min_value=1, max_value=500, initial=1, label="Packages")
    quantity = forms.DecimalField(min_value=Decimal("0.0001"), max_digits=18, decimal_places=4,
                                  label="In each package")
    uom = forms.ModelChoiceField(queryset=Uom.all_objects.none(), label="Unit")
    warehouse = forms.ModelChoiceField(queryset=Warehouse.all_objects.none(), label="Store",
                                       required=False)
    location = forms.ModelChoiceField(queryset=Location.all_objects.none(), label="Bin",
                                      required=False)

    def __init__(self, *args, item, branch_ids=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.item = item
        company_id = item.company_id
        self.fields["batch"].queryset = Batch.all_objects.filter(
            company_id=company_id, item=item).order_by("expiry_date", "batch_no")
        self.fields["batch"].required = item.track_batch
        units = [item.base_uom_id, *ItemUom.all_objects.filter(item=item).values_list("uom_id", flat=True)]
        self.fields["uom"].queryset = Uom.all_objects.filter(pk__in=units).order_by("code")
        stores = Warehouse.all_objects.filter(company_id=company_id, status=ActiveStatus.ACTIVE)
        if branch_ids is not None:
            stores = stores.filter(branch_id__in=branch_ids)
        self.fields["warehouse"].queryset = stores.order_by("name")
        bins = Location.all_objects.filter(company_id=company_id, warehouse__in=stores,
                                           status=ActiveStatus.ACTIVE).select_related("warehouse")
        self.fields["location"].queryset = bins.order_by("warehouse__code", "code")
        self.fields["location"].label_from_instance = lambda b: f"{b.warehouse.code} / {b.code}"

    def clean(self):
        data = super().clean()
        location, warehouse = data.get("location"), data.get("warehouse")
        if location is not None and warehouse is not None and location.warehouse_id != warehouse.pk:
            self.add_error("location", "This bin is in another store.")
        return data
