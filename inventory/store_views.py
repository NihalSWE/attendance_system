"""Store operation pages (Phase 4): requisitions, issues, material returns,
transfers, adjustments and counts.

One set of views serves all six, each described by a ``Doc``: its list
(server-side table), its page with lines (added in a modal, or by scanning a
label), and its actions (confirmed in a modal). Every permission and rule is
in the services (``inventory.issues`` / ``transfers`` / ``adjustments``).
"""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from base_template.tables import paginate, render
from common.forms import apply_service_errors
from common.tenant import use_company
from inventory import access, adjustments, issues, store_forms as f, transfers
from inventory.forms import ReasonForm
from inventory.models import (
    MaterialReturn,
    StockAdjustment,
    StockCount,
    StockTransfer,
    StoreIssue,
    StoreRequisition,
)
from inventory.views import branch_ids, company_or_bail


@dataclass
class Doc:
    key: str
    model: type
    title: str
    singular: str
    about: str
    code: str                     # who may start one / add lines
    header_form: type
    create: object                # (actor, company_id, values) -> row
    get: object                   # (actor, company_id, pk, code) -> (membership, row)
    columns: tuple
    search: tuple
    order: tuple
    branch_path: str              # from the row to its branch, for the list
    line_form: type = None
    line_kw: str = ""             # the line form's keyword for the row
    add_line: object = None
    remove_line: object = None
    scan: object = None
    actions: dict = field(default_factory=dict)
    select: tuple = ()

    @property
    def detail_template(self):
        return f"inventory/store/{self.key}_detail.html"


def _say(result, text):
    return text.format(obj=result)


def _reason(request):
    return request.POST.get("reason", "")


def _note(request):
    return request.POST.get("note", "")


