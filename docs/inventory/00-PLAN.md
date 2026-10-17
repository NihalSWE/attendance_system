# Store & Inventory Management — plan of record (v1, 2026-10-07)

A new module for the existing multi-tenant HR, attendance and payroll product.
First client: **Healthy Choice Foods & Beverages Limited** (20, Shamoly,
Sreemangal, Moulvibazar) — a food and beverage manufacturer. Written for Nihal,
Ajay and their senior; every decision marked **(decide)** needs a yes before the
phase that depends on it starts.

---

## 1. What the client asked for, in plain words

> A complete store and inventory system for a food & beverage factory. **Every
> stock entry and movement goes through a barcode.** It covers Raw Materials,
> Packaging Materials, Finished Goods, Spare Parts and General Store.
> Modules: Purchase & Purchase Requisition, Goods Receipt Note (GRN), Barcode
> generation & scanning, Production & BOM, Store Issue, Material Return, Stock
> Transfer, Stock Adjustment, Inventory Valuation, Batch/Lot & Expiry, Sales
> Order Management, Real-time stock monitoring & reports.
> Plus **AI-based sales orders**: AI receives and processes sales orders, and
> automatically sends order information, stock status and management reports
> to **5 designated managers**.

### How the goods move (the whole factory on one line)

```
Supplier ─► Purchase Requisition ─► Purchase Order ─► GRN (+QC, batch, expiry, LABEL)
                                                          │
                                   ┌──────────────────────┘
                                   ▼
       Raw / Packaging / Spare / General stores  ──Store Issue──►  Departments / machines
                │  ▲                                              (Material Return ◄──)
                │  └── Stock Transfer between stores / branches
                ▼
        Production order (BOM) ── consume materials ──► Finished Goods batch (+LABEL)
                                                          │
       Customer ─► Sales Order (AI or manual) ─► reserve stock ─► Delivery (scan, FEFO)
                                                          ─► Invoice ─► (Sales Return)
   Everywhere: Stock Adjustment / physical count, valuation, expiry alerts, reports
```

### The five item types

| Type | Examples | Batch & expiry | Typical store |
|---|---|---|---|
| Raw Material | sugar, juice concentrate, tea leaves, flavours | **Yes** | Raw material store |
| Packaging Material | bottles, caps, labels, cartons, shrink film | Batch yes, expiry usually no | Packaging store |
| Finished Goods | 250 ml juice, 1 L drink, tea packs | **Yes** (made here) | FG warehouse |
| Spare Parts | bearings, belts, motors, seals | No (optional serial later) | Spare parts store |
| General Store | stationery, cleaning, safety items | No | General store |

---

## 2. How it fits the product we already have

| Already built | Reused for inventory |
|---|---|
| Company = tenant (`TenantOwned`, `use_company`, `TenantManager`) | Every inventory table is company-owned and scoped the same way |
| Root **Feature access** (attendance, leave, payroll, developer_api) | New feature **`inventory`** (and **`ai_sales`**), off until root enables it per company |
| Branches (factory, depot, office) | A warehouse/store belongs to a branch |
| Departments | Store issues and requisitions go **to a department** |
| Employees + logins + roles | Who requested, approved, issued, received |
| Branch permissions (A12, `BRANCH_PERMISSIONS`, granted dynamically) | New permission codes (section 6), granted per branch |
| Services pattern: `require_…`, `_writable` whitelist, `create_validated`, `@transaction.atomic`, audit (`record_company_event`) | Every inventory write is a service function — pages, API and AI call the same services |
| Server-side tables, Excel/PDF downloads (openpyxl, reportlab, Bangla text) | All lists and reports |
| `reportlab` (already installed) | **Barcode and QR labels** (Code 128, QR, GS1-128 are built in) |
| Company mail settings (SMTP per company) + payslip emailing | Sending orders and reports to the 5 managers |
| REST API conventions (`PanelRule`, `@endpoint`, docs site, Postman) | Inventory API phase, same style |
| Background work: management command + systemd timer (like `settle_attendance`) | Expiry alerts, reorder alerts, AI inbox polling, scheduled reports |

**New Django apps** (one per business area, so each stays readable):

