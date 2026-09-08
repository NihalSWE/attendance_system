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

## Current checkpoint

- **Current deliverable:** P1 platform onboarding implemented; company setup and employee write workflows remain next. See [PLATFORM_IMPLEMENTATION.md](PLATFORM_IMPLEMENTATION.md) for files/functions and the UI workflow.
- **Actual code:** 22 implemented domain models, including the planned AuditLog; 13 registered apps and model-free base_template. P0 remains Verified. P1 is In progress; the earlier “backend complete” wording overstated authorization and workflow readiness.
- **Platform UI:** root routes to /platform/companies/ without membership; generated company identifiers, company create/edit/status, one master administrator with editable credentials/status, dated feature access and recent audit history work through forms.
- **Company UI:** existing lists remain read-only. Company-wide access is now restricted to unrestricted owner/company_admin; other roles/scopes fail closed until proper scoped views ship. Unimplemented Add/Export controls are explicitly disabled with reasons.
- **Design:** Warm Paper / Ink tokens and Sora; new platform tables use real DataTables with server-side paging/search/sorting, and database-backed selects use real Select2 while fixed choices use styled native selects. Responsive browser verification is required. Remaining component groups are not claimed complete.
- **Database:** existing PostgreSQL data/history preserved; two original auditlog migrations plus three additive corrections for Company defaults, the code sequence and administrator uniqueness. AuditLog is part of the existing 83-model design, so planned inventory remains 83 models / 88 tables / 1,615 columns / 453 FKs.
- **Architecture/user contract:** modular Django monolith, accounts.User, Django-owned ORM/migrations; future FastAPI and workers reuse services. No DRF or duplicate persistence layer.
- **Hardware:** D1 remains unverified; the original “roughly a week” estimate is historical, not a current availability claim.
- **Next action:** company organization/schedule write flows with action + branch/department scope enforcement; then employee lifecycle forms and full P1 acceptance. Do not restart P0, recreate apps, or assign root a membership as a shortcut.
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
