# Multi-Tenant Attendance Management System

> Implemented schema correction (2026-09-07): Company.code remains varchar/string but is generated from a PostgreSQL sequence starting at 10001; slug is generated with name-based numeric suffixes. Existing identifiers remain unchanged. Company defaults are BDT, BD, Asia/Dhaka. CompanyMembership now has conditional uniqueness on company and user for non-ended owner/company_admin roles (one current master per company, not shared across companies). Historical ended memberships remain. CompanyAttendanceSettings.company is implemented as FK + UNIQUE (one-to-one cardinality). No new model/table/column was added by this correction; generated diagrams retain the same relationships. See UI_AND_ONBOARDING_CONVENTIONS.md for workflow rules.


## Database Model Plan — Draft v0.6

> **Project boundary:** This document describes a separate greenfield attendance project. It is unrelated to the Polymer project in which this planning folder is temporarily stored.

## Status

- Draft architecture for discussion; not final Django models or migrations.
- Proposed stack: modular Django monolith with PostgreSQL, Celery and Redis as needed; user-selected FastAPI for API modules when required, including future ERP endpoints.
- Django should own the ORM and migrations. FastAPI should use the same service and data layer rather than maintaining a duplicate set of SQLAlchemy models.
- Current proposed model count: **86 models across 12 domain apps**. The additional model-free **base_template** app brings the Django app inventory to **13**. It owns shared layout/templates/styles; see PROJECT_SETUP.md.

## Agreed foundations

- One tenant is one `Company`.
- A platform root administrator is a global `User` with `is_superuser=True`; no separate root-admin model is required.
- Every company gets a default `Branch`, normally named Head Office.
- Every tenant-owned model has a direct `company_id` ownership path.
- Branch is stored directly when a record is branch-owned or needs a historical branch snapshot.
- An `Employee` is independent of a login `User`; `Employee.user_id` is nullable.
- An employee's database primary key never changes. A reusable business employee code belongs to an effective-dated `EmployeeAssignment`, so the same code can move to a different employee only after the previous assignment ends.
- A company may use one company-wide shift or department-based shifts. Employee-level shift assignment has the highest precedence.
- A department must have at least one shift. If it has several shifts, each employee requires an explicit shift assignment.
- Employee identity is resolved through DeviceEnrollment; the effective device scope determines which devices can count. Scope options are assigned_devices, department_devices, branch_devices, and company_devices, with employee-assignment override before assigned-branch override before company default. See [Device attendance policy](DEVICE_ATTENDANCE_POLICY.md).
- Authorized IN/OUT punches may come from different devices and still form one employee/shift attendance record. Unauthorized and unresolved source punches are preserved.
- Fingerprint and face templates may be stored optionally, encrypted, and separately from device deployment records.
- Original device messages and normalized punch events are preserved separately from calculated attendance.
- Punches are interpreted into IN/OUT allocations and then paired into attendance sessions.
- Leave can be full-day, half-day, or hourly and paid, unpaid, or partially paid.
- Compensation can be monthly, daily, or hourly. The initial monthly divisor is fixed at 30.
- Expanded leave and salary scope now includes balances/accrual, approval, encashment, salary components, payments/dues, advances, loans, remittances, corrections, and final settlement. See [Leave and salary management](LEAVE_AND_SALARY_MANAGEMENT.md) for fields, workflows, and invariants.
- LFA is optional and disabled by default; one LeaveFareAssistanceClaim reuses existing policy versions, approval, attachment, payroll-line, and payment models. It does not consume leave balance or create a recurring salary component automatically.
- Basic mode uses system-created defaults and one-step approvals. Companies need not configure advanced accrual, loans, structures, or LFA to process ordinary leave and salary.
- Package pricing is based on included features and the permitted number of active employees.

## App and model inventory

Each entry explains what the table stores, its related models, and a practical example. Model names are the proposed Django names; a related model name means the records are connected, usually by a foreign key.

A **record** or **row** is one item in a table. An **effective date range** says when a rule or assignment applies. A **snapshot** is a saved copy of the values used at the time, so a later settings change cannot rewrite history. A **ledger** is a list of balance-changing entries that explains the current balance.

Basic mode uses defaults for supporting configuration and generates calculation/history rows automatically. Having these tables does not mean the administrator must fill in 83 forms. Optional modules such as loans, encashment, and LFA can remain unused.

### 1. `accounts` — 2 models

Login accounts and access to a company.

#### 1. `User`

- **Purpose:** Stores the account a person uses to log into the software, including their identity and login status. The platform root administrator is also a User.
- **Related models:** Connects to Company through CompanyMembership; an Employee can optionally link to this account.
- **Example:** An HR officer needs a User account to approve leave. A factory worker can have attendance and salary records without a login.

#### 2. `CompanyMembership`

- **Purpose:** Records which company a login account may access and its role in that company. It allows company access to be activated or removed without deleting the login.
- **Related models:** Connects one User to one Company. Detailed employee permissions are handled by the access_control app.
- **Example:** The same login may be HR in Company A and a read-only member in Company B; each access has a separate membership.


### 2. `tenants` — 3 models

The companies using the software and the modules they can use.

#### 3. `Company`

- **Purpose:** Represents one customer organization using the attendance system. Its identity is the ownership boundary for employees, devices, attendance, leave, and salary data.
- **Related models:** Has Branch, CompanyMembership, CompanyFeature, and CompanySubscription records, plus the company-owned records in other apps.
- **Example:** ABC Ltd. and XYZ Ltd. are separate Company records; ABC employees and managers must not access XYZ data.

#### 4. `Feature`

- **Purpose:** Lists the modules the platform can make available, such as attendance, leave, payroll, or notifications. It describes a capability rather than a company's permission to use it.
- **Related models:** Connects to Package through PackageFeature, to company exceptions through CompanyFeature, and to individual actions through AccessPermission.
- **Example:** Payroll is one Feature; viewing salary and generating salary are different AccessPermission actions inside it.

