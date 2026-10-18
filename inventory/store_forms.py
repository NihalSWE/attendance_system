"""Forms for the store documents (Phase 4). Convenience only: the services
re-check everything, so line forms leave model validation to them."""

from django import forms

from common.choices import ActiveStatus
from common.forms import StyledFormMixin, date_widget
from inventory.forms import CompanyForm
from inventory.models import (
    Batch,
    Item,
    Location,
    MaterialReturn,
    MaterialReturnLine,
    StockAdjustment,
    StockAdjustmentLine,
    StockCount,
    StockCountLine,
    StockTransfer,
    StockTransferLine,
    StoreIssue,
    StoreIssueLine,
    StoreRequisition,
    StoreRequisitionLine,
    Uom,
    Warehouse,
)
from organization.models import Department


def _choice(model, label, required=True):
    return forms.ModelChoiceField(queryset=model.all_objects.none(), label=label, required=required)


class Scoped(CompanyForm):
    """Store lists limited to ``branch_ids`` (None = every branch); no in-transit store."""

    def __init__(self, *args, branch_ids=None, **kwargs):
        self.branch_ids = branch_ids
        super().__init__(*args, **kwargs)

    def stores(self):
        rows = self._active(Warehouse).exclude(kind=Warehouse.Kind.TRANSIT)
        if self.branch_ids is not None:
            rows = rows.filter(branch_id__in=self.branch_ids)
        return rows.order_by("name")

    def departments(self):
        rows = Department.all_objects.filter(company_id=self.company_id, status=ActiveStatus.ACTIVE)
        if self.branch_ids is not None:
            rows = rows.filter(branch_id__in=self.branch_ids)
        return rows.select_related("branch").order_by("name")

    def _label_departments(self, field):
        self.fields[field].queryset = self.departments()
        self.fields[field].label_from_instance = lambda d: f"{d.name} ({d.branch.name})"


class LineForm(CompanyForm):
    """A document line: the service resolves and validates it."""

    def _post_clean(self):
        pass

    def items_and_units(self):
        self.fields["item"].queryset = self._active(Item).order_by("code")
        self.fields["uom"].queryset = self._active(Uom).order_by("code")

    def batches(self, field="batch"):
        self.fields[field].queryset = Batch.all_objects.filter(company_id=self.company_id).select_related(
            "item").order_by("item__code", "expiry_date", "batch_no")
        self.fields[field].label_from_instance = lambda b: (
            f"{b.item.code} · {b.batch_no}" + (f" · exp {b.expiry_date:%d %b %Y}" if b.expiry_date else "")
            + ("" if b.status == "released" else f" ({b.get_status_display().lower()})"))

    def bins(self, field, warehouse):
        self.fields[field].queryset = (self._active(Location).filter(warehouse=warehouse).order_by("code")
                                       if warehouse else Location.all_objects.none())


class RequisitionForm(Scoped):
    department = _choice(Department, "Department asking")
    warehouse = _choice(Warehouse, "Store asked")

    class Meta:
        model = StoreRequisition
        fields = ("department", "warehouse", "request_date", "needed_by", "purpose")
        labels = {"request_date": "Date"}
        widgets = {"request_date": date_widget(), "needed_by": date_widget()}

    def limit_choices(self):
        self._label_departments("department")
        self.fields["warehouse"].queryset = self.stores()


class RequisitionLineForm(LineForm):
    item = _choice(Item, "Item")
    uom = _choice(Uom, "Unit")

    class Meta:
        model = StoreRequisitionLine
        fields = ("item", "qty", "uom", "note")

    def limit_choices(self):
        self.items_and_units()


