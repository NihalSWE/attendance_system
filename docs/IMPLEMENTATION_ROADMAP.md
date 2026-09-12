# Attendance product — phased implementation plan

## Platform corrections — 2026-09-07

User feedback corrected the onboarding contract. Read [UI_AND_ONBOARDING_CONVENTIONS.md](UI_AND_ONBOARDING_CONVENTIONS.md) and [PLATFORM_IMPLEMENTATION.md](PLATFORM_IMPLEMENTATION.md). Implemented one current master administrator per company with backend/database uniqueness, account editing, generated string codes/slugs, Bangladesh defaults, scoped Django admin forms, explicit `/platform/companies/`, centered responsive pages and readable four-space templates. No new environment variables. Existing data preserved; only the two shared demo memberships were ended with explicit user approval and audit entries. Full suite: 131 tests passed; the strengthened settings-admin valid-POST check also passed in the seven-test focused rerun. Browser creation/admin/feature flows and screenshots checked at 1440/768/375px. P1 remains in progress: next is company organization/scheduling writes and scoped authorization, then employee lifecycle pages. This supersedes earlier existing-account/role-picker examples.


> Current checkpoint (2026-09-07): P0 is verified; P1 platform onboarding is implemented, while company setup/employee writes and full scope enforcement remain outstanding. See PHASE_STATUS.md and PLATFORM_IMPLEMENTATION.md. Earlier statements about an uncreated project are historical planning context. Do not recreate scaffolding.

## Objective and current position

