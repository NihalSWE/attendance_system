"""Sales forms. Convenience only: ``sales.orders`` / ``sales.deliveries`` re-check everything."""

from django import forms

from common.forms import StyledFormMixin, date_widget
from inventory.forms import CompanyForm
from inventory.models import Batch, Item, Location, Uom, Warehouse
from sales.models import (
    Customer,
    Delivery,
    DeliveryLine,
    PriceList,
    PriceListLine,
    SalesOrder,
    SalesOrderLine,
    SalesReturn,
    SalesReturnLine,
)


def _choice(model, label, required=True):
    return forms.ModelChoiceField(queryset=model.all_objects.none(), label=label, required=required)


class ServiceForm(CompanyForm):
    def _post_clean(self):
        pass


def _sellable_stores(form):
    rows = form._active(Warehouse).exclude(kind__in=["quarantine", "rejected", "transit"])
    if getattr(form, "branch_ids", None) is not None:
        rows = rows.filter(branch_id__in=form.branch_ids)
    return rows.order_by("name")


class PriceListForm(ServiceForm):
    class Meta:
        model = PriceList
        fields = ("name", "valid_from", "valid_to", "note")
        widgets = {"valid_from": date_widget(), "valid_to": date_widget()}


class PriceForm(ServiceForm):
    item = _choice(Item, "Item")
    uom = _choice(Uom, "Unit")

    class Meta:
        model = PriceListLine
        fields = ("item", "uom", "price", "min_qty")

    def limit_choices(self):
        self.fields["item"].queryset = self._active(Item).order_by("code")
        self.fields["uom"].queryset = self._active(Uom).order_by("code")
        self.fields["min_qty"].required = False


class OrderForm(ServiceForm):
    customer = _choice(Customer, "Customer")
    warehouse = _choice(Warehouse, "Deliver from store")

    class Meta:
        model = SalesOrder
        fields = ("customer", "warehouse", "order_date", "delivery_date", "source", "customer_ref",
                  "delivery_address", "note")
        widgets = {"order_date": date_widget(), "delivery_date": date_widget()}
        help_texts = {"delivery_address": "Left empty: the customer's address."}

    def __init__(self, *args, branch_ids=None, **kwargs):
        self.branch_ids = branch_ids
        super().__init__(*args, **kwargs)

    def limit_choices(self):
        self.fields["customer"].queryset = self._active(Customer).order_by("name")
        self.fields["warehouse"].queryset = _sellable_stores(self)
        self.fields["source"].choices = [c for c in SalesOrder.Source.choices if c[0] != "ai"]


class OrderLineForm(ServiceForm):
    item = _choice(Item, "Item")
    warehouse = _choice(Warehouse, "From store", required=False)
    uom = _choice(Uom, "Unit")

    class Meta:
        model = SalesOrderLine
        fields = ("item", "warehouse", "qty", "uom", "unit_price", "discount_pct", "vat_rate", "note")
        help_texts = {"unit_price": "Left empty: the customer's price list, else the item's sale price.",
                      "vat_rate": "Left empty: the item's VAT.",
                      "warehouse": "Left empty: the order's store. Pick another to sell, say, sugar from "
                                   "the raw material store on the same order."}

    def __init__(self, *args, branch_ids=None, **kwargs):
        self.branch_ids = branch_ids
        super().__init__(*args, **kwargs)

    def limit_choices(self):
        self.fields["item"].queryset = self._active(Item).order_by("code")
        self.fields["warehouse"].queryset = _sellable_stores(self)
        self.fields["uom"].queryset = self._active(Uom).order_by("code")
        for name in ("unit_price", "discount_pct", "vat_rate"):
            self.fields[name].required = False


class NoteForm(StyledFormMixin, forms.Form):
    note = forms.CharField(max_length=255, label="Why", required=False)


class DeliveryForm(ServiceForm):
    order = _choice(SalesOrder, "Sales order")
    warehouse = _choice(Warehouse, "From store", required=False)

    class Meta:
        model = Delivery
        fields = ("order", "warehouse", "delivery_date", "vehicle_no", "driver", "note")
        help_texts = {"warehouse": "Needed only when the order goes from more than one store: one challan "
                                   "per store."}
        widgets = {"delivery_date": date_widget()}

    def limit_choices(self):
        self.fields["order"].queryset = SalesOrder.all_objects.filter(
            company_id=self.company_id, status__in=["confirmed", "partly_delivered"]).select_related(
            "customer").order_by("-order_date")
        self.fields["order"].label_from_instance = lambda o: f"{o.number} · {o.customer.name}"
        self.fields["warehouse"].queryset = _sellable_stores(self)


