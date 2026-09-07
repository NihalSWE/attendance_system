# Warm Paper / Ink — Admin Panel Design Specification

A complete, self-contained specification. Hand this to anyone building the panel; nothing here
depends on knowing the conversation it came from.

**The idea in one line:** the interface is warm paper and black ink. Colour is not decoration —
it appears only where it carries meaning, so a single red or green in a list is impossible to miss.

Everything else is a warm off-white. Never pure `#ffffff` on the page background and never pure
`#000000` in text; both are what make an interface feel cold and cheap. The warmth comes from a
few points of yellow in the neutrals, which is the whole trick.

---

## 1. Tokens

Declare these once on `:root`. Nothing in the panel may hard-code a colour.

```css
:root {
  /* ---------- Chrome: the sidebar and topbar ---------- */
  --chrome:            #faf7f2;  /* sidebar background — warm paper           */
  --chrome-text:       #20201d;  /* sidebar item text                          */
  --chrome-dim:        #767066;  /* sidebar section labels (MAIN, EXPORT)     */
  --chrome-line:       #eae5db;  /* border between sidebar and body           */
  --topbar:            #fffefc;  /* topbar background — a shade lighter       */
  --topbar-text:       #1a1a18;
  --search:            #f6f3ee;  /* search field inside the topbar            */

  /* ---------- Surfaces ---------- */
  --page:              #fffefc;  /* page background behind the cards          */
  --surface:           #ffffff;  /* cards, tables, inputs                     */
  --surface-sunken:    #faf8f4;  /* wells, disabled fields, empty states      */
  --border:            #ebe6dd;  /* every hairline                            */
  --field:             #ded8cc;  /* input borders — darker than --border      */

  /* ---------- Text ---------- */
  --text:              #17171a;  /* body copy and headings                    */
  --muted:             #6f6b63;  /* labels, captions, secondary text          */
  --faint:             #a39d92;  /* placeholders and disabled text only       */

  /* ---------- Accent: ink ---------- */
  --accent:            #111113;  /* primary buttons, active nav, focus ring   */
  --accent-hover:      #2c2c30;
  --accent-soft:       #f0ede7;  /* quiet chips and highlights                */
  --accent-ink:        #2c2c30;  /* text on --accent-soft                     */
  --on-accent:         #ffffff;  /* text on --accent                          */
  --ring:              rgba(17, 17, 19, .14);

  /* ---------- Status: the only real colour ---------- */
  --success:           #2f6b34;  --success-soft: #e9f3e9;  --success-ink: #2f6b34;
  --warning:           #9a6a12;  --warning-soft: #f8f0dd;  --warning-ink: #7d5510;
  --danger:            #c0392b;  --danger-soft:  #fbeae7;  --danger-ink:  #a32a1e;
  --info:              #2f5d7c;  --info-soft:    #e8f0f6;  --info-ink:    #24485f;

  /* ---------- Tables ---------- */
  --thead:             #f5f1ea;  /* header band — warm, not white             */
  --thead-text:        #615c53;
  --zebra:             #fdfcfa;  /* even rows — barely there                  */
  --row-hover:         #f8f6f2;

  /* ---------- Elevation ---------- */
  --shadow-sm:         0 1px 2px rgba(23, 23, 26, .05);
  --shadow:            0 1px 3px rgba(23, 23, 26, .06);
  --shadow-lg:         0 6px 20px rgba(17, 17, 19, .10);
  --shadow-ink:        0 6px 20px rgba(17, 17, 19, .18);  /* the hero card    */

  /* ---------- Radius ---------- */
  --r-sm: 6px;  --r: 8px;  --r-lg: 11px;  --r-pill: 999px;

  /* ---------- Spacing (4px base) ---------- */
  --s1: 4px; --s2: 8px; --s3: 12px; --s4: 16px; --s5: 20px; --s6: 24px; --s8: 32px;
}
```

### Why the neutrals are warm

Put these side by side and the difference is obvious: `#faf7f2` against `#fafafa`. The first has
four points more red than blue, which is what reads as paper rather than as an unpainted wall. Keep
that relationship in any neutral you add — **red ≥ green ≥ blue, and never by more than about
eight points**, or it turns beige.

