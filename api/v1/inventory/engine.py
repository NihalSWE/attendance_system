"""The Store & Inventory API, built from the panels' own forms and services
(docs/inventory/00-PLAN.md Phase 9; the guide: docs/api/130-inventory.md).

Store & Inventory has a few hundred actions across masters, store documents,
purchase, production and sales. They follow one shape, so the endpoints are
made from short specifications instead of written out one by one:

- **Masters** (items, units, categories, stores, bins, suppliers, customers):
  list, add, one, change, active/inactive - with the panel's form and
  ``inventory.masters``.
- **Documents** (store issues, GRNs, sales orders...): list, start, one (with
  its lines and the actions open now), add a line, remove a line, scan a
  label, and one endpoint per action (submit, approve, post, reverse...).

Each endpoint is still a class of its own with its own ``@endpoint``
declaration (so its documentation page, Swagger entry and the documentation
test all see it), validates through the panel's form (``api.core.forms.checked``
- the API refuses exactly what the panel refuses) and calls the panel's
service. Every request field's explanation is the panel form's own label and
help text; every document answers in one shape (``InventoryDocumentSerializer``).
"""

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from django import forms as dj
from django.db import models
from rest_framework import serializers
from rest_framework.response import Response

from api.core.docs import PATH, QUERY, Param, endpoint
from api.core.errors import ApiError
from api.core.forms import checked, form_data, service_errors
from api.core.pagination import StandardPagination
from api.core.permissions import PanelRule
from api.core.serializers import StrictSerializer
from api.core.views import ApiView

ERRORS = ["not_authenticated", "invalid_token", "token_expired", "session_ended", "signature_required",
          "invalid_signature", "two_step_setup_required", "scope_missing", "permission_denied", "rate_limited",
          "server_error"]
WRITE_ERRORS = ERRORS + ["validation_error", "unknown_field"]
ONE = ["not_found"]
MODULE_NOTE = ("Store & Inventory must be enabled for the company (Companies → Feature access); without it "
               "every inventory endpoint answers permission_denied.")
ID_PARAM_NOTE = "From the list endpoint, or the id a create returned."


# --------------------------------------------------------------------------
# Small shared serializers
# --------------------------------------------------------------------------

class InventoryRefSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="Its id.")
    code = serializers.CharField(help_text="Its code or number (may be empty).")
    name = serializers.CharField(help_text="Its name, as the panel shows it.")


def ref(obj):
    if obj is None:
        return None
    code = getattr(obj, "code", None) or getattr(obj, "number", None) or getattr(obj, "batch_no", None) or ""
    name = getattr(obj, "name", None) or getattr(obj, "full_name", None) or str(obj)
    return {"id": obj.pk, "code": str(code), "name": str(name)}


def plain(value):
    """A value as JSON: dates ISO, decimals as strings, rows as references."""
    if value is None:
        return None
    if isinstance(value, models.Model):
        if hasattr(value, "get_full_name"):
            return {"id": value.pk, "code": "", "name": value.get_full_name() or getattr(value, "email", "")}
        return ref(value)
    if isinstance(value, Decimal):
        return format(value.normalize(), "f") if value == value.to_integral() else format(value, "f")
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    return value


class InventoryReasonSerializer(StrictSerializer):
    reason = serializers.CharField(max_length=255, help_text="Why - kept with the document and in the audit log.")


class InventoryNoteSerializer(StrictSerializer):
    note = serializers.CharField(max_length=255, required=False, allow_blank=True,
                                 help_text="A note, kept with the decision (may be empty).")


class LabelScanSerializer(StrictSerializer):
    code = serializers.CharField(max_length=500, help_text="What the scanner read: a package, batch, item or bin "
                                                           "label code, or a GS1-128 / QR value.")


class MasterStatusSerializer(StrictSerializer):
    status = serializers.ChoiceField(choices=["active", "inactive"],
                                     help_text="active, or inactive (kept, but no longer offered).")


# --------------------------------------------------------------------------
# A serializer from a panel form
# --------------------------------------------------------------------------

def api_name(name, form_field):
    if isinstance(form_field, dj.ModelMultipleChoiceField):
        return f"{name}_ids"
    if isinstance(form_field, dj.ModelChoiceField):
        return f"{name}_id"
    return name


def _label(name, form_field):
    return str(form_field.label or name.replace("_", " ").capitalize()).strip()


def describe(name, form_field, extra=""):
    text = _label(name, form_field).rstrip(".") + "."
    if form_field.help_text:
        text += " " + str(form_field.help_text).strip()
    return f"{text} {extra}".strip()


def _choices(form_field):
    return [value for value, _label in form_field.choices if value not in ("", None)]


def form_fields(form_class):
    """The form's fields as the panel shows them: some forms relax a field when
    they are built (a sales line's price defaults from the price list). The
    form is built once with a placeholder company - querysets are lazy, so no
    database is touched; a form that cannot be built so gives its declared fields."""
    from inventory.forms import CompanyForm

    try:
        form = form_class(company_id=0) if issubclass(form_class, CompanyForm) else form_class()
        return form.fields
    except Exception:  # noqa: BLE001 - the declared fields are a safe answer
        return form_class.base_fields


