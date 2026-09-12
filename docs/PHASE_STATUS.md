# Attendance implementation status

## Platform corrections — 2026-09-07

User feedback corrected the onboarding contract. Read [UI_AND_ONBOARDING_CONVENTIONS.md](UI_AND_ONBOARDING_CONVENTIONS.md) and [PLATFORM_IMPLEMENTATION.md](PLATFORM_IMPLEMENTATION.md). Implemented one current master administrator per company with backend/database uniqueness, account editing, generated string codes/slugs, Bangladesh defaults, scoped Django admin forms, explicit `/platform/companies/`, centered responsive pages and readable four-space templates. No new environment variables. Existing data preserved; only the two shared demo memberships were ended with explicit user approval and audit entries. Full suite: 131 tests passed; the strengthened settings-admin valid-POST check also passed in the seven-test focused rerun. Browser creation/admin/feature flows and screenshots checked at 1440/768/375px. P1 remains in progress: next is company organization/scheduling writes and scoped authorization, then employee lifecycle pages. This supersedes earlier existing-account/role-picker examples.


## Table naming decision — 2026-09-08

Physical table names are now set explicitly with `Meta.db_table`, separately from
Django model names and app labels (which stay chosen for code readability).
`leaves`, `attendance` and `payroll` models all use the **`payroll_`** table
prefix, with the model name in snake_case: `LeaveType` -> `payroll_leave_type`,
`AttendanceRecord` -> `payroll_attendance_record`, `PayrollRun` -> `payroll_run`
(never double-prefixed). Model class names are unchanged - `LeaveType`, not
`PayrollLeaveType`. Company, branch and employee tables keep their own app
prefix because they are shared infrastructure used by every future module
(HR, Accounting), not payroll artifacts.

Applied to the 48 models in `leaves`, `attendance` and `payroll` that are **not
yet implemented**, so no table rename is required — the same free-rename window
used for `workforce` -> `employees`. `docs/scripts/build_schema.cjs` implements
the rule and the schema artifacts were regenerated: 83 models / 88 tables /
1,615 columns / 453 FKs unchanged, 49 tables now `payroll_` prefixed, **no
duplicate table names**.

Two already-migrated tables were deliberately **not** renamed:
`employees_employeecompensation` (8 rows) and
`scheduling_companyattendancesettings` (3 rows). Compensation is dated employment
history that payroll reads rather than a payroll artifact, and attendance
settings are policy configuration rather than attendance records. Renaming either
needs an explicit `AlterModelTable` migration and a deliberate decision — open
question for the lead.

`tenants.Feature` now uses `db_table = "module"` (migration
`tenants.0005_alter_feature_table`, applied). Pure `AlterModelTable`: the model is
still `Feature`, its three rows (`leave`, `attendance`, `payroll`) are unchanged,
and the 4 CompanyFeature grants plus 7 AccessPermission FKs are intact. Table
names serve the client's vocabulary; the backend structure stays whatever suits
the project. 131 tests still pass.

Device tables (`devices_*`) are unaffected, so the parallel device workstream
needs no change.

## Branch write flow — 2026-09-08

First company-administrator write workflow. `organization/services.py` holds the
authorization and the writes (`require_company_membership`,
`require_structure_manager`, `visible_branches`, `assert_branch_in_scope`,
`get_branch_for_edit`, `create_branch`, `update_branch`, `set_branch_status`);
`organization/{forms,views,urls}.py` and `organization/templates/organization/`
provide the UI. `auditlog.services.record_company_event` records a company
actor (USER + membership) as distinct from the platform owner.

Enforced on every write: active membership, role (owner/company_admin only),
branch row scope re-checked against submitted ids, and a field whitelist so a
crafted POST cannot reach an unexposed column. Each write shares one transaction
with its audit row. Exactly one active default branch per company is maintained
by demoting the previous one; the default branch cannot be retired; retiring
never deletes.

Controls corrected after review: native selects now draw our own chevron
(`appearance:none` plus currentColor gradients - no hex, no SVG file), and a
custom date picker / date-range picker was built from `design_reference/`
(`datepicker.css`, `datepicker.js`) replacing raw `input type=date`. Select2 was
already themed in `vendor-controls.css` and wired in `forms.js`.
`Branch.device_attendance_scope_override` was REMOVED from the branch form - it
is device-domain configuration, has no company default to override yet, and
belongs with attendance/device settings. The model field and service whitelist
keep it.

Known limitation: branch row scoping is currently unreachable in practice,
because `uniq_current_company_administrator` allows only one non-ended
owner/company_admin per company and only those roles may manage structure. The
enforcement is in place for when more roles gain structure rights.

147 tests pass on PostgreSQL (16 new), `check` clean, no migration drift, no new
migrations, no new environment variables.

## Root-owned department/designation catalogue — 2026-09-09

Ownership decision, taken by the user: **Department and Designation move under
root control. No company may write to those tables.** DeviceVendor stays root-
owned as already planned.

Why this needed more than a flag. Both models carried company-specific columns —
`Department.branch`, `Designation.department`, `Designation.parent` — and five
other models pointed at them (`EmployeeAssignment.department/designation`,
`DepartmentShift.department`, `DesignationPermission.designation`,
`EmployeePermissionOverride.allowed_departments`,
`CompanyMembership.allowed_departments`). A single global row cannot hold one
company's branch or one company's shift pattern, so the catalogue could not
simply lose its `company_id`; the company-specific half had to move somewhere.

Shape now built:

- `organization.Department` / `organization.Designation` — root-owned global
  catalogues, no `company` column, globally unique names.
- `organization.CompanyDepartment` — tenant; branch + catalogue department +
  nullable `head` (an Employee). Unique `(branch, department)`.
- `organization.CompanyDesignation` — tenant; company department + catalogue
  title. Unique `(company_department, designation)`.
- All five FKs above now point at the adoption rows. `code`/`name` are read
  through to the catalogue and are NOT stored on the adoption row, so they
  cannot drift.

Access model changed with it, at the user's instruction ("i don't need the
designation hierarchy"):

- `Designation.parent` and `hierarchy_level` removed, and with them the
  parent-chain ceiling walk in `DesignationPermission.clean()`.
- New `access_control.DepartmentPermission` — the department is now the unit of
  delegation. A DENIED rule there is a hard ceiling that neither a title rule
  nor an individual grant can lift; an ALLOWED rule is the floor everyone in the
  department gets. `has_permission()` resolution is now
  feature -> department ceiling -> employee override -> designation ->
  department floor -> deny.
- `EmployeePermissionOverride.clean()` gained the same ceiling check, because
  `CompanyDepartment.head` is meant to administer their own people — without it,
  a head could grant themselves anything.

Migrations, in the order they must run: `organization.0002` creates the adoption
rows and fills them from existing data; `employees.0002`, `scheduling.0002`,
`access_control.0002` and `accounts.0005` repoint their FKs (add / backfill /
drop / rename, because the new rows have different primary keys);
`organization.0003` collapses duplicate catalogue rows across companies and
strips `company`/`branch`/`parent`/`hierarchy_level`. `0003` runs last on
purpose: the old columns are the only source of truth for who adopted what.

Two bugs the migration test caught that the ordinary suite could not. The suite
builds an empty database, so it exercises every schema step and none of the
`RunPython` bodies. `organization/tests_migrations.py` rewinds to the
tenant-owned world, writes two companies that each created their own "Software"
department containing their own "Manager", and rolls forward. It failed twice:
`Designation.parent` is PROTECT, so merged-away titles could not be deleted
while another title still named them as a parent; and PostgreSQL refuses to
ALTER a table with pending deferred FK trigger events, which the deletes queue.
Fixes: null the parent chain before collapsing, and `SET CONSTRAINTS ALL
IMMEDIATE` before the schema operations in the same transaction.

Also: new multi-word tables follow the documented snake_case rule
(`organization_company_department`, `organization_company_designation`,
`access_control_department_permission`). Pre-existing run-together names
(`scheduling_departmentshift`, `access_control_designationpermission`,
`employees_employeeassignment`) were left alone and the exception is now written
down in MODEL_FIELD_DICTIONARY.md rather than left as an undocumented
inconsistency.

Inventory moves from 83 models / 88 tables to **86 models / 91 tables /
1,639 columns / 465 FKs**. Schema artifacts regenerated with
`node docs/scripts/build_schema.cjs`; `verify_schema.cjs` passes. The previous
schema documents, and the whole project as it stood before this change, are on
the `department_designation_relation_companywise` branch at commit a5f59f9.

164 tests pass on PostgreSQL (17 new: 11 organization/access_control behaviour,
6 migration), `check` clean, `makemigrations --check` reports no drift, no new
environment variables. **Not yet applied to the development database** — the
user runs `migrate`.