class DeliveryLineForm(ServiceForm):
    order_line = _choice(SalesOrderLine, "Order line")
    batch = _choice(Batch, "Batch (empty = first to expire)", required=False)
    location = _choice(Location, "Bin", required=False)
    uom = _choice(Uom, "Unit")

    class Meta:
        model = DeliveryLine
        fields = ("order_line", "batch", "location", "qty", "uom", "not_fefo_reason")

    def __init__(self, *args, delivery=None, **kwargs):
        self.delivery = delivery
        super().__init__(*args, **kwargs)

    def limit_choices(self):
        order = self.delivery.order if self.delivery else None
        self.fields["order_line"].queryset = (SalesOrderLine.all_objects.filter(
            order=order, warehouse=self.delivery.warehouse).select_related("item__base_uom")
            if order else SalesOrderLine.all_objects.none())
        self.fields["order_line"].label_from_instance = lambda l: (
            f"{l.item.code} {l.item.name} · {l.qty_open_base.normalize():f} {l.item.base_uom.code} to deliver")
        self.fields["batch"].queryset = Batch.all_objects.filter(company_id=self.company_id).select_related(
            "item").order_by("item__code", "expiry_date")
        self.fields["batch"].label_from_instance = lambda b: (
            f"{b.item.code} · {b.batch_no}" + (f" · exp {b.expiry_date:%d %b %Y}" if b.expiry_date else ""))
        self.fields["location"].queryset = (self._active(Location).filter(warehouse=self.delivery.warehouse)
                                            if order else Location.all_objects.none())
        self.fields["uom"].queryset = self._active(Uom).order_by("code")


class PaidForm(StyledFormMixin, forms.Form):
    paid_on = forms.DateField(label="Paid on", widget=date_widget())
    note = forms.CharField(max_length=255, required=False, label="Payment reference",
                           help_text="Cheque, bank or bKash reference.")


class ReturnForm(ServiceForm):
    delivery = _choice(Delivery, "From delivery", required=False)
    customer = _choice(Customer, "Customer", required=False)
    warehouse = _choice(Warehouse, "Back to store", required=False)

    class Meta:
        model = SalesReturn
        fields = ("delivery", "customer", "warehouse", "return_date", "reason")
        widgets = {"return_date": date_widget()}

    def limit_choices(self):
        self.fields["delivery"].queryset = Delivery.all_objects.filter(
            company_id=self.company_id, status="posted").select_related("order__customer").order_by("-delivery_date")
        self.fields["delivery"].label_from_instance = lambda d: f"{d.number} · {d.order.customer.name}"
        self.fields["customer"].queryset = self._active(Customer).order_by("name")
        self.fields["warehouse"].queryset = _sellable_stores(self)

    def clean(self):
        data = super().clean()
        if not data.get("delivery") and not (data.get("customer") and data.get("warehouse")):
            self.add_error("delivery", "Choose the delivery, or the customer and the store.")
        return data


class ReturnLineForm(ServiceForm):
    delivery_line = _choice(DeliveryLine, "Delivered line", required=False)
    item = _choice(Item, "Item (not from a delivery)", required=False)
    batch = _choice(Batch, "Batch", required=False)
    uom = _choice(Uom, "Unit")

    class Meta:
        model = SalesReturnLine
        fields = ("delivery_line", "item", "batch", "condition", "qty", "uom", "note")

    def __init__(self, *args, document=None, **kwargs):
        self.document = document
        super().__init__(*args, **kwargs)

    def limit_choices(self):
        lines = DeliveryLine.all_objects.filter(company_id=self.company_id, delivery__status="posted")
        if self.document is not None:
            lines = lines.filter(delivery__order__customer_id=self.document.customer_id)
            if self.document.delivery_id:
                lines = lines.filter(delivery_id=self.document.delivery_id)
        self.fields["delivery_line"].queryset = lines.select_related("delivery", "item", "batch", "uom")
        self.fields["delivery_line"].label_from_instance = lambda l: (
            f"{l.delivery.number} · {l.item.code} · {l.batch.batch_no if l.batch_id else '—'} · "
            f"{l.qty.normalize():f} {l.uom.code}")
        self.fields["item"].queryset = self._active(Item).order_by("code")
        self.fields["batch"].queryset = Batch.all_objects.filter(company_id=self.company_id).select_related(
            "item").order_by("item__code", "batch_no")
        self.fields["batch"].label_from_instance = lambda b: f"{b.item.code} · {b.batch_no}"
        self.fields["uom"].queryset = self._active(Uom).order_by("code")

    def clean(self):
        data = super().clean()
        if not data.get("delivery_line") and not data.get("item"):
            self.add_error("delivery_line", "Choose the delivered line, or the item and batch.")
        return data

