# Engineering Learning Log

## 2026-09-07 — Lesson 14: Platform authorization, atomic audit, and a usable onboarding slice

**Terms.** An authorization boundary decides who may perform a business operation.
An atomic transaction commits the business change and its audit record together.
Progressive enhancement adds richer controls to working HTML forms and pagination.

**Project problem.** Root previously hit a company-membership wall. The new
platform surface uses explicit active-superuser authorization, while company
requests still require eligible memberships. Root receives no membership or
session tenant bypass. Onboarding creates the company/default branch/settings,
then a separate company administrator can be created or selected and granted
membership through forms. This makes the beginning of the product usable without
seed_demo; it does not yet finish employee onboarding.

**Options and recommendation.** Keep writes in reusable domain services and make
views thin adapters. Each platform service checks root authority before a query or
write. For tenant-owned validation, an explicit temporary use_company context is
necessary because Django full_clean checks constraints through the scoped default
manager. Context is cleared on exit. This is operation scope, not impersonation.
Existing broad company pages now deny non-admin/restricted scopes until proper
action and row filtering ship, rather than leaking company-wide compensation.

**Audit/history.** Added the already-planned AuditLog with explicit before/after
snapshots that exclude passwords. Model/queryset guards plus a PostgreSQL trigger
reject update/delete; a failed audit insert rolls back the business operation.
Feature toggles lock the company, close current intervals and open a successor.
Historical feature answers remain intact, and repeated same-state submission is
a no-op. Future scheduled changes are rejected for review, not overwritten.
Actor deletion is blocked by audit immutability; accounts should be deactivated.
Hash chaining and protection against a privileged DB owner are not claimed.

**Responsibilities.** Lead: define the persona, delivered workflow and acceptance
gate. Implementer: services, forms, tenant-safe queries, migrations and visual
controls. Reviewer checklist: direct service authorization, cross-company IDs,
CSRF, rollback, history, password handling and actual browser usability. These
were checks performed in this session, not a claim of an independent reviewer.

**Verification.** 124 PostgreSQL tests passed (23 new). Real-browser QA started
with only a root login in an isolated DB, created a company and admin through the
UI, changed status/features, and logged into the company. Real DataTables and
Select2 loaded and operated; screenshots and computed styles were inspected at
1440, 768 and 375px. Existing development migrations/data were preserved; two
audit migrations applied, check clean and no drift. No new environment variables.

**Truthful interview phrasing.** “I separated platform administration from tenant
membership and built browser onboarding over authorized transactional services.
Company changes commit with an append-only audit record; suspension revokes
company access on the next request. I tested the workflow without demo data and
verified unauthorized operations, history preservation and rollback on PostgreSQL.”

**Next.** Company organization/schedule writes with complete action/scope checks,
then employee lifecycle pages and the full P1 acceptance gate.

A running, dated record of the engineering concepts we apply as we build the
attendance platform — written so you can revise from it and answer interview
questions truthfully from work you actually did. Each entry follows the same
five-part structure: **term → problem it solves here → options/trade-offs →
who owns what → how we verified**.

Newest entries at the top. Keep claims honest: only describe as "done/verified"
what was actually run and passed. See [PHASE_STATUS.md](PHASE_STATUS.md) for the
authoritative phase state and [TEAM_LEAD_PLAYBOOK.md](TEAM_LEAD_PLAYBOOK.md) for
how we run the four-person team.

---

## 2026-09-06 — Lesson 13: Read-only is not a slice, and a seeded demo hides that (P1 UI)

**What happened.** I built the panel — shell, dashboard, employee list, branches,
departments — styled to spec, running on real data, tenant switching verified in
a browser. The user signed in as the platform owner and got a dead end: *"You are
not a member of any company."* Then they asked whether I expected companies,
branches and memberships to appear by magic. They were right.

**Failure 1 — I built the viewing half only.** Every "Add employee" / "Add
branch" button was `<button type="button">` with no handler. They look like
features and do nothing. Meanwhile `onboard_company()`, `hire_employee()`,
`transfer_employee()`, `revise_compensation()` and `terminate_employee()` were
all already written and tested. The hard part existed; the form that calls it did
not. **A page that can only read is half a slice.**

**Failure 2 — my own seed data concealed it.** The demo was convincing precisely
because I supplied the data with `seed_demo`. From an empty database every page
is a dead end, because nothing can create a company, a membership, a branch or an
employee through the UI. The dependency chain

    company -> membership -> branch -> department -> designation -> employee

has no entry point. A seeded demo tests the read path and silently skips the
write path — which is exactly the half I had not built.

**Failure 3 — I sliced one persona without saying so.** The product has two
surfaces: **platform** (the owner: companies, features, packages — cross-tenant)
and **company** (HR/manager: employees, leave, payroll — tenant-scoped).
PROJECT_SETUP.md even specifies `sidebars/platform.html`, `company.html`,
`employee.html`. I read that and still built one sidebar for one persona. Choosing
which persona ships first is a legitimate decision; making it silently is not.

**What I did NOT do, and why.** The tempting fix is to give the superuser a
company so the panel works. That would violate a recorded decision —
DATABASE_SCHEMA.md §14: a root bypass "must be explicit, audited, and limited to
platform support duties; it must not accidentally become the normal tenant-query
path." The `no_company` wall is the correct *failure*; the missing thing is a
platform area, not a bypass.

**Rules adopted from this (now in TEAM_LEAD_PLAYBOOK.md):**
1. **Cold-start test.** A slice is only done if it can be demonstrated from an
   empty database using the UI alone. If reaching a page needs a management
   command, the slice is incomplete.
2. **No dead controls.** A button that does nothing is not a placeholder, it is a
   false claim of completeness. Either wire it or do not render it.
3. **Name the persona.** State which user a slice serves before building it.