---

## 2. Contrast — verified, not asserted

Every pair below was calculated. Do not substitute a colour without recalculating.

| Pair | Ratio | Standard |
|---|---|---|
| Body text `#17171a` on `#ffffff` | **17.89:1** | AAA |
| Muted `#6f6b63` on `#ffffff` | **5.30:1** | AA |
| Sidebar text `#20201d` on `#faf7f2` | **15.28:1** | AAA |
| Sidebar label `#767066` on `#faf7f2` | **4.59:1** | AA |
| Active nav `#ffffff` on `#111113` | **18.86:1** | AAA |
| Table head `#615c53` on `#f5f1ea` | **5.90:1** | AA |
| Danger `#c0392b` on `#ffffff` | **5.44:1** | AA |
| Success `#2f6b34` on `#e9f3e9` | **5.64:1** | AA |

> The sidebar label was originally `#8d887e`, which measures 3.30:1 and fails for small text.
> It is `#767066` for that reason. This is exactly the kind of colour that gets chosen by eye and
> then fails in use — small, uppercase, low contrast.

---

## 3. Typography

**Sora**, from Google Fonts, weights 400 / 500 / 600 / 700.

```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Sora:wght@400;500;600;700&display=swap" rel="stylesheet">
```

```css
body {
  font-family: Sora, ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  font-size: 14px;
  line-height: 1.55;
  color: var(--text);
  background: var(--page);
  -webkit-font-smoothing: antialiased;
}
```

| Role | Size | Weight | Tracking | Notes |
|---|---|---|---|---|
| Page title | 22px | 700 | −0.03em | Sora is wide; negative tracking is what keeps it tight |
| Card heading | 13px | 700 | −0.01em | |
| Body | 14px | 400 | 0 | |
| Table cell | 13px | 400 | 0 | |
| Table header | 10px | 700 | +0.09em | Uppercase |
| Field label | 10px | 700 | +0.09em | Uppercase, `--muted` |
| Metric figure | 19–24px | 700 | −0.035em | |
| Badge | 10px | 700 | +0.02em | |

**Numbers are the point of an ERP.** Any element showing an amount gets:

```css
.amount, td.numeric, .metric strong {
  font-variant-numeric: tabular-nums;
  text-align: right;
}
```

Without this, columns of figures do not line up and the whole thing looks amateur.

---

## 4. Components

### Sidebar

```css
.sidebar {
  width: 232px;
  background: var(--chrome);
  border-right: 1px solid var(--chrome-line);
  padding: var(--s3) var(--s3);
}
.sidebar__brand   { font-weight: 700; font-size: 14px; letter-spacing: -.03em; gap: var(--s2); }
.sidebar__mark    { width: 26px; height: 26px; border-radius: var(--r-sm); background: var(--accent); }
.sidebar__label   { font-size: 10px; font-weight: 700; letter-spacing: .12em; text-transform: uppercase;
                    color: var(--chrome-dim); padding: var(--s4) var(--s2) var(--s1); }
.sidebar__link    { display: flex; align-items: center; gap: 10px; padding: 9px 10px;
                    border-radius: var(--r); color: var(--chrome-text); opacity: .78; }
.sidebar__link:hover     { opacity: 1; background: rgba(17,17,19,.045); }
.sidebar__link.is-active { opacity: 1; background: var(--accent); color: var(--on-accent); font-weight: 600; }
.sidebar__link svg       { width: 16px; height: 16px; stroke-width: 2; flex: none; }
```

The active item is a **solid ink block**. In a palette with no colour, that is the only way to make
it unmistakable, and it is the single strongest visual moment in the interface — do not soften it
to a tint.

Icons are inline SVG with `stroke="currentColor"`, so they take the item's colour and cost no
request. 16px, 2px stroke. Every item has one; an icon on some and not others looks broken.

### Topbar

