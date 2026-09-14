# Claude handoff — 2026-09-15

> **Update, later on 2026-09-15 (Claude, Ajay's session):** The read-only review
> (§0) was done and reported. Ajay then authorised the next step and Claude
> **finished A15**: the disputed pages work against the real data (short lists
> show one page), the Shifts page lists, the company department list and root
> Departments/Designations were converted, and the helper gained named tables
> for several lists on one page. 319 tests passed (base_template, scheduling,
> organization). Ajay's browser acceptance is still pending. **N9** (main
> attendance list and device lists) is still open; Ajay chooses its owner.
> Remaining order: N9 decision → A10 → A11 → A12 → A13; A16 in the office.
> Details: `PHASE_STATUS.md` → "A15 finished" and `SERVER_SIDE_TABLES.md`.
> §0's "do not implement" applied to the first turn only.
>
> **Ajay's rule (2026-09-15):** Ajay's session does not build Nihal's steps
> (attendance and device lists, N5/N6/N8/N9), even when a gap is pointed out on
> one of those pages. `/attendance/` stays for N9. **Amended by Ajay the same
> day:** attendance code may be changed when a leave or salary step needs it.
> **Keep leave simple** (Ajay): no partly paid, hourly, attachments, policy
> versions or ledgers.
>
> **A10a done (2026-09-15):** HR records/cancels leave (never their own, never in
> a finalised month); default leave types for new companies plus "Add default
> leave types" on Leave types; employee code in the Record leave picker;
> employees withdraw pending requests from My leave. Plan table: `PHASE_STATUS.md`
> → "A10 split into sub-steps".
>
> **A10b done (2026-09-15):** half-day leave (one date, 0.5 day). Came in → full
> day, no late mark, unpaid half deducted; no scans → only the leave half.
> Attendance and the Now badge were changed for this.
>
> **A10c done (2026-09-15):** optional Days per year on each leave type; approved
> leave in the calendar year counts (half day 0.5); over-allowance refused;
> left shown on My leave and the approval page. Migration `leaves.0002` — Ajay
> runs migrate. **A10 (simple leave) is complete.**
>
> **A11 plan (agreed, simple):** 1 finalise/undo finalise → 2 bonus and deduction
> lines → 3 joining/leaving proration by calendar days → 4 mid-month salary change
> → 5 print-layout payslip PDF. **Part 1 done (2026-09-15):** Salary by month →
> Finalise (owner/admin; refused if overtime changed after generating) and Undo
> finalise with a reason. **Part 2 done:** payslip → Bonus and deductions (draft
> only, owner/admin), kept across regeneration; migration `payroll.0006` — Ajay
> runs migrate. Next: part 3.

## 0. First instruction: review and report, do not implement

Ajay is moving this project from Codex to a new Claude chat. Claude will work
in this **same existing folder**:

`E:\Work and Projects\NSU\freelancing_projects\attendance_system`

**Do not implement anything on your first turn.** Read this handoff, inspect
the current code and relevant documents without changing them, then tell Ajay:

1. What has already been implemented, with the page/navigation path.
2. What has actually been verified, versus merely claimed in older documents.
3. What is unfinished or disputed, especially pagination across the pages.
4. What remains for Ajay's session and what was assigned to Nihal.
5. The proposed next step, so Ajay can verify the status and choose the work.

Then wait for Ajay's instruction. Do not immediately start A10, finish N9,
refactor the table helper, seed data, install packages, migrate, run a broad
test suite, change branches, pull/merge/push, or contact Nihal. The first task
is a **read-only status handoff**, not another implementation run.

This file and Ajay's latest messages supersede older "Next action" and "done"
claims. In particular, the previous A15 completion report was disputed.

## 1. Why the handoff matters

Ajay repeatedly warned that implementation runs consumed too much of his
weekly usage allowance: he reported approximately 12%, then 10%, then 14%.
Codex promised tighter work but still used repeated inspections, failed
editing commands, debugging passes and repeated checks. Ajay rejected both
the cost and the completion report. These are user-reported usage changes;
there is no reliable per-action usage breakdown in this handoff.

