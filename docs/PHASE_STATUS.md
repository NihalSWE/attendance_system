# Attendance implementation status

> **2026-09-15 transfer update:** Read [CLAUDE_HANDOFF_2026-09-15.md](CLAUDE_HANDOFF_2026-09-15.md) first. Ajay disputes the A15 completion report; main attendance/device table conversions remain unfinished under N9. Claude must review and report done/pending/disputed work before implementing anything. Older completion and next-step claims below are historical, not acceptance.


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
connects back. **Every row is placed in a step of the 2026-09-13 plan below**
(the "Covers" column there says which).

| Skipped | Effect on today's salary until built | Connects back into |
|---|---|---|
| **Full leave (P2):** employee requests, approval step, half-day and hourly leave, partial pay, leave policies and policy versions, balances/entitlements and the balance ledger, attachments, withdrawal, amendment (cancel exists) | Only admin-recorded, pre-approved full-day leave counts | Attendance day status → payroll deductions |
| Leave fields not built yet: `policy_version`, `original_request`/`supersedes`, `current_approval_stage` (request); `original_segment` (segment); `policy_type_rule`, `entitlement`, `supersedes` (leave day) | None until policies, balances and amendment exist | Additive migrations on the same tables |
| HR role recording leave (only owner/company admin today) | Company admin records all leave | Access work |
| Default leave types created at company onboarding | Each company adds its own types first | Onboarding |
| Employee code shown in the leave form's employee picker | Names only; two people with one name look alike | Record leave form |
| Attendance corrections (manual fixes to a day) | A missed punch stays absent / half day | Attendance record → payroll |
| Breaks and multiple IN/OUT sessions (`AttendanceSession`, `PunchAllocation`) | Only first IN and last OUT count | Worked minutes |
| ~~Late, absence and repeated-lateness penalty rules~~ — **built 2026-09-13 (A4)**; time out of the office waits for N1, a rolling window across months is not offered yet | — | Payroll deduction lines |
| Overtime review, approval and pay | Not paid | Payroll earning lines |
| Employee-level shift override, rotating shifts | Everyone works their department's shift (or the company shift) | Which shift a day is measured against |
| Holiday / weekly-off work assignments (`HolidayWorkAssignment`) | Working a holiday is not paid extra | Payroll earning lines |
| Shift fields not on the form: break minutes, paid break, grace-out, overtime-after, effective dates | Defaults (0 / unpaid) | Worked minutes, overtime |
| Attendance settings not on the form: punch pairing, duplicate-punch window, attendance windows, rounding, overtime approval | Defaults | Punch interpretation |
| Mid-month salary change (compensation segments / proration) | The rate in force at month end is used for the whole month | Payroll record |
| Joining or leaving mid-month (proration rules) | Only days on the payroll are counted | Payroll record |
| Salary structure: allowances and components | Base rate only | Payroll lines |
| ~~**Company salary settings**~~ — **built 2026-09-13 (A3)**; ~~**penalty rules**~~ — **built 2026-09-13 (A4)**; still to come on the same page: overtime method and daily/hourly rate conversion for overtime (A9) | — | Payroll lines |
| **Finalise / lock a payroll run**, approval steps, correction/reversal after finalising | Runs stay draft and are regenerated; nothing is locked | Payroll run |
| Manual bonus / deduction lines on a salary | Not available | Payroll lines (`is_manual` already exists) |
| Attendance review status and manual day corrections; the *Incomplete* (missing OUT) decision | A missing OUT is counted as present and flagged | Attendance record |
| Payslip as PDF / email; per-employee salary history | Browser print only | Payslip |
| Salary and attendance pages for HR and managers, not only the company admin | Company admin only | Access |
| Payments, part-payments, dues, advances, loans (P5) | Payslip shows the amount; paying it is not recorded | Salary management |
| Employee detail/history page and terminate screen (edit details, placement and salary **built 2026-09-12**) | Termination only through the service | Employee records used by payroll |
| Access: department heads, permissions, employee logins; branch-administrator decision | Company admin only | Leave approval |
| A proper time-picker component | Plain `HH:MM` text box | Shift form |
| ~~**Holiday year calendar**~~ — **built 2026-09-13 (A2)** | — | Holiday list |
| ~~Static file cache-busting~~ — **built 2026-09-13 (A1)** | — | Every page |

## Plan after the fast-track — 2026-09-13

**Status: split and ordered 2026-09-13; work starts 2026-09-14.** Ajay: all
residue work and his five points are built, *"this is for sure"*. Salary
(company settings **and** penalty rules — he keeps penalties in the salary
settings) comes before shifts. The attendance calendar, the in-office badge,
the holiday year calendar and file cache-busting were moved up at his
direction; pairing moves up with the calendar and badge because both read it.
Work is divided between Nihal and Ajay's session. Keep this the single plan;
do not start a parallel list.

### Ajay's five points (2026-09-13)

1. An employee can be made a user and given login credentials whenever the
   admin wants.
2. Company salary settings.
3. Attendance history in a calendar. Clicking a date shows the full history:
   check-in, check-out, every break-out and break-in time, total time,
   in-office time and out-of-office time.
4. There can be several devices; an employee's attendance on any of them is
   counted.
5. The employee list shows, with a coloured badge, whether each person is in
   the office.

Also raised: **there is no employee panel**, so no employee can request leave
today (only the company admin records leave, already approved).

All five fit the current design:

- **1** — `Employee.user` (nullable FK) and `CompanyMembership` roles
  `employee` and `manager` with `allowed_branches` already exist.
- **2** — `PayrollSettings` (§54), `PayrollPolicyVersion` (§55) and
  `AttendancePenaltyRule` (§36) are in the dictionary, not built yet.
- **3** — `AttendanceSession` / `PunchAllocation` and the attendance record's
  break and outside minutes are in the dictionary, not built yet; the calendar
  is a new screen.
- **4** — attendance already merges authorised punches from every company
  device. `device_attendance_scope` exists (company / branch / department /
  assigned devices) but has no screen, and already-excluded punches have no
  re-check.
- **5** — derived from today's scans with the same pairing as 3; no table.

### Decisions (Ajay, 2026-09-13)

- **Check-in, break-out, break-in and check-out are decided by the software by
  pairing. Final.** The device's own IN/OUT or break keys are not used. The
  rule is written in DEVICE_ATTENDANCE_POLICY.md, processing step 7. Ajay knows
  the limitations of pairing; do not raise them again.
- **Leave requests are approved by the branch manager.** A branch manager is a
  login with role `manager` and `allowed_branches` set to their branch(es) —
  no new table. The approver is the active manager of the employee's branch.
  *Confirmed by Ajay on 2026-09-15:* if the branch has
  no manager, or the person asking is that manager, the request goes to the
  company admin, so no request is stuck.
- **"User list" in point 5 is taken to mean the Employees page.** The same
  badge can go on a device's Users page if Ajay meant that.

### The plan — who builds what, in order

**Split:** Nihal owns the scan → attendance-day chain (it sits next to his
device work); from now on the `attendance` app is his. Ajay's session owns
salary, shifts, holidays, logins and leave. ⬆ marks items Ajay moved up.

#### Nihal

| # | Step | What it delivers | Point | Covers from "Skipped today" |
|---|---|---|---|---|
| N0 | **Button and calendar fix** — ✅ done 2026-09-13, on main | The 8 `btn--secondary` buttons (a class that does not exist), all on his device and adoption pages, become `btn--ghost`. The five `datetime-local` fields in `devices/forms.py` (device installed at; enrollment and device-department effective from/to) show the browser's own calendar: date on the project calendar + `HH:MM` time text. A test fails if any project form has a date field without the project calendar. Device register/edit changes stay with Nihal | — | (loose ends below) |
| N1 | **Pairing and daily totals** — ✅ done 2026-09-13, on main | `AttendanceSession` + `PunchAllocation`; every scan labelled check-in / break-out / break-in / check-out (DEVICE_ATTENDANCE_POLICY.md step 7); total, in-office and out-of-office minutes and break count per day; repeat-scan window. `worked_minutes` becomes in-office time | 3 | Breaks and multiple IN/OUT sessions; attendance settings punch pairing, duplicate window, attendance windows, rounding |
| N1b | **Day close, check-out rule, live attendance** - done 2026-09-14, branch `feature/n1b-day-close` | DEVICE_ATTENDANCE_POLICY.md step 7 "When a day closes, and the check-out": a day runs until the next shift start or 24 h; a trailing OUT stays a break-out until the day closes; a day ending on an IN is checked out at the shift end with `review_status = needs_review`; arriving early is not paid (worked time from the shift start); time after the end is `calculated_overtime_minutes`, an open overtime session is blank, in review and unpaid; attendance recalculates itself on punches and on read, no Calculate button; `attendance.services.recalculate()` for Ajay's apps | 3 | Attendance settings attendance windows; the Incomplete decision |
| N2 | **Attendance calendar** ⬆ — ✅ done 2026-09-13, on main | **A planner-style month view, not the small date-picker grid** (Ajay, 2026-09-13): one big square per day holding a brief summary — status, check-in → check-out, in-office time, breaks — with a month summary above. Clicking a date opens that day's history: every check-in, break-out, break-in and check-out with time and device, plus total, in-office and out-of-office time. On a phone the month becomes a day-by-day list with the same summary. Built as a reusable piece so the employee panel (A7) shows the same calendar for "my attendance" | 3 | (new) |
| N3 | **In-office badge on the Employees page** - done 2026-09-14, branch `feature/n3-in-office-badge` | "Now" column: In office (green), On break (amber), Left (grey), Not in yet (grey), Absent (red, after shift start plus grace), On leave (blue), Off today (grey); refreshes every minute | 5 | (new) |
| N4 | **Which devices count + Re-check punches** - done 2026-09-14, branch `feature/n4-device-scope` | Attendance settings: all company devices / branch devices / department devices / assigned devices. "Re-check punches" for a date range re-runs authorisation on excluded punches, audited, then attendance is recalculated | 4 | (new) |
| N5 | **Attendance corrections** - done 2026-09-14, branch `feature/n5-corrections` | Fix a day (add a missed scan or change status) with reason and audit; review list for days checked out by rule and open overtime sessions (N1b). Approving overtime (set the end time, approved minutes) and its pay are A9's; the list links to A9's approve action (agreed 2026-09-14) | — | Attendance corrections; attendance review status and the Incomplete decision |
| N6 | **Employee detail page + terminate screen** - done 2026-09-14, branch `feature/n6-employee-detail` | Employee history (placement, salary, devices) and ending employment (`terminate_employee` exists) | — | Employee detail/history page and terminate screen |
| N7 | **Payslip redesign** - done 2026-09-14, branch `feature/n7-payslip`, merged with the employee's view (A7) (Ajay, 2026-09-14: "the worst UI … not organized … amounts messy … no padding") | Redesign `payroll/templates/payroll/payslip.html` only: a clear header (employee, period, pay basis, rules), earnings and deductions as separate, padded sections with right-aligned amounts, a totals block where net pay stands out, then attendance counts and penalties (Waive stays). Every amount through `{% load money %}{{ value\|money }}`. Template and CSS only — no change to payroll calculation or views; Ajay's session owns payroll/. Also (A7): the employee opens the same template with `for_employee=True` — breadcrumbs then lead to My payslips, never company pages — and the "Draft" label must come from the run's status (a finalised month says Finalised) | — | (new) |
| N8 | **Device connection check** - done 2026-09-15 incl. part 4, branch `feature/n8-device-connection` (Ajay, 2026-09-14: "if the device is connected to server after changing the device then there should be an alert or ping test or something to check if the device has connected") | A device cannot be pinged — it calls the server, not the other way round — so the check is its next check-in. (1) A live **Connected / Last seen … ago / Not connected** badge on the device list and detail, worked out from `last_seen_at` and the device's poll interval, refreshing itself. (2) After **Register** or **Edit** (and after the terminal's server address is typed on the device), a **Test connection** panel that waits for the next check-in (optionally queues a harmless command and waits for its answer) and says "Connected at 14:32" or, after a couple of minutes, what to check (the server address to type on the terminal, serial number, network). (3) An alert on the device list and the dashboard when an active device stops checking in. (4) **Change server address on a device that has never checked in** is refused with what to do instead — "This device has never connected to this server. Set the server address on the terminal itself first (COMM → Cloud Server)" — rather than queuing a command nobody will collect. Ajay hit this 2026-09-14 with the new SenseFace 3A: registered, never checked in, the change sat as "in progress" and then ended as "lost" ("device not reachable at the new address"), which blamed the address when the device had simply never been connected. Builds on the existing server-address status panel | — | (new) |
| N9 | **Data tables on Nihal's pages** (see A15) - done 2026-09-15, branch `feature/n9-server-side-tables` | Attendance list and every device list (devices, enrollments, punches, messages, unresolved, device users) on A15's shared server-side DataTables helper, with their filters | — | (new) |
| N10 | **Branch access on Nihal's pages** (A12 part 7, [A12_BRANCH_ACCESS_FOR_NIHAL.md](A12_BRANCH_ACCESS_FOR_NIHAL.md)) - done 2026-09-15, branch `feature/n10-branch-attendance` | Codes `attendance.view` and `attendance.fix` (HR company-wide); Daily list, Calendar, day panel, Now, Days to review, Fix a day, Withdraw, the employee page and End employment limited to the viewer's branches, checked in the services too, and listed in `BRANCH_PAGES`. Devices unchanged | — | (new) |
| N11 | **Missed scans and attendance alerts** - done 2026-09-15, branch `feature/n11-scan-requests` (built on N10) | (1) Employees report a missed scan from My attendance; whoever may fix the day's branch approves it, which adds the scan as a normal correction. (2) "Still in 2 hours after the shift" alert on Days to review and the dashboard. (3) A finished day that checked out 2 hours or more before the shift end, or was outside more than the break plus an hour during the shift, goes to Days to review | — | attendance.0005 |

#### Ajay's session