DOCS = {
    "requisitions": Doc(
        "requisitions", StoreRequisition, "Store requisitions", "store requisition",
        "A department asks a store for materials. Approved requisitions are issued first expiry first.",
        "inventory.issue", f.RequisitionForm,
        lambda **kw: issues.create_requisition(**kw),
        lambda **kw: issues.get_requisition(**kw),
        ("Number", "Date", "Department", "Store", "Needed by", "Status"),
        ("number", "purpose", "department__name", "warehouse__code"),
        ("number", "request_date", "department__name", "warehouse__code", "needed_by", "status", None),
        "department__branch", f.RequisitionLineForm, "",
        lambda **kw: issues.add_requisition_line(requisition_id=kw.pop("pk"), **kw),
        lambda **kw: issues.remove_requisition_line(requisition_id=kw.pop("pk"), **kw),
        actions={
            "submit": (lambda r, c, pk: issues.submit_requisition(actor=r.user, company_id=c, requisition_id=pk),
                       "{obj.number} sent for approval."),
            "approve": (lambda r, c, pk: issues.decide_requisition(actor=r.user, company_id=c,
                                                                   requisition_id=pk, approve=True),
                        "{obj.number} approved: the store can issue it."),
            "reject": (lambda r, c, pk: issues.decide_requisition(actor=r.user, company_id=c, requisition_id=pk,
                                                                  approve=False, note=_note(r)),
                       "{obj.number} rejected."),
            "cancel": (lambda r, c, pk: issues.cancel_requisition(actor=r.user, company_id=c, requisition_id=pk),
                       "{obj.number} cancelled."),
            "close": (lambda r, c, pk: issues.close_requisition(actor=r.user, company_id=c, requisition_id=pk,
                                                                note=_note(r)), "{obj.number} closed."),
        }, select=("department", "warehouse"),
    ),
    "issues": Doc(
        "issues", StoreIssue, "Store issues", "store issue",
        "Goods out of a store to a department. Batches are taken first expiry first; expired and "
        "quarantined stock is refused. Scan package labels to add them.",
        "inventory.issue", f.IssueForm,
        lambda **kw: issues.create_issue(**kw),
        lambda **kw: issues.get_issue(**kw),
        ("Number", "Date", "Store", "Department", "Requisition", "Status"),
        ("number", "department__name", "warehouse__code", "requisition__number", "received_by"),
        ("number", "issue_date", "warehouse__code", "department__name", "requisition__number", "status", None),
        "warehouse__branch", f.IssueLineForm, "issue",
        lambda **kw: issues.add_issue_line(issue_id=kw.pop("pk"), **kw),
        lambda **kw: issues.remove_issue_line(issue_id=kw.pop("pk"), **kw),
        lambda **kw: issues.scan_into_issue(issue_id=kw.pop("pk"), **kw),
        actions={
            "post": (lambda r, c, pk: issues.post_issue(actor=r.user, company_id=c, issue_id=pk),
                     "{obj.number} posted: the goods are out of the store."),
            "cancel": (lambda r, c, pk: issues.cancel_issue(actor=r.user, company_id=c, issue_id=pk),
                       "{obj.number} cancelled."),
            "reverse": (lambda r, c, pk: issues.reverse_issue(actor=r.user, company_id=c, issue_id=pk,
                                                              reason=_reason(r)), "{obj.number} reversed."),
        }, select=("department", "warehouse", "requisition"),
    ),
    "returns": Doc(
        "returns", MaterialReturn, "Material returns", "material return",
        "Goods back from a department. A line from an issue comes back at that issue's cost; damaged "
        "goods go to the Rejected store.",
        "inventory.issue", f.ReturnForm,
        lambda **kw: issues.create_return(**kw),
        lambda **kw: issues.get_return(**kw),
        ("Number", "Date", "Department", "Store", "Issue", "Status"),
        ("number", "department__name", "warehouse__code", "issue__number"),
        ("number", "return_date", "department__name", "warehouse__code", "issue__number", "status", None),
        "warehouse__branch", f.ReturnLineForm, "document",
        lambda **kw: issues.add_return_line(return_id=kw.pop("pk"), **kw),
        lambda **kw: issues.remove_return_line(return_id=kw.pop("pk"), **kw),
        actions={
            "post": (lambda r, c, pk: issues.post_return(actor=r.user, company_id=c, return_id=pk),
                     "{obj.number} posted: the goods are back."),
            "cancel": (lambda r, c, pk: issues.cancel_return(actor=r.user, company_id=c, return_id=pk),
                       "{obj.number} cancelled."),
            "reverse": (lambda r, c, pk: issues.reverse_return(actor=r.user, company_id=c, return_id=pk,
                                                               reason=_reason(r)), "{obj.number} reversed."),
        }, select=("department", "warehouse", "issue"),
    ),
    "transfers": Doc(
        "transfers", StockTransfer, "Transfers", "transfer",
        "Stock moved between stores: dispatched (in transit, not usable), then received. Shortages are "
        "written off with their reason.",
        "inventory.transfer", f.TransferForm,
        lambda **kw: transfers.create_transfer(**kw),
        lambda **kw: transfers.get_transfer(**kw),
        ("Number", "Date", "From", "To", "Received", "Status"),
        ("number", "from_warehouse__code", "to_warehouse__code", "vehicle_no"),
        ("number", "transfer_date", "from_warehouse__code", "to_warehouse__code", "received_date", "status", None),
        "from_warehouse__branch", f.TransferLineForm, "transfer",
        lambda **kw: transfers.add_line(transfer_id=kw.pop("pk"), **kw),
        lambda **kw: transfers.remove_line(transfer_id=kw.pop("pk"), **kw),
        lambda **kw: transfers.scan_into_transfer(transfer_id=kw.pop("pk"), **kw),
        actions={
            "dispatch": (lambda r, c, pk: transfers.dispatch(actor=r.user, company_id=c, transfer_id=pk),
                         "{obj.number} dispatched: in transit."),
            "cancel": (lambda r, c, pk: transfers.cancel_transfer(actor=r.user, company_id=c, transfer_id=pk),
                       "{obj.number} cancelled."),
            "reverse": (lambda r, c, pk: transfers.reverse_transfer(actor=r.user, company_id=c, transfer_id=pk,
                                                                    reason=_reason(r)),
                        "{obj.number} reversed: everything is back in the sending store."),
        }, select=("from_warehouse", "to_warehouse"),
    ),
    "adjustments": Doc(
        "adjustments", StockAdjustment, "Stock adjustments", "stock adjustment",
        "Stock written off or found, with a reason. Someone else approves; approving posts it.",
        "inventory.adjust", f.AdjustmentForm,
        lambda **kw: adjustments.create_adjustment(**kw),
        lambda **kw: adjustments.get_adjustment(**kw),
        ("Number", "Date", "Store", "Reason", "Count", "Status"),
        ("number", "warehouse__code", "reason", "note", "count__number"),
        ("number", "adjustment_date", "warehouse__code", "reason", "count__number", "status", None),
        "warehouse__branch", f.AdjustmentLineForm, "adjustment",
        lambda **kw: adjustments.add_line(adjustment_id=kw.pop("pk"), **kw),
        lambda **kw: adjustments.remove_line(adjustment_id=kw.pop("pk"), **kw),
        actions={
            "submit": (lambda r, c, pk: adjustments.submit(actor=r.user, company_id=c, adjustment_id=pk),
                       "{obj.number} sent for approval."),
            "approve": (lambda r, c, pk: adjustments.decide(actor=r.user, company_id=c, adjustment_id=pk,
                                                            approve=True), "{obj.number} approved and posted."),
            "reject": (lambda r, c, pk: adjustments.decide(actor=r.user, company_id=c, adjustment_id=pk,
                                                           approve=False, note=_note(r)), "{obj.number} rejected."),
            "cancel": (lambda r, c, pk: adjustments.cancel_adjustment(actor=r.user, company_id=c,
                                                                      adjustment_id=pk), "{obj.number} cancelled."),
            "reverse": (lambda r, c, pk: adjustments.reverse_adjustment(actor=r.user, company_id=c,
                                                                        adjustment_id=pk, reason=_reason(r)),
                        "{obj.number} reversed."),
        }, select=("warehouse", "count"),
    ),
    "counts": Doc(
        "counts", StockCount, "Stock counts", "stock count",
        "Count a store: the software's quantities are frozen when it starts; count by typing or scanning "
        "labels; approving turns every difference into a posted adjustment.",
        "inventory.adjust", f.CountForm,
        lambda **kw: adjustments.start_count(**kw),
        lambda **kw: adjustments.get_count(**kw),
        ("Number", "Date", "Store", "Started", "Approved", "Status"),
        ("number", "warehouse__code", "note"),
        ("number", "count_date", "warehouse__code", "started_at", "approved_at", "status", None),
        "warehouse__branch", f.FoundForm, "count",
        lambda **kw: adjustments.add_found(count_id=kw.pop("pk"), **kw),
        None,
        lambda **kw: adjustments.scan_into_count(count_id=kw.pop("pk"), **kw),
        actions={
            "zero": (lambda r, c, pk: adjustments.zero_uncounted(actor=r.user, company_id=c, count_id=pk),
                     "{obj} line(s) set to zero."),
            "submit": (lambda r, c, pk: adjustments.submit_count(actor=r.user, company_id=c, count_id=pk),
                       "{obj.number} sent for approval."),
            "approve": (lambda r, c, pk: adjustments.approve_count(actor=r.user, company_id=c, count_id=pk),
                        "{obj.number} approved: the differences are posted."),
            "cancel": (lambda r, c, pk: adjustments.cancel_count(actor=r.user, company_id=c, count_id=pk),
                       "{obj.number} cancelled."),
        }, select=("warehouse",),
    ),
}


