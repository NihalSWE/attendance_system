# Project UI and onboarding conventions

Updated 2026-09-07 from the user's correction of the platform onboarding slice. These rules supersede earlier examples of manually entered company identifiers, shared company administrator accounts, and role selection in master-account forms.

- Use four spaces for indentation in all authored code and templates. Keep rows, cells, form fields and control-flow blocks readable; do not compress entire tables onto one line. `.editorconfig` records this convention. Reference/vendor sources are not application code to reformat.
- Company list: `/platform/companies/`. `/platform/` redirects there until a real platform dashboard exists. Do not mislabel a list as a dashboard.
- A company has one current master administrator. Create a separate User with an active CompanyMembership, fixed `company_admin` role. No existing-user picker or role selector. Edit the existing account's name, email, password and membership status from the company page. Blank edit password retains the hash. Legacy owner roles remain equivalent; do not rewrite historical roles.
- Partial unique database constraints enforce one non-ended owner/company_admin per company and one such company per administrator. Suspended/invited administrators still occupy the slot. Ended memberships are historical; ending an account's membership permits an explicitly created replacement without deleting history. Ordinary employee/HR memberships are separate from this master-account rule.
- New company codes are strings generated using a PostgreSQL sequence starting at 10001, independently of the PK. Concurrent writes cannot claim the same number; failed/rolled-back writes may leave gaps. Existing company codes and slugs remain unchanged. Sequence initialization advances beyond existing numeric codes.
- Generate slugs with Django `slugify` from a suitable name/title: `felna-tech`, `felna-tech-1`, `felna-tech-2`. Respect field length when adding suffixes. Serialize allocation and retain database uniqueness. `common.identifiers.unique_slug` is the reusable helper; Company is the only implemented SlugField so far. Retain slugs after renaming. Future domain forms must use this convention, with the uniqueness scope required by their models.
- Code and slug are absent from create forms, displayed read-only on edit, and protected in the update service. Do not retrofit identifiers onto existing data. Explicit seed/import fixture identifiers remain supported outside normal platform creation.
- Company forms show name, legal name, email, phone and a single-line address input. Currency, country and timezone stay in the model/service with BDT, BD and Asia/Dhaka defaults; existing values are preserved on edit.
- Shared `StyledFormMixin` uses styled native selects for fixed enumerations and Select2 for database-backed ModelChoice/ModelMultipleChoice fields. Use multiselect only for genuine multiple relations. DataTables page-length is a fixed native select; features and company-switch options are database-backed.
- Number inputs have no spinner arrows. Integer/decimal field types validate server-side; browser handlers reject nonnumeric keystrokes/pastes while permitting a decimal point or sign only for applicable fields. Phone inputs display +88 in advance, normalize national or +88-prefixed input, and store digits beginning with 88. Server validation rejects letters; do not rely on JS alone.
- **Every list is a real data table (agreed before the project started; restated by Ajay 2026-09-14).** Real DataTables with **server-side** paging, search and sorting, a real result count and the page-length select — never a list that loads every row, never client-side-only paging. Every list has filters for what people look things up by: employee, branch, status, month/year. Build new lists on the shared helper (plan step A15) from the start; a list without them is not done.
- Center the page container within the content area (after the sidebar), with a narrower centered form container. Preserve local table scrolling, counts and pagination. Keep numeric headers and cells aligned in their own column, with visible separation from actions.
- Company detail uses a responsive details/administrator grid, compact feature summaries and a bounded recent-activity list. Verify desktop, tablet and mobile with screenshots and computed geometry. WARM_PAPER_INK_SPEC.md still owns all color/type tokens; design_reference owns form language.

## Vocabulary — use the project's words (2026-09-12)

This portal manages a company's existing workforce and its structure. Words
borrowed from other domains, or from our own internal model names, confuse the
people using it. Everything a user reads follows this table: page titles,
headings, buttons, labels, help text, placeholders, empty states, messages,
`aria-label`s and URL paths.

| Do not show | Show |
|---|---|
| Hire, Hire an employee | **Create employee** / **Edit employee** |
| Job title, title, position, role (meaning a designation) | **Designation** |
| Adopt, adopted, adoption | **Add department** / **Assign designations** / **Departments** |
| Catalogue, platform catalogue | Company screens: nothing. Root panel: **Departments**, **Designations** |

Internal names such as `CompanyDepartment`, `CompanyDesignation` or an
`adoption_*` module are fine in code, because nobody reads them on screen. Code
whose name carries a wrong user-facing word (a `hire_*` view, URL or template)
is renamed anyway, so it is not copied back into the UI.