**Interview-ready phrasing.** "I shipped an admin panel that looked complete and
wasn't: it was read-only, and my own seed data hid that no company, membership or
employee could be created through the UI. The lesson I took was to test a slice
from an empty database rather than from a fixture I wrote myself — a demo seeded
by the builder validates the read path and silently skips the write path."

---

## 2026-09-06 — Lesson 12: Seed data, environment guards, and a test that proved its own guard (P1 slice 8)

**Term — Seed / fixture data.** A repeatable command that builds a known dataset.
`manage.py seed_demo` creates two companies, branches, departments, a designation
hierarchy, shifts, weekly offs, holidays, employees on monthly/daily/hourly pay,
employees with and without logins, a transfer, a termination and a **reused
employee code**. Every identity is invented.

**Why it earns its place.** It is P1's required "reusable synthetic
demonstration", it is the data the future UI will display, and — most usefully —
it turns abstract invariants into something inspectable:

    NWT-014 -> employee #6  2023-03-01 .. 2024-02-01   (Karim, resigned)
    NWT-014 -> employee #7  2024-05-01 .. open         (Sadia)

Same code, two different people, no overlap, and neither inherits the other's
history. That is the project's core identity rule, visible in one query.

**Term — Environment guard.** The command refuses to run when `DEBUG is False`
unless `--force`. Synthetic employees must never be mistaken for a real tenant's
records. Defence in depth alongside it: company codes are prefixed `DEMO-` and
every seeded employee carries `metadata["demo"] = True`.

**The guard proved itself accidentally.** The first full-suite run failed with
8 errors: *"DEBUG is False. Refusing to seed synthetic data without --force."*
Django forces `DEBUG=False` during tests, so the guard fired exactly as designed
and blocked the test's own seeding. Rather than weaken the guard, the tests now
pass `--force`, and a new test asserts the *unforced* call raises and creates
nothing. A safety check that inconveniences you is working; the fix is to satisfy
it deliberately, never to remove it.

**New service.** `terminate_employee()` closes the open assignment and
compensation intervals and sets employment status and leaving date. It **deletes
nothing** — and closing the assignment is precisely what frees the employee code
for a later, non-overlapping holder.

**Verification.** `check` clean, no drift; seed runs and is idempotent on re-run
("already seeded - skipping"); **101/101 tests pass** on PostgreSQL. New tests
cover termination closing both intervals, termination deleting nothing, code
reuse after termination, seed idempotency, the same code live in *both* companies
at once (per-company uniqueness), the transfer leaving two assignment rows,
synthetic marking, the guard refusing without `--force`, and — proving the
product rule — the HR Manager holding `leave.approve` while a junior does not,
and Sunrise being denied `payroll.finalize` because it never enabled payroll.

**Interview-ready phrasing.** "I wrote a seeded demo dataset that doubles as
executable documentation of the domain rules — it contains a deliberately reused
employee code, a transfer and a termination, so the history invariants are
inspectable rather than theoretical. It refuses to run outside development unless
forced; that guard actually blocked my own test suite, which I treated as
confirmation and satisfied explicitly instead of loosening it."

---

## 2026-09-06 — Lesson 11: Permission resolution, default-deny, and a migration-ordering bug my dev DB hid (P1 slice 7)

**Term — Effective permission.** The final yes/no after combining every layer that
has an opinion. Ours resolves in a fixed order, first decisive answer winning:

    1. permission exists and is active     -> else DENY
    2. company has the feature enabled     -> else DENY
    3. individual employee override        -> grant/revoke decides
    4. designation's dated rule            -> allowed/denied decides
    5. nothing said yes                    -> DENY

**Default deny.** Silence is never permission. This is the safe direction: a
forgotten rule locks people out (visible, annoying, fixable) rather than letting
them in (invisible, dangerous). Explicitly tested.

**The product rule this encodes.** Enabling a company *feature* does **not** give
every employee its actions — a stated requirement from PROJECT_HANDOFF.md. The
test `test_enabling_a_feature_does_not_grant_employees_its_actions` asserts the
feature is enabled *and* the manager still cannot approve, which is exactly the
roadmap's "seeded features must not grant every employee every action."

**Disable beats enable.** When both an enable and a disable row are live, disable
wins: switching something off is a deliberate act and a stale grant must not
override it.

**Dated answers.** `has_permission(employee, code, at)` answers as of a date, so
"could they approve leave on 3 March?" returns what was true then. Payroll
re-runs and audits need the historical answer, not today's.

**Hierarchy ceiling.** A child designation cannot be ALLOWED something an ancestor
DENIES. That is chain-walking, so it lives in `clean()` (Lesson 6's rule again:
traversal → application, single-row → database).

### The bug worth remembering

The full suite failed with:
`data type bigint has no default operator class for access method "gist"`

**Cause.** `btree_gist` was created in `employees/0001`. The new
`tenants/0002` also uses an ExclusionConstraint but does **not** depend on
`employees` — so on a *fresh* database it could run first, before the extension
existed. My dev database applied it happily because the extension was already
there from an earlier migration.

**Why it matters more than it looks.** `manage.py migrate` on my machine said OK.
The bug only appeared when a database was built **from scratch** — i.e. it would
have hit CI, a new developer, or production deploy, not me. A migration that only
works on an already-migrated database is not a working migration.

**Fix.** Move `BtreeGistExtension()` into `tenants/0001_initial` — the earliest
migration that every tenant-owned app depends on — so any later exclusion
constraint can rely on it regardless of ordering. `CreateExtension` emits
`CREATE EXTENSION IF NOT EXISTS`, so re-declaring it elsewhere is harmless.

**Lesson: the test suite building a fresh database is a migration test.** Running
`migrate` on your own long-lived dev DB proves almost nothing about ordering.

