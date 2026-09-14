# Model Field Dictionary

> Implemented schema correction (2026-09-07): Company.code remains varchar/string but is generated from a PostgreSQL sequence starting at 10001; slug is generated with name-based numeric suffixes. Existing identifiers remain unchanged. Company defaults are BDT, BD, Asia/Dhaka. CompanyMembership now has conditional uniqueness on company and user for non-ended owner/company_admin roles (one current master per company, not shared across companies). Historical ended memberships remain. CompanyAttendanceSettings.company is implemented as FK + UNIQUE (one-to-one cardinality). No new model/table/column was added by this correction; generated diagrams retain the same relationships. See UI_AND_ONBOARDING_CONVENTIONS.md for workflow rules.


> This is documentation for the separate attendance project. It defines proposed Django fields and relationships; it is not executable model code.

Read [PROJECT_HANDOFF.md](PROJECT_HANDOFF.md) first and [DATABASE_MODEL_PLAN.md](DATABASE_MODEL_PLAN.md) for plain-language model purposes. This document is the proposed field contract for the 86 models.

These models belong to 12 domain apps. The user will also create the model-free base_template app for shared UI, making 13 Django apps in total without adding database tables. See [PROJECT_SETUP.md](PROJECT_SETUP.md) for commands, template ownership and the user-selected FastAPI API direction.

## Conventions

Django relation notation:

- `company — FK -> tenants.Company` means the Django field is `company`; PostgreSQL stores `company_id`.
- `O2O` means one-to-one.
- `M2M` means many-to-many. Where a named through model exists, use it rather than an implicit table.
- `PROTECT` preserves historical business records. `SET_NULL` is used for optional actors or references that may be detached. `CASCADE` is reserved for configuration/junction rows whose parent fully owns them.
- Date/time ranges use an inclusive start and exclusive end: `effective_from <= instant < effective_to`. A null end means open-ended.
- Store operational timestamps as timezone-aware UTC. Retain local work date, device-local timestamp, timezone, and offset where business meaning depends on them.
- Money uses `DecimalField(max_digits=18, decimal_places=2)`. Rates/quantities use enough scale for prorating, normally `DecimalField(max_digits=18, decimal_places=6)`.
- JSON fields hold variable adapter input or frozen calculation snapshots. They must not hide a relation that should be an FK.
- Files are private storage references. Do not expose raw storage paths publicly.
- Choice fields below are examples of controlled enums; store stable codes, not display labels.

These conceptual abstract bases are not database tables and are not part of the 86-model count:

```text
TimeStamped:
  created_at — DateTimeField(auto_now_add=True)
  updated_at — DateTimeField(auto_now=True)

TenantOwned:
  id — BigAutoField(primary_key=True)
  company — FK -> Company, PROTECT, indexed
  created_at, updated_at

ActorTracked:
  created_by — nullable FK -> User, SET_NULL
  updated_by — nullable FK -> User, SET_NULL
```

## Physical table naming

Django **model names and app labels are chosen for the code** — whatever reads
best for the project. The **physical table name is set separately** with
`Meta.db_table`, and that is what appears in PostgreSQL.

| App | Table prefix | Example |
|---|---|---|
| `leaves` | **`payroll_`** | `LeaveRequest` -> `payroll_leave_request` |
| `attendance` | **`payroll_`** | `AttendanceRecord` -> `payroll_attendance_record` |
| `payroll` | **`payroll_`** | `PayrollRun` -> `payroll_run` |
| every other app | its own label | `Employee` -> `employees_employee` (unchanged) |

Leave, attendance and payroll form one payroll family in the database, so they
share one prefix and sort together. Salary models already live in the `payroll`
app, so they need no change.

Rules:

- Always set `db_table` explicitly on a new model. Never rely on Django's
  implicit `<app>_<model>` name, because it silently changes if an app is ever
  renamed.
- The table name is `<prefix>_<snake_case_model_name>`: `LeaveType` becomes
  `payroll_leave_type`. Words are separated by underscores.
- When the snake_case name already begins with `payroll_`, it is NOT prefixed
  again: `PayrollRun` becomes `payroll_run`, never `payroll_payroll_run`.
- Implicit M2M junction tables inherit their parent's prefix automatically.
- Table names verified collision-free across all 91 tables after this rule was
  applied.
- Tables created before this rule was written do not follow the snake_case part
  of it: `scheduling_departmentshift`, `access_control_designationpermission`
  and `employees_employeeassignment` run the words together. They keep their
  names until somebody decides an `AlterModelTable` migration is worth it. New
  multi-word tables do follow the rule: `organization_company_department`,
  `organization_company_designation`, `access_control_department_permission`.

**Client-facing table names.** Some table names exist to match the client's
vocabulary, not ours. `Feature` uses `db_table = "module"` because the client
thinks in modules (Payroll now; HR and Accounting later). The model stays
`Feature`, its three rows stay as they are, and no relationship changes —
`db_table` is cosmetic. **Never restructure data to satisfy a naming
preference**: if a client's preferred name implies a different structure, that is
a conversation about the data model, not something a table name can paper over.

**Already-migrated tables are not renamed by this rule.** `employees_employeecompensation`
and `scheduling_companyattendancesettings` hold live data and keep their current
names: compensation is dated employment history that payroll *reads*, and
attendance settings are policy configuration rather than attendance records.
Renaming either would need an explicit `AlterModelTable` migration and a
deliberate decision.

Every tenant-owned relation must be validated as belonging to the same company. Forms alone are not sufficient; enforce this in services and database constraints where possible.

Device authorization and pairing follow [DEVICE_ATTENDANCE_POLICY.md](DEVICE_ATTENDANCE_POLICY.md). Enrollment identifies a person; the effective device scope determines whether a punch counts. Entry and exit devices need not match.

## 1. accounts

### 1. User

Use a custom Django user from the first migration, preferably based on `AbstractUser`.

Required naming contract: class name **User**, app label **accounts**, `AUTH_USER_MODEL = "accounts.User"`. Runtime model lookup is `User = get_user_model()` after importing get_user_model from django.contrib.auth. Do not rename this model CustomUser or use the concrete django.contrib.auth.models.User. FK/O2O/M2M declarations referencing User should use settings.AUTH_USER_MODEL; data migrations must use historical models. Every User reference in this dictionary means the same accounts.User. See PROJECT_SETUP.md for the complete contract.

- `id` — BigAutoField, PK.
- `email` — EmailField, unique, login identifier.
- `username` — CharField, unique compatibility identifier if retaining AbstractUser; generated/internal when login is email.
- `first_name`, `last_name` — CharField.
- `phone` — CharField, blank.
- `timezone`, `language` — CharField preferences.
- `password`, `last_login`, `date_joined` — Django authentication fields.
- `is_active`, `is_staff`, `is_superuser` — Django account flags. Platform root is `is_superuser=True`.
- `groups`, `user_permissions` — Django authentication M2M fields, used only for platform/framework administration unless a later decision deliberately integrates them.
- `created_at`, `updated_at` — timestamps.

Relations: memberships, optional employee links, and actor/audit relations. Deactivate accounts instead of deleting them.

### 2. CompanyMembership

Common fields: TenantOwned plus actor tracking.

- `user` — FK -> User, PROTECT.
- `role` — CharField choices: owner, company_admin, hr, manager, payroll_manager, employee, auditor.
- `status` — CharField choices: invited, active, suspended, ended.
- `joined_at`, `ended_at` — nullable DateTimeField.
- `invited_by` — nullable FK -> User, SET_NULL.
- `allowed_branches` — M2M -> Branch, blank; empty means unrestricted at branch level for an eligible role.
- `allowed_departments` — M2M -> CompanyDepartment, blank; empty means unrestricted within allowed branches.
- `last_access_at` — nullable DateTimeField.

Constraints: unique `(company, user)`; departments selected in scope must belong to a selected/allowed branch and the same company. The two M2M fields create implicit junction tables unless later replaced by an explicit scope model.

## 2. tenants

### 3. Company

Common fields: TimeStamped plus creator/updater.

- `id` — BigAutoField, PK.
- `public_id` — UUIDField, unique, immutable external identifier.
- `code`, `slug` — unique CharField identifiers.
- `name`, `legal_name` — CharField.
- `timezone` — CharField, default chosen during setup.
- `currency` — CharField(3), ISO code.
- `language`, `country_code` — CharField.
- `email`, `phone`, `address` — contact fields.
- `registration_number`, `tax_identifier` — optional CharField.
- `logo` — optional ImageField/private media reference.
- `status` — choices: trial, active, suspended, inactive.
- `activated_at`, `suspended_at` — nullable DateTimeField.
- `suspended_by` — nullable FK -> User, SET_NULL.
- `suspension_reason` — TextField, blank.

Relations: root of all tenant-owned rows; CompanyMembership, Branch, CompanyFeature, CompanySubscription, settings, employees, and operational records.

### 4. Feature

Global reference model; no company FK.

- `id` — BigAutoField, PK.
- `code` — CharField, unique, such as attendance, leave, payroll.
- `name`, `description` — display fields.
- `is_core` — BooleanField; core features cannot be removed from eligible packages.
- `is_active` — BooleanField.
- `sort_order` — PositiveIntegerField.
- `created_at`, `updated_at` — timestamps.

Relations: PackageFeature, CompanyFeature, AccessPermission.

### 5. CompanyFeature

Common fields: TenantOwned.

- `feature` — FK -> Feature, PROTECT.
- `effect` — choices: enable, disable.
- `starts_at`, `ends_at` — nullable DateTimeField.
- `limits` — JSONField for feature-specific scalar limits, validated by feature.
- `reason` — TextField.
- `granted_by` — FK -> User, PROTECT.
- `is_active` — BooleanField.

Constraints: prevent overlapping active overrides for the same company/feature/effect. Effective access combines package snapshot and this dated override.

## 3. organization

### 6. Branch

Common fields: TenantOwned plus actor tracking.

- `code` — CharField.
- `name` — CharField.
- `address`, `city`, `postal_code`, `country_code` — location fields.
- `timezone` — CharField; defaults from Company.
- `email`, `phone` — optional contact fields.
- `is_default` — BooleanField.
- `status` — choices: active, inactive.
- `device_attendance_scope_override` — nullable CharField: assigned_devices, department_devices, branch_devices, company_devices; null inherits CompanyAttendanceSettings. This is the employee's assigned/home branch policy, not the source device branch's policy.
- `opened_on`, `closed_on` — nullable DateField.

Constraints: unique `(company, code)`; one active default branch per company.

### 7. Department

**Root-owned catalogue. Not TenantOwned — no company column, and no company may write to it.** One "Human Resources" exists for the whole platform; a company adopts it through CompanyDepartment (84) rather than creating its own spelling of it.

Common fields: timestamps plus actor tracking.

- `code` — CharField, globally unique.
- `name` — CharField, globally unique.
- `description` — TextField, blank.
- `status` — active/inactive.

Nothing company-specific lives here: no branch, no head, no open/close dates. Those belong to the adoption row.

Relations: Designation, CompanyDepartment.

### 8. Designation

