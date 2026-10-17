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

    def limit_choices(self):
        pass

    def _active(self, model, **filters):
        rows = model.all_objects.filter(company_id=self.company_id, **filters)
        if hasattr(model, "status"):
            current = getattr(self.instance, "pk", None)
            rows = rows.filter(status=ActiveStatus.ACTIVE) if not current else rows
        return rows

    def clean_code(self):
        return (self.cleaned_data.get("code") or "").strip().upper()


class UomForm(CompanyForm):
    class Meta:
        model = Uom
        fields = ("code", "name", "kind", "decimals")
        help_texts = {"code": "Short and unique, e.g. KG, CARTON."}


class CategoryForm(CompanyForm):
    parent = forms.ModelChoiceField(queryset=ItemCategory.all_objects.none(), label="Inside category", required=False)

    class Meta:
        model = ItemCategory
        fields = ("code", "name", "kind", "parent")
        labels = {"kind": "Item type", "parent": "Inside category"}

    def limit_choices(self):
        rows = self._active(ItemCategory)
        if self.instance.pk:
            rows = rows.exclude(pk=self.instance.pk)
        self.fields["parent"].queryset = rows.order_by("kind", "name")
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
            "track_batch", "track_expiry", "shelf_life_days", "qc_required",
            "min_stock", "reorder_point", "reorder_qty", "max_stock", "default_warehouse",
            "standard_cost", "sale_price", "vat_rate", "hs_code", "notes",
        )
        labels = {
            "kind": "Item type", "base_uom": "Base unit", "track_batch": "Keeps batches",
            "track_expiry": "Has an expiry date", "shelf_life_days": "Shelf life (days)",
            "qc_required": "QC before use", "default_warehouse": "Usual store",
            "standard_cost": "Standard cost", "sale_price": "Sale price",
        }
        help_texts = {
            "barcode": "Only a barcode already printed on the product (EAN-13).",
            "shelf_life_days": "Fills the expiry from the manufacture date when only that is known.",
            "base_uom": "Stock is counted in this unit. Fixed once stock has moved.",
        }

    def limit_choices(self):
        self.fields["category"].queryset = self._active(ItemCategory).order_by("kind", "name")
        self.fields["base_uom"].queryset = self._active(Uom).order_by("code")
        self.fields["default_warehouse"].queryset = self._active(Warehouse).order_by("name")
        self.fields["default_warehouse"].required = False


class PackUnitForm(CompanyForm):
    uom = forms.ModelChoiceField(queryset=Uom.all_objects.none(), label="Pack unit")

    class Meta:
        model = ItemUom
        fields = ("uom", "factor", "barcode")
        labels = {"uom": "Pack unit"}
        help_texts = {"factor": "How many base units in one pack, e.g. 24.",
                      "barcode": "Only if the pack has its own barcode printed."}

    def limit_choices(self):
        self.fields["uom"].queryset = self._active(Uom).order_by("code")


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
        help_texts = {"unit_cost": "Cost of one unit written in Unit.",
                      "expiry_date": "Left empty, it is worked out from the item's shelf life."}

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
