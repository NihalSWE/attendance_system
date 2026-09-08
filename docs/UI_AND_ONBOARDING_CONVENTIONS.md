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
- Center the page container within the content area (after the sidebar), with a narrower centered form container. Preserve local table scrolling, counts and pagination. Keep numeric headers and cells aligned in their own column, with visible separation from actions.
- Company detail uses a responsive details/administrator grid, compact feature summaries and a bounded recent-activity list. Verify desktop, tablet and mobile with screenshots and computed geometry. WARM_PAPER_INK_SPEC.md still owns all color/type tokens; design_reference owns form language.

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
