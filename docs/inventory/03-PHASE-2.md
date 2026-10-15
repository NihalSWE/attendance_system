# Phase 2 — Barcodes, labels and scanning (2026-10-15)

Phase 2 of [00-PLAN.md](00-PLAN.md). **Done when:** a label printed from the
software scans back to the right item + batch + expiry + quantity on a real
scanner and a phone.

**Where it stands:** in software, done and proven - every label is rendered
at a label printer's 203 dpi and read back by a barcode decoder (zxing-cpp,
the same engine many phone apps use), and every reading resolves to the right
record (`inventory.tests_labels.LabelDecodeTests`). **Still to do on site:**
print on the client's own label printer and scan with their scanner and a
phone (camera scanning needs the live https:// address). Built on the
Phase 0 recommendations for D3/D4; sizes are a setting.

---

## 1. In plain words

- **Four kinds of label**, each resolving to one record:

  | Label | Size (setting) | Barcode | A scan fills in |
  |---|---|---|---|
  | Item (shelf card, loose item) | 50×25 mm | the product's own EAN-13 if valid, else Code 128 of the item code | item |
  | Batch card | 100×50 mm | QR + Code 128 of `B<company>-000001` | item, batch, expiry |
  | Package (each bag, drum, carton) | 100×50 mm | QR + Code 128 of `P<company>-000001` (+ GS1-128 if switched on) | item, batch, expiry, quantity, store/bin |
  | Bin | 50×25 mm | Code 128 of `L<company>-000001` | store and bin |

- **Where to print:** item page → *Print item label*, *Package labels*, and
  per batch *Batch card* / *Package labels*; Bins → *Label*; Stores → *Bin
  labels* (every bin of the store); a posted opening stock → *Labels* on each
  line (the stock count day: label each bag as it is counted).
- **Reprints need a reason** (torn, lost, faded) and are kept in the label
  register with who/when/why. A label stuck on the wrong bag is **voided**:
  scanning it is then refused with the reason.
- **Scanning:** Inventory → *Scan*. A USB/Bluetooth scanner types the code
  and presses Enter; on a phone, *Camera*. The same scan box goes on every
  document page in Phases 3–6 (GRN, issue, transfer, delivery) to add lines.

Example: 25 bags of sugar, batch 251001-01, 50 kg each → *Package labels*:
25 packages, 50, KG → 25 labels P7-000001…P7-000025. Scanning one shows
"RM-0001 · Sugar · batch 251001-01 · 50 KG, expiry 19 Sep 2027, RMS / R1-S1".

---

## 2. Decisions taken (change them in Inventory settings or here)

- **Codes** `P/B/L` + the company's id + a 6-digit number: letters, digits
  and a dash only (every keyboard-wedge scanner types them right), short
  enough for a 40 mm label, and a label from another company is recognised
  and refused instead of resolving to the wrong item. Item labels use the
  item's barcode or code (one per item).
- **QR content:** `code|item|batch|expiry YYYYMMDD|qty|unit` - the code alone
  is what the lookup needs; the rest lets a phone read the label offline.
- **GS1-128** (off by default): `(01)` GTIN-14 from the item's 13/14-digit
  barcode, `(17)` expiry, `(310n)` net kg or `(30)` count, `(10)` batch.
  The lookup also reads suppliers' GS1 barcodes in all three forms a scanner
  sends (`]C1` prefix, group separators, `(01)…` text).
- **Bars at least 0.25 mm** where the code allows (two dots at 203 dpi). A
  long item code on a 40 mm label can go thinner; prefer 50×25.
- **One page per label**, the page the size of the label: what Zebra / TSC /
  Xprinter drivers expect from a PDF ("actual size", no scaling).

---

## 3. Tables

`inventory_barcode_label` (code unique per company; kind item/batch/package/
location; item, batch, unit, quantity, store, bin; where it came from;
printed count, last printed; status active/used up/void + reason; one active
label per bin) and `inventory_label_print` (every print: who, when, copies,
reprint and why, size). `inventory_settings` gains the two label sizes and
the GS1 switch. Migration `inventory.0002_labels`.

---

## 4. Code

- `inventory/labels.py` - make (item, batch, bin, package), print (PDF +
  print log; reprint needs a reason; void never prints), void.
- `inventory/label_pdf.py` - the layouts (reportlab: Code 128 with FNC1 for
  GS1, QR, EAN-13).
- `inventory/lookup.py` - one lookup for every scan: our QR → GS1 → label
  register → item barcode / pack barcode / item code; warnings for
  quarantined or expired batches, refusals for void, unknown or another
  company's labels.
- `inventory/label_views.py`, templates `label_list.html`, `scan.html`,
  `package_labels.html`, `includes/scan_box.html`, `includes/print_dialog.html`;
  `static/inventory/js/scanbox.js` (keyboard scanners, queued scans, the
  camera via html5-qrcode 2.3.8 loaded only when asked).
- Menu: Inventory → *Scan*, *Labels*. Permissions: viewing and scanning need
  `inventory.view`; printing and voiding `inventory.labels` (bin and package
  labels in the store's branch).

**Tests:** `inventory/tests_labels.py` (20). The decode test runs where
`zxing-cpp` and `pypdfium2` are installed (`pip install zxing-cpp pypdfium2`,
developer PCs only - not in requirements.txt) and is skipped elsewhere.

---

## 5. Deploy

Pull, `migrate`, `collectstatic`, restart. Nothing new for a company without
the module.

**On site (with the client):**
1. Inventory settings: pick the label sizes loaded in their printer.
2. Print a test sheet: an item label, a bin label, a package label.
3. In the printer dialog choose "Actual size" (no fit-to-page).
4. Scan each with their scanner on the Scan page, and with a phone on the
   live site (Camera). All three must show the right item, batch, expiry
   and quantity.

## 6. Next

Phase 3 - purchase: requisition → order → GRN (partial receipts, QC,
batches, **labels printed at GRN** through `labels.package_labels`) →
receipt moves through the posting service. Needs the approval chains (D6)
and QC answers (D8).
