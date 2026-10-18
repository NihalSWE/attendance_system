# Production

| Task | Where | Steps |
|---|---|---|
| Recipe | Production → BOMs → New | Product, batch size → materials and packaging with scrap % → **Activate** (a change = a new version) |
| Plan | Production → Production orders → New | Product, quantity, date, material store, output store → **Release** (someone else) |
| Take materials | The released order → **Use materials** | The BOM's quantities fill in, first expiry first, or scan the bags → **Post** |
| Give back unused | The order → **Return materials** | Lines → Post |
| Record output | The order → **Record output** | Good and rejected quantity, batch, dates → **Print labels** for the new cartons |
| QC | The order (if the product needs QC) | Passed / Failed |
| Close | The completed order | **Close** |

The order page shows planned against used for every material; the unit cost
of the output is the materials used divided by the good quantity.
