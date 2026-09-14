# Device attendance scope and punches across devices

This is part of the separate attendance project's approved planning handoff. It adds configuration to existing models; the inventory stays at **86 domain models and 91 domain tables including implicit M2M junctions**.

## Required behavior

A company or branch may have many devices. A device may serve selected departments or be shared. Companies must be able to choose restricted device attendance or let an employee use any eligible device within their department, branch, or company.

**An employee may check in on Device 1 and check out on Device 10.** The database already supports this because PunchEvent records the source device and employee, while AttendanceRecord groups work by employee and work date/shift. AttendanceSession references two PunchAllocations without requiring their devices to match.

Three decisions must remain separate:

1. **Recognition:** can this device recognize the person, and can its user number be resolved to the correct Employee through DeviceEnrollment?
2. **Authorization:** does the employee's effective device policy allow attendance from that device?
3. **Interpretation:** where does an authorized punch fit in the combined employee/shift sequence?

Broad software permission does not upload fingerprints or face templates to devices. Provision recognition separately using the supported vendor integration and DeviceEnrollmentTemplate. Device user numbers need not match across devices; each maps to the same permanent Employee.id. Never identify a person using only a reusable company employee code or an unscoped device user number.

## Scope options

| Scope | Allowed device set, after recognition and common eligibility checks |
|---|---|
| `assigned_devices` | Devices with a valid employee enrollment whose `assigned_device_authorized` flag is true. Explicit grants can include multiple devices of the same company. |
| `department_devices` | Devices in the employee's assigned branch that either have an effective DeviceDepartment link to the employee's department or have no active department links and therefore serve the whole branch. |
| `branch_devices` | Any eligible device in the employee's assigned branch, regardless of DeviceDepartment links. |
| `company_devices` | Any eligible device in the same company, including another branch, regardless of DeviceDepartment links. Never another tenant's device. |

Department mappings are a scope rule in department_devices mode, not an unconditional restriction imposed on every mode. No active department mappings means a shared branch device in that mode; it never expands access across branches by itself. Under assigned_devices, explicit employee-device grants decide the allowed set.

For every mode, the event must resolve to an eligible employee of the same company, a valid historical enrollment, and an eligible source device. `DeviceEnrollment.attendance_enabled = false` is an explicit denial and wins over every scope. A broad scope does not override a disabled enrollment or tenant isolation.

## Existing models and new fields

| Model | Field | Meaning |
|---|---|---|
| CompanyAttendanceSettings | `device_attendance_scope` | Required company default; initially `assigned_devices`. |
| Branch | `device_attendance_scope_override` | Nullable override for employees assigned to this branch. NULL inherits the company setting. |
| EmployeeAssignment | `device_attendance_scope_override` | Nullable employee-level override stored with dated assignment history. NULL inherits assigned branch/company. |
| DeviceEnrollment | `assigned_device_authorized` | Boolean, default false. Explicit device grant used only by assigned_devices mode. |
| PunchEvent | `authorization_snapshot` | JSON snapshot explaining the policy and historical facts used to accept, exclude, or defer this punch. |

The existing DeviceEnrollment.attendance_enabled field remains a master enable/disable switch, default true, separate from the new explicit restricted-mode grant. In the simple assigned-devices workflow, selecting and authorizing a device can create the enrollment and set assigned_device_authorized together. Creating recognition-only enrollments leaves the grant false.

The effective scope is the first non-null value in this order:

```text
EmployeeAssignment.device_attendance_scope_override
    -> employee's assigned Branch.device_attendance_scope_override
    -> CompanyAttendanceSettings.device_attendance_scope
```

Use the employee's assignment/home branch at event time, not the branch of the device they happened to use. Otherwise, a permissive source branch could authorize attendance for an employee who should be restricted. An authorized override can widen or narrow the inherited set; the precedence is not an intersection of all three scopes. Company isolation and the enrollment master disable remain hard limits.

Placing the employee override on EmployeeAssignment preserves its dates without adding an EmployeeDevicePolicy model. A policy-only employee change creates a successor assignment with the same employee code, department, and designation plus an appropriate change_reason.

## Processing and history

