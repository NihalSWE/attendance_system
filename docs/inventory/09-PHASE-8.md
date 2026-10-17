# Phase 8 — Mixed-store sales orders and the AI sales order assistant (2026-10-17)

Phase 8 of [00-PLAN.md](00-PLAN.md) (§8). **Done when:** an email order in
Bangla or English becomes a correct draft sales order with stock status; a
person confirms it; the managers receive the order summary; the daily and
weekly reports go out. Proven by `ai_assistant.tests.DoneWhenTests` (with a
fake Claude and a fake inbox - no real call is made in tests);
mixed-store orders by `sales.tests_flow.MixedStoreTests`.

---

## 1. Mixed-store sales orders (client, 2026-10-15)

Raw materials can be sold like finished goods (since Phase 6). Now one order
can hold **juice from the finished-goods store and sugar from the raw
material store**:

- Each order line has its own store ("From store"; empty = the order's
  store). Quarantine, rejected and transit stores are refused; a line from
  another branch's store needs "Enter sales orders" there too.
- Confirming reserves each line in its own store.
- **Deliver** drafts **one challan per store** (DC for FGW, DC for RMS). Each
  challan picks, scans, posts, reverses and takes returns from its own store,
  under "Deliver goods" in that store's branch.
- One invoice per challan, as before, under the order's branch.
- Existing orders and challans got their order's store (migrations
  `sales.0003`/`0004`), so nothing changes for them.

## 2. The AI order assistant

```
Order email ─► inbox (every 2 min) ─┐
Pasted WhatsApp text ───────────────┴► saved message ─► Claude reads it ─► our code matches
   customer · items · units · stores ─► draft sales order + problems ─► a person reviews
   ─► Confirm (stock reserved) ─► order summary to the managers
```

- **Off until consent.** The owner or company admin records the client's
  written consent to send order text to Anthropic (Phase 0, "required before
  Phase 8"), saves the Anthropic API key, and picks the login AI drafts are
  entered as (one with "Enter sales orders"). Only then can it be switched
  on. The key and the inbox password are stored encrypted and never shown
  again; the audit log records only that one is saved.
- **Intake:** the company's order inbox (IMAP; unread mail, each marked read
  once saved, repeats skipped by Message-ID) and **Paste an order** on the
  AI order inbox page (WhatsApp, phone notes). Every message is kept,
  whatever happens next.
- **Reading:** Claude (`claude-opus-5-5` by default; Sonnet/Haiku can be
  chosen) with a strict JSON answer (structured outputs). The catalogue -
  items with Bangla names, units and learned aliases, and customers - is the
  cached start of the prompt, so each order costs little. The message is
  treated as data: nothing in it can change what the system does.
- **Matching is our code, not the AI:** customer by the sender's email or
  phone, else the AI's suggestion or the name (flagged "check it"); each line
  by an alias, code, name or Bangla name, else the AI's suggestion (flagged);
  units through the item's own units (পিস, কেজি, বস্তা, ctn... understood);
  each line from its item's usual store. **Prices come from our price lists,
  never from the AI.**
- **Checks, listed as problems:** stock not free, over the credit limit, a
  repeat (same customer, day and lines), unknown units, low AI confidence,
  "does not look like an order".
- **Review** (AI order inbox → a message): the message as it arrived beside
  the draft. The reviewer picks the customer if unknown (then the draft is
  made), matches each line no item was found for - **the words are remembered
  as an alias** for next time - or leaves it out, edits the draft on the
  sales order page, then **Confirm order**: stock reserved, and the order
  summary with stock status goes to the managers who get new orders. Ignore
  cancels the draft. The drafting login can never confirm its own draft.
- **If the AI is off, has no key, or the day's spending limit is reached**
  (default US$ 5), messages wait and can be entered by hand; the inbox job
  tries waiting ones again. A failed or declined read can be retried.
- **Every AI call is logged** with tokens (cached ones too), the model that
  answered (a fallback model if the first declined) and its estimated cost;
  the settings page shows the latest calls and today's total.
- **Reports:** daily (6 pm) and weekly (Saturday 10 am) to the managers who
  get them: orders received (and how many by the AI), confirmed, deliveries,
  waiting deliveries, invoiced and unpaid, top items and customers,
  production, low stock and expiring batches - **figures from the ERP**, an
  Excel copy attached, and a short opening paragraph written by the AI (left
  out if the AI is off or fails).