| # | Step | What it delivers | Point | Covers from "Skipped today" |
|---|---|---|---|---|
| A1 | **File cache-busting** ⬆ — ✅ done 2026-09-13 | After a CSS/JS change the browser loads the new file without Ctrl+F5 | — | Static file cache-busting |
| A2 | **Holiday year calendar** ⬆ — ✅ done 2026-09-13 | A full-year calendar to pick many holiday dates at once, with month and year navigation | — | Holiday year calendar |
| A3 | **Company salary settings** — ✅ done 2026-09-13 | Salary settings page with dated versions (`PayrollSettings`, `PayrollPolicyVersion`): monthly divisor (30 / days in month / working days), daily and hourly rate method, weekly off / holiday pay by pay type, half-day and Incomplete treatment, currency. Payroll reads them instead of constants; each run records the version used; every company starts on today's rules | 2 | Company salary settings |
| A4 | **Penalty rules** (on the salary settings page) — ✅ done 2026-09-13 | `AttendancePenaltyRule`: late (per minute, or N late days = one day's pay), absence, repeated lateness; deduction lines on the payslip | 2 | Penalty rules |
| A9 | **Overtime** — ✅ done 2026-09-14 (moved up: right after Nihal's N1b) | Review and approval, including an open overtime session with a blank check-out (the approver sets the time); overtime paid at × the hourly rate on the salary settings page (default 2×), a separate multiplier for holiday / weekly-off work, minimum overtime minutes and rounding; monthly staff's hourly rate = monthly ÷ days ÷ shift hours | — | Overtime review, approval and pay; `HolidayWorkAssignment`; attendance settings overtime approval |
| A5 | **Shifts** — ✅ done 2026-09-14 (rotating shifts deferred by Ajay: "initially I want to keep it simple") | Employee-level shift override (wins over the department shift), rotating shifts; shift form fields break minutes, paid break, grace-out, overtime-after, effective dates; a proper time picker | — | Employee override, rotating shifts; shift fields not on the form; time picker |
| A6 | **Logins** — ✅ done 2026-09-14 | "Give login" on the Edit employee page: admin types email and password, picks Employee or Branch manager (with branches); disable / enable; reset password | 1 | Access: employee logins; branch-administrator decision (= branch manager) |
| A7 | **Employee panel** — ✅ done 2026-09-14 | Own sidebar: My attendance (Nihal's N2 calendar), My leave, My payslips, My profile | 1 | (new) |
| A8 | **Done 2026-09-15 — leave requests, branch-manager approval**, branch `feature/a8-leave-requests` | Employee requests leave → Pending; branch manager approves or rejects from an inbox; approval creates the same `LeaveDay` rows as today, so attendance and salary need no change. Branch manager panel: leave inbox, branch attendance, in-office badges | 1 | Full leave: employee requests, approval step; salary and attendance pages for managers (branch manager part) |
| A10 | **Full leave** | Half-day and hourly leave, partial pay, policies and versions, balances / entitlements / ledger, attachments, withdraw, amend; default leave types at onboarding; HR records leave; employee code in the picker | — | Full leave (rest); leave fields not built; HR role recording leave; default leave types; employee code in picker |
| A11 | **Salary completeness** | Mid-month salary change and joining / leaving (segments, proration); allowances and components; manual bonus / deduction lines; finalise / lock with approval; corrections after finalising; payslip PDF and email; salary history | — | Mid-month change; joining / leaving; salary structure; finalise / lock; manual lines; payslip PDF / history |
| A12 | **Access** | Department heads, permissions, HR and payroll-manager pages | — | Department heads, permissions; salary and attendance pages for HR |
| A13 | **Later** | Payments, advances, loans (P5) | — | Payments (P5) |
| A14 | **Sidebar menus with submenus — done 2026-09-15**, branch `feature/a14-sidebar-menus` | Every important page reachable from the sidebar: a menu per area with its pages as submenus, e.g. Employees (All employees, Create employee); Attendance (Daily list, Calendar); Leave (Leave list, Record leave, Leave types); Salary (Salary by month, Salary settings, Penalty rules); Shifts (Overview, Shifts, Department shifts, Weekly offs, Holidays, Holiday calendar, Attendance settings); Organisation (Branches, Departments); Devices (Devices, Enrollments, Punches, Messages, Unresolved). Ajay: pages he could not find were only reachable through buttons inside other pages. Every later step adds its pages to these menus | — | (new) |
| A6b | **First salary for an employee without one** (bug, Ajay 2026-09-14) — ✅ done 2026-09-14, branch `feature/a6b-first-salary` | Employees created from a device's users (`devices/services/user_sync.py`) get a placement but no salary row, and Edit employee → Salary refuses: "This employee has no current salary to change" — so they can never be given one. Fix: when there is no salary, the Salary card **sets the first one** (button "Set salary", from the placement's start date by default) instead of refusing; the salary page keeps listing such people under "Skipped, no salary set" until then | — | (bug) |
| A15 | **Server-side tables — done 2026-09-15**, branch `feature/a15-server-side-tables` | Shared queryset/JSON helper and one initializer, retaining the current Paper/Ink table styles. Numbered pages, page-size choice, direct page jump, result counts, database search/order and an HTML fallback. Converted Salary month, Overtime (Employee + Branch filters), Leave, Leave types, Holidays, Penalty rules, Employees, Branches, Departments, My leave, My payslips, Approval inbox and Branch attendance. Nihal’s device/attendance lists remain N9. See SERVER_SIDE_TABLES.md. | — | (new) |
| A5c | **Calendar and salary-settings fixes** — done 2026-09-14, branch `feature/a5c-calendar-fixes` | Weekly-off conflicts compare date ranges, including stopped history. **Change start date** on Shifts → Weekly off days corrects an active or stopped rule and rebuilds changed attendance dates while preserving finalised months. Paid holiday / weekly-off fields are removed; writes force True and the calendar treats legacy flags as paid. Daily/hourly pay remains on Salary settings. The day-value label explains absence deductions and the overtime hourly-rate base. See progress below. | — | (new) |
| A16 | **SenseFace 3A** (Ajay, 2026-09-14: "add this to my task") | **Catalogue row — ✅ done 2026-09-14** (`devices/migrations/0004_seed_senseface_3a.py`, branch `feature/senseface-3a`): ZKTeco, `senseface-3a`, ADMS push, same adapter as the 2A; capabilities set conservatively (push, face) until confirmed; appears under Register device after `migrate`. **Connected 2026-09-14 19:02 (Dhaka)** in company Ajay ("Main Entrance", serial VGU6262600120), through Ajay's ngrok tunnel: set on the terminal (COMM → Cloud Server: domain only, port 443, HTTPS on) — the software's Change server address only moves a device that is already connected. It reports **pushver 2.4.1** (the 2A: 3.0.4S) and `DeviceType=att`. It was refused with 401 at first because registration had invented a communication key the device cannot send — fixed on main (5ed6d10: no key unless typed; Edit device → "Remove the communication key"). **Data retrieval ✅ 2026-09-15 (office, on the real 3A — see "A16: SenseFace 3A data retrieval" below):** firmware ZAM70-NF28VA-3.3.12, PushVersion 3.1.2S; scans arrive (ATTLOG); Device users lists its users and fingerprints (2.x `USER PIN=` / `BIODATA` lines); "Refresh user list" = `DATA QUERY USERINFO`; attendance history = `DATA QUERY ATTLOG`, asked for automatically after a gap. **Still to do:** mapping users to employees (next, automated from the employee list — Ajay); user writes (add/remove on the device) and the server-address change, measured on this protocol first; widen the catalogue capabilities (card, fingerprint) | 4 | (new) |

#### Ajay's session — progress

**A1 — file cache-busting (done 2026-09-13, branch `feature/a1-cache-busting`).**
`STORAGES["staticfiles"]` is `base_template.staticfiles.VersionedStaticFilesStorage`,
a subclass of Django's `ManifestStaticFilesStorage`. Deployed (`DEBUG=False`
after `collectstatic`) every file gets a content-hashed name
(`shell.1a2b3c4d5e6f.css`); in development and tests nothing is collected, so
the link gets `?v=` and a hash of the source file, recomputed only when the
file's modification time changes. A file missing from the manifest falls back
to the versioned link instead of raising. `STATIC_ROOT = BASE_DIR /
"staticfiles"` (already git-ignored). No template changed. 10 tests
(`base_template/tests_staticfiles.py`). Checked on the running dev server:
every stylesheet link carries `?v=` and the versioned URL is served. **No new
.env variables.**

**A2 — holiday year calendar (done 2026-09-13, branch
`feature/a2-holiday-calendar`, stacked on A1).** New page
`scheduling:holiday_year` at `/shifts/holidays/calendar/`, linked from the
holiday list ("Holiday calendar", primary) and the Shifts overview.

- Twelve month grids (Monday first, like the date picker); each day is a real,
  visually hidden checkbox. Click to select; Shift-click selects a run of
  days; arrow keys move between days with one Tab stop.
- Each selected date is a row with its own name; a date right after a named
  selected date takes its name.
- Existing holidays are shaded and listed under their month; a company-wide
  holiday cannot be selected again. Company weekly offs are shaded.
- Changing year keeps the selection: with dates selected the year arrows post
  the form back (`go_year`) and the other year re-renders with the rows kept.
- "Applies to" (branch or all) and "Paid holidays" apply to the whole batch.
- `services.add_holidays(actor, company_id, values={"days": [(date, name)],
  "branch", "is_paid"})`: owner / company admin only, all or nothing, every
  clash with the same scope named, one audit row per holiday
  (`holiday.created`, `"from": "year_calendar"`).
- Form: `HolidayYearForm` + `posted_holiday_rows` in `scheduling/forms.py`.
  Files: `holiday_year.html`, `scheduling/static/scheduling/css/holiday_year.css`,
  `.../js/holiday_year.js`.
- 16 tests (`scheduling/tests_holiday_year.py`). Checked in a browser at 1440
  (three month columns, sticky panel), 768 (two columns, panel above) and 375
  (one column, 38px days, long selections scroll inside the panel); no
  horizontal overflow, no console errors.
- **After pulling:** restart the dev server once — the scheduling app has its
  first `static/` folder, which a running server does not see.

**A3 — company salary settings (done 2026-09-13, branch
`feature/a3-salary-settings`, stacked on A2).** New page
`payroll:salary_settings` at `/salary/settings/` (owner / company admin),
linked from the Salary page; the sidebar keeps Payroll active.

- **Models** (migration `payroll/0002_salary_settings`, additive only; the
  user runs `migrate`): `PayrollPolicyVersion` with every §55 field
  (`payroll_policy_version`) and `PayrollSettings` §54 (`payroll_settings`),
  plus `PayrollRun.policy_version`.
- **Deviations from the dictionary, on purpose:** `PayrollRun.policy_version`
  is nullable — null means the standard rules, used before a company saves
  its own; `PayrollSettings.default_salary_structure` is not added until the
  salary structure tables exist (A11).
- **Versions:** each starts on the 1st of a month; a month uses the version in
  force on its first day. Saving closes the version in force the day before,
  replaces (status `retired`, shown "Replaced") one that starts the same
  month, and is refused if a change is already saved for a later month.
  Active versions cannot overlap (exclusion constraint
  `excl_payroll_policy_active_overlap`). Audit: `payroll.rules_changed`,
  `payroll.settings_updated`.
- **What the calculation reads** (`payroll/policy.py` `SalaryRules`,
  `payroll/services.py` `calculate_pay`): one day of a monthly salary (÷ fixed
  days, ÷ days in the month, ÷ working days); absence by day, by minutes short
  of the shift (shift length less an unpaid break), or only through penalty
  rules; half-day pay %; a day without a check-out (full / half / unpaid);
  paid holidays and weekly offs for daily and hourly staff; net rounding with
  a visible Rounding line; salary below zero allowed or not. Stored in the
  version's fields and `calculation_config` (validated keys only).
- **Standard rules** = the 2026-09-12 formula, with one correction: an hourly
  employee's day without a check-out is paid the shift's hours (it paid 0),
  the same as monthly and daily staff.
- Stored but not on the page until their step: overtime fields (A9),
  penalty stacking and deduction caps (A4), recovery cap (A13), daily/hourly
  rate conversion (A9), joining/leaving proration (A11), leave treatments
  (A10).
- A run records `policy_version` and the rules used (`totals_snapshot["rules"]`,
  each record's `calculation_snapshot["rules"]`); a payslip names them
  ("Standard rules" / "Company rules, version N").
- 27 new tests (`payroll/tests_settings.py`, and an end-to-end test in
  `payroll/tests_basic.py`). Checked in a browser at 1440, 768 and 375 px.
  **No new .env variables.** After pulling: `python manage.py migrate`.

**A4 — penalty rules (done 2026-09-13, branch `feature/a4-penalty-rules`,
stacked on A3; built in a separate worktree, `D:\attendance_device_a4`, so
Ajay's running server was not touched while he checked A3).**

- **Models** (migration `payroll/0003_penalty_rules`, additive only):
  `AttendancePenaltyRule` §36 (`payroll_attendance_penalty_rule`),
  `PenaltyAssessment` §37 (`payroll_penalty_assessment`),
  `PenaltyAssessmentAttendance` §38 (`payroll_penalty_assessment_attendance`),
  and `PayrollLine.source_type` + `PayrollLine.penalty_assessment` (§65).
- **Deviation, on purpose:** the dictionary lists §36–38 with the attendance
  models; they are in the **payroll app** (same table names) because they are
  salary settings and the attendance app is Nihal's — two sides adding
  migrations to one app would collide.
- **Second deviation (2026-09-14, `payroll/0004`):**
  `PenaltyAssessmentAttendance.attendance_record` is `CASCADE`, not PROTECT.
  Attendance recalculates itself and deletes a day that no longer applies; a
  PROTECT link from a draft penalty would make that fail. Draft penalties are
  rebuilt at the next salary generation, and a finalised month's attendance
  never recalculates, so no evidence of a posted penalty can be lost.
- **Versioned like the salary rules** (`payroll/penalties.py`): `code` names a
  rule across versions; change = new version from the 1st of a month (the one
  in force closes, one starting the same month is replaced, a later saved
  change refuses an earlier one); stop = no longer applies from a month.
  Audit: `penalty_rule.created/changed/stopped`, `penalty.waived`.
- **What a rule measures:** arriving late (`late_minutes`), leaving early
  (scheduled end − last OUT), working less than the shift (expected − worked),
  an absent day. `outside_minutes` is stored but not offered until N1's
  pairing. Operators: at least / more than / at most / less than / exactly.
- **How often:** every day it happens; every N qualifying days in the month
  (7 late days with N=3 → 2 penalties); N working days in a row (weekly offs
  and holidays skipped; leave, absence or a non-qualifying day breaks the run;
  `sequence_break_policy` holds this, defaults only). Runs and counts stay
  inside the month; `rolling_window` is stored but not offered.
- **What it deducts:** the minutes themselves (for an absent day, the day's
  shift minutes), a fixed number of minutes, part of a day, a full day, or a
  fixed amount. A day's value is the monthly per-day value from the salary
  rules (falls back to ÷ the fixed days when absence is not prorated), the
  daily rate, or the hourly rate × shift hours; a minute's value is a day ÷
  the shift's expected minutes (hourly: rate ÷ 60).
- **Stacking:** rules in the same group don't add up on one day — the larger
  single-day deduction counts; otherwise rules add up. A rule's "at most per
  month" and the salary setting "penalties can take at most X% of pay"
  (`maximum_period_deduction_percent`, now on the salary settings form) scale
  amounts down proportionally; the assessment records `capped`.
- **In salary generation:** one `PENALTY` deduction line per penalty
  (description = rule name + dates), linked to its `PenaltyAssessment`
  (status `proposed`) and the attendance days behind it. Regenerating a draft
  recalculates proposed penalties; **waived ones are kept and not charged
  again** (matched by `occurrence_identity` = employee + rule code/version +
  first/last date). The run's snapshot records penalty count, amount and the
  rule versions used.
- **Waive:** a draft payslip lists its penalties with days, minutes and a
  Waive button; waiving regenerates that month's draft. Undoing a waiver is
  not built.
- An absence rule deducts **on top of** the absence setting (the help text
  says so), per LEAVE_AND_SALARY_MANAGEMENT.md §6.
- 23 new tests (`payroll/tests_penalties.py`, and an end-to-end test in
  `payroll/tests_basic.py`). Checked in a browser at 1440, 768 and 375 px on
  a payslip generated from a demo month. **No new .env variables.** After
  pulling: `python manage.py migrate`.

**A5 — shifts (done 2026-09-14, branch `feature/a5-shifts`, built in the
worktree `D:\\attendance_device_a5`).** No migration: every field already
existed.

- **Shift form:** break minutes, paid break, leaving-early grace
  (`grace_out_minutes`), "overtime starts after" (`overtime_after_minutes`).
  Blank = 0. Grace in/out must be shorter than the shift.
- **An employee's own shift** (`EmployeeShiftAssignment`, dictionary §17), on
  the Edit employee page (Shift card): shows what the employee works today and
  where it comes from; gives them their own shift from a day and optionally
  until a day; lists their own shifts; ends the current one. No last day =
  `employee_override`; with one = `temporary`, and afterwards they are back on
  what they had (a temporary shift inside an override hands back to it). One
  at a time, like department shifts: a new one closes the one in force, one
  starting the same day replaces it (`cancelled`), a later saved change
  refuses an earlier one. Starts/ends at the company's midnight. Audit:
  `employee_shift.set`, `employee_shift.ended`.
- **`WorkCalendar.shift_for(department_id, on, employee_id=None)`**: the
  employee's own shift wins. Optional so existing callers keep working;
  leave passes it. **Nihal's attendance calculation and demo seeder must pass
  `employee_id`** (in his N1b), or an override is ignored there.
- **Time picker** for every `HH:MM` box (see UI_AND_ONBOARDING_CONVENTIONS.md).
- Penalties: "leaving early" subtracts the shift's grace-out.
- Shift `effective_from/to` stay off the form: a shift is retired with its
  status, and dated assignment lives on department and employee shifts.
- **Rotating shifts: deferred (Ajay, 2026-09-14: "initially I want to keep it simple").** The
  roadmap lists "rotating rosters, arbitrary split shifts" as later and the
  schema has no table for a repeating pattern. A rotation can be entered
  today as a series of temporary shifts. A repeating pattern (e.g. week A
  day, week B night) needs one new table and one step in the shift lookup
  (own shift → rotation → department → company); nothing built now changes.
- 19 new tests (`scheduling/tests_employee_shifts.py`). Browser-checked at
  1440, 768 and 375 px.

**A6 — logins (done 2026-09-14, branch `feature/a6-logins`, built in the
worktree `D:\\attendance_device_a5`).** No migration: `User`,
`CompanyMembership` (roles `employee`, `manager`, `allowed_branches`) and
`Employee.user` already existed.

- **Where:** Employees → **Edit** on a row → **Login** card (second card).
  No login: email, password (twice), Access (Employee / Branch manager) and,
  for a branch manager, the branches they manage → **Create login**. With a
  login: who signs in and their access; **Save access** (change the role or
  branches); **Set new password**; **Disable login** / **Enable login**.
- **Services** (`organization/employee_login.py`): `give_login`,
  `change_login_role`, `reset_login_password`, `set_login_active`. Owner /
  company admin only; audited (`employee.login_created`, `…_role_changed`,
  `…_password_reset`, `…_disabled/enabled`), never with the password.
- **Safety:** a login is always a new account — an email that already has a
  login is refused, so nobody can attach someone else's account; a password is
  only reset for an account that belongs to this company alone (never a
  person who is also a member elsewhere, never a superuser or staff);
  disabling suspends the membership in this company, not the account; the
  company administrator's own login is not changed from an employee record.
  Passwords go through Django's password validators.
- **The gate** (`common.middleware.SelfServiceGate`, after TenantMiddleware):
  an Employee or Branch manager login may only open the `me` pages and sign
  in/out. Any other page redirects to My account (GET) or is refused (form
  post). Needed because Salary, Attendance, Leave, Shifts, the holiday list,
  Branches and Departments only checked "is a member" — fine while only
  administrators could sign in, a salary leak once employees can. One gate
  closes every current and future company page to them by default; A7/A8 open
  their own pages by adding them to the `me` (or a branch-manager) namespace.
- **Their pages** (`base_template/me_views.py`, `/me/`): **My account**
  (name, company, employee code, designation, department, branch, shift
  today, how they sign in and their access) and **Change password** (Django's
  own form, they stay signed in). A small sidebar: My account, Change
  password, Sign out. My attendance / leave / payslips come with A7.
- A **branch manager** is kept on the same pages until A8 gives them the
  leave inbox and branch views.
- 21 new tests (`organization/tests_logins.py`): the services, the safety
  rules, the gate on seven company pages and a form post, a real sign-in,
  changing their own password, a disabled login, and the Login card.
  **No new .env variables.**

**UI fixes from Ajay's review (2026-09-14, branch
`feature/ui-dependent-fields-amounts`).**

- **Dependent fields** (`dependent.js`, see UI conventions): Salary settings
  shows "Fixed number of days" only for "a fixed number of days". Penalty
  rule: "By" and "Minutes" only for minute measures (not an absent day);
  "How many days" only for "every so many days" / "in a row"; "Amount" only
  for fixed minutes, part of a day or a fixed amount, relabelled "Minutes to
  deduct" / "Days of pay" / "Amount to deduct". Login: "Branches they manage"
  only for a branch manager.
- `[hidden]` now always hides (base.css); it was only in platform.css, so a
  hidden `.field` stayed on screen on pages without it.
- **Thousands separators** on every amount shown: the `money` template
  filter (`base_template/templatetags/money.py`) on the salary page, the
  Employees list, the Edit employee salary badge and the penalty rule
  descriptions. The payslip gets it in Nihal's N7 redesign.
- 6 tests (`base_template/tests_display.py`).

**Shift form fixes from Ajay's review (2026-09-14, on main after the N1b/N3
merge).**

- **No "Ends on the next day (night shift)" box.** The times already say it:
  an end earlier than the start (22:00 → 06:00) ends the next day. The form and
  `_apply_shift_values` derive `spans_next_day` from the times (a value passed
  in is ignored), so editing a night shift into a day shift clears it. Same
  start and end is refused. The column stays; N1b's day window reads it.
- **"The break is paid" shows only when Break (minutes) is above 0**
  (`data-show-when="default_break_minutes:>0"`; `dependent.js` now takes a
  `>N` number rule and reacts while typing). A tick left behind with no break
  is saved as unpaid.
- 5 tests in `scheduling/tests_screens.py` (one old test replaced).

**A9 — overtime (done 2026-09-14, branch `feature/a9-overtime`, worktree
`D:\\attendance_device_a5`). Migration `payroll/0005_overtime`.**

- **Approved automatically when scanned out (Ajay, 2026-09-14: "Automatic").**
  Overtime that ends in a real scan (stayed late and scanned out; worked a
  day off and scanned out) is approved by itself and salary pays it. Only a
  day nobody scanned out of **waits for you**: came back after the shift and
  never scanned out, or a check-out set by rule. Admin/HR can still change
  any day — approve fewer minutes or reject — and undo that (back to
  automatic, or waiting). The rule is `payroll.overtime.approved_minutes`,
  which attendance calls each time it writes a day.
- **Where:** sidebar → **Overtime** (also Salary → **Overtime**, "(N
  waiting)" when a day waits). A month's list: date, employee, shift end,
  what attendance counted, the state (Approved automatically / Waiting for
  you / Approved / Rejected / Too short to pay) and the minutes approved and
  paid; Show filters by state. **Decide** (waiting) or **Change** opens the
  day: shift, check-in/out, when overtime starts, every scan with its device,
  and **Approve** / **Reject** with an optional note. A decided day has
  **Undo the decision**.
- **What counts** (attendance, N1b): time after the shift's end, delayed by
  the shift's "overtime after" minutes. **Came back after the shift and never
  scanned out:** the approver types the time they left (time picker; a time
  earlier than the return is the next morning) and the minutes follow from
  it — the day's review flag clears. **Work on a holiday or weekly off:**
  every minute in the office counts, paid at the day-off rate. Changing a
  day approves all of it or fewer minutes, never more than was counted.
- **Who:** owner, company administrator or **HR** ("the admin or HR"),
  within their branches. A month whose salary is finalised is closed.
- **Kept through recalculation:** decisions live in `OvertimeDecision`
  (payroll app, one per employee-day). Attendance rewrites its days live, so
  `attendance.services.recalculate` reads the decisions back and puts the
  approved minutes (a decision's, or the automatic ones) on
  `AttendanceRecord.approved_overtime_minutes`, and marks an approved open
  session reviewed — the only change in the attendance app.
  A scan arriving after a decision shows "The day changed after the decision".
- **Pay** (`payroll/services.py`): each day's approved minutes, after the
  minimum and rounding, × the hourly rate × the multiplier. Hourly rate:
  hourly staff their own; daily staff the daily rate ÷ the shift's paid
  hours; monthly staff one day's pay (as the absence deduction works it out)
  ÷ the shift's paid hours. Two payslip lines, "Overtime (N days, 2× the
  hourly rate)" and "Work on holidays / weekly offs (…)"; the per-day working
  is kept in the payslip's calculation snapshot.
- **Salary settings → Overtime:** "Overtime pays (× the hourly rate)",
  default **2**; "Work on a day off pays", default **2**; "Don't pay
  overtime shorter than (minutes)", default 0; "Pay overtime in blocks of"
  every minute / 15 / 30 / 60 minutes (leftover minutes after the last full
  block are not paid). Dated like every other salary rule. The migration gives
  already-saved rules these defaults (the columns were placeholders nothing
  read: no overtime pay, 1×).
- **Too short to pay (fix after Ajay's review, 2026-09-14):** a day whose
  counted overtime the month's rules would pay nothing for (under "Don't pay
  overtime shorter than", or less than one block) is shown as "Too short to
  pay" and does not wait for a decision. Felan Tech had 26 waiting days of
  1–59 minutes (people scanning out a few minutes after 18:00) under a
  60-minute minimum; now 0. An open session always waits. The two settings
  were renamed to say what they do: "Don't pay overtime shorter than
  (minutes)" and "Pay overtime in blocks of" (every minute / 15 / 30 / 60).
- **Salary page notices:** days still waiting ("will not be paid until
  approved") and decisions made after the draft was generated ("Generate
  again to include it"). Approving does not regenerate salary by itself.
- 30 tests (`payroll/tests_overtime.py`); two salary settings tests post the
  new fields; two end-to-end salary tests now include the demo month's
  automatic overtime. **No new .env variables.**

**A6b — first salary for an employee without one (done 2026-09-14, branch
`feature/a6b-first-salary`). No migration.**

- **The bug:** "Create employees from device users" (Nihal's device user
  sync) makes an employee and a placement but no salary, and Edit employee →
  Salary refused: "This employee has no current salary to change" — so they
  could never be given one, and every salary run skipped them.
- **Now:** the Salary card says **No salary yet**, explains that such an
  employee is left out of salary, and its button is **Set salary**. The date
  starts, by default, when they were first placed, so no worked day is
  missed; a date before the placement is refused. Saving writes the first
  salary row ("First salary"), audited as `employee.salary_set`. An employee
  whose salary has *ended* (employment ended) is not restarted from here.
- Tests: 4 in `organization/tests_employee_edit.py`. Ajay agreed this small,
  contained change needs only the affected apps' tests (organization,
  employees, payroll: 278 OK), not the full suite. **No new .env variables.**

**A7 — employee panel (done 2026-09-14, branch `feature/a7-employee-panel`,
worktree `D:\\attendance_device_a5`). No migration.**

- **Where:** sign in with an employee's login (Employees → Edit → Login card
  gives one). The sidebar is the employee's own: **My account**, **My
  attendance**, **My leave**, **My payslips**, **Change password**, Sign out.
  The top bar says "My panel" instead of the company-wide employee search.
- **My account** (`/me/`): a **Today** card — the same status as the
  Employees page's "Now" badge (N3: In office / On break / Left / Not in yet
  / Absent / On leave / Off today, with the time) — this month so far
  (present, late, absent, on leave, time in the office) and buttons to the
  other pages; then Work and Login as before.
- **My attendance** (`/me/attendance/`): Nihal's planner calendar (N2),
  unchanged, for the signed-in employee only; month/year, Prev/Next; clicking
  a day opens the same side panel with every scan, from `me:attendance_day`.
  The calendar include takes an optional `day_url_template` for this — its
  one change.
- **My leave** (`/me/leave/`): days taken in the year by leave type and pay,
  and every application (dates, type, days, pay, status, reason) with a year
  filter. Asking for leave from here is A8.
- **My payslips** (`/me/payslips/`): **finalised months only** — a draft can
  still change, so the company checks it before anyone reads it as their
  salary. Finalising a month is not built yet (A11), so the list says "No
  payslips yet" until then. Opening one shows the same payslip page as the
  company's (`payroll.views.payslip_context(record, for_employee=True)`: no
  Waive). N7 is asked to make the payslip's breadcrumbs and "Draft" label
  follow `for_employee` and the run's status.
- **Safety:** every page reads the employee linked to the login — never an
  id from the URL — so nobody can open another person's day, leave or
  payslip; another person's payslip or a draft is a 404. A login without an
  employee record (a company administrator) is told these pages are an
  employee's own. The gate (A6) still keeps these logins off company pages.
- 8 tests (`base_template/tests_employee_panel.py`). **No new .env
  variables.**

#### Keeping the two sides apart

- **The contract is `AttendanceRecord`.** Payroll reads `attendance_status`,
  `payable_fraction`, `worked_minutes`, `late_minutes` and `leave_day`. Their
  meaning stays; after N1, `worked_minutes` is in-office time (plus paid
  break). Nihal does not edit payroll; Ajay's session does not edit the
  attendance calculation.
- **Cross-waits:** A7 reuses N2's calendar. A9 (overtime) needs N1. N1 reads
  the shift's break minutes and paid break from A5; until A5 lands it uses
  today's defaults (no break).