When a word is not in the table, use the one the rest of the project already
uses; if there is none, record it in `PHASE_STATUS.md` for confirmation.

## Time and button conventions (2026-09-12)

- **No browser-default pickers anywhere** (Ajay, 2026-09-13). Every date
  field carries `data-datepicker` (or `data-daterange` for a from/to pair) so
  the project calendar replaces the browser's. A date-and-time field
  (`DateTimeField`) is never `type="datetime-local"`: split it into a date on
  the project calendar and a time as `HH:MM` text (below). On 2026-09-13 the
  only offenders were five fields in `devices/forms.py` (device installed at,
  enrollment and device-department effective from/to), fixed in Nihal's N0.
- **Time of day** is a text input, `HH:MM` in 24-hour form, parsed and
  validated server-side. Not `<input type="time">`: the browser draws its own
  clock control there, which the design does not allow. **Since 2026-09-14
  every `HH:MM` box carries `data-timepicker`:** `timepicker.js` adds a clock
  button and a panel of hour and minute buttons in the date picker's form;
  typing still works and is tidied on blur ("930" → 09:30).
  `common.forms.time_widget()` and the shift form set it, and a test fails if
  an `HH:MM` box anywhere lacks it.
- **Derived values are never typed.** A shift's length comes from its start
  and end times, and so does "ends the next day" (end earlier than start) —
  no checkbox asks for it.
- **Amounts on screen have thousands separators (Ajay, 2026-09-14).** Every
  amount shown uses `{% load money %}{{ value|money }}` → `30,000.00`.
  Display only; stored values and calculations are untouched. Text built in
  Python formats with `f"{amount:,.2f}"`.
- **A field that only applies to some choices of another field is hidden
  until it applies (Ajay, 2026-09-14).** Put the rule on the widget:
  `data-show-when="other_field:value1,value2"` (or `other_field:>0` for a
  number, e.g. "The break is paid" only with a break), and
  `data-label-when="other_field:value=Label|value=Label"` when its meaning
  changes (`dependent.js`, loaded on every page). The field still posts; the
  service ignores a value that does not apply. `[hidden]` always hides
  (base.css), even on `.field`, which sets its own display.
- Button variants that exist: `btn--primary`, `btn--ghost`, `btn--quiet`,
  `btn--danger`, plus `btn--sm`. **`btn--secondary` does not exist** in the
  stylesheets, although some device and department list templates use it —
  those render as a plain `.btn`. Use `btn--ghost` for a secondary action.
  (Still used in 8 places on 2026-09-13; fixing them is a loose end in
  `PHASE_STATUS.md`.)
- Form actions sit in a `.row`, primary button first, then the ghost Cancel.
- **A page's main actions go in `.page__actions` at the top**, beside the
  title — never in the footer of the last card below a table. Form submit
  buttons stay with their form, and a card's "see all" link stays with its
  card. `.page__actions` wraps, so several buttons never push the page wider
  than a phone (2026-09-13).
- **Checkboxes** (2026-09-13): `StyledFormMixin` gives a checkbox or radio the
  class `check__input`, not `input` — `input` carries `width: 100%`, which
  collapsed the box to nothing. The shared `.check` component draws the box
  (18px, `--field` border, `--accent` fill and a tick when checked, focus
  ring). In a template, wrap it as `<label class="check">{{ field }}
  <span>label</span></label>`. Do not reset a checkbox's class in a form;
  only the day picker's hidden inputs set their own (`day-picker__input`).
- **Several choices from a short fixed set** (for example weekdays) use the
  `.day-picker` buttons: real checkboxes, visually hidden but focusable, with
  the filled-ink selected state the calendar uses. Weekdays run Saturday first.
- **Overlays that move their list to `<body>`** (the custom select, Select2)
  sit outside their parent in the DOM. Anything that closes on an outside
  click, like the calendar, must treat those lists as inside.
- **Filter toolbars size their dropdowns to their content.** Form controls fill
  their field (`width: 100%`); inside `.toolbar` the custom select and Select2
  are set back to their own width, so a month/year/status filter row stays on
  one line instead of each filter taking a full row.
- **Outside-click checks use the click's recorded path** (`composedPath()`),
  not `contains(target)`: a control that redraws on click removes its own
  target before the check runs.
- **Date ranges**: two inputs sharing a `data-daterange` key become one
  control; render them in one field with one label. Add `data-presets="none"`
  to the first input where report-style quick ranges make no sense, e.g.
  recording leave.
