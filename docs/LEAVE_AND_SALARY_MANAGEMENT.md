# Leave and Salary Management — Expanded Draft

> This belongs to the separate attendance project. It is temporarily stored in the Polymer workspace and introduces no application code or migrations there.

**Reading guide:** Start with the [plain-language model explanations](DATABASE_MODEL_PLAN.md#app-and-model-inventory). Each of the 86 models is explained with its purpose, related tables, and an example. This document is the companion field and workflow reference; its compact field tables are not intended as the first introduction to the models.

Status: proposed expanded design, superseding the earlier deliberately minimal leave and payroll scope. The expansion was requested on 2026-09-05. The expanded scope added 12 leave models and 19 payroll models to the 51-model baseline (82). The subsequent optional LFA claim adds one further model: the current total is 83 models across the same 12 apps, including 15 leave and 26 payroll models. The three previously proposed SalaryPayment / EmployeeLoan / LoanRepayment additions were not part of that 51 and are included here once. The count later moved to **86**: moving Department and Designation under root ownership added CompanyDepartment, CompanyDesignation and DepartmentPermission. The arithmetic above is left as written because it records how the 83 was reached.

This is a model and workflow design, not a statement of local employment, tax, or payroll rules. Statutory rates and eligibility must be configured from applicable requirements before implementation. It does not promise automatic statutory filing or bank transfers.

## Release order and field-name authority

[IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md) now controls delivery order and the first-release boundary. Initial leave includes full/half/hourly duration, paid/unpaid/partial pay, attachments, one-step approval, cancellation/amendment and simple optional annual/manual balances. Initial payroll/salary management includes calculation, reviewed overtime, bonuses/adjustments, payslips, partial payments/dues, simple advances and minimum corrections/reversals. Advanced accrual, LFA, encashment/compensatory workflows, loans, rich formulas and final-settlement automation remain planned later phases.

No physical device is currently available. Approved manual attendance and a clearly separated simulator feed the same calculation services; real-device integration has its own verification lane. Do not interpret this expanded domain design as a requirement to expose all advanced settings to the first users.

The compact field tables below are earlier conceptual summaries. Use MODEL_FIELD_DICTIONARY.md for current exact field names, FK direction and nullability. In particular PayrollLine points to adjustment/recovery/encashment/LFA sources; do not add a duplicate forward payroll_line FK to each source merely because an older summary names one. The roadmap flags proration examples that still need an explicitly adopted company policy.

## 1. Shared ownership and history

Every model described here has an id and company_id. All tenant-owned foreign keys must point into the same company. Employee-related rows reference permanent Employee.id; reusable business codes never identify the person in foreign keys.

Use created_at/created_by and, for editable drafts, updated_at/updated_by. Monetary values use decimal amounts in company currency. Do not use floating-point amounts. Posted balance and financial entries are append-only; corrections use linked compensating entries and reasons.

EmployeeAssignment supplies historical branch, department, designation, and business code. A leave request's submitting branch is an audit snapshot, not necessarily the branch applicable to every leave date. LeaveDay and PayrollCompensationSegment preserve the actual assignment for each covered date or interval.

A monthly PayrollRecord cannot accurately represent several departments with one assignment foreign key. Segments own the breakdown; optional header branch/department are display snapshots only. DeviceEnrollment may span a transfer and should keep its own authorization history; do not close it solely because a reporting assignment changes.

No additional permanent EmployeeIdentifier or generic EmployeeHistory model is introduced.

## 2. Leave models and main fields

For the explanation of each leave table, see the `leaves` app in the [model guide](DATABASE_MODEL_PLAN.md#9-leaves--15-models). The rows below list the proposed fields after those explanations.

Common company/audit fields above are omitted below.

| Model | Main fields / relationships |
|---|---|
| LeaveType | code, name, description, is_active; unique code per company |
| LeavePolicy | code, name, optional branch_id/department_id, is_company_default, is_active |
| LeavePolicyVersion | policy_id, version, effective_from/to, leave_year_start, eligibility_defaults, approval_route_definition, optional lfa_enabled/lfa_configuration, status |
| LeavePolicyTypeRule | policy_version_id, leave_type_id, balance_unit, annual_allowance, accrual_frequency/rate, joining_proration, probation/minimum_service, minimum/maximum_request, notice, allow_negative_balance, pay_percentage_default, approval_pay_override_allowed, holiday/weekend_counting, carry_forward_limit, carry_expiry, encashment_limit/basis |
| EmployeeLeavePolicyAssignment | employee_id, policy_id, effective_from/to, reason |
| LeaveEntitlement | employee_id, leave_type_id, period_start/end, unit, negative_limit; one account per employee/type/period |
| LeaveBalanceEntry | entitlement_id, entry_kind, balance_delta, reservation_delta, effective_at, expires_at, source_grant_id, source_idempotency_key, leave_day_id/request_id/encashment_id/compensatory_credit_id where applicable, reversal_of_id, reason |
| LeaveRequest | employee_id, policy_version_id, submission_assignment_id, action_type (new/amend/cancel), original_request_id, reason, status, submitted_at, supersedes_id, decision_snapshot |
| LeaveRequestSegment | request_id, leave_type_id, local_start/end_date, start/end_at where applicable, duration_type, half_day_part, requested_pay_percentage, original_segment_id for changes |
| LeaveDay | request_segment_id, entitlement_id nullable for untracked leave, employee_id, assignment_id, policy_type_rule_id, work_date, covered_start/end_at, scheduled_minutes_snapshot, leave_minutes, balance_units, approved_pay_percentage, status, supersedes_id |
| LeaveApprovalStep | request_id or encashment_id or compensatory_credit_id or lfa_claim_id (exactly one), stage_number, approver_membership_id, delegation_from_id, status, decided_at, comment |
| LeaveAttachment | request_id or lfa_claim_id (exactly one), private_storage_key, original_filename, content_type, size, checksum, uploaded_by_id |
| LeaveEncashment | employee_id, entitlement_id, policy_version_id, requested/approved_units, conversion_rate_snapshot, amount, target_period_id, status, payroll_line_id nullable until posted, reason |
| LeaveCompensatoryCredit | employee_id, source_attendance_id, entitlement_id, policy_rule_id, source_work_minutes, approved_credit_units, expires_at, treatment, status, payroll_line_id nullable if subsequently converted to cash |
| LeaveFareAssistanceClaim | employee_id, employee_assignment_id, policy_version_id, qualifying_leave_request_id nullable, benefit_date, cycle_start/end, occurrence_number, amount_basis_snapshot, compensation_id nullable, base_amount_snapshot, calculated_amount, approved_amount, currency, target_period_id nullable, eligibility_snapshot, status, approval reason/timestamps, idempotency_key |

LeavePolicyTypeRule is unique per policy version and type. A policy version is frozen once used by approved transactions. Updating policy creates a version rather than editing old approved meaning. Effective employee policy assignments may not overlap.

Resolve explicit employee policy first, then department, branch, company default. Snapshot the resolved version for the relevant dates; a request crossing a policy boundary can produce LeaveDay rows under different applicable rules. Department transfer does not recreate the person or discard balances.

Approval routes may be stored as a validated structured definition within a policy version to avoid adding separate workflow-definition tables. Materialize the actual approvers in LeaveApprovalStep at submission. LeaveApprovalStep also handles encashment, compensatory-credit, and LFA claim approval through typed nullable foreign keys and an exactly-one-target constraint.

## 3. Leave balance and accrual

LeaveEntitlement is an account identity, not a manually editable remaining-balance number. LeaveBalanceEntry is the source of truth.

Typical postings:
- Opening allocation, accrual, manual credit, carry-in, compensatory credit: positive balance_delta.
- Taken leave, expiry, carry-out, encashment: negative balance_delta.
- Pending/approved future request: positive reservation_delta.
- Rejection, withdrawal, cancellation, or consumption of a reservation: negative reservation_delta.
- Reversal: opposite deltas linked to the original entry.

Available = sum(balance_delta) - sum(reservation_delta), subject to configured negative allowance. A pending request can reserve balance according to policy. Approval must not reserve it again. When leave is consumed, release the reservation and debit the same quantity atomically.

Lock the entitlement account while reserving/posting to prevent two simultaneous approvals spending the same balance. Every scheduled accrual and consumption has a unique source key, so background retries do not credit/debit twice.

Balance units are explicit:
- Minutes for hourly balances.
- Decimal days for day-based balances; convert a covered interval using that day's scheduled paid minutes.
- A half-day consumes 0.5 day, with the interval defined by the actual shift.
Do not convert every day using a hard-coded eight-hour divisor. Save conversion inputs for history.

Credits can have expiry dates and a source grant. Debits reference the consumed grant; split a debit when it consumes multiple grants. Consume earliest-expiring eligible grants first. This supports carry-forward expiry without erasing unrelated newer credits.

Carry-forward posts a linked carry-out and carry-in with caps and expiry. Manual adjustment requires a reason and authorized actor. The system distinguishes available balance, reserved balance, used leave, expired leave, and encashed units.

No balance account is required for an unlimited untracked unpaid leave type. Unpaid leave is still represented by requests and LeaveDay.

## 4. Leave application, approval, cancellation, and exceptions

Supported operations:
- Full-day, half-day, hourly, multi-day, and mixed-segment leave.
- Paid, unpaid, and partially paid decisions by authorized managers within policy limits.
- Sick/casual/annual/parental and other company-defined types.
- Attachments and medical/application evidence.
- Sequential or single-step approval, rejection, delegation, withdrawal, and partial cancellation.
- Joining-year proration, probation eligibility, annual/up-front or periodic accrual, carry-forward, expiry, negative balances where allowed, encashment, and compensatory leave.
- Company/branch calendar handling, weekends, holiday exclusions or explicit inclusion, and overnight work dates.

Workflow:
Draft -> Submitted (reserve if configured) -> Pending approvals -> Approved -> dated consumption.
Rejected/withdrawn requests release reservations.
Amendment/cancellation is a new linked LeaveRequest action, with its own approval trail. The old approved version remains auditable.

Overlap is checked by employee and actual covered interval, not merely by request start date. Several non-overlapping hourly leaves may occur on one day. LeaveDay therefore is not unique only by request/date; use segment/work-date/slice identity.

At final approval resolve actual pay percentage and covered minutes. A partial cancellation reverses only affected units and creates a replacement allocation where necessary. Cancellation after leave was consumed posts a compensating ledger credit rather than deleting the original debit.

Changed calendars or approved leave conflicting with existing punches create a review case. Do not count the same interval as both paid work and paid leave. Do not penalize lateness during an approved protected leave interval unless an explicitly approved policy requires different treatment.

Changing approved leave after finalized payroll creates a payroll correction/arrears adjustment; it never silently changes the paid record.

Compensatory leave references exact source attendance and credited duration. A source interval cannot automatically produce both overtime pay and compensatory credit unless the company explicitly allows both. Encashment reserves eligible balance, then debits it and posts an earning once payroll is finalized. Reversals restore units through compensating entries.

## 4A. Optional Leave Fare Assistance (LFA)

### Basic operation stays available

LFA is disabled by default. Basic leave approval never requires an LFA claim, benefit cycle, or LFA amount. Basic salary generation skips this feature when disabled or when no approved claim exists.

The basic setup creates minimal company policy defaults and a base-salary component automatically. One-step approval, full/half/hourly leave with manager-decided pay percentage, base monthly/daily/hourly salary, existing attendance rules, overtime, and optional bonus remain usable without configuring salary structures, accrual, loans, or LFA. The expanded schema supports advanced options progressively.

### Policy configuration: reuse LeavePolicyVersion

Add lfa_enabled (default false) and optional validated lfa_configuration to the existing version. These settings are not a new policy model. Used versions remain immutable.

Configuration supports:
- Eligibility: minimum completed service, allowed employment statuses, optional qualifying leave types, and minimum approved scheduled leave duration.
- Leave dependency: qualifying approved leave required or benefit available independently. If required, qualifying_leave_request_id must refer to the same employee/company. LFA approval never approves that leave implicitly.
- Payment eligibility point: after leave approval, or after completed qualifying leave. The latter must be rechecked before payroll finalization.
- Amount method: fixed amount, percentage/multiple of an explicitly selected compensation basis, or manager-entered amount within a cap. No amount, rate, or legal entitlement is assumed by default.
- Amount reference date: benefit_date by default; store the resolved effective compensation and basis snapshot. Daily/hourly rates cannot be treated as monthly salary without an explicit conversion rule.
- Frequency: calendar year, configured leave year, or employment anniversary year; maximum awards per cycle defaults to one when enabled.
- Joining/leaving proration, amount cap, rounding, minimum leave measurement (scheduled days or minutes), approval route, and leave-cancellation treatment.
- salary_component_id: a company earning component for LFA, with company-configured tax treatment. Leave-policy configuration does not decide tax law.

Reuse existing leave-policy assignment precedence for company/branch/department/employee scope. Benefit-cycle identity belongs to the employee and LFA benefit, not to a department or policy version. A transfer or policy revision must not reset the already-used allowance. Cycle-basis changes require explicit reconciliation against previously awarded benefits so overlapping cycles cannot award twice accidentally.

### One new model: LeaveFareAssistanceClaim

This is both an employee application and an HR-entered award. HR can create it for an employee without a User account.

Core references:
- company_id and permanent employee_id.
- employee_assignment_id snapshot at benefit_date.
- policy_version_id and eligibility_snapshot.
- qualifying_leave_request_id, nullable unless policy requires leave.
- compensation_id and amount_basis_snapshot where compensation determines the amount.
- cycle_start, cycle_end (exclusive), and occurrence_number.
- calculated_amount, approved_amount, currency, reason, and optional target payroll period.

Lifecycle: draft -> submitted -> approved/rejected -> posted, with withdrawal/cancellation permitted before posting. Store approval decisions in LeaveApprovalStep and optional documents in LeaveAttachment. Submitted calculated inputs and final approved values are preserved; changes to submitted values invalidate prior approval or require a new audited decision.

An award is separate from payment: posted means the earning was included in finalized payroll, not that cash was paid. Rejected/withdrawn/cancelled unposted claims do not consume an award allowance; approved/posted claims reserve/use an occurrence slot.

A company's LFA toggle stops new awards. Disabling it does not silently cancel already approved awards or unpaid finalized liabilities; cancellation/recovery requires the normal explicit workflow.

### Existing payroll path

Approved LeaveFareAssistanceClaim
-> PayrollLine (earning SalaryComponent, direct lfa_claim_id)
-> PayrollRecord
-> SalaryPayment and SalaryPaymentAllocation

Reuse the existing payroll run for payment with monthly salary, or use an existing off-cycle run if it should be paid before leave. No second LFA payment model is needed.

Do not create a PayrollAdjustment for the same ordinary LFA award. Do not configure the LFA component to pay automatically every month. The approved claim creates the earning once; PayrollAdjustment is reserved for subsequent authorized corrections.

PayrollLine.lfa_claim_id is the authoritative posting relationship. The claim's posted state is a synchronized summary, not a second financial balance. Draft generation and finalization use the source key and claim lock to prevent the same claim appearing in two payable runs.

SalaryPaymentAllocation settles the payroll record as a whole. For a separately traceable LFA payment/due, use a dedicated off-cycle record containing that award; a mixed monthly salary payment does not imply a specific component was paid first. No independently maintained LFA cash balance is introduced.

### Balance, correction, and duplication rules

LFA pays a benefit; it does not consume or reserve LeaveEntitlement. Any qualifying leave consumes its own balance through the existing LeaveDay/LeaveBalanceEntry path. Leave encashment separately converts unused leave to money.

Before approval and finalization:
- Validate company/employee equality on policy, assignment, compensation, leave request, component, and target period.
- Lock the employee's LFA award processing transaction, derive the cycle/slot, and enforce one active submitted/approved/posted claim per (company, employee, benefit cycle, occurrence_number). A conditional uniqueness constraint and service validation enforce the limit; drafts can coexist but cannot both reserve the same slot.
- Check prior awards across policy revisions and organizational transfers, not just the current department.
- Check idempotency keys and permitted approved amount/currency.
- Ensure exactly one original posted earning source per claim. Correction lines explicitly reference original_line_id and never create a second original award.

If qualifying leave is cancelled before posting, stop/review the unposted award according to the snapshotted policy. After posting or payment, preserve the original claim/line and create an approved linked PayrollAdjustment or correction run if recovery is required. Never automatically erase the payment, cut salary, or award a replacement benefit.

A payment reversal reopens the existing salary obligation; it does not grant a new LFA award. A reversed/corrected award's frequency slot is released only through an authorized policy decision after reconciliation.

### Review scenarios for implementation

1. LFA disabled: ordinary leave approval and salary generation need no extra fields.
2. HR grants a fixed amount to an employee without a login; one approval produces one earning.
3. A compensation-derived award keeps its approved snapshot after a later salary revision.
4. Policy requiring approved/completed leave rejects or holds a claim without qualifying leave.
5. Repeated payroll generation and concurrent regular/off-cycle finalization produce one award.
6. Department/code transfer and policy version change do not create another annual allowance.
7. Partial salary payment, failed payment, and payment reversal preserve the original obligation.
8. Leave cancellation before and after LFA posting follows review/correction rules and preserves audit history.
9. Leave encashment plus LFA remains two distinct earnings, each with its own approved source.
10. Cross-company policy, leave, component, or payroll references are rejected.

## 5. Payroll models and main fields

For the explanation of each payroll table, see the `payroll` app in the [model guide](DATABASE_MODEL_PLAN.md#10-payroll--26-models). The rows below describe fields; the guide explains why salary calculation, approval, actual payment, and repayment each have their own records.

| Model | Main fields / relationships |
|---|---|
| PayrollSettings | company pay-cycle defaults, currency, default policy selection, payment preferences |
| PayrollPolicyVersion | version, effective dates, frequency, monthly_divisor (30 initially), day/hour conversion basis, paid holiday/leave rules by pay basis, proration, overtime rates/rounding, recovery cap, negative-net handling, approval route, component/statutory rule configuration |
| SalaryComponent | code, name, category (earning/deduction/employer_contribution), taxable flags, recurring flag, statutory/provider classification |
| SalaryStructure | name, code, description, active status; reusable component package |
| SalaryStructureComponent | structure_id, component_id, version/effective dates, fixed_amount or restricted_formula, calculation_basis, order, proration and rounding; historical rows are retained |
| EmployeeSalaryStructureAssignment | employee_id, structure_id, effective_from/to; one applicable assignment at a time |
| PayrollPeriod | start/end dates, pay_date, cycle, status, label; replace year/month-only uniqueness with company/cycle/date-range identity |
| PayrollRun | period_id, run_type (regular/off_cycle/correction/final_settlement), revision, source_run_id, status, input_cutoff, generated/finalized timestamps, idempotency_key |
| PayrollRecord | run_id, employee_id, earnings_total, deductions_total, employer_contribution_total, net_payable, currency, status, historical display snapshots |
| PayrollCompensationSegment | payroll_record_id, employee_assignment_id, compensation_id, salary_structure_assignment_id, policy_version_id, start/end dates, rate/basis snapshots, source_revision snapshots |
| PayrollDailyLine | segment_id, attendance_record_id nullable, work_date, scheduled/worked/leave/payable units, attendance revision, base earning calculation, excluded and payable intervals, deduction candidates and breakdown |
| PayrollLine | payroll_record_id, segment_id nullable, component_id, description, quantity, rate, amount, formula_snapshot, source_key, typed nullable source foreign keys (including lfa_claim_id), original_line_id for correction, service period |
| PayrollApprovalStep | run_id, stage_number, approver_membership_id, status, decided_at, comment |
| SalaryPayment | employee_id, amount, currency, payment_date, method, destination_snapshot, transaction_reference, idempotency_key, status (pending/confirmed/failed/reversed), reversal_of_id |
| SalaryPaymentAllocation | salary_payment_id, payroll_record_id, allocated_amount, reversal_of_id; source payment and target payroll employee/company/currency must match |
| SalaryAdvance | employee_id, approved_amount, recovery_method, installment_amount, recovery_start_date, status, approval actor/time, reason |
| AdvanceDisbursement | salary_advance_id, amount, paid_at, method/reference, status, idempotency_key, reversal_of_id |
| SalaryAdvanceRecovery | salary_advance_id, payroll_line_id nullable, direct_receipt_reference nullable, amount, method, effective_at, status, reversal_of_id |
| EmployeeLoan | employee_id, approved_principal, interest_method/rate, term, repayment_frequency, recovery_start_date, agreement, approval details, status |
| LoanDisbursement | loan_id, amount, paid_at, method/reference, status, idempotency_key, reversal_of_id |
| LoanInstallment | loan_id, schedule_version, installment_number, due_date, principal_due, interest_due, status, superseded_by_id |
| LoanRepayment | loan_id, amount, source (payroll/direct), payroll_line_id nullable, receipt_reference, received_at, status, idempotency_key, reversal_of_id |
| LoanRepaymentAllocation | repayment_id, installment_id, principal_amount, interest_amount, reversal_of_id |
| PayrollAdjustment | employee_id, target_period_id, component_id, amount, direction, reason, original_payroll_line_id nullable, loan_id nullable for explicit balance correction/waiver, service_dates, status, approval details, source_key |
| PayrollRemittance | recipient/provider identity and payment reference snapshot, amount, currency, paid_at, status, idempotency_key, reversal_of_id |
| PayrollRemittanceAllocation | remittance_id, payroll_line_id, amount, reversal_of_id; only eligible statutory/benefit liability lines |

Existing EmployeeCompensation remains the only owner of the employee's base monthly/daily/hourly rate. SalaryStructureComponent owns additional components. A base-salary component refers to EmployeeCompensation; it must not independently store another conflicting base salary.

Simple cases use one employee compensation record and a base-salary component. More detailed companies assign structures with allowances and deductions. An employee-specific structure can supply recurring overrides without an extra override model.

Employer contributions are company costs and third-party liabilities, not automatic employee deductions.

## 6. Earnings, deductions, proration, and overtime

Salary generation frequency and rate basis are separate. A monthly run can include monthly-, daily-, and hourly-rated employees.

Monthly employees with full-period service and no deductions receive the monthly amount. The initial deduction divisor is 30:
daily deduction rate = monthly salary / 30.
An hourly deduction rate also requires configured scheduled paid hours; do not assume an eight-hour shift.

Do not calculate a full February salary as 28/30 or a full 31-day month as 31/30. The fixed divisor is a deduction/proration convention, not an instruction to change the normal full-month amount.

For mid-month joining, leaving, or salary changes, split into PayrollCompensationSegment and use the snapshotted proration convention. Under a fixed-30 monthly policy, divide the full month's monthly base into 30 equivalent units proportionally across the actual calendar span; then apply service eligibility and the segment rate. Alternatively a configured convention may be selected, but all segments for unchanged full-month compensation must reconcile to exactly one monthly salary.

Daily earnings use payable day units. Hourly earnings use payable minutes / 60. Policy defines paid holidays and paid leave for each basis.

Approved overtime is separate from regular payable hours. Store calculated and approved time, rate basis, multiplier, and amount snapshot. The same minute must not be paid once as regular hourly time and again as a full overtime rate unless the second amount is explicitly only a premium.

SalaryComponent and PayrollAdjustment support allowances, recurring benefits, bonuses, incentives, reimbursements, arrears, corrections, tax/benefit deductions, authorized penalties, recoveries, and employer contributions. Restricted formulas refer to named inputs/components; do not store executable Python from settings.

Use a dependency order and reject circular component formulas. Store rounding at component and final-total levels. Any rounding difference is an explicit line.

PayrollDailyLine owns calculation evidence, not an additional payable balance. PayrollLine is the sole posting source for totals. PenaltyAssessment and leave are sources for lines, not a second set of amounts added again.

For an hourly employee, unpaid absence may already reduce base earnings. Do not deduct those unearned hours again. If a full-day penalty is intended to forfeit an entire day's entitlement, calculate only the additional deduction needed after the absence/unpaid-leave loss, according to the configured stacking policy.

Consecutive-workday penalties must define whether leave, holidays, absent days, or nonqualifying workdays break or skip a sequence, whether sequences cross periods, and whether groups overlap. Save rule versions, triggering dates, and a unique occurrence identity so a three-day sequence is not assessed twice by retries or overlapping windows.

## 7. Payroll runs, approval, corrections, and final settlement

PayrollPeriod is the pay date range. PayrollRun is an execution batch. PayrollRecord is unique by (run, employee), superseding the earlier one-record-per-period rule.

Allow one finalized regular result per employee/period, plus explicitly classified off-cycle and delta correction results. Re-running a draft updates/rebuilds that draft atomically with a version check; it must not create another payable salary.

Workflow:
Draft -> Calculated -> Reviewed -> Approved -> Finalized.
Salary payments have their own pending/confirmed/failed/reversed state; finalized does not mean paid.

Materialize the approval route and enforce approved permissions. Any material draft edit invalidates prior approvals. Finalization and asynchronous recalculation must use version checks and transactional locking so a stale calculation cannot overwrite an approved run.

Freeze PayrollLine inputs, rates, source revisions, and totals at finalization. Only finalized lines create financial obligations and committed advance/loan recoveries. Draft recoveries are reservations at most.

Late device data, changed leave, backdated salary revisions, or corrected attendance after finalization produce a new correction run with delta lines referencing originals. Do not overwrite the original record. If an old salary has already been paid, a negative correction becomes an explicit employee receivable or a deduction in a later run; it does not magically reverse bank funds.

Final settlement is a run type for resignation/termination. It collects service-to-date earnings, unpaid prior salaries through existing obligations, encashment, bonuses/approved exit benefits, recoveries, notice adjustments where configured, and outstanding advances/loans subject to the recovery limit. Prior unpaid salary is shown for payment allocation, not recreated as another earning.

Payslips render finalized PayrollRecord/PayrollLine snapshots. A separate Payslip model is unnecessary until independent document distribution/versioning requirements arise.

## 8. Salary payments and dues

A salary obligation and an actual payment are different facts.

SalaryPayment records a real transfer to one employee. SalaryPaymentAllocation can divide it over several of that employee's finalized records. This supports paying part of September plus remaining August salary in one transfer.

Payroll outstanding = finalized net payable - effective confirmed salary allocations, adjusted only by explicitly linked correcting obligations/settlements.

Payment unallocated amount = confirmed payment amount - its effective allocations. A receipt exceeding salary due stays visibly unallocated or is explicitly reclassified as an advance; it must not make a salary appear negatively due without an explanation.

Atomic constraints prevent allocation beyond available payment funds or target obligation. Failed/pending payments do not settle anything. A reversal is a linked compensating payment/allocation, never deletion of evidence.

Reissuing or retrying a bank instruction uses the same idempotency key where supported. Payment recording does not itself mean an automatic transfer integration exists.

No SalaryDue table is required. Due is a derived amount, with an optional rebuildable cache. An unpaid amount carries forward as an open obligation; it is not added to next month's earned salary again.

## 9. Advances and loans

Advance and loan approval does not prove money was paid. AdvanceDisbursement and LoanDisbursement record funded amounts, including partial releases.

Advance outstanding = confirmed disbursements - committed payroll/direct recoveries, considering reversals.

An advance recovery through payroll has one PayrollLine. A direct repayment has receipt details and no payroll deduction line. Enforce exactly one source method.

Loans add principal/interest terms and versioned schedules:
- Zero-interest, approved fixed-interest or reducing-balance schedules as configured.
- Regular installments, partial repayment, early repayment, deferred installments, rescheduling, and cancellation of undisbursed agreements.
- Reversed payments and correction of a wrongly recorded repayment.
- Approved interest waiver or principal balance adjustment uses a typed loan-linked PayrollAdjustment with settlement mode: payroll effect or balance-only. Balance-only adjustments never produce salary lines; payroll-mode effects are linked once.
- Retain superseded schedules and their accrued history. Any interest recomputation follows explicit effective terms, not an edit of already paid installments.

LoanRepaymentAllocation separates principal and interest and associates repayment with due installments. Loan principal outstanding = funded principal - allocated principal recoveries - approved principal waivers, plus reversal/correction effects. Interest due is tracked separately from principal and future unearned interest.

Where a payroll recovery cap or available salary is insufficient, deduct only the allowed amount and leave the rest outstanding. Generate revised schedules only through an authorized action; a skipped installment does not disappear.

Concurrent direct repayment and payroll finalization must lock the same loan/advance balance to avoid over-recovery. Recovery source identities prevent regenerated payroll from recovering twice.

No separate generic LoanBalance or AdvanceBalance model is required; balances are derived from confirmed source transactions and optional rebuildable caches.

## 10. Statutory deductions, benefits, and remittance

Use SalaryComponent for tax, employee benefit contributions, employer contributions, and other applicable deductions. PayrollPolicyVersion stores effective configuration and required year-to-date inputs. Include prior finalized and correction lines in year-to-date computation; reversals must be reflected.

Component configuration can identify provider, account references, caps, thresholds, and reporting codes without hard-coding one country's formulas. The rules themselves remain an implementation/requirements task.

Withholding from salary creates a liability to the recipient; it does not mean the company paid that recipient. PayrollRemittance and allocations record the actual settlement.

Reports distinguish gross earnings, employee deductions, employer costs, net payable, salary paid, salary due, and remittance due.

## 11. Critical constraints and verification cases

Database/service invariants to implement:
- Company/employee/currency consistency across every allocation and source link.
- Exactly one typed source for recoveries and approval targets; no unvalidated generic ID as a financial source.
- Unique accrual, leave-consumption, penalty occurrence, payroll posting, and payment idempotency keys.
- Non-overlapping effective policies, compensation, assignments, and requested/approved leave intervals where applicable.
- Sum of confirmed allocations cannot exceed funded amounts or remaining obligations; enforce with transactional locking, not only form validation.
- Each approved advance/loan is recovered only up to funded outstanding amounts.
- Immutable posted records and auditable compensating entries.
- Prevent regular-payroll duplication even when two workers finalize simultaneously.
- Versioned sources and tenant-scoped background jobs; rejected cross-company IDs fail even for nested relations.

Verification scenarios:
1. Employee A transfers and changes reusable code mid-month; segments retain both assignments and Employee B does not inherit A's balances.
2. Two leave requests concurrently use the last half-day; only the permitted total is reserved.
3. Accrual task retries; one credit is posted.
4. Half-day plus hourly leave without overlap; correct schedule-dependent units.
5. Approved leave covers late arrival; the same interval is not deducted twice.
6. Retroactive cancellation after salary payment; old payroll stays frozen and a correction is traceable.
7. Full monthly salary in February and a 31-day month; both pay the unchanged base when fully eligible.
8. Hourly unpaid absence and full-day penalty; no duplicate base-loss deduction.
9. Mid-month salary change; source rate segments reconcile.
10. One salary payment settles two months, another pays only part of a month; dues reconcile without duplicate earnings.
11. Failed/reversed salary payment; no false settlement.
12. Advance approved but never disbursed; no recovery.
13. Loan partially funded, directly repaid while payroll runs, then rescheduled; principal remains correct.
14. Overtime converted to compensatory leave; no unintended double benefit.
15. Final settlement with insufficient net salary for a loan; remaining loan stays visible.
16. Statutory amount withheld but not remitted; employee net and third-party due remain distinct.

## 12. Current boundaries and implementation order

The expanded scope supports a full HR leave and salary workflow. It is still a reviewable draft, not a guarantee that every jurisdictional or client-specific rule is specified.

The authoritative order is now in IMPLEMENTATION_ROADMAP.md:

1. Standalone foundation, company/access/employee history and schedule/calendar setup (P0–P1).
2. Usable leave, simple optional balances, approvals and cancellation/amendment (P2).
3. Manual/simulated attendance and the common calculation pipeline (P3).
4. Monthly payroll, daily evidence, lines, payslips, review/finalization and minimum correction support (P4).
5. Salary payments/dues, adjustments and simple advance disbursement/recovery (P5).
6. Pilot readiness (P6); real-device verification is a separate D1 lane when hardware is available.
7. Advanced accrual/carry-forward, encashment/compensatory leave, optional LFA, loans, richer structures, remittances and automated final settlement (P7).

Ordinary mistakes and posted corrections cannot wait for P7. P4/P5 already need immutable posting, approved correction/reversal and accurate outstanding balances; only richer automation is deferred.

Automatic bank transfers, tax filing connectors, general-ledger accounting, expense claim workflows, and subscription invoicing remain separate integration scopes. They are not implied by recording a payment, adjustment, or remittance.


## Implemented A8 workflow — 2026-09-15

Employees submit full-day paid/unpaid requests from My leave. Pending requests
have no payable leave days. Active assigned branch managers decide requests;
company administrators handle branches with no manager and managers' requests
(confirmed by Ajay). Self-approval is forbidden. Reviewers may approve paid or
unpaid, or reject with a note visible to the employee. Approval revalidates the
working calendar, conflicts and finalised salary ranges, creates existing
LeaveDay records, records an audit event and refreshes attendance atomically.
Regenerate draft salary to pick up the approved leave. Partial pay, half-day/
hourly leave, balances, attachments and withdrawal/amendment remain A10.

A10a (2026-09-15): HR records and cancels leave (not their own, not in a
finalised month); default Casual/Sick/Earned/Maternity leave types; employee
code in the Record leave picker; employees withdraw pending requests.

A10b (2026-09-15): half-day leave — one date, 0.5 day, paid or unpaid. Came in
→ full day with no late mark (unpaid half deducted); no scans → only the leave
half counts. **Leave is kept simple (Ajay):** next only A10c, an optional yearly
allowance per leave type. Partly paid and hourly leave, attachments, policies,
accrual ledgers and amendments are not planned for now; the larger design above
remains reference only.


## A15 list navigation — 2026-09-15

Salary month, Overtime, Penalty rules, Leave, Leave types, Holidays, My leave,
My payslips and Approval inbox now use the shared server-side table controls.
The existing appearance and action links remain. Use numbered pages, page size,
page jump and search across all results; overtime also has Employee and Branch
filters. Month/status filters continue to apply before table search and paging.
My payslips still shows only finalised salary; these navigation changes do not
finalise salary or change its calculation. A10 is the next implementation.