**Cost (estimate):** a typical order is about 1,200 new input tokens, the
cached catalogue and 300 output tokens - roughly US$ 0.01 to 0.05 with
Claude Opus 5.5. Measure on the pilot (the call log shows it).

## 3. Who may do what

| Who | May |
|---|---|
| Owner, company admin | AI assistant settings: consent, key, model, limit, inbox, recipients |
| "Enter sales orders" | AI order inbox: paste, review, choose customer, match lines, ignore |
| "Confirm sales orders" | Confirm an AI draft (not the drafting login's own) |

No new permissions.

## 4. Tables

`ai_settings`, `ai_recipient`, `ai_item_alias`, `ai_inbound_message`,
`ai_call`, `ai_report_run` (`ai_assistant.0001`); `sales_order_line.warehouse`
and `sales_delivery.warehouse` (`sales.0003`, `0004`).

## 5. Code

`ai_assistant/`: `claude.py` (the one place that calls the Anthropic SDK:
key, cost, limit, refusal fallback), `extract.py` (prompt, catalogue,
schema), `matching.py`, `intake.py` (pipeline and review actions),
`inbox.py` (IMAP), `summaries.py` (order summary, reports), `setup.py`,
`views.py`, commands `ai_inbox` and `ai_reports`. Sales menu: AI order inbox,
AI assistant settings. New requirement: `anthropic==1.15.0` (and its
dependencies, pinned in `requirements.txt`).

## 6. Deploy

1. Pull, `pip install -r requirements.txt`, `migrate`, `collectstatic`, restart.
2. With the client: the signed consent; an Anthropic API key for the client's
   account (console.anthropic.com); the order inbox (e.g. orders@...) with
   an app password; the five managers and what each receives (§6 of the
   Phase 0 decisions).
3. Owner: Sales → AI assistant settings: consent and date, key, drafting
   login, inbox, recipients; switch on. Try **Paste an order** first.
4. Timers, like `attendance_settle` in [DEPLOYMENT.md](../DEPLOYMENT.md):

```
cat > /etc/systemd/system/ai_inbox.service <<'UNIT'
[Unit]
Description=AI orders: read the order inboxes and draft orders

[Service]
Type=oneshot
User=www-data
WorkingDirectory=/opt/attendance
ExecStart=/opt/attendance/venv/bin/python manage.py ai_inbox
UNIT

cat > /etc/systemd/system/ai_inbox.timer <<'UNIT'
[Unit]
Description=AI orders: every 2 minutes

[Timer]
OnBootSec=3min
OnUnitActiveSec=2min

[Install]
WantedBy=timers.target
UNIT

cat > /etc/systemd/system/ai_report_daily.service <<'UNIT'
[Unit]
Description=AI assistant: daily report

[Service]
Type=oneshot
User=www-data
WorkingDirectory=/opt/attendance
ExecStart=/opt/attendance/venv/bin/python manage.py ai_reports daily
UNIT

cat > /etc/systemd/system/ai_report_daily.timer <<'UNIT'
[Unit]
Description=AI assistant: daily report at 6 pm

[Timer]
OnCalendar=*-*-* 18:00:00 Asia/Dhaka
Persistent=true

[Install]
WantedBy=timers.target
UNIT

cat > /etc/systemd/system/ai_report_weekly.service <<'UNIT'
[Unit]
Description=AI assistant: weekly report

[Service]
Type=oneshot
User=www-data
WorkingDirectory=/opt/attendance
ExecStart=/opt/attendance/venv/bin/python manage.py ai_reports weekly
UNIT

cat > /etc/systemd/system/ai_report_weekly.timer <<'UNIT'
[Unit]
Description=AI assistant: weekly report, Saturday 10 am

[Timer]
OnCalendar=Sat *-*-* 10:00:00 Asia/Dhaka
Persistent=true

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload && systemctl enable --now ai_inbox.timer ai_report_daily.timer ai_report_weekly.timer
```

`ai_reports daily --dry-run` shows who would get it without sending.

## 7. Open questions and later

- WhatsApp Business Cloud API intake (needs Meta approval) and a customer
  order form: later; pasting covers WhatsApp until then.
- An acknowledgement email to the customer: not sent yet (plan: optional).
- Report times, the five managers, and the consent: still to be filled in
  the Phase 0 decisions pack.
- Future-dated stock postings (Phase 7 question) still open.

## 8. Next

Phase 9 - the API ([10-PHASE-9.md](10-PHASE-9.md)): items, stock, labels lookup, documents, sales orders
(with an order-intake endpoint for the client's other systems), reports.
