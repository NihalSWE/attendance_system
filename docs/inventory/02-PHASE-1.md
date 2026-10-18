# Phase 1 — Foundation, masters, the stock ledger and opening stock (2026-10-15)

Phase 1 of [00-PLAN.md](00-PLAN.md). **Done when:** a company with the
module on can set up every master, load opening stock, and the stock on
hand list matches the import exactly. The test
`inventory.tests_opening.ImportTests.test_the_whole_phase_one_flow_from_the_templates`
does exactly that with the Phase 0 templates' example rows.

Built on the Phase 0 recommendations ([01-PHASE-0-DECISIONS.md](01-PHASE-0-DECISIONS.md));
nothing here needs a client answer that is not yet in, and each choice is a
setting or a field that can change before go-live.

---

## 1. In plain words (for the senior and the client)

- Root turns on **Store & Inventory** for a company (Companies → the
  company → Feature access). Until then nothing of it shows: no menu, no
  pages, no permissions on the Access page.
- The company then sets up its **units** (one click adds KG, L, PCS, CARTON…),
  **item categories**, **items** (with pack units: 1 carton = 24 bottles),
  **stores** (each in a branch) and **bins**, **suppliers** and
  **customers** - one by one, or by uploading the filled Excel templates.
- **Opening stock**: the counted stock is entered or imported as a
  **draft**, checked, then **posted**. Posting puts it on hand and sets each
  item's first average cost. A posted opening stock is never edited; a
  mistake is undone with **Reverse** (needs a reason) and a corrected one is
  posted.
- **Stock on hand** shows every item × batch × store × bin with stock, with
  the average cost and value for those allowed to see costs.

Example: the opening stock template says 8 DRUM of mango pulp at 46,200 a
drum, and the item's pack unit says 1 DRUM = 220 KG. Posting puts
**1,760 kg** on hand at **210 a kg** (46,200 ÷ 220).

---

## 2. Apps and tables

| App | Tables (Phase 1) |
|---|---|
| `inventory` | `inventory_settings`, `inventory_uom`, `inventory_item_category`, `inventory_item`, `inventory_item_uom`, `inventory_warehouse`, `inventory_location`, `inventory_batch`, `inventory_stock_move`, `inventory_stock_balance`, `inventory_item_cost`, `inventory_document_sequence`, `inventory_period_lock`, `inventory_opening_stock`, `inventory_opening_stock_line` |
| `purchasing` | `purchasing_supplier` (PR/PO/GRN in Phase 3) |
| `sales` | `sales_customer` (orders in Phase 6) |
| `production` | none yet (Phase 5) |

Feature row `inventory` ("Store & Inventory", migration `tenants.0008`).

**One change from the plan's table list:** `StockBalance` has no `value`
column. With a moving average, a row's value is quantity × the item's
average, and storing it would mean rewriting every balance row of an item on
each receipt. Pages work it out when they show it (`stock_views._cost_subquery`).

---

## 3. The posting service (`inventory/posting.py`)

The only code that moves stock. `post(...)` takes lines in the base unit
(+ in / − out) for one document and, in one transaction:

1. locks the balance rows in one fixed order (item, batch, store, bin), then
   the cost rows - no deadlocks;
2. refuses: below zero; dipping into reserved stock (except a delivery);
   issuing, consuming or delivering a batch that is quarantined, blocked or
   expired (a transfer may move it); a batch missing for an item that keeps
   batches, or given for one that does not; more decimals than the unit
   allows; another company's rows; an inactive store; a closed month;
3. updates the **moving weighted average** (per company, or per store when
   chosen in Inventory settings - fixed once stock has moved);
4. writes `StockMove` rows (never updated or deleted: the model, its queryset
   and a reversal-only rule enforce it) and updates `StockBalance`.

`reverse(...)` writes counter-moves at the original cost, refused when the
stock is already used. `next_number` hands out `OS-2026-00001`-style numbers
per type and year under a row lock. `ledger_differences` is the nightly check.

**Every later document posts through `post` / `reverse`.** Phase 4's
transfers must pass the sending store's cost on the receiving line when a
company values per store.

---

## 4. Who may do what

| Permission (branch-level, Access page) | Opens |
|---|---|
| `inventory.view` View stock and items | every inventory list, item page, stock on hand, opening stock (read) |
| `inventory.items` Set up items, stores, suppliers and customers | add/edit/deactivate masters, imports; company masters need it in any branch, stores and bins in their branch |
| `inventory.adjust` Enter stock adjustments, counts and opening stock | opening stock drafts and their lines, the opening import |
| `inventory.adjust_approve` Approve and post adjustments and opening stock | Post and Reverse, in every store on the document |
| `inventory.valuation` See stock costs and values | costs and values on stock on hand, item page, opening stock |
| `inventory.labels`, `.issue`, `.transfer`, `.period_close` | offered now; their pages arrive in Phases 2, 4 and 7 |

Owner and company admin hold all of them; a branch manager all of them in
their branches; anyone else what the Access page gives. Inventory settings
stay with the owner and company admin. The purchase, production, sales and
AI permissions are added with their phases.

---

## 5. Screens (sidebar → Inventory)

Stock on hand · Items (and the item page: details, pack units, stock now,
stock card) · Opening stock · Stores · Bins · Units · Item categories ·
Suppliers · Customers · Inventory settings. Every list is the server-side
DataTable with search and filters; Add and Edit open in a modal; deactivate,
post, cancel, remove and reverse confirm in a modal.

Imports (Items, Stores, Suppliers, Customers, Opening stock pages →
"Import from Excel"): upload → preview (nothing written, every bad row named)
→ confirm (all or nothing, through the services). A code already present is
skipped, never changed. "Download the template" serves the Phase 0 file.

---

## 6. Files

- `inventory/`: `models.py`, `posting.py`, `masters.py`, `opening.py`,
  `imports.py`, `access.py`, `forms.py`, `views.py` (masters, settings),
  `stock_views.py`, `opening_views.py`, `import_views.py`, `urls.py`,
  templates, `static/inventory/`, `management/commands/check_stock.py`,
  tests (`tests_posting.py`, `tests_pages.py`, `tests_opening.py`, helpers in
  `testing.py`).
- `purchasing/` and `sales/`: the Supplier and Customer models and their
  master definitions; `production/`: an empty shell.
- Shared files touched: `config/settings.py` and `config/urls.py` (the
  apps); `access_control/branch_access.py` (the permissions, offered only
  with the module); `access_control/page_access.py` (pages for Employee
  logins given access); `organization/access_services.py` and
  `api/v1/access/views.py` (list only offered permissions);
  `base_template/navigation.py` and `context_processors.py` (the menu);
  `base_template/templates/base_template/includes/navigation_icon.html`
  (the menu icon, next to Ajay's sidebar file - **Ajay to review**);
  `tenants/platform_services.py` and migration `tenants.0008` (the feature).

---

## 7. Deploy

1. `git pull origin main`, `python manage.py migrate` (new apps + the
   feature row), `collectstatic`, restart.
2. Nothing changes for any company until root enables Store & Inventory.
3. Optional nightly timer: `python manage.py check_stock` (exits 1 and lists
   rows when the ledger and balances disagree; nothing to find until stock
   moves).

## 8. Next

Phase 2 - barcodes and scanning: labels (Code 128 / QR / GS1-128) for
items, batches, packages and bins, the scan box and phone camera scanning,
one lookup service. Needs D3/D4 (printer model, label sizes, scanners).