Documentation belongs in **docs/** at the new attendance project root. This file will be docs/IMPLEMENTATION_ROADMAP.md; the next chat enters through docs/NEXT_CHAT_START_HERE.md and updates docs/PHASE_STATUS.md. Bare document names below mean files in this docs/ directory. Preserve docs/schema_diagrams/ and docs/scripts/ when copying from the old planning folder.

Deliver a useful first release for companies managing employees, leave, attendance-based monthly payroll, and everyday salary payments. It should offer more than a minimal calculator while keeping routine screens and setup understandable.

The user does not currently have a biometric device. They expect access in roughly a week; this is an availability estimate, not a development deadline or guaranteed delivery date. Most product work can proceed using approved manual entries and a device simulator. Real device support must be verified separately.

Only planning documentation and schema diagrams currently exist. No attendance project, migrations, services, or product UI has been implemented. The 86-model catalogue is the intended broader design; it is not an instruction to build 86 management screens before delivering useful workflows.

This plan changes implementation order and first-release scope, not the existing core data relationships. Advanced features remain in the design and should have compatible schema foundations as dependencies require, but do not describe their runtime logic as implemented until their phase is complete.

Latest setup allocation: the user will create the Django project and all app skeletons themselves. The next chat verifies that work and starts with unfinished configuration/domain work. Do not rerun startproject/startapp or mark all of P0 complete merely because app folders exist.

Architecture decision: modular Django monolith, one PostgreSQL model/migration authority and separately scalable workers as needed. The user has selected FastAPI for future API modules, superseding the earlier DRF recommendation. Keep domain services reusable by Django pages, FastAPI routers and jobs. Add FastAPI wiring when required; it need not be a separate microservice. Future API authorization, versioning, pagination, stable identifiers, idempotent writes and external-ID/webhook contracts must be designed for the actual integration. Explicitly handle ORM execution, authentication and tenant context at the FastAPI boundary.

The user will create base_template alongside the 12 domain apps: 13 Django apps total, with the same 86 domain models. Shared layout/navbar/footer/sidebar includes and common styles belong there; feature pages remain in their domain apps. Use effective permissions/features to build navigation and enforce permissions independently in backend operations. See PROJECT_SETUP.md.

P0 must enforce the exact custom authentication contract: **accounts.User**, with the class named **User**, `AUTH_USER_MODEL = "accounts.User"`, and runtime `User = get_user_model()`. Do not create CustomUser or use the default concrete Django User. Use settings.AUTH_USER_MODEL in relation declarations and the historical registry in data migrations. AbstractUser inheritance remains compatible. Verify the resolved model label and migration references before applying initial migrations.

## First-release scope

The following is the recommended release boundary based on the user's request for functionality slightly beyond basic. Feature switches and sensible defaults keep optional workflows out of the way.

| Area | First release | Planned extension |
|---|---|---|
| Companies and access | Root-admin company activation, feature/package assignment, company membership, company/branch/department/self access, designation permission defaults/ceilings, employee exceptions | Elaborate delegation administration and subscription payment/invoicing integration |
| Employees | Optional login, permanent identity, reusable employee-code history, department/designation/branch changes, salary history | Broader HR lifecycle, documents, recruitment and performance workflows |
| Schedules | Company single shift or department shifts, required employee selection when needed, employee override, weekly offs and full-date holidays | Rotating rosters, arbitrary split shifts, complex scheduling |
| Leave | Full day, half day, hourly; paid/unpaid/partial pay decided by manager; attachment; one approval step; employee or HR submission; overlap checks; cancellation/amendment; simple annual/manual balance where enabled | Scheduled accrual, carry-forward/expiry automation, complex approval chains, encashment, compensatory-credit workflows, LFA |
| Attendance | Daily list, employee day/history view, IN/OUT sessions and breaks, incomplete-punch review, approved corrections, reviewed overtime, device scope rules | Rich analytics and additional vendor adapters |
| Payroll | Monthly generation for monthly/daily/hourly employees, fixed-30 monthly deduction basis, approved leave and holiday handling, configurable late/absence rules, editable approved overtime, optional bonus/adjustment, review/finalization, readable payslip | Rich component/formula builder, automated final settlement and jurisdiction-specific deductions/remittances |
| Salary management | Record actual payments, partial payments and dues, pay older unpaid records, simple salary advances with approved disbursement/recovery, adjustment history | Loan agreements/interest/rescheduling, advanced recovery options, direct bank/mobile-money transfer integration |
| Hardware | Simulator and manual inputs first; verified push adapter after device access | Multi-vendor certification, template backup/restore verified per device family |

The requested recurring-lateness examples belong in the initial calculation engine: one-day thresholds and repeated/consecutive qualifying days. Present a few understandable rule presets rather than a general formula editor. No automatic punitive policy should be enabled without a company administrator deliberately choosing it.

Simple leave balances mean annual upfront entitlement or approved manual credit, with remaining/used/reserved totals where tracking is enabled. Balance-free leave types remain possible. Neither an accrual calendar nor an LFA configuration should be required to approve ordinary leave.

Simple salary advances mean an approved amount, actual disbursement, and next-payroll or fixed-amount recovery, subject to outstanding balance and available-pay limits. Interest-bearing loans are a later workflow. Advance approval alone must not create recoverable principal.

## User-facing workflow

The first-run setup asks for company timezone/currency, branches (default branch created automatically), departments/shifts, weekends/holidays, and employees with compensation. Seed the minimal policy versions, base-salary component, default structure, and one-step approval rules behind this setup.

The employee form asks for person, organization, employee code, salary basis/rate, and optional software access. It must not require device enrollment before the employee can request leave or participate in manually reviewed payroll.

Initial navigation should use familiar tasks: Employees, Attendance, Leave, Payroll, Payments, and Settings. Advances can be a small enabled subsection of Payments. Tenant feature access and personal permission determine visibility. Disabled advanced features should not appear as empty required setup pages. This is a proposed future UI, not a change to the existing Polymer UI.

The monthly payroll screen should guide a user through: select month -> resolve attendance/leave exceptions -> calculate -> review changes and deductions -> approve/finalize -> record payment. A payslip and a salary due are different things; finalizing payroll does not mark salary paid.

## Phase sequence and dependencies

| Phase | Outcome | Dependencies | Device needed? |
|---|---|---|---|
| P0 | Verify user-created project; complete foundation and implementation decisions | User-created attendance workspace/apps | No |
| P1 | Company setup, access, employees and schedule foundations | P0 | No |
| P2 | Usable leave workflow with simple balances | P1 | No |
| P3 | Attendance engine using simulator/manual inputs | P1; approved LeaveDay integration uses P2 | No |
| P4 | Payroll generation, review and correction | P2 + minimum complete P3 | No |
| P5 | Salary payments, dues, adjustments and advances | P4 | No |
| D1 | Verify the actual device and integrate its adapter | P3 ingestion contract + physical device/vendor access | Yes |
| P6 | Pilot and first-release readiness | P1–P5; D1 only for a biometric-enabled pilot | Conditional |
| P7 | Advanced leave/salary/vendor capabilities | Stable first release and actual client need | Only hardware-specific work |

Main sequence: **P0 -> P1 -> P2 -> P3 -> P4 -> P5 -> P6**.

**2026-09-12 reorder (salary fast-track).** To produce salary the same day, a thin slice of P1 scheduling, P2 leave, P3 attendance and P4 payroll was pulled forward ahead of the rest of P1. This changes order only: every skipped item is listed in `PHASE_STATUS.md` ("Salary fast-track") and is still built, then connected back into the same attendance and payroll calculation. D1 starts when the hardware and P3 boundary are available; it can run alongside P4/P5 without changing their calculation services. If the device arrives earlier, document protocol/access facts and schedule the integration after P3. If it arrives later, continue the software phases.

Do not label each phase as a day or promise P0–P5 in the week before hardware arrival. Use completed acceptance criteria to schedule the next phase. During the wait, prioritize P0/P1, then usable leave, then the minimum attendance input/calculation contract required by payroll.

## P0 — bootstrap and settle the contracts

**Goal:** verify the user's standalone project/app skeletons and complete a small runnable domain foundation. Project/app scaffolding belongs to the user.

1. Read NEXT_CHAT_START_HERE.md, this plan, PROJECT_HANDOFF.md, then the field dictionary and relevant business specs. Inspect the actual target repository if one exists. Never copy Polymer-specific User, accounting, or tenancy conventions.
2. In the new attendance workspace, inspect the 13 user-created Django apps (including base_template) and PostgreSQL configuration, then implement missing configuration and custom accounts.User before the first migration. Do not recreate skeletons. Preserve existing migrations/data if already present. Keep one Django model/migration authority and service boundaries ready for the user's preferred FastAPI API layer. Do not create another ORM or introduce DRF.
3. Record a dependency map for the 86 proposed models. Build migrations in dependency order; use later nullable-FK additions to resolve cross-app cycles. Implement required vertical slices progressively. Optional supporting tables may be introduced early when a real FK requires them, without presenting them as finished features.
4. Establish shared-database/shared-schema tenancy with Company as tenant, request/job tenant context, scoped access services, database constraints for high-risk cross-company relationships, and an explicit plan for RLS before any shared production pilot. Record the enforcement mechanism and test it, including background jobs and implicit M2M links.
5. Establish audit events, UTC storage/local business dates, monetary precision, stable choices, idempotency conventions, and transaction boundaries. Restrict formula inputs to controlled data and reviewed expressions.
6. Record unresolved commercial/business choices without blocking unrelated work. In particular, user-seat versus active-employee billing remains a commercial decision; do not silently charge by the provisional employee-count recommendation.

**Decisions to resolve before the corresponding feature can finalize:** salary joining/leaving/rate-change proration; paid break and hourly conversion basis; daily/hourly holiday pay; leave-balance rules; penalty sequence/stacking behavior; policy change history for offline punches; repeated-punch ambiguity; and tenant enforcement implementation. For first-release organization/compensation changes, propose effective local-day boundaries to avoid unsupported mid-day payroll splits; record whether adopted. Do not silently truncate existing timestamp history.

Fixed-30 absence deductions are already agreed. Exact joining/leaving proration is not. The proportional 30-equivalent-unit example in older notes is a proposal, not an approved universal formula. Display any provisional payroll policy in setup and require it to be resolved before affected real payroll is finalized; continue ordinary full-month payroll work meanwhile.

**Completion evidence:** application starts against PostgreSQL; custom-user migrations apply from a clean database; model checks and migration-drift checks pass; tenant context and initial isolation checks exist; dependency/decision notes are saved. A catalogue or diagram by itself does not complete P0.

## P1 — companies, employees, permission and calendar foundations

**Goal:** a company can be set up and its employees have dependable historical organization, salary and schedule records.

**Primary models:** accounts, tenants, organization, employees, access_control, subscriptions, AuditLog, and the scheduling models. Implement only the commercial workflow needed for root-admin package/feature/capacity assignment; subscription invoicing/payment collection is deferred.

Tasks:

- Root admin creates/activates a company, assigns its feature package and owner membership; default branch/settings are created transactionally and idempotently.
- Company admins manage branches, departments and designation hierarchy. Provide practical seeded roles/actions; enforce parent permission ceilings and data scope server-side even if the full delegation editor is deferred.
- Create employees with optional user accounts, dated EmployeeAssignment and applicable EmployeeCompensation. Current employee code searches resolve to the current person, while explicit historical searches can show earlier holders.
- Implement transfers, salary revisions, termination/end dates, and linking a login without breaking employee history. No active employee should enter real payroll without valid compensation and schedule.
- Configure shifts, department-shift selection, employee overrides, recurring weekly offs, full-date special holidays and HolidayWorkAssignment. Make missing or conflicting schedule setup actionable.
- Persist company/branch/employee device-scope settings now, including their history, even though device configuration screens can be completed in P3.
- Seed a reusable synthetic demonstration: two companies, multiple branches/departments, different shift/rate bases, employees without logins, a transfer and a reused code. Use invented identities.

**Completion evidence:** a company can onboard an employee through a normal workflow; history survives code reuse and transfer; another tenant cannot read/change the employee through pages, IDs, exports or jobs; shift selection precedence works; salary history and calendar dates are queryable. Seeded features must not grant every employee every action.

## P2 — leave that is useful immediately

**Goal:** HR/managers can manage real leave without waiting for attendance hardware.

**Primary models:** LeaveType, LeavePolicy, LeavePolicyVersion, LeavePolicyTypeRule, EmployeeLeavePolicyAssignment, LeaveEntitlement, LeaveBalanceEntry, LeaveRequest, LeaveRequestSegment, LeaveDay, LeaveApprovalStep and LeaveAttachment. Encashment, compensatory-credit and LFA runtime workflows remain P7 even if their nullable references require initial table stubs.

Tasks:

- Provide company defaults and a short leave-type/policy setup. Support balance-free types and optional upfront/manual annual balances; hide accrual/expiry/LFA options initially.
- Employee or HR submits full-day, first/second-half-day, or hourly leave with optional private attachment and reason. The employee need not have a login for HR to act.
- A single authorized manager/admin approves or rejects. Approval sets paid/unpaid/partial percentage, validates available balance where applicable, and materializes the daily intervals consumed by attendance/payroll.
- Validate overlaps against other active requests/approved allocations, shift duration, employment dates and calendar. Reject invalid hourly intervals and inconsistent pay percentages with readable messages.
- Implement withdrawal before approval and audited amendment/cancellation after approval, with balance reservation release or ledger reversal as needed. Posted salary effects require the later payroll correction path, not silent changes.
- Show leave list, approval inbox, calendar/date breakdown and simple balance totals. A one-step screen still uses LeaveApprovalStep internally.

**Completion evidence:** full/half/hourly and partial-pay leave all work; attachments respect tenant/user access; concurrent approvals cannot spend the same balance twice; an employee without User can receive approved leave; cancellation restores only the appropriate balance; each approved date has correct covered/payable minutes for payroll. No device table or network connection is required to finish this phase.

## P3 — attendance without a physical device

**Goal:** produce trustworthy attendance inputs for payroll while keeping vendor integration replaceable.

**Primary models:** devices and attendance apps, including device mappings, raw messages/events, allocations/sessions/daily records, corrections and penalty models. Device-template export/deployment services remain unverified and disabled until supported hardware has been tested.

Tasks:

- Define a vendor-neutral ingestion result and adapter interface: authenticated source device, original payload, raw user identifier/time, optional trustworthy vendor event identity, and parsing result. Do not require all vendors to supply fields they do not have.
- Implement durable DeviceMessage ingestion and PunchEvent extraction, database idempotency, pending-job pickup/reconciliation, and Celery/Redis only where background work is useful. Successful ingestion must not depend on a worker completing before a device response.
- Create a clearly named simulation adapter and repeatable fixture publisher. Synthetic registered devices use the same ingestion, employee resolution, authorization and calculation services as future real adapters. Label synthetic data and confine it to development/demo tenants; it must not be presented as real device evidence in live payroll.
- Provide approved manual punch/correction entry for real device-free operation. Use AttendanceCorrection -> PunchAllocation; do not fabricate a biometric PunchEvent for a manager's manual entry. Create the schedule-derived daily record before attaching its correction when necessary.
- Implement employee/device identity mapping, all four device scopes and precedence exactly as DEVICE_ATTENDANCE_POLICY.md specifies, including historical authorization. Never equate physical enrollment with restricted-mode permission.
- Merge punches across allowed devices into an employee/shift stream, use event-time ordering and duplicate/repeat checks, handle cross-midnight shifts, and generate sessions, break intervals, daily results and review flags.
- Combine approved LeaveDay coverage, holiday work and schedule data. Only conclude absence when the work window/cutoff and reviewed input completeness justify it; a device outage or unsynchronized day is not proof of absence.
- Implement simple configurable penalty presets plus the required repeated/consecutive lateness behavior, reviewed overtime and auditable attendance corrections. Preserve calculation inputs/revisions needed by future payroll snapshots.
- Deliver daily attendance, employee/date punch-and-session history and an exception queue. Demonstrate IN on Device 1 and OUT on Device 10.

**Simulator scenarios:** normal work, break scans on different devices, a retry, an out-of-order batch, offline backlog, two devices using the same vendor event number, unauthorized source, missing enrollment, unknown user, reusable device user ID, clock ambiguity, missing checkout, approved leave, holiday work and a night shift.

**Completion evidence:** replaying a fixture does not duplicate attendance; device change does not reset IN/OUT; tenant boundaries and event-time authorization hold; approved manual input produces the same calculated attendance facts; missing punches remain reviewable; late input causes controlled recalculation. All previously documented raw/calculated separation rules still hold. This establishes the contract P4 needs, not proof of ZKTeco or Tipsoi compatibility.

## P4 — understandable monthly payroll

**Goal:** calculate, explain, review and finalize salary with correct history and a controlled correction path.

**Primary models:** PayrollSettings, PayrollPolicyVersion, SalaryComponent, SalaryStructure, SalaryStructureComponent, EmployeeSalaryStructureAssignment, PayrollPeriod, PayrollRun, PayrollRecord, PayrollCompensationSegment, PayrollDailyLine, PayrollLine and PayrollApprovalStep. Read LeaveDay, attendance, compensation, calendar and penalties through services.

Tasks:

- Seed one minimal salary structure/base component and safe default payroll policy. A normal monthly employee only needs a salary amount; advanced component/formula configuration stays hidden.
- Generate monthly payroll for monthly, daily and hourly rate bases. Use actual snapshotted schedule paid time for time conversion, not a hard-coded eight-hour day.
- Monthly unchanged full service receives the full monthly salary in February and 31-day months alike. Apply the agreed monthly-salary/30 absence deduction basis. Daily and hourly pay use eligible payable units, including approved paid leave/holidays according to their policy.
- Split calculation evidence at applicable organization/compensation/policy changes. Resolve P0's proration decisions before finalizing an affected real run.
- Apply paid/unpaid/partial leave, reviewed overtime, penalties and approved one-time bonus/adjustment inputs. Do not deduct unearned hourly time twice or count a minute once as normal pay and again as full overtime pay.
- Preview employee/date/line explanations and exceptions. Every number should lead to salary history, attendance, leave or approved adjustment evidence. Penalties affect pay without erasing presence facts.
- Provide review/approval/finalization with clear user labels mapped to stable model status codes. Draft recalculation invalidates stale approval. Finalization snapshots inputs and makes PayrollLine the single authoritative posting source.
- Include the minimum correction capability now: cancel/rebuild an unposted draft; use linked reversal/delta correction records after finalization. Rich automatic final-settlement workflows can wait, but immutable posted salary cannot.
- Generate a readable payslip and payroll register. Mark unpaid/review state accurately; full payment settlement is P5.

**Completion evidence:** the acceptance examples below pass; rerunning a draft does not create duplicate payable salary; a finalized run cannot be silently edited by a delayed task; conflicting or incomplete inputs are visible and block affected payroll finalization until reviewed; a correction preserves original records. No physical device dependency is permitted inside the calculation service.

## P5 — practical salary management

**Goal:** finish the money-management workflow around calculated salary without becoming a loan/accounting suite.

**Primary models:** SalaryPayment, SalaryPaymentAllocation, PayrollAdjustment, SalaryAdvance, AdvanceDisbursement and SalaryAdvanceRecovery, plus existing PayrollLine/source links.

Tasks:

- Add optional earning/deduction adjustments with amount, reason, target month, approval and history; post each source once through PayrollLine. Bonus can use the same approved one-time adjustment workflow with a bonus component.
- Record an actual cash/bank/mobile-wallet payment and reference. This records a transaction; it does not initiate an external transfer.
- Support full/partial payment and allocation to one or several of the employee's unpaid payroll records. Display current and older dues from obligations minus effective allocations; do not re-add last month's due as this month's earnings.
- Enable advances only when requested by a company admin. Simple flow: request/create -> approve -> record disbursement -> select next-payroll/fixed recovery -> review recovery -> post payroll -> show remaining balance.
- Recover only funded outstanding principal and only the permitted amount. Leave unrecovered amounts outstanding if net salary is insufficient. Draft recovery previews do not reduce the balance.
- Include reversal of an incorrectly recorded payment/recovery with audit history. Prevent concurrent allocation/recovery from exceeding available money or outstanding principal.
- Provide an employee salary statement showing compensation history, payroll, payments, dues, adjustments and advances; keep loans/remittances out of first-release navigation.

**Completion evidence:** one partial payment leaves the correct due; old dues can be settled without duplicate earnings; failed payments settle nothing; approved-but-undisbursed advances produce no recovery; payroll retries recover once; reversal restores the correct obligation/balance; payroll/source records use one FK direction as specified in MODEL_FIELD_DICTIONARY.md.

## D1 — real device verification, when available

**Goal:** prove the actual hardware/vendor integration can feed the existing P3 service reliably.

Start this lane when the device and necessary credentials are available. It does not block P2–P5 using manual/simulated inputs.

1. Record exact vendor, model, firmware, network options, protocol mode and access to vendor documentation/credentials. Verify whether the device supports direct push, vendor-cloud API, or another documented method. Tipsoi direct access versus cloud mediation is still unresolved.
2. Capture authorized sample payloads and the acknowledgement/retry contract. Document timestamp precision, device identity, event/user IDs, batches, pagination/cursors if relevant, and command flow. Sanitize reusable fixture payloads.
3. Implement the adapter around the existing ingestion interface, not a separate attendance/payroll engine. If fields or assumptions change, update mapping documentation and repeat contract tests.
4. Test one employee, multiple punches, device/user mapping, all supported scope rules, and offline/reconnect delivery while preserving normal fingerprint use. Verify outbound push to the server and the actual network/security requirements of the selected firmware.
5. Test duplicate retries, delayed batches, clock/timezone handling, service restart, authentication failure and durable pending work. Do not claim exactly-once delivery; prove idempotent processing under the device's actual delivery behavior.
6. Template retrieval/upload remains an optional, separately tested capability; the ordinary attendance adapter does not need it to pass.

**If only one physical device is available:** one-device ingestion can be verified, but actual two-device IN/OUT interoperability remains simulator-tested only. Mark it accordingly and obtain a second device before claiming physical multi-device verification.

**Completion evidence:** real-device test log with payload examples, verified model/firmware, timestamps, retry/offline results, known limits and documented adapter contract. If only cloud access is available, explain that boundary and required vendor access. Do not silently restore the old direct-polling design when push is unavailable; continue other phases and record the integration constraint.

## P6 — pilot and release gates

**Goal:** a small company can use the first-release workflows reliably and explain every leave/salary decision.

- Walk through onboarding, ordinary leave, cancellation, attendance exceptions, payroll, bonus/adjustment, partial payment, due settlement and an advance using a realistic synthetic month before live use.
- Verify role and tenant boundaries in browser/API/jobs/exports, concurrency-sensitive balances, background recovery and audit history. Confirm chosen database enforcement works with actual PostgreSQL and worker connections.
- Exercise backups/restores, private attachment storage, failure visibility, migration application and the volumes expected for the first client. Define tenant/file/raw-event retention and a recoverable correction process.
- Review screens with an initial user. Hide optional setup unless enabled, use plain labels, and show concise explanations for deductions or blocked actions.
- Test late leave/punch changes against draft and finalized payroll, payment mistakes, duplicate job delivery and a worker restart.

Two truthful release labels are possible:

- **Manual-attendance pilot:** P1–P5 and manual-source checks passed. Can manage reviewed real leave/payroll/payments without a device. Synthetic punches remain demo data only.
- **Biometric-attendance pilot:** the above plus D1 passed for the stated device/firmware. Any unverified second device/vendor/template feature is explicitly outside the verified claim.

The pilot must not automatically deduct salary simply because a disconnected device has not synchronized. Review data completeness and missing-punch exceptions before closing payroll.

## P7 — progressive extensions

Only prioritize these after the first release is usable, or when a concrete first-client need justifies moving one forward. Keep optional feature switches and continue using existing domain history/ledger/posting paths.

Suggested sequence:

1. More leave convenience: accrual/carry-forward/expiry, additional approval stages and delegation, compensatory credit and encashment.
2. LFA: claim eligibility/cycle, approval, attachment and one payroll earning; still distinct from leave usage and encashment.
3. Salary loans: disbursement, installments, repayments/allocations, optional interest, rescheduling, waivers and recovery limits.
4. Richer salary structures, controlled formulas, employee exit/final-settlement automation, relevant statutory deductions/remittances when requirements are known.
5. Additional vendor adapters, verified template backup/restore, reporting depth and scaling changes justified by measurement.

User-approved feature design remains preserved. Deferral means its workflow is not part of the first release, not that its history tables can be deleted or its future implementation claimed complete.

## End-to-end acceptance examples

Use a company policy with monthly salary 30,000, fixed deduction divisor 30 and clearly defined schedule/paid-time rules. The examples are test specifications, not claims of current functionality.

| Case | Expected result |
|---|---|
| Full eligible unchanged month with all expected work covered | Base monthly salary 30,000 even in February or a 31-day month. |
| Two unpaid full absence days and no other adjustments | Deduction 2,000; net 28,000. Do not divide by the 22 working days. |
| Two approved fully paid leave days instead of those absences | No deduction for those days; base 30,000. |
| One full leave day approved at 50% pay | Deduction 500 under this monthly policy. |
| One half-day unpaid leave | Deduction 500 when it covers half of that day's scheduled paid time. |
| Hourly unpaid/partial leave | Deduct only the unpaid proportion of covered scheduled paid minutes; no assumed eight-hour day and no duplicate absence deduction. |
| IN on Device 1, OUT on Device 10 | Same authorized employee/shift record and session; both sources preserved. |
| Approved late threshold or repeated/consecutive lateness | Exactly the configured deduction, with triggering dates and no duplicate sequence assessment after retry. |
| Two absence days, 300 approved overtime earning, 500 bonus, 300 adjustment deduction, 1,000 funded advance recovery | Net 27,500; every amount appears once with its source. |
| Pay 20,000 against the previous 27,500 record | Due 7,500. Recording the same payment retry does not pay another 20,000. |
| Later pay the remaining 7,500 | Due zero; this is settlement, not another earning. |
| Salary/department changes during a month; old code reused by another person later | Each person's identity/history remains distinct; compensation segments use the adopted dated proration rule. |
| Leave cancellation or device backlog after payroll finalization | Linked correction/review; original salary and payment evidence stay intact. |

## Progress reporting and change rules

Maintain docs/PHASE_STATUS.md at the end of each implementation session. Record the completed scope, commands/checks actually run, files/migrations changed, remaining work and one exact next action. Never mark a whole phase done because only its models exist.

Separate four statuses: design documented, schema implemented, workflow implemented, verified. Also separate simulator verification from real-device verification. At this planning handoff every implementation phase is not started and D1 is waiting for hardware.

Use the field dictionary as the current column/relationship reference, the device-policy document for scope/pairing, and this roadmap for sequence and first-release scope. Older narrative field aliases or example formulas cannot silently override a later explicit decision. If a structural gap is discovered, explain and document it before changing the model inventory; regenerate diagrams after accepted field/relation changes.

The next chat starts with P0 in the standalone attendance workspace. It should not begin by rewriting the full design, building a biometric adapter without evidence, or treating the unrelated Polymer project as the attendance application.
