"""Purchase forms. Convenience only: ``purchasing`` services re-check everything.

Every database-backed choice is declared with an empty list and filled per
company (the default managers are tenant-scoped), limited to active rows and,
where it matters, to branches the person may act in.
"""

from decimal import Decimal

from django import forms

from common.choices import ActiveStatus
from common.forms import StyledFormMixin, date_widget
from inventory.forms import CompanyForm
from inventory.models import Batch, Item, Location, Uom, Warehouse
from organization.models import Branch, Department
from purchasing.models import (
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseRequisition,
    PurchaseRequisitionLine,
    PurchaseReturn,
    PurchaseReturnLine,
    QualityCheck,
    Supplier,
)


def _choice(model, label, required=True):
    return forms.ModelChoiceField(queryset=model.all_objects.none(), label=label, required=required)


class BranchScoped(CompanyForm):
    """A form whose branch (or store) list is limited to ``branch_ids`` (None = all)."""

    def __init__(self, *args, branch_ids=None, **kwargs):
        self.branch_ids = branch_ids
        super().__init__(*args, **kwargs)

    def _branches(self):
        rows = Branch.all_objects.filter(company_id=self.company_id, status=ActiveStatus.ACTIVE)
        if self.branch_ids is not None:
            rows = rows.filter(pk__in=self.branch_ids)
        return rows.order_by("-is_default", "name")

    def _stores(self):
        rows = self._active(Warehouse)
        if self.branch_ids is not None:
            rows = rows.filter(branch_id__in=self.branch_ids)
        return rows.order_by("name")


class ItemLineMixin:
    """A document line: its item, or its order/receipt line, is resolved by the
    service, so the form leaves model validation to the service."""

    def _post_clean(self):
        pass

    def _items(self):
        self.fields["item"].queryset = self._active(Item).order_by("code")
        self.fields["uom"].queryset = self._active(Uom).order_by("code")


class RequisitionForm(BranchScoped):
    branch = _choice(Branch, "Branch")
    department = _choice(Department, "Department", required=False)

    class Meta:
        model = PurchaseRequisition
        fields = ("branch", "department", "request_date", "needed_by", "reason")
        labels = {"request_date": "Date", "reason": "Why it is needed"}
        widgets = {"request_date": date_widget(), "needed_by": date_widget()}

    def limit_choices(self):
        self.fields["branch"].queryset = self._branches()
        self.fields["department"].queryset = Department.all_objects.filter(
            company_id=self.company_id, branch__in=self._branches()).select_related("branch").order_by("name")
        self.fields["department"].label_from_instance = lambda d: f"{d.name} ({d.branch.name})"


class RequisitionLineForm(ItemLineMixin, CompanyForm):
    item = _choice(Item, "Item")
    uom = _choice(Uom, "Unit")

    class Meta:
        model = PurchaseRequisitionLine
        fields = ("item", "qty", "uom", "note")

    def limit_choices(self):
        self._items()


class NoteForm(StyledFormMixin, forms.Form):
    note = forms.CharField(max_length=255, required=False, label="Note")


class OrderForm(BranchScoped):
    supplier = _choice(Supplier, "Supplier")
    branch = _choice(Branch, "For branch")

    class Meta:
        model = PurchaseOrder
        fields = ("supplier", "branch", "order_date", "expected_date", "terms", "note")
        widgets = {"order_date": date_widget(), "expected_date": date_widget()}

    def limit_choices(self):
        self.fields["supplier"].queryset = self._active(Supplier).order_by("name")
        self.fields["branch"].queryset = self._branches()


class FromRequisitionsForm(OrderForm):
    requisition_lines = forms.ModelMultipleChoiceField(
        queryset=PurchaseRequisitionLine.all_objects.none(), label="Approved requisition lines",
        widget=forms.CheckboxSelectMultiple)

    def limit_choices(self):
        super().limit_choices()
        lines = PurchaseRequisitionLine.all_objects.filter(
            company_id=self.company_id, requisition__status=PurchaseRequisition.Status.APPROVED,
            requisition__branch__in=self._branches(),
        ).select_related("requisition", "item__base_uom", "uom").order_by("requisition__number", "pk")
        self.fields["requisition_lines"].queryset = lines
        self.fields["requisition_lines"].label_from_instance = lambda line: (
            f"{line.requisition.number} · {line.item.code} {line.item.name} · "
            f"{line.qty_open_base.normalize():f} {line.item.base_uom.code} open")


class OrderLineForm(ItemLineMixin, CompanyForm):
    item = _choice(Item, "Item")
    uom = _choice(Uom, "Unit")

    class Meta:
        model = PurchaseOrderLine
        fields = ("item", "qty", "uom", "unit_price", "discount_pct", "vat_rate", "note")

    def limit_choices(self):
        self._items()


class EmailForm(StyledFormMixin, forms.Form):
    to = forms.EmailField(label="Send to", required=False,
                          help_text="Left empty, the supplier's own email address.")
    message = forms.CharField(label="Message (optional)", required=False,
                              widget=forms.Textarea(attrs={"rows": 3}))


class ReceiptForm(BranchScoped):
    order = _choice(PurchaseOrder, "Against order", required=False)
    supplier = _choice(Supplier, "Supplier (direct receipt)", required=False)
    warehouse = _choice(Warehouse, "Received at store")

    class Meta:
        model = GoodsReceipt
        fields = ("order", "supplier", "warehouse", "received_date", "challan_no", "invoice_no",
                  "vehicle_no", "note")
        labels = {"received_date": "Received on"}
        widgets = {"received_date": date_widget()}

    def limit_choices(self):
        self.fields["order"].queryset = PurchaseOrder.all_objects.filter(
            company_id=self.company_id,
            status__in=[PurchaseOrder.Status.APPROVED, PurchaseOrder.Status.PARTLY_RECEIVED],
            branch__in=self._branches()).select_related("supplier").order_by("-order_date")
        self.fields["order"].label_from_instance = lambda o: f"{o.number} · {o.supplier.name}"
        self.fields["supplier"].queryset = self._active(Supplier).order_by("name")
        self.fields["warehouse"].queryset = self._stores()

    def clean(self):
        data = super().clean()
        if not data.get("order") and not data.get("supplier"):
            self.add_error("supplier", "Choose the order, or the supplier for a direct receipt.")
        return data