def input_serializer(form_class, name, *, skip=(), notes=None):
    """A strict serializer with the form's fields under their API names; every
    field explained by the form's label and help text (``notes`` adds to it)."""
    notes = notes or {}
    attrs, names = {}, {}
    for fname, f in form_fields(form_class).items():
        if fname in skip:
            continue
        api = api_name(fname, f)
        names[fname] = api
        note = notes.get(api, "")
        required = f.required and not isinstance(f, dj.BooleanField)
        common = {"required": required}
        if isinstance(f, dj.ModelMultipleChoiceField):
            attrs[api] = serializers.ListField(child=serializers.IntegerField(), **common,
                                               help_text=describe(fname, f, f"A list of ids. {note}"))
        elif isinstance(f, dj.ModelChoiceField):
            attrs[api] = serializers.IntegerField(allow_null=not required, **common,
                                                  help_text=describe(fname, f, f"Its id. {note}"))
        elif isinstance(f, dj.BooleanField):
            attrs[api] = serializers.BooleanField(required=False, help_text=describe(fname, f, f"true or false. {note}"))
        elif isinstance(f, dj.DateField):
            attrs[api] = serializers.DateField(allow_null=not required, **common,
                                               help_text=describe(fname, f, f"YYYY-MM-DD. {note}"))
        elif isinstance(f, dj.DecimalField):
            attrs[api] = serializers.DecimalField(max_digits=18, decimal_places=4, allow_null=not required, **common,
                                                  help_text=describe(fname, f, f"A number. {note}"))
        elif isinstance(f, dj.IntegerField):
            attrs[api] = serializers.IntegerField(allow_null=not required, **common,
                                                  help_text=describe(fname, f, f"A whole number. {note}"))
        elif isinstance(f, (dj.TypedChoiceField, dj.ChoiceField)):
            values = _choices(f)
            attrs[api] = serializers.ChoiceField(choices=values, **common,
                                                 help_text=describe(fname, f, f"One of: {', '.join(map(str, values))}. "
                                                                              f"{note}"))
        elif isinstance(f, dj.EmailField):
            attrs[api] = serializers.EmailField(allow_blank=not required, **common,
                                                help_text=describe(fname, f, note))
        else:
            attrs[api] = serializers.CharField(allow_blank=not required, max_length=getattr(f, "max_length", None),
                                               **common, help_text=describe(fname, f, note))
    cls = type(name, (StrictSerializer,), attrs)
    cls.form_names = names
    return cls


def to_form(values, names):
    """Validated API values -> the form's data (form field names, as a browser would post)."""
    back = {api: form for form, api in names.items()}
    data = {}
    for key, value in values.items():
        name = back.get(key, key)
        if value is None:
            data[name] = ""
        elif value is True:
            data[name] = "on"
        elif value is False:
            continue
        elif isinstance(value, (list, tuple)):
            data[name] = [str(v) for v in value]
        elif isinstance(value, Decimal):
            data[name] = format(value.normalize(), "f")
        elif isinstance(value, datetime.date):
            data[name] = str(value)
        else:
            data[name] = value
    return data


def validated(serializer_class, request, partial=False):
    body = serializer_class(data=request.data, partial=partial)
    body.is_valid(raise_exception=True)
    return body.validated_data


def _with_company(form_class, form_kwargs):
    """Company-bound forms take ``company_id``; a few plain ones do not."""
    from inventory.forms import CompanyForm

    if not issubclass(form_class, CompanyForm):
        form_kwargs = {k: v for k, v in form_kwargs.items() if k != "company_id"}
    return form_kwargs


def form_values(serializer_class, form_class, request, **form_kwargs):
    """The request checked by the serializer (fields) and the panel's form (rules): cleaned values."""
    form_kwargs = _with_company(form_class, form_kwargs)
    data = validated(serializer_class, request)
    form = checked(form_class, to_form(data, serializer_class.form_names), serializer_class.form_names,
                   **form_kwargs)
    return form.cleaned_data


def page(view, request, rows, build, serializer):
    paginator = StandardPagination()
    chunk = paginator.paginate_queryset(rows, request, view=view)
    return paginator.get_paginated_response(serializer([build(r) for r in chunk], many=True).data)


def search(rows, request, fields):
    from django.db.models import Q

    text = (request.query_params.get("search") or "").strip()
    if text and fields:
        query = Q()
        for name in fields:
            query |= Q(**{f"{name}__icontains": text})
        rows = rows.filter(query)
    return rows


class InventoryView(ApiView):
    """The base of every Store & Inventory endpoint."""

    permission_classes = [PanelRule]
    company_required = True
    throttle_scope = "write"
    read_scope, write_scope = "inventory:read", "inventory:write"


def view_class(name, base=InventoryView, **attrs):
    """A view class of its own (each endpoint needs one for its documentation)."""
    attrs.setdefault("permission_classes", [PanelRule])
    return type(name, (base,), attrs)