**Verification.** `check` clean, no drift, **89/89 tests pass** on a test database
created from scratch — which is what actually proves the ordering fix.

**Interview-ready phrasing.** "Permissions resolve through feature gate →
individual override → designation default, defaulting to deny, and every lookup is
dated so we can answer what someone was allowed to do on a past date. I also hit a
migration-ordering bug where an exclusion constraint depended on the btree_gist
extension created in an unrelated app's migration — it passed on my dev database
and only failed when the test suite built a schema from scratch. I moved the
extension into the earliest shared migration."

---

## 2026-09-06 — Lesson 10: Service layer, transactions, idempotency and row locking (P1 slice 6)

**Term 1 — Service layer.** Business writes live in plain functions
(`onboard_company`, `hire_employee`, `transfer_employee`, `revise_compensation`)
rather than in views. Views, management commands, Celery tasks and a future
FastAPI router all call the same function, so none of them can skip validation or
transaction boundaries. This is the concrete thing that keeps the FastAPI option
open without duplicating business rules.

**Term 2 — Atomic transaction.** `@transaction.atomic` makes a block all-or-
nothing. Hiring writes Employee + EmployeeAssignment + EmployeeCompensation; an
employee with no compensation cannot enter payroll, so a *partial* hire is worse
than no hire. If any step fails, the database is rolled back to before the call.
Tested: a hire with a designation from the wrong department raises, and afterwards
`Employee.objects.count() == 0` — no orphans.

**Term 3 — Idempotency.** Safe to run twice. `onboard_company` uses
`get_or_create` on `code`, so a retried onboarding (network timeout, double
click, retried job) returns the existing company instead of creating a second one
with a second default branch. Tested explicitly. Honest limit recorded: `hire_employee`
is *not* idempotent by key — it is instead **blocked** by the exclusion constraint
on overlapping `(company, employee_code)`. An idempotency key belongs with the API.

**Term 4 — `SELECT ... FOR UPDATE` (row locking).** Transfer and revision both
"close the open row, then insert its successor". Without locking, two concurrent
transfers could both read the same open row, both close it, and both insert —
producing overlapping history. `select_for_update()` makes the second transaction
*wait* rather than race. The exclusion constraint would still catch it, but
locking turns a confusing constraint error into an orderly queue. Belt and braces:
the lock prevents the race, the constraint proves it can never happen.

**Ordering insight.** The old row must be closed **before** the successor is
inserted. Insert-then-close would momentarily create two open-ended periods for
one employee, which the exclusion constraint rejects. The sequence is forced by
the data model, not by convention — a good example of a constraint teaching you
the correct algorithm.

**Carry-forward semantics.** `transfer_employee(department=...)` carries every
unspecified attribute forward from the current assignment, so a
department-only move does not silently blank the branch or reset the employee
code. Tested.

**This closes a gap I previously flagged.** `validate_tenant_consistency()` only
runs on `full_clean()`. Services now go through `common.services.create_validated()`,
which calls `full_clean()` then `save()` — so cross-company FK protection is
actually enforced on every service write, not merely available.

**Verification.** `check` clean, no migration drift (services add no models),
**75/75 tests pass on PostgreSQL** — 19 new: onboarding creates company+branch+
settings, onboarding is idempotent, failure rolls the whole thing back, hire
creates all three rows, failed hire leaves nothing, transfer closes/opens
correctly, transfer carries attributes forward, backdated transfer rejected,
history survives code reuse, revision splits pay history, and a mid-month raise
leaves both periods queryable for payroll.

**Interview-ready phrasing.** "Business writes go through an atomic service layer,
so a hire either creates the employee, assignment and compensation together or
none of them. Dated changes like transfers lock the open row with SELECT FOR
UPDATE, close it, then insert the successor — the lock prevents the race and a
PostgreSQL exclusion constraint proves overlapping history is impossible.
Onboarding is idempotent via get_or_create so a retried request can't create a
second company."

---

## 2026-09-06 — Lesson 9: The NULL-equality trap, XOR constraints, and modelling "exceptions to a rule" (P1 slice 5)

**Term 1 — NULL is not equal to NULL.** In SQL, `NULL = NULL` is not true, it is
*unknown*. So a `UNIQUE (company, branch, holiday_date)` constraint does **not**
stop two company-wide holidays on the same date, because both have `branch = NULL`
and SQL never considers them duplicates. This is one of the most common real
database bugs, and it fails *silently* — the constraint looks correct and simply
never fires.

**The fix — Coalesce.** Map NULL onto a real sentinel value so the comparison
works:
```python
_BRANCH_SCOPE = Coalesce("branch", 0, output_field=BigIntegerField())
```
Used in both the Holiday unique constraint and the WeeklyOffRule exclusion
constraint, so "company-wide" behaves as a genuine, comparable scope. Tested
directly: two company-wide Friday rules collide, but a company-wide rule and a
branch-specific rule coexist.

**Term 2 — XOR / "exactly one source" check constraint.** `HolidayWorkAssignment`
must reference *either* a dated `Holiday` *or* a recurring `WeeklyOffRule`, never
both and never neither:
```python
Q(holiday__isnull=False, weekly_off_rule__isnull=True)
| Q(holiday__isnull=True, weekly_off_rule__isnull=False)
```
This pattern recurs across the design (PunchAllocation, LeaveAttachment,
PayrollLine sources), so it is worth recognising as a shape rather than a one-off.

**Design insight — modelling an exception without destroying the rule.** Working
on a recurring weekly off could have been modelled by *deleting* the weekly-off
rule for that date. That would be destructive: the recurring rule is a policy, and
erasing it to record one exception loses why the day was ever an off-day. Instead
the exception points *at* the rule (`weekly_off_rule` FK). The rule stays intact;
the exception is additive and auditable. General principle: **record exceptions as
new rows referencing the rule, never by mutating the rule.**