**Root-owned catalogue, same rule as Department — and independent of it.** Root keeps departments and designations as two separate lists. Which designations sit under which department is **each company's decision**, recorded on CompanyDesignation (85). One "Manager" therefore exists for the whole platform, and each company places it under whichever of its own departments need one.

Common fields: timestamps plus actor tracking.

- `code` — CharField, globally unique.
- `name` — CharField, globally unique.
- `description` — TextField, blank.
- `status` — active/inactive.

There is deliberately **no `department` field**. A root-level link would make the department–designation relation the same for every company, which is not how companies are organised: one puts "Manager" under Sales, another under Production.

There is deliberately **no parent/child hierarchy**. Access ceilings are carried by the department through DepartmentPermission (86), not by walking a chain of titles: a chain that is correct for one company is wrong for the next, and a global catalogue cannot be both.

### 84. CompanyDepartment

One company's use of a catalogue department, inside one of its branches. **This is the row every company-specific fact points at** — employees, shifts, permission rules, membership scopes — so nothing set by one company can reach another.

Common fields: TenantOwned plus actor tracking.

- `branch` — FK -> Branch, PROTECT.
- `department` — FK -> Department (catalogue), PROTECT.
- `head` — nullable FK -> Employee, PROTECT. The employee who administers permissions for everyone in this department.
- `description` — TextField, blank.
- `status` — active/inactive.
- `opened_on`, `closed_on` — nullable DateField.

`code` and `name` are read through to the catalogue and are **not stored locally**: a local copy could drift, which is the duplication the catalogue exists to prevent.

Constraints: unique `(branch, department)` — a branch adopts each catalogue department at most once; the branch must belong to the same company.

### 85. CompanyDesignation

**The department–designation relation, and it is company-wise.** One row places a root designation under one of the company's own departments (CompanyDepartment). Root decides which designations exist; each company decides where they go.

Common fields: TenantOwned plus actor tracking.

- `company_department` — FK -> CompanyDepartment, PROTECT.
- `designation` — FK -> Designation (catalogue), PROTECT.
- `status` — active/inactive.

`code`, `name` and `branch` are read through, as with CompanyDepartment.

Constraints: unique `(company_department, designation)`. Any active designation may be placed under any of the company's departments; a designation root has deactivated cannot be newly placed, but existing placements keep it.

## 4. employees

### 9. Employee

Common fields: TenantOwned plus actor tracking.

- `public_id` — UUIDField, unique immutable external identifier.
- `user` — nullable FK -> User, PROTECT.
- `first_name`, `middle_name`, `last_name`, `preferred_name` — CharField.
- `work_email`, `personal_email`, `phone` — optional contacts.
- `date_of_birth` — nullable DateField.
- `gender`, `blood_group`, `marital_status` — optional controlled fields.
- `national_id`, `passport_number` — optional protected identifiers.
- `address`, `emergency_contact_name`, `emergency_contact_phone`, `emergency_contact_relation` — optional fields.
- `joining_date`, `confirmation_date`, `leaving_date` — nullable DateField as applicable.
- `employment_status` — applicant, active, probation, suspended, resigned, terminated, retired.
- `photo` — optional private ImageField.
- `metadata` — JSONField for validated company-specific non-core attributes.

Constraints: unique `(company, user)` when user is not null. All business history references this permanent identity.

### 10. EmployeeAssignment

Common fields: TenantOwned plus actor tracking.

- `employee` — FK -> Employee, PROTECT.
- `employee_code` — CharField; manually entered reusable business code.
- `branch` — FK -> Branch, PROTECT.
- `department` — FK -> CompanyDepartment, PROTECT. The company's adoption row, never the catalogue row.
- `designation` — FK -> CompanyDesignation, PROTECT.
- `manager` — nullable FK -> Employee, PROTECT.
- `effective_from` — DateTimeField.
- `effective_to` — nullable DateTimeField.
- `change_reason` — TextField.
- `status` — active/ended/cancelled.
- `device_attendance_scope_override` — nullable CharField: assigned_devices, department_devices, branch_devices, company_devices; null inherits the assigned branch/company policy. This employee-level override lives on dated assignment history; changing it closes the old interval and creates its successor.

Constraints: the company department belongs to the assignment's branch; the designation is one the company has placed under that department (a CompanyDesignation of it); manager is not the employee; no overlapping active periods for one employee; no overlapping occupancy of `(company, employee_code)`. The same code may be reused after the earlier interval ends.

### 11. EmployeeCompensation

Common fields: TenantOwned plus actor tracking.

- `employee` — FK -> Employee, PROTECT.
- `pay_basis` — monthly, daily, hourly.
- `base_rate` — DecimalField money.
- `currency` — CharField(3).
- `overtime_rate_override` — nullable DecimalField money per hour.
- `effective_from` — DateTimeField.
- `effective_to` — nullable DateTimeField.
- `reason` — TextField.
- `status` — draft, active, ended, cancelled.

Constraints: positive base rate; currency matches company policy; non-overlapping active compensation ranges per employee.

## 5. access_control

### 12. AccessPermission

Global permission catalogue; no company FK.

- `id` — BigAutoField, PK.
- `feature` — FK -> Feature, PROTECT.
- `code` — CharField, globally unique, e.g. `leave.approve`.
- `name`, `description` — display fields.
- `action` — view, create, edit, approve, finalize, pay, manage.
- `is_sensitive` — BooleanField for payroll/biometric actions.
- `is_active` — BooleanField.
- `created_at`, `updated_at` — timestamps.

### 13. DesignationPermission

Common fields: TenantOwned plus actor tracking.

- `designation` — FK -> CompanyDesignation, PROTECT.
- `permission` — FK -> AccessPermission, PROTECT.
- `access_level` — default, allowed, denied.
- `can_delegate` — BooleanField.
- `effective_from`, `effective_to` — nullable DateTimeField.
- `reason` — TextField.

Constraints: unique effective rule per designation/permission at any instant; a title may not be ALLOWED what its own department DENIES through DepartmentPermission (86). That department ceiling replaced the old designation parent-chain walk.

### 14. EmployeePermissionOverride

Common fields: TenantOwned plus actor tracking.

- `employee` — FK -> Employee, PROTECT.
- `permission` — FK -> AccessPermission, PROTECT.
- `effect` — grant/revoke.
- `allowed_branches` — M2M -> Branch, blank; empty inherits membership/designation scope.
- `allowed_departments` — M2M -> CompanyDepartment, blank.
- `effective_from`, `effective_to` — nullable DateTimeField.
- `granted_by` — FK -> User, PROTECT.
- `reason` — TextField.
- `status` — active/revoked/expired.

Constraints: a GRANT may not exceed what the employee's current department DENIES — this is what makes delegating to a department head safe, since the head cannot widen the boundary they administer inside; selected departments belong to selected branches/company.

### 86. DepartmentPermission

What a whole department may do, on a dated interval. **The department is the unit of delegation**, so this layer is both the ceiling and the floor for everyone in it.

Common fields: TenantOwned plus actor tracking.

- `company_department` — FK -> CompanyDepartment, PROTECT.
- `permission` — FK -> AccessPermission, PROTECT.
- `access_level` — default, allowed, denied.
- `can_delegate` — BooleanField; whether the department head may pass this on.
- `effective_from`, `effective_to` — nullable DateTimeField.
- `reason` — TextField.

A DENIED rule is a hard ceiling: neither a designation rule (13) nor an individual grant (14) can lift it. An ALLOWED rule is the floor everyone in the department gets unless something below revokes it individually.

Constraints: one effective rule per department/permission at any instant; end after start.

## 6. scheduling

### 15. Shift

Common fields: TenantOwned plus actor tracking.

- `code`, `name` — CharField.
- `start_time`, `end_time` — TimeField.
- `spans_next_day` — BooleanField.
- `scheduled_minutes` — PositiveIntegerField; validated against times and breaks or calculated snapshot.
- `grace_in_minutes`, `grace_out_minutes` — PositiveIntegerField.
- `minimum_full_day_minutes`, `minimum_half_day_minutes` — PositiveIntegerField.
- `default_break_minutes` — PositiveIntegerField.
- `break_is_paid` — BooleanField.
- `overtime_after_minutes` — PositiveIntegerField after scheduled end.
- `effective_from`, `effective_to` — nullable DateField.
- `status` — active/inactive.

Constraints: unique `(company, code)`; timing and minute thresholds must be internally consistent.

### 16. DepartmentShift

Common fields: TenantOwned plus actor tracking.

- `department` — FK -> CompanyDepartment, PROTECT.
- `shift` — FK -> Shift, PROTECT.
- `is_default` — BooleanField.
- `effective_from` — DateField.
- `effective_to` — nullable DateField.
- `status` — active/ended.

Constraints: no overlapping duplicate department/shift assignments; at most one default at a time. In single-shift company mode all active department shifts must reference the company shift.

### 17. EmployeeShiftAssignment

Common fields: TenantOwned plus actor tracking.

- `employee` — FK -> Employee, PROTECT.
- `shift` — FK -> Shift, PROTECT.
- `effective_from` — DateTimeField.
- `effective_to` — nullable DateTimeField.
- `assignment_type` — department_selection, employee_override, temporary.
- `reason` — TextField.
- `assigned_by` — FK -> User, PROTECT.
- `status` — active/ended/cancelled.

Constraints: no overlapping effective assignments for an employee. Validate department eligibility except for an authorized override.

### 18. CompanyAttendanceSettings

Common fields: TenantOwned plus actor tracking. One-to-one relationship with Company rather than a second company FK row set.

- `company` — O2O -> Company, PROTECT, primary tenant owner.
- `shift_mode` — company_single_shift or department_shifts.
- `company_shift` — nullable FK -> Shift, PROTECT.
- `punch_pairing_strategy` — alternating initially.
- `device_attendance_scope` — CharField: assigned_devices, department_devices, branch_devices, company_devices; default assigned_devices. Effective precedence: EmployeeAssignment override, assigned Branch override, company setting.
- `duplicate_punch_window_seconds` — PositiveIntegerField.
- `attendance_window_before_minutes`, `attendance_window_after_minutes` — PositiveIntegerField.
- `missing_punch_policy` — review_required by default.
- `overtime_requires_approval` — BooleanField.
- `round_work_minutes_to`, `round_overtime_minutes_to` — PositiveIntegerField.
- `settings_version` — PositiveIntegerField.
- `effective_from` — DateTimeField.

Constraint: company_shift required only in single-shift mode. Historical AttendanceRecord snapshots preserve rules actually used.

### 19. WeeklyOffRule

Common fields: TenantOwned plus actor tracking.

- `branch` — nullable FK -> Branch, PROTECT; null means company-wide.
- `weekday` — choices 0–6.
- `is_paid` — BooleanField.
- `effective_from` — DateField.
- `effective_to` — nullable DateField.
- `status` — active/ended.

Constraints: prevent overlapping duplicate weekday rules for the same company/branch.

### 20. Holiday

Common fields: TenantOwned plus actor tracking.

- `branch` — nullable FK -> Branch, PROTECT; null means company-wide.
- `holiday_date` — DateField.
- `name`, `description` — text fields.
- `is_paid` — BooleanField.
- `status` — active/cancelled.
- `cancelled_at`, `cancelled_by` — nullable timestamp/FK -> User.

