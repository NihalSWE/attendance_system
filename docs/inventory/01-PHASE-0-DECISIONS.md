# Phase 0 — Decisions to agree, and the data to collect (2026-10-15)

Phase 0 of [00-PLAN.md](00-PLAN.md) is done when the decisions below are
written down and agreed and the Excel templates have been sent to the client.
This document is the meeting pack: take it to the client, fill in the
**Answer** lines, and get both sign-offs at the end.

Each decision has a **recommendation**. If the client has no strong view, we
build the recommendation. Every answer names the phase that needs it, so a
late answer only holds up that phase.

| Who | Role in Phase 0 |
|---|---|
| Client (Healthy Choice) | answers the questions, fills the templates, names the 5 managers |
| Client's accountant | VAT, valuation, numbering year, accounting software |
| Senior | approves the decisions and the approval chains |
| Nihal / Ajay | run the meeting, send the templates, write the answers here |

---

## 1. The meeting, step by step (about 2 hours)

1. Show the whole flow on one page (00-PLAN.md, "How the goods move").
2. Walk the store: count the stores, look at the racks, check Wi-Fi, look at
   how goods are labelled today.
3. Go through sections 2–6 of this document and write the answers.
4. Open the Excel templates together and fill two or three real rows of each,
   so they see how it works.
5. Agree a date for the filled templates to come back.
6. Sign-off (section 8).

---

## 2. Decisions that change how stock is stored

### D2 — How stock is valued: weighted average cost — needed for Phase 1

**What it means:** each item has one average cost. Every receipt updates it,
and every issue goes out at the current average.

> Example: 100 kg sugar in stock at ৳120. We receive 100 kg at ৳130.
> The average becomes (100×120 + 100×130) ÷ 200 = **৳125**. The next 50 kg
> issued to production costs 50 × 125 = **৳6,250**.

| Option | Good | Less good |
|---|---|---|
| **Weighted average, one per item for the whole company (recommended)** | Simplest to explain and check; same cost in every store | A store far away with more expensive transport has the same cost |
| Weighted average, one per item per store | Each store has its own cost | A transfer between stores changes both averages; harder to check |
| FIFO by batch | Cost follows the exact batch | More work for the accountant; can be added later because every move keeps its batch |

**Recommendation:** weighted average, one per item for the whole company.
**Answer:** ______________________

### D12 — Money, VAT and numbering — needed for Phase 3 (purchase) and Phase 6 (sales)

| Question | Recommendation | Answer |
|---|---|---|
| Currency | BDT only (purchase from abroad entered in BDT at the landed cost) | |
| VAT on purchase lines | Store the VAT rate and amount on every line, from the start | |
| VAT on sales | Rate per item (standard 15%; accountant to confirm each item) | |
| Who makes the sales invoice? | This system makes the invoice (Mushak-6.3 layout if the accountant wants it), or only a delivery challan and accounts make the invoice | |
| Payment collection / customer balance | **Later** (not in the first release) | |
| Document numbers restart each… | Calendar year (`PO-2026-00001`), or the July–June financial year | |
| Accounting software in use (Tally, other)? | If yes, we push documents to it later, like the ERP webhook | |

---

## 3. Barcodes, labels and hardware — needed for Phase 2

### D3 — What gets a label

| Label | Barcode | Size | What scanning it fills in |
|---|---|---|---|
| **Item** (shelf/bin card, loose items) | Code 128 of the item code, or the product's own EAN-13 if it has one | 50 × 25 mm | item |
| **Batch / package** (each bag, drum, carton, roll) | **QR code** with item + batch + expiry + quantity, and a **Code 128** of the label number under it | 100 × 50 mm | item, batch, expiry, quantity |
| **Location** (store, rack, bin) | Code 128 of the bin code | 50 × 25 mm | where |

**Why both a QR and a Code 128 on the package label:** every label has its
own number in our system, and that number alone tells us the item, batch,
expiry and quantity. So a cheap 1-D scanner (Code 128 only) works everywhere,
and phones or 2-D scanners can read the QR as well.

**GS1-128** (the international supermarket format) only if a customer asks
for it. **Do any customers (supermarket chains, exporters) require GS1
labels on cartons?** Answer: ______________________