**Constraint-vs-validation split, again.** Database: unique/exclusion/XOR (raceable,
single-row facts). Application `clean()`: "work_date must actually fall on that
rule's weekday" and "work_date must match the holiday's date" — these need to
*dereference* another row and compute, so they live in Python.

**A Django limitation worth knowing.** The dictionary specifies
`CompanyAttendanceSettings.company` as a OneToOne. Django **forbids overriding a
field inherited from an abstract base**, and `company` comes from `TenantOwned`.
Solution: keep the inherited FK and add `UniqueConstraint(fields=["company"])` —
at the database level a OneToOne *is* a unique foreign key, so this is the exact
equivalent. Recorded as a deliberate, documented equivalence rather than a
divergence.

**Verification.** `check` clean; `scheduling/0001_initial` created 7 models and 13
constraints and applied on PostgreSQL; **56/56 tests pass**, including night shifts
allowed to end before they start, non-spanning shifts rejected, single-shift mode
requiring a company shift, one-default-per-department, default changeable after the
prior one ends, overlapping employee shift assignments rejected, duplicate
company-wide weekly off rejected, branch+company rules coexisting, cancelled holiday
freeing its date, and both holiday-work source validations.

**Interview-ready phrasing.** "A nullable scope column silently breaks uniqueness,
because SQL treats NULL as unknown rather than equal — two company-wide holidays on
the same date would both pass a naive unique constraint. I used Coalesce to map NULL
to a sentinel inside the constraint so 'company-wide' compares as a real scope. I
also modelled working-a-weekly-off as a row pointing at the recurring rule rather
than deleting the rule, so recording an exception never destroys the policy."

---

## 2026-09-06 — Lesson 8: Middleware, the service layer, and context leaks (P1 slice 4)

**Term 1 — Middleware.** A layer that wraps every request: it runs code before the
view, calls the view, then runs code after. Django composes them in the order
listed in `MIDDLEWARE`, so ours must sit *after* `AuthenticationMiddleware` —
it needs `request.user` to already exist.

**Problem it solves here.** Until now the tenant context was only ever set by hand
(`use_company(...)` in tests). Real web requests had no tenant, so every scoped
query would have failed loud. `TenantMiddleware` resolves the active company once
per request, so ordinary `Model.objects.all()` inside any view is automatically
scoped — developers never have to remember.

**Term 2 — Thin adapter / service layer.** The middleware does *not* contain the
rule for which company a user may act in. That lives in
`accounts/services.resolve_active_company_id()`. The middleware only translates
HTTP → service call. This matters because Celery tasks, management commands and a
future **FastAPI** router need the identical rule; if it lived in middleware, only
Django page requests would get it right.

**Term 3 — Context leak (the dangerous bug).** Worker threads and database
connections are *reused* between requests. If request 1 sets the tenant context and
doesn't clear it, request 2 — possibly a different customer — inherits it and reads
the wrong company's data. So the reset lives in a **`finally`** block, which runs
even when the view raises. There is a test for exactly that
(`test_context_cleared_even_when_the_view_raises`).

**Security rule enforced.** The session may carry a user-chosen company id — which
is attacker-controllable. So the requested id is honoured **only** when the user
holds an ACTIVE membership in it; otherwise it silently falls back to a company
they genuinely belong to, never to the requested one. Non-active (invited,
suspended, ended) memberships grant nothing.

**The one legitimate use of the escape hatch.** Resolution queries
`CompanyMembership.all_objects` (unscoped) because working out *which* company a
user may act in necessarily happens **before** a tenant context exists — a
chicken-and-egg. That is the sanctioned bootstrap use; the function still only ever
returns a company backed by a real active membership.

**Who owned what.** Lead: the invariant "context is always cleared" and "session
input is untrusted". Implementer: service + middleware + registration + tests.
Reviewer: middleware ordered after AuthenticationMiddleware; reset in `finally`;
no business rule embedded in the middleware.

**Verification.** `check` clean; no migration drift (middleware adds no models);
**37/37 tests pass on PostgreSQL**, covering: context set for a member, anonymous
gets none, context cleared after response, context cleared when the view raises,
cannot select a company without membership, session choice honoured when a member,
inactive membership grants nothing, and scoped `.objects` queries working inside a
real request.

**Interview-ready phrasing.** "Tenant context is established by middleware that
resolves the user's active company membership and stores it in a contextvar, always
clearing it in a finally block — worker threads are reused, so a leaked context is a
cross-tenant data leak. The resolution rule sits in a service, not the middleware,
so background jobs and the API layer enforce the same rule. The company id from the
session is treated as untrusted input and validated against active memberships."

---

## 2026-09-06 — Lesson 7: Exclusion constraints, temporal data, and cheap-vs-expensive renames (P1 slice 3)

**Term 1 — Effective-dated (temporal) records.** Instead of overwriting a row when
something changes, you close the current interval (`effective_to`) and insert a
successor. History becomes queryable: "what did this person earn on 12 March?"

**Term 2 — Exclusion constraint.** A PostgreSQL constraint that rejects a row when
a *comparison* against existing rows is true — here, "same company AND same
employee_code AND overlapping time range". Unique constraints only compare
equality; overlap needs `&&` on a range type, which is what `ExclusionConstraint`
gives you.

**Problem it solves here.** Our invariant: an `employee_code` is **reusable by a
new person after the previous holder's interval ends, but never overlapping**. A
plain `unique(company, employee_code)` would forbid reuse forever — wrong. An
application check ("does any row overlap?") loses to concurrency: two requests
both check, both see nothing, both insert. Only the database can decide this
atomically.