His latest feedback: he does not see changes across the expected pages,
mentions seeing changes only on Overtime and Salary by month, and specifically
calls out attendance and remaining pagination work. His wording also mentions
Overtime among unfinished pages. **Do not resolve that ambiguity by assuming
he accepted Overtime or that a cache/restart explains his observation.**

The honest distinction is:

- A15 code was committed and pushed for thirteen named lists.
- A focused test run passed, and a limited browser check passed.
- Most of those pages were not individually browser-verified in that run.
- The main attendance list and device lists were explicitly left for N9.
- Ajay does not accept the overall result as complete. Report the gaps and
  ask him to verify your inventory before starting more implementation.

## 2. Repository and exact state at transfer

- Remote: `https://github.com/ajay016/attendance_system.git` (`origin`).
- Working branch: `main`.
- Last application commit: **`3440212` — Build A15 server-side tables with numbered pagination**.
- That commit was successfully pushed to `origin/main`. Local `main` was
  fast-forwarded to it. At the start of this handoff, `git status --short
  --branch` showed `## main...origin/main` with no changes.
- This handoff and status-document corrections are **local, uncommitted
  documentation changes**. They have not been pushed. No application code was
  changed during preparation of this handoff. Preserve these documents.
- The A15 implementation branch `feature/a15-server-side-tables` exists
  locally. Its implementation was pushed as `HEAD:main`; do not assume every
  local feature branch was separately pushed to origin.
- The local app was reachable at `http://127.0.0.1:8000/` during the final
  implementation check. The temporary browser tab was deliberately closed
  after verification; it did not crash or interrupt the implementation.
  Check current server state if needed later, rather than assume it is running.
- No pending Nihal merge is known. This is not a fresh audit of his remote PC
  or all remote branches. Nihal is paused per Ajay.

Recent history, newest first:

| Commit | Delivered code |
|---|---|
| `3440212` | A15 helper/list conversions; completion disputed, see §7 |
| `89baf22` | A8 employee leave requests and branch approvals |
| `f20e26f` | A14 sidebar menus and settings navigation |
| `3e2edeb` | Fix paid-break credit outside the scheduled shift |
| `b7e9f83` | A5c weekly-off periods and paid-day settings |
| `a892f12` | Original office-to-home handoff |
| `c4e1ec0` | Device push-protocol choice for a 3.x device announcing 2.x |

Do not clone again, reset the folder, discard changes, or recreate the apps.

## 3. Read these documents in this order

1. This file, especially the disputed A15 inventory and first-turn restriction.
2. `docs/HANDOFF_2026-09-14.md` for the full office context, architecture,
   settled business decisions and device protocol findings. It contains
   historical next-step instructions; do not execute them blindly.
3. `docs/PHASE_STATUS.md`: "Plan after the fast-track — 2026-09-13", the
   corrected "Next action" and the home progress entries near the end.
4. `docs/UI_AND_ONBOARDING_CONVENTIONS.md` and root `WARM_PAPER_INK_SPEC.md`.
5. `docs/SERVER_SIDE_TABLES.md` and the actual A15 files in §7. This is an
   implementation contract, not proof Ajay accepted all the pages.
6. For the relevant later step: `docs/LEAVE_AND_SALARY_MANAGEMENT.md`,
   `IMPLEMENTATION_ROADMAP.md`, `MODEL_FIELD_DICTIONARY.md`,
   `DEVICE_ATTENDANCE_POLICY.md`, `DEVICE_INTEGRATION_HANDOFF.md` as referenced
   by the existing project documentation. Locate these by filename if needed;
   do not read every large document repeatedly.

## 4. People, ownership and future Git workflow

**Ajay:** project lead, product decisions and browser acceptance. He relays
messages between the two development chats.