| App | Owns |
|---|---|
| `inventory` | items, units, categories, warehouses/bins, batches, barcodes/labels, **stock ledger**, balances, valuation, transfers, adjustments, counts, store requisition/issue/return |
| `purchasing` | suppliers, purchase requisitions, purchase orders, GRN, quality check, purchase returns |
| `production` | BOMs, production orders, consumption, output, wastage |
| `sales` | customers, price lists, sales orders, deliveries, invoices, sales returns |
| `ai_assistant` | AI settings, the 5 recipients, inbound messages, extractions, sent reports, AI audit trail |

---

## 3. Key design decisions

**D1 — One stock ledger, never edited.** Every movement (receipt, issue,
transfer, production, delivery, adjustment) writes rows to `StockMove`
(+ quantity in, − quantity out) with item, batch, warehouse, cost and the
document it came from. Stock on hand = sum of moves. A `StockBalance` row per
item × batch × warehouse × bin is updated **in the same transaction** (row
locked) so screens read stock instantly. A posted document is never edited; a
mistake is undone by a **reversal** (counter-moves). This is what accountants
and auditors expect, and it is the same idea as attendance's saved results.

**D2 — Valuation: moving weighted average cost (WAC)** per item per company
(decide: per company or per warehouse). Every receipt recalculates the average;
issues go out at the current average. Finished-goods cost = materials consumed
(+ optional labour/overhead per batch later). FIFO-by-batch can be added later
because every move already carries its batch. Bangladesh manufacturers mostly
use WAC; it is simplest to explain and audit. **(decide)**

**D3 — Barcodes.** Every label is printed by the software and resolves to one
record.
- **Item barcode** (Code 128): the item's code, or the manufacturer's EAN-13
  when the product already has one (finished goods usually do).
- **Batch/package label** (Code 128 or QR, optionally **GS1-128**): item +
  batch + expiry + quantity, e.g. `(01)item (10)batch (17)expiry (30)qty`.
  One label per bag/drum/carton; scanning it fills item, batch, expiry and
  quantity in one go.
- **Location label**: each store/rack/bin, so a scan says *where*.
- Phase 1 labels batches and packages; pallet labels (SSCC) later if needed.
- Every scan is recorded on the document line (who, when, which label).

**D4 — Scanning hardware, no drivers.** USB/Bluetooth barcode scanners act as
a keyboard: a "scan box" on every transaction page receives the code and adds
or increases the line. Phones and tablets scan with the camera in the browser
(a small JS library, e.g. html5-qrcode/ZXing). Labels print as PDF sized for
the client's label printer (Zebra/TSC/Xprinter, e.g. 50×25 mm and 100×50 mm).
**(decide: printer model and label sizes; scanner models; Wi-Fi in stores)**

**D5 — Units of measure.** Each item has a base unit (kg, g, L, ml, pcs) and
optional pack units with factors (1 carton = 24 bottles, 1 bag = 50 kg).
Quantities are stored in the base unit (`Decimal(18,4)`); documents can be
entered in any defined unit and are converted.

**D6 — One document life cycle for all documents:**
`Draft → Submitted → Approved → Posted` (+ `Cancelled`; a posted document is
reversed, never deleted). Stock moves only on **Posted**. Each document type
has its own numbering per company and year (`PR-2026-00001`, `PO-…`, `GRN-…`,
`SI-…` store issue, `MR-…`, `ST-…`, `SA-…`, `PRD-…`, `SO-…`, `DC-…` delivery
challan, `INV-…`). Who may approve is a branch permission (section 6);
optional amount limits per approver later.

**D7 — No negative stock.** Posting refuses a move that would take a batch
below zero. Sales orders **reserve** stock (`qty_reserved`) so available =
on hand − reserved.