#### 5. `CompanyFeature`

- **Purpose:** Records a root-admin exception to a company's normal package features. It can grant an extra module, temporarily enable it, or explicitly disable a package module.
- **Related models:** Connects a Company to a Feature and records who granted the exception and any expiry.
- **Example:** A company on an attendance-only package receives payroll for a trial month without changing the package for other customers.


### 3. `organization` — 3 models

Branches, departments, and positions inside a company.

#### 6. `Branch`

- **Purpose:** Stores a company location, including its name, address, and timezone if different from the company default. Every company receives a default branch such as Head Office.
- **Related models:** Belongs to Company; departments and devices belong to it, and employee assignments identify who works there.
- **Example:** A company can have Dhaka and Chattogram branches, each with several attendance devices.

#### 7. `Department`

- **Purpose:** The platform-wide list of department names, maintained by the root operator. No company can add to it or edit it. One "Human Resources" exists for everybody, so reporting across companies compares like with like instead of nine spellings of the same thing.
- **Related models:** Owns Designation. Companies reach it only through CompanyDepartment.
- **Example:** Root adds "Sales" once. Every company that has a sales team adopts that one entry.

#### 8. `Designation`

- **Purpose:** The platform-wide list of designations, also root-owned, and kept **separate from the department list**. Root decides which designations exist; each company decides which of its departments use them.
- **Related models:** Companies reach it only through CompanyDesignation, which places it under one of their departments.
- **Example:** Root adds "Manager" once. Northwind places it under Sales, Sunrise places it under Operations. There is deliberately no parent designation: access limits come from the department (see DepartmentPermission).

#### 84. `CompanyDepartment`

- **Purpose:** Records that one company uses a catalogue department, inside one of its branches. This is the row everything company-specific attaches to — employees, shifts, permission rules, access scopes — so nothing one company sets can reach another. It also names the department head, the person who administers access for everyone inside it.
- **Related models:** Links Company and Branch to a catalogue Department; connects to CompanyDesignation, EmployeeAssignment, DepartmentShift, DeviceDepartment and DepartmentPermission.
- **Example:** Northwind adopts "Sales" in Dhaka and again in Chattogram. Two rows, two heads, two shift patterns, one catalogue entry. Sunrise adopting the same "Sales" entry sees none of it.

#### 85. `CompanyDesignation`

- **Purpose:** The company's own department–designation relation: it places one root designation under one of the company's departments. The company admin picks a department and assigns the designations it uses. Employee assignments and designation-level permission rules point here, never at the shared root list.
- **Related models:** Links CompanyDepartment to a root Designation; used by EmployeeAssignment and DesignationPermission.
- **Example:** Northwind assigns "Manager" and "Sales Representative" to its Dhaka Sales department. Giving "Manager" leave-approval rights inside Northwind changes nothing for any other company that also uses "Manager".


### 4. `employees` — 3 models

The employee's permanent identity and the history of where they worked and what they earned.

#### 9. `Employee`

- **Purpose:** Stores the permanent identity and personal details of an employee. This database identity stays the same even when the business employee code, department, or designation changes.
- **Related models:** Belongs to Company and optionally links to User; assignments, compensation, attendance, leave, and payroll all refer to this employee.
- **Example:** Employee A remains the same person when their code changes from SW-001 to HR-015. Reusing SW-001 for Employee B does not transfer A's history.

#### 10. `EmployeeAssignment`

- **Purpose:** Stores an employee's business code, branch, department, designation, and manager for a particular date range. End the old assignment and create a new one when these details change.
- **Related models:** Links Employee to Branch, CompanyDepartment, CompanyDesignation, and optional manager Employee. Historical attendance and payroll segments reference the applicable assignment.
- **Example:** Employee A holds SW-001 in Software until March, then HR-015 in HR from April. Employee B may use SW-001 from April, with a different assignment and permanent identity.

#### 11. `EmployeeCompensation`

- **Purpose:** Stores the employee's base pay rate and whether that rate is monthly, daily, or hourly, together with the dates it applies. Old rates remain available after a salary increase.
- **Related models:** Belongs to Employee; PayrollCompensationSegment references the rate used for each part of a salary period.
- **Example:** An employee earns 30,000 monthly until June and 35,000 from July. June's salary continues to use the earlier compensation record.

#### Reusable employee-code history

`Employee.id` is the permanent person identity. The manually entered business identifier is stored as `EmployeeAssignment.employee_code` and may be reused only after its previous assignment has ended.

Example:

```text
Employee A -> SW-001 -> Software -> 2025-01-01 to 2026-03-31
Employee A -> HR-015 -> HR       -> 2026-04-01 to present
Employee B -> SW-001 -> Software -> 2026-04-01 to present
```

The database must prevent overlapping effective periods for the same `(company_id, employee_code)` and for the same employee. This preserves two useful query paths:

```text
Current employee code -> current EmployeeAssignment -> permanent Employee -> all personal history
Employee code -> all EmployeeAssignment rows -> every person who occupied that code over time
```

`AttendanceRecord` stores the applicable `employee_assignment_id`, so records retain the exact code, branch, department, and designation that applied on that work date. No attendance, leave, payroll, or performance history is joined through `User` or through a person's name.

### 5. `access_control` — 3 models

Which actions a person can perform after their company has access to the feature.

#### 12. `AccessPermission`

- **Purpose:** Names one specific action in the software. It provides the shared vocabulary for setting access instead of placing separate permission switches on every employee.
- **Related models:** Belongs to a Feature and is used by DesignationPermission and EmployeePermissionOverride.
- **Example:** Viewing department attendance, correcting attendance, and approving leave are three separate permissions.

#### 13. `DesignationPermission`

- **Purpose:** Defines which actions holders of a designation receive automatically, which may be granted individually, and which are denied. It supplies the normal access and upper limit for that position.
- **Related models:** Connects CompanyDesignation to AccessPermission; the department ceiling (DepartmentPermission) and company feature access also apply.
- **Example:** Assistant HR may receive employee-list access by default and be eligible for leave approval, but cannot receive payroll access if the department is denied it.

