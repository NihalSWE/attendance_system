# Phase 3 — Purchase: requisition → order → GRN → QC → return (2026-10-15)

Phase 3 of [00-PLAN.md](00-PLAN.md). **Done when:** PR → PO → two partial
GRNs → QC → labels → stock and average cost correct; a purchase return
reduces both. Proven by `purchasing.tests_flow.DoneWhenTests` and, through
every page, `purchasing.tests_pages.PurchasePageTests`.

Built on the Phase 0 recommendations for the approval chains (D6) and QC
(D8); both are permissions on the Access page, so the client's signed-off
chain is set there without code changes.

---

## 1. In plain words

```
Requisition ──approve──► Purchase order ──approve, PDF/email──► Goods receipt(s) ──► QC ──► (Return)
 (department)              (supplier, prices)                    (store, batch, labels)
```

- **Requisition** - a department asks: item, quantity, by when, why. Sent for
  approval; approved or rejected (with a reason).
- **Purchase order** - made from approved requisition lines (their open
  quantities) or line by line: supplier, price per unit, discount %, VAT %.
  Sent for approval; approved; then **PDF** / **Email to supplier** (the
  company's mail account). Can be sent back to draft, cancelled while nothing
  is received, or closed when the rest will not come.
- **Goods receipt (GRN)** - what arrived at a store, against an approved order
  (filled with the open quantities; correct what really came) or direct.
  Per line: received, refused at the gate, unit cost, batch (or one is made:
  `YYMMDD-NN`), manufacture/expiry (expiry from shelf life), packages.
  Scanning a supplier's GS1 barcode fills the line. **Posting** puts the
  accepted quantity in stock at its cost (the average cost updates), adds it
  to the order (Partly received → Received) and makes **one label per
  package**, printed from the GRN.
- **Quality check** - items marked "QC before use" arrive in **Quarantine**:
  nobody can issue, use or sell them. Passed releases the batch; Failed
  blocks it until it goes back.
- **Purchase return** - from a GRN line, at that receipt's own cost, so the
  average comes back exactly. Quarantined and blocked batches can go back.

**Example** (the test): 100 kg sugar in stock at 120. Order 6 bags at 6,500
(130 a kg). GRN 1: 2 bags → average **125**, quarantine, 2 labels; QC passes.
GRN 2: 4 bags, 1 refused at the gate, landed at 7,000 a bag (140 a kg) →
average **131.4286** on 350 kg; QC fails → return 3 bags at 140 → back to
**125** on 200 kg. The order still has 50 kg open → closed.

---

## 2. Who may do what (Access page, per branch)

| Permission | Allows |
|---|---|
| `purchase.request` Raise purchase requisitions | make, edit, send, cancel requisitions |
| `purchase.approve` Approve requisitions, orders and returns | approve / reject requisitions, approve / send back orders, post / reverse returns |
| `purchase.order` Make purchase orders and send them | orders, PDF, email, cancel, close |
| `purchase.receive` Receive goods and enter returns | GRNs (post, reverse, print their labels), return drafts |
| `purchase.qc` Quality check received goods | pass / fail received batches |

Nobody approves what they submitted themselves, except the owner and company
admin. Requisitions and orders follow their branch; GRNs their store's
branch. Owner/admin hold everything; a branch manager everything in their
branches.

---

## 3. Tables (`purchasing.0002_purchase_documents`)

`purchasing_requisition` / `_line` (ordered quantity per line),
`purchasing_order` / `_line` (price, discount, VAT, received quantity),
`purchasing_goods_receipt` / `_line` (received, refused, cost, batch, dates,
packages, QC status), `purchasing_quality_check`, `purchasing_return` /
`_line` (the cost it goes out at). Numbers per type and year: `PR-`, `PO-`,
`GRN-`, `PRT-2026-00001`.

---

## 4. Rules that hold (tested)

- Only a posted GRN or return moves stock, through the posting service
  (no negative stock, closed months refused, ledger = balances).
- A GRN cannot take more than the order has open (checked again at posting,
  under a lock, so two GRNs at once cannot over-receive).
- A QC item's delivery always gets its own batch (a released batch is never
  topped up with unchecked goods).
- Cancelling an order gives its requisition lines back; a return cannot send
  back more than the line accepted.
- Reversing a GRN is refused once its goods went back to the supplier; it
  voids its labels and takes the quantity off the order.

---

## 5. Code

`purchasing/`: `models.py`, `common.py` (permission, units, own-approval
rule), `requisitions.py`, `orders.py`, `receipts.py` (GRN + QC),
`returns.py`, `order_pdf.py` (A4 PDF and email), `forms.py`, `views.py`,
`urls.py` (`/purchase/…`), templates, `static/purchasing/js/receipt_scan.js`.
Shared changes: five permissions in `access_control/branch_access.py`; the
Purchase menu (`base_template/navigation.py`, icon in `navigation_icon.html`
- Ajay to review); purchase pages in `access_control/page_access.py`; the
label service accepts the caller's permission (a GRN prints its labels under
"Receive goods"); the lookup returns a supplier's GS1 batch and expiry.

## 6. Deploy

Pull, `migrate`, `collectstatic`, restart. For emailing orders the company
needs its mail account (Organisation → Email settings).

## 7. Next

Phase 4 - store operations: store requisition → issue (scan, FEFO, blocks
expired/quarantine), material return, transfer (dispatch → in transit →
receive by scan), adjustment with approval, physical count by scan.