class IssueForm(Scoped):
    requisition = _choice(StoreRequisition, "Against requisition", required=False)
    warehouse = _choice(Warehouse, "From store", required=False)
    department = _choice(Department, "To department", required=False)

    class Meta:
        model = StoreIssue
        fields = ("requisition", "warehouse", "department", "issue_date", "received_by", "note")
        labels = {"issue_date": "Date"}
        widgets = {"issue_date": date_widget()}

    def limit_choices(self):
        rows = StoreRequisition.all_objects.filter(
            company_id=self.company_id, status__in=["approved", "partly_issued"]).select_related("department")
        if self.branch_ids is not None:
            rows = rows.filter(warehouse__branch_id__in=self.branch_ids)
        self.fields["requisition"].queryset = rows.order_by("-request_date")
        self.fields["requisition"].label_from_instance = lambda r: f"{r.number} · {r.department.name}"
        self.fields["warehouse"].queryset = self.stores()
        self._label_departments("department")

    def clean(self):
        data = super().clean()
        if not data.get("requisition") and not (data.get("warehouse") and data.get("department")):
            self.add_error("requisition", "Choose the requisition, or the store and the department.")
        return data


class IssueLineForm(LineForm):
    requisition_line = _choice(StoreRequisitionLine, "Requisition line", required=False)
    item = _choice(Item, "Item", required=False)
    batch = _choice(Batch, "Batch (empty = first to expire)", required=False)
    location = _choice(Location, "Bin", required=False)
    uom = _choice(Uom, "Unit")

    class Meta:
        model = StoreIssueLine
        fields = ("requisition_line", "item", "batch", "location", "qty", "uom", "not_fefo_reason", "note")

    def __init__(self, *args, issue=None, **kwargs):
        self.issue = issue
        super().__init__(*args, **kwargs)

    def limit_choices(self):
        self.items_and_units()
        self.batches()
        self.bins("location", self.issue.warehouse if self.issue else None)
        if self.issue and self.issue.requisition_id:
            self.fields["requisition_line"].queryset = StoreRequisitionLine.all_objects.filter(
                requisition_id=self.issue.requisition_id).select_related("item__base_uom")
            self.fields["requisition_line"].label_from_instance = lambda line: (
                f"{line.item.code} {line.item.name} · {line.qty_open_base.normalize():f} "
                f"{line.item.base_uom.code} open")
        else:
            del self.fields["requisition_line"]


class ReturnForm(Scoped):
    issue = _choice(StoreIssue, "From issue", required=False)
    department = _choice(Department, "From department", required=False)
    warehouse = _choice(Warehouse, "Back to store", required=False)

    class Meta:
        model = MaterialReturn
        fields = ("issue", "department", "warehouse", "return_date", "note")
        labels = {"return_date": "Date"}
        widgets = {"return_date": date_widget()}

    def limit_choices(self):
        rows = StoreIssue.all_objects.filter(company_id=self.company_id, status="posted").select_related(
            "department")
        if self.branch_ids is not None:
            rows = rows.filter(warehouse__branch_id__in=self.branch_ids)
        self.fields["issue"].queryset = rows.order_by("-issue_date")
        self.fields["issue"].label_from_instance = lambda i: f"{i.number} · {i.department.name}"
        self._label_departments("department")
        self.fields["warehouse"].queryset = self.stores()

    def clean(self):
        data = super().clean()
        if not data.get("issue") and not (data.get("department") and data.get("warehouse")):
            self.add_error("issue", "Choose the issue, or the department and the store.")
        return data


class ReturnLineForm(LineForm):
    issue_line = _choice(StoreIssueLine, "Issued line", required=False)
    item = _choice(Item, "Item (not from an issue)", required=False)
    batch = _choice(Batch, "Batch", required=False)
    location = _choice(Location, "Bin", required=False)
    uom = _choice(Uom, "Unit")

    class Meta:
        model = MaterialReturnLine
        fields = ("issue_line", "item", "batch", "condition", "location", "qty", "uom", "note")

    def __init__(self, *args, document=None, **kwargs):
        self.document = document
        super().__init__(*args, **kwargs)

    def limit_choices(self):
        self.items_and_units()
        self.batches()
        self.bins("location", self.document.warehouse if self.document else None)
        lines = StoreIssueLine.all_objects.filter(company_id=self.company_id, issue__status="posted")
        if self.document is not None:
            lines = lines.filter(issue__department_id=self.document.department_id)
            if self.document.issue_id:
                lines = lines.filter(issue_id=self.document.issue_id)
        self.fields["issue_line"].queryset = lines.select_related("issue", "item", "batch", "uom").order_by(
            "-issue__issue_date", "pk")
        self.fields["issue_line"].label_from_instance = lambda line: (
            f"{line.issue.number} · {line.item.code} · {line.batch.batch_no if line.batch_id else '—'} · "
            f"{line.qty.normalize():f} {line.uom.code}")