**Do the finished goods already have EAN-13 barcodes printed on the
bottle/pack?** Answer: ______________________ (if yes, list them in the items
template, column "Barcode").

### D4 — Printers, scanners, phones

| Question | Recommendation | Answer |
|---|---|---|
| Label printer | A thermal-transfer label printer, 203 dpi, 4-inch wide (Zebra ZD-series, TSC TE-series, Xprinter class). We print a PDF; no special driver from us | |
| Label sizes | 50 × 25 mm and 100 × 50 mm, as above | |
| Label material | Polyester or coated paper with resin ribbon for cold/wet stores; plain direct-thermal paper fades | |
| How many printers, where | One at the main store (GRN) and one at the finished-goods store (production output) | |
| Scanners | 2-D handheld scanners (read QR and Code 128), USB or wireless; one per store desk | |
| Phones/tablets in the stores? | Optional; any phone with a camera and a browser can scan | |
| Wi-Fi in every store and the FG warehouse? | Needed for phones; desk PCs can be wired | |

---

## 4. Batches, expiry and quality — needed for Phase 3

### D8 — Quality check (QC) and expiry

| Question | Recommendation | Answer |
|---|---|---|
| Which new batches wait in **Quarantine** until QC passes? | Raw materials and food-contact packaging (bottles, preforms, caps, pouches). Not spare parts or general store | |
| Who does QC, and who may release a batch? | QC officer (`purchase.qc` permission) | |
| What happens to a failed batch? | Moved to a **Rejected** store and returned to the supplier (purchase return) | |
| Days before expiry to warn | 30 days by default; can be set per item (e.g. 90 for finished goods sent to distributors) | |
| Batch number when the supplier gives none | We make one: `YYMMDD-NN` (date received + counter), supplier's number kept if there is one | |
| Finished goods batch number format | `item code-YYMMDD-line` or the format already printed on the packs — **show us a pack** | |
| Picking the batch | The system suggests **first expiry, first out**; picking another batch needs a reason | |
| Expired, quarantined or blocked batches | Can never be issued, used in production or delivered (a rule, not a choice) | |

---

## 5. Stores, approvals and the month-end

### Stores

**How many factories/branches and stores, and their names?** Fill the
`01-stores.xlsx` template. Typical setup:

| Store | Type |
|---|---|
| Raw material store | Raw material |
| Packaging store | Packaging |
| Finished goods warehouse | Finished goods |
| Spare parts store | Spare parts |
| General store | General |
| Production floor | Production floor (materials issued to production sit here) |
| Quarantine | Quarantine (new batches waiting for QC) |
| Rejected | Rejected (failed QC, damaged returns) |

### Approval chains — the senior approves this table

"Approves" is a branch permission given on the Access page, so it can be
given to any person. Amount limits per approver can be added later.

| Document | Who creates it | Who approves it (recommended) | Amount limit? | Answer |
|---|---|---|---|---|
| Purchase requisition (PR) | Store keeper / department | Store in-charge or factory manager | | |
| Purchase order (PO) | Purchase officer | Factory manager; above ৳______ the MD | | |
| Goods receipt (GRN) | Store keeper | No approval; posting = received (QC separately) | | |
| Store requisition | Any department | Department head | | |
| Store issue | Store keeper | No approval (issues against an approved requisition) | | |
| Material return | Department | Store keeper receives it | | |
| Stock transfer | Store keeper (sending) | Receiving store keeper confirms by scan | | |
| Stock adjustment / count difference | Store keeper | Factory manager or accounts (`inventory.adjust_approve`) | | |
| Production order | Production manager | Factory manager | | |
| Sales order | Sales officer / AI draft | Sales manager | | |
| Delivery challan | FG store keeper | No approval (against a confirmed order) | | |

**Rule we never relax:** stock cannot go below zero. If the system says
there is not enough, the stock must be received or counted first.

### Month-end lock (D9)

| Question | Recommendation | Answer |
|---|---|---|
| Who closes a month? | Accounts, with `inventory.period_close` | |
| When? | By the 5th of the next month; after that, back-dated entries are refused and corrections go in the open month | |

