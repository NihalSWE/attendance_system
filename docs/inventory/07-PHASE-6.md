# Phase 6 — Sales: order, reserve, deliver, invoice, return (2026-10-15)

Phase 6 of [00-PLAN.md](00-PLAN.md). **Done when:** an order reserves
stock, a delivery picks FEFO batches by scan and releases the reservation,
and the invoice matches the delivery. Proven by
`sales.tests_flow.DoneWhenTests` and, through every page,
`sales.tests_pages`.

---

## 1. In plain words

```
Customer ─► Sales order ─confirm (reserve)─► Delivery challan (scan, FEFO) ─► Invoice ─► Paid
                                                     └─► Sales return (good → store, damaged → Rejected)
```

- **Price lists** by customer group: a customer uses the list named on their
  record ("Distributor"); a line can start from a quantity (21 from 500
  bottles, else 22). With no list, the item's sale price.
- **Sales order**: customer, store, lines (price from the list, discount,
  VAT from the item). **Confirming** - by someone other than whoever entered
  it, unless owner/admin - **reserves** the stock: released, unexpired
  batches in that store, first expiry first. Reserved stock cannot be
  issued, used in production or transferred; only this order's deliveries
  take it. What could not be reserved shows as short ("Reserve again" after
  production). Cancel frees the reservation; Close frees what is left.
- **Credit limit**: a customer with a limit (0 = cash only, not checked)
  whose unpaid invoices + open orders + this order go over it is confirmed
  only with a reason - a warning, not a block.
- **Delivery challan**: "Deliver" drafts it from the reserved stock; cartons
  can be scanned instead. It never takes another order's reservation.
  Posting takes the goods out, releases this order's reservation and counts
  the delivered quantity (Partly delivered → Delivered). Challan PDF with
  batch and expiry and signature lines. Reversal (before invoicing) brings
  the goods back and reserves them again.
- **Invoice**: one per delivery - exactly what was delivered, at the order's
  prices, discount and VAT. Draft → Issued (due date from payment terms) →
  **Mark paid** (date and reference). A cancelled invoice can be replaced.
  Invoice PDF (plain tax invoice; the Mushak-6.3 layout is a layout change if
  the accountant asks, D12).
- **Sales return**: from a delivery line, at the delivery's cost; good goods
  back to the store, damaged ones to the Rejected store.

**Example (the test):** 150 bottles ordered at 22 (Distributor list).
Confirming reserves batch J1 (expires first) 100 and J2 50. The delivery
scans the J1 carton (100) and takes 50 more FEFO (J2); posting leaves J1 0,
J2 150, nothing reserved; the invoice is 100 × 22 + 50 × 22 = 3,300 + 15 %
VAT 495 = **3,795**, due in 30 days.

**Payments:** only "Mark paid" for now (it makes the credit check
meaningful). Customer ledger and payment collection stay "later" (Phase 0).

---

## 2. Who may do what

| Permission | Allows |
|---|---|
| `sales.order` Enter sales orders and price lists | orders, lines, cancel, close, reserve again, price lists |
| `sales.approve` Confirm sales orders | confirm (not your own), over the credit limit with a reason |
| `sales.deliver` Deliver goods to customers | deliveries, scanning, reversal, sales returns |
| `sales.invoice` Invoice customers and record payments | invoices, issue, mark paid, cancel |

Anyone with one of them (or "View stock") sees the sales pages; each action
checks its own permission in the order's store branch.

## 3. Tables (`sales.0002_sales_documents`)

`sales_price_list` / `_line`, `sales_order` / `_line` (reserved and
delivered quantities), `sales_reservation` (order line × stock row),
`sales_delivery` / `_line`, `sales_invoice` / `_line` (one live invoice per
delivery), `sales_return` / `_line`. Numbers `SO-`, `DC-`, `INV-`, `SRT-`.

## 4. Code

`sales/orders.py` (price lists, orders, reservation, credit),
`sales/deliveries.py` (deliveries, invoices, returns), `invoice_pdf.py`
(invoice and challan), `forms.py`, `views.py`, `urls.py` (`/sales/…`),
templates. `inventory.posting.change_reserved` is the one writer of reserved
quantities. `inventory.access.require` / `scope_warehouses` accept several
permissions (any one is enough). Sales menu (icon in `navigation_icon.html`
- Ajay to review).

## 5. Deploy

Pull, `migrate`, `collectstatic`, restart. Make the price lists named on the
customers (Distributor, Dealer, Retailer...).

## 6. Next

Phase 7 - valuation, monitoring and reports ([08-PHASE-7.md](08-PHASE-7.md)): dashboard, stock on hand and
valuation at any date, batch/expiry, slow-moving, purchase/sales registers,
reorder and expiry alerts by email, month-end close.