**How it's built.**
- `common/db.py`: `TstzRange(effective_from, effective_to, RangeBoundary())`
  builds a `tstzrange` with `'[)'` bounds = inclusive start, exclusive end —
  exactly the documented convention. A NULL upper bound means open-ended.
- `ExclusionConstraint(expressions=[("company", EQUAL), ("employee_code", EQUAL),
  (period, OVERLAPS)], condition=~Q(status="cancelled"))`.
- **`btree_gist` extension required:** exclusion constraints use a GiST index, but
  GiST doesn't natively know `=` for bigint/varchar. `btree_gist` supplies those
  operator classes. Added as `BtreeGistExtension()`, the *first* operation in the
  migration — before any constraint that needs it.
- The `condition` matters: `cancelled` rows are void so they must not reserve a
  code, but `ended` rows still *occupied* it historically and must block overlap.

**Term 3 — Rename cost curve.** Renaming `workforce` → `employees` cost almost
nothing *because no migration existed yet*. Had we migrated first, it would have
meant `AlterModelTable`, renaming three tables, every FK, index and constraint,
plus coordinating a deploy. **Lesson: name things before the first migration; the
price of a rename rises sharply the moment tables exist.** Docs were renamed at the
generator's source (`MODEL_FIELD_DICTIONARY.md`) and the artifacts regenerated —
never hand-edit generated files, or the next regeneration silently reverts you.

**Who owned what.** Lead: called the rename early (correct instinct — cheapest
possible moment) and chose the name. Implementer: app swap, models, constraints,
migration surgery, doc regeneration. Reviewer: totals unchanged after regeneration
(83/12/1615/453), no `workforce_*` tables, full suite green.

**Verification.** `check` clean; `employees.0001_initial` applied on PostgreSQL;
tables confirmed `employees_*` with no `workforce_*`; schema regeneration reported
identical totals; **27/27 tests pass on real PostgreSQL**, including the reuse-vs-
overlap pair that proves the core invariant.

**Interview-ready phrasing.** "Employee codes are reusable but must never overlap in
time, so I modelled assignments as effective-dated intervals and enforced it with a
PostgreSQL exclusion constraint over `tstzrange` — equality on company and code,
overlap on the period, with cancelled rows excluded. It needs the btree_gist
extension for equality operators inside the GiST index. An application-level
overlap check would have been racy; this one is atomic."

---

## 2026-09-05 — Lesson 6: Database constraints vs. application validation (P1 slice 2)

**Term.** A *database constraint* is a rule PostgreSQL itself enforces — it cannot
be bypassed by any code path, race, or shell. *Application validation* is a rule
Python enforces in `clean()`/services — expressive, but only runs if you call it.

**Problem it solves here.** "One default branch per company" or "no duplicate
department code" cannot be safely enforced by a `if exists()` check in a view:
two concurrent requests both pass the check, then both insert. Only the database
can make that race impossible.

**The three constraint types we used, and why each:**
- **Unique constraint** — `(company, code)` on Branch. Note it's *composite*: code
  `HQ` may exist in many companies, but only once per company. Global uniqueness
  would have been wrong.
- **Partial (conditional) unique** — one *active default* branch per company:
  `UniqueConstraint(fields=["company"], condition=Q(is_default=True,
  status="active"))`. The condition is what makes it partial: it only applies to
  rows matching it, so a company can keep many non-default branches.
- **Check constraint** — `designation_parent_not_self`
  (`~Q(parent=F("id"))`). Cheap, always-on guard against direct self-parenting.

**Where the database can't reach — and what we did instead.** A check constraint
can't *walk a chain*, so full hierarchy-cycle detection lives in
`Designation.clean()` (walks ancestors). Likewise "parent must be in the same
department". Rule of thumb learned here: **single-row/single-column facts →
database constraint; multi-row traversal → application validation.**

**Reusable tenant guard.** Added `TenantOwned.validate_tenant_consistency()`:
on `full_clean()` it checks *every* FK pointing at another tenant-owned model
belongs to the same company. Written once on the base class, so all 80+ future
tenant models inherit cross-company FK protection for free. This is the
application half of DATABASE_SCHEMA.md §10; the DB half (parent
`UNIQUE(id, company_id)` + composite FK) remains an outstanding hardening step.
**Caveat recorded:** it only runs if services call `full_clean()` before `save()`.

**Verification.** `manage.py check` clean; `organization/0001_initial` created all
6 constraints; **14/14 tests pass on real PostgreSQL** — including duplicate code
rejected, second active default rejected, same code allowed in a *different*
company, cross-company FK rejected, self-parent rejected, cycle rejected,
parent-in-other-department rejected, and hierarchy_level derived from the parent.

**Interview-ready phrasing.** "I enforced tenant invariants where they can't be
raced: composite and partial unique constraints in PostgreSQL for per-company
uniqueness and the single-default-branch rule, check constraints for single-row
guards, and application validation only for the rules needing chain traversal
like hierarchy-cycle detection. I put the same-company foreign-key check on the
shared tenant base class so every model inherits it."

---

## 2026-09-05 — Lesson 5: Scoped manager, `TenantOwned`, and cross-app migration cycles (P1 slice 1)

**Term.** A *custom manager* customises the default queryset a model returns. Here
the `TenantManager` makes `Model.objects.all()` mean "…for the current company".
A *migration dependency graph* is how Django orders `CreateModel` operations
across apps when their models reference each other.

**What we built (realising Lesson 4).**
- `common/tenant.py`: a `contextvars` current-company holder + `use_company()`
  context manager.
