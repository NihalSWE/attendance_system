# Next chat — start the attendance implementation here

## Current handoff — 2026-09-14 (read this first)

**Read [HANDOFF_2026-09-14.md](HANDOFF_2026-09-14.md).** It is the complete,
current handoff: the project, people and roles, where to pull from and push to,
home setup, architecture, the design system, every final decision, what is done,
what is left for Ajay's session and for Nihal (in order), the SenseFace 3A test
for tomorrow, and where to start (A5c). Everything below this section is
historical and must not restart finished work.

## Platform corrections — 2026-09-07

User feedback corrected the onboarding contract. Read [UI_AND_ONBOARDING_CONVENTIONS.md](UI_AND_ONBOARDING_CONVENTIONS.md) and [PLATFORM_IMPLEMENTATION.md](PLATFORM_IMPLEMENTATION.md). Implemented one current master administrator per company with backend/database uniqueness, account editing, generated string codes/slugs, Bangladesh defaults, scoped Django admin forms, explicit `/platform/companies/`, centered responsive pages and readable four-space templates. No new environment variables. Existing data preserved; only the two shared demo memberships were ended with explicit user approval and audit entries. Full suite: 131 tests passed; the strengthened settings-admin valid-POST check also passed in the seven-test focused rerun. Browser creation/admin/feature flows and screenshots checked at 1440/768/375px. P1 remains in progress: next is company organization/scheduling writes and scoped authorization, then employee lifecycle pages. This supersedes earlier existing-account/role-picker examples.


## Current implementation checkpoint — 2026-09-07

Read PHASE_STATUS.md and PLATFORM_IMPLEMENTATION.md first. P0 is verified; 22 domain models now exist. The root platform area and company/admin/feature write flows are implemented. Continue P1 with organization/schedule setup and scoped authorization, then employee lifecycle pages. P2 remains unstarted. Earlier scaffold/P0 instructions below are historical and must not restart completed work.

## Current request

The user wants a phased implementation of a new multi-tenant attendance, leave, payroll and salary-management product. They want initial leave/payroll/salary operations to be useful beyond the bare basics while keeping ordinary user setup simple. No biometric device is currently available; it may arrive in roughly a week. Continue work using a simulator and approved manual attendance while keeping real-device verification separate.