Constraints: unique active holiday per `(company, branch, holiday_date)`, treating null branch as a comparable company-wide scope.

### 21. HolidayWorkAssignment

Common fields: TenantOwned plus actor tracking.

- `employee` — FK -> Employee, PROTECT.
- `work_date` — DateField.
- `holiday` — nullable FK -> Holiday, PROTECT.
- `weekly_off_rule` — nullable FK -> WeeklyOffRule, PROTECT.
- `shift` — nullable FK -> Shift, PROTECT.
- `treatment` — normal_workday, overtime, compensatory_leave, overtime_and_comp_leave when explicitly allowed.
- `reason` — TextField.
- `approved_by`, `approved_at` — FK -> User and DateTimeField.
- `status` — draft, approved, cancelled.

Constraints: exactly one applicable source (dated holiday or recurring weekly-off rule); source applies on work_date; one active assignment per employee/work_date/source.
## 7. devices

### 22. DeviceVendor

Global reference model.

- `id` — BigAutoField, PK.
- `code`, `name` — unique identifiers/display name.
- `adapter_key` — CharField selecting a reviewed application adapter, never executable user input.
- `description`, `support_url` — optional fields.
- `is_active` — BooleanField.
- `created_at`, `updated_at` — timestamps.

Relations: DeviceModel and optional BiometricTemplate format metadata.

### 23. DeviceModel

Global reference model.

- `id` — BigAutoField, PK.
- `vendor` — FK -> DeviceVendor, PROTECT.
- `model_code`, `name` — CharField.
- `protocol` — adms_push, webhook, vendor_cloud_api, file_import, other.
- `capabilities` — JSONField, validated flags for push, face, fingerprint, card, commands, template export/import.
- `supported_template_formats` — JSONField list of stable format/version codes.
- `is_active` — BooleanField.
- `created_at`, `updated_at` — timestamps.

Constraint: unique `(vendor, model_code)`.

### 24. BiometricDevice

Common fields: TenantOwned plus actor tracking.

- `public_id` — UUIDField, unique.
- `branch` — FK -> Branch, PROTECT.
- `device_model` — FK -> DeviceModel, PROTECT.
- `name`, `serial_number` — CharField.
- `external_device_id` — optional CharField used by vendor/cloud.
- `timezone` — CharField.
- `authentication_key_id` — CharField identifying a server-managed secret.
- `authentication_secret_hash` — password/hash field; never return the secret after enrollment.
- `firmware_version`, `ip_address_last_seen` — optional diagnostic fields.
- `installed_at`, `decommissioned_at`, `last_seen_at`, `last_message_at` — nullable DateTimeField.
- `clock_offset_seconds` — nullable IntegerField diagnostic estimate.
- `settings` — validated JSONField for vendor-specific configuration.
- `status` — pending, active, offline, suspended, retired.

Constraints: unique `(company, serial_number)`; device branch belongs to company.

### 25. DeviceDepartment

Common fields: TenantOwned plus actor tracking.

- `device` — FK -> BiometricDevice, PROTECT.
- `department` — FK -> CompanyDepartment, PROTECT. Not the catalogue row: a device belongs to one company's department in one branch.
- `effective_from` — DateTimeField.
- `effective_to` — nullable DateTimeField.
- `status` — active/ended.

Constraints: device and department have the same company and branch; no overlapping duplicate mapping. This is the explicit through table for `BiometricDevice.departments`. Active mappings restrict department_devices mode; no active mappings means a shared branch device in that mode. These mappings do not additionally restrict branch_devices/company_devices mode or an explicit assigned-device grant.

### 26. DeviceEnrollment

Common fields: TenantOwned plus actor tracking.

- `device` — FK -> BiometricDevice, PROTECT.
- `employee` — FK -> Employee, PROTECT.
- `device_user_id` — CharField exactly as stored by the device.
- `card_number` — optional encrypted/masked CharField.
- `device_privilege` — normal_user, device_admin, other.
- `attendance_enabled` — BooleanField, default true; a per-enrollment master switch. False excludes punches under every device scope, even company_devices. Evaluate historical value at punch time.
- `assigned_device_authorized` — BooleanField, default false; explicit permission for assigned_devices mode. Wider department/branch/company scopes do not require this flag. Creating an enrollment for recognition alone does not grant restricted-mode attendance permission.
- `effective_from` — DateTimeField.
- `effective_to` — nullable DateTimeField.
- `enrollment_status` — pending, queued, synced, failed, removed.
- `vendor_enrollment_revision` — optional CharField.
- `last_synced_at`, `removed_at` — nullable DateTimeField.
- `sync_error_code`, `sync_error_message` — optional fields.

Constraints: no overlapping use of one `(device, device_user_id)`; no overlapping duplicate employee/device authorization; resolve historical mapping using the punch timestamp. Enrollment identity, master enablement, and explicit assigned-device grant are separate concerns. Keep historical authorization changes in the audit trail; see DEVICE_ATTENDANCE_POLICY.md.

### 27. BiometricTemplate

Common fields: TenantOwned plus actor tracking.

- `employee` — FK -> Employee, PROTECT.
- `biometric_type` — fingerprint or face.
- `finger_position` — nullable choice for fingerprint.
- `template_format`, `template_version` — CharField.
- `vendor` — nullable FK -> DeviceVendor, PROTECT.
- `compatible_device_model` — nullable FK -> DeviceModel, PROTECT.
- `captured_from_device` — nullable FK -> BiometricDevice, SET_NULL.
- `encrypted_template_data` — BinaryField containing application-encrypted bytes.
- `encryption_key_version` — CharField.
- `template_checksum` — CharField.
- `template_size_bytes`, `quality_score` — nullable integers.
- `captured_at` — DateTimeField.
- `status` — active, revoked, incompatible, deleted_pending.
- `revoked_at`, `revoked_by`, `revocation_reason` — nullable audit fields.

Constraints: checksum/employee/type/format duplicate detection; face has no finger_position. Database backups and application logs must protect the encrypted data and key references.

### 28. DeviceEnrollmentTemplate

Common fields: TenantOwned.

- `device_enrollment` — FK -> DeviceEnrollment, PROTECT.
- `biometric_template` — FK -> BiometricTemplate, PROTECT.
- `device_template_id` — optional vendor-side identifier.
- `deployment_status` — pending, queued, synced, failed, removed.
- `attempt_count` — PositiveIntegerField.
- `last_attempt_at`, `deployed_at`, `removed_at` — nullable DateTimeField.
- `last_error_code`, `last_error_message` — optional fields.
- `source_key` — CharField idempotency key.

Constraints: unique active `(device_enrollment, biometric_template)`; employee and compatibility must match enrollment/device.

### 29. DeviceSyncState

Common fields: TenantOwned. One-to-one current operational state.

- `device` — O2O -> BiometricDevice, PROTECT.
- `last_vendor_sequence`, `last_vendor_cursor` — optional CharField.
- `last_device_event_at`, `last_message_received_at`, `last_punch_received_at` — nullable DateTimeField.
- `last_success_at`, `last_error_at` — nullable DateTimeField.
- `last_error_code`, `last_error_message` — optional fields.
- `consecutive_error_count`, `estimated_backlog_count` — PositiveIntegerField.
- `clock_offset_seconds` — nullable IntegerField.
- `state_data` — validated JSONField for adapter-specific cursors.
- `version` — PositiveBigIntegerField for concurrency control.

Do not treat this mutable row as historical evidence; DeviceMessage and PunchEvent preserve evidence.

### 30. DeviceMessage

Common fields: TenantOwned. Append-only source record.

- `public_id` — UUIDField, unique.
- `device` — FK -> BiometricDevice, PROTECT.
- `branch` — FK -> Branch, PROTECT snapshot at receipt.
- `message_type` — punch_batch, heartbeat, enrollment_result, command_result, device_info, unknown.
- `vendor_message_id`, `vendor_sequence` — nullable CharField.
- `idempotency_key` — nullable CharField; set only from a trustworthy adapter identity.
- `occurred_at_device` — nullable DateTimeField retaining the interpreted device time.
- `received_at` — DateTimeField, indexed.
- `content_type`, `encoding` — CharField.
- `raw_payload_text` — nullable TextField.
- `raw_payload_binary` — nullable BinaryField/private object reference for large payloads.
- `payload_json` — nullable JSONField parsed envelope.
- `payload_hash` — CharField checksum.
- `source_ip`, `request_headers_snapshot` — optional protected diagnostic fields.
- `record_count` — nullable PositiveIntegerField.
- `processing_status` — received, parsing, parsed, partially_failed, failed.
- `processing_started_at`, `processed_at` — nullable DateTimeField.
- `processing_attempts` — PositiveIntegerField.
- `processing_error` — TextField.

Constraints: one raw representation must exist; reliable `(device, idempotency_key)` unique when non-null. A payload hash alone is not safe proof that two punches are identical.

### 31. PunchEvent

Common fields: TenantOwned. Append-only source facts; mapping/processing fields may advance without changing original values.

- `device_message` — FK -> DeviceMessage, PROTECT.
- `device`, `branch` — FK -> BiometricDevice/Branch, PROTECT snapshots.
- `device_enrollment` — nullable FK -> DeviceEnrollment, PROTECT.
- `employee` — nullable FK -> Employee, PROTECT.
- `device_user_id` — CharField received from device.
- `vendor_punch_id`, `vendor_sequence` — nullable CharField.
- `source_record_index` — PositiveIntegerField within the message.
- `punched_at_device_raw` — CharField preserving original representation.
- `punched_at_device`, `punched_at_utc` — parsed DateTimeField.
- `device_timezone`, `utc_offset_minutes` — snapshot fields.
- `received_at` — DateTimeField.
- `verification_method` — fingerprint, face, card, PIN, unknown.
- `reported_direction` — nullable vendor value; informational because it is not trusted.
- `reported_status_code` — nullable CharField.
- `raw_record` — JSONField/text snapshot of the individual source record.
- `authorization_status` — authorized, unauthorized_device, unknown_employee, expired_enrollment, department_mismatch, branch_mismatch, enrollment_disabled, policy_unresolved.
- `authorization_snapshot` — JSONField containing the effective scope, winning policy level, effective evaluation time, historical enablement/grant results, and decision reason. These are frozen evaluation facts, not authoritative live relationships. Update resolution metadata only with an audited revision; never rewrite raw source values.
- `dedupe_status` — unique, probable_duplicate, confirmed_duplicate.
- `duplicate_of` — nullable FK -> self, PROTECT.
- `processing_status` — pending, allocated, excluded, needs_review, failed.
- `processing_error` — TextField.
- `resolved_at`, `processed_at` — nullable DateTimeField.

Constraints: unique `(device_message, source_record_index)`; reliable vendor punch identity unique per device when present. Timestamp is never the sole unique key.

## 8. attendance

### 32. PunchAllocation

Common fields: TenantOwned.