**Claude in this folder:** becomes "Ajay's session" after the initial review.
Owns A steps: payroll, leave, shifts/calendars, employee logins/panel, shared
layout/navigation. Reviews and integrates Nihal's branches. **This session
is allowed to push `main` when Ajay authorizes/resumes implementation.**

**Nihal:** separate Claude chat and separate computer. Owns device work and
the scan-to-attendance chain. **He pushes feature branches only, never main.**
He is currently paused. Do not treat his planned task list as evidence he is
working. Do not send anything to him. Ajay requested relay guidance in this
handoff; §11 supplies it for later, not an instruction to dispatch work now.

Normal future implementation workflow, after Ajay resumes work:

- Start one bounded step on a feature branch from current main; preserve
  unrelated local changes. Avoid working on a stale checkout.
- Test the change appropriately, commit it, and push the reviewed step to
  `origin/main` (the established workflow uses `git push origin HEAD:main`).
- Finish with this same local folder on the updated `main`; Ajay should not
  have to discover the code exists only in an isolated branch/worktree.
- For Nihal: fetch his reported branch, inspect changes, integrate on a
  `merge/...` branch from main using `--no-ff`, preserve both sides of document
  conflicts, check cross-app behavior, then push main from Ajay's session.
- Never force-push main, blindly overwrite conflicts, or invent a completed
  merge. Report the real commit/branch and push result.
- The old office server folder was `D:\attendance_device`, with a separate
  build worktree `D:\attendance_device_a5`. Those are historical office paths,
  not this machine's working folder.

## 5. Local setup and data: already established, preserve it

- Windows / PowerShell; Python 3.13, virtual environment `venv`.
- Use ` .\venv\Scripts\python.exe `, not an arbitrary system Python.
- Existing docs record Django 6.1.1 and PostgreSQL 18. Dependencies are already
  installed from `requirements.txt`; do not reinstall just to take over.
- Ajay created the database, populated `.env`, ran development migrations and
  created the accounts himself. Home setup is done.
- Ajay reported the non-secret DB settings as database `attendance_system`,
  user `postgres`, host `localhost`, port `5432`. These were not re-read from
  `.env` for this handoff. Do not change them based on the original template's
  older suggested database/role names.
- **Never read, print, copy, request in chat, edit or otherwise handle Ajay's
  database password. Do not open/dump `.env`.** Normal Django commands may use
  its configured connection without displaying credentials.
- Never commit `.env`, reset/recreate the development DB, run a fresh demo
  seed over the existing data, or delete Ajay's company/settings/employees.
- Root account email supplied by Ajay: `admin@gmail.com`; company account:
  `ajay@gmail.com`. Existing application passwords were supplied in the old
  chat but are deliberately not reproduced in a repository document. Let
  Ajay handle sign-in if necessary; do not reset credentials to gain access.
- The company shown in the final local browser check was **Google**, with
  **Head Office** as the working/default branch. Do not confuse this with
  the office companies Felan Tech and "Ajay".
- Ajay configured shifts, weekly offs/holidays, payroll/salary settings,
  attendance/overtime rules, departments, designations and a dummy device.

### September 2026 synthetic payroll exercise

Ajay explicitly requested missing device users/mappings, punches, attendance,
leave and other inputs needed to exercise existing salary calculations.
He initially had four employees and approved adding two demo employees.

- Original employees: Ajay Ghosh, Fazle Rabbi, Dia Mirza, Nihal; monthly-paid.
- Added: **Demo Daily Salary Test**, BDT 1,500/day; **Demo Hourly Salary Test**,
  BDT 200/hour. Keep the original employees and settings intact.
- Recorded seed result: six device users/mappings, **397 punches** including
  two intentional duplicate scans, **180 attendance days**, **17 approved
  leave days**, a cancelled leave example, **seven overtime decisions**, and
  **one waived penalty**.
