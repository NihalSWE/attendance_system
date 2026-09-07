# Attendance Project Handoff — Read First

## Platform corrections — 2026-09-07

User feedback corrected the onboarding contract. Read [UI_AND_ONBOARDING_CONVENTIONS.md](UI_AND_ONBOARDING_CONVENTIONS.md) and [PLATFORM_IMPLEMENTATION.md](PLATFORM_IMPLEMENTATION.md). Implemented one current master administrator per company with backend/database uniqueness, account editing, generated string codes/slugs, Bangladesh defaults, scoped Django admin forms, explicit `/platform/companies/`, centered responsive pages and readable four-space templates. No new environment variables. Existing data preserved; only the two shared demo memberships were ended with explicit user approval and audit entries. Full suite: 131 tests passed; the strengthened settings-admin valid-POST check also passed in the seven-test focused rerun. Browser creation/admin/feature flows and screenshots checked at 1440/768/375px. P1 remains in progress: next is company organization/scheduling writes and scoped authorization, then employee lifecycle pages. This supersedes earlier existing-account/role-picker examples.


> Current checkpoint (2026-09-07): P0 is verified; P1 platform onboarding is implemented, while company setup/employee writes and full scope enforcement remain outstanding. See PHASE_STATUS.md and PLATFORM_IMPLEMENTATION.md. Earlier statements about an uncreated project are historical planning context. Do not recreate scaffolding.

## Purpose and current status