- `attendance_record` — FK -> AttendanceRecord, PROTECT.
- `punch_event` — nullable FK -> PunchEvent, PROTECT.
- `attendance_correction` — nullable FK -> AttendanceCorrection, PROTECT for approved manual input.
- `sequence_number` — PositiveIntegerField.
- `event_at` — DateTimeField used by calculation; copied from punch or approved correction.
- `interpreted_direction` — in, out, ignored, unresolved.
- `is_included` — BooleanField.
- `exclusion_reason` — duplicate, unauthorized_device, outside_window, superseded, manual_exclusion, other.
- `confidence` — nullable DecimalField.
- `calculation_version` — PositiveIntegerField.
- `interpretation_note` — TextField.

Constraints: exactly one source (PunchEvent or approved AttendanceCorrection); unique source per calculation version; sequence unique within attendance/version. Merge all authorized devices into one employee/shift stream ordered by event time; never restart alternating IN/OUT at each device or incoming batch.

### 33. AttendanceSession

Common fields: TenantOwned.

- `attendance_record` — FK -> AttendanceRecord, PROTECT.
- `sequence_number` — PositiveIntegerField.
- `in_allocation` — nullable O2O/FK -> PunchAllocation, PROTECT.
- `out_allocation` — nullable O2O/FK -> PunchAllocation, PROTECT.
- `started_at`, `ended_at` — nullable DateTimeField.
- `worked_minutes` — PositiveIntegerField.
- `status` — complete, missing_in, missing_out, invalid.
- `is_manual` — BooleanField.
- `calculation_version` — PositiveIntegerField.

Constraints: end after start; allocation directions correct; sequence unique per record/version. Both allocations belong to this attendance record and employee/company, but may originate from different devices. Never require matching device IDs.

### 34. AttendanceRecord

Common fields: TenantOwned.

- `employee` — FK -> Employee, PROTECT.
- `employee_assignment` — FK -> EmployeeAssignment, PROTECT.
- `branch`, `department` — FK -> Branch/Department, PROTECT historical snapshots.
- `work_date` — DateField.
- `shift` — FK -> Shift, PROTECT.
- `shift_snapshot`, `attendance_settings_snapshot` — JSONField.
- `scheduled_start_at`, `scheduled_end_at` — DateTimeField.
- `first_in_at`, `last_out_at` — nullable DateTimeField.
- `worked_minutes`, `break_minutes`, `outside_minutes` — PositiveIntegerField.
- `late_minutes`, `early_out_minutes` — PositiveIntegerField.
- `calculated_overtime_minutes`, `approved_overtime_minutes` — PositiveIntegerField.
- `attendance_status` — present, absent, leave, holiday, weekly_off, incomplete, excused.
- `punch_status` — complete, missing_in, missing_out, no_punch, ambiguous.
- `calculation_version` — PositiveIntegerField.
- `source_revision_hash` — CharField.
- `calculated_at` — DateTimeField.
- `review_status` — clean, needs_review, reviewed.
- `reviewed_by`, `reviewed_at` — nullable FK -> User/DateTimeField.
- `is_payroll_locked` — BooleanField summary; authoritative lock belongs to finalized payroll.

Constraint: unique `(company, employee, work_date)`; assignment applies on work date and department/branch match it.

The record's branch/department describe the employee's historical assignment. Each PunchEvent retains its source device branch separately, including company_devices punches at another branch. A different source device/branch alone never creates a second daily record or changes the employee's payroll assignment.

### 35. AttendanceCorrection

Common fields: TenantOwned plus actor tracking.

- `attendance_record` — FK -> AttendanceRecord, PROTECT.
- `requested_by_user` — nullable FK -> User, SET_NULL.
- `requested_for_employee` — FK -> Employee, PROTECT.
- `correction_type` — add_punch, replace_punch, remove_from_calculation, change_work_date, adjust_overtime, other.
- `target_punch_event` — nullable FK -> PunchEvent, PROTECT.
- `proposed_event_at`, `proposed_direction` — nullable fields for structured manual input.
- `proposed_overtime_minutes` — nullable PositiveIntegerField.
- `proposed_sessions` — validated JSONField for multi-session corrections.
- `reason` — TextField.
- `attachment` — optional private FileField.
- `status` — draft, submitted, approved, rejected, withdrawn, applied.
- `approved_by`, `approved_at` — nullable FK -> User/DateTimeField.
- `decision_note` — TextField.
- `before_snapshot`, `after_snapshot` — JSONField.
- `applied_at`, `resulting_calculation_version` — nullable fields.

Approved correction creates/supersedes PunchAllocation inputs and triggers recalculation; it never edits PunchEvent.

### 36. AttendancePenaltyRule

Common fields: TenantOwned plus actor tracking.

- `name`, `code` — CharField.
- `metric` — late_minutes, early_out_minutes, worked_shortfall, outside_minutes, absence.
- `operator` — gte, gt, lte, lt, equal.
- `threshold_minutes` — nullable PositiveIntegerField.
- `required_occurrences` — PositiveIntegerField.
- `occurrence_mode` — single_day, consecutive_workdays, within_period, rolling_window.
- `rolling_window_days` — nullable PositiveIntegerField.
- `sequence_break_policy` — validated JSONField describing leave/holiday/absence behavior.
- `deduction_method` — actual_minutes, fixed_minutes, day_fraction, full_day, fixed_amount.
- `deduction_value` — DecimalField.
- `priority` — IntegerField.
- `exclusive_group` — nullable CharField.
- `stacking_policy` — highest_only, additive, capped.
- `maximum_deduction` — nullable DecimalField.
- `effective_from`, `effective_to` — DateField/null.
- `status` — draft, active, retired.
- `version` — PositiveIntegerField.

Constraint: rule inputs consistent with metric/method. Activated rule versions are immutable.

### 37. PenaltyAssessment

Common fields: TenantOwned.

- `employee` — FK -> Employee, PROTECT.
- `penalty_rule` — FK -> AttendancePenaltyRule, PROTECT.
- `payroll_period` — nullable FK -> PayrollPeriod, PROTECT.
- `period_start`, `period_end` — DateField.
- `occurrence_identity` — CharField stable idempotency key.
- `occurrence_count` — PositiveIntegerField.
- `deduction_minutes` — PositiveIntegerField.
- `deduction_day_fraction`, `deduction_amount` — DecimalField.
- `currency` — CharField(3).
- `calculation_details` — JSONField snapshot.
- `status` — proposed, approved, waived, posted, reversed.
- `approved_by`, `approved_at` — nullable FK -> User/DateTimeField.
- `reversal_of` — nullable FK -> self, PROTECT.
- `calculated_at` — DateTimeField.

Constraint: unique occurrence identity per company; one original payroll posting.

### 38. PenaltyAssessmentAttendance

Common fields: TenantOwned.

- `penalty_assessment` — FK -> PenaltyAssessment, PROTECT.
- `attendance_record` — FK -> AttendanceRecord, PROTECT.
- `sequence_number` — PositiveIntegerField.
- `qualifying_value` — DecimalField.
- `reason_snapshot` — JSONField.

Constraint: unique `(penalty_assessment, attendance_record)`. This is the explicit M2M through table.
## 9. leaves

Leave models support basic operation through defaults. Advanced fields remain unused until the company enables policies such as accrual, balances, encashment, compensatory leave, or LFA.

### 39. LeaveType

Common fields: TenantOwned plus actor tracking.

- `code`, `name` — CharField.
- `description` — TextField.
- `days_per_year` — optional DecimalField(5,1), minimum 0.5; blank = no limit.
  Built 2026-09-15 (A10c, simple): approved leave days of the type in a calendar
  year count against it; no accrual or carry-forward.
- `default_balance_unit` — days or minutes.
- `requires_attachment_by_default` — BooleanField.
- `color` — optional CharField for calendar display.
- `status` — active/inactive.

Constraint: unique `(company, code)`.

### 40. LeavePolicy

Common fields: TenantOwned plus actor tracking.

- `code`, `name`, `description` — text fields.
- `branch` — nullable FK -> Branch, PROTECT.
- `department` — nullable FK -> Department, PROTECT.
- `is_company_default` — BooleanField.
- `status` — draft, active, retired.
- `current_version` — nullable FK -> LeavePolicyVersion, PROTECT, set after version creation.

Constraints: department requires matching branch; only one applicable default per scope. Avoid a migration cycle by adding current_version after the initial version table or treating it as a cached pointer.

### 41. LeavePolicyVersion

Common fields: TenantOwned plus actor tracking.

- `leave_policy` — FK -> LeavePolicy, PROTECT.
- `version_number` — PositiveIntegerField.
- `effective_from`, `effective_to` — DateField/null.
- `leave_year_start_month`, `leave_year_start_day` — PositiveSmallIntegerField.
- `eligibility_defaults` — validated JSONField.
- `approval_route_definition` — validated JSONField referencing roles/designation rules by stable codes; actual approvers are materialized later.
- `lfa_enabled` — BooleanField, default false.
- `lfa_configuration` — validated JSONField; FK values such as SalaryComponent should be explicit nullable FKs where required.
- `lfa_salary_component` — nullable FK -> SalaryComponent, PROTECT.
- `status` — draft, active, retired.
- `activated_at`, `activated_by` — nullable DateTime/FK -> User.

Constraints: unique `(leave_policy, version_number)`; non-overlapping active effective periods. Freeze after use.

### 42. LeavePolicyTypeRule

Common fields: TenantOwned plus actor tracking.

- `policy_version` — FK -> LeavePolicyVersion, PROTECT.
- `leave_type` — FK -> LeaveType, PROTECT.
- `balance_unit` — days or minutes.
- `annual_allowance` — DecimalField.
- `grant_method` — upfront, periodic_accrual, manual, unlimited.
- `accrual_frequency` — monthly, quarterly, yearly, none.
- `accrual_amount` — DecimalField.
- `joining_proration_method` — none, calendar_days, remaining_months, company_rule.
- `minimum_service_days`, `probation_wait_days` — PositiveIntegerField.
- `minimum_request_units`, `maximum_request_units` — nullable DecimalField.
- `maximum_consecutive_units` — nullable DecimalField.
- `minimum_notice_days` — PositiveIntegerField.
- `allow_negative_balance` — BooleanField.
- `negative_balance_limit` — DecimalField.
- `pay_type_default` — paid, unpaid, partial, manager_decides.
- `pay_percentage_default` — DecimalField 0–100.
- `approval_may_change_pay_percentage` — BooleanField.
- `count_weekly_offs`, `count_holidays` — BooleanField.
- `attachment_required_after_units` — nullable DecimalField.
- `carry_forward_enabled` — BooleanField.
- `carry_forward_limit`, `carry_expiry_days` — nullable Decimal/Integer.
- `encashment_enabled` — BooleanField.
- `encashment_limit` — nullable DecimalField.
- `encashment_rate_basis` — daily_base, fixed_rate, configured_formula.
- `compensatory_credit_enabled` — BooleanField.
- `expires_compensatory_after_days` — nullable PositiveIntegerField.
- `status` — active/inactive.

Constraint: unique `(policy_version, leave_type)`; percentages and limits valid.

### 43. EmployeeLeavePolicyAssignment

Common fields: TenantOwned plus actor tracking.

- `employee` — FK -> Employee, PROTECT.
- `leave_policy` — FK -> LeavePolicy, PROTECT.
- `effective_from` — DateField.
- `effective_to` — nullable DateField.
- `reason` — TextField.
- `status` — active/ended/cancelled.

