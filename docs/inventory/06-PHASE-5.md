# Phase 5 — Production and BOM (2026-10-15)

Phase 5 of [00-PLAN.md](00-PLAN.md). **Done when:** producing 1,000 bottles
consumes the BOM's materials, creates a finished-goods batch with expiry and
labels, and its unit cost equals materials ÷ good quantity. Proven by
`production.tests_flow.DoneWhenTests.test_a_thousand_bottles` and, through
every page, `production.tests_pages`.

---

## 1. In plain words

```
BOM (recipe, versioned) ─► Production order ─release─► Use materials (FEFO / scan) ─► Post output ─► Close
                                                     ◄─ Return unused ─┘          (batch, labels, cost)
```

- **BOM**: what one batch of a product needs - ingredients and packaging,
  each with a normal scrap %. Drafted, then **activated**; one active version
  per product. A change is a **new version** (copies the materials);
  activating it archives the old one. Orders keep the version they used.
- **Production order**: product, quantity, date, line, shift, the store the
  materials come from and the finished-goods store. **Planned**, then
  **released** by someone other than the planner (owner/admin excepted).
- **Use materials**: "Use planned materials" drafts what the recipe still
  needs (scrap included), first expiry first; package labels can be scanned
  in; posting takes them out of the store. Expired, quarantined and blocked
  batches, and quarantine/rejected/transit stores, are refused. Unused
  material goes back at the cost it was used at.
- **Post output** (once, at the end of the run): good quantity, rejects
  (wastage, not stocked), batch number (made from the product code, date and
  line when empty), manufacture date, expiry (from shelf life), packages (one
  label each). The batch goes into the finished-goods store - in quarantine
  if the product needs QC (then QC releases or blocks it) - at
  **unit cost = materials used (less returns) ÷ good quantity**.
- **Planned and used** on each order: planned per material, used, still to
  use, over/under, cost. The **production report** lists completed runs:
  planned, good, rejected, yield, materials, unit cost.

**Example (the test):** a 1,000-bottle batch uses 45 kg mango pulp at 210
(9,450 - 30 kg from the batch expiring first, 15 kg from the next), 22 kg
sugar at 125 (2,750), 1,015 bottles at 3.20 (3,248, with 1.5 % scrap) and
1,010 caps at 0.90 (909, 1 %) = **16,357**. 990 good, 10 rejected →
batch `FG0001-261001-L1`, expiry 270 days later, 33 carton labels of 30,
**16.5222 a bottle**.

Labour and overhead are not in the cost yet (Phase 0, decision for later).

---

## 2. Who may do what

| Permission | Allows |
|---|---|
| `production.bom` Set up BOMs (recipes) | BOMs and versions (company-wide) |
| `production.plan` Plan and release production orders | plan, release (not your own), cancel, close |
| `production.execute` Use materials and record output | material use, returns, output, its labels |
| `purchase.qc` (existing) | QC of a finished batch waiting in quarantine |

Orders, materials and output follow the finished-goods store's branch.

## 3. Tables (`production.0001_production`)

`production_bom` / `_line`, `production_order` (with the run's material
cost, good and rejected quantities and unit cost), `production_consumption`
/ `_line` (materials used or returned), `production_output`. Numbers:
`PRD-`, `PC-`, `OUT-2026-00001`.

## 4. Code

`production/`: `models.py`, `boms.py`, `orders.py` (orders, materials,
output, cost, QC), `forms.py`, `views.py`, `urls.py` (`/production/…`),
templates. Production menu (`base_template/navigation.py`, icon in
`navigation_icon.html` - Ajay to review); pages in
`access_control/page_access.py`; three permissions in
`access_control/branch_access.py`.

## 5. Deploy

Pull, `migrate`, `collectstatic`, restart. Enter each product's recipe
(the Phase 0 BOM template has the figures) and activate it.

## 6. Next

Phase 6 - sales: customers and price lists, sales orders with stock check
and reservation, credit-limit warning, delivery challan by scan (FEFO),
invoice (PDF), sales return.