- `common/models.py`: `TenantManager` (reads the context; **fails loud** with
  RuntimeError if none is set — no silent cross-tenant reads) and `TenantOwned`
  (adds the `company` FK, `objects` scoped + `all_objects` unscoped, and
  `Meta.base_manager_name = "all_objects"` so Django's internal/related fetches
  aren't blocked by a missing context). `save()` auto-stamps `company` from context.
- `tenants/models.py`: `Company` (the tenant root — **not** TenantOwned).
- `accounts/models.py`: `CompanyMembership` (first TenantOwned model; resolves
  which company a user acts in — bootstrapped via `all_objects`). Branch/dept
  M2M scopes deferred to the P1 organization slice (documented divergence).

**The cross-app cycle (a real dependency-map lesson).** `Company` → `User`
(`suspended_by`, actor FKs), while `CompanyMembership` → `Company`. Two apps
referencing each other. Django resolved it automatically by splitting into three
ordered migrations: `accounts.0001` (User) → `tenants.0001` (Company, depends on
the swappable user) → `accounts.0002` (CompanyMembership, depends on
`tenants.0001`). This is exactly the "build migrations in dependency order; break
cycles with a later migration" rule — no manual intervention needed here because
the cycle crosses migrations, not a single one.

**Who owned what.** Lead: the invariant "no tenant query without context" and the
fail-loud choice. Implementer: managers, base, models, migrations, tests.
Reviewer: `base_manager_name` set; admin uses `all_objects`; isolation test green.

**Verification (evidence).** `manage.py check` clean; `makemigrations` produced
`tenants/0001_initial` + `accounts/0002_companymembership`; migration chain
applied clean in order; **5/5 isolation tests pass** on in-memory SQLite:
scoped-manager-returns-only-current-company, cannot-fetch-other-by-id,
missing-context-fails-loud, unscoped-sees-all, company-stamped-from-context.
SQLite was used only to prove the manager logic quickly; the authoritative
PostgreSQL run (migrate + test) is the user's acceptance step.

**Interview-ready phrasing.** "I implemented tenant isolation as a contextvars
tenant context plus a scoped default manager that fails loud when no company is
set, with an explicit unscoped escape hatch for platform code, and proved it with
a two-company test covering list, get-by-id, and the no-context case. The
Company↔CompanyMembership cross-app FK cycle resolved through Django's migration
dependency graph — three ordered migrations, no circular import."

---

## 2026-09-05 — Lesson 4: Tenant isolation strategy (P0 design; code lands in P1)

**Term.** Multi-tenancy: one system serving many customers ("tenants") whose data
must never mix. Our tenant is the **Company**. Models: database-per-tenant,
schema-per-tenant, or **shared-DB/shared-schema + a discriminator column**.

**Chosen (per DATABASE_SCHEMA.md §1, §10, §13–14):** shared-DB/shared-schema —
every tenant-owned row carries a direct `company_id`. Cheapest to operate and
migrate; isolation is enforced, not implied.

**The trap.** A `company_id` column is *not* isolation. Isolation exists only if
every query is scoped to the current company. One forgotten `.filter(company=...)`
leaks another tenant's data. So isolation is an enforcement discipline.

**Adopted enforcement — defense in depth (three layers):**
1. **Application scoping (primary):** `TenantOwned` abstract base (adds `company`
   FK) + a company-scoped default manager, reading the "current company" from a
   `contextvars` tenant context set by request middleware and a Celery base task
   (**and cleared after** — pooled connections/worker threads are reused).
2. **DB integrity (backstop, high-risk relations only):** parent `UNIQUE (id,
   company_id)` + child composite FK `(parent_id, company_id)` so a cross-tenant
   FK cannot be inserted. Applied selectively, not to all 453 FKs.
3. **PostgreSQL RLS: designed-for now, ENABLED before the P6 pilot** (roadmap).
   RLS is defense in depth; it does not replace correct managers/services.

**Root bypass:** `is_superuser` platform admin may bypass tenant scope only via an
explicit, audited `unscoped()` path — never the default query path (§14).

**Who owned what.** Lead: the invariant "no tenant-owned query without a company
context" + which relations get Layer 2. Implementer: `TenantOwned`, scoped
manager, `contextvars` context + middleware + Celery base, composite constraints.
Reviewer: every tenant model uses `TenantOwned`; no `.objects` without scope in
views/tasks; Celery sets+clears context; isolation test passes.

**Verification (P1 acceptance gate).** Two-company isolation test: acting as
Company A cannot read/update/delete Company B's rows via get-by-id, list, or a
background job; a cross-company FK write is rejected.

**Status.** Strategy ADOPTED and recorded. No code yet — the first P1 slice builds
`common/tenant.py`, `TenantOwned` + `TenantManager`, `tenants.Company`, middleware
+ Celery base, selective composite FKs, and the isolation test.

**Interview-ready phrasing.** "Multi-tenant SaaS on shared-schema Postgres, Company
as tenant. Isolation is defense-in-depth: a company-scoped default manager backed
by a contextvars tenant context (set in middleware and Celery, cleared after),
composite (id, company_id) foreign keys on high-risk relations so the database
itself rejects cross-tenant references, and RLS planned as a pre-pilot backstop.
I proved it with a two-company test asserting no cross-tenant read/write path."

---

## 2026-09-05 — Lesson 3: `.env` files and django-environ (P0)

**Term.** A `.env` file stores environment-specific config as `KEY=value`; a
*loader* reads it into the process at startup. `.env` is never committed; a
committed `.env.example` documents the keys without the values.

**Problem it solves here.** The first cut used raw `os.environ` with no `.env`
loader — so running `migrate` meant hand-setting `$env:` every shell session, and
nothing told a newcomer which variables exist. Undiscoverable and fragile.

**Options / trade-offs.**
- **django-environ (chosen).** Reads `.env`, typed getters (`bool`/`list`/`int`),
  parses one `DATABASE_URL` into Django's `DATABASES`. Django-idiomatic; one
  mature dependency.
- python-dotenv: lighter, loads into `os.environ` only — you cast types by hand.
- pydantic-settings: ideal for the future **FastAPI** side, awkward for Django
  `settings.py`. Note: `.env` is framework-neutral, so FastAPI can later read the
  *same* file via pydantic-settings — no conflict, one source of truth.
- **Reversal of Lesson 2's call:** Lesson 2 deferred this dependency "for a
  handful of variables". Config has since grown (SECRET_KEY, DEBUG,
  ALLOWED_HOSTS, DB, later Redis/Celery/email), so adopting django-environ is the
  consistent, honest revision rather than clinging to the earlier note.