Constraint: no overlapping explicit employee policy assignments.

### 44. LeaveEntitlement

Common fields: TenantOwned.

- `employee` — FK -> Employee, PROTECT.
- `leave_type` — FK -> LeaveType, PROTECT.
- `policy_version` — FK -> LeavePolicyVersion, PROTECT.
- `period_start`, `period_end` — DateField.
- `balance_unit` — days/minutes.
- `negative_balance_limit` — DecimalField.
- `status` — open, closed, cancelled.
- `closed_at`, `closed_by` — nullable DateTime/FK -> User.
- `version` — PositiveBigIntegerField for balance concurrency.

Calculated properties, not authoritative fields: credited, used, reserved, expired, encashed, available.

Constraint: unique entitlement for employee/type/period/policy context; overlapping accounts for the same type require explicit migration/reconciliation.

### 45. LeaveBalanceEntry

Common fields: TenantOwned. Append-only ledger.

- `entitlement` — FK -> LeaveEntitlement, PROTECT.
- `entry_kind` — opening, accrual, manual_credit, manual_debit, reservation, reservation_release, usage, cancellation_reversal, expiry, carry_out, carry_in, encashment, compensatory_credit, reversal.
- `balance_delta`, `reservation_delta` — signed DecimalField.
- `effective_at` — DateTimeField.
- `expires_at` — nullable DateTimeField.
- `source_grant` — nullable FK -> self, PROTECT for grant consumption.
- `source_idempotency_key` — CharField.
- `leave_request` — nullable FK -> LeaveRequest, PROTECT.
- `leave_day` — nullable FK -> LeaveDay, PROTECT.
- `leave_encashment` — nullable FK -> LeaveEncashment, PROTECT.
- `compensatory_credit` — nullable FK -> LeaveCompensatoryCredit, PROTECT.
- `reversal_of` — nullable O2O/FK -> self, PROTECT.
- `reason` — TextField.
- `posted_by` — nullable FK -> User, SET_NULL.

Constraints: nonzero balance or reservation delta; unique source key; one reversal per entry; source fields consistent with kind.

### 46. LeaveRequest

Common fields: TenantOwned plus actor tracking.

- `public_id` — UUIDField, unique.
- `employee` — FK -> Employee, PROTECT.
- `submission_assignment` — FK -> EmployeeAssignment, PROTECT.
- `policy_version` — FK -> LeavePolicyVersion, PROTECT.
- `action_type` — new, amend, cancel.
- `original_request` — nullable FK -> self, PROTECT.
- `supersedes` — nullable FK -> self, PROTECT.
- `reason` — TextField.
- `status` — draft, submitted, pending, approved, rejected, withdrawn, cancelled, partially_cancelled.
- `submitted_at` — nullable DateTimeField.
- `submitted_by` — nullable FK -> User, SET_NULL; HR may submit for an employee.
- `decision_snapshot` — JSONField.
- `current_approval_stage` — nullable PositiveIntegerField.
- `decided_at` — nullable DateTimeField.

Constraints: amendment/cancellation needs original request with same company/employee; approved request requires segments and completed approvals.

### 47. LeaveRequestSegment

Common fields: TenantOwned.

- `leave_request` — FK -> LeaveRequest, PROTECT.
- `leave_type` — FK -> LeaveType, PROTECT.
- `duration_type` — full_day, half_day, hourly.
- `start_date`, `end_date` — DateField.
- `half_day_part` — nullable first_half/second_half.
- `start_time`, `end_time` — nullable TimeField.
- `timezone` — CharField snapshot.
- `requested_units`, `requested_minutes` — Decimal/PositiveInteger snapshot.
- `requested_pay_type` — paid, unpaid, partial, manager_decides.
- `requested_pay_percentage` — nullable DecimalField 0–100.
- `original_segment` — nullable FK -> self, PROTECT.
- `sequence_number` — PositiveIntegerField.
- `status` — active, superseded, cancelled.

Constraints: duration-specific fields required; end not before start; no overlap with active segments for employee after expanding intervals.

### 48. LeaveDay

Common fields: TenantOwned. Approved daily allocation.

- `request_segment` — FK -> LeaveRequestSegment, PROTECT.
- `employee` — FK -> Employee, PROTECT.
- `employee_assignment` — FK -> EmployeeAssignment, PROTECT.
- `policy_type_rule` — FK -> LeavePolicyTypeRule, PROTECT.
- `entitlement` — nullable FK -> LeaveEntitlement, PROTECT for tracked balances.
- `work_date` — DateField.
- `covered_start_at`, `covered_end_at` — DateTimeField.
- `scheduled_minutes_snapshot` — PositiveIntegerField.
- `leave_minutes` — PositiveIntegerField.
- `balance_units` — DecimalField.
- `approved_pay_type` — paid, unpaid, partial.
- `approved_pay_percentage` — DecimalField 0–100.
- `calendar_snapshot`, `shift_snapshot` — JSONField.
- `status` — reserved, approved, consumed, cancelled, reversed.
- `supersedes` — nullable FK -> self, PROTECT.
- `consumed_at` — nullable DateTimeField.

Constraints: covered interval positive and within relevant work date/shift; active approved intervals do not overlap; pay type matches percentage.

### 49. LeaveApprovalStep

Common fields: TenantOwned.

- `leave_request` — nullable FK -> LeaveRequest, PROTECT.
- `leave_encashment` — nullable FK -> LeaveEncashment, PROTECT.
- `compensatory_credit` — nullable FK -> LeaveCompensatoryCredit, PROTECT.
- `lfa_claim` — nullable FK -> LeaveFareAssistanceClaim, PROTECT.
- `stage_number` — PositiveIntegerField.
- `stage_name` — CharField.
- `approver_membership` — FK -> CompanyMembership, PROTECT.
- `delegated_from_membership` — nullable FK -> CompanyMembership, PROTECT.
- `status` — pending, approved, rejected, skipped, cancelled.
- `decision` — nullable approve/reject.
- `comment` — TextField.
- `decided_at` — nullable DateTimeField.
- `route_snapshot` — JSONField.

Constraints: exactly one target FK; unique target/stage/approver; decision fields required when decided.

### 50. LeaveAttachment

Common fields: TenantOwned.

- `leave_request` — nullable FK -> LeaveRequest, PROTECT.
- `lfa_claim` — nullable FK -> LeaveFareAssistanceClaim, PROTECT.
- `file` — private FileField/storage key.
- `original_filename`, `content_type` — CharField.
- `size_bytes` — PositiveBigIntegerField.
- `checksum` — CharField.
- `document_type`, `description` — optional CharField/TextField.
- `uploaded_by` — nullable FK -> User, SET_NULL.
- `uploaded_at` — DateTimeField.
- `status` — active, replaced, removed.

Constraint: exactly one target.

### 51. LeaveEncashment

Common fields: TenantOwned plus actor tracking.

- `employee` — FK -> Employee, PROTECT.
- `entitlement` — FK -> LeaveEntitlement, PROTECT.
- `policy_version` — FK -> LeavePolicyVersion, PROTECT.
- `requested_units`, `approved_units` — DecimalField.
- `conversion_rate_snapshot` — DecimalField money per unit.
- `calculated_amount`, `approved_amount` — DecimalField money.
- `currency` — CharField(3).
- `target_payroll_period` — nullable FK -> PayrollPeriod, PROTECT.
- `reason` — TextField.
- `status` — draft, submitted, approved, rejected, posted, cancelled, reversed.
- `approved_by`, `approved_at` — nullable FK -> User/DateTimeField.
- `source_key` — CharField idempotency key.
- `reversal_of` — nullable FK -> self, PROTECT.

Constraint: approved units cannot exceed eligible available balance. Resulting payroll lines point back to this row, so reversals and off-cycle corrections remain possible.

### 52. LeaveCompensatoryCredit

Common fields: TenantOwned plus actor tracking.

- `employee` — FK -> Employee, PROTECT.
- `source_attendance` — FK -> AttendanceRecord, PROTECT.
- `entitlement` — FK -> LeaveEntitlement, PROTECT.
- `policy_type_rule` — FK -> LeavePolicyTypeRule, PROTECT.
- `source_work_minutes` — PositiveIntegerField.
- `approved_credit_units` — DecimalField.
- `expires_at` — nullable DateTimeField.
- `treatment` — comp_leave_only, overtime_only, both_allowed.
- `status` — draft, submitted, approved, credited, rejected, cancelled, reversed.
- `approved_by`, `approved_at` — nullable FK -> User/DateTimeField.
- `balance_entry` — nullable O2O -> LeaveBalanceEntry, PROTECT.
- `source_key` — CharField.

Constraints: unique qualifying source/treatment; do not award overtime and leave together unless treatment explicitly permits it.

### 53. LeaveFareAssistanceClaim

Common fields: TenantOwned plus actor tracking.

- `employee` — FK -> Employee, PROTECT.
- `employee_assignment` — FK -> EmployeeAssignment, PROTECT snapshot.
- `policy_version` — FK -> LeavePolicyVersion, PROTECT.
- `qualifying_leave_request` — nullable FK -> LeaveRequest, PROTECT.
- `compensation` — nullable FK -> EmployeeCompensation, PROTECT.
- `benefit_date` — DateField.
- `cycle_start`, `cycle_end` — DateField.
- `occurrence_number` — PositiveIntegerField.
- `amount_method` — fixed, compensation_percentage, compensation_multiple, manager_entered.
- `amount_basis_snapshot` — JSONField.
- `base_amount_snapshot`, `calculated_amount`, `approved_amount` — DecimalField money.
- `currency` — CharField(3).
- `target_payroll_period` — nullable FK -> PayrollPeriod, PROTECT.
- `eligibility_snapshot` — JSONField.
- `reason`, `decision_reason` — TextField.
- `status` — draft, submitted, approved, rejected, withdrawn, cancelled, posted, reversed.
- `submitted_at`, `approved_at`, `posted_at` — nullable DateTimeField.
- `approved_by` — nullable FK -> User, PROTECT.
- `idempotency_key` — CharField.
- `reversal_of` — nullable FK -> self, PROTECT.

Constraints: same-company qualifying leave; policy eligibility; unique active occurrence slot per employee/benefit cycle. Resulting PayrollLine rows point back to the claim. LFA never changes LeaveEntitlement.

## 10. payroll

Payroll is intentionally layered: settings and versioned policy define the rules; compensation and salary structures define what an employee earns; periods and runs control generation; records, segments, daily lines, and component lines preserve the calculation; payments and allocations preserve settlement. Advances and loans are agreements, not negative salary records.

### 54. PayrollSettings

One current operational-settings row per company. Historical calculations must reference PayrollPolicyVersion rather than depending on this mutable row.

Common fields: TenantOwned plus actor tracking.