```css
.topbar {
  position: sticky; top: 0; z-index: 40;
  height: 56px;
  background: var(--topbar);
  border-bottom: 1px solid var(--border);
  padding: 0 var(--s4);
  display: flex; align-items: center; gap: var(--s3);
}
```

Fixed, and one shade lighter than the sidebar so the two do not merge. No shadow — the hairline
is enough, and a shadow here makes it feel heavy.

### Card

```css
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  box-shadow: var(--shadow);
  overflow: hidden;
}
.card__head {
  padding: var(--s3) var(--s4);
  border-bottom: 1px solid var(--border);
  display: flex; align-items: center; gap: var(--s3);
}
.card__head h2 { font-size: 13px; font-weight: 700; }
```

Every distinct block of the page is a card. The complaint that fixing this palette answers is
*"it's just white everywhere"* — the cure is the **border plus the header band**, not the shadow.
Keep the shadow almost invisible; the border does the work.

### Hero metric

One card per screen may be inverted. Use it for the number that matters most, and never more than
one — two competing black blocks and neither reads as important.

```css
.metric--hero {
  background: var(--accent);
  color: var(--on-accent);
  border-color: transparent;
  box-shadow: var(--shadow-ink);
}
.metric--hero .metric__label { color: #8e8e96; }
```

### Table

```css
.table            { width: 100%; border-collapse: collapse; }
.table thead th   { background: var(--thead); color: var(--thead-text);
                    font-size: 10px; font-weight: 700; letter-spacing: .09em; text-transform: uppercase;
                    text-align: left; padding: 11px 14px; position: sticky; top: 56px; z-index: 10; }
.table tbody td   { padding: 11px 14px; border-top: 1px solid var(--border); font-size: 13px; }
.table tbody tr:nth-child(even) { background: var(--zebra); }
.table tbody tr:hover           { background: var(--row-hover); }
.table .numeric   { text-align: right; font-variant-numeric: tabular-nums; font-weight: 500; }
```

Row height lands near 38px. That is **standard density** — tighter than a roomy web app, not as
tight as an accounting grid. Readable first.

The header band is `--thead`, a warm grey. It must not be white or the header stops being a header,
and it must not be ink or it competes with the active nav item.

### Pagination

Required on every table, without exception. A table without it does not read as a data table.

```css
.pagination      { display: flex; align-items: center; gap: var(--s2);
                   padding: var(--s3) var(--s4); border-top: 1px solid var(--border);
                   color: var(--muted); font-size: 12px; }
.pagination__page       { border: 1px solid var(--border); border-radius: var(--r-sm);
                          padding: 5px 10px; background: var(--surface); }
.pagination__page.is-on { background: var(--accent); border-color: transparent;
                          color: var(--on-accent); font-weight: 700; }
```

Show a real count — `Showing 1–25 of 342` — not just arrows. Put a page-size selector beside it.

### Inputs

```css
.input {
  width: 100%;
  border: 1px solid var(--field);
  border-radius: var(--r);
  padding: 9px 11px;
  font: inherit; font-size: 13px;
  background: var(--surface);
  color: var(--text);
}
.input::placeholder    { color: var(--faint); }
.input:focus           { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--ring); }
.input:disabled        { background: var(--surface-sunken); color: var(--muted); cursor: not-allowed; }
.input.is-invalid      { border-color: var(--danger); box-shadow: 0 0 0 3px rgba(192,57,43,.12); }
```

Input borders use `--field`, which is darker than `--border`. A field that shares its border with a
hairline disappears into the card.

Number inputs carry no spinner arrows:

```css
input[type=number] { -moz-appearance: textfield; }
input[type=number]::-webkit-outer-spin-button,
input[type=number]::-webkit-inner-spin-button { -webkit-appearance: none; margin: 0; }
```

### Buttons

```css
.btn            { border: 0; border-radius: var(--r); padding: 9px 16px;
                  font: inherit; font-size: 13px; font-weight: 700; cursor: pointer; }
.btn--primary   { background: var(--accent); color: var(--on-accent); }
.btn--primary:hover { background: var(--accent-hover); }
.btn--ghost     { background: transparent; border: 1px solid var(--border); color: var(--text); }
.btn--ghost:hover   { background: var(--surface-sunken); }
.btn--danger    { background: var(--danger); color: #fff; }
.btn:disabled   { opacity: .5; cursor: not-allowed; }
```

