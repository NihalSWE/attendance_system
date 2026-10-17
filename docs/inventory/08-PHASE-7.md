# Phase 7 — Valuation, monitoring and reports (2026-10-15)

Phase 7 of [00-PLAN.md](00-PLAN.md). **Done when:** the valuation report at
any past date equals the ledger, and the nightly balance check reports zero
differences. Proven by `inventory.tests_reports.ValuationDoneWhenTests`;
every report, the dashboard, month-end close and the alert are covered by the
other classes in `inventory/tests_reports.py`.

---

## 1. In plain words

- **Inventory dashboard** (Inventory → Dashboard, also `/inventory/`): stock
  value (for those who see costs), items in stock, how many to reorder,
  batches expiring and expired, stock in quarantine; value by item type and
  by store; the low-stock and expiring lists; the work waiting (orders to
  approve, receipts not posted, QC, requisitions, issues, production
  running, sales to confirm, deliver and invoice - each only for whoever
  takes part); today's production output and sales.
- **Reports** (Inventory → Reports): 14 reports, each a server-side table you
  can search and sort, with **Download Excel** and **Download PDF** that hold
  every row with the same filters (and the table's search and sort).

| Group | Report | Shows | Opens with |
|---|---|---|---|
| Stock | Stock on hand | per item and store now: on hand, reserved, available (value) | View stock |
| Stock | Stock ledger (item card) | one item's moves over dates, running balance (value) | View stock |
| Stock | Movement summary | per item: opening, in, out, closing (values) | View stock |
| Stock | **Stock valuation** | quantity, average cost and value at the close of any day; by item or by item and store; totals by type | See stock costs and values |
| Stock | Batch and expiry | batches in stock, expired and expiring first | View stock |
| Stock | Stock aging | stock per store in brackets 0-30 … over a year, first in first out | View stock |
| Stock | Slow and non-moving | in stock, not issued/used/sold for N days (default 90, settings) | View stock |
| Stock | Reorder report | at or below reorder point / minimum: available, on order, suggested | View stock |
| Stock | Consumption by department | store issues less material returns per department and item | View stock or store issue roles |
| Purchase | Purchase register | PO lines approved onwards: amounts, VAT, received | purchase order/approve/receive |
| Purchase | GRN register | goods received line by line, batch, QC (value) | purchase roles or View stock |
| Production | Production yield and wastage | completed runs: planned, good, rejected, yield %, materials over plan (cost) | production roles or View stock |
| Sales | Sales register | issued and paid invoices, VAT, unpaid | sales order/approve/invoice |
| Sales | Customer and item sales | per customer and item: quantity, amount, VAT (cost and margin) | sales order/approve/invoice |

Values and costs show only to whoever sees stock costs ("See stock costs and
values") in every store the report covers; otherwise the value columns are
left out and the report says so (pick a store whose costs you see).

- **Month-end close** (Inventory → Month-end close, `inventory.period_close`):
  pick an ended month and close it. Closing is refused while a document dated
  in that month is still a draft or waiting (it would no longer post), or
  while the stock check finds differences. The page shows that checklist for
  the last open month and links its valuation at month end for accounts.
  After closing, any posting dated in the month is refused ("September 2026
  is closed for stock"); corrections go in the open month. A month can be
  opened again with a reason (recorded).
- **Morning stock alert:** Inventory settings now has "Send the daily stock
  alert to" (addresses) and "Slow-moving after (days)". Every morning each
  address gets one email: items at or below reorder point or minimum not
  covered by open purchase orders, batches expired and still in stock, and
  batches expiring within the warning days. Nothing to say, no email.

## 2. How the valuation works

The stock ledger is the truth: the valuation at a day is the sum of every
move dated on or before it - quantity and value, at the cost each move was
posted at (moving weighted average, D2). Nothing is re-priced, so a past
day's value never changes unless someone posts an entry dated back into that
(open) month - and then it changes by exactly that entry. Closing the month
stops that.

**Example (the test):** bearings 100 in on 3 Aug at 120, 100 on 10 Aug at 130
(average 125), 50 issued on 20 Aug at 125, 50 in on 5 Sep at 140 (128.75), 30
issued on 12 Sep at 128.75. Valued at 12 Sep: 170 pcs, **21,887.50** - what
the balances and averages said that evening. A receipt of 10 at 125 posted
later but dated 15 Aug moves 20 Aug to 160 pcs, 20,000.00, and leaves 3 and
10 Aug as they were.

Aging assumes first in, first out per store: what a store holds is what came
into it last; a transfer starts the clock again in the new store.

Entries dated after today (allowed by posting) are not in today's valuation;
the report says how many there are.

## 3. Code

- `inventory/valuation.py` - stock and value at a date, movement over dates,
  last used/received, FIFO aging, document numbers for the item card.
- `inventory/reports.py` - the 14 builders and the catalogue (`REPORTS`).
- `inventory/report_views.py` - dashboard, reports index, report page and
  download, month-end close and reopen.
- `inventory/periods.py` - `close_month`, `reopen_month`, `waiting`.
- `inventory/alerts.py`, `management/commands/stock_alerts.py`.
- Templates: `inventory/dashboard.html`, `inventory/reports/`,
  `inventory/periods.html`.
- Migration `inventory.0004_phase7_alerts` (two settings fields).
- Menu: Inventory → Dashboard, Reports, Month-end close (no new icon). No new
  permissions.

## 4. Deploy

1. Pull, `migrate`, `collectstatic`, restart.
2. Inventory settings: put the alert addresses in (the store manager, the
   GM...). Mail goes with the company's own mail account if set up
   (Company → Email settings), else the server's.
3. Two timers, like `attendance_settle` in [DEPLOYMENT.md](../DEPLOYMENT.md):

```
cat > /etc/systemd/system/inventory_alerts.service <<'UNIT'
[Unit]
Description=Inventory: morning reorder and expiry alert

[Service]
Type=oneshot
User=www-data
WorkingDirectory=/opt/attendance
ExecStart=/opt/attendance/venv/bin/python manage.py stock_alerts
UNIT

cat > /etc/systemd/system/inventory_alerts.timer <<'UNIT'
[Unit]
Description=Inventory: send the stock alert every morning

[Timer]
OnCalendar=*-*-* 08:00:00 Asia/Dhaka
Persistent=true

[Install]
WantedBy=timers.target
UNIT

cat > /etc/systemd/system/inventory_check.service <<'UNIT'
[Unit]
Description=Inventory: check the stock ledger against the balances

[Service]
Type=oneshot
User=www-data
WorkingDirectory=/opt/attendance
ExecStart=/opt/attendance/venv/bin/python manage.py check_stock
UNIT

cat > /etc/systemd/system/inventory_check.timer <<'UNIT'
[Unit]
Description=Inventory: nightly stock check

[Timer]
OnCalendar=*-*-* 02:30:00 Asia/Dhaka
Persistent=true

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload && systemctl enable --now inventory_alerts.timer inventory_check.timer
```

Check: `systemctl list-timers 'inventory_*'`, `journalctl -u inventory_check -n 20 --no-pager`.
`stock_alerts --dry-run` says what would go without sending. Both exit with
status 1 on trouble, so a failure shows in the journal.

## 5. Open questions

- Posting accepts dates after today. Should future dates be refused (most
  stock systems do)? The local demo data has opening stock dated 31 Oct,
  which is why its valuation "today" leaves it out.
- Alert recipients are addresses in settings for now; Phase 8's managers
  list (`AiRecipient`) can take over.

## 6. Next

Phase 8 - the AI sales order assistant ([09-PHASE-8.md](09-PHASE-8.md)): email orders in Bangla or English →
draft sales orders, manager summaries, daily and weekly reports.