- `currency` — CharField(3), default company currency.
- `pay_frequency` — monthly initially; reserved choices may include weekly, biweekly, semimonthly.
- `period_start_day` — PositiveSmallIntegerField, normally 1.
- `default_pay_day` — nullable PositiveSmallIntegerField.
- `default_policy_version` — nullable FK -> PayrollPolicyVersion, PROTECT.
- `default_salary_structure` — nullable FK -> SalaryStructure, PROTECT.
- `monthly_divisor` — DecimalField, default 30 for the agreed initial deduction basis.
- `overtime_enabled`, `bonus_enabled`, `advances_enabled`, `loans_enabled` — BooleanField.
- `auto_include_approved_overtime` — BooleanField.
- `auto_include_approved_leave` — BooleanField, normally true.
- `require_payroll_approval` — BooleanField.
- `allow_negative_net_pay` — BooleanField, normally false.
- `default_payment_method` — nullable CharField.
- `settings_version` — PositiveIntegerField for optimistic concurrency/display.

Constraint: unique `company` (implement as O2O semantics).

### 55. PayrollPolicyVersion

An immutable, effective-dated snapshot of payroll calculation rules. Create a new version instead of editing an activated version.

Common fields: TenantOwned plus actor tracking.

- `name`, `code` — CharField.
- `version_number` — PositiveIntegerField.
- `effective_from`, `effective_to` — DateField range.
- `monthly_divisor` — DecimalField, default 30.
- `monthly_proration_method` — fixed_30, calendar_days, scheduled_workdays, none.
- `daily_rate_method` — explicit_rate, monthly_divisor, scheduled_workdays.
- `hourly_rate_method` — explicit_rate, daily_scheduled_hours, monthly_standard_hours.
- `joining_leaving_proration_method` — CharField controlled choice.
- `absence_deduction_method` — day_fraction, scheduled_minutes, rule_only.
- `paid_leave_treatment`, `partial_leave_treatment`, `unpaid_leave_treatment` — controlled choices/DecimalField defaults.
- `overtime_method` — none, hourly_rate, fixed_rate, multiplier.
- `overtime_multiplier`, `holiday_overtime_multiplier` — DecimalField.
- `overtime_rounding_minutes`, `minimum_overtime_minutes` — PositiveIntegerField.
- `require_overtime_approval` — BooleanField.
- `late_penalty_stacking_method` — highest_only, cumulative, capped.
- `maximum_period_deduction_percent` — nullable DecimalField.
- `maximum_recovery_percent_of_net` — nullable DecimalField.
- `money_rounding_mode`, `money_rounding_increment` — CharField/DecimalField.
- `allow_negative_net_pay` — BooleanField.
- `calculation_config` — JSONField for validated non-relational formula parameters.
- `status` — draft, active, retired.
- `activated_by`, `activated_at` — nullable FK -> User/DateTimeField.

Constraints: unique `(company, code, version_number)`; no overlapping active date ranges for the same code; activated versions are immutable.

### 56. SalaryComponent

A tenant-defined earning, deduction, reimbursement, or employer contribution such as Basic Salary, Overtime, Bonus, LFA, Absence Deduction, or Loan Recovery.

Common fields: TenantOwned plus actor tracking.

- `code`, `name` — CharField.
- `category` — earning, deduction, reimbursement, employer_contribution.
- `subtype` — base_salary, overtime, bonus, allowance, absence, penalty, advance_recovery, loan_recovery, leave_encashment, lfa, tax, other.
- `calculation_method` — fixed, quantity_x_rate, percentage, formula, source_amount.
- `default_amount`, `default_rate`, `default_percentage` — nullable DecimalField.
- `formula_expression` — blank TextField; use a restricted expression language, never Python eval.
- `taxable`, `affects_gross`, `affects_net_pay`, `recurring` — BooleanField.
- `proratable`, `include_in_overtime_base` — BooleanField.
- `display_order` — PositiveIntegerField.
- `external_code` — optional CharField for accounting/payment integration.
- `is_active` — BooleanField.

Constraints: unique `(company, code)`; category and sign behavior must agree.

### 57. SalaryStructure

A reusable group of salary components assigned to employees. It permits a simple one-component salary initially and more components later.

Common fields: TenantOwned plus actor tracking.

- `code`, `name` — CharField.
- `description` — TextField, blank.
- `version_number` — PositiveIntegerField.
- `effective_from`, `effective_to` — nullable DateField range.
- `currency` — CharField(3).
- `status` — draft, active, retired.
- `is_default` — BooleanField.
- `activated_by`, `activated_at` — nullable FK -> User/DateTimeField.

Relations: components are M2M -> SalaryComponent through SalaryStructureComponent.

Constraints: unique `(company, code, version_number)`; at most one active default structure for a company/date; activated structures are immutable except retirement metadata.

### 58. SalaryStructureComponent

The named through row configuring one component inside one salary structure.

Common fields: TenantOwned plus actor tracking.

- `salary_structure` — FK -> SalaryStructure, CASCADE.
- `salary_component` — FK -> SalaryComponent, PROTECT.
- `calculation_method` — fixed, employee_compensation, quantity_x_rate, percentage, formula, source_amount.
- `fixed_amount`, `rate`, `percentage` — nullable DecimalField.
- `percentage_base_component` — nullable FK -> SalaryComponent, PROTECT.
- `formula_expression` — blank TextField.
- `minimum_amount`, `maximum_amount` — nullable DecimalField.
- `proration_method` — inherit_policy, none, day_fraction, scheduled_minutes.
- `rounding_mode`, `rounding_increment` — nullable fields.
- `display_order` — PositiveIntegerField.
- `is_mandatory`, `is_active` — BooleanField.

Constraints: unique `(salary_structure, salary_component)`; both belong to the same company; required operands depend on calculation method.

### 59. EmployeeSalaryStructureAssignment

Effective-dated assignment of a salary structure to an employee. It preserves which structure applied before and after a change.

Common fields: TenantOwned plus actor tracking.

- `employee` — FK -> Employee, PROTECT.
- `salary_structure` — FK -> SalaryStructure, PROTECT.
- `effective_from`, `effective_to` — DateField range.
- `reason` — TextField, blank.
- `status` — scheduled, active, ended, cancelled.
- `approved_by`, `approved_at` — nullable FK -> User/DateTimeField.

Constraints: no overlapping active assignments for an employee; employee and structure belong to the same company.

### 60. PayrollPeriod

The company pay window, such as September 2026. It is a control period, not an employee result.

Common fields: TenantOwned plus actor tracking.

- `code`, `name` — CharField.
- `frequency` — monthly initially.
- `period_start`, `period_end` — DateField, inclusive business dates.
- `attendance_cutoff_at`, `adjustment_cutoff_at` — nullable DateTimeField.
- `scheduled_pay_date` — nullable DateField.
- `currency` — CharField(3).
- `status` — draft, open, processing, closed, locked, cancelled.
- `opened_by`, `opened_at`, `closed_by`, `closed_at` — nullable FK -> User/DateTimeField.
- `lock_reason` — TextField, blank.

Constraints: unique `(company, code)`; `period_start <= period_end`; regular periods of the same frequency should not overlap.

### 61. PayrollRun

One attempt/version of payroll generation for a period. Regular, correction, final-settlement, and off-cycle runs remain distinguishable and idempotent.

Common fields: TenantOwned plus actor tracking.

- `payroll_period` — FK -> PayrollPeriod, PROTECT.
- `run_type` — regular, correction, off_cycle, final_settlement, reversal.
- `revision_number` — PositiveIntegerField.
- `source_run` — nullable FK -> self, PROTECT for correction/reversal.
- `policy_version` — FK -> PayrollPolicyVersion, PROTECT.
- `salary_structure_snapshot` — JSONField, optional run-level metadata only.
- `attendance_cutoff_at`, `leave_cutoff_at`, `adjustment_cutoff_at` — DateTimeField.
- `idempotency_key` — CharField.
- `status` — draft, queued, calculating, review, approved, posted, failed, cancelled, reversed.
- `calculation_started_at`, `calculation_finished_at` — nullable DateTimeField.
- `generated_by`, `approved_by`, `posted_by` — nullable FK -> User, SET_NULL/PROTECT according to audit policy.
- `approved_at`, `posted_at` — nullable DateTimeField.
- `failure_code`, `failure_message` — blank CharField/TextField.
- `totals_snapshot` — JSONField.

Constraints: unique `(company, idempotency_key)` and `(payroll_period, run_type, revision_number)`; only one posted regular result chain may be authoritative for an employee/period.

### 62. PayrollRecord

The employee-level payroll result header inside one run. It contains totals; PayrollLine contains the explanation.

Common fields: TenantOwned plus actor tracking.

- `payroll_run` — FK -> PayrollRun, PROTECT.
- `employee` — FK -> Employee, PROTECT.
- `employee_assignment_at_period_end` — nullable FK -> EmployeeAssignment, PROTECT.
- `currency` — CharField(3).
- `gross_earnings`, `total_earnings`, `total_deductions` — DecimalField money.
- `employer_contributions`, `reimbursements`, `net_pay` — DecimalField money.
- `paid_amount`, `outstanding_amount` — DecimalField cached summaries; authoritative values come from payment allocations.
- `calculation_snapshot` — JSONField containing frozen inputs/totals.
- `status` — draft, calculated, review, approved, posted, partially_paid, paid, void, reversed.
- `approved_by`, `approved_at` — nullable FK -> User/DateTimeField.
- `posted_at` — nullable DateTimeField.
- `reversal_of` — nullable O2O -> self, PROTECT.

Constraints: unique `(payroll_run, employee)`; employee belongs to run company; totals equal active line sums and allocations cannot exceed payable balance.

### 63. PayrollCompensationSegment

An effective-dated portion of one employee's payroll result. It preserves department, designation, salary basis, rate, and structure when any of them changes during the period.

Common fields: TenantOwned plus actor tracking.

- `payroll_record` — FK -> PayrollRecord, CASCADE.
- `employee_assignment` — FK -> EmployeeAssignment, PROTECT.
- `employee_compensation` — FK -> EmployeeCompensation, PROTECT.
- `salary_structure_assignment` — nullable FK -> EmployeeSalaryStructureAssignment, PROTECT.
- `policy_version` — FK -> PayrollPolicyVersion, PROTECT.
- `segment_start`, `segment_end` — DateField, inclusive dates within the payroll period.
- `branch`, `department`, `designation` — FK -> Branch/Department/Designation, PROTECT snapshot relations.
- `pay_basis` — monthly, daily, hourly.
- `base_rate`, `daily_rate`, `hourly_rate` — nullable DecimalField snapshots.
- `currency` — CharField(3).
- `monthly_divisor`, `scheduled_minutes_per_day`, `standard_minutes_in_period` — DecimalField/integer snapshots.
- `eligible_units`, `payable_units` — DecimalField.
- `base_earning_amount`, `deduction_amount`, `net_segment_amount` — DecimalField money.
- `calculation_details` — JSONField.

Constraints: segment dates must fall inside the period, cannot overlap for the same PayrollRecord, and referenced history rows must cover the segment.

### 64. PayrollDailyLine

A date-level calculation explanation within a compensation segment: work, leave, holiday, absence, late penalties, payable time, and the day's base earning/deduction.

Common fields: TenantOwned plus actor tracking.