#### 86. `DepartmentPermission`

- **Purpose:** Defines what a whole department may do. It is both a ceiling and a floor: an action the department is denied cannot be granted to anyone inside it by any other means, and an action the department is allowed is what everyone in it gets unless individually revoked. This is what makes it safe to let a department head administer their own people — they distribute access inside a boundary they cannot widen.
- **Related models:** Connects CompanyDepartment to AccessPermission; DesignationPermission and EmployeePermissionOverride are both capped by it.
- **Example:** Payroll access is denied to the Sales department. The head of Sales can still hand out leave approval to their team, but no grant they make can reach payroll.

#### 14. `EmployeePermissionOverride`

- **Purpose:** Records an individual access exception for an employee, such as granting an allowed action or removing a default action. It records the responsible administrator and applicable dates.
- **Related models:** Connects Employee to AccessPermission and the User who made the change. Grants stay within the current designation's permitted scope.
- **Example:** One Assistant HR may be allowed to approve leave while another is not. On a transfer, permissions are checked against the employee's new designation.

Effective access requires an enabled company feature, active company membership, applicable designation permissions, employee overrides, and the user's authorized branch/department scope.

### 6. `scheduling` — 7 models

Expected working hours, weekly holidays, and special working-day decisions.

#### 15. `Shift`

- **Purpose:** Defines a reusable working schedule, such as 10 AM to 5 PM, with timing and grace-period settings. The same schedule can serve several departments.
- **Related models:** Belongs to Company; used by DepartmentShift, EmployeeShiftAssignment, company settings, and historical AttendanceRecord.
- **Example:** Software and Accounting can both use the same 10 AM–5 PM shift without storing separate copies of its definition.

#### 16. `DepartmentShift`

- **Purpose:** Lists the shifts available to a department and identifies its default where applicable. Several rows let a department operate more than one shift.
- **Related models:** Connects CompanyDepartment to Shift; employee assignments select the applicable shift when more than one is available.
- **Example:** A Support department has morning and evening shifts. Its employees must be explicitly assigned to one of them.

#### 17. `EmployeeShiftAssignment`

- **Purpose:** Records an employee's particular shift for a date range. It selects a shift in a multi-shift department or provides an individual override.
- **Related models:** Connects Employee to Shift and retains the dates and person responsible for the assignment.
- **Example:** A department uses 10 AM–5 PM, but one employee has an approved 9 AM–4 PM assignment during September.

#### 18. `CompanyAttendanceSettings`

- **Purpose:** Stores company-wide choices for interpreting attendance, including shift mode, device_attendance_scope, accepted punch windows, repeated-fingerprint handling, and overtime timing. Device scope defaults to assigned_devices and can be overridden by the assigned branch or dated employee assignment.
- **Related models:** Belongs to Company and may reference its single default Shift. Attendance processing reads these settings.
- **Example:** A company chooses one shift for all departments and treats fingerprints repeated within 30 seconds as reviewable duplicates during calculation.

#### 19. `WeeklyOffRule`

- **Purpose:** Stores a recurring day off, such as every Friday, for the company or one branch. Effective dates preserve changes to the weekly calendar.
- **Related models:** Belongs to Company, optionally Branch; scheduling, leave, and payroll use it to determine expected workdays.
- **Example:** Friday and Saturday are off for Head Office, while another branch follows a different weekly schedule.

#### 20. `Holiday`

- **Purpose:** Stores one specially selected non-working date and its name. These dates are separate from recurring weekly holidays.
- **Related models:** Belongs to Company, optionally Branch; leave and attendance calculations consult the applicable calendar.
- **Example:** The admin adds a public holiday on a particular date or declares a company holiday for one branch.

#### 21. `HolidayWorkAssignment`

- **Purpose:** Records that an employee is expected to work on a holiday and how that work should be treated. The original holiday remains in the calendar.
- **Related models:** Connects Employee, Holiday, and optional Shift; attendance and payroll use the workday/overtime treatment.
- **Example:** An employee is assigned to work during a company holiday while everyone else remains off. Work on a recurring weekly off still needs its override details finalized.


### 7. `devices` — 10 models

Physical devices, authorized employees, saved biometric templates, and incoming device data.

#### 22. `DeviceVendor`

- **Purpose:** Stores the manufacturer or integration provider's name as shared reference data. It lets several vendors be represented consistently.
- **Related models:** Parent of DeviceModel; also referenced by biometric format information where needed.
- **Example:** ZKTeco and Tipsoi are vendor entries. Adding another vendor does not require a separate employee or attendance table.

#### 23. `DeviceModel`

- **Purpose:** Describes a type of device and its known integration method and capabilities. This is the product model, not a particular physical machine.
- **Related models:** Belongs to DeviceVendor and is referenced by BiometricDevice and template compatibility information.
- **Example:** Two offices may own devices of the same model; they share one DeviceModel but have separate BiometricDevice records.

#### 24. `BiometricDevice`

- **Purpose:** Stores one physical attendance machine, its serial/identifier, location, configuration, authentication details, and contact status.
- **Related models:** Belongs to Company, Branch, and DeviceModel; has department permissions, employee enrollments, messages, and synchronization state.
- **Example:** Device A at the Dhaka entrance and Device B at the Dhaka Software room are two devices owned by the same branch.

#### 25. `DeviceDepartment`

- **Purpose:** Records which departments a device serves. These links filter department_devices mode; no active links means a shared device within its branch. They do not impose an extra restriction in branch_devices/company_devices mode or on an explicit assigned-device grant.
- **Related models:** Connects BiometricDevice to CompanyDepartment within the same company and branch.
- **Example:** The entrance device serves Software and Accounting. A separate Sales device serves only Sales.

#### 26. `DeviceEnrollment`