def _doc(key):
    if key not in DOCS:
        raise Http404("Unknown document.")
    return DOCS[key]


def _header_form(request, doc, data=None, initial=None):
    return doc.header_form(data, initial=initial, company_id=request.company_id,
                           branch_ids=branch_ids(request.user, request.company_id, doc.code))


@login_required
def doc_list(request, key):
    doc = _doc(key)
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require_module(request.user, company_id)
    new_form = None
    if request.method == "POST":
        access.require(request.user, company_id, doc.code)
        new_form = _header_form(request, doc, request.POST)
        if new_form.is_valid():
            try:
                row = doc.create(actor=request.user, company_id=company_id, values=new_form.cleaned_data)
            except ValidationError as exc:
                apply_service_errors(new_form, exc)
            else:
                shortages = getattr(row, "shortages", [])
                messages.success(request, f"{row.number} started." + (
                    " Not enough usable stock: " + "; ".join(shortages) + "." if shortages else ""))
                return redirect("inventory:store_detail", key=key, pk=row.pk)
    can_new = access.may(request.user, company_id, doc.code)
    initial = {k: int(v) for k, v in request.GET.items() if k in doc.header_form.base_fields and v.isdigit()}
    with use_company(company_id):
        rows = access.scope_warehouses(doc.model.objects.select_related(*doc.select), request.user,
                                       company_id, "inventory.view", field=doc.branch_path)
        status = request.GET.get("status", "")
        if status in doc.model.Status.values:
            rows = rows.filter(status=status)
        table = paginate(request, rows, search=doc.search, order=doc.order)
    return render(request, "inventory/store/doc_list.html", {
        "doc": doc, "rows": table, "status": status, "statuses": doc.model.Status.choices,
        "can_new": can_new, "row_template": f"inventory/store/rows/{key}.html",
        "new_form": new_form or (_header_form(request, doc, initial=initial) if can_new else None),
        "open_dialog": "new-dialog" if new_form is not None or initial else "",
    })