**D8 — Batch, expiry, quality.**
- GRN creates the batch (supplier's batch no. kept), with manufacture/expiry
  dates (expiry from shelf life if only the manufacture date is known).
- New raw-material batches can start in **Quarantine** until QC passes
  **(decide: is QC required, for which item types)**; quarantine and expired
  batches cannot be issued or sold.
- Issues, production and deliveries suggest **FEFO** (first expiry, first
  out); picking another batch needs a reason.
- Alerts: expiring in N days (per item or company default), expired.

**D9 — Month-end lock.** Like a finalised salary month: a closed month refuses
back-dated postings; corrections go in the open month.

**D10 — Background jobs** stay as today: management commands run by a systemd
timer (every 1–5 minutes for the AI inbox, daily for alerts and reports). No
Celery/Redis until volume needs it.

**D11 — AI with a human in the loop.** The AI reads incoming orders and
prepares a **draft** sales order; a person confirms it (decide later whether
trusted customers' small orders may auto-confirm). The AI never posts stock,
changes prices or deletes anything. Everything it does is logged. Details in
section 8.

**D12 — Money and quantities:** `Decimal`, never float; currency from the
company (BDT); VAT/tax fields on purchase and sales lines from the start
(rates and VAT rules: **decide** with the client's accountant).

---

## 4. Database design

All tables: `TenantOwned` (company) + `ActorTracked` (created/updated by/at)
unless noted. Codes unique per company. Statuses are `TextChoices`.

### 4.1 Masters (`inventory`)

| Table | Key fields |
|---|---|
| `Uom` | code (KG, G, L, ML, PCS, BOX…), name, kind (weight/volume/count/length), decimals |
| `ItemCategory` | code, name, **kind** (raw / packaging / finished / spare / general), parent (tree) |
| `Item` | code, name, name_bn, category, kind, base_uom, barcode (unique), **track_batch**, **track_expiry**, shelf_life_days, min_stock, reorder_point, reorder_qty, max_stock, default_warehouse, standard_cost, sale_price (FG), vat_rate, hs_code, image, status |
| `ItemUom` | item, uom, factor_to_base (1 carton = 24), barcode for that pack (optional) |
| `Warehouse` | branch, code, name, **kind** (raw / packaging / finished / spare / general / production floor / quarantine / in-transit / rejected), is_active |
| `Location` (bin) | warehouse, code (e.g. R1-S2-B3), barcode, is_active |
| `Supplier` (`purchasing`) | code, name, contact, phone, email, address, BIN/TIN, payment terms, items supplied (optional), status |
| `Customer` (`sales`) | code, name, type (distributor/dealer/retailer/corporate), contact, phone, **email / WhatsApp number** (for AI matching), address, credit limit, price list, territory, status |

### 4.2 Stock core (`inventory`)

| Table | Key fields |
|---|---|
| `Batch` | item, batch_no (unique per item), supplier_batch_no, mfg_date, expiry_date, **status** (quarantine / released / blocked / expired), source (GRN / production / opening), unit_cost |
| `BarcodeLabel` | **code** (unique), kind (item / batch / package / location), item, batch, uom, quantity, location, printed_count, last_printed_by/at, status (active / consumed / void) |
| `StockMove` (**ledger, immutable**) | posted_at, work_date, item, batch, warehouse, location, **qty** (+/−, base unit), unit_cost, value, **move_type** (opening, receipt, purchase_return, issue, issue_return, transfer_out, transfer_in, adjustment, count, production_consume, production_output, delivery, sales_return, reversal), document_type + document_id + document_line_id, label (scanned), reversed_move, posted_by. Indexes: (item, warehouse), (batch), (document_type, document_id), (posted_at) |
| `StockBalance` | item, batch, warehouse, location, **qty_on_hand**, **qty_reserved**, value. Unique (item, batch, warehouse, location). Updated only by the posting service, under `select_for_update` |
| `ItemCost` | item (+ warehouse if per-warehouse WAC), avg_cost, qty_basis, updated_at |
| `DocumentSequence` | doc_type, year, prefix, next_number (locked when used) |
| `PeriodLock` | month, locked_by/at |

### 4.3 Store documents (`inventory`) — each a header + lines

| Header / lines | Notes |
|---|---|
| `StoreRequisition` / `StoreRequisitionLine` | from a department/employee: item, qty, needed_by, purpose (machine, production order) |
| `StoreIssue` / `StoreIssueLine` | against a requisition (or direct): item, batch, qty, from warehouse/bin, to department, scanned label |
| `MaterialReturn` / `…Line` | back from department/production: item, batch, qty, condition (good / damaged), to warehouse |
| `StockTransfer` / `…Line` | from warehouse → to warehouse (another branch possible); status adds **In transit**: dispatch scan, receive scan, shortages noted |
| `StockAdjustment` / `…Line` | reason (damage, expiry write-off, found, theft, sample, correction), qty ±, approval required |
| `StockCount` / `StockCountLine` | physical/cycle count: system qty frozen at start, counted qty by scan, difference → posts an adjustment when approved |

### 4.4 Purchasing (`purchasing`)

| Header / lines | Notes |
|---|---|
| `PurchaseRequisition` / `…Line` | department, requested_by, item, qty, needed_by, reason; approval |
| `PurchaseOrder` / `PurchaseOrderLine` | supplier, PR links, item, qty, uom, unit_price, VAT, discount, delivery date, terms; status adds **Partly received / Closed** |
| `GoodsReceipt` (GRN) / `GoodsReceiptLine` | PO (or direct), supplier challan/invoice no., vehicle; line: item, **qty received / accepted / rejected**, batch_no, mfg/expiry, warehouse/bin, unit cost; QC result; posting creates batch + labels + receipt moves |
| `QualityCheck` | GRN line, checked_by, result (pass / fail / partial), notes, attachment |
| `PurchaseReturn` / `…Line` | supplier, GRN/batch, qty, reason (posts negative moves) |

### 4.5 Production (`production`)

| Table | Notes |
|---|---|
| `Bom` | finished item, **version**, batch_size + uom, status (draft/active/archived), effective_from |
| `BomLine` | component item, qty per batch_size, uom, scrap %, is_packaging |
| `ProductionOrder` | FG item, BOM version, planned qty, planned date, line/shift, status (planned → released → in progress → completed → closed) |
| `ProductionConsumption` / `…Line` | materials actually issued to the order: item, batch (FEFO), qty planned vs actual |
| `ProductionOutput` | FG batch created (batch no., mfg date, expiry from shelf life), qty good, qty rejected/wastage, labels printed |
| Cost on close | FG unit cost = materials consumed ÷ good qty (labour/overhead per batch later **(decide)**) |

### 4.6 Sales (`sales`)

| Table | Notes |
|---|---|
| `PriceList` / `PriceListLine` | name, valid from/to, item, price, min qty |
| `SalesOrder` / `SalesOrderLine` | customer, order date, delivery date, **source** (manual / email / WhatsApp / API / **AI**), ai_extraction link, status (draft → confirmed → partly delivered → delivered → closed / cancelled), line: item, qty, uom, price, discount, VAT, **qty_reserved**, qty_delivered |
| `Delivery` (challan) / `DeliveryLine` | SO, vehicle/driver, line: item, **batch (FEFO, by scan)**, qty; posting moves stock out |
| `SalesInvoice` / `…Line` | from deliveries; totals, VAT; (payment collection & customer ledger = later, **decide**) |
| `SalesReturn` / `…Line` | customer, invoice/batch, qty, condition, to warehouse (good / damaged) |

### 4.7 AI sales assistant (`ai_assistant`)

| Table | Notes |
|---|---|
| `AiSettings` (one per company) | enabled, model, **API key (encrypted, like mail passwords)**, intake channels on/off, mailbox (IMAP) settings, auto-confirm rules (off), report schedule, daily cost cap |
| `AiRecipient` | the **5 managers**: name, employee/login (optional), email, WhatsApp/phone, what they receive (new orders, stock status, daily report, weekly report, alerts), active |
| `InboundMessage` | channel (email / WhatsApp / web form / API), sender, subject, raw text, attachments, received_at, status (new → processed → needs review → converted → ignored) |
| `OrderExtraction` | message, JSON result (customer, lines, dates, notes), **confidence**, matched customer/items, problems found (unknown item, short stock, credit limit), model, tokens, cost, created sales order |
| `AiReportRun` | kind (order summary / stock status / daily / weekly / alert), period, text + attached PDF/Excel, recipients, sent_at, status |
| `NotificationOutbox` | channel, to, subject, body, attachments, status, attempts, last error (retries like the ERP webhook) |
| `AiActionLog` | every AI step: input reference, output, decision, user who confirmed |

---

## 5. Rules that must always hold (tested)

1. Stock moves only through the posting service; only Posted documents move stock.
2. Posting is one transaction: moves + balances + costs + document status together.
3. Balances are locked in a fixed order (item, batch, warehouse, bin) — no deadlocks.
4. No negative stock; no issue from quarantine/blocked/expired batches.
5. A posted document is never edited or deleted — reversal only.
6. A closed month refuses back-dated postings.
7. Every barcode is unique per company and resolves to exactly one label/item.
8. Every write is audit-logged; every list is company- and branch-scoped.
9. The AI only drafts; a person confirms; all AI output is logged.
10. Sum of `StockMove` = `StockBalance` for every item × batch × warehouse (a nightly check command reports any difference).

---

## 6. Permissions (branch-level, granted like today)

| Code | Allows |
|---|---|
| `inventory.view` | see items, stock, reports |
| `inventory.items` | create/edit items, units, categories, warehouses |
| `inventory.labels` | print and reprint labels |
| `inventory.issue` | store requisitions, issues, returns |
| `inventory.transfer` | transfers (dispatch / receive) |
| `inventory.adjust` / `inventory.adjust_approve` | adjustments and counts / approve them |
| `inventory.valuation` | see costs and stock value |
| `inventory.period_close` | close a month |
| `purchase.request` / `purchase.approve` | purchase requisitions / approve them |
| `purchase.order` / `purchase.receive` / `purchase.qc` | POs / GRN / quality check |
| `production.plan` / `production.execute` / `production.bom` | orders / consume & output / BOMs |
| `sales.order` / `sales.approve` / `sales.deliver` / `sales.invoice` | orders / confirm / deliver / invoice |
| `ai.settings` / `ai.review` | AI settings & recipients / review AI drafts |

Typical people: store keeper, purchase officer, QC officer, production
manager, sales officer, accounts, management (view + reports). Owner and
company admin get all. Costs are hidden from anyone without
`inventory.valuation` (as pay is hidden today).

---

## 7. Phases — step by step

Each phase: its own branch → code + tests + docs (`docs/inventory/NN-*.md`) +
`PHASE_STATUS.md` entry → full suite → merge → push → deploy when the client
is ready. Estimates are for two developers and are rough.

### Phase 0 — Discovery and sign-off (1 week)
- Meeting with the client: walk through section 11's questions; collect the
  item list, units, stores, suppliers, customers, BOMs, approval chains,
  label printer/scanner models, the 5 managers.
- Excel templates for them to fill: items, suppliers, customers, BOMs,
  opening stock.
- **Done when:** decisions D2, D3/D4, D8, D12 and the approval chains are
  written down and agreed; templates sent.

### Phase 1 — Foundation and masters (2 weeks)
- Apps `inventory`, `purchasing`, `production`, `sales` (empty shells for
  the last three); feature `inventory` in the root catalogue + migration; the
  permissions in section 6; sidebar section (Ajay's file — coordinate).
- Units, categories, items (+ pack units), warehouses, bins, suppliers,
  customers: list, create, edit, deactivate, **Excel import**.
- The **posting service** (`post_document`), `StockMove`, `StockBalance`,
  `ItemCost`, `Batch`, `DocumentSequence` — with heavy tests before any screen
  uses them.
- **Opening stock**: import or enter per item/batch/warehouse (posts
  `opening` moves).
- **Done when:** a company with the feature on can set up all masters, load
  opening stock, and the stock-on-hand list matches the import exactly.

### Phase 2 — Barcodes and scanning (1 week)
- Label generation (reportlab Code 128 / QR / GS1-128), label templates per
  size, print screens (one, many, reprint with reason), label register.
- The **scan box** component (keyboard scanners) + **camera scanning** on
  phones; one lookup service: code → item / batch / package / location.
- **Done when:** a label printed from the software scans back to the right
  item+batch+expiry+qty on a real scanner and a phone.

### Phase 3 — Purchase: requisition → order → GRN (2 weeks)
- Purchase requisition + approval; PO from PRs (or direct) + approval + PDF
  to email the supplier; GRN against PO (partial receipts), QC (quarantine →
  release), batch + expiry entry, **labels printed at GRN**, posting →
  receipt moves + WAC update; purchase return.
- **Done when:** PR → PO → two partial GRNs → QC → labels → stock and average
  cost correct; a purchase return reduces both.

### Phase 4 — Store operations (2 weeks)
- Store requisition → issue (scan, FEFO, blocks expired/quarantine), material
  return, stock transfer (dispatch → in transit → receive by scan, shortages),
  stock adjustment with reasons and approval, physical/cycle count by scan.
- **Done when:** each document posts the right moves, refuses negative stock
  and expired batches, and a count difference becomes an approved adjustment.

### Phase 5 — Production and BOM (2 weeks)
- BOM with versions, production orders, material consumption (by scan,
  FEFO), output of FG batches with labels, wastage/rejects, order close with
  cost roll-up; planned vs actual usage report.
- **Done when:** producing 1,000 bottles consumes the BOM's materials, creates
  an FG batch with expiry and labels, and its unit cost equals materials ÷
  good qty.

### Phase 6 — Sales orders, delivery, invoice (2 weeks)
- Customers, price lists, sales orders (manual) with stock check and
  reservation, credit-limit warning, delivery challan by scan (FEFO), invoice
  (PDF), sales return.
- **Any item can be sold**, not only finished goods: raw materials (sugar,
  flavours, tea leaves), packaging, spares - from the store that holds them,
  at the customer's price list or the item's sale price (client, 2026-10-15;
  in place since Phase 6, proven by `sales.tests_flow.RawMaterialSaleTests`).
- **Done when:** an order reserves stock, a delivery picks FEFO batches by
  scan and releases the reservation, and the invoice matches the delivery.

### Phase 7 — Valuation, monitoring and reports (1.5 weeks)
- Live dashboard: stock by type/warehouse, low stock/reorder list, expiring
  soon, quarantine, pending POs/GRNs/issues, today's production and sales.
- Reports (Excel/PDF, server-side tables): stock on hand, stock ledger/item
  card, movement summary, **valuation at a date**, batch & expiry, aging, slow
  /non-moving, purchase register, GRN register, consumption by department,
  production yield & wastage, sales register, customer/item sales, reorder
  report.
- Daily alerts (reorder, expiry) by email to chosen people; month-end close.
- **Done when:** the valuation report at any past date equals the ledger, and
  the nightly balance check reports zero differences.

### Phase 8 — AI sales order assistant (2 weeks) — see section 8
- **First, mixed-store sales orders** (client, 2026-10-15): one order for
  juice from the finished-goods store and sugar from the raw-material store.
  Each order line gets its own store (the order's store is the default);
  reservation, delivery (one challan per store), invoice (one per order or
  per challan) and returns follow the line's store and its branch. **Done
  2026-10-17** ([09-PHASE-8.md](09-PHASE-8.md)): one challan per store,
  one invoice per challan.
- **Done when:** a real email order in Bangla or English becomes a correct
  draft sales order with stock status in under a minute; a person confirms it;
  the 5 managers receive the order summary; daily and weekly reports arrive on
  schedule.

### Phase 9 — API (1.5 weeks)
- Same conventions as the existing API: items, stock, labels lookup,
  documents (create/submit/approve/post), sales orders (incl. an order-intake
  endpoint for the client's other systems), reports; docs pages + Postman.
- **Done 2026-10-17** ([10-PHASE-9.md](10-PHASE-9.md)): 205 endpoints.

### Phase 10 — Go-live (2 weeks)
- Load masters; **stock count day**: count everything, print and stick
  labels, load opening stock; train each role (store, purchase, QC,
  production, sales, managers); one to two weeks of parallel run with the old
  method; then switch. Hypercare after go-live.
- **Software ready 2026-10-17** ([11-GO-LIVE.md](11-GO-LIVE.md)): the Go-live
  checklist, count sheets that import as opening stock, opening stock labels,
  the parallel-run comparison, the runbook and training sheets. The go-live
  itself follows the runbook on site.

**Total: about 19 weeks (4–5 months)** for two developers. Phases 1–4 are the
minimum useful system (stores + purchase + barcodes); the client can start
using it after Phase 4 while production, sales and AI follow.

---

## 8. The AI sales order assistant in detail

**Channels (decide which):** a company email inbox read every 1–2 minutes
(IMAP); WhatsApp via the official WhatsApp Business Cloud API (needs a Meta
business account and an approved number); a simple web order form for
customers; the API. Start with **email** (no third-party approval needed).

**Pipeline for each incoming order:**
1. Save the raw message (`InboundMessage`) — nothing is lost.
2. **Extract** with Claude (`claude-opus-5-5`) using **structured outputs**
   (a JSON schema: customer, contact, order lines with product text and
   quantity/unit, requested delivery date, notes). Works for Bangla, English
   and mixed text. The product catalogue and customer list go in a cached
   system prompt (**prompt caching**) so each order costs little.
3. **Match** in our code, not by the AI alone: customer by sender email/phone
   then by name; each line to an `Item` (code, name, Bangla name, aliases);
   units converted. Anything unsure → flagged.
4. **Check** stock availability (available = on hand − reserved), credit
   limit, price list, duplicates (same customer, same lines, same day).
5. Create a **draft sales order** with the problems listed; notify the
   reviewer.
6. A person with `ai.review` confirms or corrects it (one screen: original
   message on the left, draft on the right). Corrections are saved to improve
   matching (item aliases).
7. On confirmation: reserve stock; send the **order summary + stock status**
   to the 5 managers; optional acknowledgement to the customer.

**Management reports (scheduled, decide times):** daily 6 pm and weekly
Saturday — orders received/confirmed/delivered, sales by item and customer,
stock status (low, expiring, out of stock), production done, pending
deliveries. The numbers come from the database; the AI only writes the short
summary paragraph ("what changed, what needs attention"). Sent by email with
PDF/Excel attached; WhatsApp later if approved.

**Safeguards:** human confirmation; AI never changes prices or posts stock;
every AI call logged with input, output, tokens and cost; a daily cost cap;
the client's written consent to send order text to the AI provider; the API
key stored encrypted per company; if the AI is unavailable, orders wait in
the inbox and can be entered by hand.

**Cost (estimate, to measure in a pilot):** roughly 3–6 US cents per order
with `claude-opus-5-5` and a cached catalogue; reports are a few cents a day.
A cheaper model can be chosen later if the client prefers.

---

## 9. Screens (menu)

Inventory: Dashboard · Items · Stock on hand · Batches & expiry · Labels ·
Store requisitions · Store issues · Material returns · Transfers ·
Adjustments · Stock counts · Reports · Settings (units, categories,
warehouses, bins, period close)
Purchase: Requisitions · Purchase orders · Goods receipts (GRN) · Quality
check · Purchase returns · Suppliers
Production: BOMs · Production orders · Output · Reports
Sales: Sales orders · AI inbox · Deliveries · Invoices · Sales returns ·
Customers · Price lists
AI settings: model & key · channels · the 5 recipients · report schedule

(The sidebar file is Ajay's; agree the menu with him.)

---

## 10. Testing approach

- Posting service: unit tests for every move type, reversal, negative stock,
  locks, WAC math, period lock (most important tests of the module).
- Every form is **submitted** in tests (not just opened); every new field is
  in its service's allowed-fields list.
- Scan flows: label → lookup → line, wrong/duplicate/expired label refused.
- AI: extraction tested with saved sample messages (Bangla, English, mixed,
  messy) against a fake client; no real API calls in the test suite.
- Nightly ledger = balance check command, run in tests on generated data.

---

## 11. Questions for the client (before the phases that need them)

1. How many factories/branches and stores? Names of each store.
2. Item count per type; do finished goods already have EAN barcodes?
3. Label printer model, label sizes; scanner models; Wi-Fi/phones in stores?
4. Valuation: weighted average OK? Per company or per store?
5. Which items need QC before use? Who does QC?
6. Approval chains: who approves PR, PO, issue, adjustment, sales order? Amount limits?
7. Expiry: shelf life per item; how many days before expiry to alert?
8. Production: BOM per product, batch sizes; count labour/overhead in cost?
9. Sales: customer types, price lists, VAT, credit limits; invoices from this
   system or from accounts software? Payment collection needed?
10. AI: which channels (email first?), which inbox, the 5 managers' names,
    emails and WhatsApp numbers, report times, consent to use an AI provider.
11. Opening stock date and how they will count it.
12. Do they need accounting integration (Tally / other ERP)? The ERP webhook
    pattern can push documents if yes.

---

## 12. Risks and how we handle them

| Risk | Handling |
|---|---|
| Stock goes wrong and nobody trusts it | Immutable ledger, reversal-only, nightly balance check, count & adjust flow |
| Labels not stuck or unreadable | Label at receipt and production; reprint with reason; scan-or-search fallback |
| Store Wi-Fi weak | Scanners work on any PC; phone scanning needs Wi-Fi — check in Phase 0 |
| AI misreads an order | Human confirms every draft; item aliases learn from corrections |
| Scope creep (accounts, HR links) | Accounting/payment collection marked "later" until decided |
| Two developers, many phases | Phases 1–4 first = usable stores system; release in steps |