- Six draft salaries were checked against independent scan-based arithmetic
  and earning/deduction lines. Recorded total net: **BDT 177,047**. The daily
  demo intentionally exercises a negative net under additive penalties with
  no deduction cap. This is a historical verification result; do not assume
  the current DB remains unchanged after subsequent user edits.
- These are synthetic device events, not proof physical hardware works.
- Local, ignored `.qa/seed_salary_scenarios.py` and `.qa/inspect_seed.py` exist.
  They are not shipped management commands. **Do not rerun them on takeover.**

### Paid-break defect: already fixed

`attendance/pairing.py` previously credited outside minutes as paid breaks
even outside the scheduled shift. Dia's September 28 example incorrectly
credited 75 additional regular paid minutes. The fix is commit `3e2edeb`.
Recorded regular minutes changed from 615 to 540, with 120 overtime minutes.
Her monthly pay was unaffected; the equivalent BDT 200/hour example changed
from BDT 2,850 to BDT 2,600. Do not present this as an unfixed new discovery.

## 6. What was implemented before the disputed A15 step

These are the recorded implementations on main. Validate relevant behavior
when it matters; do not restart them because an old roadmap calls them pending.

| Step | Existing feature / location |
|---|---|
| A1 | Development static-file cache busting |
| A2 | Holiday year calendar, under Shifts |
| A3 | Effective-dated salary settings and calculation rules, Salary → Salary settings |
| A4 | Attendance penalty rules and deductions/waivers, Salary → Penalty rules |
| A5 | Shift break/grace/overtime controls and employee shift override; rotating shifts were deferred |
| A5c | Date-aware weekly-off overlap checks, Change start date with recalculation/posted-month protection; remove paid-day checkboxes; clarify monthly day value |
| A6 | Employee/manager logins from Edit employee → Login, enable/disable/reset controls and self-service gate |
| A6b | Set the first salary for an employee created from device users |
| A7 | **Employee panel already exists**: `/me/`, My account, My attendance calendar, My leave, My payslips |
| A8 | Full-day leave requests, pending inbox, manager approval/rejection and admin fallback; branch attendance |
| A9 | Automatic overtime approval from completed scans, manual decisions for exceptions, overtime salary lines/settings |
| A14 | Sidebar menus/submenus with important settings links; existing links on individual pages retained |
| A16, partial | SenseFace 3A catalogue entry and connection/protocol fixes; physical verification remains incomplete |

Nihal's merged work: N0 UI controls, N1 pairing, N1b live day close, N2
attendance calendar, N3 Now badge, N4 device scope and Re-check punches, N7
payslip redesign, and the no-shift/day-off attendance fix that prevented a
salary-generation crash.

### A8 details to preserve

- `leaves/workflow.py`, `leaves/request_views.py`, forms/templates and
  `leaves/tests_requests.py` implement the employee request workflow.
- Employee → My leave → Request leave produces a pending request with no
  payable `LeaveDay` records yet. Approval produces the existing leave-day
  inputs and recalculates attendance atomically, with permission, calendar,
  conflict and finalised-month checks and an audit trail.
- Manager → My branches → Approval inbox / Branch attendance.
- Company administrator → Leave → Approval inbox handles fallback cases.
- **Ajay confirmed:** when there is no active branch manager, or the requester
  is the manager, company administrators handle the request. No self-approval.
- Approvers can approve paid/unpaid or reject with a note visible to the
  employee. Regenerate a draft salary to incorporate newly approved leave.
- Half-day/hourly leave, balances, attachments, amendment/withdrawal are A10.
- No migrations or new environment variables were introduced by A8.

### A14 details to preserve

`base_template/navigation.py` owns company menu destinations, role visibility
and active-page aliases. Seven areas: Employees, Attendance, Leave, Salary,
Shifts, Organisation, Devices. A8 added Approval inbox. Important settings
have direct sidebar links, sometimes to a fragment on the existing page.
The shared responsive drawer works across root/company/employee surfaces.
Do not remove the original page-level links while adding sidebar entries.