- **Purpose:** Maps an employee to a device-specific user number and keeps enrollment dates/upload status. attendance_enabled is a master switch; assigned_device_authorized is a separate explicit grant for assigned_devices mode. Wider scopes allow eligible mapped devices without that explicit grant.
- **Related models:** Connects Employee to BiometricDevice; PunchEvent uses it to identify and authorize a punch, and DeviceEnrollmentTemplate records template uploads.
- **Example:** Under assigned_devices, Employee A counts only on explicitly authorized Devices 1 and 3. Under branch_devices, any eligible mapped device in the assigned branch may count. Recognition on a device alone does not grant permission in restricted mode.

#### 27. `BiometricTemplate`

- **Purpose:** Stores an optional encrypted fingerprint or face template belonging to an employee, including its format and compatibility details. A template is biometric matching data, not simply an employee photo.
- **Related models:** Belongs to Employee and references vendor/source-device information; deployments are tracked through DeviceEnrollmentTemplate.
- **Example:** After a machine is replaced, a compatible saved template can be uploaded to the replacement if the vendor integration supports it.

#### 28. `DeviceEnrollmentTemplate`

- **Purpose:** Tracks an attempt to deploy a particular saved template to an employee's enrollment on a device. It records whether deployment succeeded or failed.
- **Related models:** Connects DeviceEnrollment to BiometricTemplate; several templates can be associated with one enrollment.
- **Example:** An employee's two fingerprint templates upload successfully to Device 1, while the face template fails on Device 3 and needs retry.

#### 29. `DeviceSyncState`

- **Purpose:** Stores the device's current synchronization progress and latest success or failure. It helps the system resume work and detect devices that have stopped communicating.
- **Related models:** Has one current state row per BiometricDevice, including the vendor's progress marker where available.
- **Example:** After reconnection the device sends old punches. Its sync state records progress, but the complete evidence stays in DeviceMessage and PunchEvent.

#### 30. `DeviceMessage`

- **Purpose:** Preserves one complete message received from a device so it can be parsed again if processing fails. A message can contain many employees' punches or no punches at all.
- **Related models:** Belongs to BiometricDevice and Company; one message can produce many PunchEvent records.
- **Example:** A device reconnects and uploads 200 punches in one request. The server saves that whole request before acknowledging receipt.

#### 31. `PunchEvent`

- **Purpose:** Stores one punch extracted from a saved device message, including the device user number and reported time. Employee mapping can remain unresolved until the correct enrollment is found; attendance processing preserves the original punch facts.
- **Related models:** Links to DeviceMessage, BiometricDevice, and, when resolved, Employee and DeviceEnrollment. PunchAllocation interprets it later.
- **Example:** The message containing 200 punches produces 200 punch records. An unknown user number remains available for later matching instead of being discarded.


### 8. `attendance` — 7 models

Interpretations and daily calculations derived from punches, plus corrections and penalty evidence.

#### 32. `PunchAllocation`

- **Purpose:** Records which attendance day a punch belongs to and whether processing treats it as IN, OUT, or excluded. This interpretation can be revised without changing the device's original punch.
- **Related models:** Connects PunchEvent to AttendanceRecord, with ordering, inclusion reason, and calculation version.
- **Example:** A 09:55 punch is interpreted as IN; another fingerprint seconds later is retained but excluded as a probable repeat.

#### 33. `AttendanceSession`

- **Purpose:** Stores one paired entry-to-exit interval used to calculate time worked. Entry and exit can originate from different allowed devices. The gaps between completed sessions describe interpreted outside/break periods.
- **Related models:** Belongs to AttendanceRecord and references its entry and exit punches; an exit may be missing until corrected.
- **Example:** IN at 09:55 on Device 1 and OUT at 13:00 on Device 10 form one session. IN at 13:45 on Device 3 and OUT at 17:10 on Device 1 form another, leaving a 45-minute gap. Missing or accidental punches require review.

#### 34. `AttendanceRecord`

- **Purpose:** Stores the calculated summary for one employee's work date: expected shift, entry/exit, worked time, lateness, breaks, and overtime. It is the daily attendance list row.
- **Related models:** Links Employee, applicable EmployeeAssignment and Shift, plus PunchAllocation and AttendanceSession; payroll reads its calculated results.
- **Example:** A record can say present, 120 minutes late, five hours worked. A full-day salary penalty is assessed separately rather than erasing that attendance.

#### 35. `AttendanceCorrection`

- **Purpose:** Stores a request or authorized change to an attendance result, with the reason, original and corrected values, and approval history. It leaves source device punches intact.
- **Related models:** Belongs to AttendanceRecord and links the requesting/approving users; approved changes trigger attendance recalculation.
- **Example:** An employee forgot the final fingerprint. HR approves a documented checkout correction, and the daily result is recalculated.

#### 36. `AttendancePenaltyRule`

- **Purpose:** Defines when attendance behavior causes a salary penalty and how that penalty is measured. Rules can consider one day or several qualifying days.
- **Related models:** Belongs to Company; PenaltyAssessment applies it to an employee's AttendanceRecord history.
- **Example:** A configured rule says 120 minutes late costs one day's salary, or 60 minutes late on three consecutive working days costs one day.

#### 37. `PenaltyAssessment`

- **Purpose:** Stores one actual result of a penalty rule, including the employee, affected period, and proposed deduction. It preserves the rule inputs so the decision can be explained.
- **Related models:** Links Employee, AttendancePenaltyRule, and PayrollPeriod; related attendance is listed through PenaltyAssessmentAttendance and payroll posts the deduction.
- **Example:** The three qualifying late days produce one assessment for a one-day deduction, rather than independently deducting a day for each occurrence.

#### 38. `PenaltyAssessmentAttendance`

- **Purpose:** Lists the attendance days that contributed to a particular penalty. This small linking table gives a multi-day deduction its supporting evidence.
- **Related models:** Connects PenaltyAssessment to AttendanceRecord; one assessment can reference several days.
- **Example:** Opening a three-day lateness penalty shows the three daily records that triggered it.