## Designations are independent of departments — 2026-09-12

**Correction of the 2026-09-09 catalogue change.** Ajay's instruction was that
root keeps departments and designations as **two separate lists**, and each
company decides which designations go under which of its departments — the
backup branch was named `department_designation_relation_companywise` for
exactly that reason. The 2026-09-09 change kept the old `Designation.department`
foreign key on the root catalogue, which made the relation root-wise: root had
to create "HR Manager" and "Sales Manager" separately, and no company could put a
designation under a department of its own choosing. That was carried over from
the old model without being checked against the instruction, and it was then
written into Nihal's task prompt as intended behaviour, so his root and company
screens were built to enforce it.

Corrected design, now reflected in `docs/MODEL_FIELD_DICTIONARY.md` §8/§85,
`docs/DATABASE_SCHEMA.md` rows 8/85, `docs/DATABASE_MODEL_PLAN.md` §8/§85 and the
regenerated schema artifacts:

```
Department          root     code, name
Designation         root     code, name          <- no department
CompanyDepartment   company  branch + department (+ head)
CompanyDesignation  company  company_department + designation   <- the relation
```

`CompanyDesignation` already existed and already was the company-wise relation;
the only structural error was the root foreign key and the rules built on it.

Also recorded: a **vocabulary rule** in `docs/UI_AND_ONBOARDING_CONVENTIONS.md`.
Nihal's screens used "Hire an employee", "job title", "adopt a department" and
"catalogue". This portal creates and edits employees; it does not hire. The UI
says **Create employee / Edit employee**, **Designation**, **Add department /
Assign designations**, and never "catalogue" on company screens.

**Status: merged.** Nihal made the code change on
`feature/organisation-catalogue-ui` (commit 2852d07) and it was merged into
`feature/company-org-setup` after review:

- `organization/0004_designation_independent_of_department` — additive and
  reversible. Removes `Designation.department` and the per-department unique
  constraints, merges designations that differed only by department
  (case- and spacing-insensitive), repoints every company placement at the
  survivor, suffixes colliding codes (`MGR`, `MGR-2`), and settles deferred
  constraints before the ALTERs.
- Removed everything the old rule required: `CompanyDesignation.clean()`, the
  department filter on the company's designation multiselect, the
  `department_titles` endpoint and `adoption.js`.
- `tests_migrations.py` now migrates to the graph's leaf nodes and asserts the
  final state: one "Manager" row, every company placement resolving to it,
  globally unique codes.
- Wording applied everywhere a person reads it, per the vocabulary table in
  `UI_AND_ONBOARDING_CONVENTIONS.md`. The `hire_*` modules, templates, script,
  tests and URLs became `employee_*`, and `employees.services.hire_employee`
  became `create_employee`. Root screens moved from `/platform/catalogue/...`
  to `/platform/departments/` and `/platform/designations/`. Internal names
  (`CompanyDepartment`, `CompanyDesignation`, `adoption_*`, `catalogue_*`) are
  unchanged because nobody reads them on screen.
- The root designation usage table gained a Department column, because a
  company using one designation under two of its departments otherwise showed
  as two identical rows.

Review found one edit to an already-applied migration
(`access_control/0002`) — docstring wording only, no operation changed, so it
is safe. His branch notes were written to a new repo-root `PHASE_STATUS.md`
because he had been told not to edit `docs/`; they are folded into this entry
and that file is removed.

**Wording still open** — raised by Nihal, none blocking, for Ajay to decide:

1. **"Platform lists"** as the breadcrumb on the root department/designation
   forms — the only phrase found for "the two root lists together".
2. **"Add a department"** means different things on the two surfaces: root
   *creates* a name for everyone, a company *starts using* one in a branch. The
   company one could become "Use a department here" if it reads ambiguous.
3. **"Used by"** on the root lists counts company placements, so one company
   using Manager under two departments counts as 2. If it should mean "how many
   companies", the annotation needs to count distinct companies instead.

Schema artifacts regenerated: 86 models / 91 tables / **1,638 columns / 464
FKs** (one column and one foreign key fewer). `verify_schema.cjs` passes.

## Salary fast-track — 2026-09-12

**Decision (Ajay):** salary generation is needed today. The original flow is
**reordered, not cut** — in Ajay's words, *"nothing will be removed from the
original flow; we will implement all these that we are skipping today"*.

A thin slice of four phases is built first, in this order, each on the real
dictionary models so later work extends them additively rather than replacing
them:

| Step | What | Status |
|---|---|---|
| 1 | Schedules: shifts, company attendance settings (single company shift), weekly off days, holidays | **Done** — see below |
| 2 | Leave, thin slice: leave types; company admin records approved full-day leave, paid or unpaid | **Done** — see below |
| 3 | Attendance calculation from device punches: present / half day / absent / weekly off / holiday / paid leave / unpaid leave; monthly calculate + review list | **Done** — see below |
| 4 | Basic salary: generate a month (draft, regenerable), one record per employee with lines, payslip | **Done** — see below |

**Formula agreed (defaults, 2026-09-12):**

- Monthly staff: base − (absent days + unpaid-leave days) × base ÷ 30. Weekly
  offs, holidays and paid leave are not deducted.
- Daily staff: rate × days present; a half day counts as 0.5. Weekly offs and
  holidays are not paid.
- Hourly staff: rate × hours worked.
- Days before the employee's joining date are not counted at all.

**Keep payroll runs as draft** until full leave and the payroll correction path
exist. A finalised run is immutable by design, and today's rules are
deliberately incomplete.

### Step 1 — Schedules (done 2026-09-12)

The scheduling tables already existed from P1; this adds the screens and
services. New: `scheduling/services.py`, `forms.py`, `views.py`, `urls.py` and
`templates/scheduling/` (overview, shared form, holiday list, holiday cancel),
mounted at `/schedules/`. The sidebar "Schedules" link, previously a dead `#`,
now points there.

- **Shifts:** code, name, start/end (24-hour text `HH:MM` — no native time
  control), night-shift tick, "late after" grace, full-day and half-day minimum
  minutes. Shift length is **derived** from start and end, never typed.
  Deactivate, never delete; the company shift cannot be deactivated.
- **Attendance settings:** choose the company shift and what a missing punch
  means. Saving switches the company to single-shift mode — onboarding leaves
  it in department mode because no shift exists yet — and bumps
  `settings_version`. The overview warns *"Attendance cannot be calculated
  yet"* until a company shift is chosen.
- **Weekly off days:** company-wide or per branch, paid or unpaid, from a date.
  Stopped with an end date, never deleted, so past months keep them.
- **Holidays:** full-date, company-wide or per branch, paid or unpaid; list by
  year with pagination; cancel keeps the record.
- Every write: owner/company-admin only, field whitelist, branch scope
  re-checked, one transaction with an audit row. Duplicate weekday or holiday
  date gives a readable field error instead of a constraint name.

Two existing bugs in `Shift.clean()` fixed (no migration — `clean()` only):
it compared start and end times without checking they existed, and it blamed
the break field for a zero or negative shift length. Either made any form that
rejected a time crash with a 500 instead of showing the field error.

31 new tests (`scheduling/tests_screens.py`), including a real request to every
page. Full suite: **407 tests pass** on PostgreSQL; `check` clean; no migration
drift; no new environment variables. Browser check at 1440/768/375 is still pending — it needs a signed-in
company administrator.

### Fixes from Ajay's review (2026-09-12)

- **Every calendar closed when a month or year was picked.** The month/year
  dropdowns are custom selects whose option list is moved to `<body>`, so the
  calendar read a click on a month as a click outside and closed. Fixed in
  `datepicker.js`, which every date field on the site uses. Escape inside an
  open list now closes only the list; the check runs in the capture phase
  because `customselect.js` loads first. Custom dropdowns also open scrolled to
  the selected option — the year list used to open at 1946. Verified in a
  browser.
