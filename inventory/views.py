"""Store & Inventory pages. Thin adapters: every permission check and every
write is in the services (``inventory.masters``, ``inventory.opening``).

Masters (units, categories, items, stores, bins, suppliers, customers) share
one page shape: a server-side table, **Add** in a modal, **Edit** in a modal
(the page opens with that row's form, ``?edit=<id>``), and Deactivate /
Activate behind a confirmation modal. A refused form comes back in its
modal with the reasons.
"""

from dataclasses import dataclass, field

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from access_control.branch_access import ALL_BRANCHES, branches_for
from base_template.tables import paginate, render
from common.choices import ActiveStatus
from common.forms import apply_service_errors
from common.tenant import use_company
from inventory import access, forms, masters
from inventory.models import (
    InventorySettings,
    Item,
    ItemCategory,
    ItemKind,
    Location,
    Uom,
    Warehouse,
)
from purchasing.models import Supplier
from purchasing.services import SUPPLIER
from sales.models import Customer
from sales.services import CUSTOMER


def company_or_bail(request):
    """``(company_id, None)``, or ``(None, response)`` when the page cannot open:
    the platform owner (no company), no company, or the module not enabled."""
    if request.user.is_superuser:
        return None, redirect("platform:company_list")
    if not request.company_id:
        return None, render(request, "base_template/no_company.html")
    if not access.inventory_on(request.company_id):
        return None, render(request, "inventory/not_enabled.html", status=404)
    return request.company_id, None


def branch_ids(user, company_id, code):
    """Branch ids where ``user`` holds ``code`` (None = every branch), for form lists."""
    branches = branches_for(user, company_id, code)
    return None if branches is ALL_BRANCHES else set(branches)


# --------------------------------------------------------------------------
# Masters
# --------------------------------------------------------------------------

@dataclass
class MasterPage:
    key: str
    master: object
    form: type
    title: str
    singular: str
    row_template: str
    columns: tuple
    search: tuple
    order: tuple
    queryset: object
    about: str = ""
    #: The form needs the branches where the actor may set up stores.
    branch_scoped: bool = False
    filters: dict = field(default_factory=dict)
    import_kind: str = ""

    @property
    def url(self):
        return reverse(f"inventory:{self.key}")


def _status_filter(request, rows):
    status = request.GET.get("status", "").strip()
    if status in ActiveStatus.values:
        rows = rows.filter(status=status)
    return rows, status


PAGES = {
    "units": MasterPage(
        "units", masters.UOM, forms.UomForm, "Units", "unit", "inventory/rows/unit.html",
        ("Code", "Name", "Kind", "Decimals", "Status"),
        ("code", "name", "kind"), ("code", "name", "kind", "decimals", "status", None),
        lambda request, cid: Uom.objects.all(),
        about="The units stock is counted in. Packs (1 carton = 24 pieces) are set on each item.",
    ),
    "categories": MasterPage(
        "categories", masters.CATEGORY, forms.CategoryForm, "Item categories", "category",
        "inventory/rows/category.html", ("Code", "Category", "Item type", "Inside", "Status"),
        ("code", "name", "kind", "parent__name"),
        ("code", "name", "kind", "parent__name", "status", None),
        lambda request, cid: ItemCategory.objects.select_related("parent"),
        about="Your own grouping of items, each under one of the five item types.",
    ),
    "items": MasterPage(
        "items", masters.ITEM, forms.ItemForm, "Items", "item", "inventory/rows/item.html",
        ("Code", "Item", "Type", "Category", "Unit", "Batches", "Status"),
        ("code", "name", "name_bn", "barcode", "category__name"),
        ("code", "name", "kind", "category__name", "base_uom__code", "track_batch", "status", None),
        lambda request, cid: _items(request),
        about="Everything the factory buys, makes, stores or sells.", import_kind="items",
    ),
    "stores": MasterPage(
        "stores", masters.WAREHOUSE, forms.WarehouseForm, "Stores", "store",
        "inventory/rows/store.html", ("Code", "Store", "Type", "Branch", "Bins", "Status"),
        ("code", "name", "branch__name"),
        ("code", "name", "kind", "branch__name", None, "status", None),
        lambda request, cid: _stores(request, cid), branch_scoped=True,
        about="Each store belongs to a branch; stock, issues and transfers follow its branch.",
        import_kind="stores",
    ),
    "bins": MasterPage(
        "bins", masters.LOCATION, forms.LocationForm, "Bins", "bin", "inventory/rows/bin.html",
        ("Store", "Bin", "Description", "Status"),
        ("code", "description", "warehouse__code", "warehouse__name"),
        ("warehouse__code", "code", "description", "status", None),
        lambda request, cid: access.scope_warehouses(
            Location.objects.select_related("warehouse"), request.user, cid, "inventory.view",
            field="warehouse__branch"),
        branch_scoped=True,
        about="Racks, shelves and bins inside a store, so a scan says exactly where. Optional.",
    ),
    "suppliers": MasterPage(
        "suppliers", SUPPLIER, forms.SupplierForm, "Suppliers", "supplier",
        "inventory/rows/supplier.html", ("Code", "Supplier", "Contact", "Phone", "Terms", "Status"),
        ("code", "name", "contact_person", "phone", "email"),
        ("code", "name", "contact_person", "phone", "payment_terms_days", "status", None),
        lambda request, cid: Supplier.objects.all(), import_kind="suppliers",
    ),
    "customers": MasterPage(
        "customers", CUSTOMER, forms.CustomerForm, "Customers", "customer",
        "inventory/rows/customer.html", ("Code", "Customer", "Type", "Phone", "Territory", "Status"),
        ("code", "name", "phone", "whatsapp", "email", "territory"),
        ("code", "name", "customer_type", "phone", "territory", "status", None),
        lambda request, cid: Customer.objects.all(), import_kind="customers",
        about="Their email and WhatsApp number let the AI order assistant recognise them later.",
    ),
}


