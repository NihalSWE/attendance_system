# Basic attendance relationship diagram only

`BASIC_ATTENDANCE.dbml` is a revised version of the user-supplied 46-table sketch. It has 48 tables. Copy its complete contents into dbdiagram.io. It is a visual aid only: do not generate application models/migrations from it or use it to replace the authoritative advanced plan. No application code, real database, roadmap or authoritative schema files were changed for this task.

## What was missing or misleading

Every original integer foreign-key column already had a Ref. The issues were missing columns/links, incorrect relationship direction, and disconnected concepts.

- Department needs Branch; Designation needs Department and optional parent Designation.
- Company entitlement needs a separate path to individual permissions. AccessPermission now links to Feature; DesignationPermission and EmployeePermissionOverride link those permissions to their holders.
- EmployeeAssignment retains permanent Employee identity, dated branch/department/designation and reusable employee_code; manager is optional. Effective start/end replaces the inadequate effective_date + is_historical combination. Compensation also has a date range.
- CompanyAttendanceSettings has one row per Company and an optional company_shift. WeeklyOffRule and Holiday can be branch-specific. HolidayWorkAssignment can reference either a holiday or a weekly-off rule, plus an optional shift.
- DeviceVendor and DeviceModel are global catalogues; tenant ownership belongs on BiometricDevice and its operational rows.
- BiometricTemplate belongs to Employee. DeviceEnrollmentTemplate connects DeviceEnrollment to BiometricTemplate, rather than merely repeating a device ID. DeviceSyncState is one-to-one with the device.
- PunchEvent references its DeviceMessage and optional resolved enrollment/employee. An unresolved punch is still valid raw evidence. duplicate_of preserves lineage; it is not permission to delete evidence.
- AttendanceRecord is the daily parent of many allocations and sessions. Each session has separate in_allocation and out_allocation references. PunchAllocation has exactly one source: raw punch or approved correction. This preserves cross-device pairing and manual corrections.
- PenaltyAssessment references an employee and rule. PenaltyAssessmentAttendance already supplies the many-to-many evidence relation; the redundant single attendance_record pointer was removed from the assessment.
- LeaveType makes leave classification visible. PayrollPeriod groups dates. Daily payroll can use multiple LeaveDay rows, represented with DBML many-to-many notation, and links to relevant salary/assignment history and calendar evidence.
- SalaryAdvanceRecovery connects an advance to recoveries across payrolls. A single FK on SalaryAdvance cannot describe several installments. PayrollAdjustment now shows its target period and simplified posting destination.
- Direct company ownership is included on tenant-owned rows. Basic single-column FKs do not prove that all linked rows share a company; same-company/employee/date checks and advanced constraints still belong to the final schema.

## Naming decisions

- The isolated Module box was omitted because Feature already represents the product-module catalogue. A separate Module hierarchy would require a separately defined purpose.
- Permission + Access was consolidated to the established AccessPermission name, with its Feature FK. DesignationPermission supplies the missing designation-grant relation.
- EmployeeRegistrationAccess had no permission/device target and no defined counterpart in the final plan. The revised diagram shows the established EmployeePermissionOverride for employee grants instead. This does not invent a new registration feature. If the original box was meant to represent biometric registration, that is covered by DeviceEnrollment.
- EmployeeShiftAssignment references Shift directly. Department eligibility is checked through dated organizational/DepartmentShift history; no new department_shift FK is proposed.

## Deliberate visual shortcuts — not changes to the advanced model plan

- Display `name`, `salary` and `amount` fields are illustrative summaries, not the complete Django field contract.
- LeaveRequest -> LeaveType and LeaveDay -> LeaveRequest collapse policy and request-segment layers. They are not proposed replacements for the final leave relationships.
- PayrollRecord -> PayrollPeriod collapses PayrollRun. PayrollDailyLine -> PayrollRecord and history rows collapses PayrollCompensationSegment. Multiple runs, compensation segments, recalculation history and actual posting remain in the final plan.
- SalaryAdvanceRecovery -> PayrollRecord and PayrollAdjustment -> PayrollRecord summarize the actual PayrollLine posting path.
- PayrollSettings is retained as the original sketch's basic settings box; detailed payroll policy/version models are outside this view.
- Direct PayrollDailyLine <> LeaveDay is a visual many-to-many shorthand. A physical implementation needs its junction relation, as already specified by the final plan.
- Approval steps, audit actors, detailed temporal/exclusion constraints, subscriptions, disbursements, salary payments, loan extensions and most scalar fields are intentionally outside this mini diagram. Their absence here does not remove them from the final plan.
- One current master administrator per company remains a conditional role/status rule. A unique company_id on CompanyMembership would incorrectly forbid all other staff memberships, so this diagram does not add that constraint.

DBML relationship syntax is checked against the official documentation: https://dbml.dbdiagram.io/docs/#relationships--foreign-key-definitions

## Validation performed

The official @dbml/cli successfully parsed the file and exported PostgreSQL SQL into an ignored local QA file. The SQL was not executed. An additional structural check verified 48 tables, 123 unique relationships, valid reference endpoints and no disconnected integer foreign-key columns.
