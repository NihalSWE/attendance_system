# Products, stock and production: the complete plan

Version 3 · 27 October 2026 · for approval before any building starts.

Every decision here is made; each is marked **Decided** with the reason, so any
one can be changed before building. Example used throughout: a biscuit factory.

---

## 1. Who this is for

The same software serves two kinds of company:

| Company | What they do | Switches on |
|---|---|---|
| **Trading** | Buy products, store them, sell them | Store & Inventory |
| **Manufacturing** | Buy materials, make products, store them, sell them | Store & Inventory + Production |

**Decided:** Production becomes its own switch per company (Companies → Feature
access). A trading company never sees materials, recipes or production
anywhere: not in the menu, not in forms, not in "Next step" boxes.

Each product says **how you get it**:
- **Buy**: stock comes from purchases; its cost is what you paid (average).
- **Make**: stock comes from production; its cost is the production cost.
- **Both**: a manufacturer that sometimes buys the same product from outside;
  both costs flow into one average.

---

## 2. Plain words used everywhere

| Word | Meaning |
|---|---|
| **Store** | A warehouse or room. A branch can have many (raw store, packing store, main store, depot). |
| **Material** | Something you buy and use up in production: flour, sugar, wrappers, cartons. |
| **Product** | Something you sell: biscuits. |
| **Unit** | What stock is counted in, set once per item (packet, kg, piece). |
| **Other unit** | A bigger unit of the same item: 1 carton = 24 packets; 1 bag = 50 kg. |
| **Batch** | (1) One mix of the recipe: "1 batch = 10 cartons". (2) The stock that came out of one production or one delivery, with its own **batch number**, made/received date, expiry and cost. Both meanings are explained in §6. |
| **Production** | One making of a product: "make 5 batches (50 cartons) of biscuits today". Three steps: **Plan → Start → Finish**. |

No other jargon appears on screen.

---

## 3. One menu, in the order you work

```
Products & stock                 (Store & Inventory switch)
  1. Materials                   (only with Production on)
  2. Products
  3. Stock in                    first count · purchases · goods received
  4. Make                        (only with Production on)
  5. Stock                       what you have, by store and batch
  6. Move stock                  transfers between stores
  7. Sell                        deliveries and invoices
  8. Reports
  Settings                       stores · units · categories · cost rates · warranty
```

**Decided:**
- The order of the menu is the order of the work.
- A **checklist** on the first page shows what is done and what is next
  (✓ Materials added · ✓ Biscuit recipe saved · ✗ No flour in stock), each line a
  link to where it is done. The checklist follows the company: a trading company
  sees Products → Stock in → Sell.
