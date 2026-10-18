# Store & Inventory: masters, stock, documents, purchase, production, sales

Everything the panel's Inventory, Purchase, Production and Sales menus do, for
an app, a scanner, or the client's other systems (web shop, distributor app,
ERP). The company must have the **Store & Inventory** module (platform owner →
Companies → Feature access); without it every endpoint here answers
`permission_denied`.

**API keys:** `inventory:read` / `inventory:write`, `purchase:read` /
`purchase:write`, `production:read` / `production:write`, `sales:read` /
`sales:write`; the reports use `reports:read`. A key acts as the owner or
administrator who made it.

## One shape for every document

A store issue, a GRN, a sales order and a production order all answer the same
way, so a client handles them with one piece of code:

```json
{"id": 12, "kind": "store-issues", "number": "SI-2026-00012", "status": "draft",
 "status_label": "Draft", "date": "2026-10-16",
 "store": {"id": 3, "code": "RMS", "name": "Raw material store"}, "other_store": null,
 "party": {"id": 2, "code": "PRD", "name": "Production"}, "reference": null,
 "amount": null, "vat": null, "total": null, "created_at": "2026-10-16T09:12:00+06:00",
 "note": "", "details": {"received_by": "Karim"},
 "lines": [{"id": 41, "item": {...}, "batch": {...}, "qty": "20", "unit": "KG",
            "qty_base": "20", "base_unit": "KG", "details": {}}],
 "actions": ["post", "cancel"]}
```

- `party` is who it is with (department, supplier, customer - or a production
  order's product); `reference` the document it comes from (an issue's
  requisition, a GRN's order, an invoice's delivery).
- `details` holds every other value that kind keeps (vehicle, due date, who
  approved, a line's `condition` or `qty_received_base` ...).
- `actions` are the actions its status allows now; your permission is checked
  when you call one.
- Numbers are strings without trailing zeros (`"40000"`, `"82.5"`).

## The pattern, for every kind

| What | Endpoint |
|---|---|
| List (filters `status`, `search`) | `GET /api/v1/<area>/<kind>` |
| Start a draft | `POST /api/v1/<area>/<kind>` |
| One, with lines and actions | `GET /api/v1/<area>/<kind>/{id}` |
| Add a line / remove one | `POST …/{id}/lines` · `DELETE …/{id}/lines/{line_id}` |
| Scan a label in | `POST …/{id}/scan` `{"code": "P12-000045"}` |
| An action | `POST …/{id}/<action>` (submit, approve, post, reverse ...) |

| Area | Kinds |
|---|---|
| `inventory` | `requisitions`, `issues`, `returns`, `transfers` (+ `receive`), `adjustments`, `counts` (+ `counted`, `zero`), `opening` |
| `purchase` | `requisitions`, `orders`, `receipts` (+ `qc`), `returns` |
| `production` | `boms` (+ `activate`, `new-version`), `orders` (+ `use`, `return-materials`, `output`, `qc`), `materials` |
| `sales` | `orders` (+ `confirm`, `reserve`, `deliver`), `deliveries` (+ `invoice`), `invoices` (+ `issue`, `paid`), `returns` |

Each request field is the panel's form field under its API name (`warehouse_id`
where the form says Store); the same rules refuse the same mistakes, with the
panel's words in `error.fields`.

## Masters, stock and labels

- Items, units, categories, stores, bins, suppliers, customers:
  `GET`/`POST /api/v1/inventory/<master>`, `GET`/`PATCH …/{id}`,
  `POST …/{id}/status`.
- `GET /api/v1/inventory/stock` - what is where now (filters store, item, batch,
  type); values only with "See stock costs and values".
- `GET /api/v1/inventory/items/{id}/ledger` - every move over dates.
- `GET /api/v1/inventory/batches` - with expiry and days left.
- `GET /api/v1/inventory/labels/lookup?code=` - what a scanned code is.

## A store issue, step by step

```
POST /api/v1/inventory/issues        {"warehouse_id": 3, "department_id": 2, "issue_date": "2026-10-16"}
POST /api/v1/inventory/issues/12/lines {"item_id": 7, "qty": "20", "uom_id": 1}   ← first expiry first
POST /api/v1/inventory/issues/12/scan  {"code": "P12-000045"}                    ← or scan cartons
POST /api/v1/inventory/issues/12/post                                            ← the goods leave
```

## Sales: from order to paid

```
POST /api/v1/sales/orders                 customer, store, date
POST /api/v1/sales/orders/21/lines        juice from FGW; sugar with warehouse_id of RMS
POST /api/v1/sales/orders/21/confirm      reserves stock (someone else than who entered it)
POST /api/v1/sales/orders/21/deliver      one challan per store → {"made": [...], "skipped": []}
POST /api/v1/sales/deliveries/18/post     the goods leave
POST /api/v1/sales/deliveries/18/invoice  → the draft invoice
POST /api/v1/sales/invoices/17/issue · …/paid {"paid_on": "2026-11-10", "note": "bKash 8A77X"}
```

## Sending orders in from another system

`POST /api/v1/sales/intake` with an API key holding `sales:write`:

```json
{"customer_code": "CUS-0012", "customer_ref": "WEB-10045", "delivery_date": "2026-10-20",
 "lines": [{"item_code": "FG-0001", "qty": "20", "unit": "CARTON"},
           {"item_code": "RM-0001", "qty": "200", "unit": "KG"}]}
```

- A **draft** sales order (source `api`): customer and items by code, prices
  from the customer's price list, each line from its item's usual store.
  Nothing is confirmed or reserved; a person confirms it.
- The same customer and `customer_ref` again → `409 conflict` (send an
  `Idempotency-Key` too, for retries on a bad connection).
- Or send `text` (and `sender`) instead of lines: the order goes to the **AI
  order inbox**, which drafts it when the assistant is on (`message_status`
  `waiting` otherwise).

## Reports, dashboard, month-end

- `GET /api/v1/inventory/reports` - the 14 reports you may open;
  `GET …/reports/{slug}` (as data: columns, rows, totals) and
  `GET …/reports/{slug}/export?file_type=xlsx|pdf`. Valuation at any past date:
  `GET /api/v1/inventory/reports/valuation?as_of=2026-09-30`.
- `GET /api/v1/inventory/dashboard` - the dashboard's figures.
- `GET /api/v1/inventory/periods` (closed months, what blocks the next) ·
  `POST /api/v1/inventory/periods` `{"month": "2026-09-30"}` ·
  `POST …/periods/{id}/reopen` `{"reason": "..."}`.