- **Weekly off days: several at once.** The Day dropdown became seven day
  buttons, Saturday first (Ajay's choice). `add_weekly_offs` stores one rule per
  selected day in one all-or-nothing transaction, and a clash names the day
  already off. New `.day-picker` component in `components.css`: real checkboxes,
  visually hidden but focusable, with the calendar's filled-ink selected style.
  Checked at desktop and 375px. 36 scheduling tests pass.

### Step 2 — Leave, thin slice (done 2026-09-12)

New in the `leaves` app: models, migration `leaves/0001_initial`, services,
forms, views, URLs and templates, mounted at `/leave/`. The sidebar "Leave"
link, previously a dead `#`, now points there.

- **Models** use the dictionary's names and fields (§39, §46–48), today's
  subset only: `LeaveType`, `LeaveRequest`, `LeaveRequestSegment`, `LeaveDay`.
  Tables follow the payroll-family rule: `payroll_leave_type`,
  `payroll_leave_request`, `payroll_leave_request_segment`,
  `payroll_leave_day`.
- **Paid or unpaid is chosen per leave, not per type** — the design has the
  approver decide pay, so a leave type carries no paid flag.
- **Record leave**: employee, leave type, from–to (one range control), paid or
  unpaid, reason. Recorded as already approved. The service expands the range
  into one `LeaveDay` per **working** day: weekly offs and holidays inside the
  range are skipped and noted on the request, so a Thursday–Saturday unpaid
  leave with Friday off costs two days. Each day stores the shift window and
  minutes it covers and a pay percentage (100 or 0).
- Refused with a readable message: overlap with existing leave (names the
  dates), before joining or after leaving, no placement on a date, a range with
  no working day, an inactive leave type, no company shift chosen yet.
- **Cancel** keeps the leave on record; its days become cancelled and the same
  dates can be recorded again.
- **Leave types**: list, add, edit, activate/deactivate.
- `scheduling/calendar.py` — new shared `WorkCalendar`: for a branch and date,
  working day / weekly off / holiday. Leave uses it now; attendance will too.
- `common/forms.py` — `apply_service_errors` moved here from the schedule views
  so every app puts a service's error on the right field the same way.

**Another calendar bug found while testing, fixed in `datepicker.js`:** the
date-range picker closed after its first date, so an end date could never be
picked. Clicking a day redraws the grid and removes the clicked button, so the
outside-click check found it "outside". Outside clicks are now judged by the
click's recorded path. Report-style quick ranges ("Last 30 days") are switched
off on the leave field with `data-presets="none"`. Verified in a browser:
17 → 19 September writes both dates.

18 new tests (`leaves/tests_leave.py`), all passing.

### Steps 3 and 4 — Attendance and basic salary (done 2026-09-12)

**Attendance** (`attendance` app, `/attendance/`): `AttendanceRecord`
(dictionary §34 subset, table `payroll_attendance_record`), one per employee
per day. `calculate_attendance` reads authorized, non-duplicate device punches
inside the shift's attendance window, approved `LeaveDay`s and the
`WorkCalendar`:

- leave → *Leave*, payable by its pay percentage
- holiday / weekly off → payable if marked paid
- IN and OUT → *Present* (≥ full-day minutes), *Half day* (≥ half-day
  minutes) or *Absent*; late minutes after the grace are recorded
- IN only → *Incomplete*, counted as present and flagged, when the company's
  missing-punch setting is "review required"; *Absent* when it is "treat as
  absent"
- no punch → *Absent*

Recalculating replaces the month. The attendance page lists every day with
filters, and has a Calculate button.

**Basic salary** (`payroll` app, `/salary/`): `PayrollPeriod`, `PayrollRun`,
`PayrollRecord`, `PayrollLine` (dictionary §60–62, §65 subset; tables
`payroll_period`, `payroll_run`, `payroll_record`, `payroll_line`).
**Generate** recalculates the month's attendance, then writes one draft record
per employee. `calculate_pay` is pure and applies the agreed formula: monthly
base − (absent + unpaid leave + ½ per half day + unpaid off days) × base ÷ 30;
daily rate × payable working days; hourly rate × hours worked plus paid leave
hours. It never goes below zero. The rate is the compensation in force at
month end. Runs stay **draft** and can be regenerated. Each employee has a
**payslip** with earnings, deductions, net pay and the month's attendance
counts, printable from the browser.

**Demo punches** for development: `python manage.py seed_demo_punches
--company <code> [--month YYYY-MM]` registers a clearly labelled simulated
device, enrols the company's employees, and sends a repeatable month of
punches (on time, late, half day, absent, one missing OUT) through the real
parser and `ingest()` pipeline. DEBUG only unless `--force`.

7 new tests (`payroll/tests_basic.py`): the formula for each pay basis, and
punches → attendance → salary end to end. The full suite was not run, at
Ajay's instruction, until salary works; it is the gate before `main`.

### Department shifts (added 2026-09-12, at Ajay's direction)

The fast-track had put every employee on one company shift. Ajay's flow is that
**a shift is assigned to a department and every employee in it gets that shift
automatically**; only the employee-level override waits.

- Attendance settings offer **Shifts per department** or **One shift for the
  company** again (saving no longer forces single-shift mode). In department
  mode the company shift is optional and covers any department without its own.
- **Set department shift** (Schedules): department, shift, from a date. A change
  closes the department's previous shift the day the new one starts — nothing
  deleted, so past months keep the shift they were worked on. The same start
  date replaces; a date before an existing later change is refused.
- The Schedules page lists every department with the shift it works today, or
  "Company shift" / "No shift", and warns when a department has nothing to be
  measured against.
- `WorkCalendar.shift_for(department, date)` is the single place that decides
  the shift. Attendance, leave and the demo punches all use it, so an employee
  is measured against their own department's shift for each day.

Felna Tech is still in single-shift mode (set earlier today so its attendance
could be calculated); switching it to per-department is a settings change in
the UI.

### Skipped today — must be built and connected to salary

This is the authoritative list. Nothing on it is dropped; each item says how it
connects back.

| Skipped | Effect on today's salary until built | Connects back into |
|---|---|---|
| **Full leave (P2):** employee requests, approval step, half-day and hourly leave, partial pay, leave policies and policy versions, balances/entitlements and the balance ledger, attachments, withdrawal, amendment (cancel exists) | Only admin-recorded, pre-approved full-day leave counts | Attendance day status → payroll deductions |
| Leave fields not built yet: `policy_version`, `original_request`/`supersedes`, `current_approval_stage` (request); `original_segment` (segment); `policy_type_rule`, `entitlement`, `supersedes` (leave day) | None until policies, balances and amendment exist | Additive migrations on the same tables |
| HR role recording leave (only owner/company admin today) | Company admin records all leave | Access work |
| Default leave types created at company onboarding | Each company adds its own types first | Onboarding |
| Employee code shown in the leave form's employee picker | Names only; two people with one name look alike | Record leave form |
| Attendance corrections (manual fixes to a day) | A missed punch stays absent / half day | Attendance record → payroll |
| Breaks and multiple IN/OUT sessions (`AttendanceSession`, `PunchAllocation`) | Only first IN and last OUT count | Worked minutes |
| Late, absence and repeated-lateness penalty rules | Late minutes recorded, not deducted | Payroll deduction lines |
| Overtime review, approval and pay | Not paid | Payroll earning lines |
| Employee-level shift override, rotating shifts | Everyone works their department's shift (or the company shift) | Which shift a day is measured against |
| Holiday / weekly-off work assignments (`HolidayWorkAssignment`) | Working a holiday is not paid extra | Payroll earning lines |
| Shift fields not on the form: break minutes, paid break, grace-out, overtime-after, effective dates | Defaults (0 / unpaid) | Worked minutes, overtime |
| Attendance settings not on the form: punch pairing, duplicate-punch window, attendance windows, rounding, overtime approval | Defaults | Punch interpretation |
| Mid-month salary change (compensation segments / proration) | The rate in force at month end is used for the whole month | Payroll record |
| Joining or leaving mid-month (proration rules) | Only days on the payroll are counted | Payroll record |
| Salary structure: allowances and components | Base rate only | Payroll lines |
| **Finalise / lock a payroll run**, approval steps, correction/reversal after finalising | Runs stay draft and are regenerated; nothing is locked | Payroll run |
| Manual bonus / deduction lines on a salary | Not available | Payroll lines (`is_manual` already exists) |
| Attendance review status and manual day corrections; the *Incomplete* (missing OUT) decision | A missing OUT is counted as present and flagged | Attendance record |
| Payslip as PDF / email; per-employee salary history | Browser print only | Payslip |
| Salary and attendance pages for HR and managers, not only the company admin | Company admin only | Access |
| Payments, part-payments, dues, advances, loans (P5) | Payslip shows the amount; paying it is not recorded | Salary management |
| Employee detail, edit, history, transfer, salary revision, terminate screens | Services exist, no screens | Employee records used by payroll |
| Access: department heads, permissions, employee logins; branch-administrator decision | Company admin only | Leave approval |
| A proper time-picker component | Plain `HH:MM` text box | Shift form |
| **Holiday year calendar** (Ajay, priority later): a larger calendar to select all of a year's holidays at once, multiple dates, with month/year navigation | Holidays are added one at a time | Holiday list |
| Static file cache-busting for deployment (hashed file names) | After a JS/CSS change a browser keeps the old file until Ctrl+F5 | Every page |

## Current checkpoint

- **Current deliverable:** P1 platform onboarding implemented; company setup and employee write workflows remain next. See [PLATFORM_IMPLEMENTATION.md](PLATFORM_IMPLEMENTATION.md) for files/functions and the UI workflow.
- **Actual code:** 22 implemented domain models, including the planned AuditLog; 13 registered apps and model-free base_template. P0 remains Verified. P1 is In progress; the earlier “backend complete” wording overstated authorization and workflow readiness.
- **Platform UI:** root routes to /platform/companies/ without membership; generated company identifiers, company create/edit/status, one master administrator with editable credentials/status, dated feature access and recent audit history work through forms.
- **Company UI:** existing lists remain read-only. Company-wide access is now restricted to unrestricted owner/company_admin; other roles/scopes fail closed until proper scoped views ship. Unimplemented Add/Export controls are explicitly disabled with reasons.
- **Design:** Warm Paper / Ink tokens and Sora; new platform tables use real DataTables with server-side paging/search/sorting, and database-backed selects use real Select2 while fixed choices use styled native selects. Responsive browser verification is required. Remaining component groups are not claimed complete.
- **Database:** existing PostgreSQL data/history preserved; two original auditlog migrations plus three additive corrections for Company defaults, the code sequence and administrator uniqueness. The root-catalogue change adds six migrations across five apps; with the 2026-09-12 designation correction the documented inventory is 86 models / 91 tables / 1,638 columns / 464 FKs. Nihal's organization/0004 is merged, so code and documents now agree.
- **Architecture/user contract:** modular Django monolith, accounts.User, Django-owned ORM/migrations; future FastAPI and workers reuse services. No DRF or duplicate persistence layer.
- **Hardware:** D1 remains unverified; the original “roughly a week” estimate is historical, not a current availability claim.
- **Next action:** salary fast-track (2026-09-12) — step 2, the thin leave slice, then attendance calculation, then payroll. Then build everything in the "Skipped today" table and connect it back. Employee lifecycle screens and full P1 acceptance follow. Do not restart P0, recreate apps, or assign root a membership as a shortcut.
- **Environment:** no new .env variables.
- **Verification on 2026-09-07:** 124/124 tests pass on a fresh dedicated PostgreSQL test database (101 existing + 23 new); `check` clean; `makemigrations --check --dry-run` reports no changes; auditlog.0001 and .0002 applied successfully to the development database. Browser onboarding passed without seed_demo at 1440px, 768px and 375px. Full P1 employee onboarding is still pending.

## Phase tracker

| Phase | Status | Evidence required before completion | Current next action |
|---|---|---|---|
| P0 — Foundation/contracts | **Verified** | Runnable Django/PostgreSQL foundation, initial migrations/checks, tenancy and dependency decisions | Complete: migrations applied on PostgreSQL, 5/5 isolation tests green on Postgres, tenancy strategy + scoping code recorded |
| P1 — Company/people/calendar | In progress — platform onboarding delivered | Browser company setup, employee/history and access checks | Next: company organization/scheduling writes and scoped authorization, then employee lifecycle forms. Full P1 cold-start gate remains outstanding. |
| P2 — Leave | Not started | Approved full/half/hourly/partial-pay leave, optional balances, cancellation and concurrent-balance checks | After P1 |
| P3 — Attendance simulation/manual | Not started | Shared ingestion/calculation pipeline, cross-device/retry/history tests, manual correction workflow | After P1; integrate LeaveDay from P2 |
| P4 — Payroll | Not started | Correct monthly/daily/hourly calculations, traceable lines, finalization and correction checks | After P2 and minimum complete P3 |
| P5 — Salary management | Not started | Payments/dues, adjustment and funded-advance recovery checks | After P4 |
| D1 — Real device | Waiting for hardware and P3 | Actual model/firmware/payload/retry/offline evidence; distinguish one-device from multi-device proof | Continue software phases while waiting |
| P6 — Pilot | Not started | End-to-end workflows, tenant/isolation checks, operational readiness and explicit manual/biometric release label | After P1–P5; D1 for biometric claim |
| P7 — Extensions | Deferred by release scope | Feature-specific acceptance checks; do not infer completion from model existence | Prioritize after initial client workflow is usable |

## Implementation evidence

Existing planning checks cover documentation field/relationship consistency and the diagram viewer only. The first implementation evidence is recorded in the session log below; it does not yet validate attendance calculations, leave balances, salary amounts or real hardware.

### Session log

```text
Session date: 2026-09-07
Phase: P1 platform-owner onboarding (first part of UI slice 2)
Implemented: root routing; companies list/create/detail/edit/status; new/existing
  administrator accounts and memberships; membership editing; feature changes;
  transactional append-only audit; actual themed DataTables + Select2.
Files/functions: see PLATFORM_IMPLEMENTATION.md for the complete inventory.
Models/migrations: planned AuditLog model (22 implemented total); additive
  auditlog.0001_initial + .0002_protect_audit_rows. No old migrations rewritten.
Checks actually run:
  - 124 tests PASS on PostgreSQL, 69.286 seconds in the final full-suite run.
  - manage.py check: no issues.
  - makemigrations --check --dry-run: no changes.
  - migrate: both auditlog migrations applied OK to the existing development DB.
  - Browser: separate fresh PostgreSQL QA DB with only a bootstrap root, then
    create company, new company admin, feature enablement, activation, company
    edit, membership edit and administrator login using UI forms; no seed_demo.
  - DataTables search, result counts; Select2 actual option interactions;
    desktop/tablet/mobile screenshots and computed styles checked.
  - No page horizontal overflow at 1440/768/375px; mobile menu open/Escape close.
Earlier failures resolved: full_clean requires temporary tenant context even for
  authorized platform writes; preliminary overlapping test runs collided, so all
  final checks ran sequentially in a dedicated test database. A proposed catalogue
  data migration conflicted with existing isolated test fixtures and was removed
  before development migration; onboarding initializes reference features instead.
Limits: company pages still lack write forms and granular scopes. They now fail
  closed for non-admin/restricted memberships. Support impersonation, packages,
  subscription workflows, remaining component groups, RLS and job scoping remain
  unfinished. No real-device/leave/payroll functionality is claimed.
Environment: no new variables; .env.example unchanged.
Exact next task: company authorization/scopes and organization/schedule writes;
  then employee lifecycle forms and the full P1 cold-start employee workflow.
```

```text
Session date: 2026-09-05
Attendance repository/workspace: D:\attendance_device (standalone; separate from Polymer)
Phase(s) worked: P0 — foundation
Implemented behavior:
  - Verified user-created project state: config package, 13 app folders, Django 6.1.1
    in venv, no db/migrations yet (clean slate).
  - Flagged + fixed: tenants, organization, workforce (since renamed to
    "employees") were NOT in INSTALLED_APPS.
  - Set AUTH_USER_MODEL = "accounts.User" before any migration.
  - Created custom accounts.User (AbstractUser, email login via custom UserManager,
    optional username, phone/timezone/language, created_at/updated_at) + admin.
  - Switched DATABASES to PostgreSQL via ATTENDANCE_DB_* env vars (12-factor);
    installed psycopg[binary] 3.3.5; added CONN_MAX_AGE.
  - Added common/ package with abstract bases TimeStamped, ActorTracked
    (TenantOwned deferred to P1 with Company). No new Django app / no new table.
Models/migrations/files changed:
  config/settings.py; accounts/models.py; accounts/admin.py; common/__init__.py;
  common/models.py; accounts/migrations/0001_initial.py (generated);
  docs/ENGINEERING_LEARNING_LOG.md + docs/TEAM_LEAD_PLAYBOOK.md (new).
Checks actually run and results:
  - manage.py check -> "System check identified no issues (0 silenced)".
  - get_user_model()._meta.label -> "accounts.User"; USERNAME_FIELD -> "email".
  - makemigrations -> accounts/0001_initial.py, depends on ('auth', ...),
    db_table accounts_user, custom manager registered.
Manual/simulated/real-device evidence: none (no attendance work this session).
Decisions adopted and outstanding:
  - ADOPTED: email-login custom user; PostgreSQL via env config; abstract bases
    in common/ (not a 14th app).
  - OUTSTANDING (P0 close-out): apply migration to fresh PostgreSQL (user-run);
    tenant-enforcement mechanism (ownership + same-company FK checks + RLS plan);
    83-model dependency/cycle map; then P1 Company/TenantOwned.
Known limitations or blockers:
  - Migration not yet applied to PostgreSQL (user creating DB/role + running
    migrate themselves; assistant did not handle the DB password).
Phase completion criteria still missing:
  - migrate on clean PostgreSQL; tenant context + initial isolation checks;
    recorded dependency/decision notes.
Exact next task:
  - User runs the migrate commands and pastes output; on success, record tenancy
    enforcement + model-dependency notes to close P0, then begin P1 (tenants.Company
    + TenantOwned base + company onboarding).
```

```text
Session date: 2026-09-05 (continued)
Phase(s) worked: P0 close-out / P1 slice 1 — tenant isolation foundation
Implemented behavior:
  - common/tenant.py: contextvars current-company + use_company() context manager.
  - common/models.py: TenantManager (fail-loud when no context) + TenantOwned
    abstract base (company FK, objects scoped, all_objects unscoped,
    base_manager_name=all_objects, save() auto-stamps company from context).
  - tenants/models.py: Company (tenant root; not TenantOwned).
  - accounts/models.py: CompanyMembership (first TenantOwned model; unique
    (company,user)); branch/dept M2M scopes deferred to P1 organization slice.
  - admin: Company registered; CompanyMembership admin uses all_objects.
Models/migrations/files changed:
  common/tenant.py (new); common/models.py; tenants/models.py; tenants/admin.py;
  accounts/models.py; accounts/admin.py; tenants/tests.py (new);
  tenants/migrations/0001_initial.py + accounts/migrations/0002_companymembership.py
  (generated); docs/ENGINEERING_LEARNING_LOG.md (Lesson 5).
Checks actually run and results:
  - manage.py check -> no issues.
  - makemigrations -> tenants/0001_initial (Company) + accounts/0002 (CompanyMembership).
  - Migration chain applied clean in order accounts.0001 -> tenants.0001 ->
    accounts.0002 (cross-app cycle auto-resolved by dependency graph).
  - 5/5 tenant-isolation tests PASS on in-memory SQLite (scoped list, get-by-id
    isolation, no-context fail-loud, unscoped-all, company auto-stamp).
Manual/simulated/real-device evidence: none (no attendance work).
Decisions adopted and outstanding:
  - ADOPTED: fail-loud scoped manager + all_objects escape hatch; company auto-stamp
    on save; CompanyMembership branch/dept M2M deferred to organization slice.
  - OUTSTANDING: run migrate + tests on PostgreSQL (user); request middleware +
    Celery base (next slices); composite (id, company_id) FKs on high-risk relations;
    RLS before P6 pilot.
Known limitations or blockers:
  - Isolation verified on SQLite only; PostgreSQL acceptance run is the user's step.
  - No web request path yet (middleware deferred to the P1 auth/login slice).
Exact next task:
  - User runs migrate + `manage.py test tenants` on PostgreSQL and pastes output.
    Then build the organization app (Branch/Department/Designation) as the next P1
    slice, followed by tenant middleware wired to CompanyMembership resolution.
```

```text
Session date: 2026-09-05 (continued)
Phase(s) worked: P0 sign-off + P1 slice 2 — organization structure
P0 CLOSED — evidence:
  - showmigrations on PostgreSQL: accounts.0001 [X], accounts.0002 [X],
    tenants.0001 [X] (user ran migrate; DB "attendance_system").
  - 5/5 tenant-isolation tests PASS on real PostgreSQL (not just SQLite).
Implemented behavior (P1 slice 2):
  - common/choices.py: ActiveStatus, DeviceAttendanceScope shared enums.
  - common/models.py: TenantOwned.validate_tenant_consistency() — every FK to a
    tenant-owned model must share the company; runs via full_clean(). Inherited
    by all future tenant models.
  - organization/models.py: Branch (unique (company,code); partial-unique single
    active default branch; device_attendance_scope_override), Department (FK
    Branch PROTECT; unique (branch,code) and (branch,name)), Designation (FK
    Department; nullable self-FK parent; hierarchy_level derived in save();
    unique (department,code); check constraint parent != self; clean() rejects
    self-parent, cross-department parent and hierarchy cycles).
  - organization/admin.py: all three registered via unscoped all_objects.
Models/migrations/files changed:
  common/choices.py (new); common/models.py; organization/models.py;
  organization/admin.py; organization/tests.py (new);
  organization/migrations/0001_initial.py (generated, 6 constraints);
  docs/ENGINEERING_LEARNING_LOG.md (Lesson 6).
Checks actually run and results:
  - manage.py check -> no issues.
  - makemigrations organization -> 0001_initial with all 6 constraints.
  - 14/14 tests PASS on real PostgreSQL (9 organization + 5 tenants), covering
    per-company scoping, duplicate code rejection, same code allowed in another
    company, second-active-default rejection, cross-company FK rejection,
    hierarchy_level derivation, self-parent, cycle and cross-department parent.
Decisions adopted and outstanding:
  - ADOPTED: DB constraints for single-row/raceable rules; application clean()
    only for multi-row traversal (cycle detection); shared enums in common/.
  - OUTSTANDING: organization/0001 not yet applied to the dev DB (user runs
    migrate); DB-level composite (id, company_id) FKs still to be added;
    tenant middleware; RLS before P6.
Known limitations or blockers:
  - validate_tenant_consistency() only runs when callers use full_clean();
    services must call it. DB composite-FK backstop still pending.
Exact next task:
  - User runs `manage.py migrate` to apply organization/0001. Then build the
    employees app (Employee, EmployeeAssignment with reusable dated employee_code,
    EmployeeCompensation) as P1 slice 3.
```

```text
Session date: 2026-09-06
Phase(s) worked: P1 slice 3 — app rename + employees domain
App rename (user-requested):
  - "workforce" renamed to "employees" BEFORE any migration existed, so there was
    no table rename and no migration history to rewrite. Old app directory deleted,
    new app created with startapp, INSTALLED_APPS updated.
  - Renamed across code + docs: MODEL_FIELD_DICTIONARY.md (the generator's single
    source of truth), DATABASE_MODEL_PLAN.md, DATABASE_SCHEMA.md,
    IMPLEMENTATION_ROADMAP.md, PROJECT_SETUP.md, accounts/models.py docstring.
    Generated artifacts (FULL_DATABASE_SCHEMA.md, ATTENDANCE_SCHEMA.dbml/.svg/.html,
    schema_diagrams/*.svg, SCHEMA_DATA.json) regenerated via
    `node docs/scripts/build_schema.cjs`; stale schema_diagrams/workforce.svg deleted.
  - Regeneration reported unchanged totals: 83 models, 12 apps, 1615 columns,
    453 FKs — confirming the rename did not alter the design.
  - settings.py also gained 'django.contrib.postgres' (framework app, no tables)
    because ExclusionConstraint requires it. Domain app count remains 13.
Implemented behavior:
  - common/db.py: TstzRange expression for tstzrange(from, to, '[)') ranges.
  - employees/models.py: Employee (permanent identity, public_id UUID, optional
    user, partial-unique (company,user)); EmployeeAssignment (reusable dated
    employee_code, branch/department/designation/manager, device scope override);
    EmployeeCompensation (monthly/daily/hourly dated pay history).
  - Database-enforced history rules via PostgreSQL ExclusionConstraint + btree_gist:
    no overlapping assignments per employee, no overlapping occupancy of
    (company, employee_code), no overlapping active/ended compensation per employee.
    Cancelled rows are excluded so a void row never reserves a code.
  - CheckConstraints: period end after start (assignment + compensation),
    positive base_rate. clean(): department-in-branch, designation-in-department,
    manager is not the employee.
Models/migrations/files changed:
  employees/{models,admin,tests}.py (new app); common/db.py (new);
  config/settings.py; accounts/models.py (docstring);
  employees/migrations/0001_initial.py (generated; BtreeGistExtension() inserted
  as the first operation); docs as listed above.
Checks actually run and results:
  - manage.py check -> no issues.
  - manage.py migrate -> employees.0001_initial applied OK on PostgreSQL.
  - Verified tables exist: employees_employee, employees_employeeassignment,
    employees_employeecompensation (no workforce_* tables).
  - FULL SUITE: 27/27 tests PASS on real PostgreSQL, including employee_code
    reusable after the prior interval ends, overlapping code rejected, history not
    transferred with a reused code, overlapping assignment rejected, cancelled row
    does not reserve a code, manager!=employee, designation-in-department,
    end-after-start, successive compensation allowed, overlapping compensation
    rejected, negative base_rate rejected.
Decisions adopted and outstanding:
  - ADOPTED: app name "employees"; ExclusionConstraint + btree_gist for all
    effective-dated non-overlap rules; cancelled excluded from period reservation.
  - OUTSTANDING: tenant middleware (web request path); DB-level composite
    (id, company_id) FKs; RLS before P6; scheduling models.
Known limitations or blockers:
  - No web request path yet; tenant context is set explicitly via use_company().
  - validate_tenant_consistency() still only runs when callers use full_clean().
Exact next task:
  - Build the tenant middleware wiring CompanyMembership -> tenant context for real
    requests, then the scheduling app (Shift, DepartmentShift, WeeklyOffRule,
    Holiday, CompanyAttendanceSettings) as P1 slice 4.
```

```text
Session date: 2026-09-06 (continued)
Phase(s) worked: P1 slice 4 — tenant middleware / request path
Implemented behavior:
  - accounts/services.py: resolve_active_company_id(user, requested_company_id)
    and get_active_memberships(user). Honours a session-selected company ONLY when
    the user holds an ACTIVE membership in it; otherwise falls back to a company
    they do belong to. Non-active memberships grant nothing. Uses all_objects
    deliberately (bootstrap: resolving the company precedes having a context).
  - common/middleware.py: TenantMiddleware — thin adapter; sets request.company_id,
    sets the tenant context, and ALWAYS clears it in a finally block so a reused
    worker thread cannot leak one tenant's context into the next request.
  - config/settings.py: registered after AuthenticationMiddleware (needs request.user).
Models/migrations/files changed:
  accounts/services.py (new); common/middleware.py (new); accounts/tests.py
  (rewritten); config/settings.py. No models, so NO migration.
Checks actually run and results:
  - manage.py check -> no issues.
  - makemigrations --check --dry-run -> "No changes detected" (no drift).
  - FULL SUITE: 37/37 tests PASS on real PostgreSQL, including context set for a
    member, anonymous gets none, context cleared after response, context cleared
    when the view raises, session cannot select a non-member company, session
    honoured when a member, invited/inactive membership grants nothing, and scoped
    .objects queries working inside a real request.
Decisions adopted and outstanding:
  - ADOPTED: business rule in a service (reusable by Celery/FastAPI), middleware
    stays a thin HTTP adapter; session company id treated as untrusted input;
    context reset in finally.
  - OUTSTANDING: Celery base task applying the same context (when workers arrive);
    DB-level composite (id, company_id) FKs; RLS before P6; scheduling models;
    login/company-switch UI.
Known limitations or blockers:
  - No login views or company-switcher UI yet; middleware is exercised by tests
    via RequestFactory, not yet by a real browser session.
  - validate_tenant_consistency() still only runs when callers use full_clean().
Exact next task:
  - Build the scheduling app (Shift, DepartmentShift, EmployeeShiftAssignment,
    CompanyAttendanceSettings, WeeklyOffRule, Holiday, HolidayWorkAssignment) as
    P1 slice 5 — the last P1 domain before P2 leave.
```

```text
Session date: 2026-09-06 (continued)
Phase(s) worked: P1 slice 5 — scheduling / calendar
Implemented behavior:
  - common/db.py: added DateRange (daterange) alongside TstzRange.
  - scheduling/models.py — 7 models:
    Shift (unique (company,code); positive scheduled_minutes; half-day <= full-day;
      clean() allows a night shift to end before it starts only when
      spans_next_day, and blocks an unpaid break consuming the whole shift),
    CompanyAttendanceSettings (one row per company via UniqueConstraint on company
      — the DB equivalent of the dictionary's O2O, since Django forbids overriding
      an abstract base's field; check constraint: single-shift mode requires
      company_shift; missing_punch_policy defaults to review_required so a device
      outage is never auto-absence),
    DepartmentShift (no duplicate department/shift period; at most one default per
      department at a time — both ExclusionConstraints),
    EmployeeShiftAssignment (no overlapping effective assignment per employee),
    WeeklyOffRule (no duplicate weekday rule per scope/time; Coalesce(branch,0)
      makes NULL = company-wide a comparable scope),
    Holiday (unique ACTIVE holiday per company/scope/date, same Coalesce trick;
      cancelling frees the date),
    HolidayWorkAssignment (XOR check: exactly one of holiday / weekly_off_rule;
      one live assignment per employee/date; clean() verifies the source actually
      applies on work_date). Working a recurring weekly off references the rule
      instead of deleting it.
Models/migrations/files changed:
  scheduling/{models,admin,tests}.py; common/db.py;
  scheduling/migrations/0001_initial.py (7 models, 13 constraints).
Checks actually run and results:
  - manage.py check -> no issues.
  - manage.py migrate -> scheduling.0001_initial applied OK on PostgreSQL.
  - FULL SUITE: 56/56 tests PASS on real PostgreSQL.
Decisions adopted and outstanding:
  - ADOPTED: Coalesce sentinel for nullable scope columns inside unique/exclusion
    constraints (SQL NULL != NULL would silently break them); XOR check constraint
    for exactly-one-source; exceptions recorded as rows referencing a rule rather
    than mutating/deleting the rule; CompanyAttendanceSettings O2O expressed as a
    unique FK (documented equivalence, not a divergence).
  - SIMPLIFICATION RECORDED: HolidayWorkAssignment uniqueness is per
    (company, employee, work_date) rather than per-source — stricter than the
    dictionary, since two contradictory assignments on one date are never valid.
  - OUTSTANDING: P1 workflow services (onboarding, transfer, salary revision),
    seeded roles/permissions, two-company demo fixture, access-scope enforcement;
    Celery tenant base; DB composite (id, company_id) FKs; RLS before P6.
Known limitations or blockers:
  - P1 has MODELS but not WORKFLOWS. No onboarding/transfer service, no UI, no
    seeded demo data. P1 must NOT be marked Verified on model existence alone.
  - No login views yet; middleware proven via RequestFactory, not a browser.
Exact next task:
  - P1 slice 6: company-onboarding and employee-onboarding SERVICES (transactional,
    idempotent) — create company + default branch + attendance settings; hire an
    employee with assignment + compensation + shift; transfer and salary revision
    that close the current dated interval and open the successor. Then the
    two-company demo fixture.
```

```text
Session date: 2026-09-06 (continued)
Phase(s) worked: P1 slice 6 — service layer
Implemented behavior:
  - common/services.py: create_validated(model, **kwargs) — builds, full_clean()s,
    then saves. This is what actually activates TenantOwned.validate_tenant_
    consistency() on every service write (previously only available, not enforced).
  - tenants/services.py: onboard_company() — atomic; creates Company + default
    Branch + CompanyAttendanceSettings; idempotent on company code via
    get_or_create so a retried onboarding cannot duplicate. Settings default to
    department_shifts mode because single-shift mode requires a shift that does
    not exist yet at onboarding.
  - employees/services.py: hire_employee() (atomic: Employee + EmployeeAssignment
    + EmployeeCompensation + optional EmployeeShiftAssignment; currency defaults
    from company); transfer_employee() and revise_compensation() — both lock the
    open dated row with select_for_update(), close it (effective_to + status
    ended), then insert the successor. Unspecified attributes carry forward.
Models/migrations/files changed:
  common/services.py (new); tenants/services.py (new); employees/services.py (new);
  tenants/tests_services.py (new); employees/tests_services.py (new).
  NO models added -> NO migration.
Checks actually run and results:
  - manage.py check -> no issues; makemigrations --check -> "No changes detected".
  - FULL SUITE: 75/75 tests PASS on real PostgreSQL (19 new service tests),
    including: onboarding creates all three rows; onboarding idempotent on code;
    a failure part-way rolls back the company too; hire creates employee+
    assignment+compensation; a failed hire leaves zero partial rows; transfer
    closes the old interval and opens a new one; transfer carries forward
    unspecified attributes; backdated transfer/revision rejected; employee history
    stays distinct after the employee_code is reused by another person; a
    mid-month raise leaves both compensation periods queryable.
Decisions adopted and outstanding:
  - ADOPTED: all business writes go through services; services use
    create_validated() so full_clean() (and tenant-consistency) always runs;
    close-then-open ordering for dated history (insert-then-close would violate
    the exclusion constraint); select_for_update() on the open row.
  - RECORDED LIMIT: hire_employee is not idempotent by key; it is blocked by the
    (company, employee_code) exclusion constraint instead. An idempotency key
    belongs with the API phase.
  - OUTSTANDING: seeded roles/permissions + access-scope enforcement; two-company
    demo fixture; login/company-switch UI; Celery tenant base; DB composite
    (id, company_id) FKs; RLS before P6.
Known limitations or blockers:
  - Still no UI/login. P1 workflows exist as services and are proven by tests, not
    yet by a browser walkthrough.
  - access_control app still has no models; permission ceilings/scope not enforced.
Exact next task:
  - P1 slice 7: access_control models (AccessPermission, DesignationPermission,
    EmployeePermissionOverride) plus an effective-permission resolution service and
    the seeded two-company demo fixture, which together satisfy the remaining P1
    completion evidence.
```

```text
Session date: 2026-09-06 (continued)
Phase(s) worked: P1 slice 7 — access control / permissions
USER INSTRUCTION RECORDED: do NOT build the admin panel or any product UI without
  asking first — the user will supply the admin-panel design. Backend work
  continues meanwhile. (Also saved to assistant memory.)
Implemented behavior:
  - tenants/models.py: Feature (global catalogue, not TenantOwned) and
    CompanyFeature (dated per-company enable/disable with limits JSON; exclusion
    constraint prevents overlapping active rows of the same effect).
  - accounts/models.py: closed the previously deferred gap — CompanyMembership now
    has allowed_branches / allowed_departments M2M and last_access_at.
  - access_control/models.py: AccessPermission (global catalogue, unique code such
    as "leave.approve", action enum, is_sensitive); DesignationPermission (dated
    default/allowed/denied per designation, can_delegate, one effective rule per
    designation/permission via exclusion constraint, clean() enforces the parent
    hierarchy ceiling); EmployeePermissionOverride (dated grant/revoke, optional
    branch/department scopes, one live override per employee/permission).
  - access_control/services.py: has_permission(employee, code, at),
    is_feature_enabled(), get_current_designation(), get_effective_permissions().
    Resolution order: active permission -> company feature enabled -> employee
    override -> designation rule -> DEFAULT DENY. Explicit disable beats enable.
    All lookups dated, so historical answers are correct.
Models/migrations/files changed:
  tenants/models.py; accounts/models.py; access_control/{models,services,tests}.py;
  migrations: tenants/0002_feature_companyfeature, access_control/0001_initial,
  accounts/0003_companymembership_allowed_branches_and_more.
BUG FOUND AND FIXED (important):
  - Full suite failed on a FRESH test database with "data type bigint has no
    default operator class for access method gist". btree_gist was created in
    employees/0001, but tenants/0002 uses an ExclusionConstraint and does not
    depend on employees, so on a clean database it could run first.
  - `manage.py migrate` on the dev DB succeeded because the extension already
    existed — the bug was only visible when a schema was built from scratch.
  - Fix: moved BtreeGistExtension() into tenants/0001_initial, the earliest
    migration every tenant-owned app depends on. CreateExtension is
    "IF NOT EXISTS", so the duplicate in employees/0001 stays harmless.
Checks actually run and results:
  - manage.py check -> no issues; makemigrations --check -> "No changes detected".
  - manage.py migrate -> tenants.0002, access_control.0001, accounts.0003 applied.
  - FULL SUITE: 89/89 tests PASS on a test database built FROM SCRATCH (14 new),
    including default-deny, enabling a feature grants nobody its actions,
    designation allowed/denied/default, feature disable beating enable, a company
    without the feature denied despite an allowed rule, override grant beating a
    missing rule, override revoke beating designation-allowed, dated answers
    (denied in March, allowed in June), parent-denies-child-cannot-be-allowed, and
    the effective-permission set.
Decisions adopted and outstanding:
  - ADOPTED: default deny; feature gate is necessary but not sufficient; explicit
    disable beats enable; permission answers are dated; hierarchy ceiling enforced
    in clean() (chain traversal), uniqueness/overlap in the database.
  - OUTSTANDING: two-company demo seed command; UI (blocked pending the user's
    design); Celery tenant base; DB composite (id, company_id) FKs; RLS before P6.
Known limitations or blockers:
  - No UI yet, by user instruction — ask for the admin-panel design before building.
  - Branch/department scope fields exist on overrides/memberships but scope-level
    filtering is not yet applied inside queries (permission answers yes/no only).
Exact next task:
  - P1 slice 8: `manage.py seed_demo` management command creating two companies
    with branches, departments, designations, employees, shifts, holidays,
    permissions and a reused employee code — the reusable synthetic demonstration
    P1 requires, and the data the future UI will display.
```

```text
Session date: 2026-09-06 (continued)
Phase(s) worked: P1 slice 8 — termination service + synthetic demo seed
Implemented behavior:
  - employees/services.py: terminate_employee() — atomic; closes the open
    assignment and compensation intervals, sets employment_status and
    leaving_date. Deletes nothing; closing the assignment is what frees the
    employee_code for a later non-overlapping holder.
  - tenants/management/commands/seed_demo.py: builds the P1 synthetic
    demonstration. Northwind Textiles (DEMO-NWT): 2 branches, 3 departments,
    5 designations with a parent hierarchy, day + night shifts, Friday weekly
    off, 2 holidays, 7 employees on monthly/daily/hourly bases, one WITH a login
    and the rest without, a transfer (Nusrat -> Sales/Chittagong), a termination
    (Karim) and the REUSED code NWT-014 reissued to Sadia afterwards. Sunrise
    Logistics (DEMO-SNR): 1 employee deliberately reusing code NWT-001 to show
    codes are unique per company, not globally; payroll feature NOT enabled.
  - Safety: refuses to run when DEBUG is False unless --force; company codes are
    prefixed DEMO- and every seeded employee carries metadata["demo"]=True.
  - Idempotent: re-running skips companies that already have employees.
Models/migrations/files changed:
  employees/services.py; tenants/management/{__init__,commands/__init__}.py;
  tenants/management/commands/seed_demo.py; tenants/tests_seed.py.
  NO models added -> NO migration.
Checks actually run and results:
  - manage.py check -> no issues; makemigrations --check -> no changes.
  - manage.py seed_demo -> created both companies; re-run reported
    "already seeded - skipping" (idempotency confirmed against the dev DB).
  - Inspected: NWT-014 held by employee #6 (2023-03-01..2024-02-01) then by
    employee #7 (2024-05-01..open) — two distinct people, no overlap.
  - FULL SUITE: 101/101 tests PASS on PostgreSQL (12 new).
NOTE — the guard proved itself:
  - The suite first failed with 8 errors: "DEBUG is False. Refusing to seed
    synthetic data without --force." Django forces DEBUG=False under test, so the
    guard fired as designed. Fixed by passing --force in tests and ADDING a test
    that asserts the unforced call raises and creates nothing. The guard was not
    weakened.
Decisions adopted and outstanding:
  - ADOPTED: synthetic data is environment-guarded, prefixed and flagged;
    termination closes intervals rather than deleting; seed doubles as executable
    documentation of the identity/history rules.
  - OUTSTANDING: UI — BLOCKED PENDING THE USER'S ADMIN-PANEL DESIGN (user
    instruction, also saved to assistant memory: ask before building any UI).
    Also: Celery tenant base; DB composite (id, company_id) FKs; RLS before P6;
    branch/department scope filtering inside queries.
Known limitations or blockers:
  - P1 cannot be marked Verified: its completion evidence requires the workflow
    to be exercised through pages ("another tenant cannot read/change the
    employee through pages, IDs, exports or jobs"). Isolation is proven at the
    ORM/service layer by tests, NOT yet through a browser.
Exact next task:
  - ASK THE USER FOR THE ADMIN-PANEL DESIGN, then build the P1 UI (login,
    dashboard, company switcher, employee list/detail, hire/transfer/revise
    forms) on top of the existing services. If the user prefers to defer the UI,
    the next backend option is P2 leave models.
```

## Decisions to record during implementation

Record adopted choices and their effect in the implementation session below. Open items include the standalone target, concrete tenant database enforcement, salary proration for mid-period changes, paid-time conversion and holiday pay by rate basis, first-client leave balance rules, penalty sequence/stacking, historical policy reconstruction and eventual billing seat definition. See P0 for when each decision is needed; do not block unrelated work on a decision that belongs to a later phase.

## Session update template

Append a dated entry after each implementation session and update the checkpoint/phase tracker above:

```text
Session date:
Attendance repository/workspace:
Phase(s) worked:
Implemented behavior:
Models/migrations/files changed:
Checks actually run and results:
Manual/simulated/real-device evidence:
Decisions adopted and outstanding:
Known limitations or blockers:
Phase completion criteria still missing:
Exact next task:
```

Use Not started / In progress / Verified / Blocked / Deferred for implementation work. Waiting for the physical device is a condition on D1, not a blocker for all phases. Mark a phase Verified only after its workflow and acceptance criteria are met, not merely after its schema is added.

```text
Session date: 2026-09-06 (continued)
Phase(s) worked: P1 UI — admin panel, first pass (INCOMPLETE, corrected below)
Implemented behavior:
  - Design system from WARM_PAPER_INK_SPEC.md + design_reference/ form:
    base_template/static/base_template/css/{tokens,base,components,shell}.css.
    Zero hard-coded hex outside tokens.css (verified by grep). Contrast
    recalculated for the one colour introduced (--on-accent-soft, 9.35:1 AAA).
  - Shell: sidebar (ink active block, inline SVG icons), topbar (search, company
    switcher, user), breadcrumbs, page furniture, empty states, alerts.
  - Pages: login, dashboard, employee list (search/status filter/pagination with
    real count), branch list, department list.
  - accounts/services.py + base_template/context_processors.py power the company
    switcher; switch_company re-validates the submitted id against active
    memberships before honouring it.
  - seed_demo now also creates logins + CompanyMembership rows so the panel is
    reachable (group.admin@demo.test belongs to BOTH demo companies).
Checks actually run and results:
  - manage.py check clean; 101/101 tests still pass on PostgreSQL.
  - Browser-verified: tenant switching genuinely re-scopes (Northwind 7 employees
    / 2 branches -> Sunrise 1 / 1); reused code NWT-014 visibly held by two
    different people; transfer visible (Nusrat now Chittagong/Sales); mobile 375px
    has no page-level horizontal scroll, sidebar collapses, table scrolls in-card.
  - Computed styles match the spec (body, buttons, inputs and all states, labels,
    thead, zebra, numeric alignment, badges, pagination).
BUGS FOUND BY LOOKING, NOT BY READING MARKUP:
  - A multi-line {# ... #} comment rendered as visible text in the topbar; Django's
    {# #} is single-line only. Replaced with {% comment %}; grepped for others.
  - Table rows measured 44.5px against the spec's ~38px (body leading inflating
    cells). Cell line-height tightened; plain rows now 38.1px.
GAP FOUND BY THE USER (this is the important entry):
  - Signing in as the ROOT/platform owner yields "You are not a member of any
    company" and nothing else. Correct per DATABASE_SCHEMA.md §14 (root needs no
    membership) but a dead end: there is NO platform surface.
  - The panel is READ-ONLY. Every "Add ..." control is <button type="button"> with
    no handler. Nothing in the UI can create a company, membership, branch,
    department, designation or employee.
  - The dependency chain company -> membership -> branch -> department ->
    designation -> employee has no entry point, so from an EMPTY database the
    panel is unusable. It only demonstrated well because seed_demo supplied the
    data — a builder-seeded demo exercises the read path and skips the write path.
  - Services already exist and are tested for onboard_company, hire_employee,
    transfer_employee, revise_compensation, terminate_employee. The missing piece
    is forms + POST handlers, not domain logic.
Decisions adopted and outstanding:
  - ADOPTED (now safeguards 6-9 in TEAM_LEAD_PLAYBOOK.md): cold-start test; no
    dead controls; name the persona before building; read and write ship together.
  - REJECTED: giving the superuser a CompanyMembership to make pages work. That
    would make the root bypass the normal tenant-query path, which
    DATABASE_SCHEMA.md §14 forbids. The fix is a platform area.
Known limitations or blockers:
  - P1 CANNOT be marked Verified: its evidence requires onboarding an employee
    "through a normal workflow", which is impossible in the UI today.
  - No Select2 / DataTables / date picker / modal components yet; tables are
    server-rendered with hand-built pagination.
Exact next task:
  - P1 UI slice 2 — PLATFORM SURFACE + WRITE FLOWS, in this order:
      (a) platform area for is_superuser: company list, create company (calls
          onboard_company), grant a user a CompanyMembership, toggle features;
      (b) company setup writes: branch, department, designation create/edit;
      (c) employee writes: hire form (hire_employee), transfer, revise salary,
          terminate.
    Acceptance is the cold-start test: drop into an EMPTY database and reach a
    working employee list using only the browser, with seed_demo never run.
```

## 2026-09-10 — organisation catalogue UI (Nihal, device workstream)

Phases 0, 0.5, 1, 2 and 3 of the organisation-side brief. Ajay away; no
collision risk, but see the branch note below before merging.

### What was actually run

```
manage.py test                     347 tests, OK   (283 at Phase 0, +64 added)
manage.py check                    clean
makemigrations --check --dry-run   No changes detected
```

No schema change was needed in any phase; every table already existed.

Cold start verified end to end on an empty database, browser POSTs only,
`seed_demo` never run: root created "Software" and "Developer" in the
catalogue, a company adopted Software into Head Office with the Developer
title, and Nadia Khan was hired into Head Office / Software / Developer with
code E-1.

Screens checked at 1440, 768 and 375 px: no horizontal overflow, tables
scroll inside their own container, the two-column form grid collapses to one.

### Phase 0 — merged code and database rebuild

Rebuilt from an empty PostgreSQL database: 25 migrations, seed_demo, 283
tests green.

**Discrepancy worth correcting.** The brief said to take the merged code from
`origin/main`. `main` is still at `4eb6615` and contains **neither**
workstream — `devices/models.py` is a 3-line stub there and `CompanyDepartment`
does not exist. The merge is real but lives on
`origin/feature/company-org-setup` (`3015f13`, "Merge the device integration
and move it onto the company adoption rows"), which contains all 14 device
commits. Work continued from that commit. `main` was deliberately **not**
fast-forwarded: publishing Ajay's feature branch as trunk while he is away is
his call, not mine. It is a clean fast-forward whenever he wants it.

Also moved a personal ngrok hostname out of the shared `ALLOWED_HOSTS`
default into `.env`.

### Phase 0.5 — finishing the schema move

**One stale reference found in the whole project**, the known one:
`organization/views.py:50` counted `Count("departments")`, the reverse
accessor of the tenant-owned Department the catalogue replaced. Every request
to the branch list raised FieldError. Now counts `company_departments`.

Swept `Count(`/`Sum(`/`annotate(`/`select_related(`/`prefetch_related(`/
`order_by(`/`values(`/`values_list(` across every app's `.py`, plus
department/designation references in `.html`. Nothing else was broken. The
templates that looked wrong are correct: `CompanyDepartment` and
`CompanyDesignation` expose read-through `code`/`name` properties, so
`{{ d.name }}` resolves to the catalogue value.

**Why it survived a green suite:** `organization/` and `base_template/` had
zero view tests — nothing ever executed the query. Added
`organization/tests_views.py` (12 tests): every organisation and shell page
now gets a logged-in request asserting 200 **and** a real value read off the
response. Verified the regression test earns its place by reintroducing the
old accessor — it fails with the original FieldError and passes once reverted.

### Phase 1 — root catalogue screens

Eight screens under `/platform/catalogue/`: list, create, edit and
activate/deactivate for departments and job titles, linked from the root
sidebar. `is_superuser` only; a company administrator gets 403 on all eight,
asserted rather than assumed. No superuser is given a CompanyMembership.

Never hard-deletes: the edit and status screens list which companies use a
row so the consequence is visible, and deactivation stops new adoptions while
existing ones keep working. Uniqueness collisions are field errors, not 500s.
An already-adopted title cannot be moved to another department.

**Judgement call:** the catalogue got its own `catalogue` URL namespace
rather than reusing `organization`. Two includes of one namespace raises
`urls.W005` and broke the company sidebar's `branch_list` reverse.
`base_template`'s shell treats that namespace as a platform surface.

### Phase 2 — company adopts a department

One screen: pick a branch and a catalogue department, then the job titles
that branch uses. Adoption plus titles are written in one transaction with
the audit row, and the audit snapshot includes the title list.

Three refusals carry the rules: a title from another department (and the
multiselect never offers it), a second adoption of the same department into
one branch (reported on the department field, not page-level), and removing a
title employees hold (a readable field error, not a ProtectedError page).
Unticking deactivates; re-ticking reactivates the same row. Branch and
department are fixed once adopted.

### Phase 3 — hire an employee

Calls `employees.services.hire_employee`; no domain logic reimplemented.
Branch, department and job title are dependent Select2 fields, each narrowing
the next, with the chain re-checked server-side. Start date is stored
timezone-aware in the company's zone.

`employee_code` reuse is enforced by the exclusion constraint. Django
validates constraints inside `full_clean()`, so the failure arrives named
after the constraint; it is translated into a readable field error. A code
**is** reusable once the previous placement closes — covered by a test that
hires, ends the placement, then hires someone else on the same code.

The employee list's "Add employee (coming next)" button is now a working
"Hire employee" link, and the company sidebar's Departments entry points at
the new adoption screen instead of the old read-only page.

### Known limitations / still open

- `main` has neither workstream; needs Ajay's decision (see Phase 0).
- The old read-only `/departments/` page still exists and is no longer linked
  from the sidebar. Left in place rather than removed unilaterally.
- Transfer, revise-salary and terminate flows remain unbuilt; their services
  exist and are tested.
- Device work: the offline/backlog test on the SenseFace 2A is still not run,
  and writing users to the device is limited to what was verified on the
  hardware.

### Exact next task

Employee lifecycle writes on top of Phase 3: transfer (`transfer_employee`),
revise compensation (`revise_compensation`) and terminate
(`terminate_employee`). All three services exist and are tested; the missing
piece is forms and POST handlers, exactly as the hire flow was.