def camel(text):
    return "".join(part.capitalize() for part in text.replace("-", "_").replace("/", "_").split("_"))


# --------------------------------------------------------------------------
# The document shape
# --------------------------------------------------------------------------

class InventoryLineSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The line id (use it to remove the line).")
    item = InventoryRefSerializer(allow_null=True, help_text="The item.")
    batch = InventoryRefSerializer(allow_null=True, help_text="The batch (its number in code), when the item keeps batches.")
    store = InventoryRefSerializer(allow_null=True, help_text="The line's own store, when it has one (sales lines, returns).")
    location = InventoryRefSerializer(allow_null=True, help_text="The bin, when one is used.")
    qty = serializers.CharField(allow_null=True, help_text="The quantity, in unit.")
    unit = serializers.CharField(allow_null=True, help_text="The unit the quantity is in, e.g. CARTON.")
    qty_base = serializers.CharField(allow_null=True, help_text="The same quantity in the item's base unit.")
    base_unit = serializers.CharField(allow_null=True, help_text="The item's base unit, e.g. PCS.")
    unit_price = serializers.CharField(allow_null=True, help_text="Price or cost per unit, when the document "
                                                                  "has one (null when hidden or not used).")
    amount = serializers.CharField(allow_null=True, help_text="Line amount before VAT, when the document has one.")
    note = serializers.CharField(help_text="The line's note (may be empty).")
    details = serializers.DictField(help_text="Every other value this kind of line keeps (e.g. condition, "
                                              "qty_received_base, not_fefo_reason, system_qty), by its field name.")


class InventoryDocumentSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The document id.")
    kind = serializers.CharField(help_text="Which kind of document, e.g. store-issues, purchase-orders.")
    number = serializers.CharField(help_text="Its number, e.g. SI-2026-00012 (empty for a few kinds).")
    status = serializers.CharField(help_text="Its status code, e.g. draft, posted.")
    status_label = serializers.CharField(help_text="The status as the panel shows it.")
    date = serializers.CharField(allow_null=True, help_text="Its main date (YYYY-MM-DD).")
    store = InventoryRefSerializer(allow_null=True, help_text="The store it works on (the sending store of a transfer).")
    other_store = InventoryRefSerializer(allow_null=True, help_text="A second store: the receiving store of a transfer, "
                                                           "the output store of a production order.")
    party = InventoryRefSerializer(allow_null=True, help_text="Who it is with: department, supplier, customer - or the "
                                                     "product of a production order.")
    reference = InventoryRefSerializer(allow_null=True, help_text="The document it comes from (code = its number), e.g. "
                                                         "the requisition of an issue, the order of a GRN.")
    amount = serializers.CharField(allow_null=True, help_text="Total before VAT, for documents with prices.")
    vat = serializers.CharField(allow_null=True, help_text="VAT, for documents with prices.")
    total = serializers.CharField(allow_null=True, help_text="Total with VAT, for documents with prices.")
    created_at = serializers.CharField(help_text="When it was started (ISO 8601).")


class InventoryDocumentSerializer(InventoryDocumentSummarySerializer):
    note = serializers.CharField(help_text="Its note, purpose or reason (may be empty).")
    details = serializers.DictField(help_text="Every other value this kind of document keeps (dates, who "
                                              "approved or posted, vehicle, terms ...), by its field name.")
    lines = InventoryLineSerializer(many=True, help_text="Its lines, in order.")
    actions = serializers.ListField(child=serializers.CharField(), help_text="The actions open now by its status "
                                                                             "(POST …/{action}); your permission "
                                                                             "is checked when you call one.")


_SKIP_HEADER = {"id", "company", "created_at", "updated_at", "number", "status", "lines"}
_SKIP_LINE = {"id", "company", "created_at", "updated_at", "item", "batch", "location", "qty", "uom", "factor",
              "note", "label"}


def _concrete(model):
    return [f for f in model._meta.get_fields() if f.concrete and not f.auto_created]


def _qty_text(value):
    return None if value is None else plain(value)


def line_out(line, parent_field=None):
    if isinstance(line, dict):
        return line
    model = type(line)
    item = getattr(line, "item", None)
    factor = getattr(line, "factor", None) or Decimal("1")
    qty = next((getattr(line, name) for name in ("qty", "qty_change", "qty_received", "counted_qty")
                if getattr(line, name, None) is not None), None)
    uom = getattr(line, "uom", None)
    used = {"qty", "qty_change", "qty_received", "counted_qty", "unit_price", "unit_cost", "amount", "warehouse",
            "from_location", parent_field}
    details = {}
    for f in _concrete(model):
        if f.name in _SKIP_LINE or f.name in used:
            continue
        details[f.name] = plain(getattr(line, f.name))
    price = next((getattr(line, name) for name in ("unit_price", "unit_cost") if getattr(line, name, None) is not None),
                 None)
    amount = getattr(line, "amount", None)
    return {
        "id": line.pk, "item": ref(item), "batch": ref(getattr(line, "batch", None)),
        "store": ref(getattr(line, "warehouse", None)),
        "location": ref(getattr(line, "location", None) or getattr(line, "from_location", None)),
        "qty": _qty_text(qty), "unit": uom.code if uom else None,
        "qty_base": _qty_text((qty * factor).quantize(Decimal("0.0001")) if qty is not None else None),
        "base_unit": item.base_uom.code if item is not None else None,
        "unit_price": _qty_text(price), "amount": _qty_text(amount() if callable(amount) else amount),
        "note": getattr(line, "note", "") or "", "details": details,
    }