The destination for these files is **docs/** in the new attendance project root. Start at **docs/NEXT_CHAT_START_HERE.md** and track progress in **docs/PHASE_STATUS.md**. The files originated in external_project_plans/attendance_management_system within an unrelated Polymer workspace; copy that inner directory's contents directly into docs/, preserving schema_diagrams/ and scripts/. The old wrapper path is source provenance only, not the location to search in the new project. No attendance application has been implemented in these planning artifacts; inspect the actual new repository state and do not inherit Polymer business models.

All Markdown links below are relative to this document in docs/. When operating from the attendance project root, prefix document filenames with docs/. Generated schema exports are also inside docs/.

Latest instruction: the user will create the Django project and all app skeletons using commands themselves. Do not run startproject/startapp or recreate that work. Inspect the supplied skeleton and continue from its actual state. P0 now means verifying/completing the foundation and domain decisions, not spending a session on scaffolding. If a required skeleton is missing, identify the missing item and provide the minimal command for the user while continuing independent design review.

## Architecture and future ERP APIs

The selected architecture is a modular Django monolith with PostgreSQL and independently scalable background workers where needed. Django owns the business models, migrations, authorization and transactions. Module service functions own writes/calculations; views, jobs and future API endpoints call those functions.

The user explicitly prefers **FastAPI for future API modules instead of Django REST Framework**. This supersedes the earlier DRF recommendation. Keep FastAPI integration possible through reusable business services. Add routers/application wiring when APIs are needed; there is no need to build all ERP endpoints during the initial leave/payroll phases. Define public contracts as business operations, not automatic CRUD access to all 86 models. Introduce versioned endpoints, authenticated tenant-scoped integration identities, permissions, pagination, stable public identifiers and idempotent writes when the integration is implemented. Outbound webhooks and external-ID mappings require their own delivery/ownership design.

FastAPI can be an API layer around the Django-owned domain; it does not have to be a separate microservice. Its integration must explicitly handle ASGI routing, Django initialization, ORM transaction/sync-async boundaries and authentication/tenant context. Reuse business authorization instead of assuming Django page middleware secures FastAPI. Do not duplicate persistence models, migrations or payroll rules. Keep public ERP APIs and vendor device protocols as distinct contracts. Do not introduce DRF as a temporary API layer.

The user will also create **base_template**, bringing the application inventory to **13 apps: 12 domain apps plus one shared presentation app**. It owns the base layout, navbar/footer, sidebar includes and shared styles/scripts, with no domain models. Use permission-aware menu data and optional platform/company/employee sidebar includes; keep feature-page templates in their domain apps. Read PROJECT_SETUP.md for all app commands and the proposed namespaced layout. The database model/table counts remain unchanged.

Before the first application migrations, implement the custom accounts.User and configure AUTH_USER_MODEL. The user can create all project/app skeletons first and leave migration work for this implementation step. If migrations already exist, inspect and preserve them; do not delete a database or migration history to force a fresh start.

**Mandatory user-model naming:** the concrete custom class is exactly `User` in `accounts`, with `AUTH_USER_MODEL = "accounts.User"`. Runtime code resolves it using `from django.contrib.auth import get_user_model` followed by `User = get_user_model()`. Do not name it CustomUser or import the default concrete django.contrib.auth.models.User. Relation declarations use settings.AUTH_USER_MODEL; data migrations use their historical apps registry. AbstractUser is an acceptable superclass and does not make the concrete model Django's default User. See PROJECT_SETUP.md for the full contract.

## Read order and authority

1. [PHASE_STATUS.md](PHASE_STATUS.md): exact current implementation status and next action.
2. [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md): release scope, phase dependencies, deliverables and acceptance gates.
3. [PROJECT_HANDOFF.md](PROJECT_HANDOFF.md): business context, agreed rules and known engineering gaps.
4. [DATABASE_MODEL_PLAN.md](DATABASE_MODEL_PLAN.md): purpose and relationships of the 86 proposed models.
5. [MODEL_FIELD_DICTIONARY.md](MODEL_FIELD_DICTIONARY.md): authoritative current proposed column/relation names.
6. [DEVICE_ATTENDANCE_POLICY.md](DEVICE_ATTENDANCE_POLICY.md): exact authorization and pairing semantics, including offline history.
7. [LEAVE_AND_SALARY_MANAGEMENT.md](LEAVE_AND_SALARY_MANAGEMENT.md): deeper domain rules. Some older field aliases are conceptual; use the field dictionary for actual names and posting direction.
8. [FULL_DATABASE_SCHEMA.md](FULL_DATABASE_SCHEMA.md) and [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md): all-field and architecture views. SVG/HTML/DBML are companion artifacts, not evidence of an implemented database.

The latest explicit user instruction wins. The roadmap controls release timing/scope; the field dictionary controls current proposed fields; the device policy controls device scope. Document and resolve discrepancies instead of silently copying an older alias or formula. The expanded 86-model design is a broader target, not a claim all advanced workflows must be exposed immediately.

Read the core handoff/phase documents first and relevant domain details before working on that phase. Generated Markdown/DBML/SVG/HTML schema files are alternate representations of the same design; do not load every representation or repeatedly re-read unchanged documents merely to consume context. Use the field dictionary as the primary field reference and inspect generated views only where helpful.

## Exact starting task

Continue P1 company setup: permission/scope enforcement, branch/department/designation create/edit, shifts and department-shift assignments, weekly offs/holidays and attendance settings. Then wire employee hire/detail/transfer/compensation/termination to the existing services and prove the full cold-start flow in an isolated test database. Preserve existing data and migrations.

Follow WARM_PAPER_INK_SPEC.md for colour/type/tokens and design_reference/ for form/layout. Shared components stay in base_template; domain pages stay in their apps. Show actual pages and verify interactions/screenshots at desktop, tablet and mobile widths. Do not substitute styled lookalikes for requested Select2/DataTables.

Teach each meaningful slice in chat, implement within the agreed scope without repeated permission requests, and record actual checks/limitations in PHASE_STATUS and ENGINEERING_LEARNING_LOG.

## First release to build

- Company/root controls, scoped access, departments/designations, employee code/assignment and compensation history, optional user accounts, simple shift/calendar setup.
- Full/half/hourly leave, optional attachment, manager-decided paid/unpaid/partial percentage, one-step approval, simple annual/manual balances where enabled, overlap validation and cancellation/amendment.
- Attendance engine using manual/simulated inputs, daily/history views, cross-device IN/OUT, review/corrections, ordinary late/absence and repeated/consecutive-lateness rules, reviewed overtime.
- Monthly payroll for monthly/daily/hourly rate bases, fixed-30 monthly deduction divisor, snapshots/segments, approved leave/overtime, optional bonus/adjustment, review/finalization, readable payslips and minimum correction support.
- Actual payment recording, partial settlement, salary dues and older-balance settlement, simple optional advances with disbursement/recovery. No automatic bank transfer implied.

Advanced accrual/carry-forward, LFA, encashment/compensatory workflows, full loans, richer formulas/remittances and automatic final settlement remain planned extensions. Do not delete their proposed models. Do not present unimplemented feature logic as complete because its table exists.

## Decisions and invariants that must survive the handoff

- Company is the tenant. User can have several company memberships; Employee may have no User. Every tenant path must enforce ownership, including M2M links and jobs.
- Employee.id is permanent; EmployeeAssignment carries dated reusable employee_code and organization history. New holders of the same code never inherit a previous person's history.
- Raw DeviceMessage/PunchEvent evidence stays separate from PunchAllocation/AttendanceSession/AttendanceRecord and PayrollLine. Manual corrections do not masquerade as biometric evidence.
- Device scope options are assigned/department/branch/company devices. EmployeeAssignment override wins over assigned Branch override then company default. Enrollment recognition and explicit assigned-device grant are separate; attendance_enabled=false wins in every scope.
- IN on one allowed device and OUT on another form one employee/shift stream. Use event time, not delivery order, and historical authorization for offline data.
- Monthly unchanged full service receives full salary. The divisor 30 applies to deductions; it does not prorate February to 28/30. Joining/leaving/rate-change proration still needs an explicitly adopted policy before affected payroll is finalized.
- Paid leave prevents the corresponding deduction; hourly/daily pay and overtime must not double count the same minutes. Missing synchronization is not automatic absence.
- PayrollLine is the authoritative financial posting; payment allocations determine due. A prior unpaid salary is not a new earning next month. Posted corrections/reversals preserve source history.
- The actual device protocol, Tipsoi access mode and template compatibility are unverified. No legacy port-4370 polling fallback is authorized by a lack of push evidence.

## Copyable opening message for the new chat

```text
Continue my attendance management project using the documents in docs/ at this project root. They are the handoff from the previous chat. Read them before implementing and use the recorded decisions instead of asking me to repeat the requirements.

I am creating the Django project and app skeletons myself. Do not run startproject/startapp or recreate my setup. Inspect what exists, preserve my work, and start with the unfinished parts of P0: custom accounts.User, configuration, tenancy, migration dependencies and the domain foundation. Configure AUTH_USER_MODEL before initial migrations. If a skeleton is missing, tell me the minimal command needed and continue work that does not depend on it. Do not reset existing databases or migration history.

My custom authentication model must be named exactly User in the accounts app, not CustomUser or any other name. Set AUTH_USER_MODEL = "accounts.User". Runtime code should use `from django.contrib.auth import get_user_model` and `User = get_user_model()`, never the concrete default django.contrib.auth.models.User. Use settings.AUTH_USER_MODEL for model relations and historical apps.get_model in data migrations. Using AbstractUser as the superclass is fine; the concrete model must still be my accounts.User.

Read docs/NEXT_CHAT_START_HERE.md, docs/PHASE_STATUS.md and docs/IMPLEMENTATION_ROADMAP.md first, followed by docs/PROJECT_HANDOFF.md and docs/PROJECT_SETUP.md. Consult docs/DATABASE_MODEL_PLAN.md, docs/MODEL_FIELD_DICTIONARY.md, docs/DEVICE_ATTENDANCE_POLICY.md and docs/LEAVE_AND_SALARY_MANAGEMENT.md before implementing the relevant phase. The complete schema is in docs/FULL_DATABASE_SCHEMA.md and the companion DBML/SVG/HTML files. The field dictionary controls current field names; the roadmap controls release scope. Preserve accepted decisions and flag actual inconsistencies rather than redesigning everything. The generated formats are alternate views; do not repeatedly load all of them.

Use a modular Django monolith with PostgreSQL; add Celery/Redis where background processing is needed. My preferred framework for future API modules is FastAPI, not Django REST Framework. Keep business operations reusable by Django pages, FastAPI routers and jobs. Add API wiring when required without duplicating persistence models/migrations, business rules or authorization. No immediate microservice split is needed.

I am also creating base_template: 13 apps total, consisting of the 12 domain apps plus this model-free shared UI app. Keep the base layout, navbar/footer, sidebar includes and common styles/scripts there; keep feature-page templates in their domain apps. Sidebars may vary by user group, using effective permissions/company features for menu visibility while enforcing access separately in backend operations. Follow docs/PROJECT_SETUP.md.

Follow the phases: foundation and employees/schedules; useful leave; manual/simulated attendance; payroll; payments/dues/adjustments/simple advances; pilot. I have no device yet and expect one in roughly a week. Do not block software work on hardware, and do not claim simulator tests prove compatibility with a real device.

Initially deliver full/half/hourly leave, paid/unpaid/partial pay, attachments, one-step approval and simple optional balances; monthly payroll for monthly/daily/hourly rates, fixed-30 monthly deductions, reviewed overtime and bonus/adjustment; partial salary payments, dues and simple funded advances. Keep advanced accrual, LFA, loans and richer workflows in the design but outside ordinary initial setup.

Preserve company isolation, optional Employee-to-User links, permanent employee identity and reusable-code history. Preserve raw events separately from attendance/payroll. Pair IN/OUT across allowed devices in the same employee/shift stream, with historical device-scope authorization. Keep full-month monthly salary intact, financial sources posted once, and paid/finalized history correct through explicit corrections.

Implement the next incomplete phase with its acceptance checks. Avoid repeated scaffolding, speculative infrastructure and unnecessary rewrites. At each checkpoint update docs/PHASE_STATUS.md with implemented behavior, checks actually run, unresolved decisions and the exact next task. Regenerate schema artifacts when fields or relations change using node docs/scripts/build_schema.cjs from the project root. Keep documentation in docs/, including docs/schema_diagrams/ and docs/scripts/. These documents originated in an unrelated Polymer project; none of that application's code or business conventions should be inherited. Do not look for the old external_project_plans wrapper in this project.
```

Copy the contents of the original attendance_management_system planning directory into the new project's docs/ directory. Preserve its internal files and subdirectories; the renderer uses its script location rather than the old workspace path. This document is a handoff prompt; it does not itself create or move a chat, repository or running project.