class ReceiptLineForm(ItemLineMixin, CompanyForm):
    order_line = _choice(PurchaseOrderLine, "Order line", required=False)
    item = _choice(Item, "Item", required=False)
    location = _choice(Location, "Bin", required=False)
    uom = _choice(Uom, "Unit")

    class Meta:
        model = GoodsReceiptLine
        fields = ("order_line", "item", "location", "qty_received", "qty_rejected", "uom", "unit_cost",
                  "batch_no", "supplier_batch_no", "mfg_date", "expiry_date", "packages", "note")
        widgets = {"mfg_date": date_widget(), "expiry_date": date_widget()}
        help_texts = {"expiry_date": "Left empty, worked out from the manufacture date and shelf life."}

    def __init__(self, *args, receipt=None, **kwargs):
        self.receipt = receipt
        super().__init__(*args, **kwargs)

    def limit_choices(self):
        self._items()
        receipt = self.receipt
        self.fields["order_line"].queryset = (PurchaseOrderLine.all_objects.filter(
            order_id=receipt.order_id).select_related("item") if receipt and receipt.order_id
            else PurchaseOrderLine.all_objects.none())
        self.fields["order_line"].label_from_instance = lambda line: (
            f"{line.item.code} {line.item.name} · {line.qty_open_base.normalize():f} "
            f"{line.item.base_uom.code if line.item_id else ''} open")
        self.fields["location"].queryset = (self._active(Location).filter(warehouse_id=receipt.warehouse_id)
                                            if receipt else Location.all_objects.none())
        if receipt and not receipt.order_id:
            del self.fields["order_line"]
        self.fields["qty_rejected"].required = False

    def clean(self):
        data = super().clean()
        if not data.get("order_line") and not data.get("item"):
            self.add_error("item", "Choose the order line or the item.")
        if data.get("qty_rejected") is None:
            data["qty_rejected"] = Decimal("0")
        return data


class QcForm(StyledFormMixin, forms.Form):
    result = forms.ChoiceField(choices=QualityCheck.Result.choices, label="Result")
    notes = forms.CharField(max_length=500, required=False, label="Notes",
                            help_text="Needed when it fails: what was wrong.")


class ReturnForm(CompanyForm):
    supplier = _choice(Supplier, "Supplier", required=False)
    receipt = _choice(GoodsReceipt, "From goods receipt", required=False)

    class Meta:
        model = PurchaseReturn
        fields = ("receipt", "supplier", "return_date", "reason")
        labels = {"return_date": "Returned on", "reason": "Why it goes back"}
        widgets = {"return_date": date_widget()}

    def limit_choices(self):
        self.fields["supplier"].queryset = self._active(Supplier).order_by("name")
        self.fields["receipt"].queryset = GoodsReceipt.all_objects.filter(
            company_id=self.company_id, status=GoodsReceipt.Status.POSTED).select_related(
            "supplier").order_by("-received_date")
        self.fields["receipt"].label_from_instance = lambda r: f"{r.number} · {r.supplier.name}"

    def clean(self):
        data = super().clean()
        if not data.get("receipt") and not data.get("supplier"):
            self.add_error("supplier", "Choose the goods receipt, or the supplier.")
        return data


class ReturnLineForm(ItemLineMixin, CompanyForm):
    receipt_line = _choice(GoodsReceiptLine, "Received line", required=False)
    item = _choice(Item, "Item (not from a receipt)", required=False)
    batch = _choice(Batch, "Batch", required=False)
    warehouse = _choice(Warehouse, "From store", required=False)
    location = _choice(Location, "Bin", required=False)
    uom = _choice(Uom, "Unit")

    class Meta:
        model = PurchaseReturnLine
        fields = ("receipt_line", "item", "batch", "warehouse", "location", "qty", "uom", "note")

    def __init__(self, *args, returnable=(), **kwargs):
        self.returnable = returnable
        super().__init__(*args, **kwargs)

    def limit_choices(self):
        self._items()
        self.fields["receipt_line"].queryset = GoodsReceiptLine.all_objects.filter(
            pk__in=[line.pk for line in self.returnable]).select_related("receipt", "item", "batch")
        self.fields["receipt_line"].label_from_instance = lambda line: (
            f"{line.receipt.number} · {line.item.code} · batch {line.batch_no or '—'} · "
            f"{line.qty_accepted.normalize():f} {line.uom.code if line.uom_id else ''} received")
        self.fields["batch"].queryset = Batch.all_objects.filter(company_id=self.company_id).select_related(
            "item").order_by("item__code", "batch_no")
        self.fields["batch"].label_from_instance = lambda b: f"{b.item.code} · {b.batch_no}"
        self.fields["warehouse"].queryset = self._active(Warehouse).order_by("name")
        self.fields["location"].queryset = self._active(Location).select_related("warehouse").order_by(
            "warehouse__code", "code")
        self.fields["location"].label_from_instance = lambda b: f"{b.warehouse.code} / {b.code}"

    def clean(self):
        data = super().clean()
        if not data.get("receipt_line") and not data.get("item"):
            self.add_error("receipt_line", "Choose the received line, or the item, batch and store.")
        return data