@dataclass
class Action:
    name: str                       # the URL verb: post, approve, reverse ...
    title: str
    summary: str
    what: list
    description: str
    call: object                    # (request, company_id, pk, data) -> anything
    roles: list
    body: object = None             # serializer class for the body, or None
    body_example: object = None
    statuses: tuple = ()            # the statuses it is offered in (for "actions")
    panel: str = ""                 # the panel page it mirrors (gate for Employee logins)
    response: object = None         # serializer for a non-document answer
    response_example: object = None
    response_build: object = None   # (result) -> data for ``response``
    status_code: int = 200


@dataclass
class DocSpec:
    key: str                        # e.g. store-issues (the documents' "kind", and part of the page ids)
    area: str                       # documentation menu area
    path: str                       # URL, e.g. inventory/issues
    title: str                      # Store issues
    singular: str                   # store issue
    about: str                      # what it is, one or two sentences
    model: type
    get: object                     # (actor, company_id, pk, code) -> (membership, row)
    queryset: object                # (request, company_id) -> rows the caller may see
    view_code: object               # permission(s) to see one
    read_roles: list
    write_roles: list
    scopes: tuple                   # (read scope, write scope)
    panel: dict                     # list / detail / create / lines / action -> panel page names
    date_field: str = ""
    store_field: str = ""
    other_store_field: str = ""
    party_field: str = ""
    reference_field: str = ""
    search: tuple = ("number",)
    totals: object = None           # (row) -> {"amount", "vat", "total"}
    lines_of: object = None         # (row) -> lines (default row.lines)
    parent_field: str = ""          # the line's FK to the document (left out of details)
    create_form: type = None
    create_form_kwargs: object = None   # (request, company_id) -> form kwargs
    create_call: object = None      # (actor, company_id, values) -> row
    create_example: object = None
    create_notes: dict = field(default_factory=dict)
    create_description: str = ""
    line_form: type = None
    line_form_kwargs: object = None     # (request, company_id, row) -> form kwargs
    add_line: object = None         # (actor, company_id, pk, values) -> line(s)
    line_example: object = None
    line_notes: dict = field(default_factory=dict)
    line_description: str = ""
    remove_line: object = None      # (actor, company_id, pk, line_id)
    scan: object = None             # (actor, company_id, pk, code) -> lines
    actions: list = field(default_factory=list)
    example_number: str = "DOC-2026-00001"


def _header(spec, row):
    totals = spec.totals(row) if spec.totals else None
    if totals is None and hasattr(row, "total"):
        totals = {"amount": row.amount, "vat": row.vat, "total": row.total}
    reference = getattr(row, spec.reference_field, None) if spec.reference_field else None
    return {
        "id": row.pk, "kind": spec.key, "number": getattr(row, "number", "") or "",
        "status": row.status, "status_label": row.get_status_display(),
        "date": plain(getattr(row, spec.date_field, None)) if spec.date_field else None,
        "store": ref(getattr(row, spec.store_field, None)) if spec.store_field else None,
        "other_store": ref(getattr(row, spec.other_store_field, None)) if spec.other_store_field else None,
        "party": ref(getattr(row, spec.party_field, None)) if spec.party_field else None,
        "reference": ref(reference),
        "amount": plain(totals["amount"]) if totals else None,
        "vat": plain(totals["vat"]) if totals else None,
        "total": plain(totals["total"]) if totals else None,
        "created_at": plain(row.created_at),
    }


def document_out(spec, row):
    from common.tenant import use_company

    used = {spec.date_field, spec.store_field, spec.other_store_field, spec.party_field, spec.reference_field,
            "note", "amount", "vat", "total"}
    details = {f.name: plain(getattr(row, f.name)) for f in _concrete(type(row))
               if f.name not in _SKIP_HEADER and f.name not in used}
    with use_company(row.company_id):
        lines = spec.lines_of(row) if spec.lines_of else row.lines.all().order_by("pk")
        lines = [line_out(line, spec.parent_field) for line in lines]
    note = getattr(row, "note", None) or getattr(row, "purpose", None) or getattr(row, "reason", None) or ""
    return {**_header(spec, row), "note": str(note), "details": details, "lines": lines,
            "actions": [a.name for a in spec.actions if not a.statuses or row.status in a.statuses]}