### 9. `leaves` — 15 models

Leave applications, approvals, optional balances, and optional leave-related benefits.

#### 39. `LeaveType`

- **Purpose:** Names a company-defined kind of leave. The name classifies applications; payment and allowance rules are configured separately.
- **Related models:** Belongs to Company; used by LeavePolicyTypeRule, LeaveEntitlement, and LeaveRequestSegment.
- **Example:** Annual, sick, casual, and unpaid leave are types. In basic mode the manager still decides the approved payment percentage.

#### 40. `LeavePolicy`

- **Purpose:** Identifies a set of leave rules for a group of employees. Its name stays stable while the detailed rules may change over time.
- **Related models:** Belongs to Company and may apply to a branch/department; LeavePolicyVersion stores rules, and EmployeeLeavePolicyAssignment assigns employees.
- **Example:** Head Office Leave Policy and Factory Leave Policy can have different allowances or approval routes.

#### 41. `LeavePolicyVersion`

- **Purpose:** Stores the actual leave rules that apply during a particular period, including approval routing and optional LFA settings. Used versions are preserved so policy changes do not rewrite past approvals.
- **Related models:** Belongs to LeavePolicy; contains LeavePolicyTypeRule rows and is referenced by approved requests and benefits.
- **Example:** The company changes its annual entitlement next year. Earlier leave continues to show the policy version under which it was approved.

#### 42. `LeavePolicyTypeRule`

- **Purpose:** Stores the detailed rules for one leave type within one policy version: allowance, accrual, eligibility, payment defaults, carry-forward, and other enabled options.
- **Related models:** Connects LeavePolicyVersion to LeaveType; entitlement and approved daily allocations refer to the applicable rule.
- **Example:** Annual leave may receive a yearly allowance while unpaid leave has no balance limit. These require two rules within the same policy version.

#### 43. `EmployeeLeavePolicyAssignment`

- **Purpose:** Records which leave policy applies to an employee over a date range. It supports explicit employee assignment and preserves changes caused by transfers or eligibility.
- **Related models:** Connects Employee to LeavePolicy; approved transactions retain their resolved version.
- **Example:** An employee moves from factory rules to Head Office rules in July. The earlier policy assignment remains visible.

#### 44. `LeaveEntitlement`

- **Purpose:** Identifies an employee's leave balance account for one type and leave year. The account says whose balance is being tracked; its entries determine the available amount.
- **Related models:** Connects Employee and LeaveType to a period; has LeaveBalanceEntry records and is used by LeaveDay and LeaveEncashment.
- **Example:** Employee A's annual-leave account for 2026 is different from their sick-leave account and their annual-leave account for 2027.

#### 45. `LeaveBalanceEntry`

- **Purpose:** Stores each individual change to a leave balance: grant, monthly accrual, reservation, use, cancellation release, expiry, or adjustment. Existing entries are preserved and errors are corrected with opposite entries.
- **Related models:** Belongs to LeaveEntitlement and links to the request, approved day, encashment, or compensatory credit responsible.
- **Example:** Grant 12 days, reserve two for a request, then release the reservation when rejected. The available balance can be explained from those entries.

#### 46. `LeaveRequest`

- **Purpose:** Stores the employee's application, reason, submission status, and overall decision. Amendments or cancellations refer back to the original request so earlier decisions remain visible.
- **Related models:** Belongs to Employee; contains LeaveRequestSegment, LeaveApprovalStep, and optional LeaveAttachment records.
- **Example:** An employee asks for three days off. HR may submit the request for them even when the employee has no software login.

#### 47. `LeaveRequestSegment`

- **Purpose:** Stores one requested interval within an application, including its leave type and whether it is a full day, half day, or hours. It allows one application to contain different durations.
- **Related models:** Belongs to LeaveRequest; approved intervals are expanded into LeaveDay records.
- **Example:** An application requests a full Monday and two hours on Tuesday. Those are two segments within the same request.

#### 48. `LeaveDay`

- **Purpose:** Stores approved leave coverage for an actual work date, including time covered, leave units, and approved pay percentage. It is the daily result used by attendance and payroll.
- **Related models:** Links LeaveRequestSegment, EmployeeAssignment, applicable policy rule, and optional LeaveEntitlement.
- **Example:** Two approved hours on Tuesday at 50% pay are recorded here; payroll can distinguish those hours from absence or fully paid leave.

#### 49. `LeaveApprovalStep`

- **Purpose:** Records one stage and decision in an approval process, including the approver and comments. A simple company uses one step; advanced companies may use several.
- **Related models:** Links to exactly one LeaveRequest, LeaveEncashment, LeaveCompensatoryCredit, or LeaveFareAssistanceClaim.
- **Example:** A manager approves the first stage and HR approves the second. The record shows who actually made each decision.

#### 50. `LeaveAttachment`

- **Purpose:** Stores the reference and details of a privately stored supporting file. Several files may support one application.
- **Related models:** Belongs to exactly one LeaveRequest or LeaveFareAssistanceClaim and records who uploaded it.
- **Example:** An employee attaches their signed application or supporting medical document; authorized reviewers can open it.

#### 51. `LeaveEncashment`

- **Purpose:** Records a request and approval to convert eligible unused leave into money. It records the units, conversion rate, and amount and connects the balance deduction to salary.
- **Related models:** Links Employee, LeaveEntitlement, policy version, approvals, and the resulting PayrollLine.
- **Example:** Five unused days are approved for payment. The system removes those five days from the balance and creates one salary earning.

#### 52. `LeaveCompensatoryCredit`

- **Purpose:** Records approved extra leave earned from working on a holiday or other qualifying extra time. It keeps evidence of the work that earned the credit.
- **Related models:** Links Employee and source AttendanceRecord to LeaveEntitlement; approval creates a LeaveBalanceEntry.
- **Example:** An employee works an approved holiday and receives one compensatory day. The same work is not automatically rewarded twice as overtime and leave.