- **Cache-busting (2026-09-13).** Every `{% static %}` link carries the file's
  version, so after a CSS or JavaScript change a normal reload picks up the new
  file — no Ctrl+F5. Development: `shell.css?v=<hash>`; deployed after
  `collectstatic`: `shell.<hash>.css`
  (`base_template/staticfiles.py`). Always link static files with
  `{% static %}`, never a hard-coded `/static/...` path, or the link is not
  versioned. One catch: an app that gets its **first** `static/` folder is
  only found after the dev server restarts.
- **A page with many choices across a year** (the holiday calendar) uses
  month grids of real, visually hidden checkboxes in the date picker's form:
  Monday first, filled-ink selected day, Shift-click for a run of days, arrow
  keys between days. The action button sits at the top of the selection
  panel, and on a tablet or phone the panel goes above the calendar.

## Database clarification and Django admin

Creating a company also creates Head Office and CompanyAttendanceSettings in the onboarding transaction. Attendance settings define shift mode, punch interpretation/windows, device scope and missing-punch handling. The current company field is an inherited FK with a unique constraint: at most one settings row per company, equivalent to one-to-one cardinality, not M2M. This does not create shifts or attendance records.

Root has no implicit tenant. `common.admin.TenantOwnedAdmin` explicitly scopes edit-form construction, related choices, validation, saving and rendering to the object's company, and restores context afterward. The company is read-only on an existing support-admin form. Related choices exclude other companies. Root-only cross-company listings use all_objects. This fixes the settings edit crash without weakening TenantManager. Support admin writes use Django's admin log; custom platform writes use AuditLog.

Feature is a database catalogue, not a choices field. CompanyFeature stores dated enable/disable grants for a company. A future module must check both company entitlement and the person's action permission, including in business services. Feature enablement never implies that an unimplemented module exists.

## Select controls — the rule (2026-09-08)

**The control follows the DATA, not the widget.**

| Data | Control | Why |
|---|---|---|
| Database-backed (`ModelChoiceField`, `ModelMultipleChoiceField`) | **Select2** | dynamic lists; may need search, paging, remote loading |
| Static choice lists (`ChoiceField`, status/type enums) | **our custom select** (`customselect.js`, `.cs__*`) | fixed and short; needs no search machinery |
| Multiple selection | **Select2 multiple** | removable tags |

`StyledFormMixin` applies `js-select2` to the first, `js-select` to the second.
`.select` stays on both as the no-JavaScript fallback.

A native `<select>` is not acceptable for a static list: its closed box can be
styled, but its **open dropdown is drawn by the operating system** and cannot be.
`customselect.js` replaces it with our own panel — form taken from
`design_reference/` (`.om-sel` control/menu/option/tick), colour from our tokens,
selected option a solid ink block, inline SVG instead of the reference's remote
iconify masks. The real `<select>` stays in the form and posts normally.

Hand-written selects in templates must carry `js-select` themselves; only
form-rendered fields get it automatically.

## Overlay controls must not be clipped

Pop-out panels (date picker, and any future menu or popover) are appended to
`<body>` with `position: fixed` and placed from the trigger's rectangle. `.card`
uses `overflow: hidden` for its rounded corners, so a panel positioned inside a
card gets cut off. Placement prefers below the trigger, flips above only when
below will not fit, clamps to the viewport edges, and re-anchors on scroll and
resize.


## Sidebar navigation (A14, 2026-09-15)

- Important pages and settings belong in the sidebar under their area. Maintain
  company destinations in `base_template/navigation.py`, including edit/detail
  aliases and appropriate access visibility. Keep existing page-level links and
  buttons; sidebar links are additional navigation, not replacements.
- Link to an existing section with a stable fragment when settings share a page.
  Do not duplicate a form just to give it a sidebar destination. Context-specific
  actions (such as a particular device's users) remain on that record's page.
- The current menu opens on navigation. Selected links use solid ink and
  `aria-current`; disclosure headings use native `details`/`summary` controls.
- At 1024 px and below, the shared Menu button opens a labelled, scrollable
  drawer. Keep Close, Escape, focus wrapping/return and backdrop dismissal.
  Root, company and self-service menus retain separate destinations.


## Server-side tables (A15, 2026-09-15)

Keep the existing table/row/badge/button styling. Ajay explicitly requested
pagination without a table redesign, and rejected Next/Previous-only paging.
Use numbered pages, first/last controls, page size, a direct page jump and the
real result count. Keep page-level filters and links. Reuse the Paper/Ink
rules in `vendor-controls.css`; do not import a stock DataTables theme.

Company and employee lists use `base_template.tables.paginate/render` and
`base_template/js/tables.js`. Search and ordering run against the authorised
queryset before slicing, never only against the browser’s current page. Action
and live/composite columns without a faithful database ordering are explicitly
not sortable. See `SERVER_SIDE_TABLES.md` for integration and the N9 boundary.