def summary_example(spec, **changes):
    return {"id": 12, "kind": spec.key, "number": spec.example_number, "status": "draft", "status_label": "Draft",
            "date": "2026-10-16", "store": {"id": 3, "code": "RMS", "name": "Raw material store"},
            "other_store": None, "party": None, "reference": None, "amount": None, "vat": None, "total": None,
            "created_at": "2026-10-16T09:12:00+06:00", **changes}


LINE_EXAMPLE = {"id": 41, "item": {"id": 7, "code": "RM-0001", "name": "Sugar (refined)"},
                "batch": {"id": 18, "code": "S-261012-01", "name": "S-261012-01"}, "store": None, "location": None,
                "qty": "50", "unit": "KG", "qty_base": "50", "base_unit": "KG", "unit_price": None, "amount": None,
                "note": "", "details": {}}


def document_example(spec, **changes):
    return {**summary_example(spec), "note": "", "details": {}, "lines": [LINE_EXAMPLE],
            "actions": [a.name for a in spec.actions][:2], **changes}


# --------------------------------------------------------------------------
# Endpoints for a document kind
# --------------------------------------------------------------------------

def _id_param(spec):
    return Param("document_id", PATH, "integer", f"The {spec.singular}'s id. {ID_PARAM_NOTE}", required=True,
                 example=12)


LIST_PARAMS = [Param("status", QUERY, "string", "Only this status, e.g. draft.", example="draft"),
               Param("search", QUERY, "string", "Text in its number or names.", example="SI-2026")]


def _one(spec, request, pk):
    with service_errors():
        _m, row = spec.get(actor=request.user, company_id=request.company_id, pk=pk, code=spec.view_code)
    return row


def _get_or_404(spec, request, pk):
    if not spec.model.objects.filter(pk=pk).exists():
        raise ApiError("not_found", f"No such {spec.singular} in this company.")
    return _one(spec, request, pk)


