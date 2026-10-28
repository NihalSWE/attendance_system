# Training sheets - one per role

**Start with the step-by-step guide of the new screens (2026-10-28):**
[PRODUCTS-AND-STOCK-TUTORIAL.pdf](../PRODUCTS-AND-STOCK-TUTORIAL.pdf) - Start here,
Materials, Products (recipe and cost), Stock in, Make (Plan, Start, Finish), Stock, Move
stock, Sell and Warranty, every box with what to write and what it does. Rebuild it with
`venv/Scripts/python docs/scripts/build_products_tutorial.py`. The sheets below cover the
older document pages (requisitions, purchase orders, GRN, QC), which stay as they are.

Each sheet is one page: what the person does every day, where it is in the
menu, and the mistakes the software stops. Train on the real masters, each
person doing their own flow once from start to finish.

| Role | Sheet | Permissions (Access page) |
|---|---|---|
| Store keeper | [01-store-keeper.md](01-store-keeper.md) | View stock, Store requisitions/issues/returns, Transfer stock, Print labels |
| Department head | [02-department-head.md](02-department-head.md) | Approve store requisitions |
| Purchase | [03-purchase.md](03-purchase.md) | Raise requisitions, Make purchase orders, Approve (a different person) |
| Receiving and QC | [04-receiving-qc.md](04-receiving-qc.md) | Receive goods (GRN), Quality check |
| Production | [05-production.md](05-production.md) | Set up BOMs, Plan and release, Use materials and record output |
| Sales | [06-sales.md](06-sales.md) | Enter sales orders, Confirm (a different person), Deliver, Invoice |
| Managers and accounts | [07-managers-accounts.md](07-managers-accounts.md) | See stock costs and values, Approve adjustments, Close a stock month |