#### 53. `LeaveFareAssistanceClaim`

- **Purpose:** Stores an optional Leave Fare Assistance application or HR award, including eligibility, benefit cycle, approved amount, and qualifying leave if required. LFA is disabled by default and does not consume leave balance.
- **Related models:** Links Employee, policy version, optional LeaveRequest, approvals/attachments, and its earning PayrollLine.
- **Example:** An eligible employee receives an approved annual LFA amount with salary or through a separate payroll run. A department transfer does not allow a second annual claim.


### 10. `payroll` — 26 models

Salary calculations and the actual payments, recoveries, and outstanding amounts that follow them.

#### 54. `PayrollSettings`

- **Purpose:** Stores the company's current payroll defaults, such as currency, pay cycle, and default calculation policy. Basic setup can fill these automatically.
- **Related models:** Belongs to Company; selects the rules and defaults used when creating payroll periods and runs.
- **Example:** A company generates salary monthly in BDT using the initially agreed 30-day deduction divisor.

#### 55. `PayrollPolicyVersion`

- **Purpose:** Preserves the salary calculation rules applicable during a period: divisor, proration, overtime, rounding, recovery limits, and approval settings. Older salary results retain their version.
- **Related models:** Belongs to Company and is referenced by PayrollCompensationSegment and saved calculation inputs.
- **Example:** Overtime rates change in July. June's finalized payroll continues to use June's policy rather than the latest settings.

#### 56. `SalaryComponent`

- **Purpose:** Names one kind of salary earning, employee deduction, or employer contribution. It classifies amounts; it does not by itself pay the employee.
- **Related models:** Belongs to Company; used by SalaryStructureComponent, PayrollAdjustment, and PayrollLine.
- **Example:** Basic salary, overtime, bonus, LFA, tax deduction, and employer contribution are different components with different treatment.

#### 57. `SalaryStructure`

- **Purpose:** Defines a reusable group of salary components for employees. It is optional configuration for companies that need allowances or recurring deductions beyond basic pay.
- **Related models:** Belongs to Company, contains SalaryStructureComponent rows, and is assigned through EmployeeSalaryStructureAssignment.
- **Example:** An Office Staff structure includes base salary and a transport allowance. Simple employees can use the system's base-pay default.

#### 58. `SalaryStructureComponent`

- **Purpose:** Stores the rule or amount for one component within a structure, including when it applies. Base pay refers to EmployeeCompensation so it is not maintained in two places.
- **Related models:** Connects SalaryStructure to SalaryComponent and supplies calculation rules to payroll.
- **Example:** The Office Staff structure includes a fixed transport allowance; an older amount is retained when the allowance changes.

#### 59. `EmployeeSalaryStructureAssignment`

- **Purpose:** Records which salary structure an employee uses for a date range. A change to the employee's package creates a new assignment.
- **Related models:** Connects Employee to SalaryStructure; PayrollCompensationSegment preserves the assignment used for calculation.
- **Example:** An employee changes from a basic-only package to a package with allowances from July.

#### 60. `PayrollPeriod`

- **Purpose:** Defines the dates covered by a salary cycle and its pay date. It is the calendar container, not an individual employee's salary result.
- **Related models:** Belongs to Company and contains PayrollRun records.
- **Example:** September 1–30 is one period. Its normal salary run and a later correction run can both relate to that same period.

#### 61. `PayrollRun`

- **Purpose:** Records a batch of salary calculations and its review/finalization state. Its type explains whether it is regular salary, an extra payment run, a correction, or final settlement.
- **Related models:** Belongs to PayrollPeriod and contains PayrollRecord and PayrollApprovalStep records.
- **Example:** HR calculates September salary for 100 employees in one run; an approved bonus can be processed in a separate extra run.

#### 62. `PayrollRecord`

- **Purpose:** Stores one employee's salary result within a run: earnings, deductions, employer contributions, and net payable. Finalized results preserve the amounts used at approval.
- **Related models:** Connects Employee to PayrollRun; contains PayrollLine, daily evidence, compensation segments, and payment allocations.
- **Example:** Employee A's September result shows what the company owes them. It remains unpaid until actual salary payments are allocated.

#### 63. `PayrollCompensationSegment`

- **Purpose:** Splits an employee's salary calculation into periods where assignment, salary rate, and policy stay consistent. It preserves transfers and pay changes within the same month.
- **Related models:** Belongs to PayrollRecord and references EmployeeAssignment, EmployeeCompensation, optional salary structure assignment, and policy version.
- **Example:** An employee transfers and receives a raise on September 16. One segment covers September 1–15 and another covers 16–30.

#### 64. `PayrollDailyLine`

- **Purpose:** Explains the time and calculation inputs for one date within a compensation segment. It records worked/leave/payable units and the evidence behind daily earnings or deductions.
- **Related models:** Belongs to PayrollCompensationSegment and references AttendanceRecord and the relevant leave evidence.
- **Example:** September 10 shows five hours worked and two approved unpaid leave hours. Payroll uses these details without subtracting the same unpaid time twice.

#### 65. `PayrollLine`

- **Purpose:** Stores one actual earning, deduction, or employer-contribution item in an employee's payroll. These lines are the amounts that add up to the payroll totals.
- **Related models:** Belongs to PayrollRecord, references SalaryComponent, and links to its source such as LFA, a penalty, adjustment, or recovery.
- **Example:** The payslip contains separate lines for base salary, overtime, and an advance deduction. Daily calculation evidence is not added to salary again.

#### 66. `PayrollApprovalStep`

- **Purpose:** Records who reviewed or approved a salary run and their decision. A change to the proposed salary after approval must be reviewed again.
- **Related models:** Belongs to PayrollRun and references the approving CompanyMembership.
- **Example:** HR reviews the calculations and the owner gives final approval before the salary obligations are finalized.

#### 67. `SalaryPayment`