def document_urls(spec):
    """The URL patterns (and their view classes) of one document kind."""
    from django.urls import path as url

    slug = spec.key
    name = camel(slug)
    base = dict(read_scope=spec.scopes[0], write_scope=spec.scopes[1])
    urls = []

    # --- list and start ---------------------------------------------------
    @endpoint(id=slug, area=spec.area, title=spec.title, summary=f"The {spec.title.lower()} you may see.",
              what_it_does=[f"Lists {spec.title.lower()}, newest first, in the branches you may see.",
                            "Filters: status, search."],
              description=f"{spec.about} {MODULE_NOTE}", roles=spec.read_roles, scopes=[spec.scopes[0]],
              params=LIST_PARAMS, paginated=True, response=InventoryDocumentSummarySerializer,
              response_example={"count": 1, "next": None, "previous": None, "results": [summary_example(spec)]},
              errors=ERRORS + ["validation_error"])
    def list_get(self, request):
        rows = spec.queryset(request, request.company_id)
        status = request.query_params.get("status")
        if status:
            rows = rows.filter(status=status)
        rows = search(rows, request, spec.search).order_by("-pk")
        return page(self, request, rows, lambda r: _header(spec, r), InventoryDocumentSummarySerializer)

    attrs = {"get": list_get, "panel_page": spec.panel["list"]}
    if spec.create_call:
        create_input = input_serializer(spec.create_form, f"{name}CreateSerializer", notes=spec.create_notes)

        @endpoint(id=f"{slug}-create", area=spec.area, title=f"Start a {spec.singular}",
                  summary=f"A new {spec.singular}, as a draft.",
                  what_it_does=[f"Starts the {spec.singular} with its number; records it in the audit log.",
                                "Answers the whole document, lines included."],
                  description=(spec.create_description or spec.about) + " The same form and rules as the panel.",
                  roles=spec.write_roles, scopes=[spec.scopes[1]], request=create_input, response=InventoryDocumentSerializer,
                  response_status=201, request_example=spec.create_example,
                  response_example=document_example(spec), errors=WRITE_ERRORS)
        def list_post(self, request):
            kwargs = spec.create_form_kwargs(request, request.company_id) if spec.create_form_kwargs else {}
            values = form_values(create_input, spec.create_form, request, company_id=request.company_id, **kwargs)
            with service_errors(create_input.form_names):
                row = spec.create_call(request.user, request.company_id, values)
            return Response(InventoryDocumentSerializer(document_out(spec, _one(spec, request, row.pk))).data, status=201)

        attrs["post"] = list_post
        attrs["panel_page"] = {"GET": spec.panel["list"], "POST": spec.panel.get("create", spec.panel["list"])}
    cls = view_class(f"{name}ListView", **base, **attrs)
    urls.append(url(spec.path, cls.as_view(), name=f"{slug}"))

    # --- one ---------------------------------------------------------------
    @endpoint(id=f"{slug}-one", area=spec.area, title=f"One {spec.singular}",
              summary=f"A {spec.singular} with its lines and the actions open now.",
              what_it_does=["Answers the document, every line, and which actions its status allows."],
              description=spec.about, roles=spec.read_roles, scopes=[spec.scopes[0]], params=[_id_param(spec)],
              response=InventoryDocumentSerializer, response_example=document_example(spec), errors=ERRORS + ONE)
    def one_get(self, request, document_id):
        return Response(InventoryDocumentSerializer(document_out(spec, _get_or_404(spec, request, document_id))).data)

    cls = view_class(f"{name}OneView", **base, get=one_get, panel_page=spec.panel["detail"])
    urls.append(url(f"{spec.path}/<int:document_id>", cls.as_view(), name=f"{slug}_one"))

    # --- lines -----------------------------------------------------------------
    if spec.add_line:
        line_input = input_serializer(spec.line_form, f"{name}InventoryLineSerializer", notes=spec.line_notes)

        @endpoint(id=f"{slug}-lines-add", area=spec.area, title=f"Add a line to a {spec.singular}",
                  summary="One more line on the draft.",
                  what_it_does=["Adds the line (or the lines it splits into, first expiry first) and answers the "
                                "whole document."],
                  description=(spec.line_description or "Only while it is a draft.") + " The panel's rules apply.",
                  roles=spec.write_roles, scopes=[spec.scopes[1]], params=[_id_param(spec)], request=line_input,
                  response=InventoryDocumentSerializer, request_example=spec.line_example,
                  response_example=document_example(spec), errors=WRITE_ERRORS + ONE)
        def lines_post(self, request, document_id):
            row = _get_or_404(spec, request, document_id)
            kwargs = spec.line_form_kwargs(request, request.company_id, row) if spec.line_form_kwargs else {}
            values = form_values(line_input, spec.line_form, request, company_id=request.company_id, **kwargs)
            with service_errors(line_input.form_names):
                spec.add_line(request.user, request.company_id, row.pk, values)
            return Response(InventoryDocumentSerializer(document_out(spec, _one(spec, request, row.pk))).data)

        cls = view_class(f"{name}LinesView", **base, post=lines_post, panel_page=spec.panel["lines"])
        urls.append(url(f"{spec.path}/<int:document_id>/lines", cls.as_view(), name=f"{slug}_lines"))

    if spec.remove_line:
        line_param = Param("line_id", PATH, "integer", "The line's id (lines[].id).", required=True, example=41)

        @endpoint(id=f"{slug}-lines-remove", area=spec.area, title=f"Remove a line from a {spec.singular}",
                  summary="Takes a line off the draft.", what_it_does=["Removes it and answers the document."],
                  description="Only while it is a draft.", roles=spec.write_roles, scopes=[spec.scopes[1]],
                  params=[_id_param(spec), line_param], response=InventoryDocumentSerializer,
                  response_example=document_example(spec), errors=WRITE_ERRORS + ONE)
        def line_delete(self, request, document_id, line_id):
            row = _get_or_404(spec, request, document_id)
            with service_errors():
                spec.remove_line(request.user, request.company_id, row.pk, line_id)
            return Response(InventoryDocumentSerializer(document_out(spec, _one(spec, request, row.pk))).data)

        cls = view_class(f"{name}LineView", **base, delete=line_delete, panel_page=spec.panel["lines"])
        urls.append(url(f"{spec.path}/<int:document_id>/lines/<int:line_id>", cls.as_view(),
                        name=f"{slug}_line"))

    if spec.scan:
        @endpoint(id=f"{slug}-scan", area=spec.area, title=f"Scan a label into a {spec.singular}",
                  summary="Adds what a package, batch or item label stands for.",
                  what_it_does=["Reads the code like the panel's scan box and adds the line(s); answers the "
                                "document."],
                  description="A package label brings its item, batch and quantity; a label already on the "
                              "document, or one used up or void, is refused.",
                  roles=spec.write_roles, scopes=[spec.scopes[1]], params=[_id_param(spec)], request=LabelScanSerializer,
                  response=InventoryDocumentSerializer, request_example={"code": "P12-000045"},
                  response_example=document_example(spec), errors=WRITE_ERRORS + ONE)
        def scan_post(self, request, document_id):
            row = _get_or_404(spec, request, document_id)
            code = validated(LabelScanSerializer, request)["code"]
            with service_errors():
                spec.scan(request.user, request.company_id, row.pk, code)
            return Response(InventoryDocumentSerializer(document_out(spec, _one(spec, request, row.pk))).data)

        cls = view_class(f"{name}ScanView", **base, post=scan_post, panel_page=spec.panel["lines"])
        urls.append(url(f"{spec.path}/<int:document_id>/scan", cls.as_view(), name=f"{slug}_scan"))

    # --- actions ----------------------------------------------------------------
    for action in spec.actions:
        urls.append(_action_url(spec, action, base))
    return urls