- **Shared files:** `includes/sidebar.html`, `employee_list.html`,
  `components.css`. Whoever lands first merges; the other pulls main before
  continuing. The badge (N3) owns the Employees list, so "Give login" (A6) goes
  on the Edit employee page.
- Nihal pushes branches only; Ajay's session reviews, merges and pushes main.

### Loose ends from 2026-09-12 (not in the table above)

| Item | State |
|---|---|
| `btn--secondary` buttons | The class does not exist, so 8 buttons render as a plain `.btn`: `devices/device_detail.html` (3), `devices/device_users.html` (2), `devices/message_detail.html`, `devices/punch_detail.html`, `organization/adoption_list.html`. Fix: `btn--ghost` — Nihal's N0. |
| Browser-default calendars on the device pages | Five `datetime-local` fields in `devices/forms.py` (register/edit device, enrollment, device department). Every other date field uses the project calendar. Fix: Nihal's N0. |
| Excluded punches on Nihal's server | The new default tick applies to new enrollments only. Existing enrollments need "Authorised for assigned-devices mode" ticked by hand. Already-excluded punches stay excluded until someone presses **Re-check** on Devices → Which devices count (N4, `feature/n4-device-scope`): on 2026-09-14 that page showed 23 "not an assigned device" and 25 "nobody enrolled with that number" for 1–14 Sep. Not pressed for him — it changes real punches. |
| Felna Tech shift mode | Still single company shift (set 2026-09-12 so its attendance could be calculated). Switching to department shifts is a settings change in Shifts. |
| Demo data in Ajay's local database | Simulated device `DEMO-SIM-FELNA` ("not real hardware") and its 1–12 Sep demo punches stay, at Ajay's request; they feed Felna's attendance and salary. |

### Merged 2026-09-13

Nihal's `feature/device-ui-fixes` (479cc87, 35b7728) and
`fix/device-department-overlap-error` (ef2049c) are merged into `main`
(81daa91). Full suite: **533 tests, green, 3.4 minutes** with `--parallel 4
--keepdb`.

- Checkboxes are drawn by the shared `.check` component; `StyledFormMixin`
  gives checkboxes and radios `check__input` instead of `input` (see
  UI_AND_ONBOARDING_CONVENTIONS.md).
