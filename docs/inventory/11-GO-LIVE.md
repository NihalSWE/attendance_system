# Phase 10 — Go-live runbook (2026-10-17)

Phase 10 of [00-PLAN.md](00-PLAN.md): load masters; the **stock count day**
(count everything, print and stick labels, load opening stock); train each
role; one to two weeks of **parallel run** with the old method; then switch;
hypercare after. This page is the plan for those weeks. The software's part:
**Inventory → Go-live** (and `python manage.py golive_check --company ID`).

Training sheets, one per role: [training/](training/).

---

## The timeline

| When | What | Who | Done when |
|---|---|---|---|
| T-21 | Phase 0 decisions signed (valuation, label sizes, approvals, recipients) | Client + us | [01-PHASE-0-DECISIONS.md](01-PHASE-0-DECISIONS.md) filled |
| T-21 | Server: pull, `pip install -r requirements.txt`, migrate, collectstatic, restart; timers (stock alert, nightly check, AI) | Developer | `systemctl list-timers` shows them |
| T-14 | Enable Store & Inventory for the company; **Inventory settings** saved | Platform owner, owner | Go-live: Decisions ready |
| T-14 | Import masters: stores and bins, items and pack units, suppliers, customers (Excel templates); price lists named on customers | Store manager, accounts | Go-live: Masters ready |
| T-10 | **People**: each person's permissions on the Access page; two people for every approval | Owner | Go-live: People ready (no "Nobody has ...") |
| T-10 | Label printer installed, test labels at the chosen sizes; scanners paired | IT, store | A test label scans on the Scan page |
| T-7 | **Training**, role by role (sheets below), on the real masters | Us | Each person did their flow once |
| T-2 | Freeze: no new receipts or issues in the old system after the cut-off time | Store manager | Notice sent |
| **T-1** | **Count day** (below) | Every store | Every store's count sheet back |
| **T0** | Load the counts as opening stock; post (someone else); print and stick labels | Store manager, approver | Go-live: Stock ready; labels printed |
| T0 → T+14 | **Parallel run** (below) | Everyone | Two clean weekly comparisons |
| T+14 | **Switch**: the old method stops | Owner | Signed off |
| T+14 → T+44 | Hypercare: daily check-in the first week, then weekly; first month-end close together | Us + accounts | First month closed |

## The count day (T-1 → T0)

1. **Count sheets:** Inventory → Go-live → Count sheets → each store → Excel
   (or PDF for a clipboard). The sheet lists the items to count, in the
   opening stock import's own layout.
2. **Count** store by store: quantity in the unit shown; for batch items the
   batch number and expiry (and manufacture date if known); a bin code if
   bins are used. Write extra rows for anything not listed. Leave a row's
   quantity empty if the item is not there - it is skipped on import.
3. **Costs:** the unit cost column comes from the item's standard cost; accounts
   checks it (last purchase price is a good basis).
4. **Load:** Inventory → Opening stock → Import → the filled sheet, opening
   day = the go-live date. The preview shows every row: added, skipped (not
   counted) or refused with the reason - fix and upload again.
5. **Post:** someone other than who loaded it posts the opening stock.
6. **Labels:** on the posted opening stock, **Print all labels** (one per
   batch, one per item without batches); for cartons, each line's package
   labels. Stick them on as you go store by store.
7. **Check:** Inventory → Go-live should show Stock ready and the ledger in
   agreement; the Stock on hand report per store matches the count sheets.

## The parallel run (T0 → T+14)

- Every receipt, issue, transfer, production and delivery is entered in the
  software **and** the old way.
- **Daily:** the store manager looks at the dashboard and the Go-live page.
- **Weekly** (at least twice): export the old system's stock (item code,
  store code, quantity) and **Inventory → Go-live → Compare**. Every difference
  is explained (a missed entry, a wrong unit, a wrong batch) and corrected in
  the system that was wrong - in the software with an adjustment (approved by
  someone else) or a reversal.
- **Switch criteria:** two weekly comparisons in a row with no unexplained
  difference; every role has done its flow on real documents; the first
  morning alert and nightly check came in clean.

## Rollback

If the switch must wait, nothing is lost: keep running both. If the opening
stock itself was wrong, reverse it (Opening stock → Reverse, before anything
was issued from it) and load a corrected count.

## Hypercare

- First week: a daily 15-minute check-in with the store manager; problems go
  in one shared list with who and when.
- First month-end: close the month together (Inventory → Month-end close),
  download the valuation for accounts.
- After a month: weekly, then on request.