def _action_url(spec, action, base):
    from django.urls import path as url

    slug = spec.key
    when = (f" Offered while it is {', '.join(action.statuses).replace('_', ' ')}." if action.statuses else "")

    @endpoint(id=f"{slug}-{action.name}", area=spec.area, title=action.title, summary=action.summary,
              what_it_does=action.what, description=action.description + when, roles=action.roles,
              scopes=[spec.scopes[1]], params=[_id_param(spec)], request=action.body,
              response=action.response or InventoryDocumentSerializer, response_status=action.status_code,
              request_example=action.body_example if action.body else None,
              response_example=action.response_example or document_example(spec), errors=WRITE_ERRORS + ONE + (
                  ["conflict"] if action.name in ("post", "approve", "confirm", "dispatch") else []))
    def act(self, request, document_id):
        row = _get_or_404(spec, request, document_id)
        data = validated(action.body, request) if action.body else {}
        with service_errors():
            result = action.call(request, request.company_id, row.pk, data)
        if action.response is not None:
            return Response(action.response(action.response_build(result)).data, status=action.status_code)
        return Response(InventoryDocumentSerializer(document_out(spec, _one(spec, request, row.pk))).data,
                        status=action.status_code)

    cls = view_class(f"{camel(slug)}{camel(action.name)}View", **base, post=act,
                     panel_page=action.panel or spec.panel["action"])
    return url(f"{spec.path}/<int:document_id>/{action.name}", cls.as_view(), name=f"{slug}_{action.name}")


# --------------------------------------------------------------------------
# Masters
# --------------------------------------------------------------------------

@dataclass
class MasterSpec:
    key: str                # items, units ...
    path: str               # inventory/items
    title: str
    singular: str
    page: object            # inventory.views.PAGES entry (form, master, queryset)
    about: str
    write_roles: list
    read_roles: list
    scopes: tuple
    panel: str              # the panel page name (inventory:items)
    create_example: dict
    extra_out: object = None        # (row) -> dict of extra output values
    extra_fields: dict = field(default_factory=dict)   # extra output serializer fields
    out_example: dict = None


def _out_serializer(spec):
    attrs = {"id": serializers.IntegerField(help_text=f"The {spec.singular}'s id."),
             "status": serializers.CharField(help_text="active or inactive.")}
    for fname, f in spec.page.form.base_fields.items():
        if isinstance(f, dj.ModelChoiceField):
            attrs[fname] = InventoryRefSerializer(allow_null=True, help_text=describe(fname, f))
        elif isinstance(f, dj.BooleanField):
            attrs[fname] = serializers.BooleanField(help_text=describe(fname, f))
        elif isinstance(f, (dj.DecimalField, dj.FloatField)):
            attrs[fname] = serializers.CharField(allow_null=True, help_text=describe(fname, f, "A number, as text."))
        elif isinstance(f, dj.IntegerField):
            attrs[fname] = serializers.IntegerField(allow_null=True, help_text=describe(fname, f))
        else:
            attrs[fname] = serializers.CharField(allow_null=True, help_text=describe(fname, f))
    attrs.update(spec.extra_fields)
    return type(f"{camel(spec.key)}MasterSerializer", (serializers.Serializer,), attrs)


def master_out(spec, row):
    out = {"id": row.pk, "status": row.status}
    for fname, f in spec.page.form.base_fields.items():
        value = getattr(row, fname, None)
        if isinstance(f, dj.ModelChoiceField):
            out[fname] = ref(value)
        elif isinstance(f, (dj.DecimalField, dj.FloatField)):
            out[fname] = plain(value)
        elif isinstance(f, (dj.BooleanField, dj.IntegerField)):
            out[fname] = value
        else:
            out[fname] = None if value is None else (plain(value) if not isinstance(value, str) else value)
    if spec.extra_out:
        out.update(spec.extra_out(row))
    return out