**Who owned what.** Lead: secrets live only in `.env`, `.env` is git-ignored.
Implementer: wired `environ.Env` + `read_env`, moved SECRET_KEY/DEBUG/
ALLOWED_HOSTS/DATABASE_URL to env, shipped `.env.example` + `.gitignore`.
Reviewer: no hard-coded secret; `.env` excluded from git.

**Verified.** `manage.py check` clean; settings resolve from `.env`
(`DATABASE_URL` → ENGINE=postgresql, NAME=attendance_device, USER=attendance,
HOST=localhost:5432, CONN_MAX_AGE=60; DEBUG and ALLOWED_HOSTS typed correctly).

**Interview-ready phrasing.** "I externalised config with django-environ: a single
`DATABASE_URL` and typed settings loaded from a git-ignored `.env`, with a
committed `.env.example` as the contract — so secrets never hit version control
and the same file can feed a future FastAPI service."

---

## 2026-09-05 — Lesson 1: The swappable / custom user model (P0)

**Term.** `AUTH_USER_MODEL` is the Django setting naming which model represents a
login account. A "swappable user model" means Django lets you substitute your
own class for the built-in `auth.User` — but only if you declare it *before the
first migration is applied*.

**Problem it solves here.** Django bakes the user model's identity into the very
first migration. Swapping the user model *after* migrating raises
`Inconsistent migration history`, whose standard fix is to drop the database and
start over — unacceptable once real data exists. Our handoff mandates a custom
`accounts.User` precisely to avoid ever being trapped there. We were at the one
safe moment: no `db.sqlite3`, no applied migrations.

**Options / trade-offs.**
- `AbstractUser` (**chosen**, and what MODEL_FIELD_DICTIONARY.md §1 specifies):
  inherit Django's password/permission plumbing, add our own fields. Low risk,
  fast, standard.
- `AbstractBaseUser`: total control, but you re-implement permissions. Overkill.
- Default `auth.User`: rejected — unswappable later, violates the contract.
- Sub-decision — **login by email**: the dictionary makes `email` the login
  identifier, so `USERNAME_FIELD = "email"`, `username` becomes an optional
  internal field, and a small custom `UserManager` keys account creation on
  email (otherwise `createsuperuser` still demands a username).

**Who owned what.**
- *Team lead:* enforced the exact naming contract (class `User`, app `accounts`,
  `AUTH_USER_MODEL = "accounts.User"`) and that no `migrate` ran early.
- *Implementer:* wrote the model, manager, admin, setting, and first migration.
- *Reviewer checklist:* `get_user_model()` resolves to `accounts.User`; no import
  of `django.contrib.auth.models.User`; relations use `settings.AUTH_USER_MODEL`.

**How we verified.**
- `manage.py check` → "no issues".
- `get_user_model()._meta.label` → `accounts.User`; `USERNAME_FIELD` → `email`.
- `makemigrations` produced `accounts/0001_initial.py` — depends on `auth`
  (swappable-safe), `db_table = accounts_user`, custom manager registered.
- *Pending your run:* `migrate` against a fresh PostgreSQL database applies it
  from clean. (You are creating the DB/role and running migrate yourself.)

**Interview-ready phrasing.** "I stood up a Django project on a custom user model
from the first migration — `AUTH_USER_MODEL = 'accounts.User'`, an AbstractUser
subclass that logs in by email via a custom manager — because swapping the user
model after migrating forces a destructive reset. I verified the resolved model
label and that the initial migration declared the swappable `auth` dependency."

---

## 2026-09-05 — Lesson 2: 12-factor database config (P0)

**Term.** "12-factor config" means reading deployment-specific values
(especially secrets) from the *environment*, not hard-coding them in source.

**Problem it solves here.** The original settings hard-coded a SQLite path; the
architecture requires PostgreSQL, and a committed DB password would be a leak.

**What we did.** `DATABASES` now uses `django.db.backends.postgresql`; added
`psycopg[binary]` 3.3.5 as the driver and `CONN_MAX_AGE=60` to reuse connections.
Initially read `ATTENDANCE_DB_*` from raw `os.environ`; **superseded the same day
by Lesson 3**, which switched to a single `DATABASE_URL` loaded from `.env` via
django-environ. No password is committed either way.

**Verified.** `manage.py check` passes with the new engine; driver imports.
Applying migrations is the user's own step (DB not yet created).

## Lesson 15 — Invariants, generated identifiers and reviewable UI

A data invariant is a rule every write must preserve. The user requires one current master administrator per company. Hiding role/account choices cannot enforce that: the service locks the company and PostgreSQL conditional unique constraints protect concurrent or direct writes. Ended memberships preserve historical identity; suspended memberships still occupy the slot. The lead specifies the rule, the implementer handles both UI and persistence, and the reviewer checks conflicting data and bypass attempts. No independent reviewer was claimed.

A sequence reserves distinct numbers under concurrency; it does not promise gapless numbering. Company codes remain strings. Slugify turns names into URL-friendly text; collision allocation adds -1, -2 under a transaction-scoped lock. A real two-connection test proves simultaneous identical names produce different codes/slugs.

Tenant context must cover form creation and rendering, not just the main queryset. The default admin's Shift choice field caused the settings edit crash despite the settings list working. The shared root-only admin adapter scopes all these steps and restores context; tests cover GET, valid POST and a cross-company submitted shift.