1. Authenticate the sending device/integration and derive its company from trusted registration. Durably save the accepted raw message before downstream attendance calculation.
2. Extract PunchEvents, retaining source device, source branch, device user identifier, original timestamp, interpreted UTC timestamp, and receipt time.
3. Resolve the device-specific user identifier through DeviceEnrollment at punch time. An enrollment on another device is not a substitute. Unknown or ambiguous identity stays unresolved and available for later reconciliation.
4. Resolve employee assignment, scope, device eligibility, and enrollment enablement/grants as they applied when the event occurred. Save an authorization_status and authorization_snapshot explaining the decision.
5. Retain unauthorized/unresolved punches as source evidence; exclude them from normal attendance inputs. Do not discard them or mark an employee present merely because the message was received.
6. Combine all authorized punches for the same company, employee, and shift work date, across devices and incoming batches. Sort by interpreted event time with deterministic tie handling, not network arrival order. Apply duplicate/repeat rules before pairing.
7. Create PunchAllocations and AttendanceSessions from the combined stream. Preserve device identity on each source punch. Alternation follows the whole stream, not a separate IN/OUT counter per device.

   **Labels are decided by the software, by pairing — final (Ajay, 2026-09-13).** A device's own IN/OUT or break keys are never used, even when the device has them (`reported_direction` stays informational, as in PUNCH_EVENT_CONTRACT.md). After repeat filtering, the day's scans alternate IN, OUT, IN, OUT across the whole stream. The first IN is **check-in**; the last OUT is **check-out**; every OUT that is followed by another IN is a **break-out**, and that IN is the matching **break-in**. Per day: total time = check-in to check-out; in-office time = the sum of IN→OUT sessions; out-of-office time = the sum of break-out→break-in gaps.

   **When a day closes, and the check-out — Ajay, 2026-09-14. Supersedes the earlier "a day that ends on an IN has no check-out" sentence.**

   - **A day's scans** run from the attendance window before its shift start until the day closes: the employee's **next shift start, or 24 hours after this shift started, whichever comes first**. That covers two shifts in 24 hours, and a day before a weekly off or holiday does not swallow scans made on the off day (those belong to the off day, as holiday work). This replaces the fixed window after the shift end.
   - **While the day is open**, a trailing OUT is a **break-out**, not a check-out — someone out at lunch at 13:00 is on a break, not gone. A trailing IN means **still in**.
   - **When the day closes**, a trailing OUT becomes the **check-out**. If the day ends on an IN, the **check-out is the shift's scheduled end time**, and the day is flagged **needs review** ("check-out by rule, no scan"). It counts up to the shift end until a reviewer corrects it — on Attendance → Days to review → Fix a day (built 2026-09-14, N5): add the real check-out scan, change the day's status, or accept the rule's time. A correction is an input to every later recalculation, never an edit to the record, so it survives new punches; each one is audited and can be withdrawn. An open overtime session is decided on the Overtime page (A9).
   - **After the shift end.** Time after the scheduled end is **overtime**, not regular worked time. With a real OUT it is an overtime candidate for approval (plan step A9). If someone scans back in after the shift ended and never scans out, that overtime session has a **blank check-out**, goes to review, and **pays nothing unless an admin or HR approves it**. The regular day still closes as above. Approving it (Overtime page, plan step A9) means typing the time the person left; the minutes follow from it and the review flag clears. Overtime that ends in a real OUT is **approved automatically** (Ajay, 2026-09-14); admin or HR can still pay fewer minutes or reject it. Paid at × the hourly rate set on Salary settings (2× by default).
   - **Arriving early** (e.g. 08:30 for a 09:00 shift) is normal: it is **not overtime and not paid**. Regular worked time counts from the shift start, while the real check-in time is still shown. Lateness is measured from the shift start after grace, as before. Overtime is only time after the shift end (Ajay, 2026-09-14).
   - **A working day with no scans** is "not in yet" until the shift ends, then absent.
   - **Live, not on request.** Attendance is recalculated automatically: when punches arrive, for the employee-days they belong to (including a device's late backlog for earlier days); when a schedule, holiday, weekly off or leave changes, for the days it affects; and when a day closes. Nobody presses Calculate. A day inside a finalised salary month never changes.
   - The company setting `auto_absent` (a day without a check-out is absent) stays for a company that chose it; `review_required` now means the rule above.
8. Derive the single AttendanceRecord. Its branch and department come from the employee assignment; a source device at another branch does not transfer employment, shift ownership, or salary allocation.

Night shifts use the scheduled work date/window rather than splitting at midnight. Missing or ambiguous punches remain reviewable. A new device does not imply an IN punch, and a device change does not automatically end a session.

For offline delivery, current settings cannot substitute for historical policy. AuditLog must record device-scope and enablement/grant changes with complete relevant before/after snapshots and an effective timestamp; for initial immediate changes this is the audited transaction time. Preserve this audit history for at least as long as punch reconciliation needs it. Employee overrides also use dated EmployeeAssignment intervals; DeviceDepartment links and enrollment identity already have effective intervals.

This plan does not add a separate scope-version table. Historical company/branch/enablement resolution must therefore reconstruct the policy from the retained structured AuditLog changes and current state, including the initial configuration. A saved authorization_snapshot helps reproduce an already-evaluated punch but cannot establish the policy for a newly arriving historical punch. If history is missing or ambiguous, mark policy_unresolved and require review rather than silently applying today's broader permission. Scheduled/retroactive policy editing needs a deliberate extension of this history contract; it is not implied by mutable current fields.

Changing a scope or enablement flag must not overwrite earlier punch decisions silently. Re-evaluation must be audited and trigger the relevant attendance calculation revision. If payroll is already finalized, follow the existing correction/off-cycle process.

**Re-check punches — built 2026-09-14 (N4).** Re-evaluation is an explicit administrator action on Devices → Which devices count, for a date range of at most 366 days in company time:

- It re-judges only punches that do not count (every excluding status plus `policy_unresolved`), under the policy switches **as they are at the re-check** — the scope chain, `assigned_device_authorized` and `attendance_enabled`. Identity, the employee's assignment and device-department links are still read at the punch time.
- A punch that already counts is never re-judged, so a narrower rule cannot remove attendance already credited.
- Days inside a posted payroll run are skipped entirely.
- Each punch keeps a `recheck` note (time, actor, previous status) in its `authorization_snapshot`; one `punches.rechecked` AuditLog row holds every punch's before and after status.
- Attendance for the employee-days of punches that now count is recalculated after the commit.

Without a re-check, stored decisions never change when a setting changes.

## Duplicate and clock handling

Vendor event identifiers are device-scoped: Device 1's transaction 123 and Device 10's transaction 123 are different events. Same-employee punches close together across different devices may be rapid repeat scans, but proximity alone is not proof of duplication. Preserve both, use configured repeat filtering or review, and do not silently remove valid short intervals. Clock skew can reverse apparent order; retain raw time, clock interpretation, and review uncertainty instead of forcing a plausible sequence.

## Examples the next implementation must verify

| Scenario | Expected result |
|---|---|
| Employee checks in on Device 1 at 09:55 and out on Device 10 at 17:05; both are allowed | One session and one daily record. Each punch retains its own device. |
| Four allowed punches arrive from four devices | One employee/shift sequence, interpreted IN, OUT, IN, OUT after filtering; no per-device reset. |
| assigned_devices employee scans a recognition-only enrollment with assigned_device_authorized=false | Preserve punch, mark unauthorized_device, exclude from calculation. |
| branch_devices employee scans two same-branch devices with different department mappings | Both can count; department links do not restrict this mode. |
| department_devices employee scans a shared device with no department links | Can count within the assigned branch if the common eligibility checks pass. |
| department_devices employee scans a device mapped only to another department | Preserve punch, mark department_mismatch, exclude from calculation. |
| branch_devices employee scans another branch's device | Preserve punch, mark branch_mismatch, exclude from calculation. |
| company_devices employee scans another branch's device | Can count in the original employee/shift daily record; preserve source branch separately. |
| Company uses branch_devices but one employee has assigned_devices override | That employee remains restricted to explicitly authorized devices. |
| Wider scope but attendance_enabled=false on that enrollment | Preserve punch, mark enrollment_disabled, exclude from calculation. |
| Another company's device/identity is supplied | Do not cross tenant boundaries or attach it to this company's employee. |
| Device 10's earlier punch arrives after Device 1's later punch | Merge by event time and recalculate the affected day through the normal revision process. |
| Company changes scope after an offline punch occurred | Evaluate the historical policy; missing history results in policy_unresolved/review. |

These are required future service checks, not claims that an attendance engine already exists. See MODEL_FIELD_DICTIONARY.md for columns and DATABASE_SCHEMA.md for the overall architecture.