def _items(request):
    rows = Item.objects.select_related("category", "base_uom")
    kind = request.GET.get("kind", "").strip()
    if kind in ItemKind.values:
        rows = rows.filter(kind=kind)
    return rows


def _stores(request, company_id):
    from django.db.models import Count

    rows = Warehouse.objects.select_related("branch").annotate(bin_count=Count("locations"))
    return access.scope_warehouses(rows, request.user, company_id, "inventory.view")


def _form(page, request, company_id, *, data=None, instance=None):
    kwargs = {"company_id": company_id, "instance": instance}
    if page.branch_scoped:
        kwargs["branch_ids"] = branch_ids(request.user, company_id, masters.CODE)
    return page.form(data, **kwargs)


def _list(request, page, *, new_form=None, edit_form=None, edit_row=None, open_dialog=""):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require(request.user, company_id, "inventory.view")
    can_manage = access.may(request.user, company_id, masters.CODE)
    with use_company(company_id):
        rows, status = _status_filter(request, page.queryset(request, company_id))
        table = paginate(request, rows, search=page.search, order=page.order)
        if can_manage and edit_form is None and request.GET.get("edit", "").isdigit():
            try:
                _, edit_row = masters.get_for_edit(page.master, actor=request.user,
                                                   company_id=company_id,
                                                   pk=int(request.GET["edit"]))
            except PermissionDenied:
                edit_row = None
            if edit_row is not None:
                edit_form = _form(page, request, company_id, instance=edit_row)
                open_dialog = "edit-dialog"
        if can_manage and new_form is None:
            new_form = _form(page, request, company_id)
    return render(request, "inventory/master_list.html", {
        "page_def": page, "rows": table, "status": status, "statuses": ActiveStatus.choices,
        "kind": request.GET.get("kind", ""), "kinds": ItemKind.choices if page.key == "items" else (),
        "can_manage": can_manage, "new_form": new_form, "edit_form": edit_form,
        "edit_row": edit_row, "open_dialog": open_dialog,
        "edit_view": f"inventory:{page.key}_edit", "status_view": f"inventory:{page.key}_status",
        "sees_costs": access.sees_costs(request.user, company_id),
        "can_print_labels": access.may(request.user, company_id, "inventory.labels"),
        "print_form": forms.PrintForm(),
    })


def master_list(request, key):
    page = PAGES[key]
    if request.method != "POST":
        return _list(request, page)
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    # Refuse before validating: a refused form must not come back as if allowed.
    access.require(request.user, company_id, masters.CODE)
    form = _form(page, request, company_id, data=request.POST)
    if form.is_valid():
        try:
            row = masters.create(page.master, actor=request.user, company_id=company_id,
                                 values=form.cleaned_data)
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, f"{page.singular.capitalize()} “{row}” added.")
            return redirect(page.url)
    return _list(request, page, new_form=form, open_dialog="new-dialog")


def master_edit(request, key, pk):
    page = PAGES[key]
    if request.method != "POST":
        return redirect(f"{page.url}?edit={pk}")
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    _, row = masters.get_for_edit(page.master, actor=request.user, company_id=company_id, pk=pk)
    form = _form(page, request, company_id, data=request.POST, instance=row)
    if form.is_valid():
        update = masters.update_item if page.master is masters.ITEM else (
            lambda **kw: masters.update(page.master, **kw))
        try:
            row = update(actor=request.user, company_id=company_id, pk=pk,
                         values=form.cleaned_data)
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, f"{page.singular.capitalize()} “{row}” saved.")
            return redirect(request.POST.get("next") or page.url)
    # The bound form's instance is already changed in memory; show the saved row's name.
    row = type(row).all_objects.get(pk=row.pk)
    return _list(request, page, edit_form=form, edit_row=row, open_dialog="edit-dialog")


@require_POST
def master_status(request, key, pk):
    page = PAGES[key]
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    form = forms.StatusForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose a valid status.")
        return redirect(page.url)
    try:
        row = masters.set_status(page.master, actor=request.user, company_id=company_id, pk=pk,
                                 status=form.cleaned_data["status"])
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, f"“{row}” is now {row.get_status_display().lower()}.")
    return redirect(request.POST.get("next") or page.url)


master_list = login_required(master_list)
master_edit = login_required(master_edit)
master_status = login_required(master_status)


@login_required
@require_POST
def standard_units(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    added = masters.add_standard_units(actor=request.user, company_id=company_id)
    if added:
        messages.success(request, "Added " + ", ".join(u.code for u in added) + ".")
    else:
        messages.info(request, "Every standard unit is here already.")
    return redirect("inventory:units")


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

@login_required
def settings_page(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    membership = access.require_module(request.user, company_id)
    if membership.role not in ("owner", "company_admin"):
        raise PermissionDenied("Inventory settings are kept by the owner or company administrator.")
    from inventory.posting import settings_for

    current = settings_for(company_id)
    form = forms.SettingsForm(request.POST or None, instance=current)
    if request.method == "POST" and form.is_valid():
        try:
            masters.update_settings(actor=request.user, company_id=company_id,
                                    values=form.cleaned_data)
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, "Inventory settings saved.")
            return redirect("inventory:settings")
    with use_company(company_id):
        from inventory.models import StockMove

        moved = StockMove.objects.exists()
    return render(request, "inventory/settings.html", {"form": form, "moved": moved})