---

## 6. Production, sales and the AI assistant

### Production — needed for Phase 5

| Question | Recommendation | Answer |
|---|---|---|
| A BOM (recipe) for every product, with its normal batch size | Fill `05-boms.xlsx`, one row per material | |
| Count labour and overhead in the product cost? | Materials only at first; labour/overhead per batch later | |
| Production lines and shifts | Names of the lines (Line 1, PET line…) | |
| Normal wastage % per material | In the BOM template, column "Scrap %" | |

### Sales — needed for Phase 6

| Question | Recommendation | Answer |
|---|---|---|
| Customer types | Distributor, dealer, retailer, corporate | |
| Price lists | One price list per customer type; special prices per customer later | |
| Credit limits | A warning on the order, a manager can still confirm | |

### AI sales orders — needed for Phase 8

| Question | Recommendation | Answer |
|---|---|---|
| How do orders arrive today? | Phone, WhatsApp, email, salesman? | |
| First channel for the AI | A company email inbox (e.g. orders@…); WhatsApp Business later (needs Meta approval) | |
| Order summary + stock status after each confirmed order | To all 5 managers by email | |
| Daily report | 6 pm every working day | |
| Weekly report | Saturday 10 am | |
| Written consent to send order text to the AI provider (Anthropic) | Required before Phase 8 | |

**The 5 managers who receive orders and reports:**

| # | Name | Designation | Email | WhatsApp number | Receives: orders / stock / daily / weekly / alerts |
|---|---|---|---|---|---|
| 1 | | | | | |
| 2 | | | | | |
| 3 | | | | | |
| 4 | | | | | |
| 5 | | | | | |

---

## 7. The templates and opening stock

The templates are in [templates/](templates/). Each file has three sheets:
**How to fill** (every column explained), **Example** (real-looking rows)
and the sheet to fill. Codes must be unique; columns marked * are required.
They are built by `docs/scripts/build_inventory_templates.py`; the headings
are what the Phase 1 importers read (the trailing `*` is ignored), so change
them there and in the importer together.

| File | What | Needed for |
|---|---|---|
| `01-stores.xlsx` | stores (warehouses) and their bins/racks | Phase 1 |
| `02-items.xlsx` | every item, with its type, unit, batch/expiry, reorder levels; pack units (1 carton = 24 bottles) | Phase 1 |
| `03-suppliers.xlsx` | suppliers | Phase 1 |
| `04-customers.xlsx` | customers | Phase 1 (used in Phase 6) |
| `05-boms.xlsx` | recipes: materials per batch of each product | Phase 5 |
| `06-opening-stock.xlsx` | stock counted on the opening day, per store, item and batch | Phase 10 (a trial load in Phase 1) |

**Opening stock date (question 11 in 00-PLAN.md):** recommendation — count
everything on the last day of a month after Phase 4 is live, and enter it as
the opening stock of the 1st. Labels are printed and stuck during the count.
**Opening date:** ______________________ **Who counts:** ______________________

**Item codes:** if the client has no codes, we suggest a prefix per type and a
number: `RM-0001` raw material, `PM-0001` packaging, `FG-0001` finished
goods, `SP-0001` spare part, `GS-0001` general store.

**Units:** KG, G, L, ML, PCS, BOX, CARTON, BAG, DRUM, ROLL, M, SET, PAIR. Tell
us if they use any other unit.

---

## 8. Sign-off

| Decision | Agreed (yes / changed to …) |
|---|---|
| D2 Valuation | |
| D3 Labels | |
| D4 Printers, scanners, Wi-Fi | |
| D8 QC, batches, expiry | |
| D12 VAT, invoices, numbering year | |
| Approval chains | |
| Month-end lock | |
| Opening stock date | |
| Date the templates come back | |

Client: ______________________ Date: __________

Senior: ______________________ Date: __________

---

## 9. What we start before every answer is in

Phase 1 (apps, masters, the stock ledger) can start with the
recommendations above, because every one of them is a setting or a field we
can change before go-live, except **D2**: per-company or per-store average
changes the cost table. We build the cost table so that both work (a store
column that stays empty for "per company"), and the setting decides.
