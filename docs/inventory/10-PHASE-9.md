# Phase 9 — The Store & Inventory API (2026-10-17)

Phase 9 of [00-PLAN.md](00-PLAN.md): "Same conventions as the existing API:
items, stock, labels lookup, documents (create/submit/approve/post), sales
orders (incl. an order-intake endpoint for the client's other systems),
reports; docs pages + Postman." **Done:** 205 endpoints (the whole API: 508),
proven by `api.tests.test_inventory` (12 flows and security checks, through
the API only) and the API's documentation test (every endpoint, field and
error documented). The client guide: [docs/api/130-inventory.md](../api/130-inventory.md).

---

## 1. What it covers

| Area | Endpoints |
|---|---|
| Store & inventory (111) | masters (items, units, categories, stores, bins, suppliers, customers); stock, item ledger, batches, label lookup; store requisitions, issues, material returns, transfers (receive), adjustments, counts (counted, zero), opening stock; 14 reports as data and Excel/PDF; dashboard; month-end close |
| Purchase (36) | requisitions, orders, goods receipts (QC), returns |
| Production (24) | BOMs (activate, new version), production orders (use, return materials, output, QC), material use |
| Sales (34) | orders (confirm, reserve, deliver - one challan per store), deliveries (invoice), invoices (issue, paid), returns; **order intake** |

## 2. How it is built

Same rules as every other area of the API (docs/api/05-how-the-api-is-built.md):
signed requests or API keys, the panels' permission gate (`PanelRule`), the
company header, one error shape, paging, `Idempotency-Key`, rate limits.

Because Store & Inventory follows one pattern everywhere, its endpoints are
made from short specifications (`api/v1/inventory/engine.py`):

- each **document kind** (`DocSpec`) names its panel forms, its services and
  its actions; the engine makes list, start, one, add line, remove line, scan
  and one endpoint per action - each a view class of its own with its own
  documentation page;
- each **master** (`MasterSpec`) reuses the panel's `PAGES` entry;
- the request fields are **the panel form's fields** under API names
  (`warehouse_id`), explained by the form's labels and help texts; input is
  validated by the panel's form and saved by the panel's service, so the API
  refuses exactly what the panel refuses;
- every document answers in **one shape** (header, `details`, lines,
  `actions`), so a client needs one piece of code for all of them.

The panel's report builder, report download and dashboard were moved into
shared functions (`inventory.report_views.build`, `export`,
`dashboard_figures`) that the panel and the API both call.

## 3. Order intake

`POST /api/v1/sales/intake` (API key with `sales:write`): lines by customer and
item **code** become a draft sales order (prices from the customer's price
list, each line from its item's store); `customer_ref` stops a repeat
(`409 conflict`). Or `text` goes to the AI order inbox (Phase 8). Nothing is
confirmed or reserved: a person confirms.

## 4. API key scopes

New: `inventory:read`/`write`, `purchase:read`/`write`,
`production:read`/`write`, `sales:read`/`write` (reports: `reports:read`).

## 5. Deploy

Pull, `migrate` (nothing new), `collectstatic`, restart. Make API keys for the
client's systems in the company panel (Settings → API keys) with only the
scopes each needs.

## 6. Next

Phase 10 - go-live ([11-GO-LIVE.md](11-GO-LIVE.md)): masters loaded, the stock count day, labels, training,
the parallel run, then the switch.