- `payroll_segment` — FK -> PayrollCompensationSegment, CASCADE.
- `work_date` — DateField.
- `attendance_record` — nullable FK -> AttendanceRecord, PROTECT.
- `leave_days` — M2M -> LeaveDay, blank, related evidence for overlapping hourly/partial leaves.
- `holiday` — nullable FK -> Holiday, PROTECT.
- `weekly_off_rule` — nullable FK -> WeeklyOffRule, PROTECT.
- `holiday_work_assignment` — nullable FK -> HolidayWorkAssignment, PROTECT.
- `day_type` — workday, weekly_off, holiday, approved_holiday_work, non_employment_day.
- `attendance_status` — present, absent, leave, mixed, incomplete, not_applicable.
- `scheduled_minutes`, `worked_minutes`, `approved_leave_minutes`, `payable_minutes` — PositiveIntegerField.
- `absence_minutes`, `late_minutes`, `early_out_minutes`, `overtime_minutes` — PositiveIntegerField.
- `paid_day_fraction`, `unpaid_day_fraction`, `payable_day_fraction` — DecimalField.
- `base_earning_amount`, `attendance_deduction_amount`, `penalty_deduction_amount`, `overtime_amount` — DecimalField money.
- `rate_snapshot`, `calculation_details` — JSONField.
- `status` — calculated, reviewed, overridden, reversed.

Constraints: unique `(payroll_segment, work_date)`; all evidence belongs to the employee/company/date; fractions are nonnegative and bounded where applicable.

### 65. PayrollLine

The authoritative itemized earning or deduction. Each line identifies its salary component and, when applicable, the business record that caused it.

Common fields: TenantOwned plus actor tracking.

- `payroll_record` — FK -> PayrollRecord, CASCADE.
- `payroll_segment` — nullable FK -> PayrollCompensationSegment, PROTECT.
- `payroll_daily_line` — nullable FK -> PayrollDailyLine, PROTECT.
- `salary_component` — FK -> SalaryComponent, PROTECT.
- `source_type` — base_compensation, attendance, penalty, overtime, bonus, adjustment, advance_recovery, loan_repayment, leave_encashment, lfa, manual, reversal.
- `penalty_assessment` — nullable FK -> PenaltyAssessment, PROTECT.
- `payroll_adjustment` — nullable FK -> PayrollAdjustment, PROTECT.
- `salary_advance_recovery` — nullable FK -> SalaryAdvanceRecovery, PROTECT.
- `loan_repayment` — nullable FK -> LoanRepayment, PROTECT.
- `leave_encashment` — nullable FK -> LeaveEncashment, PROTECT.
- `lfa_claim` — nullable FK -> LeaveFareAssistanceClaim, PROTECT.
- `description` — CharField.
- `service_date`, `service_period_start`, `service_period_end` — nullable DateField.
- `quantity`, `rate`, `percentage` — nullable DecimalField.
- `amount` — nonnegative DecimalField money.
- `direction` — earning, deduction, employer_only.
- `currency` — CharField(3).
- `formula_snapshot`, `calculation_details` — JSONField.
- `taxable`, `is_manual`, `is_system_generated` — BooleanField.
- `original_line` — nullable FK -> self, PROTECT for reversals/corrections.
- `status` — active, void, reversed.

Constraints: source fields must agree with `source_type`; at most one typed source for a normal line; prevent duplicate active posting from the same source/component/run; reversal lines reference an original and use the opposite financial effect.

### 66. PayrollApprovalStep

One stage in payroll review and approval. A simple company can use one step; stricter companies can use several.

Common fields: TenantOwned plus actor tracking.

- `payroll_run` — FK -> PayrollRun, CASCADE.
- `sequence` — PositiveSmallIntegerField.
- `required_permission` — nullable FK -> AccessPermission, PROTECT.
- `assigned_designation` — nullable FK -> Designation, PROTECT.
- `assigned_user` — nullable FK -> User, PROTECT.
- `acted_by` — nullable FK -> User, PROTECT.
- `status` — pending, approved, rejected, skipped, cancelled.
- `decision_note` — TextField, blank.
- `acted_at`, `due_at` — nullable DateTimeField.

Constraints: unique `(payroll_run, sequence)`; assignee must have company access; steps execute in order unless policy explicitly permits parallel stages.

### 67. SalaryPayment

An actual payment transaction to an employee. It is separate from PayrollRecord so partial, combined, failed, refunded, or corrected payments are representable.

Common fields: TenantOwned plus actor tracking.

- `employee` — FK -> Employee, PROTECT.
- `payment_reference` — CharField.
- `payment_date` — DateField.
- `amount` — positive DecimalField money.
- `currency` — CharField(3).
- `payment_method` — cash, bank_transfer, mobile_wallet, cheque, other.
- `provider_name`, `provider_reference` — optional CharField.
- `bank_account_snapshot` — JSONField containing masked settlement details.
- `status` — draft, pending, completed, failed, cancelled, refunded, reversed.
- `processed_by`, `processed_at` — nullable FK -> User/DateTimeField.
- `failure_reason` — TextField, blank.
- `idempotency_key` — CharField.
- `reversal_of` — nullable O2O -> self, PROTECT.

Constraints: unique `(company, payment_reference)` and `(company, idempotency_key)`; allocated amount cannot exceed completed payment amount.

### 68. SalaryPaymentAllocation

Allocates some or all of a completed salary payment to a specific PayrollRecord, enabling partial payments and one payment covering several payroll records.

Common fields: TenantOwned plus actor tracking.

- `salary_payment` — FK -> SalaryPayment, PROTECT.
- `payroll_record` — FK -> PayrollRecord, PROTECT.
- `allocated_amount` — positive DecimalField money.
- `allocated_at` — DateTimeField.
- `allocation_reference` — CharField.
- `status` — active, reversed.
- `reversal_of` — nullable O2O -> self, PROTECT.

Constraints: unique `(company, allocation_reference)`; payment and payroll record belong to the same employee/company/currency; active allocations cannot overallocate either side.

### 69. SalaryAdvance

The approved salary-advance agreement: requested/approved amount and intended recovery plan. It is not itself proof that money was paid.

Common fields: TenantOwned plus actor tracking.

- `employee` — FK -> Employee, PROTECT.
- `request_reference` — CharField.
- `requested_amount`, `approved_amount` — DecimalField money.
- `currency` — CharField(3).
- `requested_on`, `approved_on` — nullable DateField.
- `reason` — TextField.
- `recovery_method` — next_payroll, fixed_installments, manual.
- `installment_count` — nullable PositiveIntegerField.
- `installment_amount` — nullable DecimalField.
- `first_recovery_period` — nullable FK -> PayrollPeriod, PROTECT.
- `maximum_recovery_percent` — nullable DecimalField.
- `status` — draft, submitted, approved, rejected, partially_disbursed, disbursed, recovering, settled, cancelled, written_off.
- `approved_by` — nullable FK -> User, PROTECT.
- `outstanding_amount` — DecimalField cached balance.

Constraints: unique `(company, request_reference)`; approved amount cannot exceed requested amount without explicit override; outstanding derives from disbursements minus applied recoveries/reversals.

### 70. AdvanceDisbursement

Records money actually given under a SalaryAdvance. Several disbursements may fulfill one agreement.

Common fields: TenantOwned plus actor tracking.

- `salary_advance` — FK -> SalaryAdvance, PROTECT.
- `disbursement_reference` — CharField.
- `disbursed_at` — DateTimeField.
- `amount` — positive DecimalField money.
- `currency` — CharField(3).
- `payment_method`, `provider_reference` — CharField.
- `status` — pending, completed, failed, cancelled, reversed.
- `processed_by` — nullable FK -> User, PROTECT.
- `reversal_of` — nullable O2O -> self, PROTECT.

Constraints: unique `(company, disbursement_reference)`; completed active disbursements cannot exceed approved advance amount.

### 71. SalaryAdvanceRecovery

An actual recovery of advance principal, normally posted as a payroll deduction but optionally received outside payroll.

Common fields: TenantOwned plus actor tracking.

- `salary_advance` — FK -> SalaryAdvance, PROTECT.
- `payroll_period` — nullable FK -> PayrollPeriod, PROTECT.
- `recovery_date` — DateField.
- `scheduled_amount`, `recovered_amount` — DecimalField money.
- `currency` — CharField(3).
- `method` — payroll_deduction, cash, bank_transfer, adjustment, write_off.
- `external_reference` — optional CharField.
- `status` — scheduled, posted, received, skipped, cancelled, reversed.
- `idempotency_key` — CharField.
- `reversal_of` — nullable O2O -> self, PROTECT.

Relations: PayrollLine points to this row when method is payroll_deduction.

Constraints: unique `(company, idempotency_key)`; posted/received recoveries cannot exceed outstanding principal unless explicitly treated as a refundable overpayment.

### 72. EmployeeLoan

The employee loan agreement, preserving principal, terms, approval, and balance separately from payroll deductions.

Common fields: TenantOwned plus actor tracking.

- `employee` — FK -> Employee, PROTECT.
- `loan_reference` — CharField.
- `loan_type` — salary_loan, emergency, welfare, other.
- `requested_principal`, `approved_principal` — DecimalField money.
- `currency` — CharField(3).
- `interest_method` — none, flat, declining_balance.
- `annual_interest_rate`, `flat_interest_amount` — nullable DecimalField.
- `term_installments` — PositiveIntegerField.
- `installment_frequency` — payroll_period, monthly, manual.
- `first_due_date` — DateField.
- `grace_periods` — PositiveIntegerField, default 0.
- `maximum_recovery_percent` — nullable DecimalField.
- `reason`, `terms_snapshot` — TextField/JSONField.
- `status` — draft, submitted, approved, rejected, partially_disbursed, active, suspended, settled, cancelled, written_off.
- `approved_by`, `approved_at` — nullable FK -> User/DateTimeField.
- `principal_outstanding`, `interest_outstanding` — cached DecimalField balances.

Constraints: unique `(company, loan_reference)`; balances reconcile to completed disbursements, repayments, and approved waivers/write-offs.

### 73. LoanDisbursement

Records the actual release of loan funds. It may be one payment or several tranches.

Common fields: TenantOwned plus actor tracking.

- `employee_loan` — FK -> EmployeeLoan, PROTECT.
- `disbursement_reference` — CharField.
- `disbursed_at` — DateTimeField.
- `principal_amount` — positive DecimalField money.
- `currency` — CharField(3).
- `payment_method`, `provider_reference` — CharField.
- `status` — pending, completed, failed, cancelled, reversed.
- `processed_by` — nullable FK -> User, PROTECT.
- `reversal_of` — nullable O2O -> self, PROTECT.

Constraints: unique `(company, disbursement_reference)`; completed disbursements cannot exceed approved principal.

### 74. LoanInstallment

The scheduled principal/interest due for one loan installment. This schedule is distinct from what was actually recovered.

Common fields: TenantOwned plus actor tracking.

- `employee_loan` — FK -> EmployeeLoan, CASCADE.
- `installment_number` — PositiveIntegerField.
- `due_date` — DateField.
- `payroll_period` — nullable FK -> PayrollPeriod, PROTECT.
- `opening_principal` — DecimalField money.
- `principal_due`, `interest_due`, `fee_due`, `total_due` — DecimalField money.
- `paid_principal`, `paid_interest`, `paid_fee` — cached DecimalField summaries.
- `status` — scheduled, partially_paid, paid, deferred, waived, cancelled.
- `deferred_to` — nullable FK -> self, PROTECT.
- `calculation_snapshot` — JSONField.

