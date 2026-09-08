# Team Lead Playbook

How we run this project as a **four-person team (including you, the lead)** while
building the attendance platform. This is a learning/planning scenario: the four
roles are a way to think about ownership and review discipline, **not** an
instruction to launch autonomous agents or to claim that independent reviews
happened when they did not. Every "reviewer" checkbox below is a real check a
human (or you) must actually perform.

Companion docs: [ENGINEERING_LEARNING_LOG.md](ENGINEERING_LEARNING_LOG.md) for the
concepts as we hit them; [PHASE_STATUS.md](PHASE_STATUS.md) for authoritative state;
[IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md) for scope and gates.

## The four roles

Current checkpoint (2026-09-07): platform onboarding is delivered; P1 remains
in progress. Company organization/schedule writes, granular scopes and employee
forms are next. The first part of the cold-start flow is verified, not the full
employee workflow. See PLATFORM_IMPLEMENTATION.md and PHASE_STATUS.md.

| Role | Owns | On this project |
|---|---|---|
| **Team lead** | Scope, sequencing, decisions, "definition of done", protecting invariants | You. Decides *what* and *in what order*; guards the handoff invariants. |
| **Architect** | Model/dependency design, tenancy & security strategy, API boundaries | Shared: the design docs + decisions recorded before each slice. |
| **Implementer** | Writing the code/migrations for the current slice | This session, on your direction. |
| **Reviewer** | Independent verification before a slice is called done | A checklist run against the actual diff/commands — never assumed. |

One person can wear several hats, but the **reviewer hat must be worn
deliberately and separately** from the implementer hat. "It compiles" is not a
review.

## How every meaningful slice is introduced (the teaching contract)

Before each new decision or implementation slice, we state, in chat:
1. **The engineering term** and its plain-language meaning.
2. **The real problem** it solves *in this project*.
3. **Options, recommendation, trade-offs.**
4. **Who owns what** (lead / implementer / reviewer).
5. **How we will verify** the result.

## Definition of done (per slice)

A slice is done only when: code + migration written → `manage.py check` clean →
migration generates without drift → the slice's behavior is exercised (test,
shell check, or documented manual run) → PHASE_STATUS + learning log updated with
*what was actually run*. Schema existing ≠ feature done.

## Invariants the lead must protect (from the handoff — do not let a slice break these)

- **Company is the tenant.** Every tenant-owned row, M2M link, and background job
  must enforce company ownership; a `company_id` column alone is not isolation.
- **Custom user contract:** exactly `accounts.User`; `get_user_model()` at
  runtime; `settings.AUTH_USER_MODEL` in relations; historical registry in data
  migrations. Never the default `auth.User`.
- **Permanent employee identity vs. reusable code:** `Employee.id` is forever;
  `EmployeeAssignment` carries the dated, reusable `employee_code`. A new holder
  never inherits a prior person's history.
- **Raw evidence vs. calculation:** `DeviceMessage`/`PunchEvent` stay separate
  from `PunchAllocation`/`AttendanceSession`/`AttendanceRecord`/`PayrollLine`.
  Manual corrections never masquerade as biometric evidence.
- **Cross-device pairing** uses event time and *historical* authorization.
- **Payroll:** full unchanged month = full salary; the /30 divisor is for
  *deductions*, not for prorating February; `PayrollLine` is the one
  authoritative posting; a prior unpaid salary is not next month's earning.
- **Honesty:** simulator ≠ real-device proof; a table existing ≠ its workflow
  implemented.

## Slice safeguards (binding — adopted 2026-09-06)

Agreed conditions for proceeding with vertical slices. A slice that breaks any of
these is not done, regardless of how much of it works.

1. **Shared foundation first.** P0/P1 must establish `accounts.User`, company
   isolation, employee identity/history, permissions and scheduling before
   dependent leave/attendance/payroll workflows are built on them. *(Status:
   foundation models/services exist; full P1 workflow/access gates remain open.)*
2. **Keep required model dependencies real.** If a model genuinely needs a
   foreign key, it gets one. Never delete an important FK or downgrade it to an
   unvalidated integer just to keep a phase small. If the target does not exist
   yet, defer *that model* to a later migration — do not weaken the relation.