- Both enrollment switches ("Attendance enabled", "Authorised for
  assigned-devices mode") start ticked for a new enrollment; editing keeps the
  saved values.
- Device page actions ("Enrollments", "Enroll an employee", "Open the
  enrollment used") moved into the page header; `.page__actions` wraps.
- Editing a device no longer invents a comm key (it had locked a live device
  out with 401s); the server-address status panel no longer reloads forever.
- Mapping a device to one department twice is a readable form error instead of
  an exclusion-violation page.
- `PASSWORD_HASHERS` is MD5 under `manage.py test` only (the full run fell from
  ~8.5 to ~3.4 minutes). Production is unaffected.
- Ajay's session, in the merge: removed the scheduling forms' `class=""`
  resets on checkboxes, which only existed to undo the old `input` class.

## Current checkpoint

- **Current deliverable:** P1 platform onboarding implemented; company setup and employee write workflows remain next. See [PLATFORM_IMPLEMENTATION.md](PLATFORM_IMPLEMENTATION.md) for files/functions and the UI workflow.
- **Actual code:** 22 implemented domain models, including the planned AuditLog; 13 registered apps and model-free base_template. P0 remains Verified. P1 is In progress; the earlier “backend complete” wording overstated authorization and workflow readiness.
- **Platform UI:** root routes to /platform/companies/ without membership; generated company identifiers, company create/edit/status, one master administrator with editable credentials/status, dated feature access and recent audit history work through forms.
- **Company UI:** existing lists remain read-only. Company-wide access is now restricted to unrestricted owner/company_admin; other roles/scopes fail closed until proper scoped views ship. Unimplemented Add/Export controls are explicitly disabled with reasons.
- **Design:** Warm Paper / Ink tokens and Sora; new platform tables use real DataTables with server-side paging/search/sorting, and database-backed selects use real Select2 while fixed choices use styled native selects. Responsive browser verification is required. Remaining component groups are not claimed complete.
- **Database:** existing PostgreSQL data/history preserved; two original auditlog migrations plus three additive corrections for Company defaults, the code sequence and administrator uniqueness. The root-catalogue change adds six migrations across five apps; with the 2026-09-12 designation correction the documented inventory is 86 models / 91 tables / 1,638 columns / 464 FKs. Nihal's organization/0004 is merged, so code and documents now agree.
- **Architecture/user contract:** modular Django monolith, accounts.User, Django-owned ORM/migrations; future FastAPI and workers reuse services. No DRF or duplicate persistence layer.
- **Hardware:** D1 remains unverified; the original “roughly a week” estimate is historical, not a current availability claim.
- **Next action (2026-09-15, after A11 part 1):** Leave (A10) is complete and simple. A11 is split into five simple parts (see "A11 plan" at the end); **A11 is complete** (parts 1–5: finalise/undo, bonus and deduction lines, joining/leaving mid-month, salary change mid-month, printable payslip). Ajay must have run `python manage.py migrate` for `payroll.0006` and `leaves.0002`. **A12 branch access** is agreed (see "A12 plan" at the end); **part 1, the permission list and access check, is done** (no migration). **A12 parts 2 and 3 are done** (Access page; company pages open by permission through `access_control/page_access.py` `BRANCH_PAGES`, sidebar "Company" section and "Your branches" card follow it); **parts 4, 5 and 6 are done** (the Employees area, Leave and Overtime, then Salary, limited by branch — each branch prepares its own salary, the owner/admin finalises); **part 7 is done** (the note for Nihal, [A12_BRANCH_ACCESS_FOR_NIHAL.md](A12_BRANCH_ACCESS_FOR_NIHAL.md); A12 is complete on Ajay's side). Ajay (2026-09-15): salary (A13) waits. **A16 3A data retrieval is done** (see "A16: SenseFace 3A data retrieval"); next is **mapping device users to employees, automated and from the employee list** (Ajay's request); also on the plan: **every employee (and other database-backed) picker uses our Select2 design** — Ajay named `employees/` (add an employee filter), `salary/overtime/` and `attendance/` (the Daily list, Nihal's, in his Select2 step); then the other plain selects (Record leave employee, Access branch filter, Create employee manager) (Ajay, 2026-09-15); **show whether each person's biometric is registered, and a Mapped / Not mapped badge, on the Employees list and the Device users list** (Ajay, 2026-09-15); and the two outage checks (3A end-to-end; 2A backlog on reconnect, where the automatic history request is not yet on); then A13 (plan first) and the leave-recalculation loose end on request. The September 2026 salary seed for company Ajay is in the git-ignored `.qa/seed_september_office.py` (check: `.qa/inspect_september_office.py`). N9 lists stay with Nihal; attendance code may be changed by Ajay's session only when a leave/salary step needs it (Ajay, 2026-09-15). Ajay's in-browser acceptance of A15, A10a and A10b is still pending. A10 → A11 → A12 → A13 was the prior sequence, not permission to proceed now. Nihal resumed 2026-09-15: **N9, N6, N5 and N8 merged** (office session; N5 brings migration `attendance.0004_corrections`, which Ajay runs). Nihal's pages get branch permissions only after the A12 part 7 note. A16 requires the office SenseFace 3A. Preserve completed setup, A5c, the paid-break fix, A14, A8 and the existing employee panel.
- **Environment:** no new .env variables.
- **Verification on 2026-09-07:** 124/124 tests pass on a fresh dedicated PostgreSQL test database (101 existing + 23 new); `check` clean; `makemigrations --check --dry-run` reports no changes; auditlog.0001 and .0002 applied successfully to the development database. Browser onboarding passed without seed_demo at 1440px, 768px and 375px. Full P1 employee onboarding is still pending.

## Phase tracker

| Phase | Status | Evidence required before completion | Current next action |
|---|---|---|---|
| P0 — Foundation/contracts | **Verified** | Runnable Django/PostgreSQL foundation, initial migrations/checks, tenancy and dependency decisions | Complete: migrations applied on PostgreSQL, 5/5 isolation tests green on Postgres, tenancy strategy + scoping code recorded |
| P1 — Company/people/calendar | In progress — platform onboarding delivered | Browser company setup, employee/history and access checks | Next: company organization/scheduling writes and scoped authorization, then employee lifecycle forms. Full P1 cold-start gate remains outstanding. |
| P2 — Leave | In progress — thin slice (2026-09-12): leave types, admin-recorded approved full-day leave, cancel | Approved full/half/hourly/partial-pay leave, optional balances, cancellation and concurrent-balance checks | A8 and A10 of the 2026-09-13 plan |
| P3 — Attendance simulation/manual | In progress — thin slice (2026-09-12): monthly calculation from authorised punches, first IN / last OUT | Shared ingestion/calculation pipeline, cross-device/retry/history tests, manual correction workflow | N1–N5 (Nihal) and A5 of the 2026-09-13 plan |
| P4 — Payroll | In progress — basic salary (2026-09-12): draft runs, fixed formula, payslip | Correct monthly/daily/hourly calculations, traceable lines, finalization and correction checks | A3, A4, A9 and A11 of the 2026-09-13 plan |
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

## 2026-09-12 — device server address from the software (Nihal, device workstream)

Branch `feature/device-server-address`, on top of `main`.

An administrator can now change a device's server address from the device edit
form. The rule that kept it out of `WRITABLE_OPTIONS` has not been relaxed —
a wrong address still strands the hardware, and the software still cannot
revert a device it can no longer reach. What changed is the order of
operations: **the address is proved to reach this server before the device is
told anything.**

The server fetches its own `/iclock/serveraddress-check` endpoint at the
candidate address with a one-time token and requires a reply signed with this
deployment's `SECRET_KEY`, so "reachable" means "reaches *us*", not "something
answered". Only then is the change queued. The new address is saved as current
only when a request actually arrives from the device on that hostname — never
on the device's own `Return=0`, which it sends before it tries. If it never
arrives, the two failures are told apart and worded differently: still checking
in at the old address (it ignored the command, nothing on the device changed)
versus gone silent (it switched and cannot reach us, and only the terminal can
undo that — the page names the exact old address, port and menu path).

No Celery, no worker: states advance on the device's own requests, and the
deadline is judged whenever the status is read.

New model `DeviceServerAddressChange` and three columns on `BiometricDevice`
(`server_scheme`, `server_host`, `server_port`), in `devices/0003`.

### Proved on the SenseFace 2A (ZAM70-NF24HA-Ver3.0.15, PushSDK 3.0.4S)

- `IclockSvrIP` / `IclockSvrPort` are the option names. Read back empty before
  the write and holding the hostname after it.
- **One option per `SET OPTION` command.** A tab-separated pair answers
  `Return=0` and is swallowed whole as the value of the first option — the same
  trap as the lowercase `DATA UPDATE user` field names.
- **No backup or secondary server option exists on this firmware**, and the
  server address is not readable at all (~350 candidate names probed, plus
  `INFO`, `CHECK` and `DATA QUERY tablename=options|config|network`). The
  software can only observe where a request arrived from.
- Step 1 caught every failure without touching the device: dead tunnel,
  unresolvable host, wrong port, http-vs-https, and a hostname missing from
  `ALLOWED_HOSTS`.
- Live hardware also surfaced a race the tests had not: the device polls every
  ~10s, so a check-in lands inside the probe window. It was confirming an
  attempt that had not been sent and flipping it out of `checking` mid-probe,
  which broke the probe's own token lookup. Only a *sent* attempt is now
  advanced by a device request; there is a regression test for it.

### Known limitations / still open

- The hardware success path pointed the device at the address it was already
  using, so every branch was safe. A change to a **different** address, and the
  deliberate lost-device recovery, still need doing with someone at the
  terminal.
- The device detail page overflows horizontally by 8px at 375px, caused by the
  pre-existing "Retire device" button in `page__actions`. Not touched here.
  *(Fixed 2026-09-13: `.page__actions` now wraps, in `feature/device-ui-fixes`.)*
- Devices whose address was typed in by hand show "not confirmed yet" until
  their first change; there is no backfill, deliberately, because we have no
  evidence of where they point until a request arrives.


## 2026-09-14 — N1b: a day closes on a rule, and attendance is live (Nihal)

Branch `feature/n1b-day-close`, on top of `main` (A5 merged in).

**A day owns the scans between its own opening and its close** — the next shift
start, or 24 hours after this shift started, whichever comes first
(`attendance/day_window.py`, `build_windows()`). The windows are built as one
contiguous chain across the range, so no scan falls in a gap and none lands in
two days. One rule covers three cases: a second shift the same day ends the
first, a night shift keeps the 06:00 check-out it began, and a day off owns its
own scans instead of extending the day before.

**While a day is open, a trailing OUT is a break-out, not a check-out.** That
was the bug that made somebody's lunch read as going home. When the day closes,
a trailing OUT becomes the check-out; a day that closes on an IN is checked out
at the shift's scheduled end with `review_status = needs_review` and the reason
"check-out by rule, no scan", and counts up to that end. Those days are the
review list N5 will show.

**Minutes are measured against the shift, not the scans:**

| | |
|---|---|
| `worked_minutes` | in-office time between the scheduled start and end, plus the paid part of the break. No overtime in it. |
| `calculated_overtime_minutes` | in-office time after the scheduled end, delayed by `Shift.overtime_after_minutes` |
| (neither) | time before the scheduled start — arriving early is normal, not overtime, and not paid. The real check-in time is still shown. |

**Attendance is live.** There is no Calculate button. `attendance.services`
exposes `recalculate()` (a date range), `refresh()` (bring a range up to date
only where it can have moved) and `recalculate_for_punches()`; device ingestion
calls the last one after a punch batch lands, and every read path calls
`refresh()`. `calculate_attendance()` is kept as a month wrapper so payroll's
call sites are unchanged. A day inside a **posted** `PayrollRun` is never
rewritten — `locked_ranges()` reads `PayrollPeriod.start_date`/`end_date`
(the model has no year/month columns).

Taking A5 in: an employee's own shift wins, and `shift_for` ignores it unless
the employee is named, so both lookups pass `employee_id=` now. The same
14:00–22:00 day reads 480 regular minutes on the employee's evening shift and
240 regular plus overtime on the company's 09:00–18:00 one.

### Things this cost

- A duplicate-scan window that compared each scan only to the last **kept** one
  let a steady stutter through as a phantom break-out. It chains from the
  previous scan now, kept or dropped.
- `refresh()` treated "nothing stored" as "settled history" and skipped a range
  that had never been built. It now requires `anything_stored and not
  open_days and not reaches_today` before it skips.
- `AttendanceRecord.delete()` hit `ProtectedError` — sessions PROTECT
  allocations. The override clears sessions, then allocations.
- `--parallel` needs `tblib` (added to requirements.txt), and `--parallel`
  with `--keepdb` reuses stale worker clones after a migration.

## 2026-09-14 — N3: the "Now" badge on the Employees page (Nihal)

Branch `feature/n3-in-office-badge`, on top of `feature/n1b-day-close`.

A **Now** column on the Employees page says where each person is, from today's
scans. `attendance/live_status.py` (`statuses_for(company_id, employee_ids=,
now=)`) derives it on read through the same `day_window` + `pairing` code the
calendar uses, so the badge and the calendar can never tell different stories
about the same day. **It writes nothing** — there is a test asserting no
`AttendanceRecord` appears from rendering the list.

| Badge | Tone | When |
|---|---|---|
| In office | success | the last scan today was an IN |
| On break | warning | the last scan was an OUT and the shift is still running |
| Left | neutral | the last scan was an OUT and the shift has ended (or the day has closed) |
| Not in yet | neutral | no scans, and the grace has not run out |
| Absent | danger | no scans, and shift start + `grace_in_minutes` has passed |
| On leave | info | an approved/reserved/consumed `LeaveDay` for today |
| Off today | neutral | a weekly off or a holiday — unless they scanned in anyway, in which case it says where they are |
| No shift | neutral | a working day with no shift to judge against |

"Absent" here is a **live reading, not the stored one**. The record does not
call a day absent until it closes (DEVICE_ATTENDANCE_POLICY.md step 7); the
badge says so as soon as somebody is late enough to be missing, which is the
question the page is being asked. Splitting "Left" from "On break" by the shift
end is the same kind of choice: the stored record keeps the day open until it
closes, which is right for deciding a check-out, but a badge answering "where
are they now" must not read "On break" all evening because somebody left at six.
The next day starts fresh — yesterday's check-out says nothing about this
morning.

`attendance/static/attendance/js/now_badge.js` repolls
`attendance:attendance_now` once a minute and repaints only the cells. Once a
minute, not once a second: the data only moves when somebody walks past a
terminal. Cells are rebuilt as elements, not `innerHTML` — the label is ours,
but this rewrites sixty times an hour and a template string here would be the
one place an injection could hide. A failed poll is silent; the column on
screen is still the last good answer.

The column is correct **before any script runs** — `base_template/views.py`
fills it server-side in one pass for the whole page (punches, placements, leave
and calendar fetched once, not per row). The endpoint is login-gated and
tenant-scoped: another company asking for these employee ids gets `{}`.

`employee_list.html` and `base_template/views.py` are shared files; `main` was
pulled immediately before touching them.

**No new migration, no new environment variable.** 21 tests in
`attendance/tests_now_badge.py`; checked at 1440, 768 and 375 px.


## 2026-09-14 — N7: the payslip, redesigned (Nihal)

Branch `feature/n7-payslip`, from clean `main`. Template and CSS only:
`payroll/templates/payroll/payslip.html`, a new
`payroll/static/payroll/css/payslip.css`, and a test file. No payroll view,
service or calculation was changed — the page uses exactly the context the
view already passed.

It reads top to bottom the way a paper payslip is read:

1. **Who and which month.** Employee name, code, Designation, Department and
   branch beside the pay period, its dates, and the pay basis and rate. The
   page head shows the run's **real status** — Draft or Finalised — where the
   old page always said "Draft".
2. **Three figures.** Gross earnings, deductions, and **net pay as the one
   hero figure** (`metric--hero`, per the style guide: one hero only). Where
   three figures fall to two per row (≤1100 px), net pay takes its own row
   rather than sitting half-width under the others.
3. **Earnings beside deductions**, as separate tables, each closed by its
   total; the deductions table ends with net pay under an ink rule. Every
   line carries **its basis underneath**, e.g. "Monthly salary", "11 days ×
   2,000.00 a day", "12 h × 150.00", "340 minutes short of the shift across
   the month". A penalty names its rule and the dates (with minutes late),
   and **Waive** sits on that line while the run is a draft. A waived
   penalty is listed under the table as "Waived, not deducted", with who
   waived it.
4. **The month's attendance** as a strip of small figures (absent in red when
   there are any), a **Calendar view** link to that employee's N2 calendar for
   the same month, and the salary rules in the card footer.

Every amount goes through `{% load money %}` / `|money`. Print hides the
sidebar, topbar, breadcrumbs and buttons and keeps the two tables side by
side.

The table component has no `tfoot` styles, so totals rendered with 1px
padding and centred labels (the old payslip had the same). `payslip.css`
gives them the body cells' rhythm. That fix is local to the payslip; other
tables with a `tfoot` still have the gap.

Checked on Northwind's September draft at 1440, 768 and 375 px: no page
overflow, and neither table scrolls at 375. 8 tests in
`payroll/tests_payslip.py` (layout and wording only — they reuse the August
fixture from `tests_basic.EndToEndTests`).

### Found along the way — pushed separately

Generating that September draft crashed:
`IntegrityError: null value in column "scheduled_start_at"`. My bug from N1b:
an employee with **no shift** (department mode, their department has none,
another department does) got a record on a weekly off or holiday with no
scheduled start. Before N1b such an employee got no record on any day; N1b
checked that for working days only. Fixed on
**`fix/attendance-no-shift-day-off`** (3 regression tests). Punch ingestion
was never affected — `recalculate_for_punches` catches the error — but
Generate salary and any page calling `refresh()` for that employee were.

## 2026-09-14 — N4: which devices count, and Re-check punches (Nihal)

Branch `feature/n4-device-scope`, from clean `main` (after A9). No migration:
every column already existed — `CompanyAttendanceSettings.device_attendance_scope`,
the branch and employee-assignment overrides, `assigned_device_authorized` —
and so did the decision engine (`devices/services/authorization.py`). What was
missing was a way to choose the rule and a way to apply a changed rule to past
punches.

**Page:** Devices → **Which devices count** (`/devices/attendance-rules/`),
linked from the device list and the punch list. For A14's menu: add it under
Devices.

- **Punches count on** — the four options as cards, each with what it means:
  assigned devices only / department devices / branch devices / company
  devices. Saving goes through `attendance_rules.set_company_scope()`:
  permission re-checked in the service, `settings_version` bumped, and an audit
  record whose `before_data` carries the old scope. That previous value is what
  lets a late punch from an offline device still be judged by the rule in force
  when it was made — there is a test for exactly that. If a branch or employees
  have their own rule, the card says so.
- **Punches that don't count** — a From/To range (default: the last 30 days,
  in company time), a table of why punches were left out and what fixes each
  reason, with the count linking to the punch list.
- **Re-check N punches** — a separate POST for exactly the range shown.

**Where it lives.** The plan put the scope in Attendance settings, which is
`scheduling/`. It is on a Devices page instead so `scheduling/` was not
touched, and because choosing the rule and re-checking the punches it affects
belong on one screen. The company column is the same one either way.
**Decided by Ajay, 2026-09-15:** the company device scope stays on Devices →
Which devices count.

**What a re-check does** (`attendance_rules.recheck_punches()`):

- Takes every punch in the range whose status keeps it out of attendance
  (`EXCLUDING_STATUSES` + `policy_unresolved`) and judges it again **under the
  settings in force now** — `authorization.evaluate(..., policy_at=now)`.
  Only the policy switches move to now: the scope chain, the device grant and
  attendance enabled. Who the punch belongs to is still resolved at the punch's
  own time (device user numbers are reused), and so are the employee's placement
  and the device's department links.
- **Never touches a punch that already counts.** Tightening a rule cannot
  quietly take away a day somebody was credited for.
- **Skips days inside a finalised salary month** — their punches are not even
  re-judged.
- Records `recheck` (when, who, previous status) in each punch's
  `authorization_snapshot`, and one `punches.rechecked` audit row holding every
  punch's before and after status.
- After the commit, rebuilds attendance for the employee-days of punches that
  now count (`recalculate_for_punches`).

Ingestion is unchanged: `policy_at` defaults to the punch time.

A punch from before a person's enrollment on that device started stays
excluded after a re-check, and should: a re-check cannot attach a scan to
someone the device did not know then. The page says the fix — set the
enrollment's start date earlier, then re-check. That is most of the
"nobody enrolled" punches on Nihal's server.

**Tests:** 25 in `devices/tests_attendance_rules.py`. The fixture backdates
the company's setup to January; without that every row is seconds old, a
change a moment after creation reads as the original setup, and the re-check
tests passed whether or not they judged by today's rules. Checked by breaking
`policy_at` on purpose: 7 tests fail. Checked on D Company's real data at
1440, 768 and 375 px (on a phone the "what fixes it" text moves under the
reason so the count stays on screen).

**No new migration, no new environment variable.**


## 2026-09-14 — Home setup and A5c (Ajay's session)

Cloned main at `a892f12` into the home project folder and created `venv` using
Python 3.13. Installed the pinned requirements, including Django 6.1.1 and
`tblib`. PostgreSQL 18 was already running. Copied `.env.example` to the ignored
`.env`; Ajay owns its database values/password, development migrations and
superuser creation. The assistant did not open `.env` or handle the database
password. `manage.py check` and `pip check` pass; scheduling/payroll first passed
196 tests, then 213 after the A5c regressions were added. No development database
reset or assistant-run development migration.

**A5c — branch `feature/a5c-calendar-fixes`.**

- Weekly-off overlap checks use `[effective_from, effective_to)` for the same
  company/branch and weekday, including stopped history. Adjacent periods are
  allowed. Refusals name the weekday, scope and dates and suggest changing the
  existing start date. Weekly-off writes serialize on the company row; the
  existing exclusion constraint remains the final guard.
- **Navigation: Shifts → Weekly off days → Change start date**, for active and
  stopped rules. The project date picker is used. Permission, company and branch
  scope are rechecked in the service. A start must precede the stop date.
  Corrections record before/after dates in `weekly_off.start_changed`.
- Moving either direction rebuilds the changed range with the existing
  `attendance.services.recalculate`, in monthly batches bounded by employee
  placement and today. Branch rules limit the employee set. The calendar write,
  recalculation and audit share a transaction. Finalised months remain locked;
  an unsuccessful rebuild rolls the correction back.
- **Shifts → Add weekly off days**, **Shifts → All holidays → Add holiday/Edit**,
  and **Shifts → Holiday calendar** no longer ask whether days off are paid.
  The columns remain; service writes always store True, including a caller that
  submits False. `WorkCalendar` treats legacy False flags as paid without a data
  rewrite. Daily/hourly day-off payment still follows Salary settings.
  Redundant Paid columns and Unpaid calendar markers are removed.
- **Payroll → Salary settings:** the form and current-rules summary say
  **One day's pay for absence deductions**; help explains unpaid leave and the
  overtime hourly-rate base. No salary formula or monthly pay basis was changed.
- 17 new regression tests cover boundaries, stopped periods, dates back to 2000,
  tenant/branch/role refusal, form submission, audited corrections, recalculation
  in both directions, finalised-month preservation, rollback and legacy paid flags.
- Browser QA used synthetic rendered test fixtures (rolled back) at 1440, 768 and
  375 px: no page horizontal overflow; the new date picker opens within the phone
  viewport; paid fields are absent. No browser login password was entered.
- `makemigrations --check --dry-run`: no changes. **No migration and no new
  environment variable; `.env.example` unchanged.** Full suite: **905 tests
  PASS**, `manage.py test --parallel 4 --keepdb --noinput`, 276.656 seconds.
  After the summary-label follow-up, all 26 salary-settings tests also pass.

Next at home (updated 2026-09-15): **A8**, then A15; A14 is complete. Office A16 still needs the physical
SenseFace 3A. Nihal's planned work is N5, N6, N8; N9 waits for A15. Keep his attendance
recalculation API, overtime hook and finalised-month guard when merging N5.

## 2026-09-14 — Home salary simulation and paid-break correction

Ajay completed local database/migration/account setup and configured his company,
Head Office, shifts, days off, holidays, salary/attendance/overtime rules,
departments, designations and dummy device. The assistant did not open `.env`
or handle the database password.

- Seeded September 2026 as a full-month simulation, including future dates,
  through the real device ingestion and attendance/payroll services. Preserved
  the four existing employees, their salaries/placements and company settings.
  Ajay explicitly approved two extra demo employees: daily BDT 1,500 and hourly
  BDT 200. Six device users are mapped; 397 punches include two intentional
  duplicate scans. There are 180 attendance days, 17 approved leave days, a
  cancelled leave example, seven overtime decisions and one waived penalty.
- Six draft salaries match independent scan-based arithmetic and every earning
  and deduction code. Total net is **BDT 177,047**. The daily demo's negative
  net intentionally exercises additive penalties with no deduction cap.
  This is synthetic data, not evidence from the physical device.
- Fixed `attendance/pairing.py` on `fix/paid-break-outside-shift`: `_account`
  previously credited all outside minutes as paid breaks, including time before
  or after the shift. Each gap is now intersected with the scheduled shift
  before applying the paid-break allowance. Raw outside time and overtime
  calculations remain intact.
- Reproduced Dia's September 28 defect before fixing it. After recalculation,
  scans 09:00, 18:00, 19:15, 21:15 produce **540 regular minutes**, not 615,
  and 120 approved overtime minutes. At BDT 200/hour the read-only equivalent
  now pays **BDT 2,600**, removing the BDT 250 overpayment. Her monthly salary
  and all six seeded net salaries remain unchanged. All 180 attendance days
  now match the independent reference. No employee compensation was changed.
- Regression cases cover gaps before/after shifts, crossing either boundary,
  paid lunch mixed with overtime, allowance caps, open overtime and overnight
  shifts, plus persisted attendance feeding an hourly salary calculation.
- Validation: **229 attendance/payroll tests pass** (78.865 seconds). All eight
  inspected app pages and six payslips render; posting Generate salary preserves
  the draft totals. `git diff --check` passes. No schema changes.
- No migration or environment-variable change. Review the simulated month in
  **Payroll → September 2026** and **Attendance → Calendar → Dia → September 28**.
  The local detailed report and seed scripts are in ignored `.qa/salary-seed/`;
  local data and reports are not shipped to other computers by a git pull.

**Coordination override:** Nihal is not making changes now. Generate his prompt
only when Ajay explicitly asks. His remaining plan is N5, N6, N8, then N9 after
A15; these are not active assignments. Ajay's next build (updated 2026-09-15) is **A15 → A10 → A11 → A12 → A13**; A14 and A8 are complete. A16 awaits the physical SenseFace 3A in the office.


## 2026-09-15 — A14: sidebar menus and submenus (Ajay's session)

Built on `feature/a14-sidebar-menus`. Important pages and settings are now
reachable directly from seven expandable company menus, with 28 submenu links
for an unrestricted company administrator:

| Menu | Submenus |
|---|---|
| Employees | All employees; Create employee |
| Attendance | Daily list; Calendar |
| Leave | Leave list; Record leave; Leave types |
| Salary | Salary by month; Salary settings; Penalty rules; Overtime; Overtime settings |
| Shifts | Overview; Shifts; Department shifts; Weekly off days; Holidays; Holiday calendar; Attendance settings |
| Organisation | Branches; Departments |
| Devices | All devices; Register device; Enrollments; Punches; Messages; Unresolved; Which devices count |

- **Existing links and buttons on individual pages remain.** Penalty rules and
  Overtime settings link to their existing Salary settings sections. Shifts,
  Department shifts and Weekly off days link to anchored cards on the overview.
  Device users still requires choosing a device; its existing device-page link
  is preserved. No replacement settings screens, new lists or duplicated forms.
- `base_template/navigation.py` owns the company menu destinations and their
  edit/detail aliases. Namespaced routes prevent cross-app highlighting clashes.
  The current menu opens on page load; the selected page has the solid ink
  state and `aria-current`. Fragment navigation updates the selected submenu.
- Create/settings links follow structure-manager access. Device navigation and
  the broad employee list/dashboard follow unrestricted administrator access.
  Root and employee/branch-manager sidebars remain separate. Existing endpoint
  and service permissions are unchanged; the sidebar grants no access.
- Shared `navigation.js` handles tablet/phone navigation for all three surfaces.
  The drawer has Close, Escape and backdrop dismissal, focus wrapping/return,
  background page inertness and its own scroll. Native details allow keyboard
  expansion and a usable navigation fallback without JavaScript.
- Browser checks at **1440, 768 and 375 px** found no page horizontal overflow.
  Verified Salary settings, Penalty rules, Overtime settings and Weekly off
  days navigation; keyboard expansion, Tab/Shift+Tab wrapping and Escape;
  phone employee/platform menus retain their own destinations. Previews used
  synthetic test fixtures in a rolled-back transaction, not the local payroll.
- Eight new tests cover every destination and fragment, record-page selection,
  retained page actions, HR/scoped administrator restrictions, company switching,
  employee/manager separation and the platform sidebar.
- Validation: **916 tests pass** in the full suite, run once with
  `manage.py test --parallel 4 --keepdb --noinput`. `git diff --check` passes.
- **No migration; `.env.example` unchanged; no new environment variables.**
  Normal reload picks up the versioned CSS/JS. No local salary or configuration
  changes in this step. QA logs/previews live in ignored `.qa/`.

**Next (updated after A8):** A15 shared server-side tables, A10 full leave, A11 salary completeness, A12 access, A13
payments. A16 still needs the physical SenseFace 3A in the office. Nihal remains
paused; N5/N6/N8/N9 are planned only. Generate his prompt only when Ajay asks.


## 2026-09-15 — A8: employee leave requests and branch-manager approval

Built on `feature/a8-leave-requests`. A7's employee panel now includes the A8
workflow. **Confirmed by Ajay:** the company administrator handles requests
when there is no active assigned branch manager, and managers' own requests.
Nobody may decide their own request. Routing uses current active memberships;
an unassigned manager manages no branches, and inactive managers do not block
the administrator fallback.

- **Employee:** My leave → Request leave (`/me/leave/request/`). Choose a leave
  type, full-day date range, requested paid/unpaid status and a reason. The
  service gets the employee from the login; posted employee IDs grant no access.
  Submission creates Pending request/segment rows and **no LeaveDay rows**.
- **Manager:** My branches → Approval inbox (`/me/leave-inbox/`). Review requests
  for assigned branches, approve as paid/unpaid or reject with a required note.
  **Administrator fallback:** Leave → Approval inbox opens the same scoped page.
  Employees see the status, decision note and approved pay in My leave.
- Approval locks the request and employee, rechecks dates, placements, existing
  leave and finalised salary ranges, then uses the same day-expansion helper as
  admin-recorded leave. Days, decision and audit are atomic. Repeated decisions
  are refused; failure rolls everything back. Approval refreshes affected
  attendance. Draft salary is updated using the existing Generate salary flow.
- Weekly offs/holidays are skipped; empty working-day ranges and overlapping
  pending/approved leave are refused. Requests spanning branches must be split.
  Approved pay may differ from requested pay without rewriting the request.
- **Manager:** My branches → Branch attendance (`/me/branch-attendance/`). Date
  and employee-name filters, scoped attendance and today's in-office badges.
  Badges update on page refresh. No payroll amounts or company-wide employee
  pages are opened to managers; the existing `/me/` gate remains in force.
- Inbox and branch attendance use database pagination (25 rows), filters and
  result counts. Shared DataTables paging/search/sorting/page-size integration
  remains **A15**, the agreed next step; include both new lists in that work.
- Half-day/hourly leave, partial pay, balances, attachments, amendments and
  withdrawals remain A10. Existing admin Record leave and Cancel links remain.
- **166 focused tests pass** (`leaves base_template payroll`, parallel 4,
  keepdb; 47.121 seconds), including 11 new workflow tests. Coverage includes
  employee ownership, branch/company refusal, manager/admin routing, replay,
  audit rollback, posted-range guards, calendar skipping and unpaid salary
  arithmetic. Brief synthetic browser checks verified request/decision forms,
  pay-field hiding on rejection, inbox and branch list at 375/1440 px, with no
  horizontal page overflow. Test fixtures were rolled back; local salaries and
  company configuration were not changed.
- **No migration, no new variable, `.env.example` unchanged.** Reused the
  existing LeaveRequest/Segment/Day tables and statuses. No database password
  was read or handled. Focused verification replaces a repeated full-suite run
  for this step following Ajay's usage-cost feedback.

**Next:** A15 → A10 → A11 → A12 → A13. A16 still needs the office SenseFace 3A.
Nihal remains paused; N5, N6, N8 and N9 are planned only. No prompt until asked.

## A15 completed — 2026-09-15 (Ajay’s session)

- Shared `base_template.tables.paginate/render` handles bounded database pages,
  allowlisted search/order, counts and DataTables JSON through each existing
  authenticated view. Existing escaped cell/action markup is reused.
- Thirteen company/self-service lists converted, including the A8 inbox and
  manager attendance list. Numbered pages, 10/25/50/100 rows, direct page jump,
  first/last navigation and a counted HTML fallback replace Next/Previous-only
  pagination. **Ajay explicitly required the current table styling to remain.**
  Reused the existing Paper/Ink styles; existing page links remain.
- Overtime adds Employee and Branch filters; state counts, search and sorting
  happen before database slicing. SQL state/payment projections were compared
  against the existing overtime rules for automatic, pending, rejected,
  short, day-off and disabled-pay cases. Salary formulas were not changed.
- Salary numeric columns sort numerically; leave requests occupy one table row
  even when they have several segments. Employee Now badges follow the visible
  page after a draw. Finalised-only personal payslips and existing access guards
  remain enforced.
- Verification: **171 focused tests passed** (`leaves base_template payroll`); dedicated table
  tests cover real SQL LIMIT/OFFSET, all company adapters/orders, hostile inputs,
  escaping, tenant/role/self-service restrictions and overtime parity. Browser
  checked the real local employee and overtime pages: retained styling, changed
  page length to 10, jumped to page 3 (21–30 of 30), then clicked numbered page 2
  (11–20 of 30). Browser was closed deliberately after verification.
- No database migrations, dependencies or `.env.example` changes. No database
  password was read. No Nihal prompt generated.

**Next:** A10 → A11 → A12 → A13. A16 stays office-only. Nihal is paused;
N5/N6/N8/N9 are planned work, not active assignments.

## A15 finished — 2026-09-15 (Claude, Ajay's session)

Ajay disputed the A15 completion above. Claude reviewed it read-only, then finished it.

- **Why the pages looked unchanged:** the 13 converted pages were probed against
  Ajay's real local data with Django's test client (inside a rolled-back
  transaction; no password used). Every page returned its table JSON, every
  sortable column and search worked. Most lists are short (Employees 6, Salary by
  month 6, Holidays 2, Branches/Departments/Leave types 1, Leave 18); only
  Overtime (30) has more than one page at 25 rows, so the others show a single
  page "1". The branch-manager pages could not be probed: no manager login exists.
- **Named tables:** `paginate(..., name=...)` / `data-server-table="name"` lets
  one page hold several independent lists (`table=<name>` draws, `<name>_page`
  fallback). Unnamed tables are unchanged. The fallback pager builds its links
  in Python so each list keeps the others' parameters.
- **Converted lists A15 had missed:** Shifts → Shifts, Department shifts and
  Weekly off days (all on the Shifts page; readiness still checks every active
  department, not one page); the older company department list (designation
  count now one SQL annotation); root Departments and Designations (their search
  and status filters stay; browser-only `data-enhance` removed).
- **Remaining:** N9 — Attendance → Daily list and the device lists. Owner to be
  decided by Ajay.
- **Tests:** `manage.py test base_template scheduling organization --parallel 4
  --keepdb` → **319 tests OK**. Two new table tests cover the named Shifts-page
  lists (separate counts, order, search by weekday name, per-list fallback page
  parameters) and the company/root department lists. Not a full-suite run.
- **Not yet done:** Ajay's in-browser check. Claude cannot sign in to the local
  app without his password, so the rendered pager was not screenshot-verified
  in this step.
- No migrations, dependencies or `.env.example` changes.
- Ajay then pointed at `/attendance/`; it is N9 (Nihal's). Ajay decided: **Nihal's
  parts are not built by Ajay's session** — it stays as is for N9.

## A10 split into sub-steps — 2026-09-15 (Claude, Ajay's session)

| Step | Scope | Needs migration |
|---|---|---|
| **A10a — done** | HR records/cancels leave; default leave types; employee code in the Record leave picker; employee withdraws a pending request | No |
| **A10b — done** | Half-day leave: Full day / Half day on Record leave and Request leave; paid or unpaid | No |
| **A10c — done** | Optional **Days per year** on each leave type; left shown on My leave and the approval page; over-allowance refused when recording, requesting and approving. No accrual, carry-forward or ledger | Yes: `leaves.0002_leave_type_days_per_year` |
| — | Cancel approved leave: HR/admin use the existing Cancel. Nothing to build | — |

**Simplified 2026-09-15 (Ajay: "keep leave simple").** Dropped: partly paid
leave (the database allows paid = 100% or unpaid = 0% only, and the attempt was
discarded unpushed), hourly leave, morning/afternoon choice, attachments, leave
policies/versions, accrual/carry-forward ledger, amendment, partial cancellation.

## A10a done — 2026-09-15 (Claude, Ajay's session)

- **HR records leave.** `leaves.services.require_leave_recorder` (owner, company
  admin, HR) guards Record leave and Cancel. Nobody records or cancels their own
  leave. Recording or cancelling inside a finalised salary month is refused
  (record previously did not check). Leave types remain owner/admin only.
  Sidebar: Leave → Record leave now also shows for HR (`record=True` item flag).
- **Default leave types.** Casual (CL), Sick (SL), Earned (EL), Maternity (ML), no
  allowances yet (A10c). Created by the platform's Create company screen
  (`onboard_company(default_leave_types=True)`); seeds/tests are unchanged.
  Existing companies: Leave → Leave types → **Add default leave types** adds only
  missing codes, audited. The button hides once all four exist.
- **Employee code in the picker.** Record leave lists "E1 · Rahim"; Select2 finds
  people by code or name.
- **Withdraw.** My leave → Action → **Withdraw** on a pending request → confirm.
  Status becomes Withdrawn, its segment is cancelled, the dates can be requested
  again; approved requests cannot be withdrawn. Audited as `leave.withdrawn`.
- No migrations, dependencies or `.env.example` changes.
- Tests: **full suite 936 tests OK** (`manage.py test --parallel 4 --keepdb`).
  New/updated tests cover HR record/cancel and the own-leave refusal, default
  types (new company, add-missing, admin only), the code in the picker, the HR
  sidebar link, and withdraw (own pending only; approved refused; dates reusable).
  Not browser-verified by Claude (the local app needs Ajay's sign-in).
- Loose end noticed, not changed: Record/Cancel leave do not recalculate
  attendance immediately (approval does); days update when attendance refreshes.

## A10b done — half-day leave — 2026-09-15 (Claude, Ajay's session)

Ajay allowed attendance changes where a leave step needs them, and asked for
leave to stay simple.

- **Leave:** Leave → Record leave and My leave → Request leave have **Length:
  Full day / Half day**. A half day is one date, 0.5 day, half the shift's
  minutes, paid or unpaid as today. No morning/afternoon choice. Two dates with
  Half day are refused. Leave list and My leave show "Casual (half day)"; My
  leave counts 0.5.
- **Attendance (`attendance/services.py`, Nihal's app, changed with Ajay's
  permission):** half-day leave and the employee scanned in → Present, no late or
  early-out minutes, paid day (paid leave) or half paid (unpaid leave); still
  in progress until the day closes, like any day. No scans → Leave, half paid
  (paid) or unpaid. Full-day leave is unchanged. `live_status.py`: the Now badge
  says On leave only for full-day leave.
- **Salary:** monthly deducts the unpaid half on a worked day too; the
  "minutes short" method does not count the leave half as short; hourly pays a
  paid half's minutes on a worked day. Daily pay already followed the day's
  payable fraction.
- **Nihal, when he resumes:** pull main before touching attendance; keep the
  half-day branch in `_write_day` and the `balance_units__gte=1` badge filter.
- Tests: **full suite 941 tests OK**. New `leaves/tests_half_day.py` runs real
  scans through attendance and salary (paid/unpaid half day, came in / did not);
  leave tests cover one-date rule, 0.5 units, request/approval and My leave text.
  Not browser-verified by Claude (the local app needs Ajay's sign-in).

## A10c done — yearly allowance per leave type — 2026-09-15 (Claude, Ajay's session)

- **Setting:** Leave → Leave types → Add / Edit → **Days per year** (blank = no
  limit, whole or half days). Leave types list shows the column ("No limit").
- **Counting:** approved leave days of that type whose date falls in the calendar
  year (reserved/approved/consumed leave days; a half day is 0.5). Pending
  requests do not use the allowance. Leave crossing New Year counts per year.
- **Refusal:** Record leave, Request leave and approval refuse leave that goes
  over, e.g. "Casual leave: 0 of 1.5 days left in 2026; this leave needs 0.5."
  Approval rechecks, because other leave may have been approved meanwhile.
- **Shown:** My leave → **Allowance in {year}** (Days per year, Used, Left) for
  active types with an allowance; the approval page shows "Casual: 3 of 10 days
  left in 2026". Record leave shows the remaining days in its refusal message
  (kept simple: no live balance on the form).
- **Migration:** `leaves/migrations/0002_leave_type_days_per_year.py` adds the
  nullable field. **Ajay runs `python manage.py migrate`.** Existing leave types
  get no limit; no data changes.
- Tests: `makemigrations --check` → no changes; **full suite 943 tests OK** on
  fresh test databases (new migration, so no `--keepdb`). New tests: allowance
  with half days and New Year reset; request allowed while pending, approval
  and a further request refused; approval page and My leave text. Not
  browser-verified by Claude (the local app needs Ajay's sign-in).
- No `.env.example` changes or dependencies.

## A11 plan — salary, kept simple — 2026-09-15 (agreed by Ajay)

| Part | Scope | Migration |
|---|---|---|
| **1 — done** | Finalise month (owner/admin) and Undo finalise with a reason | No |
| **2 — done** | Add bonus / Add deduction lines on a draft payslip (amount + reason); kept on regenerate; removable only while draft | Yes: `payroll.0006` |
| **3 — done** | Joining/leaving mid-month: monthly salary for the employed days, calendar days of the month | No |
| **4 — done** | Salary change mid-month: days before at the old rate, the rest at the new rate (one Basic line per rate); monthly salaries only | No |
| **5 — done** | Payslip PDF as a print layout (browser Save as PDF); no new package | No |

Agreed choices: undo allowed for owner/admin with a reason; proration by calendar
days; print-layout PDF; one part per turn. Not in A11: email, allowance formulas,
tax, bank files (payments/advances/dues are A13).

## A11 part 1 done — finalise salary — 2026-09-15 (Claude, Ajay's session)

- **Salary → Salary by month**, with a generated draft: **Finalise {month}** →
  confirmation → **Finalise**. Owner/company admin only (HR refused).
  - Status becomes Finalised (badge with date); Generate/Regenerate disappears.
  - Employees see the payslips under My payslips (already finalised-only).
  - The month's attendance and overtime stop changing (`locked_ranges`).
  - Proposed penalties become Posted. Audited as `payroll.finalised`.
  - Refused when overtime was decided after the draft was generated
    ("Generate the month again, then finalise"), so a stale draft is not locked.
- **Undo finalise** (same page, when finalised) → reason required → month becomes
  a draft; posted penalties return to proposed; audited as `payroll.reopened`
  with the reason. Generate again after fixing, then finalise.
- Code: `payroll.services.finalise_payroll` / `reopen_payroll`; views
  `payroll_finalise` / `payroll_reopen` (`/salary/finalise/`, `/salary/finalise/undo/`);
  template `payroll/run_action.html`; sidebar keeps Salary by month selected.
- Tests: new `payroll/tests_finalise.py` (no draft refused, HR refused, lock and
  no regenerate, reason required, undo unlocks and regenerates, stale draft after
  an overtime decision refused, page buttons and confirmation pages); **full
  suite 946 tests OK**. Not browser-verified by Claude (needs Ajay's sign-in).
- No migrations, dependencies or `.env.example` changes.

## A11 part 2 done — bonus and deduction lines — 2026-09-15 (Claude, Ajay's session)

- **Salary → Salary by month → an employee's payslip → Bonus and deductions**
  (owner/company admin, draft month only): choose **Bonus** or **Deduction**,
  amount above zero, reason (shown on the payslip) → **Add line**. The month is
  regenerated and the payslip shows the line in Earnings or Deductions.
- Kept across every regeneration; **Remove** marks it removed (nothing deleted)
  and regenerates. On a finalised month the card says to Undo finalise first.
  Employees see the lines on their finalised payslip but not the card.
- Deductions still respect "never below zero" unless Salary settings allow
  negative salary.
- Model `PayrollAdjustment` (`payroll_adjustment`: employee, target period, type,
  amount > 0, reason, status) and `PayrollLine.payroll_adjustment`
  (`source_type="adjustment"`, `is_manual=True`). Audited as
  `payroll.adjustment_added` / `payroll.adjustment_removed`.
- **Migration `payroll/0006_payroll_adjustment.py` — Ajay runs `python manage.py migrate`.**
- Tests: `makemigrations --check` → no changes. Full suite on fresh databases:
  949 tests, 948 passed; the one failure was the new test expecting a negative
  net after removing a bonus (the standard rules cap at zero). Test corrected;
  `payroll` app rerun fresh: **103 tests OK**. New `payroll/tests_adjustments.py`
  covers net and regeneration, removal, invalid values, HR refused, finalised
  month refused, and the payslip page add/remove. Not browser-verified by Claude.
- No `.env.example` changes or dependencies.

## A11 part 3 done — joining or leaving mid-month — 2026-09-15 (Claude, Ajay's session)

- Uses the employee's existing **Joining date** and **Leaving date** (Edit
  employee). No new setting.
- Salary generation now counts only attendance days from the joining date to
  the leaving date (inclusive). An employee with no days inside employment that
  month gets no payslip.
- **Monthly salary:** Basic = monthly salary × employed calendar days ÷ calendar
  days in the month, e.g. joined 16 Aug → 16 of 31 days → 30,000 × 16/31 =
  15,483.87. The payslip line reads "Basic salary (16 of 31 days employed)".
  Absence and other deductions inside the employed days work as before. A full
  month still shows "Basic salary".
- **Daily and hourly staff:** unchanged apart from ignoring days outside
  employment; they are already paid for the days/hours worked.
- The payslip's calculation snapshot stores `employed_days`.
- Tests: formula test (15 of 30 days → half salary; full month unchanged) and
  new `payroll/tests_proration.py` (joining 16 Aug, leaving 10 Aug, full month);
  **full suite 952 tests OK**. Not browser-verified by Claude.
- No migrations, dependencies or `.env.example` changes.

## A11 part 4 done — salary change mid-month — 2026-09-15 (Claude, Ajay's session)

- Uses the existing dated salary history: a change from a date closes the old
  salary at that moment and opens the new one (`employees.services.revise_compensation`,
  e.g. Edit employee → Salary with a later start date).
- **Monthly salaries:** when more than one monthly salary is in force during the
  employed days of the month, the payslip has one Basic line per salary, e.g.
  "Basic salary 30,000.00 (01 Aug–15 Aug, 15 of 31 days)" = 14,516.13 and
  "Basic salary 31,000.00 (16 Aug–31 Aug, 16 of 31 days)" = 16,000.00. Works
  together with joining/leaving mid-month (part 3).
- Kept simple: one day's pay for absence deductions still uses the salary at
  month end. A change of pay basis inside a month, and daily/hourly rate changes,
  still use the rate in force at month end (not split).
- The payslip snapshot stores `basic_segments` (rate, first day, last day).
- Tests: new `payroll/tests_salary_change.py` (raise on the 16th → two lines;
  raise plus joining on the 10th → 6 and 16 days; no change → one line);
  **full suite 955 tests OK**. Not browser-verified by Claude.
- No migrations, dependencies or `.env.example` changes.

## A11 part 5 done — printable payslip (PDF) — 2026-09-15 (Claude, Ajay's session)

- Found already present: the payslip page (company and My payslips) had a
  Print button and `payroll/static/payroll/css/payslip.css` print rules hiding
  the sidebar, top bar, breadcrumbs and buttons.
- Completed: the button is now **Print or save as PDF** (tooltip: choose Save as
  PDF as the printer). Print rules also hide message banners, the footer and the
  company-only **Bonus and deductions** card (`no-print`), and set 12 mm page
  margins. The printed/PDF payslip keeps the header, earnings and deductions,
  net pay and attendance summary. No new package.
- Tests: payslip page test checks the button and the non-printed card;
  `payroll` + employee panel tests **117 OK** (small template/CSS change, so
  the affected apps only, per the testing agreement; full suite last ran at
  955 OK for part 4). Printing itself is not browser-verified by Claude.
- **A11 is complete.**
- No migrations, dependencies or `.env.example` changes.

## Attendance-related items check — 2026-09-15 (asked by Ajay)

Nothing in the A steps is left partial because of Nihal's attendance code:
- N9 (`/attendance/` Daily list and device lists on server-side tables) is not
  started by Ajay's session, per Ajay's instruction; the discarded attempt left
  nothing behind.
- Half-day leave changed attendance with Ajay's permission and is complete.
- Hourly and partly paid leave were dropped by Ajay's "keep leave simple"
  decision, not left partial.
- Loose end (leave side, not Nihal's code): Record leave and Cancel leave do not
  recalculate attendance immediately (approving a request does); days update on
  the next attendance refresh or salary generation. Small fix; awaits Ajay.

## A12 plan — branch access, handed out dynamically — 2026-09-15 (agreed by Ajay)

Ajay's decisions: access is dynamic; a branch manager automatically controls
everything for their branches and can give any access they hold to HR,
department heads or anyone else in those branches; branch managers can create
logins; salary settings are company-wide and untouchable by branches;
attendance and device pages are left to Nihal. Claude's choices, accepted with
"go": each branch prepares and reviews its own salary, the owner/admin finalises
the whole company once; the existing HR role keeps company-wide leave recording
and overtime.

| Part | Scope | Migration |
|---|---|---|
| **1 — done** | Permission list and one access check (`access_control/branch_access.py`), grant/remove services | No |
| **2 — done** | Organisation → Access page: people in your branches, tick permissions per branch, create logins | No |
| **3 — done** | Branch managers and grantees open company pages; sidebar and dashboard by permission and branch | No |
| **4 — done** | Employees area branch-scoped (list, create/edit, logins) | No |
| **5 — done** | Leave and overtime branch-scoped (list, record/cancel, approval inbox, overtime) | No |
| **6 — done** | Salary branch-scoped: Salary by month and payslips by branch; generate only your branches inside the month; bonus/deductions; finalise and settings stay owner/admin | No (not needed) |
| **7 — done** | Written note for Nihal: [A12_BRANCH_ACCESS_FOR_NIHAL.md](A12_BRANCH_ACCESS_FOR_NIHAL.md) — attendance pages (Daily list, Calendar, day panel, Now, Days to review / Fix a day / Withdraw) and the N6 employee page / End employment; devices stay owner/admin | No |

Permissions: employees view / create and edit / logins; leave view / record and
cancel / approve; overtime view / decide; salary view / prepare (generate, bonus
and deductions); access give to others. Not grantable: company-wide settings,
branches/departments, finalising a month.

**Confirmed by Ajay (2026-09-15), rules for every later part:**
1. The company (owner / company admin) keeps **all access in every branch,
   exactly as it operates today**. Branch scoping in parts 3–6 only limits
   branch managers and people given access; owner/admin pages stay unchanged.
2. **A branch without an active branch manager is handled by the company**
   (owner/admin already hold every branch). Leave approvals already fall back to
   the company inbox (A8). The company may also grant that branch's HR or anyone
   else access from the Access page.

## A12 part 1 done — permission list and access check — 2026-09-15 (Claude, Ajay's session)

- `access_control/branch_access.py` answers "may this person do X, and in which
  branches?": `branches_for`, `can`, `require`, `scope_queryset`.
  - Owner / company admin → every branch. Branch manager → every branch
    permission in their own branches (their login's branch list), automatically.
  - Existing HR role → company-wide `leave.view`, `leave.record`,
    `overtime.view`, `overtime.decide` (unchanged behaviour).
  - Everyone else → only live grants: `EmployeePermissionOverride` rows with a
    branch list (dated; removed grants are ended, not deleted).
- `grant_access` / `revoke_access`: the granter needs `access.grant` and the
  permission itself in every chosen branch; nobody changes their own access;
  granting adds branches to an existing grant; removing keeps the other
  branches. Audited as `access.granted` / `access.revoked`.
- It does not use the older `has_permission` (department/designation rules and
  company feature switches), which no page uses and would deny everything for
  companies without feature rows.
- **No migration.** A first attempt seeded the permissions and features in a
  data migration; it broke 19 older `access_control` tests that create the
  `leave`/`payroll` features themselves (duplicate feature code). It was removed
  before pushing. Checking needs no rows; granting creates the permission row
  (and its feature, if missing) on first use and keeps any existing row with the
  same code.
- No page changes yet; nothing visible until parts 2–6.
- Tests: new `access_control/tests_branch_access.py` (permission row created on
  first grant; owner everywhere, manager own branches, HR company-wide leave,
  employee nothing, branch filter, unknown code; manager grants/removes in own
  branch only with audit rows; a grantee hands on only what they hold, never to
  themselves, HR cannot grant; grants add branches, removal keeps the rest).
  **Full suite 960 tests OK** on fresh databases after removing the migration.
- No `.env.example` changes or dependencies.
- No migrations, dependencies or `.env.example` changes.

## A12 part 2 done — Organisation → Access page — 2026-09-15 (Claude, Ajay's session, office)

- **Where:** sidebar → **Organisation → Access** (owner and company admin);
  a **branch manager** gets **Access** under "My branches" in their sidebar.
  `/organization/access/` (`organization:access`) and
  `/organization/access/<employee>/` (`organization:access_person`).
- **The list:** the people placed now in the branches where you may give
  access (owner/admin: every branch; branch manager: their own). Server-side
  table (A15 helper) with a Branch filter; columns Code, Employee, Branch,
  Login (Employee / Branch manager / HR / Owner / Company administrator, or no
  login; disabled shown), Access (what their role gives, in words, plus every
  permission given by hand with its branches), Manage.
- **Manage (one person):** a grid of the 11 branch permissions × your
  branches. Tick to give, untick to remove, optional reason, **Save access** →
  `grant_access` / `revoke_access` per permission (audited `access.granted` /
  `access.revoked`). Only cells you may change are live (you hold that
  permission there and may give access there); the rest are greyed, and a
  crafted post for another branch or permission is ignored. Your own access and
  an owner/admin (who has everything) cannot be changed here. Role-given
  access is shown in words above the grid ("Branch manager: every permission
  in Head Office, automatically"; "HR: views and records leave and decides
  overtime in every branch").
- **Login card:** someone without a login can be given one right there —
  email and password → an **Employee** login. Allowed for the owner/admin and
  for anyone with "Create and manage logins" in that person's branch (a branch
  manager has it automatically). `give_login` now checks exactly that;
  **making someone a branch manager stays owner/admin only** (on Edit
  employee, unchanged). Other login actions (role, password, disable) stay on
  Edit employee for the owner/admin — part 4 brings the Employees area to
  branches.
- **The gate (A6):** a branch-manager login may now open these two pages
  (`BRANCH_MANAGER_VIEWS` in `common/middleware.py`); every other company page
  still sends them to My account until part 3. HR (no "give access") gets 403.
- Tests: `organization/tests_access_page.py` (13): owner sees everyone, manager
  only their branch; HR refused, Employee login gated; gate opens only these
  pages; granted access shown; manager ticks/unticks in their branch (audited);
  crafted tick for another branch ignored; another branch's person refused;
  own access read-only; owner grants across branches; tick parsing; manager
  creates an Employee login in their branch, not in another branch, and cannot
  make a branch manager. organization + access_control + base_template +
  common: **270 tests OK**.
- No migration, dependency or `.env.example` change.

## A12 part 3 done — company pages open by permission — 2026-09-15 (Claude, Ajay's session, office)

- **One list decides it:** `access_control/page_access.py` →
  `BRANCH_PAGES = {view name: permission}`. An Employee or Branch-manager login
  may open a company page **only if it is listed there and they hold its
  permission in at least one branch**. The gate (`SelfServiceGate`) asks
  `may_open` instead of part 2's fixed list, so a person given "Give access to
  others" (not only a branch manager) now reaches the Access page too.
- **Only branch-scoped pages are listed.** Today: the Access pages. A company
  page that still shows every branch (Employees, Leave, Overtime, Salary) is
  **not** listed yet, so nobody sees another branch's data in between; parts
  4–6 add their pages to `BRANCH_PAGES` as they limit them to the viewer's
  branches, and the sidebar and My account follow automatically.
- **Sidebar:** an Employee/Branch-manager login gets a **Company** section
  under Me / My branches, built by `company_menus(..., allowed=...)` from the
  same list (same grouped menus as the company sidebar). Owner, admin and HR
  keep the company sidebar unchanged.
- **Dashboard → "Your branches" card** on My account (their home page), for a
  branch manager or anyone given access: the branches, and per permission
  held, counts limited to them — people placed in your branches and in the
  office now (`employees.view`), leave waiting for your approval (branch
  manager, links to the Approval inbox) — plus **Give access** when they may.
  The owner/admin company dashboard is unchanged.
- Tests (6 more in `organization/tests_access_page.py`): a person given
  "Give access" reaches the Access page and sees the Company menu, other
  company pages still redirect; without access nothing opens and no menu or
  card; branch manager sees the menu and the card, salary still closed; a grant
  without a page shows the card only; owner keeps the company sidebar;
  `may_open` needs a listed page and its permission. Affected apps
  (organization, access_control, base_template, common, leaves): **314 OK**;
  full suite run before pushing.
- No migration, dependency or `.env.example` change.

## A12 part 4 done — the Employees area by branch — 2026-09-15 (Claude, Ajay's session, office)

Owner and company admin: unchanged (every employee, every card). For a branch
manager or a person given access, each part follows its permission **in the
employee's branch** (a branch manager has every permission in their branches):

| Page / card | Needs | Notes |
|---|---|---|
| **Employees list** (`employee_list`) | View employees | Only people whose latest placement is in those branches. **Base rate** only where they may view salaries (and the rate column is not sortable for them, so pay order is not revealed). Name links to Edit (the employee page is the company's); **Edit** and **Create employee** only where they may edit. The live "Now" refresh stays company-only (Nihal's endpoint is not branch-limited yet; the column shows the state when the page was opened). |
| **Create employee** | Create and edit employees **and** Prepare salary | Only their branches in the Branch list, reporting managers from their branches; the department/designation lookups answer only for their branches. A new person comes with their pay, so salary access for that branch is needed too. |
| Edit → **Details**, **Placement** | Create and edit employees | A placement can only move to a branch where they may edit people. |
| Edit → **Salary** | Prepare salary | Otherwise read-only: the rate shows only with View salaries ("Has a salary" otherwise). |
| Edit → **Login** | Create and manage logins | Create, new password, disable/enable an **Employee** login. Making someone a branch manager, and a branch manager's own login, stay with the owner/admin. |
| Edit → **Shift** (own shift) | Owner/admin | Shifts are the company's Shifts area. |
| Employee page, End employment (N6) | Owner/admin | Nihal's; covered by the part 7 note. |

- Added to `BRANCH_PAGES`: `employee_list`, `organization:employee_create`,
  `organization:employee_edit`, and the two department/designation lookups. The
  sidebar (Employees menu) and My account ("People placed in your branches"
  now links to the list) follow.
- Services check the same rules (a crafted post is refused):
  `employee_edit_services.get_employee_for_edit(code=...)` (no code = the old
  owner/admin rule, still used by Nihal's pages), `card_permissions`,
  `change_placement` (target branch), `change_salary` (`salary.prepare`),
  `employee_login._existing(company_only=...)` (password and disable/enable
  for Employee logins in the branch), `employee_views._creator`.
- A company admin restricted to some branches (a platform-side setting) is
  still refused the Employees list, exactly as before — nothing was widened.
- Tests: `organization/tests_branch_employees.py` (16): manager's list with pay
  and actions; view-only hides pay and editing; another branch's access shows
  that branch; owner unchanged; HR 403 and plain employee gated; manager edits
  details, another branch 403; placement to another branch refused; pay
  follows prepare-salary (edit-only sees no rate, salary post 403); shift and
  role stay company; manager resets and disables an Employee login, not
  another manager's, not in another branch; create in own branch only;
  lookups stay in branch; edit-only cannot create. **Full suite run before
  pushing.**
- No migration, dependency or `.env.example` change.


## Company profile and branding — 2026-09-16 (Claude, Ajay's session)

Asked for quickly (Ajay, 2026-09-16): a small company profile, the logo shown
in the panel, and a footer credit.

- **Organisation → Company profile** (`organization:company_profile`, owner and
  company admin only): company name (required), registered name, contact
  person, email, phone, address and a logo.
  `organization/company_profile.py` holds the form and the audited write
  (`company.profile_updated`); `Company` gains one field, `contact_person`
  (migration `tenants.0006`, `db_default=""` so the migration tests' historical
  inserts still work). Every other Company column stays with the platform.
- **The logo is the panel's brand.** All three sidebars share
  `base_template/includes/brand.html`: the company's logo when there is one,
  otherwise `base_template/static/base_template/img/logo.png` — replace that
  file to change the default. CSS caps it at 120x28 px with `object-fit:
  contain`, so a 1000x800 upload is scaled, never stretched, and cannot push
  the sidebar about.
- **Uploads:** `MEDIA_URL` / `MEDIA_ROOT` (`media/` under the project) are new;
  Django serves them only while DEBUG is on. **A server needs an Nginx
  `location /media/` pointing at `/opt/attendance/media/`** — see
  [DEPLOYMENT.md](DEPLOYMENT.md). Logos are limited to PNG/JPG/WEBP/SVG and
  2 MB, checked in the form and again in the service.
- **Footer on every page:** "Designed and developed by IGL Web Ltd.", linking
  https://iglweb.com/web/.
- Tests: `organization/tests_company_profile.py` (6). No `.env.example` change.

## N10, N11 and Nihal's docs merged — 2026-09-15 (Claude, Ajay's session, office)

- Merged on `merge/nihal-n10-n11` from main `da4817e`: N10
  (`feature/n10-branch-attendance`) and N11 (`feature/n11-scan-requests`)
  cleanly; `docs/n-series-decisions` with one conflict in this file (both
  sections kept — its "waiting for the part 7 note" text is now history: N10
  built it).
- Reviewed: N10 follows [A12_BRANCH_ACCESS_FOR_NIHAL.md](A12_BRANCH_ACCESS_FOR_NIHAL.md)
  (`attendance/access.py`; only Employee and Branch-manager logins are limited;
  fixes re-checked in the services; the day's branch from its record or the
  placement at midday). The two new codes are in `BRANCH_PERMISSIONS` and
  `HR_COMPANY_WIDE`; nine views in `BRANCH_PAGES`; devices unchanged.
- Sidebar: Attendance → **Missed scans** (`attendance:missed_scan_list`,
  Decide keeps it selected); My account → **Missed scans** (`me:missed_scans`,
  Report and Withdraw keep it selected).
- **Migration:** `attendance.0005_missed_scan_requests` — Ajay runs
  `python manage.py migrate` and restarts the server.
- **For Ajay to confirm** (Nihal's questions; Claude's recommendation in
  brackets): the 2 h still-in / 2 h early check-out / break + 60 min limits
  stay constants [yes, until a company needs them different]; a branch
  manager's own missed scan is decided by HR or the company [yes]; unusual days
  go to review and never change pay [yes].
- Full suite run (no `--keepdb`, new migration) before pushing.
- No dependency or `.env.example` change.

## A16: SenseFace 3A data retrieval — 2026-09-15 (Claude, Ajay's session, office)

Tested live on the 3A (company Ajay, "Main Entrance", VGU6262600120; firmware
ZAM70-NF28VA-3.3.12-OCM-2535, PushVersion 3.1.2S-20250616, announces
`pushver=2.4.1`, `DeviceType=att`; options: 2 users, 2 fingerprints, 0 faces).
It speaks the **attendance push 2.x** command dialect whatever the Push
protocol setting says:

| Sent | Answer |
|---|---|
| `DATA QUERY tablename=user,fielddesc=*,filter=*` (3.x, what "Refresh user list" sent) | `Return=-1004` |
| `DATA QUERY USERINFO PIN=1` | `Return=0`; `USER PIN=1 Name=NIHAL Pri=14 Card=196793 …` in the operation log, and his `BIODATA` fingerprint row |
| `DATA QUERY USERINFO` | `Return=0`; **every** user (1 NIHAL, 2 RYHAN) and their fingerprint rows |
| `DATA QUERY ATTLOG StartTime=2026-09-01 00:00:00⇥EndTime=2026-09-15 23:59:59` | `Return=0`; all 10 records: 7 new (six from before it first connected, and one from 15:35 today) and the 3 we held (stored as confirmed duplicates, not counted twice) |

The 15:35 scan was made while the ngrok tunnel was down and was **not** sent
when the device reconnected — a 2.x device can silently keep scans back.

What changed (branch `feature/a16-3a-users`):

- `devices/services/protocol.py` (new): which dialect a device speaks —
  `PUSH3` (2A) or `ATT2` (3A) — from what it announces at the handshake (now
  kept in `settings["announced"]`), or, before that, from the 2.x operation-log
  lines it has sent.
- Commands follow the dialect: on the 3A, **Refresh user list** sends `DATA
  QUERY USERINFO`; **Fetch attendance history (last 31 days)** sends `DATA QUERY
  ATTLOG`; the 3.x-only requests are not offered. The 2A is unchanged.
- **Catch-up:** asked again, the same ATTLOG request returned nothing — the
  3A sends only records it has not handed over, so asking is cheap and never
  repeats. A 2.x device is therefore asked (last 31 days) on its first command
  poll after 2 minutes of silence (or its first ever), and once an hour
  regardless. Tracked as `last_poll_at` / `last_catch_up_at` in the sync
  state, because uploads also stamp `last_seen_at`.
- **Device users** reads both formats (3.x `user pin=` / `biodata`; 2.x `USER
  PIN=` / `BIODATA` / `FP` / `FACE`), and also lists numbers that only scanned
  ("seen in scans; name not received yet") so they can be mapped. A "Refresh
  user list" button is on the page. `table=BIODATA` uploads are filed as
  enrollment data.
- Push/Remove user are refused for a 2.x device until its write form is
  measured (the 3.x forms were measured on the 2A, where a wrong key deleted
  every user).
- Tests: `devices/tests_att2.py` (13, from the captured payloads);
  `devices/tests_server_tables.py` counts the scan-only numbers in the roster.
  No migration or `.env.example` change.

**Advice:** set the 3A's Edit device → Push protocol back to "As the device
announces": everything above worked in 2.x; the 3.x answer (set 2026-09-14)
was never shown to help this device.

## A12 part 7 done — the note for Nihal — 2026-09-15 (Claude, Ajay's session, office)

A12 is complete on Ajay's side. The note
[A12_BRANCH_ACCESS_FOR_NIHAL.md](A12_BRANCH_ACCESS_FOR_NIHAL.md) tells Nihal
how to branch-limit his pages, with the same rules and tools as parts 4–6:

- Two new permission codes, added by Nihal together with the pages that use
  them: **View attendance** (`attendance.view`) and **Fix attendance days**
  (`attendance.fix`), both company-wide for HR as today.
- Page by page: Daily list, Calendar, day panel, Now (`employees.view` or
  `attendance.view`), Days to review / Fix a day / Withdraw (`attendance.fix`,
  services re-check), employee page (`employees.view`; salary history only
  with View salaries) and End employment (`employees.edit`; not a branch
  manager's). **Devices stay owner/admin** (unrestricted), as before.
- Company logins keep today's behaviour; only Employee and Branch-manager
  logins are limited. Tests with `leaves/tests_branch_access.TwoBranchCase`.

Code in this part (Ajay's pages only): links to Nihal's pages now follow
`BRANCH_PAGES` instead of "company-wide", so they switch on by themselves
when he lists a page — the Employees list's live "Now" refresh and its link
to the employee page, and the attendance-calendar link on the overtime day
page and the payslip. Owner/admin: unchanged. Tests: one in
`organization/tests_branch_employees.py`, one in
`payroll/tests_branch_overtime.py` (links appear only once the page is
listed). No migration, dependency or `.env.example` change.

After Nihal's branch: retire My branches → Branch attendance
(`me:branch_attendance`) in favour of the scoped Daily list, if Ajay agrees.

## A12 part 6 done — salary by branch — 2026-09-15 (Claude, Ajay's session, office)

One salary run per month for the company, as before; each branch prepares
its own people inside it, and the owner or company admin finalises the month
once. Owner and company admin: unchanged (Generate rebuilds the whole month;
Finalise, Undo finalise, Salary settings, penalty rules and Waive stay theirs
only). HR and the other company roles keep Salary by month as before.

| Page / action | Needs (in the branch) | What they get |
|---|---|---|
| **Salary by month** (`payroll:payroll_home`) | View salaries **or** Prepare salary | Payslips of people placed in their branches at the month's end; totals for those only ("in your branches"). No Finalise, Undo finalise or Salary settings. The Overtime button follows their overtime access. |
| **Generate … for your branches** | Prepare salary | Rebuilds only their people's payslips (their attendance is brought up to date first); every other branch's draft payslip stays exactly as it was. Refused once the month is finalised. |
| **Payslip** | View salaries or Prepare salary | Their branches only (another branch's payslip: 403). Waive a penalty: owner/admin only. Calendar view hidden (attendance is Nihal's). |
| **Bonus / deduction lines** (add, remove) | Prepare salary | Only on a payslip in that branch; that branch is regenerated. |

- How it works: `payroll.services.generate_payroll(..., branch_ids=...)`.
  With branch ids it rebuilds the people whose last attendance day of the
  month is in those branches, deletes only their old payslips and proposed
  penalties, and recomputes the run's totals from all payslips in it.
  "Skipped, no salary set" is kept per person so a branch pass does not lose
  the others. Owner/admin pass no branch ids: the whole month, as before.
- Finalise is still refused while overtime was decided after the payslip it
  belongs to was generated — now checked **per payslip** (each keeps its own
  generated time), so a branch regenerating cannot hide another branch's
  newer overtime decision (`payroll.overtime.decided_after(company, run)`).
- Added to `BRANCH_PAGES`: `payroll:payroll_home`, `payroll:payroll_generate`,
  `payroll:payslip`, `payroll:payslip_adjustment_add`,
  `payroll:payslip_adjustment_remove`. The sidebar "Company" section shows
  Salary → Salary by month (and Overtime from part 5).
- Services check the same rules: `payroll.services.salary_branches`,
  `_preparer` (lines), `generate_payroll` (each branch needs Prepare salary).
- Tests: `payroll/tests_branch_salary.py` (8): a branch generates its own
  people and leaves the rest (other payslips keep their ids, totals cover the
  month); only branches where they may prepare; finalising stays with the
  company (service and gate); overtime decided after a payslip still blocks
  finalising after another branch regenerates; manager's page, generate
  button and 403 on another branch's payslip; view-only sees payslips
  without Generate or Add line (POST 403); prepare in another branch adds a
  line there and regenerates only that branch; owner and HR unchanged.
  `organization/tests_access_page.py` (two) and `organization/tests_logins.py`
  (one): checks that used Salary as "not yet open" now use Salary settings.
  **Full suite run before pushing** (1119 OK).
- No migration, dependency or `.env.example` change.

## A12 part 5 done — leave and overtime by branch — 2026-09-15 (Claude, Ajay's session, office)

Owner, company admin and HR: unchanged (every branch's leave and overtime, as
before; the other company roles keep the Leave list as before). For a branch
manager or a person given access, each page follows its permission **in the
branch** (a branch manager has every permission in their branches):

| Page | Needs | Notes |
|---|---|---|
| **Leave list** (`leaves:leave_list`) | View leave **or** Record and cancel leave | Only leave asked for in those branches. **Cancel** only where they may record, for every day of the leave. Leave types button hidden (leave types stay the company's). |
| **Record leave** | Record and cancel leave | Employee list: people placed in those branches. The service refuses a day that falls in a placement outside them, and their own leave, as before. |
| **Cancel leave** | Record and cancel leave | Every branch the leave touches must be theirs. |
| **Approval inbox** (My account → My branches) | Approve leave requests | Before: branch managers only. Now also anyone given this access, for requests from people (not branch managers) in those branches, never their own. The company inbox is unchanged (branch managers' requests and branches with no branch manager). Sidebar link and the "Leave waiting for your approval" count on My account follow. |
| **Overtime list** (`payroll:overtime_list`) | View overtime **or** Decide overtime | Days in those branches; Decide/Change only where they may decide, otherwise **View**. "Salary for …" hidden until part 6. |
| **Overtime day** (`payroll:overtime_decide`) | View or Decide | View-only: the day and its scans, no form, no Undo; a POST is refused. "Open in the attendance calendar" hidden (Nihal's page). |
| **Undo an overtime decision** | Decide overtime | |

- Added to `BRANCH_PAGES` (which now accepts "any of these codes"):
  `leaves:leave_list`, `leaves:leave_record`, `leaves:leave_cancel`,
  `payroll:overtime_list`, `payroll:overtime_decide`, `payroll:overtime_undo`.
  The sidebar "Company" section shows Leave (Leave list, Record leave) and
  Salary → Overtime accordingly.
- Services check the same rules: `leaves.services.recorder_branches` (record,
  cancel), `leaves.workflow.approve_branches` / `reviewer` / `reviewable`
  (inbox and decisions), `payroll.overtime.overtime_scope` (list, day, decide,
  undo; replaces `require_overtime_approver`). New helper
  `access_control.branch_access.branches_for_any`.
- Not changed: leave types, the company approval inbox, salary pages
  (part 6), attendance pages (Nihal, part 7 note).
- Tests: `leaves/tests_branch_access.py` (9) and
  `payroll/tests_branch_overtime.py` (5): manager sees/cancels/decides own
  branch only; view-only sees without cancel, record or decide (POST and Undo
  403); access in another branch stays there (crafted record/cancel/decide
  refused); an employee without access is kept out by the gate; owner and HR
  unchanged; a person given "Approve leave" gets the inbox, the My account
  count and decides; the branch manager still decides. **Full suite run
  before pushing.**
- No migration, dependency or `.env.example` change.

## N5 and N8 merged — 2026-09-15 (Claude, Ajay's session, office)

- Merged on `merge/nihal-n5-n8` from main `d353b89`: N5 (`feature/n5-corrections`)
  cleanly, then N8 (`feature/n8-device-connection`) with the one expected
  conflict in this file (both sections kept). `attendance/services.py` keeps
  both the overtime hook (A9) and N5's corrections.
- Sidebar: Attendance → **Days to review** (`attendance:attendance_review`; Fix a
  day and Withdraw keep it selected).
- **Migration:** `attendance.0004_corrections` — Ajay runs
  `python manage.py migrate` and restarts the server.
- Nihal asked whether his pages (Days to review, Fix a day, device pages) should
  get permission codes now. Answer: **no — wait for the A12 part 7 note.** They
  stay owner/admin (plus whatever his own checks allow) and are not added to
  `access_control/page_access.py` `BRANCH_PAGES`, because a page joins that
  list only once it shows nothing outside the viewer's branches.
- Full suite run (no `--keepdb`, new migration) before pushing.
- No dependency or `.env.example` change.

## 2026-09-15 — N9: Nihal's lists on the shared server-side table (Nihal)

Branch `feature/n9-server-side-tables`, from fresh `main` at `d8de2d6`.
**No migration, no dependency, no `.env.example` change.** Built only N9; N5,
N6 and N8 are on their own branches, pushed 2026-09-14 and not merged yet.

Every list below now counts, searches, sorts and pages on the server through
`base_template/tables.py` (`paginate` + `render`, `data-server-table`,
`base_template/includes/table_pagination.html`), with its existing filters,
links, badges and Paper/Ink styling. The page's own filters narrow the set
first; the table's search, order and page run on that set, so
`recordsTotal` / `recordsFiltered` are real counts, never the rows on screen.

| Page | Path | Filters kept | Table search | Sortable columns |
|---|---|---|---|---|
| Attendance → Daily list | `/attendance/` | month, year, **Branch (new)**, employee, status | employee name and code, branch, status, note | all 10 |
| Devices | `/devices/` | name/serial, status, branch | name, serial, branch, model, status | device, serial, branch, status, last seen |
| Enrollments | `/devices/enrollments/` | search, device | employee, device, user number | employee, device, user number, attendance, grant, period |
| Message log | `/devices/messages/` | device, status | device, type, status | received, device, type, records, status |
| Punches | `/devices/punches/` | search, device, authorisation, duplicate status | user number, employee, device, method, outcome | punched, employee, device, method, outcome |
| Unresolved queue | `/devices/unresolved/` | reason | user number, employee, device, reason | punched, identity, device, reason |
| Device users | `/devices/<id>/users/` | search, mapped / not mapped | user id, name, role, card, employee | all but "Write to device" |

**Daily list:** a Branch column was added beside Employee (the filter is easier
to trust when the rows show the branch), and the employee code shows under the
name and is searchable. The badge reads "N of M days" when a filter is on. The
query keeps `branch` on every row, which is the field
`access_control.branch_access.scope_queryset(..., field="branch")` will narrow
on — not wired in, per Ajay, until the A12 part 7 note.

**Device users is not a database table.** The roster is rebuilt from the
uploads the device sent (`devices/services/device_roster.py`); there is nothing
to count or search in SQL without a new model. So `devices/views/ui.py
_paginate_rows` does the same work on the server over the **whole** roster —
never client-side only — with the helper's exact contract: the same request
parameters, 10–100 rows, a literal 200-character search, server-owned sort
keys, and the same table description registered on the request, so
`render`, the pager include and `tables.js` are unchanged. User ids sort as
numbers. **Decided by Ajay, 2026-09-15:** keep it this way — paged on the
server from the device's uploads, no new table.

**Also changed:** the device base template loads its own DataTables copy and
the old `devices.js` enhancer only when a page has no server table (base.html
loads DataTables for those), so no page loads it twice; `data-enhance`, the
"filter this page" notes and the old `_paginate` helper are gone. The
screen-reader-only "Actions" headers became visible text: `.sr-only` is
absolutely positioned and escaped the table's scroll box, widening the page on
a phone. The Punches and Unresolved tables had an unlabelled action column the
first column list missed; the helper's "headers and row cells must have the
same length" guard caught it in the tests.

**Merge note for Ajay:** `feature/n8-device-connection` also edits
`devices/templates/devices/device_list.html` (Connection column),
`devices/templates/devices/base.html` (connection script) and the device list
view. Both changes are small and side by side; expect a textual conflict there
if N8 is merged after N9, not a logical one.

**Checked** on D Company's real data through the browser with the live
DataTables draws: Daily list 87 September days, search "Nihal" 14 of 87,
Late sorted numerically, Branch filter narrowing to 0 for an office with no
attendance; Punches 240, search 63 of 240; Messages 595; Unresolved 191;
Device users 4, user ids sorted as numbers. No page overflow at 1440, 768 or
375 on any of the seven lists.

**Tests:** 10 in `attendance/tests_daily_list.py`, 12 in
`devices/tests_server_tables.py` — whole-set counts, database search across
pages, every orderable column, hostile parameters, tenant boundaries, the
counted HTML pager keeping filters, headers kept on an empty search, escaping,
and a single DataTables script per device page.

**Navigation (A14):** no new pages. The Daily list and the device lists keep
their paths.

## 2026-09-14 — N6: an employee's history, and ending employment (Nihal)

Branch `feature/n6-employee-detail`, from clean `main` (after A6b). **No
migration, no new environment variable.** Owner or company administrator only
— the same check as Edit employee (`get_employee_for_edit`).

**Pages** — for A14's menu these hang off Employees rather than needing their
own entries:

- **Employee** — `/organization/employees/<id>/` (`organization:employee_detail`),
  reached by clicking a name on the Employees list. Read-only; every change
  still happens on Edit. *Now* (status, joined, salary, login, contact) beside
  *Attendance this month* (counts + Calendar view); *Placement history* and
  *Salary history* with first and last day in company time and a "Now" badge
  on the current row; *Devices* (user number, dates, Assigned / Recognised
  only / Attendance off / Ended); *Shift of their own*; *Recent changes* from
  the audit log. Actions: End employment, Edit.
- **End employment** — `/organization/employees/<id>/end/`
  (`organization:employee_end`). Says plainly what will happen, then asks for
  the last working day, resigned / terminated / retired, a note, and two
  ticked-by-default options shown only when they apply: disable their login,
  end their device enrollments.

**What ending does** (`organization/employee_detail_services.end_employment`,
wrapping `employees.services.terminate_employee`, which was not changed):

- Placement and salary end at the **midnight after the last working day**
  (company time) — how every other end is stored.
- `leaving_date` is set to the **last working day** itself. The existing
  service sets it from the end instant, which is the day after; attendance
  reads `leaving_date` as the last day worked, so without this correction the
  day after leaving would still get an (absent) record. A test covers it.
- Attendance is recalculated from the last day to today, which removes any day
  already written after it. A finalised month is never touched.
- Device enrollments (if ticked) end at the same instant, each with an audit
  record whose `before_data` holds the old `effective_to`, as the device policy
  history requires. The person stays on the terminals until removed on the
  device's users page — ending does not delete biometrics.
- The login (if ticked) is suspended through `employee_login.set_login_active`,
  with its own audit record.
- One `employee.employment_ended` audit record with before/after.

**Refused:** somebody who has already left; a last working day in the future
(record it once it has happened — until then they are at work and on
attendance); a day before the current placement began; a day before the end of
a finalised salary month; a salary that starts after the last day (closing it
would break the salary row's end-after-start rule — change the salary first).

**Not done here, on purpose:** part-month salary proration for a leaver
(A11 — the screen says so); ending leave booked after the last day (leaves/ is
Ajay's); re-hiring; notice periods recorded ahead of time.

Shared file touched: `employee_list.html` (the name links to the page),
checked identical to `origin/main` first. `organization/urls.py` gained the two
routes next to `employee_edit`.

**Tests:** 20 in `organization/tests_employee_detail.py`. Checked on D
Company's Nihal at 1440, 768 and 375 px (GET only — nobody's employment was
ended); below 1100 px the history tables drop the Note column so nothing
scrolls, and on a phone the placement table scrolls inside its own box.

## 2026-09-14 — N5: fixing a day by hand, and the days to review (Nihal)

Branch `feature/n5-corrections`, from clean `main` (N4, N7 and A9 merged).
**One migration: `attendance/0004_corrections`** — a new table
`payroll_attendance_correction`, a nullable `PunchAllocation.attendance_correction`
column, and a check that every allocation has exactly one source (a punch or a
correction). Checked first: no existing allocation lacked a punch.

**The design rule.** Attendance recalculates itself whenever a punch arrives,
so a fix made by editing the record would be gone by the next scan. A
correction is an *input* instead: `recalculate()` reads the corrections in
force for every employee-day it builds (`attendance/corrections.py`), exactly
as it reads A9's `OvertimeDecision`. Corrections are keyed by employee and
date, not by record, because a record can be removed and written again. A
PunchEvent is never edited.

**Three fixes** (`attendance/correction_services.py`), each audited, each with
a required reason, each refused inside a finalised salary month, and each
applied straight away by owner, company administrator or HR (the same people
as A9's overtime):

- **Add a missed scan** — joins the day's stream and is labelled by pairing
  like any other scan (a scan added between two real ones becomes a break,
  not a check-out). After recalculating, the service checks the scan really
  landed on that day: a time that belongs to another day's window, or one
  seconds after a real scan (swallowed as a repeat), is refused and nothing
  is kept. The timeline shows it as "Added by hand". The Now badge sees it too.
- **Change the status** — present, half day or absent, on a finished working
  day. Scans and minutes stay as measured; status, payable fraction and a
  "Marked … by hand: reason" note follow the correction, and the day is
  reviewed. One in force per day: a new one supersedes the last. Leave,
  holidays and weekly offs are refused — they have their own pages.
- **Accept as it is** — "the rule's check-out is right". Kept only while the
  day still needs review for that same reason; a different reason turning up
  later is a new question.

**Withdraw** takes any correction back and rebuilds the day without it. The
correction keeps what it did (before/after), with the withdrawal's own
before/after beside it. Every action writes an AuditLog row with the day
before and after.

Each action first brings the day up to date, then judges it — a day may have
closed, or never been written, since anybody last looked. (A test caught
this: the status change refused a closed day nobody had opened yet.)

**Pages** — for A14's menu, under **Attendance**:

- **Days to review** — `/attendance/review/` (`attendance:attendance_review`).
  Closed days waiting for a person: "Check-out by rule" rows link to Fix this
  day; "Overtime, no check-out" rows link to A9's approval,
  `payroll:overtime_decide record.pk`. Days in a finalised salary month are
  not listed. The page refreshes this month and last before listing.
- **Fix a day** — `/attendance/day/<employee_id>/<date>/fix/`
  (`attendance:attendance_day_fix`). The day now (status, review banner,
  totals, every scan), Add a missed scan, Change the status, Accept the rule's
  check-out (only on that kind of day), and Changes made to this day with
  Withdraw. An open overtime day's banner links to A9 instead of offering
  Accept. Reached from Days to review and from a **Fix this day** link in the
  calendar's day panel (shown only to people who may fix, and never for
  another company's employee).

The day panel's empty state no longer says "Calculate the month" — there has
been no Calculate button since N1b.

**Brought up to date with main on 2026-09-15** (after N9, `60792e9`):
`attendance/views.py` conflicted on imports only (N9's `paginate`/`render`
beside N5's forms and services). Main's half-day leave branch in `_write_day`
merged beside the corrections untouched. One interaction git could not see:
a half-day leave day somebody came in on reads "present", but `_write_day`
applies a status correction only to an ordinary working day — so a status
change there would have been stored and silently done nothing. *Change the
status* is now refused on any day with leave recorded ("Change or cancel the
leave on the Leave page"), and the Fix a day page does not offer it; tested,
and the test fails without the guard.

**Deviations from MODEL_FIELD_DICTIONARY §35**, deliberately: keyed by
`employee` + `work_date` instead of an `attendance_record` FK (records are
derived and can be rewritten); types are `add_scan` / `change_status` /
`accept_review` (only what is built); statuses are `applied` / `superseded` /
`withdrawn` (no request-and-approve workflow yet); no attachment. The
dictionary's `approved_by`, `approved_at`, `decision_note`, `before_snapshot`,
`after_snapshot` and `proposed_event_at` names are kept.

**Tests:** 29 in `attendance/tests_corrections.py`; two calendar fixtures now
give their allocations a real punch (required by the new check). Checked on
D Company's real review days (Nihal's rule check-outs on 9, 10 and 13 Sep and
open overtime on 12 Sep; Ajay and Moin on 13 Sep) at 1440, 768 and 375 —
the review list folds times under the date below 1100 px and becomes one card
per day on a phone, so the action is never behind a sideways scroll. None of
those days was corrected.

**No new environment variable.**

## 2026-09-14 — N8: is the device connected, and a connection test (Nihal)

Branch `feature/n8-device-connection`, from clean `main` (after A16). **No
migration, no new environment variable.**

A terminal cannot be pinged — it calls the server. So "connected" means one
thing: it has checked in recently. `last_seen_at` is stamped on every
authenticated device request, including the idle `getrequest` poll, and
`devices/services/connection.py` reads it against the device's push interval:

| Badge | When |
|---|---|
| **Connected** (green) | seen within 6 × the push interval, and never tighter than 2 minutes — real polling drifts from the setting (Nihal's SenseFace is set to 20 s; the server log shows it polling every 5–10 s) |
| **Last seen … ago** (amber) | quiet for up to 15 minutes |
| **Not connected** (red) | quiet for longer, or never checked in |
| Retired / Suspended (grey) | not judged |

**1. Live badge** — on the device list (new *Connection* column replacing
*Last seen*) and on the device page. Correct when served; `connection.js`
refreshes it every 20 s from `devices:device_connections` (JSON, this
company's devices only; unknown or foreign ids are ignored).

**2. Connection test** — a *Connection* card at the top of the device page.
A test is a start time; it passes when a check-in arrives after it.
*Register device* and *Edit* now land on the device page with a test already
running (`?test=<start>#connection`), because whether the terminal can reach
the server is the next thing anybody wants to know. *Test connection* starts
one by hand; ticking *Also send a harmless command* queues
`query_options` (the device re-sends its own settings) and follows it by its
command id: queued → picked up → answered (with the return code if it
failed). The panel polls every 3 s and stops once everything has answered.
After max(6 × interval, 2 minutes) with no check-in it says what to check —
the server address and port to type on the terminal (from
`setup_instructions`), the registered serial number, the network, the comm
key — and keeps listening. The start time is URL-encoded: an ISO time ends in
"+00:00", and an unencoded "+" reads back as a space, which silently showed no
test at all (caught by a test).

**3. Alert** — *"N devices have stopped checking in"*, listing each with its
branch and how long it has been quiet, on the device list and on the dashboard
(dashboard only for people who may manage devices). Only **active** devices
that are *Not connected*: a pending device is being set up, not broken.

Shared files touched: `base_template/views.py` and `dashboard.html` (the alert),
both identical to `origin/main` before the change.

**Found on the way:** the device list's header had a screen-reader-only
"Actions" label. `.sr-only` is `position: absolute`, and `.table-wrap` is not
positioned, so the label escaped the table's scroll box and widened the whole
page to 684 px on a phone. Fixed on this page by making the header text
visible. **The same trap is on every table with an `.sr-only` header** (other
device lists, the attendance list). The one-line general fix is
`position: relative` on `.table-wrap` in `components.css` — a shared
design-system file, so it is left for Ajay. N5's review list had the same
problem and was fixed the same way.

**Part 4, added 2026-09-15 — no address change for a device that never
checked in.** `server_address.request_change` refuses it before anything is
written (no attempt row, no probe, no command) with *"This device has never
connected to this server. Set the server address on the terminal itself first
(COMM → Cloud Server), using the values under “Enter these on the device” on
its page. Once it has checked in, its address can be changed from here."* The
Edit form closes the Server address field for such a device with the same
words, the way it already did while a change was running. The server-address
test fixture now sets `last_seen_at` (it always meant an already-connected
device). 4 tests; removing the service guard fails the refusal test. The
branch first took main in at `d8de2d6` (only `PHASE_STATUS.md` conflicted).

**Brought up to date with main again on 2026-09-15** (after N9 and N6,
`53295f8`): `devices/views/ui.py` conflicted in the device list — N9's
`paginate` kept, each row's connection attached after it, the stopped-devices
alert kept. One interaction the merge could not show: `connection.js` polled
the device ids the page was *served* with, so after a table redraw to another
page or a search the new rows' badges would never refresh. It now reads the
ids on screen at every poll (`data-connection-for`), and the unused
`data-connection-devices` attribute is gone.

**Checked on the real terminal** (Main Entrance, NYU7251601501): a test started
before its last check-in reads *Connected at 18:44:13*; one started after it
and older than 2 minutes shows the advice. While testing, the terminal stopped
polling at 18:44:13 local with every earlier request answered 200 and the
server still up — the badge showed *Last seen … ago* as designed. Checked at
1440, 768 and 375 px.

**Tests:** 26 in `devices/tests_connection.py`, including a real `getrequest`
poll passing a test end to end, and signed-out access to every new route. A
mistake worth recording: inserting the redirect helper above
`device_register` moved its `@login_required` / `@company_user_required` onto
the helper, leaving Register unprotected. The existing tests failed at once;
the decorators are back on `device_register` and the diff against `main` shows
no decorator change on any existing view.

## 2026-09-15 — N10: branch access on attendance and the employee page (Nihal)

Branch `feature/n10-branch-attendance`, from main `fd85eda`, following
[A12_BRANCH_ACCESS_FOR_NIHAL.md](A12_BRANCH_ACCESS_FOR_NIHAL.md). No migration,
no `.env.example` change.

**Codes.** `attendance.view` ("View attendance") and `attendance.fix` ("Fix
attendance days") added to `BRANCH_PERMISSIONS`, both in `HR_COMPANY_WIDE` —
HR keeps seeing and fixing attendance everywhere, as under N5.

**Who sees what.** `attendance/access.py` holds the rules. Company logins
(owner, admin, HR, payroll manager, auditor) see attendance in every branch as
before; only an Employee or Branch-manager login is limited, to branches where
it holds `attendance.view`. Fixing needs `attendance.fix` in the **day's**
branch — the branch on the day's record, or the placement at midday that date
when no record is written yet; a day with no placement only for someone who
may fix every branch. Payroll manager and auditor still cannot fix unless given
the code.

| Page | Now |
|---|---|
| Daily list | rows, Branch and Employee filters limited to the viewer's branches |
| Calendar | picker offers people placed in those branches; asking for anyone else falls back to the first; the grid leaves out days worked in another branch |
| Day panel | 403 for a day in another branch, checked before it is recalculated; Fix link only with `attendance.fix` there |
| Now | a branch login gets only people placed where it may view employees or attendance; other ids are dropped, and none left answers `{}` (an empty list would otherwise mean everybody) |
| Days to review | `review_queue(company_id, branches)` |
| Fix a day / Withdraw | `correction_services.require_corrector(actor, company_id, employee_id, work_date)`; `add_scan`, `change_status`, `accept_review` check it, `withdraw` checks the corrected day's branch inside its lock. `CORRECTION_ROLES` is gone. Calendar link only when the viewer could open that person there |
| Employee page | `get_employee_for_edit(code="employees.view")`; salary card, salary history and salary audit events only with `salary.view` in the branch; Edit / End employment links only with `employees.edit`; Calendar link and month counts follow `attendance.view` |
| End employment | `code="employees.edit"`; a branch login cannot end someone with more than an Employee login (a branch manager) or its own employment; disabling the login needs `employees.logins` in the branch, checked before anything is written |

Two things found while testing End employment as a branch manager: the login
was disabled *after* the placement closed, so `set_login_active`'s branch check
found no placement and refused — it now runs first, in the same transaction.
And once ended the person is placed nowhere, so a branch login cannot open
their page any more; it is sent to the Employees list instead (the company
still lands on the employee page).

All nine views are in `BRANCH_PAGES`. Ajay's two "link follows BRANCH_PAGES"
tests (`organization/tests_branch_employees.py`,
`payroll/tests_branch_overtime.py`) assumed the pages were not listed yet; they
now check the link is there and goes when the entry is removed. `access_control/tests_branch_access.py` counts 13 permissions
now instead of 11.

**Tests:** 32 in `attendance/tests_branch_attendance.py` on `TwoBranchCase` —
per page: branch manager sees own branch only; a grant in the other branch
shows that branch only; without the code the gate redirects to My account;
owner and HR unchanged; crafted posts and direct service calls refused. Also:
auditor still sees the list but cannot fix; devices still closed to a branch
manager. Full suite: 1153 tests, OK after the count update. Pages checked at 1440,
768 and 375 px as the D Company owner (no branch-manager login exists in the
dev data; the limited views are covered by the tests).

## 2026-09-15 — N11: missed scans, still in after the shift, unusual days (Nihal)

Branch `feature/n11-scan-requests`, built on `feature/n10-branch-attendance`
with main `6fa80d8` (A16) merged in — **merge N10 first**. Migration
`attendance.0005_missed_scan_requests` (one new table,
`payroll_missed_scan_request`). No `.env.example` change.

Why: a fingerprint cannot tell a forgotten scan from a person who was not
there. Nihal's scenarios — walking back in behind a colleague after scanning out
for tea, forgetting to scan out, staying late without scanning — were either
silent (a short day with nothing flagged) or waited for HR to notice.

**1. Missed-scan requests.** `MissedScanRequest` (employee, attendance day, scan
time, reason, the day's branch, status, decision). My attendance has a *Missed
scans* button and the day panel a *Report a missed scan* link (`me:missed_scans`,
`me:missed_scan_report`, `me:missed_scan_withdraw`). The scan is tried against
the day when it is asked — `correction_services.check_scan_fits` runs the real
add-scan in a transaction that is always rolled back — so "that time is not part
of this day" or "you already scanned then" is said straight away. Nothing
changes until approved. *Attendance → Missed scans* (`attendance:missed_scan_list`,
`attendance:missed_scan_decide`, in `BRANCH_PAGES` under `attendance.fix`) lists
requests for the branches where the viewer may fix attendance: a branch manager
their own, HR and the company everywhere. Approving calls
`correction_services.add_scan`, the same fix as Fix a day, and links the
correction (it can be withdrawn there like any other). Rejecting needs a note
the employee reads. Nobody sees or decides their own request. Days to review
links to it with the waiting count.

**2. Still in after the shift.** `live_status.still_in_after_shift`: people whose
last scan today is an IN two hours (`STILL_IN_ALERT_MINUTES = 120`) after
today's shift ended. Shown as an alert on Days to review and on the dashboard
(beside N8's stopped-devices alert), limited to the branches where the viewer
may fix attendance; each name opens Fix a day.

**3. Unusual days to review.** `attendance.services.unusual_reason`, applied
when a working day closes and nothing else already needs review:
*checked out long before the shift end* (2 hours or more,
`EARLY_CHECK_OUT_REVIEW_MINUTES`) and *long time outside during the shift*
(more than the shift's break plus 60 minutes, counting only time inside the
scheduled shift, `LONG_OUTSIDE_REVIEW_MINUTES`). They change nothing the day
counts or pays. Fix a day explains them and offers *Accept the day as it is*
(`accept_review` now accepts these two reasons as well as the rule's check-out).
Not applied on a half-day leave, where leaving early is the point.

The three thresholds are constants for now; they can become attendance
settings if a company wants different numbers.

For Ajay: menu entries not added (sidebar is Ajay's) — suggested *Attendance →
Missed scans* (`attendance:missed_scan_list`) and *My account → Missed scans*
(`me:missed_scans`). Small edits outside Nihal's area: `base_template/me_urls.py`
(three paths), `base_template/templates/base_template/me/attendance.html` (the
button), `base_template/views.py` and `dashboard.html` (the alert).

**Tests:** 19 in `attendance/tests_missed_scans.py`. Full suite 1185 tests, OK. New pages checked at 768 and 375 px (no overflow) as the Northwind owner, whose login has an employee record; nothing was submitted in the dev data.

## 2026-09-15 — Nihal's steps after the N5/N8 merge (Nihal)

Main at `f17f054` (N5 and N8 merged, 1097 tests). `attendance.0004_corrections`
applied. Nothing left to build in the N-series.

**Settled (Ajay, 2026-09-15)** — no longer open:

- **Device users** stay paged on the server from the device's own uploads
  (`devices/views/ui.py _paginate_rows`); no roster table.
- **The company device scope** stays on Devices → Which devices count
  (`devices:attendance_rules`), not Attendance settings.

**Waiting for the A12 part 7 note:** my pages stay owner/admin and are **not**
added to `access_control/page_access.py` `BRANCH_PAGES` until then — Days to
review, Fix a day, the device pages and connection test, Daily list, Calendar,
the employee page and End employment, and the live "Now" refresh. The note will
say how to limit each to the viewer's branches with `branches_for` /
`scope_queryset`; the queries already carry `branch`.

**Still needs data, not code:** first salaries for Nihal, Dia and Ajay (D
Company, skipped by the September draft) — pay basis and rate from Nihal.

## 2026-09-19 — Departments and designations back to company level (Nihal)

Branch `feature/company-departments`, from main `54aa0a0`. Reverses the
2026-09-09 "root-owned catalogue + adoption" decision (see "Root-owned
department/designation catalogue"): **departments and designations are
company-owned again.** Data loss for the old catalogue/adoption rows was
accepted by the owner.

**Models (organization/models.py):**
- `Department` is `TenantOwned` again: branch, code, name, **head**,
  description, status, opened_on, closed_on. Unique `(branch, code)` and
  `(branch, name)`.
- `Designation` is `TenantOwned` again: department, **parent** (kept; how it
  drives access is decided later), hierarchy_level, code, name, status. Unique
  `(department, code)`, and the same-department / no-cycle `clean()`.
- `CompanyDepartment` and `CompanyDesignation` are **removed**.

**FK repoints (field names kept, logic untouched):** EmployeeAssignment,
scheduling.DepartmentShift, access_control.DepartmentPermission /
DesignationPermission and both `allowed_departments` M2Ms
(accounts.CompanyMembership, access_control.EmployeePermissionOverride),
devices.DeviceDepartment — all now point at Department / Designation. The
`DepartmentPermission.company_department` field name is deliberately kept so
the access framework code reads unchanged.

**UI:** the root catalogue screens (`organization.catalogue_*`, `platform/`
templates, and the `platform/` catalogue URL include) are removed. The company
"adoption" screens are repurposed into company department CRUD
(`organization.adoption_*`, URL names kept): create/edit a department with
code + name + head + status, and manage its designations on the edit page
(`designation_add`, `designation_status`). The `adopt_department` /
`adopt_designation` helpers keep their signatures but now create the company's
own rows.

**Migrations (destructive; data loss accepted):** organization 0005/0006,
plus AlterField in employees, scheduling, access_control, accounts, devices.
The dev DB was **not** migrated (the running server stays on main); tests build
a fresh DB. Applying on an existing DB drops the old catalogue/adoption data —
deliberate.

**Designations have their own screen (2026-09-19):** a separate Organisation → Designations page (list / create / edit / status, `organization:designation_list` etc.) instead of managing them inline on the department; a **Designations** entry was added to the company sidebar next to Departments. Each designation still belongs to one department (chosen on the form) and may name a parent in the same department.

**Not done (decide later):** using `head` (or `parent`) for delegated access —
the access *rules* were left working as-is, only their model targets swapped.

**Nav (Ajay's — needs his edit):** the platform sidebar links to the removed
catalogue screens were removed to unbreak the platform pages; the company
sidebar "Departments" item still resolves (organization:adoption_list).

**Tests:** full suite 1124, OK. Obsolete catalogue/adoption/migration tests
removed (`organization/tests_catalogue.py`, `tests_adoption.py`,
`tests_migrations.py`); `organization/tests.py` rewritten for the company-owned
model. Fresh CRUD-screen tests for the repurposed department pages are a
follow-up worth adding.



## Device data flow, part 1: templates kept and a trial write — 2026-09-19 (Claude, Ajay's session)

**Plan agreed with Nihal (2026-09-19): "enrol once, copy to the rest".** A
company's devices are one model; a person is enrolled on one, the software maps
the device user to an employee, pulls the fingerprint/face/card and writes them
to the company's other devices. Two workstreams in parallel: Nihal moves
departments and designations back to company level (organization/employees);
Ajay's session builds the device side in the `devices` app only, keyed on
(device, device user number), never an employee. Bulk employee import and the
employee-to-device-user mapping wait for Nihal's merge. The seam:
`commands.push_to_device(device, device_user_id, name, card, role,
finger_template, face_template)` — plain values.

**Decision to confirm with the owner — a biometric store on the server.** Until
now templates lived only on the devices. `DeviceUserTemplate`
(`devices_device_user_template`, migration `devices.0005`) now keeps each
fingerprint/face a device uploads, **encrypted** with `BIOMETRIC_TEMPLATE_KEY`
(Fernet, `cryptography`). No key or a wrong key: nothing is saved and the
Device users page says why (and `manage.py check` warns, `devices.W001`);
nothing is ever stored in plain text by this code. Retention: rebuildable — the
device holds the originals, so a lost key or table is recovered by pulling
again. **Open, for Ajay/the owner:** the raw upload itself (`DeviceMessage.
raw_payload_text`, append-only evidence) already holds every template the
device sent, unencrypted, since before this change. Encrypting the copy does
not remove those; redacting `tmp=` from stored raw messages would break the
"raw evidence is never rewritten" rule, so it needs an explicit decision.

What was built (branch `feature/device-templates`):

- `devices/services/templates.py`: encrypt/decrypt, `save_from_payload` (every
  `biodata`/`BIODATA` upload, called by `/iclock/cdata`), `save_from_messages`
  (from uploads already stored), `templates_for`, `as_payload`, `saved_counts`,
  `key_problem` and the system check.
- `devices/services/commands.py`: `build_template_update` (PushSDK 3.x
  `DATA UPDATE biodata Pin=…\tNo=…\tIndex=…\tValid=…\tDuress=…\tType=…\tMajorVer=…\tMinorVer=…\tFormat=…\tTmp=…`
  — **not yet measured**), `push_to_device` (all-or-nothing, user record first;
  refuses a 2.x device, another model's template, bad numbers/roles/cards; while
  `TEMPLATE_WRITE_MEASURED` is False templates go only to test user
  `TEST_USER_ID` = 99999), `COMMANDS_PER_POLL` = 5 per check-in (a
  server-address pair is never split), queued template bodies kept encrypted
  and redacted (`Tmp=…`) once handed over, `note_results` / `recent_results`
  (each `ID=&Return=` answer kept beside its command, last 50).
- Device users page: **Save fingerprints and faces**, a **Saved here** column,
  a **trial card** (copy one user's saved finger/face to test user 99999;
  **Remove test user 99999**) and **Commands and answers**.
- Tests: `devices/tests_templates.py` (28, made-up template strings — real ones
  never go in the repository); `devices` app 321 OK.
- `.env.example`: `BIOMETRIC_TEMPLATE_KEY=` (placeholder). `requirements.txt`:
  `cryptography`, `cffi`, `pycparser`.

**Measured on the office SenseFace 2A (NYU7251601501, ZAM70-NF24HA-Ver3.0.15,
Push 3.0.4S), company Amazon, 2026-09-19:** `DATA QUERY tablename=biodata,…`
→ `Return=8`: 4 fingerprints (Type 1, MajorVer 13, ~1.5 KB base64) and 4 faces
(Type 9, MajorVer 40, MinorVer 1, ~850 chars) for 445966, 445962, 445900,
445961; 445963 has none. Its user table has three rows with an **empty user
number** (uid 7, 8, 9) left by the old lowercase-write trap; they cannot be
removed by Pin and must be deleted on the terminal.

**Write commands measured on the office 2A, 2026-09-19 (Ajay at the device):**

| What | Command that works | Proof |
|---|---|---|
| User record | `DATA UPDATE user Pin=…	Name=…	CardNo=…	Privilege=…	Grp=1` | read back in the user table |
| Role | field **`Privilege`** (0/2/6/14). **`Pri` is ignored** (Return=0, nothing changes) — the old code sent `Pri`, so no role sent before this ever applied | 99999 read back `privilege=14`, opened the admin menu |
| Card | `CardNo=` in the user record | read back (Nihal 196793, Sajal 2796848) |
| Fingerprint | `DATA UPDATE biodata Pin=…	No=6	Index=0	Valid=1	Duress=0	Type=1	MajorVer=13	MinorVer=0	Format=0	Tmp=…` | Nihal's saved finger written to 99999 (Nihal deleted from the device first): the device identified him as 99999 |
| Door permission | `DATA UPDATE userauthorize Pin=…	AuthorizeTimezoneId=1	AuthorizeDoorId=1` | without it: "Invalid time period" (rtlog event 23); after it the userauthorize count went 3 -> 4 and he was let through. The device counts this table when asked but does not upload its rows |
| Delete one user | `DATA DELETE user Pin=…` (as before) | 99999 gone from the read-back |
| Face | same `biodata` form, `Type=9`, `No=0`, `MajorVer=40`, `MinorVer=1` | Ajay removed himself on the terminal; his saved face, finger and card were written back to 445962 and each was recognised as him |

Re-sending a user record keeps that user's fingerprint and door permission.
Ajay's own 445962 had no door permission before any of this (refused at 14:04,
before the first write) and was given one. Nihal (445966, Super Admin, card)
and Sajal (445961, card), removed for the test, were restored from the
software with fingerprint, face and door permission; Ajay (445962, Super
Admin) the same after he removed himself.

The code now sends `Privilege`, adds the door permission on an access-control
device (`needs_access_grant`: DeviceType `acc`), writes fingerprints and faces to any
user on this model (`MEASURED_TEMPLATE_TYPES = {"1", "9"}`); an unmeasured type
still goes only to test user 99999, and the face trial card is hidden. `take_pending_commands` no longer fails the whole reply when a
template cannot be decrypted: that one command is dropped and shown as not
sent (a second dev server without the key had returned 500 to every poll).

Next: a second device of the same model for the real device-to-device test
(so far each template was written back to the device that captured it); the 3A the same way (ATT2 forms) once a 3A is
reachable; then bulk queueing beyond `MAX_PENDING` for whole fleets.

## Device data flow, part 2: mapping, bulk map, copy between devices — 2026-09-19 (Claude, Ajay's session)

Built on main after the company-departments merge (branch `feature/device-mapping`).
The Employee ID (the employee's current code) is the device user number.

- **Employees list:** a **Devices** column (Mapped · N / Not mapped, and Finger ·
  Face when the server holds them), a **Map** button per row and **Bulk map to
  devices** on the page. Map dialog: the employee's branch shown read-only, a
  device of that branch (Select2), "Counts from" date, both switches ticked.
  Bulk map: a branch (read-only when the viewer has one) and all its devices or
  one. Anyone who may edit employees in the branch (`employees.edit`) may map;
  both views are in `BRANCH_PAGES`.
- **Mapping** (`devices/services/mapping.py` `map_employee`, `map_one`,
  `map_branch`): creates the DeviceEnrollment under the Employee ID (digits only,
  ≤ 20); refuses another branch's device, a second mapping, a number another
  person holds there. A person not on the device yet — or on it without a
  fingerprint/face another same-model device captured — is **sent to it** via
  `push_to_device` with name, card, role (from any company device that reports
  them), door permission, fingerprint and face. A start day before today
  re-checks the company's excluded punches from that day (administrators).
- **Device users:** **Map automatically by Employee ID** (unmapped users whose
  number is an employee's current code; nothing written to the device).
  **Removed from device**: a user absent from the device's latest complete user
  list (the upload answering "Refresh user list" carries `cmdid`) is kept on
  the list, marked, sorted last and not counted as unmapped.
- **Copy users to another device** (Ajay, 2026-09-19): checkboxes with a
  select-all on Device users; ticking shows a selection bar (count, "Select all
  N on this device", Clear, the target device, Copy to device). Targets are the
  company's other devices **of the same model** only. `transfer_users` copies
  name, card, role, door permission, fingerprint and face from the source's
  saved templates; a user mapped to an employee on the source is mapped on the
  target too when it is in that employee's branch. Ticks survive paging.
- **Outbox** (`DeviceOutboxCommand`, `devices.0007`): device writes now wait in
  their own table (up to 5,000 per device) instead of the 10-slot refresh queue,
  handed over 5 per check-in after any refresh commands; answers mark each row
  done/refused; a template body waits encrypted and is cleared once sent.
- Shared UI: a native `<dialog>` modal and the selection bar/checkbox in
  `components.css`; the date picker attaches to a dialog it sits in and follows
  a value set by script.
- Tests: `devices/tests_mapping.py` (23); table/template tests updated for the
  new columns and the outbox. Browser-checked (rendered pages) at 1280 and 375.
- No `.env` change. Migration `devices.0007`.

### Part 2b — the scenarios (Ajay, 2026-09-19)

| Situation | Where | What happens |
|---|---|---|
| People enrolled at the terminal, not in the software | Device users → tick (or "Add them all") → **Add as employees** | Employee per user: name from the device, Employee ID = device number, device's branch, department/designation **"Unassigned"** (made on first use; HR moves them later), linked at once, fingerprint/face kept. No salary yet. Someone whose number is already an Employee ID is linked, not duplicated (`import_users`) |
| Employees in the software (bulk-made), not on devices | Employees → tick / Select all → **Send to devices** | To every device of their branch: ID and name (+ card, fingerprint, face when saved); enrol the rest at the terminal and the device reports the templates back (`send_employees`) |
| Both exist, numbers match | Device users → **Link to existing employees** | Linked by Employee ID, nothing written |
| New or replaced device of the same model | New device's Users → **Load employees onto this device** | Every active employee of its branch, with the saved fingerprint/face from any device of the model (`load_device`) |
| A second device alongside the first | Device users → tick → **Copy to device** | Same model only (`transfer_users`) |

Checkboxes are always on Device users now (not only when a copy target exists).
The old "Create employees for the unmapped users" (draft codes `DEV-<n>`) is
replaced on the page by Add as employees (Employee ID = device number).
Tests: `ScenarioTests` in `devices/tests_mapping.py`.

### Verified on the office 2A — 2026-09-19 (Ajay at the device)

Everyone except Ajay deleted on the terminal, then **Load employees onto this
device**: Moin, Nihal and Sajal came back with name, card, role, door
permission, fingerprint and face — **faces and fingers recognised for everyone,
Nihal's card works and he opens the admin menu (Super Admin restored)**.
Rayhan went back with ID and name only (nothing captured yet). The
device → software → device loop is proven on the 2A for all of it.

Still open: capture on one 2A and write to a *second* 2A (no second unit yet);
the 3A (ATT2, writes refused, unmeasured); volume; the raw-upload encryption
decision; merging `feature/device-mapping` to main (awaiting Ajay).

### Plan — next session (2026-09-20), agreed with Ajay

1. **Volume test on the 2A, outside office hours:** put **at least 50 test
   users** on the device from the software, time it, then **remove them all from
   the software** and confirm the device's user count is back to where it was.
   Only test numbers (a reserved range, e.g. 90001–90050), deleted by Pin only.
2. Needed for that test, to build first:
   - **Bulk "Remove from device"** in the Device users selection bar (today
     Remove is one row at a time), behind the confirm modal, delete by Pin only.
   - **A progress card** on the device's Users page: done / refused / waiting,
     a bar and time left, refreshing itself, kept when you leave the page; a
     "Sending…" badge on the device in the Devices list; refused ones listed per
     person with Retry.
3. **Speed, measured during that test:** raise commands per check-in from 5
   (cautious, not measured) to 20, then 50 (the 2A reports MaxPackageSize ≈ 2 MB,
   a template command is ≈ 1.5 KB); and shorten the device's check-in interval
   (Delay 10 s → 2–3 s) while a job runs, restored to 10 s when it finishes.
   Watch that scans are still recognised promptly while a batch is written.
   Target: 500 people ≈ 7 min at 50/check-in, ≈ 2 min with the shorter interval.
4. Then: merge to main (after Ajay's go, full suite first), CSV/Excel bulk
   employee import, removal from devices when employment ends.

### 3A remote test — checklist from what the 2A taught us (2026-09-19)

The 3A is reachable only through `workforce.iglweb.com` and nobody is on site,
so the device must prove every write itself: **each write is followed by a
read-back** (`DATA QUERY USERINFO PIN=99999`, proven on this 3A) and compared.
Test user 99999 only, on the less-used 3A, outside office hours, one command at a
time. Before anything: pull both 3As' users + fingerprints/faces (read-only,
proven) — the backup. Needs the new code on the live server first (blocked by
the company-departments migration: wipe or a data-keeping migration).

| 2A trap (what happened) | Check on the 3A |
|---|---|
| **"Done" is not proof.** `Return=0` came back for commands that changed nothing or did the wrong thing | Read back after every write; compare field by field |
| **Write names ≠ upload names.** Lowercase `pin=` was accepted and made a user with an **empty number** (the three empty rows on the 2A, undeletable by Pin); `Card` was ignored, `CardNo` worked | Write with the spelling the 3A *uploads* (`PIN`, `Name`, `Pri`, `Card`, `Grp`, `TZ`) and read back; any field missing from the read-back means a wrong name |
| **Role silently ignored.** `Pri=14` → nothing; `Privilege=14` worked | Test the role on its own (0 → 14) and read back; on the 3A `Pri` is the documented name — prove it |
| **"Invalid time period"** (rtlog event 23): a user written from software had **no door permission** (`userauthorize`), so the device recognised and refused them | The 3A is time-attendance (`DeviceType=att`): expect no door table, **but** its user row carries `TZ=0000000100000000` and `Grp=1` — a wrong or blank TZ/group can refuse the same way. Write TZ and Grp exactly as the 3A uploads them, and look for the refusal code in its scans |
| **Device type forgotten after the DB rebuild** (the device does not re-register), so the door permission was skipped | Do not rely on registration data; the dialect comes from what the device announced/sent, and the 3A is already known as ATT2 |
| **A wrong delete key wiped every user** (`uid=` on the 2A) | **No deletes on the client's 3A.** 99999 stays, harmless, with no templates of its own. Delete is measured on an office 3A |
| **Several options in one command** became one wrong value (`SET OPTION` with tabs) | One field/option per command when in doubt; never the server address on the 3A |
| **Template types.** 2A: fingerprint Type 1 v13, face Type 9 v40.1; templates only transfer within one model | Read the 3A's own types/versions from the backup; `BIODATA` vs the older `FINGERTMP`/`FACE` — try `BIODATA` first (it uploads that), read back, compare the stored template exactly (hash) |
| **Verify mode.** The 3A uploads `Verify=-1` (device default) | Send `Verify=-1` or omit it; a wrong verify mode could stop face/finger working |
| **Query forms differ per dialect** (3.x table form → `-1004` on the 3A; `USERINFO` → `-629` on the 2A) | Only the 2.x forms on the 3A |
| **Scans made while offline were not re-sent** by the 3A until asked (`DATA QUERY ATTLOG`) | Already handled (catch-up); after the test, confirm no scans were missed |
| **"Removed from device" needs a complete user list** marked with `cmdid` — the 2A's answers have it | The 3A's `USERINFO` answer may not; check before trusting the Removed badge there |
| **Invented comm key → 401** after registration | Leave the 3A's comm key as it is |
| **Two dev servers on one port** split device and browser traffic | Not relevant on the live server; check only one worker set is serving |

Recognition (does the copied face open for the person) and delete stay
unproven until someone stands at a 3A — recommend a 3A in the office.

### Part 2c — remove from the device in bulk, and a progress card — 2026-09-20

- **Remove from device** in the Device users selection bar (and the row's own
  Remove button, now through the same service): deletes **by user number only**
  — the form measured as safe — and **never the device's last super admin**
  (removing every administrator leaves a terminal nobody can open the menu on;
  only a factory reset recovers it). Saved fingerprints/faces, the enrollment
  and the punch history are all kept, so anyone removed can be sent back.
  `mapping.remove_users`; refused on a 2.x device (unmeasured).
- **Single user writes now use the outbox too** (`queue_user_push`,
  `queue_user_delete`): they went to the 10-slot refresh queue, which a bulk
  removal would have overflowed — found by the new tests.
- **Progress card** on Device users: "Sending to <device> — N of M", a bar,
  done / waiting / refused, time left (from the queue and the device's
  check-in interval), refreshing every 5 s and stopping when the run ends.
  It survives leaving the page. `commands.job_progress` +
  `devices:device_job_progress` (JSON) + `devices/js/job_progress.js`.
- The confirm modal now also takes its wording from the **button** pressed, so
  one bar with several actions asks only for the risky one.
- Tests: `RemoveFromDeviceTests`, `JobProgressTests` (devices: 362 OK).

### 3A writes measured on the client's device — 2026-09-20

Remote, through `workforce.iglweb.com`, nobody on site; one device only
("Healthy Chooice Shade - 1"), test user 99999, every write read back with
`DATA QUERY USERINFO PIN=99999`.

| Sent | Answer | Read back |
|---|---|---|
| `DATA QUERY tablename=user,…` (3.x) | **Refused (-1004)** | — |
| `DATA QUERY USERINFO` | Done (0) | the whole user list, with BIODATA |
| `DATA UPDATE USERINFO PIN=99999⇥Name=TEST 99999⇥Pri=0⇥Passwd=⇥Card=⇥Grp=1⇥TZ=0000000100000000` | Done (0) | `name "TEST 99999", role Normal User` — **the 2.x user write is proven** |
| the same with `Card=987654321` and one `DATA UPDATE BIODATA` per template | Done (0) | `card 987654321, 1 fingerprint, 1 face` — the device **stored** both templates |

Also found and fixed on the way: after the server's database was rebuilt the
device never re-announced, so the dialect fell back to 3.x and Refresh user
list was refused — `protocol.dialect` now falls back to the model catalogue
(`senseface-3a` speaks ATT2) and Edit device offers a 2.x override. The trial
card also refused to write a user record without a template; it now writes the
record alone, which is the right first step on an unproven protocol.

**What this changes:** `MEASURED_WRITES["att2"] = {"user"}` — a real person's
record, name, role and card may now be written to a 3A (so the client's people
can be put on their devices from the software). **Templates still go only to
test user 99999**: the device stored a copied fingerprint and face, but nobody
has scanned against one, so "it stores it" is not yet "it recognises them".
**Deleting stays refused on ATT2 entirely**, not even trialled — the wrong
delete key wiped a 2A, and this device is at a client with nobody on site.

Still open for the 3A: recognition of a copied template (watch for punches
from 99999, or someone at the terminal), deletes, and a second 3A for a true
device-to-device copy.
## 2026-09-20 — Tests for the department and designation screens (Nihal)

Branch `feature/department-screen-tests`, from main. The screens rebuilt on
2026-09-19 (Departments repurposed, Designations given their own page) shipped
without tests; Ajay asked for them. 19 tests in
`organization/tests_department_screens.py`, covering the refusals that carry
the rules rather than only that a page renders:

- Departments: the list shows the department and its branch; adding one;
  a branch refusing a second department with the same code; the same code
  being free in another branch; editing renaming it while the **branch stays
  fixed**; a department with employees refusing to deactivate; an empty one
  deactivating; copying to another branch bringing the designations but
  **not** the head; copying twice skipping what is already there.
- Designations: the list showing the title, its department and branch; adding
  one; a department refusing a second title with the same code; the same code
  being free in another department; editing renaming it while the
  **department stays fixed**; a parent in another department being refused;
  a title somebody holds refusing to deactivate; an unused one deactivating
  from the list.
- Who may: an Employee login is sent to My account from all four pages, and
  the services refuse a crafted call as well.

No migration, no `.env` change. Full suite 1213 OK.
## 2026-09-20 — Attendance date and date-range filters (Nihal)

Branch `feature/attendance-date-filters`, from main `5302833` (Ajay's task B).
No migration, no `.env` change. Full suite 1163 OK.

- **Daily list**: an **On date** single-day filter and a **From / To** range,
  both using the project widgets (`data-datepicker`, `data-daterange`), kept in
  the query string and applied *before* the server-side table, so its counts
  are the real counts of the chosen window. Precedence: a single date wins,
  then the range, then the month selectors. A range is capped at 366 days
  (the window is recalculated on read, as Re-check punches is); a backwards
  range is refused with a message and the month is shown instead. The card
  heading and the badge show the active window.
- **Calendar**: a **Jump to date** picker sets the month shown; the month/year
  selectors and the prev/next arrows still work.
- The filter is a real Django form (`DailyListFilterForm` in
  `attendance/forms.py`) so `common/tests_form_controls.py` — the sweep that
  fails on a browser-default picker — covers these fields too.

7 tests in `attendance/tests_date_filters.py`: a single date, a range, a
one-sided range as a single day, a backwards range falling back to the month,
the single date overriding a range, the inputs rendering, and the calendar
jump landing on the right month.
## 2026-09-20 — Department head access (Nihal, framework included)

Branch `feature/department-head-access`, from main `5302833`. Ajay's decision:
the head of a department manages that department's people, and Nihal writes all
of it including `access_control`. No migration, no `.env` change, no new
sidebar destination (a head uses pages already in `BRANCH_PAGES`).
Full suite **1214 OK with no existing test changed** — the only test file added
is `access_control/tests_department_head.py`.

**The framework, beside the branch one** (`access_control/branch_access.py`):

- `HEAD_CODES` — `employees.view`, `attendance.view`, `attendance.fix`,
  `leave.view`, `leave.approve`, `overtime.view`. Deliberately out:
  `employees.edit`, `employees.logins`, `salary.view`, `salary.prepare`,
  `overtime.decide` (it changes pay) and `access.grant`.
- `headed_departments(user, company_id, at=None)` — the active departments
  whose `head` is this user. The field keeps no dated history, so `at` is
  accepted (a dated head would slot in) but unused.
- `scope_for(...) -> Scope(branches, departments)`, and
  `can(..., branch_id=None, department_id=None)`,
  `scope_queryset(..., department_field=None)`.

Two semantics worth remembering:

- **`can(branch_id=X)` keeps its old meaning.** Heading a department inside X
  does *not* open branch X — a head is not a branch-wide anything. The row's
  `department_id` must be passed for a head to pass.
- **`can()` with neither id** answers "anywhere", and that now counts
  departments. That is what makes `page_access.may_open` open the page to a
  head, and it is the only behaviour change; nothing depended on it before,
  because nobody was a head.
- `scope_queryset` **without** `department_field` is byte-identical to before.

**Pages filtered by department as well as branch** (Ajay's as well as Nihal's):
employees list and employee page, attendance daily list / calendar / day panel
/ days to review / fix a day / withdraw / missed scans, leave list and approval
inbox, overtime list (view only). Enforced in the services too —
`require_fix_day`, `correction_services`, `scan_requests.reviewable/decide`,
`leaves.workflow.reviewable` (which `decide_request` re-runs), and payroll's
`_record_in_scope`.

**A head never decides their own** day fix, missed scan or leave; those fall
back to the branch manager or the company. Pay stays hidden. Access ends the
moment the head field changes or the department is deactivated, and the change
is audited on its own line, `department.head_changed`, with the before/after
employee id.

**A leak caught during the build, recorded because it nearly shipped:** the
first cut widened payroll's branch queryset to include the head's branch, which
would have let a head open *another department's* overtime day in that branch.
`branches` is now the genuine branch reach used for filtering, a separate
`choice_branches` feeds only the branch dropdown, and `_record_in_scope` allows
a branch match **or** a headed-department match. Two tests cover it.

20 tests: what a head holds and does not, the three boundaries (own record,
another department in the same branch, another branch), no editing / no pay /
no deciding overtime, losing access when the head field or the department
status changes, and the audit line.


## Bulk employee import: EMP-ID and Name (2026-09-21)

A new company arrives with 400-600 people. The file carries **two columns
only — `EMP-ID` and `Name`**, the two things the attendance terminals know a
person by. Everything else (department, designation, salary, contact details)
is filled in afterwards on Edit employee, as HR gets to each person. This
replaces the first, many-column version on this branch, which was never merged.

**No database change.** Each person is created the way the device import
already creates one (`devices.services.mapping`), so both ways in behave the
same: placed in the branch's **Unassigned** department and designation
(`unassigned_placement`, made on first use, reused after), **no salary
record** (payroll already skips such people by name), `needs_hr_review` in
their metadata. They count for attendance at once — with no department shift
they work the company shift. Their placement starts at **midnight today** in
the company's timezone, so HR setting the real department "from today" on
Edit employee *corrects* that row instead of adding a one-day history line
(tested).

**Which branch:**
- The company picks it from a dropdown that **starts on the default branch**;
  not changing it means the default branch.
- A **branch manager** imports into their own branch only. With one branch the
  field is **locked** (a disabled field, so a posted value is ignored); with
  several, the dropdown lists only theirs. `check_branch` refuses any other
  branch in the service too.
- Permission: `employees.edit` in that branch, as Create employee. No pay is
  set, so `salary.prepare` is not needed.

**Steps:** download the demo CSV (the headings and five made-up people) →
upload → preview (nothing written; every bad row named with its line: not
digits, missing, repeated in the file, already in the company) → confirm. All
or nothing, one transaction, one `employees.imported` audit line, and
everything re-checked at confirm because it comes back through the session.
Headings are forgiving ("Employee ID", "emp id", "EMPID"); Excel .xlsx is still
read (openpyxl stays in requirements.txt); 2000 rows per file.

`BRANCH_PAGES` gains the three import views under `employees.edit` — purely
additive; without it SelfServiceGate bounces a branch manager to `/me/`.

35 tests in `organization/tests_employee_import.py`. Clearing the company's
branch dropdown is not an error: an empty branch means the default branch.
