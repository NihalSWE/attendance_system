# Shared server-side tables — A15

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

## Template and browser contract

Mark exactly one table per rendered page:

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

Keep the table headers present for zero results. One queryset object must
produce one tbody row (group multi-segment leave into its request row).
Colspan empty rows are excluded from JSON. `render` extracts only the marked
table's escaped cells and preserves row/cell attributes through DataTables'
row metadata, including live badge hooks. Other cards and summary tables stay
ordinary HTML. The payroll footer remains the whole run's totals.

`base.html` loads the existing DataTables 2.3.4 script and the single
`base_template/js/tables.js` initializer when a view supplies `server_table`.
Remove that page's duplicate DataTables script and `data-enhance` marker.
Use the existing `vendor-controls.css`; **no stock vendor stylesheet**.
The counted, numbered HTML fallback keeps filters through `querystring`.

## Completed and remaining scope

Completed: Employees, Branches, Departments, Salary month, Overtime (including
Employee and Branch filters), Leave, Leave types, Holidays, Penalty rules,
My leave, My payslips, Approval inbox and Branch attendance.

Nihal is paused. His planned N9 converts the attendance list and device lists
(devices, enrollments, punches, messages, unresolved and device users) to this
contract. The platform company list already has its own server-side table.
Do not restart A15 or generate a Nihal prompt until Ajay requests it.

No migrations, dependency changes or environment variables are required.
