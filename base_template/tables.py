"""Server-side tables over existing, escaped Django row templates.

Call paginate() on an already authorised/scoped queryset, then render() as
usual. Mark ONE table with data-server-table. HTML remains a usable fallback;
DataTables draws receive only that table's cells, using the same access guards
and action markup. Search fields and column ordering are server-owned lists.
"""

from html.parser import HTMLParser

from django.core.paginator import Page, Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render as django_render


def integer(value, default, low=0, high=2_147_483_647):
    try:
        return min(high, max(low, int(value)))
    except (ValueError, TypeError):
        return default


def paginate(request, queryset, *, search=(), order=(), total=None):
    """Count/filter/order in SQL and fetch at most 100 rows (including fallback).

    An order entry is a field, a tuple of fields, or None for a computed/action
    column. The incoming column names, regex flags and ORM paths are never used.
    Counts describe the authorised queryset after the page's explicit filters,
    before and after the additional table search, respectively.
    """
    ajax = request.method == "GET" and request.GET.get("table") == "1"
    length = integer(request.GET.get("length" if ajax else "per_page"), 25, 10, 100)
    total = queryset.count() if total is None else total
    query = request.GET.get("search[value]" if ajax else "table_q", "").strip()[:200]
    if query and search:
        clause = Q()
        for field in search:
            clause |= Q(**{f"{field}__icontains": query})
        queryset = queryset.filter(clause).distinct()
    ordering = []
    for index in range(min(len(order), 8)):
        column = integer(request.GET.get(f"order[{index}][column]"), -1)
        if 0 <= column < len(order) and order[column]:
            fields = order[column]
            fields = (fields,) if isinstance(fields, str) else fields
            descending = request.GET.get(f"order[{index}][dir]") == "desc"
            ordering.extend(("-" if descending else "") + field for field in fields)
    if ordering:
        queryset = queryset.order_by(*ordering, "pk")
    else:
        queryset = queryset.order_by(*(queryset.query.order_by or ("pk",)), "pk")
    paginator = Paginator(queryset, length)
    if ajax:
        # Respect DataTables' offset, including an offset beyond the last row.
        start = integer(request.GET.get("start"), 0)
        page = Page(queryset[start:start + length], start // length + 1, paginator)
    else:
        page = paginator.get_page(request.GET.get("page"))
    request.server_table = {
        "ajax": ajax, "draw": integer(request.GET.get("draw"), 0),
        "total": total, "page": page, "query": query,
        "orderable": ",".join(str(i) for i, field in enumerate(order) if field),
        "columns": len(order), "start": max(0, page.start_index() - 1),
        "numbers": list(paginator.get_elided_page_range(page.number, on_each_side=2, on_ends=1)) if not ajax else [],
    }
    return page


class TableRows(HTMLParser):
    """Keep trusted template markup, including escaping and row data attributes."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.active = self.body = self.cell = False
        self.rows, self.cells, self.parts = [], [], []
        self.attrs, self.cell_attrs = {}, []
        self.empty = False

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "table" and "data-server-table" in attrs_dict:
            self.active = True
        elif self.active and tag == "tbody":
            self.body = True
        elif self.body and tag == "tr":
            self.cells, self.attrs, self.empty, self.cell_attrs = [], attrs_dict, False, []
        elif self.body and tag == "td":
            self.cell, self.parts = True, []
            self.cell_attrs.append(attrs_dict)
            self.empty |= "colspan" in attrs_dict
        elif self.cell:
            self.parts.append(self.get_starttag_text())

    def handle_startendtag(self, tag, attrs):
        if self.cell:
            self.parts.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        if tag == "td" and self.cell:
            self.cells.append("".join(self.parts))
            self.cell = False
        elif tag == "tr" and self.body:
            if self.cells and not self.empty:
                self.rows.append({**{str(i): cell for i, cell in enumerate(self.cells)},
                                  "DT_RowAttr": self.attrs, "DT_RowData": {"cellAttrs": self.cell_attrs}})
        elif tag == "tbody" and self.active:
            self.body = False
        elif tag == "table" and self.active:
            self.active = False
        elif self.cell:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        if self.cell:
            self.parts.append(data)

    def handle_entityref(self, name):
        self.handle_data(f"&{name};")

    def handle_charref(self, name):
        self.handle_data(f"&#{name};")


def render(request, template_name, context=None, **kwargs):
    table = getattr(request, "server_table", None)
    context = dict(context or {})
    if table:
        context["server_table"] = table
    response = django_render(request, template_name, context, **kwargs)
    if not table or not table["ajax"] or response.status_code != 200:
        return response
    parser = TableRows()
    parser.feed(response.content.decode(response.charset))
    if any(sum(key.isdigit() for key in row) != table["columns"] for row in parser.rows):
        raise ValueError("Server table headers and row cells must have the same length.")
    return JsonResponse({"draw": table["draw"], "recordsTotal": table["total"],
                         "recordsFiltered": table["page"].paginator.count, "data": parser.rows})