- **Purpose:** Records an actual payment to an employee, including amount, date, payment method, transaction reference, and confirmation state. Pending or failed payments do not settle salary.
- **Related models:** Belongs to Employee; SalaryPaymentAllocation connects it to the salaries being paid.
- **Example:** The company transfers 20,000 to Employee A. Recording that confirmed transfer is separate from calculating the employee's salary.

#### 68. `SalaryPaymentAllocation`

- **Purpose:** Explains which salary obligations a payment settles and by how much. It supports part payments and one payment covering several unpaid periods.
- **Related models:** Connects SalaryPayment to PayrollRecord; both must belong to the same employee and company.
- **Example:** A 20,000 payment clears 5,000 still due for August and pays 15,000 toward September.

#### 69. `SalaryAdvance`

- **Purpose:** Stores an approved salary advance and its recovery agreement. Approval describes what may be paid and how it should be recovered; actual payment is recorded separately.
- **Related models:** Belongs to Employee; has AdvanceDisbursement and SalaryAdvanceRecovery records.
- **Example:** An employee is approved for a 10,000 advance to be recovered in two monthly salary deductions.

#### 70. `AdvanceDisbursement`

- **Purpose:** Records money actually released against a salary advance, with payment reference and confirmation. Recoverable balance is based on funded amounts.
- **Related models:** Belongs to SalaryAdvance; its confirmed amount contributes to the outstanding advance.
- **Example:** The company approves 10,000 but initially pays only 6,000. The system must not recover the unpaid 4,000.

#### 71. `SalaryAdvanceRecovery`

- **Purpose:** Records one repayment of an advance through salary deduction or direct employee payment. It reduces the funded outstanding amount and retains reversal history.
- **Related models:** Belongs to SalaryAdvance and links either to a payroll deduction line or a direct repayment reference.
- **Example:** A 3,000 deduction in September reduces the advance balance once; rerunning salary must not recover it again.

#### 72. `EmployeeLoan`

- **Purpose:** Stores an employee loan agreement, including approved principal, interest terms if any, repayment period, and status. It is separate from a short salary advance because loans may have detailed schedules.
- **Related models:** Belongs to Employee; has LoanDisbursement, LoanInstallment, and LoanRepayment records.
- **Example:** An employee receives approval for a 60,000 interest-free loan with a twelve-month repayment plan.

#### 73. `LoanDisbursement`

- **Purpose:** Records the actual money paid out under a loan agreement. It supports partial releases and distinguishes approval from funded debt.
- **Related models:** Belongs to EmployeeLoan; confirmed disbursements establish the funded principal.
- **Example:** A 60,000 loan is approved, but 30,000 is paid now and the remaining amount later.

#### 74. `LoanInstallment`

- **Purpose:** Stores a scheduled repayment due date and its principal/interest breakdown. It describes what should be paid, while repayments describe what actually was paid.
- **Related models:** Belongs to EmployeeLoan and is settled through LoanRepaymentAllocation; prior schedules are retained when rescheduled.
- **Example:** An installment is due on September 30. If insufficient salary is available, the unpaid portion remains visible.

#### 75. `LoanRepayment`

- **Purpose:** Records one actual loan recovery through payroll or direct payment, including status and reference. It must not recover more than the applicable outstanding amount.
- **Related models:** Belongs to EmployeeLoan, may link to a PayrollLine, and has LoanRepaymentAllocation rows.
- **Example:** The employee directly pays 8,000 toward the loan. This reduces the balance even though it was not a salary deduction.

#### 76. `LoanRepaymentAllocation`

- **Purpose:** Splits a repayment across the installments and principal/interest it settles. It makes partial and early repayments explainable.
- **Related models:** Connects LoanRepayment to LoanInstallment and records the principal and interest portions.
- **Example:** Of a payment, part clears an overdue installment and the rest goes toward the next installment; the breakdown remains visible.

#### 77. `PayrollAdjustment`

- **Purpose:** Stores an approved one-time addition or deduction with its reason and supporting source. It is used for bonuses, arrears, manual corrections, and other explicit adjustments.
- **Related models:** Links Employee, target period, SalaryComponent, and optional original PayrollLine. Ordinary approved adjustments create a payroll line once.
- **Example:** HR adds a 2,000 bonus or corrects an earlier deduction. Explicit loan balance-only waivers are a special case and must not also create a salary payment.

#### 78. `PayrollRemittance`

- **Purpose:** Records an actual company payment to an authority or benefit provider for withheld deductions or employer contributions. It tracks money paid out after payroll created that obligation.
- **Related models:** Belongs to Company and has PayrollRemittanceAllocation records.
- **Example:** A deduction withheld from employees remains due to the recipient until the company confirms its separate remittance.

#### 79. `PayrollRemittanceAllocation`

- **Purpose:** Records which payroll deduction/contribution amounts a remittance settles. It helps distinguish employee salary due from money due to a third party.
- **Related models:** Connects PayrollRemittance to eligible PayrollLine records, with the allocated amount.
- **Example:** One confirmed provider payment settles contributions from many employees' payroll lines, leaving any unpaid contribution balance visible.


### 11. `subscriptions` — 3 models

The packages sold to companies and which package each company currently uses.

#### 80. `Package`

- **Purpose:** Defines a commercial offering with price, billing period, and active-employee limit. Packages determine available features and capacity, not individual employee permissions.
- **Related models:** Connects to Feature through PackageFeature and to subscribed companies through CompanySubscription.
- **Example:** A Basic package might include attendance for up to 50 active employees; the number counts employees even when they have no login.

#### 81. `PackageFeature`

- **Purpose:** Lists one feature included in a package. Several rows describe the full set of modules customers receive with that package.
- **Related models:** Connects Package to Feature.
- **Example:** A Standard package has separate entries for attendance, leave, and payroll.

#### 82. `CompanySubscription`

- **Purpose:** Records the package assigned to a company, its dates and status, and saved price/limit terms. These saved values preserve the subscription agreement after later catalogue changes.
- **Related models:** Connects Company to Package; CompanyFeature handles individual feature exceptions.
- **Example:** Company A subscribes to Standard for a term. Expiry or suspension affects access while the subscription history remains recorded.


