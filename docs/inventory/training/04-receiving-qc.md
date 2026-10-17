# Receiving (GRN) and quality check

| Task | Where | Steps |
|---|---|---|
| Receive against an order | Purchase → Goods receipts → New | Choose the order: its open lines fill in → quantities received and rejected, supplier batch, expiry (or scan the GS1 barcode) → **Post** |
| Receive without an order | Purchase → Goods receipts → New | Supplier and store → lines → Post |
| Labels | The posted receipt → **Print labels** | One per package; stick them on before putting away |
| Quality check | Purchase → Quality check | Each line waiting: **Passed** (goods leave quarantine) or **Failed** with what was wrong |
| Send back | Purchase → Purchase returns → New | From the receipt → lines → Post |

Items that need QC stay in the quarantine store - nobody can issue, use or
sell them - until passed. A failed batch is blocked; return it.