This is a separate, new multi-tenant attendance and salary product. These documents originated in a planning directory within the unrelated Polymer workspace. Their agreed destination is **docs/** in the new attendance project root, with schema_diagrams/ and scripts/ retained inside docs/. The user will copy the inner planning directory's contents there; the original source has not been renamed here. Do not inherit Polymer application conventions, business models, roles or dependencies.

The user wants another chat to continue without needing this conversation. Start with [NEXT_CHAT_START_HERE.md](NEXT_CHAT_START_HERE.md), [PHASE_STATUS.md](PHASE_STATUS.md) and [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md). Then read this business handoff, DATABASE_MODEL_PLAN.md, MODEL_FIELD_DICTIONARY.md, DEVICE_ATTENDANCE_POLICY.md and the relevant leave/salary rules. The starting instructions provide the full reading order and document authority rules.

Current deliverables are planning documents only. There is no runnable attendance Django project, migration set, PostgreSQL schema, complete business service layer, or verified device adapter yet. Described behavior is intended behavior, not already implemented behavior.

The current objective is phased implementation in a future standalone attendance workspace. The user has no physical device yet and expects one in roughly a week. First deliver usable leave, payroll and everyday salary management with simple screens/defaults; build attendance input/calculation using a simulator and approved manual entries until real-device verification is possible. This planning turn creates only the roadmap and handoff; it does not bootstrap the application inside Polymer.

Recommended first-release scope includes full/half/hourly paid/unpaid/partial leave with simple optional balances and one-step approval; monthly payroll across monthly/daily/hourly salary bases; reviewed overtime, bonus/adjustments, payments/partial payments/dues and simple advances. Advanced accrual, LFA, encashment/compensatory workflows, loans, richer structures and automated final settlement are later phase work. Preserve their proposed design while accurately reporting unimplemented logic. See the roadmap for dependencies and exact acceptance gates.

Latest setup instruction: the user will create the Django project and all app skeletons themselves. The next chat should inspect and preserve that setup, skip startproject/startapp, and implement the unfinished P0 custom-user/configuration/tenancy foundation before advancing. The custom User must be configured before the first migrations; do not delete existing migration history/data if the user has already migrated.

Explicit custom-user contract: define class **User** in **accounts**, not CustomUser or another name, and set `AUTH_USER_MODEL = "accounts.User"`. Runtime code imports get_user_model from django.contrib.auth and assigns `User = get_user_model()`. Never use the concrete default django.contrib.auth.models.User. Relations use settings.AUTH_USER_MODEL; data migrations use the historical apps registry. AbstractUser may be the superclass; the concrete model remains accounts.User. Every User reference in the existing 83-model design denotes this custom model.

Architecture is a modular Django monolith with reusable business services and separately scalable workers as needed. The user's latest choice is FastAPI for future API modules; the earlier DRF recommendation is superseded. FastAPI can be introduced around the Django-owned domain when APIs are needed, without an immediate microservice split. Explicitly integrate ORM transactions, authentication/permissions and tenant context; do not duplicate persistence models, migrations or payroll rules.

The user will create base_template alongside the 12 domain apps (13 apps total). It holds shared base templates, navbar/footer, optional group-specific sidebar includes and common styles/scripts, with no domain models. Feature pages stay in their owning apps; menu visibility derives from effective permissions/features and does not replace backend authorization. PROJECT_SETUP.md contains the commands and layout. Database inventory remains 83 domain models and 88 domain tables.

## Why push-based attendance is essential

The user previously built a ZKTeco K40 polling system, probably using port 4370. They reported 4–5 seconds just to check/connect, periodic downloads (for example every ten minutes), interference with fingerprint use, and repeated failures leaving devices unreachable for hours or a day. Locking and throttling did not make the approach reliable.

The new design must prefer devices initiating outbound push to a public server. Office internet access should be enough; do not design normal synchronization around an office public/static IP or inbound port 4370.

Offline punches should remain on a supported device and upload after connectivity returns. The server must tolerate retries, duplicates, old/out-of-order punches, and device clocks that differ from server time. Store device event time and server receipt time separately, along with time-zone interpretation.

Original discussion named ZKTeco F35, SenseFace 2A/3A/4A, and SpeedFace-V5L as TA PUSH/ADMS candidates. These are user-provided integration context, not verified procurement guarantees; check exact firmware/protocol/template support before committing an adapter.

The client's device appears to be Tipsoi Prompt P205. Direct push/webhook/API access versus Tipsoi Cloud remains unverified. Do not claim its cloud advertising proves that it can post directly to our server.

Biometric data may be saved optionally for replacement-device enrollment. Templates need format/vendor/version information and encryption. Saved templates are not automatically portable across vendors or all models.

## Confirmed product requirements

- Django + PostgreSQL forms the modular-monolith backend. Celery/Redis may process durable device messages and background calculations. The user prefers FastAPI for APIs when required; keep one authoritative core model/migration layer and shared business authorization.
- One Company equals one tenant. A root User administers companies and feature access without needing CompanyMembership.
- A company has at least one default branch. Branches contain departments, devices, and assigned employees. Departments and designations are dynamic.
- HR/Software/Sales are departments. HR Manager/Assistant HR/Senior Developer/Junior Developer are designations; designations have a parent hierarchy for delegated access.
- A company can own several devices, a branch can contain several devices, and departments can be served by selected devices.
- An employee can be registered on multiple devices. Attendance is counted only when the employee has the appropriate active device authorization at punch time. Unauthorized or unresolved punches are still preserved.
- Device authorization supports assigned_devices, department_devices, branch_devices, and company_devices. Effective precedence is dated EmployeeAssignment override, assigned/home Branch override, then CompanyAttendanceSettings default. DeviceEnrollment recognition, master enablement, and explicit restricted-device grant are separate. See [DEVICE_ATTENDANCE_POLICY.md](DEVICE_ATTENDANCE_POLICY.md) for exact fields, rules, history requirements, and examples.
- IN on Device 1 and OUT on Device 10 can form one AttendanceSession. Pairing merges allowed devices by employee/shift/event time, never separately per device. The attendance record retains employee assignment branch/department; each punch retains its source device/branch. Wider access does not provision biometric enrollment or permit another tenant's devices.
- Employee.id is permanent. EmployeeAssignment owns the reusable business code and dated branch/department/designation history. The same business code can belong to another employee after the earlier assignment ends.
- An Employee may have no User account. Linking a login later does not create a new employee or replace attendance history. A user can have membership in multiple companies.
- Company feature access does not grant every employee access to its actions. Designation defaults, designation limits, individual overrides, and organizational scope determine effective permissions.
- Shift modes: company-wide single shift or department shifts. Each active department requires a shift; if several exist, the employee requires an explicit shift assignment. Employee overrides remain supported.
- Preserve raw messages and punches. A DeviceMessage may contain many PunchEvents or no attendance punches. Employee resolution may occur after durable ingestion.
- Derive PunchAllocation, AttendanceSession, and AttendanceRecord separately. The proposed default is alternating IN/OUT within a shift window, excluding identified rapid repeats for calculation while retaining evidence.
- Alternating punches are inferred movement, not proof of physical entry or exit. An odd count or missing punch must remain incomplete/reviewable; do not fabricate a checkout at shift end.
- Calendar UI should later show daily totals and detailed entry/exit/break history. No UI implementation is currently requested.
- During future implementation, include simple leave/attendance/payroll/payment screens as vertical slices in the roadmap. The current request remains documentation only; no product UI is being built in Polymer.
- Weekly holidays and selected full-day special holidays are separate. An admin can require holiday work. Half-day holidays were explicitly excluded.
- Basic leave supports full-day, half-day, hourly, optional attachments, and manager-decided paid/unpaid/partial-paid percentages.
- Every active employee needs applicable compensation. Basic payroll supports monthly generation with monthly/daily/hourly rates, fixed-30 monthly deduction basis initially, editable approved overtime, and optional bonus.
- Preserve salary and employment history. A transfer or salary revision mid-month must retain its actual dated calculation context.
- Penalties can deduct time or day fractions, including a full day for 120 minutes late, or a day for repeated qualifying lateness across several days. Presence facts and financial penalties stay separate.
- Advanced leave/salary capability is now requested in the schema, but basic operation must remain simple. Automatically create minimal configuration; do not force users through accrual, structures, loans, or multi-stage approvals.
- Package access depends on included features and capacity. The current planning recommendation uses active Employee count, not only User logins. Do not present this billing interpretation as a legally/commercially settled contract if the user later clarifies otherwise.
- LFA means Leave Fare Assistance. It is optional, disabled by default, and separate from encashment. One LeaveFareAssistanceClaim reuses existing policies, approvals, attachments, payroll lines, and payments.

## Scope history and superseded choices

The model count is a design inventory, not a goal that justifies redundant tables.

- Original attendance/basic leave/basic salary draft: 42 models.
- Advances/recoveries/adjustments: 45.
- Hierarchical permissions and EmployeeIdentifier: 49.
- Packages/subscriptions: 52.
- EmployeeIdentifier removed when reusable code history moved into EmployeeAssignment: 51.
- Expanded leave and payroll: 82.
- Optional LeaveFareAssistanceClaim: 83.
- A proposed 54-model intermediate expansion was superseded, not added on top again.

Use current names: DeviceMessage replaces DeviceEvent; PunchEvent replaces RawPunch; PunchAllocation replaces AttendancePunch; PayrollDailyLine replaces PayrollAttendanceLine.

The earlier one-to-one message/punch relationship was wrong. The earlier employee-code-never-reused rule was rejected. The earlier one-payroll-record-per-month constraint was superseded by PayrollRun and explicit correction/off-cycle behavior.

A PayrollRecord header cannot describe every department/rate used in a transfer month. PayrollCompensationSegment holds that historical breakdown. Do not force DeviceEnrollment to close merely because an employment assignment changes; device authorization has its own effective dates.

The earlier unconditional department-device filter is superseded by the explicit device scope policy. Shared devices need no DeviceDepartment rows. Department mappings restrict department_devices mode, not branch/company mode. attendance_enabled is a hard per-enrollment disable in every mode; assigned_device_authorized is checked only for assigned_devices. No new model was added for these rules.

## What is designed versus what still needs engineering

Most business areas are identified. The next task must convert the model catalogue into a coherent executable design rather than copying every draft field literally.

Resolve and record these items during schema review:
1. Tenant enforcement: direct ownership, same-company foreign-key consistency, administrative exceptions, background-job scope, and whether PostgreSQL row-level security is used. A company_id column alone does not enforce isolation.
2. Access scope and delegation: exact company/branch/department/self filters, who may grant access, cycle prevention in designation hierarchy, and how hierarchy changes invalidate access. Do not assume Django is_staff grants company-wide business access.
3. Dates/history: a consistent boundary convention (prefer inclusive start/exclusive end), no overlapping employee-code assignments, and handling mid-day changes if required.
4. Shift/calendar history: shift definitions and department defaults can change; preserve the schedule used historically, not only today's settings. The exact precedence for weekly-off overrides needs to be defined.
5. Holiday work: HolidayWorkAssignment currently references a dated Holiday. Working on a recurring weekly off must also be representable without deleting the recurring rule.
6. Device ingestion: reliable idempotency identity per adapter, retry receipts/batches, outbox or durable job pickup, unknown employee resolution, device-user-number reuse by event time, clock correction, and raw retention.
   Device-scope authorization for offline punches must use historical assignment, scope, and enablement/grants. Current company/branch fields require structured AuditLog change history and initial snapshots; already-processed punches also retain authorization_snapshot. Missing historical policy must cause policy_unresolved/review, not application of today's wider access. See DEVICE_ATTENDANCE_POLICY.md before implementing this service.
7. Immutable source versus mutable resolution: preserve original payload/punch values; explicitly define which processing/mapping fields may change or need revisions.
8. Attendance correction: specify how approved manual entry/exit corrections become calculation inputs while raw punches remain untouched. JSON before/after fields alone are not the calculation engine.
9. Breaks/overtime/penalties: paid/unpaid break treatment, repeated-lateness windows across periods, threshold inclusivity, stacking/caps, and how missing punches affect salary. Treat prior 30-second windows and thresholds as examples/configuration, not universal policy.
10. Salary formulas: fixed-30 deduction basis is agreed; exact joining/leaving/mid-period-rate proration remains a company policy decision. The earlier proportional 30-equivalent-unit example is a proposal, not a separately confirmed business rule. Full eligible unchanged months must reconcile to normal monthly salary.
11. Financial sources: use one authoritative posting path; prevent duplicate earnings, salary payment allocations, and advance/loan recovery. Distinguish an agreement, disbursement, scheduled installment, and actual repayment.
12. Loan balance-only waivers currently use an explicitly typed PayrollAdjustment mode. Review whether this remains coherent; never produce employee salary from a principal waiver unintentionally.
13. Feature/subscription history: current subscription snapshots price/limit but mutable PackageFeature rows can change active access. Choose explicit package versioning or a feature snapshot strategy and document it.
14. Policy JSON and relational fields: use actual validated foreign keys for model references rather than hiding unconstrained IDs in JSON. JSON is for variable rule parameters or snapshots, not a substitute for tenant-safe relations.
15. Defaults and validation: field nullability, money precision, deletion behavior, effective-period constraints, idempotency keys, indexes, encryption storage, and transaction locks.
16. Company-specific statutory rates, provider reporting, final-settlement formulas, and device API details remain requirements/integration work; do not invent a universal answer.

These points are not new approved models. If a necessary structural change emerges, explain why and update the count/relationships consistently. Preserve the user's simple initial workflow.

## Completed design deliverables

- IMPLEMENTATION_ROADMAP.md now defines P0–P7 plus the conditional D1 real-device lane, the first-release feature boundary and measurable completion criteria. NEXT_CHAT_START_HERE.md provides the exact starting task and copyable prompt; PHASE_STATUS.md records that implementation has not started.

- `FULL_DATABASE_SCHEMA.md`, `ATTENDANCE_SCHEMA.dbml`, `ATTENDANCE_SCHEMA.svg`, and `ATTENDANCE_SCHEMA.html` now show the entire proposed schema with every physical field expanded, including inherited fields and implicit junction tables. The local HTML viewer supports search, zoom, and selecting a table's FK connections. Twelve app diagrams are in `schema_diagrams/`.
- These field-level outputs come from one documentation renderer, `scripts/build_schema.cjs`; its resolved schema is also saved in `schema_diagrams/SCHEMA_DATA.json`. They contain 88 tables, 1,615 columns, and 453 FKs after the device-scope additions. Type/nullability clarifications and constraints not expressible in basic DBML are documented in `FULL_DATABASE_SCHEMA.md`.

- `MODEL_FIELD_DICTIONARY.md` now defines proposed fields and relations for all 83 models. It is the field-level contract, not executable Django code.
- `DATABASE_SCHEMA.md` now maps the proposed PostgreSQL relationships, ER views, ownership paths, physical M2M junctions, constraints, indexes, idempotency, retention, and scaling considerations.
- The 83 Django model classes imply 88 attendance-domain tables because five currently proposed M2M fields use implicit junction tables. Django framework/auth tables are additional.
- No project, app, model class, migration, SQL DDL, service, or UI has been created.

## Intended future implementation deliverables

Follow the roadmap starting with P0 in a standalone attendance workspace. Do not attach application code to the surrounding ERP or treat this current documentation task as authorization to create an application here. Once a target attendance workspace is supplied for implementation, proceed with the authorized phase and its checks.

- Django models grouped by the documented apps, with appropriate fields, FK/one-to-one/M2M through tables, related names, and help text.
- Review the field dictionary while writing the real models; record and explain any implementation-driven change instead of silently diverging.
- Explicit database constraints/indexes plus documented service-level rules for invariants that span rows.
- Initial migration files and a PostgreSQL schema derived from those migrations.
- Readable overall and per-app ER diagrams matching the actual generated models, not independently invented table lists.
- Minimal setup instructions and tests covering tenant boundaries, code reuse/history, duplicate ingestion, leave balances, payroll duplication, and repayment allocation.
- A decision log for assumptions resolved during implementation and a truthful status report distinguishing model/migration work from unimplemented services/adapters/UI.

Readiness means migrations can actually be generated/applied and important constraints verified. Merely writing class definitions does not implement the complete attendance/payroll application.

## Transfer instructions

Copy all contents of external_project_plans/attendance_management_system/ into **<new attendance project>/docs/**, preserving schema_diagrams/ and scripts/. Do not retain the old wrapper directories unless deliberately wanted for archival purposes. Use the copyable prompt in **docs/NEXT_CHAT_START_HERE.md**. The next chat should check docs/PHASE_STATUS.md, follow docs/IMPLEMENTATION_ROADMAP.md, preserve the confirmed decisions and update the checkpoint after each implementation session. A missing device blocks only real-device verification; it does not block leave/payroll/manual-attendance work. Bare document names in this handoff refer to files inside docs/; relative Markdown links remain unchanged.

Do not copy old account-usage percentages into project requirements. Weekly usage is account-wide, changes over time, and cannot be predicted accurately from a model count.