### 12. `auditlog` — 1 models

Who changed what, when they changed it, and the information needed to explain the action.

#### 83. `AuditLog`

- **Purpose:** Stores a record of a meaningful action, including the actor, affected object, time, and before/after details where appropriate. It supports investigation without replacing the business history tables.
- **Related models:** References User and usually Company, plus the affected record identity. Platform-wide root actions can have no company.
- **Example:** The log records who approved a correction or changed access. Passwords, biometric templates, and other secrets must not be copied into the log.


### Models that are easy to confuse

| Question | Where to look |
|---|---|
| What did the device send? | DeviceMessage: complete request; PunchEvent: each individual punch inside it. |
| How did we interpret the punches? | PunchAllocation: IN/OUT or exclusion; AttendanceSession: paired work intervals; AttendanceRecord: daily totals. |
| What did the employee ask for and what was approved for each day? | LeaveRequest and LeaveRequestSegment: application; LeaveDay: approved daily coverage. |
| How much leave is available, and why? | LeaveEntitlement: the employee/type/year account; LeaveBalanceEntry: the changes that produce its balance. |
| What period is being paid and what salary did one employee earn? | PayrollPeriod: covered dates; PayrollRun: calculation batch; PayrollRecord: employee result; PayrollLine: actual earning/deduction items. |
| How much has actually been paid? | SalaryPayment: confirmed money transfer; SalaryPaymentAllocation: which salary records it settles. |
| Was an advance or loan approved, funded, scheduled, or repaid? | Agreement model: approval; disbursement: funds paid out; LoanInstallment: expected repayment; recovery/repayment models: actual collection. |
| Does a package feature let every employee use it? | Package/CompanyFeature enables a company module; AccessPermission and designation/employee access decide who may perform each action. |

Example: Employee A is owed 30,000 for September and receives 20,000. PayrollRecord holds the 30,000 result; SalaryPayment records the confirmed 20,000 transfer; SalaryPaymentAllocation applies it to September. The remaining salary due is 10,000, without creating a new SalaryDue table.

For field lists, detailed approval flows, and calculation rules, continue to [Leave and salary management](LEAVE_AND_SALARY_MANAGEMENT.md).

## Model count by app

| App | Count |
|---|---:|
| `accounts` | 2 |
| `tenants` | 3 |
| `organization` | 3 |
| `employees` | 3 |
| `access_control` | 3 |
| `scheduling` | 7 |
| `devices` | 10 |
| `attendance` | 7 |
| `leaves` | 15 |
| `payroll` | 26 |
| `subscriptions` | 3 |
| `auditlog` | 1 |
| **Total** | **83** |

## Primary business flow

```text
Company subscription and features
    -> company membership and effective permissions
    -> employee assignment, compensation, shift, and device enrollment
    -> device message
    -> punch event
    -> punch allocation
    -> attendance session
    -> daily attendance record
    -> leave and penalty effects
    -> payroll daily line
    -> payroll calculation including approved adjustments and recovery candidates
    -> finalized payroll lines and committed recoveries
    -> salary payments and allocations
    -> outstanding salary / loan / advance / remittance balances
```

## Tenant and branch rules

- The authenticated user or device determines `company_id`; clients must not be trusted to choose another tenant ID in request data.
- Tenant-owned queries must always be scoped by company.
- A device, department, enrollment, punch, attendance record, leave record, and payroll record must never reference records from another company.
- `branch_id` is required for branch-owned records and stored as a snapshot where later employee/device transfers could otherwise change historical meaning.
- Important tenant relationships should be checked in model/service validation and covered by database constraints wherever PostgreSQL and Django permit.

## Important uniqueness and history rules

- A business employee code may be reused, but only when its prior `EmployeeAssignment` has ended; two employees may never occupy the same company code during overlapping dates.
- One employee has one active assignment and one applicable compensation record at a time.
- Attendance records reference the effective `EmployeeAssignment`, preserving the employee code and organizational context that applied on the attendance date.
- One company has one default branch and one active subscription at a time.
- One attendance record exists per company, employee, and work date.
- One payroll record exists per run and employee. Only one finalized regular result may cover the same employee/period; corrections and off-cycle runs use explicit types and delta source links.
- Device punches are not made unique only by employee and timestamp; legitimate punches may occur at the same time.
- Reliable vendor punch IDs or sequences provide strict idempotency. Unreliable matches are stored and marked as probable duplicates rather than deleted.
- Raw device messages and punch events are never rewritten by attendance correction or recalculation.

## Scope change log

- Explanation revision: all 86 models now have purpose, relationship, and example notes grouped by app. Model names, count, and the agreed basic-operation scope are unchanged.

- LFA addition: one optional LeaveFareAssistanceClaim under leaves increases the total from 82 to 83 (leaves 15, payroll still 26). Existing policy, approval, attachment, and payroll models receive optional fields; the basic workflow remains available.

- 2026-09-05: Expanded the confirmed 51-model draft to 82 models: leaves increases from 2 to 14, payroll from 7 to 26. Other apps keep their existing models. This supersedes the previous minimal leave/payroll scope and the unimplemented 54-model proposal.
- EmployeeIdentifier remains removed; EmployeeAssignment owns reusable business code history.

## Deferred decisions

- Cross-branch punching rules beyond explicit device enrollment.
- Exact company/jurisdiction-specific payroll formulas and external payment/statutory integrations; finalized salary is preserved and corrected through linked delta runs.
- Detailed notification delivery models.
- Subscription invoices, payments, discounts, taxes, and payment gateways.
- Exact ZKTeco and Tipsoi adapter contracts and biometric-template compatibility.
- Client-specific leave allowances, eligibility, approval routing, accrual rates, carry-forward caps, and encashment rules; supporting models and workflows are now included.
- Rotating rosters and advanced scheduling.
