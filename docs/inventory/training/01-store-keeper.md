# Store keeper

**Every day:** issue what departments ask for, take back what they return,
move stock between stores, keep labels on everything.

| Task | Where | Steps |
|---|---|---|
| Issue an approved requisition | Inventory → Store issues → New | Choose the requisition: store and lines fill in → check or **scan the cartons** → **Post** |
| Issue without a requisition | Inventory → Store issues → New | Store, department, date → add lines (batches are taken first expiry first) → Post |
| Take goods back | Inventory → Material returns → New | From the issue → lines (good / damaged) → Post |
| Move stock | Inventory → Transfers → New | From, to → lines or scan → **Dispatch**; the other store **Receives** |
| Find something | Inventory → Scan (or any scan box) | Scan the label: item, batch, expiry, quantity, where |
| Labels | Inventory → Labels; an item's page → Package labels | Print; a reprint asks why |

**The software stops you when:** a batch is expired, quarantined or blocked;
there is not enough in that store or bin; stock is reserved for a sales order;
you take a batch that is not the first to expire without saying why; the month
is closed.

**Mistakes:** a posted issue is never edited - **Reverse** it (with a reason)
and issue again.