3. **Migrate progressively.** Later migrations add models, fields and relations.
   Django orders migrations by the cross-app dependency graph, and targeting one
   app does not exclude another app's migrations; normally just run
   `python manage.py migrate` and let the graph resolve.
   [Django migration dependencies](https://docs.djangoproject.com/en/5.2/topics/migrations/#dependencies)
4. **Never defer safety.** "Advanced loans come later" is fine. "Tenant
   isolation, approval checks or financial correctness come later" is not. Each
   delivered workflow is safe on the day it ships.
5. **Test across phases, not only within them.** Leave passing and payroll
   passing separately proves nothing. The required evidence is integration:
   approved paid leave producing the correct salary, attendance feeding payroll,
   a cancellation after finalization behaving correctly.

**Consequence for P2:** the twelve core leave models need nothing from payroll or
attendance and are built now with all FKs intact. `LeaveEncashment` and
`LeaveFareAssistanceClaim` (need `payroll.PayrollPeriod`) and
`LeaveCompensatoryCredit` (needs `attendance.AttendanceRecord`) are added in a
later migration once those models exist. They are P7 workflows, so nothing in the
first release depends on them.

## Slice safeguards, part 2 (adopted 2026-09-06, after the read-only panel)

6. **Cold-start test.** A slice is done only when it can be demonstrated from an
   **empty database, through the UI alone**. If reaching a working page requires
   `seed_demo` or any management command, the slice is incomplete. A demo seeded
   by the builder exercises the read path and silently skips the write path.
7. **No dead controls.** A button or link that does nothing is not a placeholder —
   it is a false claim of completeness. Either wire it to a service or do not
   render it. Disabled-with-a-reason is acceptable; inert-but-inviting is not.
8. **Name the persona before building.** This product has two surfaces:
   **platform** (owner: companies, features, packages, subscriptions —
   cross-tenant) and **company** (HR/manager/employee — tenant-scoped).
   PROJECT_SETUP.md specifies separate `sidebars/platform.html`, `company.html`
   and `employee.html` for this reason. Deciding which persona ships first is
   fine; deciding it silently is not.
9. **Read + write together.** Every entity a slice displays must be creatable and
   editable within that same slice, or the slice must state explicitly which
   write path is deferred and why.

**Root bypass stays forbidden as a shortcut.** Do not give the superuser a
CompanyMembership to make pages work. DATABASE_SCHEMA.md §14 requires any root
bypass to be explicit, audited and limited to support duties, never the normal
tenant-query path. The fix for "root sees nothing" is a platform area, not a
bypass.

## Physical table naming (binding — adopted 2026-09-08)

Model names and app labels are chosen for readability in code. The physical
table name is set separately with `Meta.db_table`.

- `leaves`, `attendance` and `payroll` models all use the **`payroll_`** table
  prefix. They are one payroll family in the database.
- Every other app uses its own label as the prefix.
- **Always set `db_table` explicitly** on a new model, so an app rename can never
  silently rename a table.
- `docs/scripts/build_schema.cjs` implements this rule, so regenerated schema
  documents stay correct. Change the rule there, not by hand-editing artifacts.
- Already-migrated tables are not renamed retroactively without a deliberate
  decision and an `AlterModelTable` migration.

## Wording discipline: duplicates

Never say attendance "ignores duplicate scans". The actual rule:

- Raw `PunchEvent` evidence is **never deleted**; a suspected duplicate is
  retained and marked (`dedupe_status`, `duplicate_of`).
- A **retransmission must not multiply attendance effects** — that is idempotent
  processing, not deletion.
- **Closeness in time is not proof of duplication.** Two scans seconds apart may
  be genuine. Ambiguous repeats need an explicit, configurable interpretation
  rule and stay reviewable; confirmed duplicates are excluded from
  `PunchAllocation` while remaining in the evidence tables.

## Review checklists

### Any model/migration slice
- [ ] Relations to the user use `settings.AUTH_USER_MODEL`, not `accounts.User`.
- [ ] Deletion behavior matches the dictionary (PROTECT history / SET_NULL actors
      / CASCADE only for owned config/junctions).
- [ ] Tenant-owned models carry `company` and the same-company rule is enforced
      in a service/constraint, not just the form.
- [ ] `makemigrations` shows **no** unexpected drift; migration reviewed by eye.
- [ ] Money = `Decimal(18,2)`; rates = `Decimal(18,6)`; timestamps UTC.
- [ ] Field names match MODEL_FIELD_DICTIONARY.md, or the divergence is recorded.

### P0 foundation (this phase)
- [x] 13 apps registered in `INSTALLED_APPS`.
- [x] `AUTH_USER_MODEL = "accounts.User"` set before first migration.
- [x] `get_user_model()` resolves to `accounts.User`; login by email.
- [x] `accounts/0001_initial` generated, depends on `auth` (swappable-safe).
- [x] Migrations applied to PostgreSQL; final suite rebuilds an isolated test DB.
- [x] Tenant-isolation strategy recorded; scoped manager and middleware implemented.

## Task/dependency planning

Build migrations in dependency order; break cross-app cycles with a later
nullable-FK addition rather than a forced circular import. Record a model
dependency map before large model work (P0 item, due at the start of P1).

## Binding onboarding/UI correction

Follow [UI_AND_ONBOARDING_CONVENTIONS.md](UI_AND_ONBOARDING_CONVENTIONS.md). A backend constraint and real cold-start UI must agree. Check default-admin edit forms as well as lists; related-field widgets perform their own queries. Review formatting as maintainability, and verify geometry visually rather than treating a zero-overflow check as sufficient.