def master_urls(spec):
    from django.urls import path as url

    from inventory import masters as services
    from inventory.views import branch_ids

    out = _out_serializer(spec)
    create_input = input_serializer(spec.page.form, f"{camel(spec.key)}InputSerializer")
    names = create_input.form_names
    base = dict(read_scope=spec.scopes[0], write_scope=spec.scopes[1])
    slug = f"inventory-{spec.key}"
    example = spec.out_example or {"id": 7, "status": "active"}
    singular_key = {"categories": "category"}.get(spec.key, spec.key[:-1] if spec.key.endswith("s") else spec.key)
    id_param = Param(f"{singular_key}_id", PATH, "integer",
                     f"The {spec.singular}'s id.", required=True, example=7)
    pk_name = id_param.name

    def form_kwargs(request):
        kwargs = {"company_id": request.company_id}
        if spec.page.branch_scoped:
            kwargs["branch_ids"] = branch_ids(request.user, request.company_id, services.CODE)
        return kwargs

    @endpoint(id=slug, area="inventory", title=spec.title, summary=f"The company's {spec.title.lower()}.",
              what_it_does=[f"Lists {spec.title.lower()}, by code.", "Filters: status, search."],
              description=f"{spec.about} {MODULE_NOTE}", roles=spec.read_roles, scopes=[spec.scopes[0]],
              params=[Param("status", QUERY, "string", "active or inactive.", example="active"),
                      Param("search", QUERY, "string", "Text in its code or name.", example="sugar")],
              paginated=True, response=out,
              response_example={"count": 1, "next": None, "previous": None, "results": [example]},
              errors=ERRORS + ["validation_error"])
    def list_get(self, request):
        from inventory import access

        access.require(request.user, request.company_id, "inventory.view")
        rows = spec.page.queryset(request, request.company_id)
        status = request.query_params.get("status")
        if status in ("active", "inactive"):
            rows = rows.filter(status=status)
        rows = search(rows, request, spec.page.search).order_by("code") if hasattr(spec.page.master.model, "code") \
            else search(rows, request, spec.page.search)
        return page(self, request, rows, lambda r: master_out(spec, r), out)

    @endpoint(id=f"{slug}-create", area="inventory", title=f"Add a {spec.singular}",
              summary=f"A new {spec.singular}.", what_it_does=["Adds it; records it in the audit log."],
              description=f"{spec.about} The same form and rules as the panel.", roles=spec.write_roles,
              scopes=[spec.scopes[1]], request=create_input, response=out, response_status=201,
              request_example=spec.create_example, response_example=example, errors=WRITE_ERRORS)
    def list_post(self, request):
        values = form_values(create_input, spec.page.form, request, **form_kwargs(request))
        with service_errors(names):
            row = services.create(spec.page.master, actor=request.user, company_id=request.company_id, values=values)
        return Response(out(master_out(spec, row)).data, status=201)

    cls = view_class(f"{camel(spec.key)}MasterListView", **base, get=list_get, post=list_post,
                     panel_page={"GET": spec.panel, "POST": spec.panel})
    urls = [url(spec.path, cls.as_view(), name=slug)]

    def _row(pk):
        row = spec.page.master.model.objects.filter(pk=pk).first()
        if row is None:
            raise ApiError("not_found", f"No such {spec.singular} in this company.")
        return row

    @endpoint(id=f"{slug}-one", area="inventory", title=f"One {spec.singular}", summary=f"A {spec.singular}.",
              what_it_does=["Answers it."], description="Change it with PATCH; turn it off with POST …/status.",
              roles=spec.read_roles, scopes=[spec.scopes[0]], params=[id_param], response=out,
              response_example=example, errors=ERRORS + ONE)
    def one_get(self, request, **kwargs):
        from inventory import access

        access.require(request.user, request.company_id, "inventory.view")
        return Response(out(master_out(spec, _row(kwargs[pk_name]))).data)

    @endpoint(id=f"{slug}-change", area="inventory", title=f"Change a {spec.singular}",
              summary="The fields sent; the others stay.", what_it_does=["Changes it; records it in the audit log."],
              description="Send only what changes. The panel's rules apply (e.g. a code stays unique).",
              roles=spec.write_roles, scopes=[spec.scopes[1]], params=[id_param], request=create_input,
              response=out, request_example={k: v for k, v in list(spec.create_example.items())[:1]},
              response_example=example, errors=WRITE_ERRORS + ONE)
    def one_patch(self, request, **kwargs):
        row = _row(kwargs[pk_name])
        changes = validated(create_input, request, partial=True)
        data = form_data(spec.page.form, row, to_form(changes, names), {}, **form_kwargs(request))
        form = checked(spec.page.form, data, names, instance=row, **form_kwargs(request))
        values = {k: v for k, v in form.cleaned_data.items() if k in spec.page.master.fields}
        with service_errors(names):
            row = services.update(spec.page.master, actor=request.user, company_id=request.company_id, pk=row.pk,
                                  values=values)
        return Response(out(master_out(spec, row)).data)

    cls = view_class(f"{camel(spec.key)}MasterOneView", **base, get=one_get, patch=one_patch,
                     panel_page={"GET": spec.panel, "PATCH": f"{spec.panel}_edit"})
    urls.append(url(f"{spec.path}/<int:{pk_name}>", cls.as_view(), name=f"{slug}_one"))

    @endpoint(id=f"{slug}-status", area="inventory", title=f"Turn a {spec.singular} on or off",
              summary="Active, or inactive (kept for history, no longer offered).",
              what_it_does=["Sets the status; records it in the audit log."],
              description="Nothing is deleted: documents that used it keep it.", roles=spec.write_roles,
              scopes=[spec.scopes[1]], params=[id_param], request=MasterStatusSerializer, response=out,
              request_example={"status": "inactive"}, response_example={**example, "status": "inactive"},
              errors=WRITE_ERRORS + ONE)
    def status_post(self, request, **kwargs):
        row = _row(kwargs[pk_name])
        status = validated(MasterStatusSerializer, request)["status"]
        with service_errors():
            row = services.set_status(spec.page.master, actor=request.user, company_id=request.company_id,
                                      pk=row.pk, status=status)
        return Response(out(master_out(spec, row)).data)

    cls = view_class(f"{camel(spec.key)}MasterStatusView", **base, post=status_post,
                     panel_page=f"{spec.panel}_status")
    urls.append(url(f"{spec.path}/<int:{pk_name}>/status", cls.as_view(), name=f"{slug}_status"))
    return urls