def _line_form(request, doc, row, data=None):
    kwargs = {"company_id": request.company_id}
    if doc.line_kw:
        kwargs[doc.line_kw] = row
    return doc.line_form(data, **kwargs)


def _page(request, doc, pk, line_form=None, open_dialog="", extra=None):
    company_id = request.company_id
    _, row = doc.get(actor=request.user, company_id=company_id, pk=pk, code="inventory.view")
    branch = _branch_of(row, doc.branch_path)
    may = access.may(request.user, company_id, doc.code, branch)
    editable = row.status in ("draft", "counting")
    with use_company(company_id):
        lines = list(row.lines.select_related(*_line_related(doc)).order_by("pk"))
    context = {
        # Flags, not the functions: a template would call a function it is given.
        "doc": doc, "row": row, "lines": lines, "may": may, "can_edit": editable and may,
        "can_scan": editable and may and doc.scan is not None,
        "can_remove": editable and may and doc.remove_line is not None,
        "can_approve": access.may(request.user, company_id, _approve_code(doc), branch),
        "line_form": line_form or (_line_form(request, doc, row) if editable and may and doc.line_form else None),
        "reason_form": ReasonForm(), "note_form": f.NoteForm(auto_id="note_%s"), "open_dialog": open_dialog,
        "sees_costs": access.sees_costs(request.user, company_id),
    }
    if doc.key == "transfers":
        context["receive_form"] = f.ReceiveForm(initial={"received_date": row.transfer_date})
        context["can_receive"] = row.status == "in_transit" and access.may(
            request.user, company_id, "inventory.transfer", row.to_warehouse.branch_id)
    if doc.key == "counts" and row.status == "submitted":
        context["moves_since"] = adjustments.moves_since_start(row)
    context.update(extra or {})
    context["buttons"] = _buttons(request, doc, row, context)
    return render(request, "inventory/store/detail.html", context)


def _button(action, label, style="ghost", confirm="", dialog=""):
    """A page action: posted straight (with a confirmation) or through a dialog."""
    return {"action": action, "label": label, "style": style, "confirm": confirm, "dialog": dialog}