Constraints: unique `(employee_loan, installment_number)`; component totals reconcile to total due; paid summaries derive from active allocations.

### 75. LoanRepayment

An actual amount recovered for an employee loan, from payroll or an external payment.

Common fields: TenantOwned plus actor tracking.

- `employee_loan` — FK -> EmployeeLoan, PROTECT.
- `payroll_period` — nullable FK -> PayrollPeriod, PROTECT.
- `repayment_reference` — CharField.
- `repayment_date` — DateField.
- `amount` — positive DecimalField money.
- `currency` — CharField(3).
- `method` — payroll_deduction, cash, bank_transfer, adjustment, write_off.
- `status` — pending, posted, received, failed, cancelled, reversed.
- `provider_reference` — optional CharField.
- `idempotency_key` — CharField.
- `reversal_of` — nullable O2O -> self, PROTECT.

Relations: PayrollLine points to this row when method is payroll_deduction.

Constraints: unique `(company, repayment_reference)` and `(company, idempotency_key)`; allocations cannot exceed the active repayment amount.

### 76. LoanRepaymentAllocation

Splits one LoanRepayment across one or more scheduled installments and principal/interest/fee buckets.

Common fields: TenantOwned plus actor tracking.

- `loan_repayment` — FK -> LoanRepayment, PROTECT.
- `loan_installment` — FK -> LoanInstallment, PROTECT.
- `principal_amount`, `interest_amount`, `fee_amount` — nonnegative DecimalField money.
- `allocated_at` — DateTimeField.
- `status` — active, reversed.
- `reversal_of` — nullable O2O -> self, PROTECT.

Constraints: repayment and installment belong to the same loan/company/currency; active allocations cannot exceed the repayment or remaining installment balances.

### 77. PayrollAdjustment

An approved one-time earning, deduction, balance correction, waiver, or carry-forward instruction. It becomes financial only when a PayrollLine posts it or when its explicitly selected non-payroll balance action is applied.

Common fields: TenantOwned plus actor tracking.

- `employee` — FK -> Employee, PROTECT.
- `adjustment_reference` — CharField.
- `salary_component` — nullable FK -> SalaryComponent, PROTECT.
- `target_payroll_period` — nullable FK -> PayrollPeriod, PROTECT.
- `adjustment_type` — earning, deduction, correction, advance_waiver, loan_waiver, carry_forward.
- `amount` — DecimalField money; store nonnegative and use type/direction for effect.
- `currency` — CharField(3).
- `service_date` — nullable DateField.
- `reason` — TextField.
- `source_document_reference` — optional CharField.
- `affects_payroll`, `affects_advance_balance`, `affects_loan_balance` — BooleanField.
- `salary_advance` — nullable FK -> SalaryAdvance, PROTECT.
- `employee_loan` — nullable FK -> EmployeeLoan, PROTECT.
- `status` — draft, submitted, approved, rejected, posted, cancelled, reversed.
- `approved_by`, `approved_at`, `posted_at` — nullable FK -> User/DateTimeField.
- `idempotency_key` — CharField.
- `reversal_of` — nullable O2O -> self, PROTECT.

Constraints: unique `(company, adjustment_reference)` and `(company, idempotency_key)`; target/source fields agree with adjustment type; a balance-only waiver must not silently create salary earnings.

### 78. PayrollRemittance

A company-level transfer to a third party for deductions or contributions, such as tax, insurance, or another provider. This is optional for the initial release.

Common fields: TenantOwned plus actor tracking.

- `payroll_period` — FK -> PayrollPeriod, PROTECT.
- `provider_type` — tax_authority, insurer, lender, benefit_provider, other.
- `provider_name`, `provider_account_reference` — CharField.
- `remittance_reference` — CharField.
- `remittance_date` — nullable DateField.
- `amount` — positive DecimalField money.
- `currency` — CharField(3).
- `payment_method`, `provider_transaction_reference` — optional CharField.
- `status` — draft, approved, submitted, completed, failed, cancelled, reversed.
- `approved_by`, `processed_by` — nullable FK -> User, PROTECT.
- `approved_at`, `processed_at` — nullable DateTimeField.
- `idempotency_key` — CharField.
- `reversal_of` — nullable O2O -> self, PROTECT.

Constraints: unique `(company, remittance_reference)` and `(company, idempotency_key)`; active allocations cannot exceed a completed remittance.

### 79. PayrollRemittanceAllocation

Links part of a PayrollRemittance to the employee PayrollLine that created the third-party liability.

Common fields: TenantOwned plus actor tracking.

- `payroll_remittance` — FK -> PayrollRemittance, PROTECT.
- `payroll_line` — FK -> PayrollLine, PROTECT.
- `allocated_amount` — positive DecimalField money.
- `allocated_at` — DateTimeField.
- `status` — active, reversed.
- `reversal_of` — nullable O2O -> self, PROTECT.

Constraints: remittance and line must have the same company/currency/provider-compatible component; active allocations cannot exceed either available remittance value or line liability.

## 11. subscriptions

### 80. Package

A platform-owned commercial offering. Package rows are global and controlled by root administrators, not company administrators.

- `id` — BigAutoField, PK.
- `code`, `name` — unique CharField.
- `description` — TextField, blank.
- `billing_interval` — monthly, yearly, custom.
- `price`, `currency` — DecimalField money/CharField(3).
- `included_employee_limit` — nullable PositiveIntegerField; null means unlimited.
- `included_user_limit` — nullable PositiveIntegerField if login-seat pricing is later offered.
- `device_limit`, `branch_limit` — nullable PositiveIntegerField.
- `trial_days` — PositiveIntegerField.
- `is_public`, `is_active` — BooleanField.
- `display_order` — PositiveIntegerField.
- `created_by`, `updated_by` — nullable FK -> User, SET_NULL.
- `created_at`, `updated_at` — DateTimeField.

Relations: features are M2M -> Feature through PackageFeature; CompanySubscription references Package.

### 81. PackageFeature

The explicit through row describing which Feature a Package contains and any package-level limit for it.

- `id` — BigAutoField, PK.
- `package` — FK -> Package, CASCADE.
- `feature` — FK -> Feature, PROTECT.
- `is_included` — BooleanField.
- `usage_limit` — nullable PositiveBigIntegerField.
- `configuration` — JSONField for validated feature-specific commercial limits.
- `created_at`, `updated_at` — DateTimeField.

Constraint: unique `(package, feature)`.

### 82. CompanySubscription

The company's effective-dated subscription to a package, including price and capacity snapshots so later package edits do not rewrite the commercial history.

Common fields: TenantOwned plus actor tracking.

- `package` — FK -> Package, PROTECT.
- `subscription_reference` — CharField.
- `starts_at`, `ends_at` — DateTimeField range.
- `trial_ends_at`, `cancelled_at` — nullable DateTimeField.
- `status` — trial, active, past_due, suspended, cancelled, expired.
- `billing_interval` — CharField snapshot.
- `price_snapshot` — DecimalField money.
- `currency` — CharField(3).
- `employee_limit_snapshot`, `user_limit_snapshot`, `device_limit_snapshot`, `branch_limit_snapshot` — nullable PositiveIntegerField.
- `feature_snapshot` — JSONField immutable commercial snapshot of feature codes and limits.
- `external_customer_id`, `external_subscription_id` — optional CharField.
- `auto_renew` — BooleanField.
- `cancellation_reason` — TextField, blank.

Constraints: unique `(company, subscription_reference)`; normally no overlapping active/trial subscriptions; entitlements use the subscription snapshot plus explicit CompanyFeature overrides. Capacity counting policy must define whether employees, users, or both are billable—the current recommendation is active employees.

## 12. auditlog

### 83. AuditLog

Implementation checkpoint (2026-09-07): this planned model now exists, bringing
implemented domain models to 22; the proposed inventory remains 83. Model/queryset
guards and a PostgreSQL trigger prohibit audit UPDATE/DELETE. Consequently,
deleting a referenced actor cannot perform SET_NULL while that trigger is active;
use account/membership deactivation. Any later approved erasure path needs an
explicit audited exception design. Optional hash/correlation fields are present;
tamper-evident hash chaining is not implemented. Field/relation design is unchanged,
so the existing generated proposed schema artifacts do not need regeneration.

An append-only security and business audit trail. It records who changed what, but does not replace domain history such as EmployeeAssignment, compensation versions, or payroll reversals.

- `id` — BigAutoField, PK.
- `company` — nullable FK -> Company, PROTECT; null only for platform-level actions.
- `actor_user` — nullable FK -> User, SET_NULL.
- `actor_membership` — nullable FK -> CompanyMembership, SET_NULL.
- `actor_type` — user, system, device, background_job, root_admin.
- `action` — CharField stable code such as employee.updated or payroll_run.posted.
- `object_app`, `object_model`, `object_id`, `object_public_id` — CharField identifiers; use content types only if their operational tradeoff is accepted.
- `object_display` — CharField snapshot.
- `request_id`, `correlation_id`, `idempotency_key` — optional CharField, indexed.
- `ip_address` — nullable GenericIPAddressField.
- `user_agent` — TextField, blank.
- `before_data`, `after_data`, `metadata` — JSONField with sensitive values redacted.
- `occurred_at` — DateTimeField, indexed.
- `integrity_hash`, `previous_hash` — optional CharField for tamper-evident chaining.

Constraints/indexes: index `(company, occurred_at)`, `(company, object_model, object_id, occurred_at)`, and `correlation_id`; application roles may not update or delete audit rows. Retention/export rules are company- and law-dependent.

## Relationship and implementation notes

- `Employee.id` is the permanent person/employment identity. Reusable visible employee codes live on effective-dated EmployeeAssignment rows, so both the former and current holder of a code retain their own histories.
- Tenant-owned junction rows repeat `company` deliberately. This makes scoping and partitioning practical, but Django validation alone cannot guarantee same-company foreign keys. Services must validate them and high-risk paths should use PostgreSQL constraints/triggers or composite-key patterns where appropriate.
- Raw DeviceMessage and PunchEvent rows are immutable evidence. PunchAllocation, AttendanceSession, AttendanceRecord, and Payroll* rows are derived layers and may be recalculated by version/reversal without rewriting the evidence.
- `PayrollLine` is the authoritative posting link to penalty, adjustment, recovery, encashment, and LFA source records. Do not add a second authoritative forward FK from each source back to one line; use Django reverse relations because a source may legitimately produce reversal/correction lines.
- Cached balances/totals (`LeaveEntitlement`, advances, loans, payroll records) improve reads but must be transactionally reconciled from immutable ledger/allocation rows.
- Implicit M2M tables in this proposal are `CompanyMembership.allowed_branches`, `CompanyMembership.allowed_departments`, `EmployeePermissionOverride.allowed_branches`, `EmployeePermissionOverride.allowed_departments`, and `PayrollDailyLine.leave_days`. All other M2M relationships use named through models.