- Every page ends with a **Next step** box (after adding a material: "Next: add
  the product that uses it"; after saving a recipe: "Next: put the materials in
  stock" or "Make it").

---

## 4. How every screen is built (UI rules)

### 4.1 Look
The approved style (first product-page mockup): number tiles across the top,
white cards with a clear title, one solid blue main button per page, other
buttons outlined, delete buttons red, links blue and underlined, on/off settings
as switches, status as coloured chips (green done, amber waiting, red problem),
every list a data table with SL, search, show N and pages.

### 4.2 Fields
- **Asked once.** A value set on an item is never asked again: the unit is set
  on the item and every quantity box after that shows it ("18 kg",
  "50 cartons"); the price comes from the product; the cost comes from stock.
- **Only valid choices.** Every picker shows only what fits the boxes before it:
  categories of the chosen type, stores of the chosen branch, batches of the
  chosen item in the chosen store, only the item's own units, only dates
  allowed (no future made date, nothing in a closed month).
- **Shown only when needed.** Expiry boxes appear only for items with expiry;
  warranty only when the company and the item use it; production parts only
  with Production on.
- **Grouped.** Main fields first; rarely used ones folded under "More details".

### 4.3 Saving (no page reload)
**Decided:** every form saves with `fetch` and stays on screen:
1. On press, the button shows "Saving…" and cannot be pressed twice.
2. The server checks everything (the browser checks are only a help).
3. **The message appears just above the Save button**, where the eye already is:
   - success: green, e.g. "Flour added." Then the list updates (or the next
     step opens) without reloading the page;
   - error: red, the reason in plain words, and each wrong field outlined red
     with its own reason under it. What was typed stays.
4. If the connection fails: "Not saved: no connection. Try again." and nothing
   is lost.
5. A modal form stays open on error and closes only on success.

### 4.4 UI first, then logic
**Decided:** the screens are designed and built first as working pages with
sample data, approved by you area by area, and only then connected to the
saving and calculation logic. Reason: you see and correct the flow before any
logic is changed.

---

## 5. Setting up (once)

- **Stores**: name, branch, kind (raw, packing, finished goods, quarantine,
  rejected). Any number per branch.
- **Units**: kg, g, litre, piece, packet, carton, bag … (a standard list is
  added in one click).
- **Categories**: per item type.
- **Inventory settings**: average cost for the whole company or per store;
  expiry warning days; label sizes.
- **Cost rates** (Production only): Line worker 75/hour, Supervisor 150/hour,
  Oven 300/hour … used by product cost sheets (§7.4). A change affects future
  productions only.
- **Warranty** (§10): one company switch; off by default.

---

## 6. Batches, explained

### 6.1 Recipe batch (one mix)
The recipe is written for **one batch** the way the factory works: "1 batch = 10
cartons: flour 18 kg, sugar 5 kg, oil 3 kg, wrappers 240, cartons 10". To make
50 cartons you make **5 batches**; the software multiplies the recipe by 5.

### 6.2 Batch number (the stock)
Stock of an item that keeps batches is held **batch by batch**. A batch is
stock that came in together:
- **materials**: each delivery (20 bags of flour = flour batch FL-231026, with
  received date, expiry, cost);
- **products**: each production (48 good cartons = biscuit batch
  BIS-271026-1, made date, expiry = made date + shelf life, cost per unit).

Stock "Biscuit: 300 cartons" is really, for example, 120 cartons of
BIS-201026-1 (cost 245.10) + 180 of BIS-271026-1 (cost 248.33).

**Decided:**
- One batch number per production by default; a product switch "New batch
  number for every batch made" gives one number per mix instead.
- When stock goes out (to production or to a customer), the batch that expires
  first goes first. Expired batches can never be used or sold.
- A batch can wait in quarantine until a quality check passes it.
- **Recall:** from any batch you can see what it was made from (flour batch
  FL-231026) and where it went (which customers, which dates).
- "Keeps batches" is a switch per item: on for food, medicine, chemicals; off
  for screws or cartons, which are then just a quantity per store.

---

## 7. Items

### 7.1 Materials (Production on)
**Materials page:** data table of code, name, unit, in stock (all stores),
status chip (OK / low / out). Filters: category, store, status. Main button
**Add material**.

**Add material** (one modal):

| Field | Rule |
|---|---|
| Name | Required. |
| Code | Suggested (RM-0012), can be changed, unique. |
| Category | Only material categories. |
| Unit | Required; locked once stock moves. |
| Other units | Optional: 1 bag = 50 kg. |
| Keeps batches | Switch. |
| Has expiry | Switch (turns Keeps batches on); then shelf life in days. |
| Quality check before use | Switch. |
| Usual store | Optional. |
| Stock to keep | Below it the material shows "low" and is in the morning email. |
| Warranty | Only if the company uses warranty (§10). |

**Material page:** tiles (in stock, value, average cost); stock by store and
batch; **used in** (which products); every movement; Next step box.

### 7.2 Products
**Products page:** data table of code, name, how you get it (Buy / Make /
Both), stock, sale price, cost per unit, margin, status chip. Main button **Add
product**.

**Add product:**

| Field | Rule |
|---|---|
| Name, code, category | As for materials. |
| How you get it | Buy / Make / Both (Make and Both only with Production on). |
| Unit | The smallest unit you sell (packet). Locked once stock moves. |
| Other units | 1 carton = 24 packets. |
| Sale price | **One price per unit**: Packet Tk 20 · Carton Tk 450 (the carton is not forced to 24 × 20). |
| VAT % | Optional. |
| Keeps batches · Has expiry · Shelf life · Quality check | Switches, as for materials. |
| Finished goods store | Where new stock goes by default. |
| Stock to keep | A quantity (5,000 packets) **or** days of sales (15 days, from the last 30 days' average). |
| Warranty | Only if the company uses warranty (§10). |

**Product page (the hub):**
1. Tiles: cost per packet · sale price · margin · in stock (packets and cartons).
2. **Make** button with the suggestion (§8.1) — Make products only.
3. **Recipe** (Make only): one batch makes N; materials per batch with cost,
   in stock, enough for N batches; Add material (existing or new right there).
4. **Cost sheet** (Make only, §7.4).
5. **Stock by store and batch.**
6. **Productions** (Make) or **Purchases** (Buy): history with cost per unit.
7. **Sold**: last 30 days and average per day.
8. Next step box.

### 7.3 Recipe rules
- One batch makes a quantity in the product's unit (> 0).
- Each line: a material and how much one batch uses **in that material's unit**
  (shown, not chosen), plus normal wastage % (0-100).
- Allowed: materials and semi-finished products (something you make and use in
  another product, e.g. cream made in-house for cream biscuits).
- Refused: the product itself; loops (A uses B uses A); inactive items; the
  same material twice.
- At least one material before it can be made.
- Changing a recipe changes future productions only; finished ones keep what
  they used.

### 7.4 Cost sheet (per batch)
| Line | How | Example (1 batch = 10 cartons) |
|---|---|---|
| Materials | Automatic: recipe × current average cost (+ wastage) | 1,904 |
| Labour | people × hours × rate | 4 × 1.2 h × 75 = 360 |
| Machines | hours × rate | Oven 0.5 h × 300 = 150 |
| Fixed per batch | an amount | Gas 120 |
| % of materials | a percentage | Rent share 5 % = 95 |

Shown: cost per batch, per carton, per packet; margin per packet.

---

## 8. Making a product (Production on)

### 8.1 How many to make
```
to make  = stock to keep − in stock − what planned productions will make
batches  = to make ÷ packets per batch, rounded up
suggest  = the smaller of that and what the materials allow
```
"Make 5 batches (50 cartons): keep 200 cartons, in stock 150. Materials allow 7."
With no stock to keep set, you choose yourself.

### 8.2 Plan
Production → Make → **New production** (or **Make** on the product):
- product; how many (batches, or cartons/packets — one converts to the other);
  date; finished goods into (filled from the product).

The screen fills in itself (recipe × 5):

| Material | Needed | In stock | Taken from |
|---|---|---|---|
| Flour | 90 kg | 120 kg | Raw store A 60 + Raw store B 30 |
| Sugar | 25 kg | 40 kg | Raw store A |
| Wrappers | 1,200 | 800 | **short 400** |
| Cartons | 50 | 300 | Packing store |

**Decided:**
- Materials are taken from the usual store first, then other stores of the same
  branch, oldest expiry first. Stock in another branch is never taken directly:
  it must be moved first (a real truck trip).
- Anything short is red with two buttons: **Make fewer** (sets the most you can
  make: 33 cartons) and **Buy the rest** (adds the shortfall to a purchase
  request).
- A plan can be saved while short; it cannot be **started** while short.
- Two plans needing the same flour each show "another planned production also
  needs it". Nothing is held back until Start.

### 8.3 Start
Press **Start** when the materials leave the stores. Each store's lines are
filled with the planned amounts; the store keeper changes a line only if they
gave more or less, or a different batch (a reason is asked if it is not the
oldest). On Start the stock goes down in each store, at its real cost. Start
refuses if anything is no longer there.

### 8.4 Finish
- **Good** and **damaged** quantity (in cartons or packets).
- **Into store**: one store, or split (Main 30 + Depot 18); must add up.
- Labour, machine and other costs pre-filled from the cost sheet; correct them
  if the day was different.
- **Leftover materials**: Return to their store, or mark wasted (reason).
- Checks: good below 90 % or above 105 % of plan needs a reason; a production
  where nothing good came out is recorded as **failed** (reason; its cost is a
  loss).
- Result: a new batch (or one per mix), batch number, made date, expiry, labels
  ready; quarantine first if the product needs a quality check.

### 8.5 Production cost
Costs happen per production (the mixer, the oven, the shift), so they are
**added up per production and divided by the good quantity**:

| Cost | Amount |
|---|---|
| Materials actually used | 9,520 |
| Labour | 1,800 |
| Machines and gas | 600 |
| **Total** | **11,920** |
| Good: 48 cartons = 1,152 packets | |
| **Per batch** (5) | 2,384 |
| **Per carton** | 248.33 |
| **Per packet** | **10.35** (sells at Tk 20) |

Damaged goods carry no cost of their own; their cost is spread over the good
ones, so waste shows as a higher cost per packet. **Decided:** stock is valued
at this full cost per unit. The product page shows expected cost (before) and
actual cost (after) for every production.

### 8.6 Statuses
Planned → Started → Finished. Cancel is possible while Planned; after Start,
return the materials first. A finished production can be **reversed** (reason)
while none of its batch has been sold, moved or used.

---

## 9. Companies that only buy and sell
- Products with "Buy": stock comes in by purchase or first count, into a store,
  with batch and expiry if the product keeps them.
- Cost per unit = average purchase cost. Margin = sale price − that cost.
- Selling, stock, batches, expiry, units and prices per unit, transfers,
  reports: exactly the same screens as for manufacturers, without any
  production parts.

---

## 10. Warranty (optional)
- One company switch (Settings → Warranty), **off by default**. A food company
  leaves it off and never sees warranty.
- When on, **any item** can have warranty (a switch on the item):
  - **on a material**: the supplier's warranty, from the received date; a
    faulty unit goes back to the supplier;
  - **on a product**: your warranty to the customer, from the delivery date.
- Months per item; optional **serial number per unit** (printed at receipt or
  production, scanned at delivery) for things tracked one by one.
- **Claims**: find by serial, or by invoice and batch; the screen says whether it
  is still under warranty. Outcome: replace, repair (cost recorded), refund, or
  rejected (reason). A returned unit goes to the rejected store, or back to
  stock if it passes a check.
- Report: claims per item, cost of claims, top reasons.

---

## 11. Every awkward situation

| # | Situation | What happens |
|---|---|---|
| 1 | Material in two stores of the branch | Taken from both (§8.2). |
| 2 | Material only in another branch | Shown as short; move it first. |
| 3 | Not enough in total | Can plan, cannot start; Make fewer / Buy the rest. |
| 4 | Two plans need the same stock | Both warned; the first to Start takes it; the second cannot start. |
| 5 | Stock used between plan and start | Start refuses and shows what is short now. |
| 6 | Batch expires before Start | Not taken; the next batch is. |
| 7 | Batch waiting for quality check | Never taken or sold until passed. |
| 8 | Store keeper gives a different batch | Allowed if valid; reason if not the oldest. |
| 9 | More material used than planned | Allowed up to stock; reason if more than 5 % over. |
| 10 | Less used | Return it, or wasted with a reason. |
| 11 | Spilled or spoiled | Wasted with a reason; its cost stays in the production. |
| 12 | Far fewer good than planned | Reason below 90 %; cost per unit shows red. |
| 13 | Nothing good came out | Failed production: cost to loss, reason required. |
| 14 | Output to several stores | Split; must add up. |
| 15 | Product needs quality check | Quarantine; pass = sellable; fail = rejected store, value to loss. |
| 16 | Recipe changed after planning | The plan offers "use the new recipe"; started ones keep theirs. |
| 17 | Material switched off while in a recipe | Red warning on the product; cannot plan until replaced. |
| 18 | Recipe in kg, bought in bags | Converted through other units; stock always in the unit. |
| 19 | Half a packet | Refused for units counted whole. |
| 20 | Date in a closed month | Refused. |
| 21 | Cancel after Start | Return the materials first. |
| 22 | Mistake after Finish | Reverse (reason) while the batch is untouched; else a stock adjustment. |
| 23 | Two people saving at once | The second is told what is left; nothing goes negative. |
| 24 | Stock would go negative | Never allowed. |
| 25 | Semi-finished loop | Refused when adding the line. |
| 26 | Semi-finished not in stock | Shortage offers **Make it first**. |
| 27 | Cost rate changed | Future productions only. |
| 28 | Average cost per store | Each store's own cost used. |
| 29 | Delete an item | Only if it never moved and is in no recipe. |
| 30 | User from another branch | Sees and acts only in their branches. |
| 31 | Who may start | Anyone with Start permission, including the person who planned it. To make it a two-person job, give Plan and Start to different people. |
| 32 | User without "See costs" | Quantities only; no money on any screen. |
| 33 | Same product bought and made | Both costs flow into one average; history shows which is which. |
| 34 | Trading company | Never sees production, materials or recipes. |
| 35 | Warranty off | No warranty field or screen anywhere. |
| 36 | Saving fails (connection) | Message above Save; nothing typed is lost. |
| 37 | Double press on Save | The button is locked while saving. |

---

## 12. Who may do what

| Permission | Can |
|---|---|
| View stock | See items, stock, productions (no money without "See costs"). |
| Set up items | Add and edit materials, products, recipes, cost sheets. |
| Cost rates | Change rates. |
| Plan production | Plan, Make fewer, Buy the rest. |
| Start production | Start (materials out), cancel. |
| Finish production | Finish, failed production, reverse. |
| Quality check | Pass or fail quarantined batches. |
| See costs | See every amount. |
| Warranty claims | Record and decide claims. |

---

## 13. Reports
Production cost (per product and period, expected vs actual) · Material use
(per material and store: used, returned, wasted) · Waste and failed productions
· Stock value (by store and batch) · Expiry · Shortages · Warranty claims.
Each with filters, a data table, Excel and PDF.

---

## 14. What exists and what changes

| Part | Today |
|---|---|
| Items, units, other units, batches, expiry, quarantine, oldest-first | Exists, reused |
| Stores, first count, purchases, transfers, selling, price per unit (price lists) | Exists, reused |
| Recipe, production (one store in, one out), cost of materials | Exists, changed |
| Production across stores, output split, full cost, Make fewer, Buy the rest, failed, reverse | New logic on existing parts |
| Production switch, Buy/Make/Both, semi-finished, cost rates and sheets, stock-to-keep suggestion | New |
| Warranty, serial numbers, claims | New |
| All screens, menu, checklist, Next step boxes, fetch saving | Rebuilt |

About 60 % of the needed logic exists and is reused; about 25 % is changed;
about 15 % is new. The screens are rebuilt on top.

---

## 15. Phases

Status of each piece: **Done** (built, reused as it is) · **Change** (built,
reworked) · **New** (to build).

### Phase 0 · Already done (no new work, reused by every phase)
| Piece | Status |
|---|---|
| Items, units, other units (1 carton = 24 packets) | Done |
| Batches: number, dates, expiry, quarantine, quality check, oldest-first | Done |
| Stores, bins, branches; stock by store and batch; average cost | Done |
| First count, purchases, goods received, transfers, returns | Done |
| Selling from stock: deliveries, invoices; price per unit (price lists) | Done |
| Labels and scanning; month-end lock; permissions per branch; audit trail | Done |
| Data tables with SL, search, pages (all inventory tables, 26-27 Oct) | Done |
| Pickers that offer only valid choices (unit, batch, bin, category) | Done |
| Delete for items never used | Done |
| One page per item type, short add forms | Done → merged into Materials / Products (Phase 2) |
| Recipe on the finished good's page, add a new material from it | Done → moved into the product hub (Phase 2) |
| Production: plan, materials out, leftovers back, output as a batch, QC | Done for **one store** → Change (Phases 6-7) |

### Phase 1 · Layout pieces (UI)
| | Sub-phase | Status |
|---|---|---|
| 1.1 | Tiles, cards, buttons (main / outlined / red), links, switches, chips | New |
| 1.2 | Data tables in the new look (behaviour already done) | Change |
| 1.3 | Fetch form: Saving…, message above Save, red fields, keep typed values | New |
| 1.4 | Next step box and setup checklist | New |
| 1.5 | New menu in work order, shown per company switches | New |

### Phase 2 · Screens with sample data (UI) — **your approval gate**
| | Sub-phase | Status |
|---|---|---|
| 2.1 | First page with checklist | New |
| 2.2 | Materials list, add, material page | Change (from Raw materials / Packaging pages) |
| 2.3 | Products list, add (Buy/Make/Both, price per unit), product hub | Change |
| 2.4 | Plan, Start, Finish screens | Change |
| 2.5 | Stock, Stock in, Move, Sell screens | Change |
| 2.6 | Warranty screens | New |

Nothing is connected until you approve these screens.

### Phase 3 · Company and product setup (logic)
| | Sub-phase | Status |
|---|---|---|
| 3.1 | Production as its own company switch | New |
| 3.2 | Buy / Make / Both on products | New |
| 3.3 | Sale price per unit on the product (from price lists) | Change |
| 3.4 | Stock to keep: quantity or days of sales | Change (field exists) |

### Phase 4 · Materials and products (logic)
| | Sub-phase | Status |
|---|---|---|
| 4.1 | Add / edit / delete with fetch saving | Change |
| 4.2 | Checklist and Next steps working from real data | New |
| 4.3 | Product hub: stock by store and batch, sold last 30 days | Change |

### Phase 5 · Recipe and cost (logic)
| | Sub-phase | Status |
|---|---|---|
| 5.1 | Recipe per batch, wastage, add new material inline | Done → wired to the new screens |
| 5.2 | Cost rates page | New |
| 5.3 | Cost sheet per product (labour, machines, fixed, %) | New |
| 5.4 | Expected cost per batch / carton / packet, margin | New |
| 5.5 | Semi-finished products in recipes, loop check | New |

### Phase 6 · Planning a production (logic)
| | Sub-phase | Status |
|---|---|---|
| 6.1 | How many to make (suggestion) | New |
| 6.2 | Plan across several stores, oldest first | Change (one store today) |
| 6.3 | Shortages, Make fewer, Buy the rest | New |

### Phase 7 · Start and Finish (logic)
| | Sub-phase | Status |
|---|---|---|
| 7.1 | Start: materials out of several stores | Change |
| 7.2 | Finish: good / damaged, split into several stores | Change |
| 7.3 | Leftovers, waste, failed production | Change (returns exist) |
| 7.4 | Actual full cost, expected vs actual, stock at full cost | New |
| 7.5 | Reverse a finished production | New |
| 7.6 | One batch number per production or per mix | Change |

### Phase 8 · Warranty (logic)
| | Sub-phase | Status |
|---|---|---|
| 8.1 | Company switch, warranty per item | New |
| 8.2 | Serial numbers: print, scan at delivery | New |
| 8.3 | Claims and their report | New |

### Phase 9 · Finishing
| | Sub-phase | Status |
|---|---|---|
| 9.1 | Reports (production cost, material use, waste, expiry, shortages) | Change (several exist) |
| 9.2 | Convert existing data (productions, prices) | New |
| 9.3 | Tutorial rebuilt from the new screens | Change |
| 9.4 | Rest of the app moved to the new layout, area by area | Change |

Every phase from 3 on: tests for every rule and situation it touches, a full
test run, a browser walk-through with you, then merge.