## 7. A15: committed implementation versus outstanding acceptance

Commit `3440212` changed **33 files**, with 1,012 insertions and 291 deletions
including tests and documentation. That size is not a justification for the
usage cost, nor evidence all requested pages are complete.

### Exact scope of the committed conversion

The implementation marked these thirteen tables for the new helper:

| Page | View file |
|---|---|
| Employees | `base_template/views.py` |
| Branches | `organization/views.py` |
| Departments/adoptions | `organization/adoption_views.py` |
| Salary by month | `payroll/views.py` |
| Overtime | `payroll/views.py`, `payroll/table_views.py` |
| Penalty rules within Salary settings | `payroll/views.py` |
| Leave list | `leaves/views.py` |
| Leave types | `leaves/views.py` |
| Holidays | `scheduling/views.py` |
| My leave | `base_template/me_views.py` |
| My payslips | `base_template/me_views.py` |
| Approval inbox | `leaves/request_views.py` |
| Manager Branch attendance | `leaves/request_views.py` |

**Not converted in A15:** the main Attendance → Daily list and Nihal's device
lists: devices, enrollments, punches, messages, unresolved and device users.
These were left under N9. The platform company list already had a separate
server-side implementation. Other summary/detail tables were not all converted.
The manager's Branch attendance page is **not** the main attendance list.

### Shared files and implementation decisions

- `base_template/tables.py`: `paginate()` receives an already-authorised
  queryset; handles bounded search/order/count/LIMIT/OFFSET. `render()` renders
  the existing template and extracts the marked table's tbody into JSON via
  an HTML parser. It reuses escaped cell/action markup and row/cell attributes.
- `base_template/static/base_template/js/tables.js`: one DataTables
  initializer, numbered first/previous/pages/next/last controls, page sizes
  10/25/50/100, direct page jump, search across results, failure notice.
- `base_template/templates/base_template/includes/table_pagination.html`:
  counted HTML fallback with numbered links and a page-number input.
- `base_template/templates/base_template/base.html`: conditionally loads the
  existing DataTables 2.3.4 library and initializer. No vendor CSS was added;
  it uses the pre-existing Paper/Ink `vendor-controls.css`.
- Same list URL returns JSON for `table=1`; integer `draw/start/length`,
  `search[value]`, `order[n][column]` / `order[n][dir]`. Search/order fields are
  server-owned lists; regex/client ORM names are ignored. Page length caps at
  100. Counts are scoped after explicit page filters, before/after table search.
- `payroll/table_views.py`: overtime state/count projections in SQL, Employee
  and Branch filters, then database paging. Existing overtime calculation
  services remain. This duplicates some state logic in SQL; preserve/test
  parity if later business rules change.
- Salary snapshot numeric fields use casts for numeric ordering. Multi-segment
  leave is grouped into one request row, so counts match displayed rows.
- Some action/live/composite columns are intentionally not sortable; not every
  displayed column has a SQL ordering. Evaluate this against Ajay's requirements.
- `attendance/static/attendance/js/now_badge.js` was changed to read the current
  visible employee cells each poll, so paging does not poll only the first page.
- Existing list filters were retained; Overtime specifically gained Employee
  and Branch filters. Do not claim every conceivable filter was added everywhere.
- No migrations, added package dependencies or `.env.example` changes.

### Evidence and limits

- `.qa/a15-validation.log`: **171 tests passed in 43.561 seconds**, command:
  `.\venv\Scripts\python.exe manage.py test leaves base_template payroll --parallel 4 --keepdb --noinput`.
- `base_template/tests_tables.py`: five test methods with subcases for company
  adapters/orders, real LIMIT/OFFSET and count/search behavior, malformed input,
  escaping, tenant/role/self-service boundaries and overtime rule parity.
- Earlier focused attempts failed and were corrected; `.qa/a15-tests.log`,
  `.qa/a15-focused-tests.log`, `.qa/a15-fixes.log` include failures. Do not
  confuse them with the final successful validation log.