**One primary button per view.** Ink is loud in a paper interface; two primaries and neither wins.

### Status badges

This is where the palette earns itself. All the colour lives here, so a status is legible at a glance
down a long column.

```css
.badge            { display: inline-block; border-radius: var(--r-sm); padding: 3px 9px;
                    font-size: 10px; font-weight: 700; letter-spacing: .02em; }
.badge--success   { background: var(--success-soft); color: var(--success-ink); }
.badge--warning   { background: var(--warning-soft); color: var(--warning-ink); }
.badge--danger    { background: var(--danger-soft);  color: var(--danger-ink); }
.badge--info      { background: var(--info-soft);    color: var(--info-ink); }
.badge--neutral   { background: var(--accent-soft);  color: var(--accent-ink); }
```

| Meaning | Badge |
|---|---|
| Posted, Confirmed, Paid, Received | `success` |
| Draft, Pending, Partial | `warning` |
| Cancelled, Reversed, Overdue | `danger` |
| Issued, In Transit, Submitted | `info` |
| Everything else | `neutral` |

---

## 5. Rules

**Do**

- Put every colour in a token. One file decides the palette.
- Keep colour for status. A screen with nothing unusual on it should be paper, ink and one black button.
- Give every card a border. It is what separates sections.
- Use `tabular-nums` and right alignment on every figure.
- Recalculate contrast whenever a colour changes.

**Do not**

- Use `#ffffff` for the page background or `#000000` for text. Both read cold.
- Add a second accent colour. There is one, and it is black.
- Tint the active nav item instead of filling it. In a paper interface a tint is invisible.
- Colour a table header white or ink. Warm grey, `--thead`.
- Put two hero metrics or two primary buttons on one screen.
- Use a shadow to separate sections. The border does that; the shadow only lifts.

---

## 6. Dark mode

Not specified here, and not required. If it is added later, this palette inverts cleanly:
`#141412` for chrome, `#1a1a18` for surfaces, `#f0ede7` for text, with the same warm bias
preserved — red ≥ green ≥ blue on every neutral. The status colours need lightening, not
inverting: `#5fbf6a`, `#d6a63c`, `#e2685a`, `#6ea3c9`.

---

## 7. Copy-paste starting point

```css
:root{--chrome:#faf7f2;--chrome-text:#20201d;--chrome-dim:#767066;--chrome-line:#eae5db;
--topbar:#fffefc;--topbar-text:#1a1a18;--search:#f6f3ee;--page:#fffefc;--surface:#fff;
--surface-sunken:#faf8f4;--border:#ebe6dd;--field:#ded8cc;--text:#17171a;--muted:#6f6b63;
--faint:#a39d92;--accent:#111113;--accent-hover:#2c2c30;--accent-soft:#f0ede7;
--accent-ink:#2c2c30;--on-accent:#fff;--ring:rgba(17,17,19,.14);--success:#2f6b34;
--success-soft:#e9f3e9;--success-ink:#2f6b34;--warning:#9a6a12;--warning-soft:#f8f0dd;
--warning-ink:#7d5510;--danger:#c0392b;--danger-soft:#fbeae7;--danger-ink:#a32a1e;
--info:#2f5d7c;--info-soft:#e8f0f6;--info-ink:#24485f;--thead:#f5f1ea;--thead-text:#615c53;
--zebra:#fdfcfa;--row-hover:#f8f6f2;--shadow-sm:0 1px 2px rgba(23,23,26,.05);
--shadow:0 1px 3px rgba(23,23,26,.06);--shadow-lg:0 6px 20px rgba(17,17,19,.10);
--shadow-ink:0 6px 20px rgba(17,17,19,.18);--r-sm:6px;--r:8px;--r-lg:11px;--r-pill:999px;
--s1:4px;--s2:8px;--s3:12px;--s4:16px;--s5:20px;--s6:24px;--s8:32px;}
```