def _buttons(request, doc, row, ctx):
    """What may be done to ``row`` now, by this person."""
    s, may, approve = row.status, ctx["may"], ctx["can_approve"]
    lines = bool(ctx["lines"])
    out = []
    if doc.key == "requisitions":
        if s == "draft" and may and lines:
            out.append(_button("submit", "Send for approval", "primary",
                               "The approver sees it; it cannot change while it waits."))
        if s == "submitted" and approve:
            out += [_button("reject", "Reject", "quiet", dialog="note"),
                    _button("approve", "Approve", "primary", "The store can then issue it.")]
        if s == "partly_issued" and may:
            out.append(_button("close", "Close", "quiet", dialog="note"))
        if s in ("draft", "submitted", "approved", "rejected") and may:
            out.append(_button("cancel", "Cancel", "quiet", "It is kept for the record and goes no further."))
    elif doc.key in ("issues", "returns"):
        if s == "draft" and may:
            if lines:
                out.append(_button("post", "Post", "primary", "The stock moves now; a posted document is "
                                                              "undone only by reversing it."))
            out.append(_button("cancel", "Cancel draft", "quiet", "The draft is kept but cannot be posted."))
        if s == "posted" and may:
            out.append(_button("reverse", "Reverse", "danger", dialog="reason"))
    elif doc.key == "transfers":
        if s == "draft" and may:
            if lines:
                out.append(_button("dispatch", "Dispatch", "primary",
                                   "The goods leave the sending store and are in transit until received."))
            out.append(_button("cancel", "Cancel draft", "quiet", "The draft is kept but cannot be dispatched."))
        if s in ("in_transit", "received") and may:
            out.append(_button("reverse", "Reverse", "danger", dialog="reason"))
    elif doc.key == "adjustments":
        if s == "draft" and may:
            if lines:
                out.append(_button("submit", "Send for approval", "primary",
                                   "Someone with approval rights checks it; approving posts it."))
            if not row.count_id:
                out.append(_button("cancel", "Cancel draft", "quiet", "The draft is kept but cannot be posted."))
        if s == "submitted" and approve:
            out += [_button("reject", "Reject", "quiet", dialog="note"),
                    _button("approve", "Approve and post", "primary", "The stock changes now.")]
        if s == "posted" and approve:
            out.append(_button("reverse", "Reverse", "danger", dialog="reason"))
    elif doc.key == "counts":
        if s == "counting" and may:
            out += [_button("zero", "Not found = 0", "quiet",
                            "Every line not counted yet is set to zero: it was not found."),
                    _button("submit", "Send for approval", "primary",
                            "Counting ends; the approver sees every difference.")]
        if s == "submitted" and approve:
            warning = ""
            if ctx.get("moves_since"):
                warning = (f" Warning: {ctx['moves_since']} stock move(s) happened in this store after the "
                           "count started; their effect is counted as a difference.")
            out.append(_button("approve", "Approve", "primary",
                               "Every difference is posted as a stock adjustment." + warning))
        if s in ("counting", "submitted") and may:
            out.append(_button("cancel", "Cancel count", "quiet", "Nothing changes in stock."))
    return out


def _branch_of(row, path):
    obj = row
    for part in path.split("__"):
        obj = getattr(obj, part)
    return obj.pk


def _approve_code(doc):
    return {"requisitions": "inventory.issue_approve", "adjustments": "inventory.adjust_approve",
            "counts": "inventory.adjust_approve"}.get(doc.key, doc.code)


def _line_related(doc):
    common = ["item__base_uom"]
    return common + {
        "requisitions": ["uom"], "issues": ["uom", "batch", "location", "label", "requisition_line"],
        "returns": ["uom", "batch", "warehouse", "location", "issue_line__issue"],
        "transfers": ["uom", "batch", "from_location", "to_location"],
        "adjustments": ["uom", "batch", "location"], "counts": ["batch", "location"],
    }[doc.key]