class TransferForm(Scoped):
    from_warehouse = _choice(Warehouse, "From store")
    to_warehouse = _choice(Warehouse, "To store")

    class Meta:
        model = StockTransfer
        fields = ("from_warehouse", "to_warehouse", "transfer_date", "vehicle_no", "note")
        labels = {"transfer_date": "Date"}
        widgets = {"transfer_date": date_widget()}

    def limit_choices(self):
        self.fields["from_warehouse"].queryset = self.stores()
        self.fields["to_warehouse"].queryset = self._active(Warehouse).exclude(
            kind=Warehouse.Kind.TRANSIT).order_by("name")


class TransferLineForm(LineForm):
    item = _choice(Item, "Item")
    batch = _choice(Batch, "Batch (empty = first to expire)", required=False)
    from_location = _choice(Location, "From bin", required=False)
    to_location = _choice(Location, "To bin", required=False)
    uom = _choice(Uom, "Unit")

    class Meta:
        model = StockTransferLine
        fields = ("item", "batch", "from_location", "to_location", "qty", "uom", "note")

    def __init__(self, *args, transfer=None, **kwargs):
        self.transfer = transfer
        super().__init__(*args, **kwargs)

    def limit_choices(self):
        self.items_and_units()
        self.batches()
        self.bins("from_location", self.transfer.from_warehouse if self.transfer else None)
        self.bins("to_location", self.transfer.to_warehouse if self.transfer else None)


class ReceiveForm(StyledFormMixin, forms.Form):
    received_date = forms.DateField(label="Received on", widget=date_widget())


class AdjustmentForm(Scoped):
    warehouse = _choice(Warehouse, "Store")

    class Meta:
        model = StockAdjustment
        fields = ("warehouse", "adjustment_date", "reason", "note")
        labels = {"adjustment_date": "Date"}
        widgets = {"adjustment_date": date_widget()}

    def limit_choices(self):
        self.fields["warehouse"].queryset = self.stores()
        self.fields["reason"].choices = [c for c in StockAdjustment.Reason.choices
                                         if c[0] not in ("count", "transit")]


class AdjustmentLineForm(LineForm):
    item = _choice(Item, "Item")
    batch = _choice(Batch, "Batch", required=False)
    location = _choice(Location, "Bin", required=False)
    uom = _choice(Uom, "Unit")

    class Meta:
        model = StockAdjustmentLine
        fields = ("item", "batch", "batch_no", "expiry_date", "location", "qty_change", "uom", "unit_cost", "note")
        widgets = {"expiry_date": date_widget()}

    def __init__(self, *args, adjustment=None, **kwargs):
        self.adjustment = adjustment
        super().__init__(*args, **kwargs)

    def limit_choices(self):
        self.items_and_units()
        self.batches()
        self.bins("location", self.adjustment.warehouse if self.adjustment else None)


class CountForm(Scoped):
    warehouse = _choice(Warehouse, "Store to count")

    class Meta:
        model = StockCount
        fields = ("warehouse", "count_date", "note")
        labels = {"count_date": "Date"}
        widgets = {"count_date": date_widget()}

    def limit_choices(self):
        self.fields["warehouse"].queryset = self.stores()


class FoundForm(LineForm):
    item = _choice(Item, "Item")
    batch = _choice(Batch, "Batch", required=False)
    location = _choice(Location, "Bin", required=False)

    class Meta:
        model = StockCountLine
        fields = ("item", "batch", "location", "counted_qty", "note")
        labels = {"counted_qty": "Counted (base unit)"}

    def __init__(self, *args, count=None, **kwargs):
        self.count = count
        super().__init__(*args, **kwargs)

    def limit_choices(self):
        self.fields["item"].queryset = self._active(Item).order_by("code")
        self.batches()
        self.bins("location", self.count.warehouse if self.count else None)


class ScanForm(forms.Form):
    code = forms.CharField(max_length=500)


class NoteForm(StyledFormMixin, forms.Form):
    note = forms.CharField(max_length=255, label="Why", required=True)
