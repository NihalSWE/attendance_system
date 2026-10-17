# Sales

| Task | Where | Steps |
|---|---|---|
| Order | Sales → Sales orders → New | Customer, store, date → lines (price from the customer's price list; raw materials may come from their own store) |
| Confirm | The order → **Confirm** | Someone else than who entered it; reserves the stock; over the credit limit it asks why |
| Deliver | The confirmed order → **Deliver** | One challan per store → check or **scan the cartons** → **Post** → print the challan |
| Invoice | The posted delivery → **Invoice** | Check → **Issue** → when paid, **Mark paid** (date, reference) |
| Returns | Sales → Sales returns → New | From the delivery → lines (good / damaged) → Post |
| AI orders | Sales → AI order inbox | Open each message: check the draft beside the original, match unknown lines, **Confirm order** |

Reserved stock cannot be issued or used by anyone else. A delivery cannot be
reversed once invoiced - cancel the invoice first.
