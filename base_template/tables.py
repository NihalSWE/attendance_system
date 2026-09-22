"""Server-side tables over existing, escaped Django row templates.

Call paginate() on an already authorised/scoped queryset, then render() as
usual. Mark the table with data-server-table. HTML remains a usable fallback;
DataTables draws receive only that table's cells, using the same access guards
and action markup. Search fields and column ordering are server-owned lists.

A page with several lists names each one: paginate(..., name="shifts") and
data-server-table="shifts". Its draws send table=shifts and its HTML fallback
uses shifts_page / shifts_per_page / shifts_table_q, so the lists page
independently. An unnamed table keeps table=1 and page / per_page / table_q.
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


def _search_and_order(request, queryset, *, search, order, query_param, use_order):
    """The table search and column ordering, read from the request.

    One implementation for the page (``paginate``) and for a download of the
    same table (``table_queryset``), so a download sorts and searches exactly
    as the screen did. Returns ``(queryset, query, sorted_by)`` where
    ``sorted_by`` is ``[(column_index, descending)]`` as applied.
    """
    query = request.GET.get(query_param, "").strip()[:200]
    if query and search:
        clause = Q()
        for field in search:
            clause |= Q(**{f"{field}__icontains": query})
        queryset = queryset.filter(clause).distinct()
    ordering, sorted_by = [], []
    if use_order:
        for index in range(min(len(order), 8)):
            column = integer(request.GET.get(f"order[{index}][column]"), -1)
            if 0 <= column < len(order) and order[column]:
                fields = order[column]
                fields = (fields,) if isinstance(fields, str) else fields
                descending = request.GET.get(f"order[{index}][dir]") == "desc"
                ordering.extend(("-" if descending else "") + field for field in fields)
                sorted_by.append((column, descending))
    if ordering:
        queryset = queryset.order_by(*ordering, "pk")
    else:
        queryset = queryset.order_by(*(queryset.query.order_by or ("pk",)), "pk")
    return queryset, query, sorted_by


def table_queryset(request, queryset, *, search=(), order=(), name=""):
    """The rows a download of this table should hold: every page, not one.

    Reads what the table on screen was showing - its search box and its sort,
    which the download link carries as ``search[value]`` / ``order[i][...]``
    (base_template/js/export_links.js), or the no-script ``table_q``. Returns
    ``(queryset, query, sorted_by)`` like ``_search_and_order``.

    Not namespaced yet (Ajay, 2026-09-21): ``search[value]`` and
    ``order[i][...]`` carry no table name, so on a page with two server tables
    a download would take whichever table's search is in the URL. No page with
    a download has two tables today. Before adding one that does, namespace
    these (``{name}_search`` / ``{name}_order...``) here and in export_links.js.
    """
    prefix = f"{name}_" if name else ""
    param = "search[value]" if "search[value]" in request.GET else f"{prefix}table_q"
    return _search_and_order(request, queryset, search=search, order=order,
                             query_param=param, use_order=True)


def paginate(request, queryset, *, search=(), order=(), total=None, name=""):
    """Count/filter/order in SQL and fetch at most 100 rows (including fallback).

    An order entry is a field, a tuple of fields, or None for a computed/action
    column. The incoming column names, regex flags and ORM paths are never used.
    Counts describe the authorised queryset after the page's explicit filters,
    before and after the additional table search, respectively.
    """
    prefix = f"{name}_" if name else ""
    params = {"page": f"{prefix}page", "per_page": f"{prefix}per_page", "query": f"{prefix}table_q"}
    ajax = request.method == "GET" and request.GET.get("table") == (name or "1")
    length = integer(request.GET.get("length" if ajax else params["per_page"]), 25, 10, 100)
    total = queryset.count() if total is None else total
    queryset, query, _sorted = _search_and_order(
        request, queryset, search=search, order=order,
        query_param="search[value]" if ajax else params["query"],
        # Another list's draw on the same page must not reorder this one.
        use_order=ajax,
    )
    paginator = Paginator(queryset, length)
    if ajax:
        # Respect DataTables' offset, including an offset beyond the last row.
        start = integer(request.GET.get("start"), 0)
        page = Page(queryset[start:start + length], start // length + 1, paginator)
    else:
        page = paginator.get_page(request.GET.get(params["page"]))

    def link(number):
        values = request.GET.copy()
        values.pop("table", None)
        values[params["page"]] = number
        return f"?{values.urlencode()}"

    numbers = []
    if not ajax:
        for number in paginator.get_elided_page_range(page.number, on_each_side=2, on_ends=1):
            current, dots = number == page.number, number == paginator.ELLIPSIS
            numbers.append({"number": number, "current": current, "dots": dots,
                            "url": None if current or dots else link(number)})
    table = {
        "ajax": ajax, "draw": integer(request.GET.get("draw"), 0), "name": name,
        "total": total, "page": page, "query": query, "params": params,
        "orderable": ",".join(str(i) for i, field in enumerate(order) if field),
        "columns": len(order), "start": max(0, page.start_index() - 1), "numbers": numbers,
        "previous": link(page.previous_page_number()) if not ajax and page.has_previous() else None,
        "next": link(page.next_page_number()) if not ajax and page.has_next() else None,
    }
    tables = getattr(request, "server_tables", {})
    tables[name] = table
    request.server_tables = tables
    if not name:
        request.server_table = table
    return page


class TableRows(HTMLParser):
    """Keep trusted template markup, including escaping and row data attributes."""

    def __init__(self, name=""):
        super().__init__(convert_charrefs=False)
        self.name = name
        self.active = self.body = self.cell = False
        self.rows, self.cells, self.parts = [], [], []
        self.attrs, self.cell_attrs = {}, []
        self.empty = False

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "table" and "data-server-table" in attrs_dict:
            self.active = (attrs_dict["data-server-table"] or "") == self.name
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
    tables = getattr(request, "server_tables", {})
    context = dict(context or {})
    if tables:
        context["server_tables"] = tables
    if "" in tables:
        context["server_table"] = tables[""]
    response = django_render(request, template_name, context, **kwargs)
    table = next((table for table in tables.values() if table["ajax"]), None)
    if not table or response.status_code != 200:
        return response
    parser = TableRows(table["name"])
    parser.feed(response.content.decode(response.charset))
    if any(sum(key.isdigit() for key in row) != table["columns"] for row in parser.rows):
        raise ValueError("Server table headers and row cells must have the same length.")
    return JsonResponse({"draw": table["draw"], "recordsTotal": table["total"],
                         "recordsFiltered": table["page"].paginator.count, "data": parser.rows})
