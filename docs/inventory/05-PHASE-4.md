# Phase 4 — Store operations (2026-10-15)

Phase 4 of [00-PLAN.md](00-PLAN.md). **Done when:** each document posts the
right moves, refuses negative stock and expired batches, and a count
difference becomes an approved adjustment. Proven by
`inventory.tests_store` (services) and `inventory.tests_store_pages` (every
document through its pages).

---

## 1. In plain words

| Document | What it does | Stock moves |
|---|---|---|
| **Store requisition** (SR-) | A department asks a store for materials; approved or rejected | none |
| **Store issue** (SI-) | Goods out of a store to a department, against an approved requisition or direct | out (issue) |
| **Material return** (MR-) | Goods back from a department; damaged goods to the Rejected store | in, at the issue's cost |
| **Transfer** (ST-) | Between stores: dispatched (in transit, not usable), then received | out → transit → in; shortage written off |
| **Stock adjustment** (SA-) | Written off (damaged, expired, lost, sample) or found, with a reason; someone else approves | ± on approval |
| **Stock count** (SC-) | Count a store; approving turns every difference into a posted adjustment | via the adjustment |

**First expiry, first out.** An issue or transfer line without a batch takes
the batch that expires first (split across batches when needed). Example:
120 kg asked; batch A (expires March) has 80 kg, batch B (June) 100 kg →
80 kg of A and 40 kg of B. Picking B while A has stock needs a reason. An
issue against a requisition is filled this way automatically.

**Never used:** expired, quarantined or blocked batches, and anything in a
quarantine, rejected or in-transit store - refused on issue, production use
and delivery (posting service).

**Scanning.** Issues, transfers and counts have a scan box: scanning a
package label adds its item, batch and quantity (a count adds it to the
counted quantity; each label once). A fully issued package's label is marked
used up (and active again if the issue is reversed).

**Counting** is blind: the software's quantities are frozen when the count
starts and hidden until it is submitted. "Not found = 0" sets every line not
counted to zero. On approval the adjustment is made and posted; moves in the
store after the count started are warned about.

---

## 2. Who may do what (Access page, per branch)

| Permission | Allows |
|---|---|
| `inventory.issue` Store requisitions, issues and returns | raise requisitions, issue, take returns |
| `inventory.issue_approve` Approve store requisitions (**new**) | approve / reject requisitions - the department head's yes from the Phase 0 approval chain, given as access (heading a department gives nothing by itself) |
| `inventory.transfer` Transfer stock | dispatch (sending branch), receive (receiving branch) |
| `inventory.adjust` Adjustments, counts, opening stock | draft adjustments, run counts |
| `inventory.adjust_approve` Approve adjustments | approve / reject / reverse adjustments, approve counts |

Nobody approves what they submitted, except the owner and company admin.

---

## 3. Tables (`inventory.0003_store_documents`)

`inventory_store_requisition` / `_line` (issued quantity per line),
`inventory_store_issue` / `_line` (batch, bin, scanned label, why not FEFO),
`inventory_material_return` / `_line` (condition, store, cost),
`inventory_stock_transfer` / `_line` (arrived, shortage reason),
`inventory_stock_adjustment` / `_line` (± change, new batch, cost),
`inventory_stock_count` / `_line` (system frozen, counted). The in-transit
store per branch (`TRN-<branch code>`) is made by the software when first
needed.

---

## 4. Code

`inventory/models_store.py` (the tables), `fefo.py` (which batch, how much),
`doc_common.py`, `issues.py` (requisition, issue, material return, scanning),
`transfers.py`, `adjustments.py` (adjustments and counts), `store_forms.py`,
`store_views.py` (one set of views for all six, described by `DOCS`),
templates `inventory/store/…`. Posting service: refuses use from unusable
stores; a reversal applies incoming counter-moves first (a transfer via
transit never dips transit below zero). Purchase returns and material
returns re-read the line they come from (never a stale copy).

## 5. Deploy

Pull, `migrate`, `collectstatic`, restart. Give "Approve store requisitions"
to the department heads (or whoever the client names) on the Access page.

## 6. Next

Phase 5 - production and BOM: BOM versions, production orders, material
consumption by scan (FEFO), finished-goods batches with labels, wastage, cost
roll-up (materials ÷ good quantity).