The earlier compressed HTML and left-aligned forms hindered review and use. Four-space markup and separate rows/cells now expose structure. Browser checks cover actual create/admin/feature workflows, aligned numeric columns, centered forms, and compact desktop/mobile detail layouts. Full PostgreSQL suite passed 131 tests; strengthened focused tests passed seven. No new environment variables. Next remains company setup and scoped permissions, then employee writes.
## Lesson 16 — Shared catalogues, and the data migration your test suite never runs

**The term.** A *catalogue* (or reference table) is data the platform defines
once for everybody: one ZKTeco, one `leave.approve` action, one "Human
Resources". An *adoption row* (link table, junction) is how one tenant says "I
use that entry, here, like this". The split matters because a shared row cannot
hold anything tenant-specific — the moment you add a branch or a head or a
permission rule to it, ten companies are sharing one answer.

**The problem in this project.** The client requires one curated list of
departments and designations so ten tenants cannot invent ten spellings of the
same thing, and so cross-company reporting compares like with like. But
`Department` carried `branch`, `Designation` carried `parent`, and five other
models pointed at both. Moving them under root ownership was therefore not a
flag but a restructuring: the tenant half had to go somewhere, and every FK that
meant "a department" had to be repointed at that somewhere. **A useful test for
any "should this be global?" question: list the row's columns. If any of them
only make sense for one tenant, the answer is two tables, not one.**

**Ordering a multi-app schema move.** Six migrations across five apps, and the
order is the whole design. `organization.0002` only adds — it creates the
adoption rows and fills them from the existing tenant-owned data. Then each
dependant app repoints its FK, using add / backfill / drop / rename rather than
`AlterField`, because the new rows have different primary keys. Only then does
`organization.0003` strip `company` and `branch` off the catalogue. Reversing
that order destroys the only record of who adopted what. The general rule:
**additive first, destructive last, with the readers moved in between.**

**The gap the ordinary suite leaves.** Lesson 6 established that the test suite
building a fresh database is your migration test. That is true for the *schema*
steps and false for the *data* steps: a fresh database has no rows, so every
`RunPython` body runs against empty tables and proves nothing. Those bodies are
the only thing standing between a working development database and a mangled
one.

So `organization/tests_migrations.py` uses `MigrationExecutor` to rewind to the
tenant-owned world, write the awkward case by hand — two companies that each
created their own "Software" department containing their own "Manager" — and
roll forward. It failed immediately, twice:

1. `ProtectedError` on `Designation.parent`. The parent link is `PROTECT`, so a
   merged-away title could not be deleted while another title still named it as
   a parent. The chain is being dropped two operations later anyway; the fix is
   to null it before collapsing.
2. `cannot ALTER TABLE ... because it has pending trigger events`. Django creates
   foreign keys as `DEFERRABLE INITIALLY DEFERRED`, so the updates and deletes
   leave trigger events queued, and PostgreSQL will not alter a table in that
   state inside the same transaction. The fix is
   `schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")` after the data work
   and before the column drops.

Neither would have appeared before the user ran `migrate` on real data. **Write
the data-migration test with the ugliest input you can think of, and write it
before you hand anyone the migrate command.**

**Ceilings versus chains.** The old access rule walked `Designation.parent` and
refused to ALLOW a child what an ancestor DENIED. Replacing it with a rule on
the department is not just simpler to query — one hop instead of a walk — it is
the only version that survives a shared catalogue, because a title hierarchy
correct for one company is wrong for the next. The ceiling is also what makes
`CompanyDepartment.head` safe to delegate to: a head distributes access inside a
boundary they cannot widen. Delegation without an enforced ceiling is a
privilege-escalation path, not a feature — so the check lives in `clean()` on
both the title rule and the individual override, not only in the UI.

**Responsibilities.** The lead decides what is platform vocabulary and what is
tenant configuration, and states the FK rule that follows ("anything meaning *a
department* points at the adoption row"). The implementer writes the migrations
in dependency order and the data-migration test with duplicate input. The
reviewer checks the reverse path, the constraint drops around any collapse, and
that no new FK points at a catalogue row.

**Verified.** 164 tests on PostgreSQL, including 6 that migrate backwards and
forwards over seeded duplicates; `makemigrations --check` reports no drift;
schema artifacts regenerated and `verify_schema.cjs` passes. Applying the
migrations to the development database is the user's own step.


## Lesson 17 — Change what you were asked to change, and check what you carried over

**What happened.** The instruction was "root owns departments and designations;
each company maps designations to its departments". The implementation changed
*ownership* — who may write the tables — and left every other part of the old
model standing, including `Designation.department`. That one carried-over
foreign key quietly moved the department–designation relation from the company
to root, which is the opposite of what was asked. When its consequence surfaced
("root must create Manager once per department"), it was explained as intended
rather than recognised as a contradiction, and it went into a teammate's task
prompt, so two days of screens were built on it.

**The rule.** When you restructure a model, re-read the instruction against
*every field the model keeps*, not only the ones you touched. A field you did
not change is still a decision, and "it was already there" is not a reason.
If a consequence of your design would surprise the person who gave the
instruction, say so as a question before building on it — do not describe it
as intended.

**For the lead.** A task prompt is a transcription, and a transcription can be
wrong. Before a prompt goes to someone who will work alone for days, check its
core rule against the original words. Here the backup branch's own name —
`department_designation_relation_companywise` — contradicted the prompt.

**Vocabulary is part of the spec.** The same screens said "Hire an employee",
"job title", "adopt" and "catalogue": borrowed or internal words the users do not
use. The fix is a written vocabulary table (UI conventions doc) rather than
correcting words one screen at a time.