- This was **not a full repository test run**, nor an exhaustive browser check.
- The actual local company Employees page was opened and its retained styling,
  six rows and enhanced controls were visible. Overtime showed 30 September
  rows. Page size was changed to 10, direct jump to page 3 showed **21–30 of 30**,
  then clicking numbered page 2 showed **11–20 of 30**.
- These limited checks do not invalidate Ajay's report about his pages. The
  exact cause of his observed discrepancy was **not diagnosed**. Do not say it
  is definitely cache, a stopped server, a wrong branch or user error.
- The browser was closed deliberately. No subsequent fix was performed after
  Ajay disputed the completion report.

### The next decision, not an automatic implementation task

First present Ajay a page-by-page inventory: code present, verified behavior,
untested/disputed behavior and definitely missing conversion. Include both
company and employee/manager navigation. Ajay should decide whether Claude
finishes all table work now, reassigns N9, or proceeds with another step. Do
not declare "A15 accepted, next A10" because an older progress row says done.

## 8. Business and architecture rules already settled

Modular Django/PostgreSQL monolith. Apps: accounts, tenants, organization,
employees, scheduling, leaves, attendance, devices, payroll, auditlog,
access_control, subscriptions, base_template. `base_template` has no models.
Bangladesh defaults: BDT and Asia/Dhaka. Future APIs were planned around the
same services using FastAPI, not a new DRF implementation.

- Company-owned data uses `TenantOwned`; scoped managers raise without a
  company context. Use `with use_company(company_id)`. Preserve role/branch/
  department/employee restrictions and scoped counts. Never weaken tenancy or
  assign root a company membership as a shortcut.
- Custom account uses email login; membership selects company and role.
  Employee identity is permanent; reusable employee codes live on dated
  assignments. Placement, salary, shift and policy history must be retained.
- Writes belong in service functions with permission checks, validation and
  audit in the same transaction. Forms/views are not the only authority.
- Explicit `Meta.db_table` names matter; check the field dictionary rather
  than casually rename physical tables. Audit entries are append-only.
- Salary/penalty rule versions begin on the first of a month. Posted salary
  locks related attendance and overtime. Normal corrections must respect this.
- `SelfServiceGate` confines employee/manager accounts to `/me/` plus auth and
  company-switch routes. Company GETs redirect; blocked writes return 403.
- Attendance is live: recalculate on punches and refresh closed days on read.
  No new Calculate button. Do not rewrite attendance in a posted month.
- Software pairs scans into check-in/break-out/break-in/check-out; device
  action keys do not decide the sequence. Day closes at next shift start or
  24 hours, whichever comes first. Early arrivals are not paid/overtime.
- Open overtime after the shift remains unpaid until its end is decided.
  Completed real check-outs normally approve overtime automatically. Owner,
  company admin and HR can decide exceptions. A rule-set checkout may need review.
- Overtime counts from shift end with "Overtime after" normally zero. Example:
  18:00 end, 19:15 checkout = 75 counted minutes, 60 paid under hourly blocks.
  Minimum/block rules apply per day; too-short cases do not wait for approval.
  Separate normal/day-off multipliers default to 2×. Hourly-rate derivation
  follows hourly rate, daily rate / shift paid hours, or monthly day value /
  shift paid hours, respectively.
- Keep Monthly pay basis. Holidays/weekly offs are treated as paid calendar
  days; daily/hourly pay for them still follows Salary settings. Night shift
  and shift duration are derived from times; rotating shifts remain deferred.
- Employees see only **finalised** payslips. The finalisation feature itself
  remains A11; do not expose drafts to make My payslips look populated.
- Leave manager fallback and no-self-approval are settled in §6; do not re-ask.

## 9. Remaining work for Ajay's session (Claude)

First: review/report and get Ajay's decision on the disputed tables. The
previous feature order after A15 was the following; it is not authorization
to implement immediately:

| Step | Remaining intended scope |
|---|---|
| A10 | Full leave: half-day/hourly/partial pay; policies/versions; balances/entitlements/ledger; attachments; withdraw/amend; default leave types at onboarding; HR recording leave; employee code in picker |
| A11 | Salary completeness: mid-month pay changes and joining/leaving proration; allowances/components; manual bonus/deduction; finalise/approve/lock; corrections after finalising; payslip PDF/email; salary history |
| A12 | Access/permissions: department heads, granular permissions, HR and payroll-manager pages |
| A13 | Payments/dues, advances and loans; P5 scope |
| A16 | Physical SenseFace 3A verification in the office; not completed by synthetic home data |

Existing services/models may cover pieces of these steps. Inspect before
building; do not restart completed features from old broad P0–P7 roadmaps.
An older handoff also mentions a `tfoot` padding gap outside the payslip;
treat this as an unverified UI loose end, not a confirmed fixed defect.

## 10. Nihal's remaining part — paused

| Step | Planned responsibility |
|---|---|
| N5 | Attendance correction/review list; missed scans/status corrections with reason and audit; rule-checkout and open-overtime review. Link overtime decisions to existing A9; do not duplicate approval/pay logic |
| N6 | Employee detail/history (placement, salary, devices) and termination UI; `terminate_employee` already exists |
| N8 | Connected/Last seen/Not connected status; connection test based on a device's next check-in; silent-device alerts; refuse server-address changes for devices that never connected |
| N9 | Real server-side tables for main attendance list and device lists: devices, enrollments, punches, messages, unresolved, device users |

The old order was N5 → N6 → N8 → N9 after A15. A15 helper code is now on main,
but the product-wide table work is disputed and N9 is unfinished. Ajay may
change priority or ownership; coordinate before touching the same pages.

## 11. What to tell Nihal, only when Ajay resumes him

No message was sent by Codex. This is relay guidance requested by Ajay for
the successor. Do not dispatch it or start Nihal automatically.

Current status message:

> Main is at `3440212`. It contains A5c, the paid-break fix, A14 sidebar menus,
> A8 leave requests/approvals, and A15 helper code plus Ajay-side list changes.
> Ajay disputes completion of pagination across the app. Your main attendance
> and device lists have not yet been converted (N9). Stay paused until Ajay
> chooses the next task and confirms who owns the remaining table work.

When Ajay explicitly selects a Nihal step, provide one short copy-paste prompt
with a concrete branch name for that selected step and these instructions:

```text
Start from main: git checkout main, git pull --ff-only.
Use your venv Python, apply required migrations with python manage.py migrate,
then RESTART your running server so your browser uses the updated checkout.
Create the agreed feature branch from fresh main and implement only that step.
Preserve local changes; stop and report a conflict rather than overwrite them.
Push your feature branch after the step and tell Ajay its exact name and tests.
Never push main. Ajay's session reviews/merges and pushes main.
Do not edit the shared sidebar; report new destinations for Ajay's session.
Use focused tests during development and the agreed final checks for the scope.
Report completed work, remaining work, navigation, migrations and any
.env.example additions with non-secret example values. Never include secrets.
```

Cross-app changes he must retain:

- `attendance/services.py` `_write_day` populates approved overtime through
  `payroll.overtime.approved_minutes()`.
- `attendance/pairing.py` paid-break credit is limited to scheduled time.
- `attendance/static/attendance/js/now_badge.js` polls current visible cells,
  not only employee IDs captured on the initial page.
- The shared attendance calendar accepts `day_url_template` for employee links.
- Payslip template has `for_employee` branches, correct employee breadcrumbs,
  and labels derived from run status; don't expose company actions or drafts.
- A8 approval uses existing LeaveDay/attendance recalculation; don't replace
  this with separate overtime approval logic.
- Device registration must not invent a communication key. Preserve Remove
  communication key and the Push protocol override. Migration 0004 for the
  SenseFace 3A is already taken; pull before adding migrations.
