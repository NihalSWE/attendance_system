# Shared server-side tables — A15

> **2026-09-15 update (Claude, Ajay's session):** A15 is finished on Ajay's side. The disputed pages were checked against the real local data: they work, but most have one page of rows at the default 25, so the pager shows only "1". The lists A15 had missed are now converted too (Shifts page lists, company department list, root Departments/Designations). The main attendance list and device lists remain N9. Ajay's own in-browser acceptance is still pending; see [CLAUDE_HANDOFF_2026-09-15.md](CLAUDE_HANDOFF_2026-09-15.md).

Implemented 2026-09-15. Keep the existing Paper/Ink table styling and page
actions. Use numbered pages, first/last, 10/25/50/100 rows, a direct page jump,
and a real count. Search/order must cover the full authorised result set.

## View contract

Use the existing authenticated list URL for HTML and JSON; do not create a
second endpoint that bypasses its role, company, branch or employee guards.

```python
from base_template.tables import paginate, render

# Inside the existing guarded view and company scope:
page = paginate(
    request, queryset.order_by("name"),
    search=("code", "name"),
    order=("code", "name", None),  # One entry per table column; action has none.
)
return render(request, "app/list.html", {"page": page})
```

- Apply the page's explicit filters before `paginate`. Its `recordsTotal` is
  the authorised, explicitly filtered count; `recordsFiltered` additionally
  applies the table's search. Scope both counts, not just returned rows.
- `order` entries are an ORM field, a tuple of tie-break fields, or `None` for
  an action/live/composite column without a faithful SQL ordering. Never accept
  ORM field names from the client. Numeric snapshots need numeric annotations.
- JSON requests use `table=1`, `draw`, `start`, `length`, `search[value]` and
  `order[n][column/dir]`. Draw/offset are bounded integers; lengths are capped
  at 100, including `-1`. Search is literal, limited to 200 characters; regex
  and client column names are ignored. Ordering adds a stable PK tie-breaker.
- Fetch page objects before any Python row enrichment. Do not build a full
  list and paginate it afterwards. Overtime's computed counts/states use SQL
  projections, regression-checked against the existing calculation services.

## Several lists on one page (named tables)

A page with more than one list names each one. The Shifts page is the example:
Shifts, Department shifts and Weekly off days page, search and sort separately.

```python
shifts = paginate(request, Shift.objects.order_by("status", "name"), name="shifts",
                  search=("code", "name", "status"), order=("code", "name", ..., None))
```

```html
{% with server_table=server_tables.shifts %}
    <table class="table" data-server-table="shifts" data-orderable="{{ server_table.orderable }}" ...>
    {% include "base_template/includes/table_pagination.html" %}
{% endwith %}
```

- A named list's draws send `table=<name>`; only that list is returned as JSON
  and only that list's order parameters apply.
- Its HTML fallback uses `<name>_page`, `<name>_per_page` and `<name>_table_q`,
  and keeps the other lists' parameters, so each list keeps its own page.
- An unnamed table is unchanged (`table=1`, `page`, `per_page`, `table_q`) and is
  still available as `server_table`; every table is in `server_tables`.
- Values that describe the whole company (the Shifts page's readiness warning)
  are computed from the full queryset, never from one page of rows.

## Template and browser contract

Mark the table:

```html
<table class="table" data-server-table
       data-orderable="{{ server_table.orderable }}"
       data-length="{{ server_table.page.paginator.per_page }}"
       data-start="{{ server_table.start }}"
       data-query="{{ server_table.query }}">
    <!-- Existing thead/tbody and autoescaped cells/actions -->
</table>
{% include "base_template/includes/table_pagination.html" %}
```

Keep the table headers present when a search finds nothing (test the list's
`server_table.total`, not the page's rows). One queryset object must produce one
tbody row (group multi-segment leave into its request row). Colspan empty rows
are excluded from JSON. `render` extracts only the requested table's escaped
cells and preserves row/cell attributes through DataTables' row metadata,
including live badge hooks. Other cards and summary tables stay ordinary HTML.
The payroll footer remains the whole run's totals.

`base.html` loads the existing DataTables 2.3.4 script and the single
`base_template/js/tables.js` initializer when a view supplies a server table.
Remove that page's duplicate DataTables script and `data-enhance` marker (the
root platform base skips its own DataTables script on such pages).
Use the existing `vendor-controls.css`; **no stock vendor stylesheet**.
The counted, numbered HTML fallback keeps filters in its links.

With 25 rows per page, a list of 25 or fewer rows shows a single page "1".
That is correct behaviour, not a missing pager; choose 10 rows to see paging
on a short list.

## Completed and remaining scope

Completed (Ajay's side):

| Area | Lists |
|---|---|
| Employees | All employees |
| Organisation | Branches, Departments (adoptions), the older company department list |
| Salary | Salary by month, Overtime (with Employee and Branch filters), Penalty rules |
| Leave | Leave list, Leave types, Approval inbox |
| Shifts | Shifts, Department shifts, Weekly off days (one page, named tables), Holidays |
| Employee panel | My leave requests, My payslips |
| Manager panel | Approval inbox, Branch attendance |
| Root panel | Departments, Designations; Companies already had its own server-side table |

Intentionally ordinary HTML: bounded summary tables (Upcoming holidays, a
payslip's lines, detail pages, the second table on Salary settings and My leave).

Remaining — N9 (Nihal, paused, unless Ajay reassigns it): the main Attendance
→ Daily list and the device lists (devices, enrollments, punches, messages,
unresolved and device users). They still use a fixed Django page or
browser-only paging.

No migrations, dependency changes or environment variables are required.