@login_required
def doc_detail(request, key, pk):
    doc = _doc(key)
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    return _page(request, doc, pk)


@login_required
@require_POST
def doc_line_add(request, key, pk):
    doc = _doc(key)
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    _, row = doc.get(actor=request.user, company_id=company_id, pk=pk)
    form = _line_form(request, doc, row, request.POST)
    if form.is_valid():
        try:
            doc.add_line(actor=request.user, company_id=company_id, pk=pk, values=form.cleaned_data)
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, "Added.")
            return redirect("inventory:store_detail", key=key, pk=pk)
    return _page(request, doc, pk, line_form=form, open_dialog="line-dialog")


@login_required
@require_POST
def doc_line_remove(request, key, pk, line_pk):
    doc = _doc(key)
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    try:
        doc.remove_line(actor=request.user, company_id=company_id, pk=pk, line_id=line_pk)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, "Line removed.")
    return redirect("inventory:store_detail", key=key, pk=pk)


@login_required
@require_POST
def doc_scan(request, key, pk):
    """One scan (a keyboard scanner types the code and presses Enter): add it."""
    doc = _doc(key)
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    code = request.POST.get("code", "").strip()[:500]
    try:
        made = doc.scan(actor=request.user, company_id=company_id, pk=pk, code=code)
    except ValidationError as exc:
        messages.error(request, f"{code}: " + "; ".join(exc.messages))
    else:
        if isinstance(made, list):
            messages.success(request, f"{code}: " + "; ".join(
                f"{line.qty.normalize():f} {line.uom.code} {line.item.code}"
                + (f" batch {line.batch.batch_no}" if line.batch_id else "") for line in made) + " added.")
        else:
            messages.success(request, f"{code}: counted, {made.item.code} now "
                                      f"{made.counted_qty.normalize():f} {made.item.base_uom.code}.")
    return redirect(reverse("inventory:store_detail", args=[key, pk]) + "#scan")


@login_required
@require_POST
def doc_action(request, key, pk, action):
    doc = _doc(key)
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    if action not in doc.actions:
        raise Http404("Unknown action.")
    run, success = doc.actions[action]
    try:
        result = run(request, company_id, pk)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, _say(result, success))
    return redirect("inventory:store_detail", key=key, pk=pk)


@login_required
@require_POST
def transfer_receive(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    form = f.ReceiveForm(request.POST)
    received, reasons = {}, {}
    for name, value in request.POST.items():
        if name.startswith("got_") and name[4:].isdigit() and value.strip() != "":
            try:
                received[int(name[4:])] = Decimal(value.strip())
            except InvalidOperation:
                messages.error(request, f"“{value}” is not a number.")
                return redirect("inventory:store_detail", key="transfers", pk=pk)
        if name.startswith("why_") and name[4:].isdigit():
            reasons[int(name[4:])] = value
    if not form.is_valid():
        messages.error(request, "Give the date it arrived.")
        return redirect("inventory:store_detail", key="transfers", pk=pk)
    try:
        transfers.receive(actor=request.user, company_id=company_id, transfer_id=pk,
                          received_date=form.cleaned_data["received_date"], received=received, reasons=reasons)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, "Received: the goods are in the store.")
    return redirect("inventory:store_detail", key="transfers", pk=pk)


@login_required
@require_POST
def count_save(request, pk):
    """Save the counted quantities typed on the count's lines."""
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    counted = {}
    for name, value in request.POST.items():
        if name.startswith("counted_") and name[8:].isdigit():
            value = value.strip()
            try:
                counted[int(name[8:])] = Decimal(value) if value else None
            except InvalidOperation:
                messages.error(request, f"“{value}” is not a number.")
                return redirect("inventory:store_detail", key="counts", pk=pk)
    try:
        adjustments.set_counted(actor=request.user, company_id=company_id, count_id=pk, counted=counted)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, "Counts saved.")
    return redirect("inventory:store_detail", key="counts", pk=pk)