- For N9, inspect `docs/SERVER_SIDE_TABLES.md`, preserve current styles and
  existing links, and actually convert his own lists with scoped database
  paging/search/order/counts. A15's presence alone does not convert them.

## 12. Device knowledge to retain for the office

- SenseFace 2A on Nihal's server: firmware ZAM70-NF24HA-Ver3.0.15, PushSDK
  3.0.4S, DeviceType `acc`; 3.x registry/tabledata roster path works.
- SenseFace 3A, office company "Ajay", Main Entrance, serial VGU6262600120:
  system 3.2.1.2.1 (2025-03-26), menu reports Push 3.1.2 but announces
  `pushver=2.4.1`, DeviceType `att`. It connected on 2026-09-14 after removal
  of an invented communication key. A scan arrived; roster upload remained
  empty and the 3.x user-query command returned -1004.
- Historical ngrok address/settings are in the original handoff; do not assume
  that tunnel is still live or copy it as this home computer's configuration.
- Planned hardware experiment: select PushSDK 3.x override, restart terminal,
  observe registry/push/tabledata. If it stops sending, revert to announced
  protocol, capture the 2.x user format (e.g. OPERLOG USER PIN) and implement
  its roster/query path based on actual evidence.
- Verify first contact, scans, users and server-address changes before widening
  catalogue capability flags. Hardware work requires the office device.
- A push device calls the server. The first server address must be entered on
  the terminal itself; a never-connected terminal cannot fetch a queued
  address-change command. Connection tests should wait for check-in, not
  pretend the server can ping/control an unreachable terminal.

## 13. UI and working discipline

- **Keep the current visual style.** Ajay requested functional pagination,
  not a redesign, and explicitly rejected only Next/Previous buttons.
- Reuse Sora, Paper/Ink tokens and existing shared components. No stock
  DataTables theme or invented colors/classes. `btn--secondary` is not valid.
- Important settings must remain in sidebar submenus **and** on their current
  individual pages. Keep root/company/employee navigation separate.
- Reuse project date/time widgets and dependent fields; database choices use
  Select2, fixed choices the established custom select. Commas on amounts;
  `money` and `hm` formatters for money/minutes. Maintain responsive behavior.
- Avoid unnecessary approval questions after implementation is authorized,
  but this handoff specifically requires a read-only first response.
- Keep scope explicit. Batch small reads, inspect only relevant sections,
  avoid repeated whole-repo scans, unnecessary agents, repeated full suites
  or redundant browser checks. Do not promise an exact usage percentage cap.
- The historical testing agreement: affected apps while building; full suite
  for significant merges/big changes, not just after a pull. New migrations
  require fresh test databases rather than stale worker clones. Parallel
  failures may need a targeted serial rerun for a readable traceback.
- Latest A15 evidence is the 171-test focused run, not the historical full
  suite. A5c recorded 905 full tests; A14 recorded 916. Do not mix these runs
  into a claim that the current commit passed the full suite.
- Ignored `.qa/` contains scratch scripts and old failure logs. Do not commit
  or rerun those indiscriminately. No secret handling is needed for review.
- Every later delivery should report: done before / changed this turn /
  remaining / exact navigation / tests and limits / docs and plan / Git state /
  `.env.example` variables and safe example values if changed.
- No environment variables were added by the home A5c, paid-break fix, A14,
  A8 or A15 work. No environment variables are changed by this handoff.

## 14. Requested first response from Claude

Start with: "I have reviewed the handoff. I have not implemented anything."
Only say this after actually doing the read-only review. Then give Ajay a
concise completed/pending/disputed inventory with navigation paths and the
division of responsibilities. Explain that the employee panel already exists,
that N9's main attendance/device conversions are outstanding, and that the
other claimed A15 conversions need reconciliation with his observed pages.
State that current application main is `3440212`, and that this handoff's local
documentation changes are uncommitted. Do not start the next feature until
Ajay has verified the inventory and tells you what to do.
