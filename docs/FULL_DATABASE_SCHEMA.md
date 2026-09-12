# Complete attendance database schema — every field

This is the field-level schema for the proposed attendance project: **83 domain models, 5 implicit M2M junctions, 1638 columns, and 464 foreign-key relationships**. No database or Django application is generated.

- [Interactive diagram](ATTENDANCE_SCHEMA.html): search a table, zoom, and highlight its relationships.
- [Full SVG diagram](ATTENDANCE_SCHEMA.svg): all table boxes contain all physical fields, types, key markers, and referenced targets.
- [Editable DBML source](ATTENDANCE_SCHEMA.dbml): all fields and relationships for a compatible database diagram editor.
- [Field dictionary](MODEL_FIELD_DICTIONARY.md): meaning, choice values, and detailed constraints.
- [Device attendance policy](DEVICE_ATTENDANCE_POLICY.md): device scopes, employee/branch/company precedence, enrollment versus permission, historical decisions, and pairing across devices.
- [Database architecture](DATABASE_SCHEMA.md): business flows and tenant/index design.

## Reading the schema

- PK = primary key. FK = many-to-one foreign key. FK/UQ = one-to-one or unique foreign key. UQ = unique column. NULL = optional database value.
- Django `employee` becomes the physical column `employee_id`. Vendor text fields already named `device_user_id` remain scalar text, not foreign keys.
- Shared fields are expanded in every applicable table. M2M fields are represented by physical junction tables, not fictitious array columns.
- The five implicit junctions inherit tenant ownership through their parents; they have no direct company column. Django User groups/permissions and framework infrastructure tables are outside this domain diagram.
- PostgreSQL types are proposed mappings: text/choice CharFields use varchar unless a known width is specified; rates use numeric(18,6), monetary amounts numeric(18,2), JSONField uses jsonb, and encrypted BinaryField uses bytea. Optional text may be NULL in this proposal; blank-versus-NULL must be finalized consistently in the future Django models.
- Ambiguous mixed descriptions were made explicit for display: actor fields ending in _by reference User, fields ending in _at are timestamps; attendance-session allocation links and ledger reversal links marked O2O/FK use a unique FK. Leave-treatment fields are choice strings, with percentages retained on the approved leave records. Open effective_to values are nullable.
- DBML includes column PK/unique constraints, all FKs, and implicit-junction pair uniqueness. Other multi-column, conditional, range, ledger, hierarchy, and tenant consistency constraints remain explanatory notes; this file is not an executable or fully constrained migration.
- Arrowheads run from each child FK to the referenced parent PK. Click a table to highlight connections. FK target labels remain readable even with connecting lines hidden.

## App diagrams

- [accounts — all fields](schema_diagrams/accounts.svg)
- [tenants — all fields](schema_diagrams/tenants.svg)
- [organization — all fields](schema_diagrams/organization.svg)
- [employees — all fields](schema_diagrams/employees.svg)
- [access_control — all fields](schema_diagrams/access_control.svg)
- [scheduling — all fields](schema_diagrams/scheduling.svg)
- [devices — all fields](schema_diagrams/devices.svg)
- [attendance — all fields](schema_diagrams/attendance.svg)
- [leaves — all fields](schema_diagrams/leaves.svg)
- [payroll — all fields](schema_diagrams/payroll.svg)
- [subscriptions — all fields](schema_diagrams/subscriptions.svg)
- [auditlog — all fields](schema_diagrams/auditlog.svg)

## accounts

### User

Physical table: `accounts_user`. Global/platform table; see company field if present.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `email` | `varchar` | UQ | No | — |
| `username` | `varchar` | UQ | No | — |
| `first_name` | `varchar` | — | No | — |
| `last_name` | `varchar` | — | No | — |
| `phone` | `varchar` | — | No | — |
| `timezone` | `varchar` | — | No | — |
| `language` | `varchar` | — | No | — |
| `password` | `varchar` | — | No | — |
| `last_login` | `timestamptz` | — | Yes | — |
| `date_joined` | `timestamptz` | — | No | — |
| `is_active` | `boolean` | — | No | — |
| `is_staff` | `boolean` | — | No | — |
| `is_superuser` | `boolean` | — | No | — |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |

Relations: memberships, optional employee links, and actor/audit relations. Deactivate accounts instead of deleting them.
### CompanyMembership

Physical table: `accounts_company_membership`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `user_id` | `bigint` | FK | No | `accounts_user.id` |
| `role` | `varchar` | — | No | — |
| `status` | `varchar` | — | No | — |
| `joined_at` | `timestamptz` | — | Yes | — |
| `ended_at` | `timestamptz` | — | Yes | — |
| `invited_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `last_access_at` | `timestamptz` | — | Yes | — |

Constraints: unique `(company, user)`; departments selected in scope must belong to a selected/allowed branch and the same company. The two M2M fields create implicit junction tables unless later replaced by an explicit scope model.
### CompanyMembership_allowed_branches

Physical table: `accounts_company_membership_allowed_branches`. Implicit M2M junction.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `companymembership_id` | `bigint` | FK | No | `accounts_company_membership.id` |
| `branch_id` | `bigint` | FK | No | `organization_branch.id` |

Unique pair; both rows must belong to the same company. Tenant ownership is inherited from the source parent; this implicit junction has no company_id.
### CompanyMembership_allowed_departments

Physical table: `accounts_company_membership_allowed_departments`. Implicit M2M junction.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `companymembership_id` | `bigint` | FK | No | `accounts_company_membership.id` |
| `companydepartment_id` | `bigint` | FK | No | `organization_company_department.id` |

Unique pair; both rows must belong to the same company. Tenant ownership is inherited from the source parent; this implicit junction has no company_id.

## tenants

### Company

Physical table: `tenants_company`. Global/platform table; see company field if present.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `public_id` | `uuid` | UQ | No | — |
| `code` | `varchar` | UQ | No | — |
| `slug` | `varchar` | UQ | No | — |
| `name` | `varchar` | — | No | — |
| `legal_name` | `varchar` | — | No | — |
| `timezone` | `varchar` | — | No | — |
| `currency` | `varchar(3)` | — | No | — |
| `language` | `varchar` | — | No | — |
| `country_code` | `varchar` | — | No | — |
| `email` | `varchar` | — | No | — |
| `phone` | `varchar` | — | No | — |
| `address` | `text` | — | No | — |
| `registration_number` | `varchar` | — | Yes | — |
| `tax_identifier` | `varchar` | — | Yes | — |
| `logo` | `varchar` | — | Yes | — |
| `status` | `varchar` | — | No | — |
| `activated_at` | `timestamptz` | — | Yes | — |
| `suspended_at` | `timestamptz` | — | Yes | — |
| `suspended_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `suspension_reason` | `text` | — | No | — |

Relations: root of all tenant-owned rows; CompanyMembership, Branch, CompanyFeature, CompanySubscription, settings, employees, and operational records.
### Feature

Physical table: `tenants_feature`. Global/platform table; see company field if present.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `code` | `varchar` | UQ | No | — |
| `name` | `varchar` | — | No | — |
| `description` | `text` | — | No | — |
| `is_core` | `boolean` | — | No | — |
| `is_active` | `boolean` | — | No | — |
| `sort_order` | `integer` | — | No | — |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |

Relations: PackageFeature, CompanyFeature, AccessPermission.
### CompanyFeature

Physical table: `tenants_company_feature`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `feature_id` | `bigint` | FK | No | `tenants_feature.id` |
| `effect` | `varchar` | — | No | — |
| `starts_at` | `timestamptz` | — | Yes | — |
| `ends_at` | `timestamptz` | — | Yes | — |
| `limits` | `jsonb` | — | No | — |
| `reason` | `text` | — | No | — |
| `granted_by_id` | `bigint` | FK | No | `accounts_user.id` |
| `is_active` | `boolean` | — | No | — |

Constraints: prevent overlapping active overrides for the same company/feature/effect. Effective access combines package snapshot and this dated override.

## organization

### Branch

Physical table: `organization_branch`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `code` | `varchar` | — | No | — |
| `name` | `varchar` | — | No | — |
| `address` | `text` | — | No | — |
| `city` | `varchar` | — | No | — |
| `postal_code` | `varchar` | — | No | — |
| `country_code` | `varchar` | — | No | — |
| `timezone` | `varchar` | — | No | — |
| `email` | `varchar` | — | Yes | — |
| `phone` | `varchar` | — | Yes | — |
| `is_default` | `boolean` | — | No | — |
| `status` | `varchar` | — | No | — |
| `device_attendance_scope_override` | `varchar` | — | Yes | — |
| `opened_on` | `date` | — | Yes | — |
| `closed_on` | `date` | — | Yes | — |

Constraints: unique `(company, code)`; one active default branch per company.
### Department

Physical table: `organization_department`. Global/platform table; see company field if present.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `code` | `varchar` | UQ | No | — |
| `name` | `varchar` | UQ | No | — |
| `description` | `text` | — | No | — |
| `status` | `varchar` | — | No | — |

Relations: Designation, CompanyDepartment.
### Designation

Physical table: `organization_designation`. Global/platform table; see company field if present.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `code` | `varchar` | UQ | No | — |
| `name` | `varchar` | UQ | No | — |
| `description` | `text` | — | No | — |
| `status` | `varchar` | — | No | — |
### CompanyDepartment

Physical table: `organization_company_department`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `branch_id` | `bigint` | FK | No | `organization_branch.id` |
| `department_id` | `bigint` | FK | No | `organization_department.id` |
| `head_id` | `bigint` | FK | Yes | `employees_employee.id` |
| `description` | `text` | — | No | — |
| `status` | `varchar` | — | No | — |
| `opened_on` | `date` | — | Yes | — |
| `closed_on` | `date` | — | Yes | — |

Constraints: unique `(branch, department)` — a branch adopts each catalogue department at most once; the branch must belong to the same company.
### CompanyDesignation

Physical table: `organization_company_designation`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `company_department_id` | `bigint` | FK | No | `organization_company_department.id` |
| `designation_id` | `bigint` | FK | No | `organization_designation.id` |
| `status` | `varchar` | — | No | — |

Constraints: unique `(company_department, designation)`. Any active designation may be placed under any of the company's departments; a designation root has deactivated cannot be newly placed, but existing placements keep it.

## employees

### Employee

Physical table: `employees_employee`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `public_id` | `uuid` | UQ | No | — |
| `user_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `first_name` | `varchar` | — | No | — |
| `middle_name` | `varchar` | — | No | — |
| `last_name` | `varchar` | — | No | — |
| `preferred_name` | `varchar` | — | No | — |
| `work_email` | `varchar` | — | Yes | — |
| `personal_email` | `varchar` | — | Yes | — |
| `phone` | `varchar` | — | Yes | — |
| `date_of_birth` | `date` | — | Yes | — |
| `gender` | `varchar` | — | Yes | — |
| `blood_group` | `varchar` | — | Yes | — |
| `marital_status` | `varchar` | — | Yes | — |
| `national_id` | `varchar` | — | Yes | — |
| `passport_number` | `varchar` | — | Yes | — |
| `address` | `text` | — | Yes | — |
| `emergency_contact_name` | `varchar` | — | Yes | — |
| `emergency_contact_phone` | `varchar` | — | Yes | — |
| `emergency_contact_relation` | `varchar` | — | Yes | — |
| `joining_date` | `date` | — | Yes | — |
| `confirmation_date` | `date` | — | Yes | — |
| `leaving_date` | `date` | — | Yes | — |
| `employment_status` | `varchar` | — | No | — |
| `photo` | `varchar` | — | Yes | — |
| `metadata` | `jsonb` | — | No | — |

Constraints: unique `(company, user)` when user is not null. All business history references this permanent identity.
### EmployeeAssignment

Physical table: `employees_employee_assignment`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `employee_id` | `bigint` | FK | No | `employees_employee.id` |
| `employee_code` | `varchar` | — | No | — |
| `branch_id` | `bigint` | FK | No | `organization_branch.id` |
| `department_id` | `bigint` | FK | No | `organization_company_department.id` |
| `designation_id` | `bigint` | FK | No | `organization_company_designation.id` |
| `manager_id` | `bigint` | FK | Yes | `employees_employee.id` |
| `effective_from` | `timestamptz` | — | No | — |
| `effective_to` | `timestamptz` | — | Yes | — |
| `change_reason` | `text` | — | No | — |
| `status` | `varchar` | — | No | — |
| `device_attendance_scope_override` | `varchar` | — | Yes | — |

Constraints: the company department belongs to the assignment's branch; the designation is one the company has placed under that department (a CompanyDesignation of it); manager is not the employee; no overlapping active periods for one employee; no overlapping occupancy of `(company, employee_code)`. The same code may be reused after the earlier interval ends.
### EmployeeCompensation

Physical table: `employees_employee_compensation`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `employee_id` | `bigint` | FK | No | `employees_employee.id` |
| `pay_basis` | `varchar` | — | No | — |
| `base_rate` | `numeric(18,2)` | — | No | — |
| `currency` | `varchar(3)` | — | No | — |
| `overtime_rate_override` | `numeric(18,2)` | — | Yes | — |
| `effective_from` | `timestamptz` | — | No | — |
| `effective_to` | `timestamptz` | — | Yes | — |
| `reason` | `text` | — | No | — |
| `status` | `varchar` | — | No | — |

Constraints: positive base rate; currency matches company policy; non-overlapping active compensation ranges per employee.

## access_control

### AccessPermission

Physical table: `access_control_access_permission`. Global/platform table; see company field if present.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `feature_id` | `bigint` | FK | No | `tenants_feature.id` |
| `code` | `varchar` | UQ | No | — |
| `name` | `varchar` | — | No | — |
| `description` | `text` | — | No | — |
| `action` | `varchar` | — | No | — |
| `is_sensitive` | `boolean` | — | No | — |
| `is_active` | `boolean` | — | No | — |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
### DesignationPermission

Physical table: `access_control_designation_permission`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `designation_id` | `bigint` | FK | No | `organization_company_designation.id` |
| `permission_id` | `bigint` | FK | No | `access_control_access_permission.id` |
| `access_level` | `varchar` | — | No | — |
| `can_delegate` | `boolean` | — | No | — |
| `effective_from` | `timestamptz` | — | Yes | — |
| `effective_to` | `timestamptz` | — | Yes | — |
| `reason` | `text` | — | No | — |

Constraints: unique effective rule per designation/permission at any instant; a title may not be ALLOWED what its own department DENIES through DepartmentPermission (86). That department ceiling replaced the old designation parent-chain walk.
### EmployeePermissionOverride

Physical table: `access_control_employee_permission_override`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `employee_id` | `bigint` | FK | No | `employees_employee.id` |
| `permission_id` | `bigint` | FK | No | `access_control_access_permission.id` |
| `effect` | `varchar` | — | No | — |
| `effective_from` | `timestamptz` | — | Yes | — |
| `effective_to` | `timestamptz` | — | Yes | — |
| `granted_by_id` | `bigint` | FK | No | `accounts_user.id` |
| `reason` | `text` | — | No | — |
| `status` | `varchar` | — | No | — |

Constraints: a GRANT may not exceed what the employee's current department DENIES — this is what makes delegating to a department head safe, since the head cannot widen the boundary they administer inside; selected departments belong to selected branches/company.
### DepartmentPermission

Physical table: `access_control_department_permission`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `company_department_id` | `bigint` | FK | No | `organization_company_department.id` |
| `permission_id` | `bigint` | FK | No | `access_control_access_permission.id` |
| `access_level` | `varchar` | — | No | — |
| `can_delegate` | `boolean` | — | No | — |
| `effective_from` | `timestamptz` | — | Yes | — |
| `effective_to` | `timestamptz` | — | Yes | — |
| `reason` | `text` | — | No | — |

Constraints: one effective rule per department/permission at any instant; end after start.
### EmployeePermissionOverride_allowed_branches

Physical table: `access_control_employee_permission_override_allowed_branches`. Implicit M2M junction.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `employeepermissionoverride_id` | `bigint` | FK | No | `access_control_employee_permission_override.id` |
| `branch_id` | `bigint` | FK | No | `organization_branch.id` |

Unique pair; both rows must belong to the same company. Tenant ownership is inherited from the source parent; this implicit junction has no company_id.
### EmployeePermissionOverride_allowed_departments

Physical table: `access_control_employee_permission_override_allowed_departments`. Implicit M2M junction.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `employeepermissionoverride_id` | `bigint` | FK | No | `access_control_employee_permission_override.id` |
| `companydepartment_id` | `bigint` | FK | No | `organization_company_department.id` |

Unique pair; both rows must belong to the same company. Tenant ownership is inherited from the source parent; this implicit junction has no company_id.

## scheduling

### Shift

Physical table: `scheduling_shift`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `code` | `varchar` | — | No | — |
| `name` | `varchar` | — | No | — |
| `start_time` | `time` | — | No | — |
| `end_time` | `time` | — | No | — |
| `spans_next_day` | `boolean` | — | No | — |
| `scheduled_minutes` | `integer` | — | No | — |
| `grace_in_minutes` | `integer` | — | No | — |
| `grace_out_minutes` | `integer` | — | No | — |
| `minimum_full_day_minutes` | `integer` | — | No | — |
| `minimum_half_day_minutes` | `integer` | — | No | — |
| `default_break_minutes` | `integer` | — | No | — |
| `break_is_paid` | `boolean` | — | No | — |
| `overtime_after_minutes` | `integer` | — | No | — |
| `effective_from` | `date` | — | Yes | — |
| `effective_to` | `date` | — | Yes | — |
| `status` | `varchar` | — | No | — |

Constraints: unique `(company, code)`; timing and minute thresholds must be internally consistent.
### DepartmentShift

Physical table: `scheduling_department_shift`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `department_id` | `bigint` | FK | No | `organization_company_department.id` |
| `shift_id` | `bigint` | FK | No | `scheduling_shift.id` |
| `is_default` | `boolean` | — | No | — |
| `effective_from` | `date` | — | No | — |
| `effective_to` | `date` | — | Yes | — |
| `status` | `varchar` | — | No | — |

Constraints: no overlapping duplicate department/shift assignments; at most one default at a time. In single-shift company mode all active department shifts must reference the company shift.
### EmployeeShiftAssignment

Physical table: `scheduling_employee_shift_assignment`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `employee_id` | `bigint` | FK | No | `employees_employee.id` |
| `shift_id` | `bigint` | FK | No | `scheduling_shift.id` |
| `effective_from` | `timestamptz` | — | No | — |
| `effective_to` | `timestamptz` | — | Yes | — |
| `assignment_type` | `varchar` | — | No | — |
| `reason` | `text` | — | No | — |
| `assigned_by_id` | `bigint` | FK | No | `accounts_user.id` |
| `status` | `varchar` | — | No | — |

Constraints: no overlapping effective assignments for an employee. Validate department eligibility except for an authorized override.
### CompanyAttendanceSettings

Physical table: `scheduling_company_attendance_settings`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK/UQ | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `shift_mode` | `varchar` | — | No | — |
| `company_shift_id` | `bigint` | FK | Yes | `scheduling_shift.id` |
| `punch_pairing_strategy` | `varchar` | — | No | — |
| `device_attendance_scope` | `varchar` | — | No | — |
| `duplicate_punch_window_seconds` | `integer` | — | No | — |
| `attendance_window_before_minutes` | `integer` | — | No | — |
| `attendance_window_after_minutes` | `integer` | — | No | — |
| `missing_punch_policy` | `varchar` | — | No | — |
| `overtime_requires_approval` | `boolean` | — | No | — |
| `round_work_minutes_to` | `integer` | — | No | — |
| `round_overtime_minutes_to` | `integer` | — | No | — |
| `settings_version` | `integer` | — | No | — |
| `effective_from` | `timestamptz` | — | No | — |

Constraint: company_shift required only in single-shift mode. Historical AttendanceRecord snapshots preserve rules actually used.
### WeeklyOffRule

Physical table: `scheduling_weekly_off_rule`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `branch_id` | `bigint` | FK | Yes | `organization_branch.id` |
| `weekday` | `integer` | — | No | — |
| `is_paid` | `boolean` | — | No | — |
| `effective_from` | `date` | — | No | — |
| `effective_to` | `date` | — | Yes | — |
| `status` | `varchar` | — | No | — |

Constraints: prevent overlapping duplicate weekday rules for the same company/branch.
### Holiday

Physical table: `scheduling_holiday`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `branch_id` | `bigint` | FK | Yes | `organization_branch.id` |
| `holiday_date` | `date` | — | No | — |
| `name` | `text` | — | No | — |
| `description` | `text` | — | No | — |
| `is_paid` | `boolean` | — | No | — |
| `status` | `varchar` | — | No | — |
| `cancelled_at` | `timestamptz` | — | Yes | — |
| `cancelled_by_id` | `bigint` | FK | Yes | `accounts_user.id` |

Constraints: unique active holiday per `(company, branch, holiday_date)`, treating null branch as a comparable company-wide scope.
### HolidayWorkAssignment

Physical table: `scheduling_holiday_work_assignment`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `employee_id` | `bigint` | FK | No | `employees_employee.id` |
| `work_date` | `date` | — | No | — |
| `holiday_id` | `bigint` | FK | Yes | `scheduling_holiday.id` |
| `weekly_off_rule_id` | `bigint` | FK | Yes | `scheduling_weekly_off_rule.id` |
| `shift_id` | `bigint` | FK | Yes | `scheduling_shift.id` |
| `treatment` | `varchar` | — | No | — |
| `reason` | `text` | — | No | — |
| `approved_by_id` | `bigint` | FK | No | `accounts_user.id` |
| `approved_at` | `timestamptz` | — | No | — |
| `status` | `varchar` | — | No | — |

Constraints: exactly one applicable source (dated holiday or recurring weekly-off rule); source applies on work_date; one active assignment per employee/work_date/source.

## devices

### DeviceVendor

Physical table: `devices_device_vendor`. Global/platform table; see company field if present.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `code` | `varchar` | UQ | No | — |
| `name` | `varchar` | UQ | No | — |
| `adapter_key` | `varchar` | — | No | — |
| `description` | `text` | — | Yes | — |
| `support_url` | `varchar` | — | Yes | — |
| `is_active` | `boolean` | — | No | — |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |

Relations: DeviceModel and optional BiometricTemplate format metadata.
### DeviceModel

Physical table: `devices_device_model`. Global/platform table; see company field if present.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `vendor_id` | `bigint` | FK | No | `devices_device_vendor.id` |
| `model_code` | `varchar` | — | No | — |
| `name` | `varchar` | — | No | — |
| `protocol` | `varchar` | — | No | — |
| `capabilities` | `jsonb` | — | No | — |
| `supported_template_formats` | `jsonb` | — | No | — |
| `is_active` | `boolean` | — | No | — |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |

Constraint: unique `(vendor, model_code)`.
### BiometricDevice

Physical table: `devices_biometric_device`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `public_id` | `uuid` | UQ | No | — |
| `branch_id` | `bigint` | FK | No | `organization_branch.id` |
| `device_model_id` | `bigint` | FK | No | `devices_device_model.id` |
| `name` | `varchar` | — | No | — |
| `serial_number` | `varchar` | — | No | — |
| `external_device_id` | `varchar` | — | Yes | — |
| `timezone` | `varchar` | — | No | — |
| `authentication_key_id` | `varchar` | — | No | — |
| `authentication_secret_hash` | `varchar` | — | No | — |
| `firmware_version` | `varchar` | — | Yes | — |
| `ip_address_last_seen` | `inet` | — | Yes | — |
| `installed_at` | `timestamptz` | — | Yes | — |
| `decommissioned_at` | `timestamptz` | — | Yes | — |
| `last_seen_at` | `timestamptz` | — | Yes | — |
| `last_message_at` | `timestamptz` | — | Yes | — |
| `clock_offset_seconds` | `integer` | — | Yes | — |
| `settings` | `jsonb` | — | No | — |
| `status` | `varchar` | — | No | — |

Constraints: unique `(company, serial_number)`; device branch belongs to company.
### DeviceDepartment

Physical table: `devices_device_department`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `device_id` | `bigint` | FK | No | `devices_biometric_device.id` |
| `department_id` | `bigint` | FK | No | `organization_company_department.id` |
| `effective_from` | `timestamptz` | — | No | — |
| `effective_to` | `timestamptz` | — | Yes | — |
| `status` | `varchar` | — | No | — |

Constraints: device and department have the same company and branch; no overlapping duplicate mapping. This is the explicit through table for `BiometricDevice.departments`. Active mappings restrict department_devices mode; no active mappings means a shared branch device in that mode. These mappings do not additionally restrict branch_devices/company_devices mode or an explicit assigned-device grant.
### DeviceEnrollment

Physical table: `devices_device_enrollment`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `device_id` | `bigint` | FK | No | `devices_biometric_device.id` |
| `employee_id` | `bigint` | FK | No | `employees_employee.id` |
| `device_user_id` | `varchar` | — | No | — |
| `card_number` | `varchar` | — | Yes | — |
| `device_privilege` | `varchar` | — | No | — |
| `attendance_enabled` | `boolean` | — | No | — |
| `assigned_device_authorized` | `boolean` | — | No | — |
| `effective_from` | `timestamptz` | — | No | — |
| `effective_to` | `timestamptz` | — | Yes | — |
| `enrollment_status` | `varchar` | — | No | — |
| `vendor_enrollment_revision` | `varchar` | — | Yes | — |
| `last_synced_at` | `timestamptz` | — | Yes | — |
| `removed_at` | `timestamptz` | — | Yes | — |
| `sync_error_code` | `varchar` | — | Yes | — |
| `sync_error_message` | `text` | — | Yes | — |

Constraints: no overlapping use of one `(device, device_user_id)`; no overlapping duplicate employee/device authorization; resolve historical mapping using the punch timestamp. Enrollment identity, master enablement, and explicit assigned-device grant are separate concerns. Keep historical authorization changes in the audit trail; see DEVICE_ATTENDANCE_POLICY.md.
### BiometricTemplate

Physical table: `devices_biometric_template`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `employee_id` | `bigint` | FK | No | `employees_employee.id` |
| `biometric_type` | `varchar` | — | No | — |
| `finger_position` | `varchar` | — | Yes | — |
| `template_format` | `varchar` | — | No | — |
| `template_version` | `varchar` | — | No | — |
| `vendor_id` | `bigint` | FK | Yes | `devices_device_vendor.id` |
| `compatible_device_model_id` | `bigint` | FK | Yes | `devices_device_model.id` |
| `captured_from_device_id` | `bigint` | FK | Yes | `devices_biometric_device.id` |
| `encrypted_template_data` | `bytea` | — | No | — |
| `encryption_key_version` | `varchar` | — | No | — |
| `template_checksum` | `varchar` | — | No | — |
| `template_size_bytes` | `integer` | — | Yes | — |
| `quality_score` | `integer` | — | Yes | — |
| `captured_at` | `timestamptz` | — | No | — |
| `status` | `varchar` | — | No | — |
| `revoked_at` | `timestamptz` | — | Yes | — |
| `revoked_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `revocation_reason` | `text` | — | Yes | — |

Constraints: checksum/employee/type/format duplicate detection; face has no finger_position. Database backups and application logs must protect the encrypted data and key references.
### DeviceEnrollmentTemplate

Physical table: `devices_device_enrollment_template`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `device_enrollment_id` | `bigint` | FK | No | `devices_device_enrollment.id` |
| `biometric_template_id` | `bigint` | FK | No | `devices_biometric_template.id` |
| `device_template_id` | `varchar` | — | Yes | — |
| `deployment_status` | `varchar` | — | No | — |
| `attempt_count` | `integer` | — | No | — |
| `last_attempt_at` | `timestamptz` | — | Yes | — |
| `deployed_at` | `timestamptz` | — | Yes | — |
| `removed_at` | `timestamptz` | — | Yes | — |
| `last_error_code` | `varchar` | — | Yes | — |
| `last_error_message` | `text` | — | Yes | — |
| `source_key` | `varchar` | — | No | — |

Constraints: unique active `(device_enrollment, biometric_template)`; employee and compatibility must match enrollment/device.
### DeviceSyncState

Physical table: `devices_device_sync_state`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `device_id` | `bigint` | FK/UQ | No | `devices_biometric_device.id` |
| `last_vendor_sequence` | `varchar` | — | Yes | — |
| `last_vendor_cursor` | `varchar` | — | Yes | — |
| `last_device_event_at` | `timestamptz` | — | Yes | — |
| `last_message_received_at` | `timestamptz` | — | Yes | — |
| `last_punch_received_at` | `timestamptz` | — | Yes | — |
| `last_success_at` | `timestamptz` | — | Yes | — |
| `last_error_at` | `timestamptz` | — | Yes | — |
| `last_error_code` | `varchar` | — | Yes | — |
| `last_error_message` | `text` | — | Yes | — |
| `consecutive_error_count` | `integer` | — | No | — |
| `estimated_backlog_count` | `integer` | — | No | — |
| `clock_offset_seconds` | `integer` | — | Yes | — |
| `state_data` | `jsonb` | — | No | — |
| `version` | `bigint` | — | No | — |
### DeviceMessage

Physical table: `devices_device_message`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `public_id` | `uuid` | UQ | No | — |
| `device_id` | `bigint` | FK | No | `devices_biometric_device.id` |
| `branch_id` | `bigint` | FK | No | `organization_branch.id` |
| `message_type` | `varchar` | — | No | — |
| `vendor_message_id` | `varchar` | — | Yes | — |
| `vendor_sequence` | `varchar` | — | Yes | — |
| `idempotency_key` | `varchar` | — | Yes | — |
| `occurred_at_device` | `timestamptz` | — | Yes | — |
| `received_at` | `timestamptz` | — | No | — |
| `content_type` | `varchar` | — | No | — |
| `encoding` | `varchar` | — | No | — |
| `raw_payload_text` | `text` | — | Yes | — |
| `raw_payload_binary` | `bytea` | — | Yes | — |
| `payload_json` | `jsonb` | — | Yes | — |
| `payload_hash` | `varchar` | — | No | — |
| `source_ip` | `inet` | — | Yes | — |
| `request_headers_snapshot` | `jsonb` | — | Yes | — |
| `record_count` | `integer` | — | Yes | — |
| `processing_status` | `varchar` | — | No | — |
| `processing_started_at` | `timestamptz` | — | Yes | — |
| `processed_at` | `timestamptz` | — | Yes | — |
| `processing_attempts` | `integer` | — | No | — |
| `processing_error` | `text` | — | No | — |

Constraints: one raw representation must exist; reliable `(device, idempotency_key)` unique when non-null. A payload hash alone is not safe proof that two punches are identical.
### PunchEvent

Physical table: `devices_punch_event`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `device_message_id` | `bigint` | FK | No | `devices_device_message.id` |
| `device_id` | `bigint` | FK | No | `devices_biometric_device.id` |
| `branch_id` | `bigint` | FK | No | `organization_branch.id` |
| `device_enrollment_id` | `bigint` | FK | Yes | `devices_device_enrollment.id` |
| `employee_id` | `bigint` | FK | Yes | `employees_employee.id` |
| `device_user_id` | `varchar` | — | No | — |
| `vendor_punch_id` | `varchar` | — | Yes | — |
| `vendor_sequence` | `varchar` | — | Yes | — |
| `source_record_index` | `integer` | — | No | — |
| `punched_at_device_raw` | `varchar` | — | No | — |
| `punched_at_device` | `timestamptz` | — | No | — |
| `punched_at_utc` | `timestamptz` | — | No | — |
| `device_timezone` | `varchar` | — | No | — |
| `utc_offset_minutes` | `integer` | — | No | — |
| `received_at` | `timestamptz` | — | No | — |
| `verification_method` | `varchar` | — | No | — |
| `reported_direction` | `varchar` | — | Yes | — |
| `reported_status_code` | `varchar` | — | Yes | — |
| `raw_record` | `jsonb` | — | No | — |
| `authorization_status` | `varchar` | — | No | — |
| `authorization_snapshot` | `jsonb` | — | No | — |
| `dedupe_status` | `varchar` | UQ | No | — |
| `duplicate_of_id` | `bigint` | FK | Yes | `devices_punch_event.id` |
| `processing_status` | `varchar` | — | No | — |
| `processing_error` | `text` | — | No | — |
| `resolved_at` | `timestamptz` | — | Yes | — |
| `processed_at` | `timestamptz` | — | Yes | — |

Constraints: unique `(device_message, source_record_index)`; reliable vendor punch identity unique per device when present. Timestamp is never the sole unique key.

## attendance

### PunchAllocation

Physical table: `payroll_punch_allocation`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `attendance_record_id` | `bigint` | FK | No | `payroll_attendance_record.id` |
| `punch_event_id` | `bigint` | FK | Yes | `devices_punch_event.id` |
| `attendance_correction_id` | `bigint` | FK | Yes | `payroll_attendance_correction.id` |
| `sequence_number` | `integer` | — | No | — |
| `event_at` | `timestamptz` | — | No | — |
| `interpreted_direction` | `varchar` | — | No | — |
| `is_included` | `boolean` | — | No | — |
| `exclusion_reason` | `text` | — | No | — |
| `confidence` | `numeric(18,6)` | — | Yes | — |
| `calculation_version` | `integer` | — | No | — |
| `interpretation_note` | `text` | — | No | — |

Constraints: exactly one source (PunchEvent or approved AttendanceCorrection); unique source per calculation version; sequence unique within attendance/version. Merge all authorized devices into one employee/shift stream ordered by event time; never restart alternating IN/OUT at each device or incoming batch.
### AttendanceSession

Physical table: `payroll_attendance_session`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `attendance_record_id` | `bigint` | FK | No | `payroll_attendance_record.id` |
| `sequence_number` | `integer` | — | No | — |
| `in_allocation_id` | `bigint` | FK/UQ | Yes | `payroll_punch_allocation.id` |
| `out_allocation_id` | `bigint` | FK/UQ | Yes | `payroll_punch_allocation.id` |
| `started_at` | `timestamptz` | — | Yes | — |
| `ended_at` | `timestamptz` | — | Yes | — |
| `worked_minutes` | `integer` | — | No | — |
| `status` | `varchar` | — | No | — |
| `is_manual` | `boolean` | — | No | — |
| `calculation_version` | `integer` | — | No | — |

Constraints: end after start; allocation directions correct; sequence unique per record/version. Both allocations belong to this attendance record and employee/company, but may originate from different devices. Never require matching device IDs.
### AttendanceRecord

Physical table: `payroll_attendance_record`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `employee_id` | `bigint` | FK | No | `employees_employee.id` |
| `employee_assignment_id` | `bigint` | FK | No | `employees_employee_assignment.id` |
| `branch_id` | `bigint` | FK | No | `organization_branch.id` |
| `department_id` | `bigint` | FK | No | `organization_department.id` |
| `work_date` | `date` | — | No | — |
| `shift_id` | `bigint` | FK | No | `scheduling_shift.id` |
| `shift_snapshot` | `jsonb` | — | No | — |
| `attendance_settings_snapshot` | `jsonb` | — | No | — |
| `scheduled_start_at` | `timestamptz` | — | No | — |
| `scheduled_end_at` | `timestamptz` | — | No | — |
| `first_in_at` | `timestamptz` | — | Yes | — |
| `last_out_at` | `timestamptz` | — | Yes | — |
| `worked_minutes` | `integer` | — | No | — |
| `break_minutes` | `integer` | — | No | — |
| `outside_minutes` | `integer` | — | No | — |
| `late_minutes` | `integer` | — | No | — |
| `early_out_minutes` | `integer` | — | No | — |
| `calculated_overtime_minutes` | `integer` | — | No | — |
| `approved_overtime_minutes` | `integer` | — | No | — |
| `attendance_status` | `varchar` | — | No | — |
| `punch_status` | `varchar` | — | No | — |
| `calculation_version` | `integer` | — | No | — |
| `source_revision_hash` | `varchar` | — | No | — |
| `calculated_at` | `timestamptz` | — | No | — |
| `review_status` | `varchar` | — | No | — |
| `reviewed_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `reviewed_at` | `timestamptz` | — | Yes | — |
| `is_payroll_locked` | `boolean` | — | No | — |

Constraint: unique `(company, employee, work_date)`; assignment applies on work date and department/branch match it.
### AttendanceCorrection

Physical table: `payroll_attendance_correction`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `attendance_record_id` | `bigint` | FK | No | `payroll_attendance_record.id` |
| `requested_by_user_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `requested_for_employee_id` | `bigint` | FK | No | `employees_employee.id` |
| `correction_type` | `varchar` | — | No | — |
| `target_punch_event_id` | `bigint` | FK | Yes | `devices_punch_event.id` |
| `proposed_event_at` | `timestamptz` | — | Yes | — |
| `proposed_direction` | `varchar` | — | Yes | — |
| `proposed_overtime_minutes` | `integer` | — | Yes | — |
| `proposed_sessions` | `jsonb` | — | No | — |
| `reason` | `text` | — | No | — |
| `attachment` | `varchar` | — | Yes | — |
| `status` | `varchar` | — | No | — |
| `approved_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `approved_at` | `timestamptz` | — | Yes | — |
| `decision_note` | `text` | — | No | — |
| `before_snapshot` | `jsonb` | — | No | — |
| `after_snapshot` | `jsonb` | — | No | — |
| `applied_at` | `timestamptz` | — | Yes | — |
| `resulting_calculation_version` | `integer` | — | Yes | — |
### AttendancePenaltyRule

Physical table: `payroll_attendance_penalty_rule`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `name` | `varchar` | — | No | — |
| `code` | `varchar` | — | No | — |
| `metric` | `varchar` | — | No | — |
| `operator` | `varchar` | — | No | — |
| `threshold_minutes` | `integer` | — | Yes | — |
| `required_occurrences` | `integer` | — | No | — |
| `occurrence_mode` | `varchar` | — | No | — |
| `rolling_window_days` | `integer` | — | Yes | — |
| `sequence_break_policy` | `jsonb` | — | No | — |
| `deduction_method` | `varchar` | — | No | — |
| `deduction_value` | `numeric(18,6)` | — | No | — |
| `priority` | `integer` | — | No | — |
| `exclusive_group` | `varchar` | — | Yes | — |
| `stacking_policy` | `varchar` | — | No | — |
| `maximum_deduction` | `numeric(18,6)` | — | Yes | — |
| `effective_from` | `date` | — | No | — |
| `effective_to` | `date` | — | Yes | — |
| `status` | `varchar` | — | No | — |
| `version` | `integer` | — | No | — |

Constraint: rule inputs consistent with metric/method. Activated rule versions are immutable.
### PenaltyAssessment

Physical table: `payroll_penalty_assessment`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `employee_id` | `bigint` | FK | No | `employees_employee.id` |
| `penalty_rule_id` | `bigint` | FK | No | `payroll_attendance_penalty_rule.id` |
| `payroll_period_id` | `bigint` | FK | Yes | `payroll_period.id` |
| `period_start` | `date` | — | No | — |
| `period_end` | `date` | — | No | — |
| `occurrence_identity` | `varchar` | — | No | — |
| `occurrence_count` | `integer` | — | No | — |
| `deduction_minutes` | `integer` | — | No | — |
| `deduction_day_fraction` | `numeric(18,6)` | — | No | — |
| `deduction_amount` | `numeric(18,2)` | — | No | — |
| `currency` | `varchar(3)` | — | No | — |
| `calculation_details` | `jsonb` | — | No | — |
| `status` | `varchar` | — | No | — |
| `approved_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `approved_at` | `timestamptz` | — | Yes | — |
| `reversal_of_id` | `bigint` | FK | Yes | `payroll_penalty_assessment.id` |
| `calculated_at` | `timestamptz` | — | No | — |

Constraint: unique occurrence identity per company; one original payroll posting.
### PenaltyAssessmentAttendance

Physical table: `payroll_penalty_assessment_attendance`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `penalty_assessment_id` | `bigint` | FK | No | `payroll_penalty_assessment.id` |
| `attendance_record_id` | `bigint` | FK | No | `payroll_attendance_record.id` |
| `sequence_number` | `integer` | — | No | — |
| `qualifying_value` | `numeric(18,6)` | — | No | — |
| `reason_snapshot` | `jsonb` | — | No | — |

Constraint: unique `(penalty_assessment, attendance_record)`. This is the explicit M2M through table.

## leaves

### LeaveType

Physical table: `payroll_leave_type`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `code` | `varchar` | — | No | — |
| `name` | `varchar` | — | No | — |
| `description` | `text` | — | No | — |
| `default_balance_unit` | `varchar` | — | No | — |
| `requires_attachment_by_default` | `boolean` | — | No | — |
| `color` | `varchar` | — | Yes | — |
| `status` | `varchar` | — | No | — |

Constraint: unique `(company, code)`.
### LeavePolicy

Physical table: `payroll_leave_policy`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `code` | `text` | — | No | — |
| `name` | `text` | — | No | — |
| `description` | `text` | — | No | — |
| `branch_id` | `bigint` | FK | Yes | `organization_branch.id` |
| `department_id` | `bigint` | FK | Yes | `organization_department.id` |
| `is_company_default` | `boolean` | — | No | — |
| `status` | `varchar` | — | No | — |
| `current_version_id` | `bigint` | FK | Yes | `payroll_leave_policy_version.id` |

Constraints: department requires matching branch; only one applicable default per scope. Avoid a migration cycle by adding current_version after the initial version table or treating it as a cached pointer.
### LeavePolicyVersion

Physical table: `payroll_leave_policy_version`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `leave_policy_id` | `bigint` | FK | No | `payroll_leave_policy.id` |
| `version_number` | `integer` | — | No | — |
| `effective_from` | `date` | — | No | — |
| `effective_to` | `date` | — | Yes | — |
| `leave_year_start_month` | `integer` | — | No | — |
| `leave_year_start_day` | `integer` | — | No | — |
| `eligibility_defaults` | `jsonb` | — | No | — |
| `approval_route_definition` | `jsonb` | — | No | — |
| `lfa_enabled` | `boolean` | — | No | — |
| `lfa_configuration` | `jsonb` | — | Yes | — |
| `lfa_salary_component_id` | `bigint` | FK | Yes | `payroll_salary_component.id` |
| `status` | `varchar` | — | No | — |
| `activated_at` | `timestamptz` | — | Yes | — |
| `activated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |

Constraints: unique `(leave_policy, version_number)`; non-overlapping active effective periods. Freeze after use.
### LeavePolicyTypeRule

Physical table: `payroll_leave_policy_type_rule`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `policy_version_id` | `bigint` | FK | No | `payroll_leave_policy_version.id` |
| `leave_type_id` | `bigint` | FK | No | `payroll_leave_type.id` |
| `balance_unit` | `varchar` | — | No | — |
| `annual_allowance` | `numeric(18,6)` | — | No | — |
| `grant_method` | `varchar` | — | No | — |
| `accrual_frequency` | `varchar` | — | No | — |
| `accrual_amount` | `numeric(18,2)` | — | No | — |
| `joining_proration_method` | `varchar` | — | No | — |
| `minimum_service_days` | `integer` | — | No | — |
| `probation_wait_days` | `integer` | — | No | — |
| `minimum_request_units` | `numeric(18,6)` | — | Yes | — |
| `maximum_request_units` | `numeric(18,6)` | — | Yes | — |
| `maximum_consecutive_units` | `numeric(18,6)` | — | Yes | — |
| `minimum_notice_days` | `integer` | — | No | — |
| `allow_negative_balance` | `boolean` | — | No | — |
| `negative_balance_limit` | `numeric(18,6)` | — | No | — |
| `pay_type_default` | `varchar` | — | No | — |
| `pay_percentage_default` | `numeric(18,6)` | — | No | — |
| `approval_may_change_pay_percentage` | `boolean` | — | No | — |
| `count_weekly_offs` | `boolean` | — | No | — |
| `count_holidays` | `boolean` | — | No | — |
| `attachment_required_after_units` | `numeric(18,6)` | — | Yes | — |
| `carry_forward_enabled` | `boolean` | — | No | — |
| `carry_forward_limit` | `numeric(18,6)` | — | Yes | — |
| `carry_expiry_days` | `integer` | — | Yes | — |
| `encashment_enabled` | `boolean` | — | No | — |
| `encashment_limit` | `numeric(18,6)` | — | Yes | — |
| `encashment_rate_basis` | `varchar` | — | No | — |
| `compensatory_credit_enabled` | `boolean` | — | No | — |
| `expires_compensatory_after_days` | `integer` | — | Yes | — |
| `status` | `varchar` | — | No | — |

Constraint: unique `(policy_version, leave_type)`; percentages and limits valid.
### EmployeeLeavePolicyAssignment

Physical table: `payroll_employee_leave_policy_assignment`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `employee_id` | `bigint` | FK | No | `employees_employee.id` |
| `leave_policy_id` | `bigint` | FK | No | `payroll_leave_policy.id` |
| `effective_from` | `date` | — | No | — |
| `effective_to` | `date` | — | Yes | — |
| `reason` | `text` | — | No | — |
| `status` | `varchar` | — | No | — |

Constraint: no overlapping explicit employee policy assignments.
### LeaveEntitlement

Physical table: `payroll_leave_entitlement`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `employee_id` | `bigint` | FK | No | `employees_employee.id` |
| `leave_type_id` | `bigint` | FK | No | `payroll_leave_type.id` |
| `policy_version_id` | `bigint` | FK | No | `payroll_leave_policy_version.id` |
| `period_start` | `date` | — | No | — |
| `period_end` | `date` | — | No | — |
| `balance_unit` | `varchar` | — | No | — |
| `negative_balance_limit` | `numeric(18,6)` | — | No | — |
| `status` | `varchar` | — | No | — |
| `closed_at` | `timestamptz` | — | Yes | — |
| `closed_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `version` | `bigint` | — | No | — |

Constraint: unique entitlement for employee/type/period/policy context; overlapping accounts for the same type require explicit migration/reconciliation.
### LeaveBalanceEntry

Physical table: `payroll_leave_balance_entry`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `entitlement_id` | `bigint` | FK | No | `payroll_leave_entitlement.id` |
| `entry_kind` | `varchar` | — | No | — |
| `balance_delta` | `numeric(18,6)` | — | No | — |
| `reservation_delta` | `numeric(18,6)` | — | No | — |
| `effective_at` | `timestamptz` | — | No | — |
| `expires_at` | `timestamptz` | — | Yes | — |
| `source_grant_id` | `bigint` | FK | Yes | `payroll_leave_balance_entry.id` |
| `source_idempotency_key` | `varchar` | — | No | — |
| `leave_request_id` | `bigint` | FK | Yes | `payroll_leave_request.id` |
| `leave_day_id` | `bigint` | FK | Yes | `payroll_leave_day.id` |
| `leave_encashment_id` | `bigint` | FK | Yes | `payroll_leave_encashment.id` |
| `compensatory_credit_id` | `bigint` | FK | Yes | `payroll_leave_compensatory_credit.id` |
| `reversal_of_id` | `bigint` | FK/UQ | Yes | `payroll_leave_balance_entry.id` |
| `reason` | `text` | — | No | — |
| `posted_by_id` | `bigint` | FK | Yes | `accounts_user.id` |

Constraints: nonzero balance or reservation delta; unique source key; one reversal per entry; source fields consistent with kind.
### LeaveRequest

Physical table: `payroll_leave_request`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `public_id` | `uuid` | UQ | No | — |
| `employee_id` | `bigint` | FK | No | `employees_employee.id` |
| `submission_assignment_id` | `bigint` | FK | No | `employees_employee_assignment.id` |
| `policy_version_id` | `bigint` | FK | No | `payroll_leave_policy_version.id` |
| `action_type` | `varchar` | — | No | — |
| `original_request_id` | `bigint` | FK | Yes | `payroll_leave_request.id` |
| `supersedes_id` | `bigint` | FK | Yes | `payroll_leave_request.id` |
| `reason` | `text` | — | No | — |
| `status` | `varchar` | — | No | — |
| `submitted_at` | `timestamptz` | — | Yes | — |
| `submitted_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `decision_snapshot` | `jsonb` | — | No | — |
| `current_approval_stage` | `integer` | — | Yes | — |
| `decided_at` | `timestamptz` | — | Yes | — |

Constraints: amendment/cancellation needs original request with same company/employee; approved request requires segments and completed approvals.
### LeaveRequestSegment

Physical table: `payroll_leave_request_segment`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `leave_request_id` | `bigint` | FK | No | `payroll_leave_request.id` |
| `leave_type_id` | `bigint` | FK | No | `payroll_leave_type.id` |
| `duration_type` | `varchar` | — | No | — |
| `start_date` | `date` | — | No | — |
| `end_date` | `date` | — | No | — |
| `half_day_part` | `varchar` | — | Yes | — |
| `start_time` | `time` | — | Yes | — |
| `end_time` | `time` | — | Yes | — |
| `timezone` | `varchar` | — | No | — |
| `requested_units` | `numeric(18,6)` | — | No | — |
| `requested_minutes` | `integer` | — | No | — |
| `requested_pay_type` | `varchar` | — | No | — |
| `requested_pay_percentage` | `numeric(18,6)` | — | Yes | — |
| `original_segment_id` | `bigint` | FK | Yes | `payroll_leave_request_segment.id` |
| `sequence_number` | `integer` | — | No | — |
| `status` | `varchar` | — | No | — |

Constraints: duration-specific fields required; end not before start; no overlap with active segments for employee after expanding intervals.
### LeaveDay

Physical table: `payroll_leave_day`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `request_segment_id` | `bigint` | FK | No | `payroll_leave_request_segment.id` |
| `employee_id` | `bigint` | FK | No | `employees_employee.id` |
| `employee_assignment_id` | `bigint` | FK | No | `employees_employee_assignment.id` |
| `policy_type_rule_id` | `bigint` | FK | No | `payroll_leave_policy_type_rule.id` |
| `entitlement_id` | `bigint` | FK | Yes | `payroll_leave_entitlement.id` |
| `work_date` | `date` | — | No | — |
| `covered_start_at` | `timestamptz` | — | No | — |
| `covered_end_at` | `timestamptz` | — | No | — |
| `scheduled_minutes_snapshot` | `integer` | — | No | — |
| `leave_minutes` | `integer` | — | No | — |
| `balance_units` | `numeric(18,6)` | — | No | — |
| `approved_pay_type` | `varchar` | — | No | — |
| `approved_pay_percentage` | `numeric(18,6)` | — | No | — |
| `calendar_snapshot` | `jsonb` | — | No | — |
| `shift_snapshot` | `jsonb` | — | No | — |
| `status` | `varchar` | — | No | — |
| `supersedes_id` | `bigint` | FK | Yes | `payroll_leave_day.id` |
| `consumed_at` | `timestamptz` | — | Yes | — |

Constraints: covered interval positive and within relevant work date/shift; active approved intervals do not overlap; pay type matches percentage.
### LeaveApprovalStep

Physical table: `payroll_leave_approval_step`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `leave_request_id` | `bigint` | FK | Yes | `payroll_leave_request.id` |
| `leave_encashment_id` | `bigint` | FK | Yes | `payroll_leave_encashment.id` |
| `compensatory_credit_id` | `bigint` | FK | Yes | `payroll_leave_compensatory_credit.id` |
| `lfa_claim_id` | `bigint` | FK | Yes | `payroll_leave_fare_assistance_claim.id` |
| `stage_number` | `integer` | — | No | — |
| `stage_name` | `varchar` | — | No | — |
| `approver_membership_id` | `bigint` | FK | No | `accounts_company_membership.id` |
| `delegated_from_membership_id` | `bigint` | FK | Yes | `accounts_company_membership.id` |
| `status` | `varchar` | — | No | — |
| `decision` | `varchar` | — | Yes | — |
| `comment` | `text` | — | No | — |
| `decided_at` | `timestamptz` | — | Yes | — |
| `route_snapshot` | `jsonb` | — | No | — |

Constraints: exactly one target FK; unique target/stage/approver; decision fields required when decided.
### LeaveAttachment

Physical table: `payroll_leave_attachment`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `leave_request_id` | `bigint` | FK | Yes | `payroll_leave_request.id` |
| `lfa_claim_id` | `bigint` | FK | Yes | `payroll_leave_fare_assistance_claim.id` |
| `file` | `varchar` | — | No | — |
| `original_filename` | `varchar` | — | No | — |
| `content_type` | `varchar` | — | No | — |
| `size_bytes` | `bigint` | — | No | — |
| `checksum` | `varchar` | — | No | — |
| `document_type` | `varchar` | — | Yes | — |
| `description` | `text` | — | Yes | — |
| `uploaded_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `uploaded_at` | `timestamptz` | — | No | — |
| `status` | `varchar` | — | No | — |

Constraint: exactly one target.
### LeaveEncashment

Physical table: `payroll_leave_encashment`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `employee_id` | `bigint` | FK | No | `employees_employee.id` |
| `entitlement_id` | `bigint` | FK | No | `payroll_leave_entitlement.id` |
| `policy_version_id` | `bigint` | FK | No | `payroll_leave_policy_version.id` |
| `requested_units` | `numeric(18,6)` | — | No | — |
| `approved_units` | `numeric(18,6)` | — | No | — |
| `conversion_rate_snapshot` | `numeric(18,2)` | — | No | — |
| `calculated_amount` | `numeric(18,2)` | — | No | — |
| `approved_amount` | `numeric(18,2)` | — | No | — |
| `currency` | `varchar(3)` | — | No | — |
| `target_payroll_period_id` | `bigint` | FK | Yes | `payroll_period.id` |
| `reason` | `text` | — | No | — |
| `status` | `varchar` | — | No | — |
| `approved_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `approved_at` | `timestamptz` | — | Yes | — |
| `source_key` | `varchar` | — | No | — |
| `reversal_of_id` | `bigint` | FK | Yes | `payroll_leave_encashment.id` |

Constraint: approved units cannot exceed eligible available balance. Resulting payroll lines point back to this row, so reversals and off-cycle corrections remain possible.
### LeaveCompensatoryCredit

Physical table: `payroll_leave_compensatory_credit`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `employee_id` | `bigint` | FK | No | `employees_employee.id` |
| `source_attendance_id` | `bigint` | FK | No | `payroll_attendance_record.id` |
| `entitlement_id` | `bigint` | FK | No | `payroll_leave_entitlement.id` |
| `policy_type_rule_id` | `bigint` | FK | No | `payroll_leave_policy_type_rule.id` |
| `source_work_minutes` | `integer` | — | No | — |
| `approved_credit_units` | `numeric(18,6)` | — | No | — |
| `expires_at` | `timestamptz` | — | Yes | — |
| `treatment` | `varchar` | — | No | — |
| `status` | `varchar` | — | No | — |
| `approved_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `approved_at` | `timestamptz` | — | Yes | — |
| `balance_entry_id` | `bigint` | FK/UQ | Yes | `payroll_leave_balance_entry.id` |
| `source_key` | `varchar` | — | No | — |

Constraints: unique qualifying source/treatment; do not award overtime and leave together unless treatment explicitly permits it.
### LeaveFareAssistanceClaim

Physical table: `payroll_leave_fare_assistance_claim`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `employee_id` | `bigint` | FK | No | `employees_employee.id` |
| `employee_assignment_id` | `bigint` | FK | No | `employees_employee_assignment.id` |
| `policy_version_id` | `bigint` | FK | No | `payroll_leave_policy_version.id` |
| `qualifying_leave_request_id` | `bigint` | FK | Yes | `payroll_leave_request.id` |
| `compensation_id` | `bigint` | FK | Yes | `employees_employee_compensation.id` |
| `benefit_date` | `date` | — | No | — |
| `cycle_start` | `date` | — | No | — |
| `cycle_end` | `date` | — | No | — |
| `occurrence_number` | `integer` | — | No | — |
| `amount_method` | `varchar` | — | No | — |
| `amount_basis_snapshot` | `jsonb` | — | No | — |
| `base_amount_snapshot` | `numeric(18,2)` | — | No | — |
| `calculated_amount` | `numeric(18,2)` | — | No | — |
| `approved_amount` | `numeric(18,2)` | — | No | — |
| `currency` | `varchar(3)` | — | No | — |
| `target_payroll_period_id` | `bigint` | FK | Yes | `payroll_period.id` |
| `eligibility_snapshot` | `jsonb` | — | No | — |
| `reason` | `text` | — | No | — |
| `decision_reason` | `text` | — | No | — |
| `status` | `varchar` | — | No | — |
| `submitted_at` | `timestamptz` | — | Yes | — |
| `approved_at` | `timestamptz` | — | Yes | — |
| `posted_at` | `timestamptz` | — | Yes | — |
| `approved_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `idempotency_key` | `varchar` | — | No | — |
| `reversal_of_id` | `bigint` | FK | Yes | `payroll_leave_fare_assistance_claim.id` |

Constraints: same-company qualifying leave; policy eligibility; unique active occurrence slot per employee/benefit cycle. Resulting PayrollLine rows point back to the claim. LFA never changes LeaveEntitlement.

## payroll

### PayrollSettings

Physical table: `payroll_settings`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK/UQ | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `currency` | `varchar(3)` | — | No | — |
| `pay_frequency` | `varchar` | — | No | — |
| `period_start_day` | `integer` | — | No | — |
| `default_pay_day` | `integer` | — | Yes | — |
| `default_policy_version_id` | `bigint` | FK | Yes | `payroll_policy_version.id` |
| `default_salary_structure_id` | `bigint` | FK | Yes | `payroll_salary_structure.id` |
| `monthly_divisor` | `numeric(18,6)` | — | No | — |
| `overtime_enabled` | `boolean` | — | No | — |
| `bonus_enabled` | `boolean` | — | No | — |
| `advances_enabled` | `boolean` | — | No | — |
| `loans_enabled` | `boolean` | — | No | — |
| `auto_include_approved_overtime` | `boolean` | — | No | — |
| `auto_include_approved_leave` | `boolean` | — | No | — |
| `require_payroll_approval` | `boolean` | — | No | — |
| `allow_negative_net_pay` | `boolean` | — | No | — |
| `default_payment_method` | `varchar` | — | Yes | — |
| `settings_version` | `integer` | — | No | — |

Constraint: unique `company` (implement as O2O semantics).
### PayrollPolicyVersion

Physical table: `payroll_policy_version`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `name` | `varchar` | — | No | — |
| `code` | `varchar` | — | No | — |
| `version_number` | `integer` | — | No | — |
| `effective_from` | `date` | — | No | — |
| `effective_to` | `date` | — | Yes | — |
| `monthly_divisor` | `numeric(18,6)` | — | No | — |
| `monthly_proration_method` | `varchar` | — | No | — |
| `daily_rate_method` | `varchar` | — | No | — |
| `hourly_rate_method` | `varchar` | — | No | — |
| `joining_leaving_proration_method` | `varchar` | — | No | — |
| `absence_deduction_method` | `varchar` | — | No | — |
| `paid_leave_treatment` | `varchar` | — | No | — |
| `partial_leave_treatment` | `varchar` | — | No | — |
| `unpaid_leave_treatment` | `varchar` | — | No | — |
| `overtime_method` | `varchar` | — | No | — |
| `overtime_multiplier` | `numeric(18,6)` | — | No | — |
| `holiday_overtime_multiplier` | `numeric(18,6)` | — | No | — |
| `overtime_rounding_minutes` | `integer` | — | No | — |
| `minimum_overtime_minutes` | `integer` | — | No | — |
| `require_overtime_approval` | `boolean` | — | No | — |
| `late_penalty_stacking_method` | `varchar` | — | No | — |
| `maximum_period_deduction_percent` | `numeric(18,6)` | — | Yes | — |
| `maximum_recovery_percent_of_net` | `numeric(18,6)` | — | Yes | — |
| `money_rounding_mode` | `varchar` | — | No | — |
| `money_rounding_increment` | `numeric(18,6)` | — | No | — |
| `allow_negative_net_pay` | `boolean` | — | No | — |
| `calculation_config` | `jsonb` | — | No | — |
| `status` | `varchar` | — | No | — |
| `activated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `activated_at` | `timestamptz` | — | Yes | — |

Constraints: unique `(company, code, version_number)`; no overlapping active date ranges for the same code; activated versions are immutable.
### SalaryComponent

Physical table: `payroll_salary_component`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `code` | `varchar` | — | No | — |
| `name` | `varchar` | — | No | — |
| `category` | `varchar` | — | No | — |
| `subtype` | `varchar` | — | No | — |
| `calculation_method` | `varchar` | — | No | — |
| `default_amount` | `numeric(18,2)` | — | Yes | — |
| `default_rate` | `numeric(18,6)` | — | Yes | — |
| `default_percentage` | `numeric(18,6)` | — | Yes | — |
| `formula_expression` | `text` | — | No | — |
| `taxable` | `boolean` | — | No | — |
| `affects_gross` | `boolean` | — | No | — |
| `affects_net_pay` | `boolean` | — | No | — |
| `recurring` | `boolean` | — | No | — |
| `proratable` | `boolean` | — | No | — |
| `include_in_overtime_base` | `boolean` | — | No | — |
| `display_order` | `integer` | — | No | — |
| `external_code` | `varchar` | — | Yes | — |
| `is_active` | `boolean` | — | No | — |

Constraints: unique `(company, code)`; category and sign behavior must agree.
### SalaryStructure

Physical table: `payroll_salary_structure`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `code` | `varchar` | — | No | — |
| `name` | `varchar` | — | No | — |
| `description` | `text` | — | No | — |
| `version_number` | `integer` | — | No | — |
| `effective_from` | `date` | — | Yes | — |
| `effective_to` | `date` | — | Yes | — |
| `currency` | `varchar(3)` | — | No | — |
| `status` | `varchar` | — | No | — |
| `is_default` | `boolean` | — | No | — |
| `activated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `activated_at` | `timestamptz` | — | Yes | — |

Relations: components are M2M -> SalaryComponent through SalaryStructureComponent.

Constraints: unique `(company, code, version_number)`; at most one active default structure for a company/date; activated structures are immutable except retirement metadata.
### SalaryStructureComponent

Physical table: `payroll_salary_structure_component`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `salary_structure_id` | `bigint` | FK | No | `payroll_salary_structure.id` |
| `salary_component_id` | `bigint` | FK | No | `payroll_salary_component.id` |
| `calculation_method` | `varchar` | — | No | — |
| `fixed_amount` | `numeric(18,2)` | — | Yes | — |
| `rate` | `numeric(18,6)` | — | Yes | — |
| `percentage` | `numeric(18,6)` | — | Yes | — |
| `percentage_base_component_id` | `bigint` | FK | Yes | `payroll_salary_component.id` |
| `formula_expression` | `text` | — | No | — |
| `minimum_amount` | `numeric(18,2)` | — | Yes | — |
| `maximum_amount` | `numeric(18,2)` | — | Yes | — |
| `proration_method` | `varchar` | — | No | — |
| `rounding_mode` | `varchar` | — | Yes | — |
| `rounding_increment` | `numeric(18,6)` | — | Yes | — |
| `display_order` | `integer` | — | No | — |
| `is_mandatory` | `boolean` | — | No | — |
| `is_active` | `boolean` | — | No | — |

Constraints: unique `(salary_structure, salary_component)`; both belong to the same company; required operands depend on calculation method.
### EmployeeSalaryStructureAssignment

Physical table: `payroll_employee_salary_structure_assignment`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `employee_id` | `bigint` | FK | No | `employees_employee.id` |
| `salary_structure_id` | `bigint` | FK | No | `payroll_salary_structure.id` |
| `effective_from` | `date` | — | No | — |
| `effective_to` | `date` | — | Yes | — |
| `reason` | `text` | — | No | — |
| `status` | `varchar` | — | No | — |
| `approved_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `approved_at` | `timestamptz` | — | Yes | — |

Constraints: no overlapping active assignments for an employee; employee and structure belong to the same company.
### PayrollPeriod

Physical table: `payroll_period`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `code` | `varchar` | — | No | — |
| `name` | `varchar` | — | No | — |
| `frequency` | `varchar` | — | No | — |
| `period_start` | `date` | — | No | — |
| `period_end` | `date` | — | No | — |
| `attendance_cutoff_at` | `timestamptz` | — | Yes | — |
| `adjustment_cutoff_at` | `timestamptz` | — | Yes | — |
| `scheduled_pay_date` | `date` | — | Yes | — |
| `currency` | `varchar(3)` | — | No | — |
| `status` | `varchar` | — | No | — |
| `opened_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `opened_at` | `timestamptz` | — | Yes | — |
| `closed_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `closed_at` | `timestamptz` | — | Yes | — |
| `lock_reason` | `text` | — | No | — |

Constraints: unique `(company, code)`; `period_start <= period_end`; regular periods of the same frequency should not overlap.
### PayrollRun

Physical table: `payroll_run`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `payroll_period_id` | `bigint` | FK | No | `payroll_period.id` |
| `run_type` | `varchar` | — | No | — |
| `revision_number` | `integer` | — | No | — |
| `source_run_id` | `bigint` | FK | Yes | `payroll_run.id` |
| `policy_version_id` | `bigint` | FK | No | `payroll_policy_version.id` |
| `salary_structure_snapshot` | `jsonb` | — | Yes | — |
| `attendance_cutoff_at` | `timestamptz` | — | No | — |
| `leave_cutoff_at` | `timestamptz` | — | No | — |
| `adjustment_cutoff_at` | `timestamptz` | — | No | — |
| `idempotency_key` | `varchar` | — | No | — |
| `status` | `varchar` | — | No | — |
| `calculation_started_at` | `timestamptz` | — | Yes | — |
| `calculation_finished_at` | `timestamptz` | — | Yes | — |
| `generated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `approved_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `posted_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `approved_at` | `timestamptz` | — | Yes | — |
| `posted_at` | `timestamptz` | — | Yes | — |
| `failure_code` | `varchar` | — | No | — |
| `failure_message` | `text` | — | No | — |
| `totals_snapshot` | `jsonb` | — | No | — |

Constraints: unique `(company, idempotency_key)` and `(payroll_period, run_type, revision_number)`; only one posted regular result chain may be authoritative for an employee/period.
### PayrollRecord

Physical table: `payroll_record`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `payroll_run_id` | `bigint` | FK | No | `payroll_run.id` |
| `employee_id` | `bigint` | FK | No | `employees_employee.id` |
| `employee_assignment_at_period_end_id` | `bigint` | FK | Yes | `employees_employee_assignment.id` |
| `currency` | `varchar(3)` | — | No | — |
| `gross_earnings` | `numeric(18,2)` | — | No | — |
| `total_earnings` | `numeric(18,2)` | — | No | — |
| `total_deductions` | `numeric(18,2)` | — | No | — |
| `employer_contributions` | `numeric(18,2)` | — | No | — |
| `reimbursements` | `numeric(18,2)` | — | No | — |
| `net_pay` | `numeric(18,2)` | — | No | — |
| `paid_amount` | `numeric(18,2)` | — | No | — |
| `outstanding_amount` | `numeric(18,2)` | — | No | — |
| `calculation_snapshot` | `jsonb` | — | No | — |
| `status` | `varchar` | — | No | — |
| `approved_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `approved_at` | `timestamptz` | — | Yes | — |
| `posted_at` | `timestamptz` | — | Yes | — |
| `reversal_of_id` | `bigint` | FK/UQ | Yes | `payroll_record.id` |

Constraints: unique `(payroll_run, employee)`; employee belongs to run company; totals equal active line sums and allocations cannot exceed payable balance.
### PayrollCompensationSegment

Physical table: `payroll_compensation_segment`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `payroll_record_id` | `bigint` | FK | No | `payroll_record.id` |
| `employee_assignment_id` | `bigint` | FK | No | `employees_employee_assignment.id` |
| `employee_compensation_id` | `bigint` | FK | No | `employees_employee_compensation.id` |
| `salary_structure_assignment_id` | `bigint` | FK | Yes | `payroll_employee_salary_structure_assignment.id` |
| `policy_version_id` | `bigint` | FK | No | `payroll_policy_version.id` |
| `segment_start` | `date` | — | No | — |
| `segment_end` | `date` | — | No | — |
| `branch_id` | `bigint` | FK | No | `organization_branch.id` |
| `department_id` | `bigint` | FK | No | `organization_department.id` |
| `designation_id` | `bigint` | FK | No | `organization_designation.id` |
| `pay_basis` | `varchar` | — | No | — |
| `base_rate` | `numeric(18,6)` | — | Yes | — |
| `daily_rate` | `numeric(18,6)` | — | Yes | — |
| `hourly_rate` | `numeric(18,6)` | — | Yes | — |
| `currency` | `varchar(3)` | — | No | — |
| `monthly_divisor` | `numeric(18,6)` | — | No | — |
| `scheduled_minutes_per_day` | `integer` | — | No | — |
| `standard_minutes_in_period` | `integer` | — | No | — |
| `eligible_units` | `numeric(18,6)` | — | No | — |
| `payable_units` | `numeric(18,6)` | — | No | — |
| `base_earning_amount` | `numeric(18,2)` | — | No | — |
| `deduction_amount` | `numeric(18,2)` | — | No | — |
| `net_segment_amount` | `numeric(18,2)` | — | No | — |
| `calculation_details` | `jsonb` | — | No | — |

Constraints: segment dates must fall inside the period, cannot overlap for the same PayrollRecord, and referenced history rows must cover the segment.
### PayrollDailyLine

Physical table: `payroll_daily_line`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `payroll_segment_id` | `bigint` | FK | No | `payroll_compensation_segment.id` |
| `work_date` | `date` | — | No | — |
| `attendance_record_id` | `bigint` | FK | Yes | `payroll_attendance_record.id` |
| `holiday_id` | `bigint` | FK | Yes | `scheduling_holiday.id` |
| `weekly_off_rule_id` | `bigint` | FK | Yes | `scheduling_weekly_off_rule.id` |
| `holiday_work_assignment_id` | `bigint` | FK | Yes | `scheduling_holiday_work_assignment.id` |
| `day_type` | `varchar` | — | No | — |
| `attendance_status` | `varchar` | — | No | — |
| `scheduled_minutes` | `integer` | — | No | — |
| `worked_minutes` | `integer` | — | No | — |
| `approved_leave_minutes` | `integer` | — | No | — |
| `payable_minutes` | `integer` | — | No | — |
| `absence_minutes` | `integer` | — | No | — |
| `late_minutes` | `integer` | — | No | — |
| `early_out_minutes` | `integer` | — | No | — |
| `overtime_minutes` | `integer` | — | No | — |
| `paid_day_fraction` | `numeric(18,6)` | — | No | — |
| `unpaid_day_fraction` | `numeric(18,6)` | — | No | — |
| `payable_day_fraction` | `numeric(18,6)` | — | No | — |
| `base_earning_amount` | `numeric(18,2)` | — | No | — |
| `attendance_deduction_amount` | `numeric(18,2)` | — | No | — |
| `penalty_deduction_amount` | `numeric(18,2)` | — | No | — |
| `overtime_amount` | `numeric(18,2)` | — | No | — |
| `rate_snapshot` | `jsonb` | — | No | — |
| `calculation_details` | `jsonb` | — | No | — |
| `status` | `varchar` | — | No | — |

Constraints: unique `(payroll_segment, work_date)`; all evidence belongs to the employee/company/date; fractions are nonnegative and bounded where applicable.
### PayrollLine

Physical table: `payroll_line`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `payroll_record_id` | `bigint` | FK | No | `payroll_record.id` |
| `payroll_segment_id` | `bigint` | FK | Yes | `payroll_compensation_segment.id` |
| `payroll_daily_line_id` | `bigint` | FK | Yes | `payroll_daily_line.id` |
| `salary_component_id` | `bigint` | FK | No | `payroll_salary_component.id` |
| `source_type` | `varchar` | — | No | — |
| `penalty_assessment_id` | `bigint` | FK | Yes | `payroll_penalty_assessment.id` |
| `payroll_adjustment_id` | `bigint` | FK | Yes | `payroll_adjustment.id` |
| `salary_advance_recovery_id` | `bigint` | FK | Yes | `payroll_salary_advance_recovery.id` |
| `loan_repayment_id` | `bigint` | FK | Yes | `payroll_loan_repayment.id` |
| `leave_encashment_id` | `bigint` | FK | Yes | `payroll_leave_encashment.id` |
| `lfa_claim_id` | `bigint` | FK | Yes | `payroll_leave_fare_assistance_claim.id` |
| `description` | `text` | — | No | — |
| `service_date` | `date` | — | Yes | — |
| `service_period_start` | `date` | — | Yes | — |
| `service_period_end` | `date` | — | Yes | — |
| `quantity` | `numeric(18,6)` | — | Yes | — |
| `rate` | `numeric(18,6)` | — | Yes | — |
| `percentage` | `numeric(18,6)` | — | Yes | — |
| `amount` | `numeric(18,2)` | — | No | — |
| `direction` | `varchar` | — | No | — |
| `currency` | `varchar(3)` | — | No | — |
| `formula_snapshot` | `jsonb` | — | No | — |
| `calculation_details` | `jsonb` | — | No | — |
| `taxable` | `boolean` | — | No | — |
| `is_manual` | `boolean` | — | No | — |
| `is_system_generated` | `boolean` | — | No | — |
| `original_line_id` | `bigint` | FK | Yes | `payroll_line.id` |
| `status` | `varchar` | — | No | — |

Constraints: source fields must agree with `source_type`; at most one typed source for a normal line; prevent duplicate active posting from the same source/component/run; reversal lines reference an original and use the opposite financial effect.
### PayrollApprovalStep

Physical table: `payroll_approval_step`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `payroll_run_id` | `bigint` | FK | No | `payroll_run.id` |
| `sequence` | `integer` | — | No | — |
| `required_permission_id` | `bigint` | FK | Yes | `access_control_access_permission.id` |
| `assigned_designation_id` | `bigint` | FK | Yes | `organization_designation.id` |
| `assigned_user_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `acted_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `status` | `varchar` | — | No | — |
| `decision_note` | `text` | — | No | — |
| `acted_at` | `timestamptz` | — | Yes | — |
| `due_at` | `timestamptz` | — | Yes | — |

Constraints: unique `(payroll_run, sequence)`; assignee must have company access; steps execute in order unless policy explicitly permits parallel stages.
### SalaryPayment

Physical table: `payroll_salary_payment`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `employee_id` | `bigint` | FK | No | `employees_employee.id` |
| `payment_reference` | `varchar` | — | No | — |
| `payment_date` | `date` | — | No | — |
| `amount` | `numeric(18,2)` | — | No | — |
| `currency` | `varchar(3)` | — | No | — |
| `payment_method` | `varchar` | — | No | — |
| `provider_name` | `varchar` | — | Yes | — |
| `provider_reference` | `varchar` | — | Yes | — |
| `bank_account_snapshot` | `jsonb` | — | No | — |
| `status` | `varchar` | — | No | — |
| `processed_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `processed_at` | `timestamptz` | — | Yes | — |
| `failure_reason` | `text` | — | No | — |
| `idempotency_key` | `varchar` | — | No | — |
| `reversal_of_id` | `bigint` | FK/UQ | Yes | `payroll_salary_payment.id` |

Constraints: unique `(company, payment_reference)` and `(company, idempotency_key)`; allocated amount cannot exceed completed payment amount.
### SalaryPaymentAllocation

Physical table: `payroll_salary_payment_allocation`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `salary_payment_id` | `bigint` | FK | No | `payroll_salary_payment.id` |
| `payroll_record_id` | `bigint` | FK | No | `payroll_record.id` |
| `allocated_amount` | `numeric(18,2)` | — | No | — |
| `allocated_at` | `timestamptz` | — | No | — |
| `allocation_reference` | `varchar` | — | No | — |
| `status` | `varchar` | — | No | — |
| `reversal_of_id` | `bigint` | FK/UQ | Yes | `payroll_salary_payment_allocation.id` |

Constraints: unique `(company, allocation_reference)`; payment and payroll record belong to the same employee/company/currency; active allocations cannot overallocate either side.
### SalaryAdvance

Physical table: `payroll_salary_advance`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `employee_id` | `bigint` | FK | No | `employees_employee.id` |
| `request_reference` | `varchar` | — | No | — |
| `requested_amount` | `numeric(18,2)` | — | No | — |
| `approved_amount` | `numeric(18,2)` | — | No | — |
| `currency` | `varchar(3)` | — | No | — |
| `requested_on` | `date` | — | Yes | — |
| `approved_on` | `date` | — | Yes | — |
| `reason` | `text` | — | No | — |
| `recovery_method` | `varchar` | — | No | — |
| `installment_count` | `integer` | — | Yes | — |
| `installment_amount` | `numeric(18,2)` | — | Yes | — |
| `first_recovery_period_id` | `bigint` | FK | Yes | `payroll_period.id` |
| `maximum_recovery_percent` | `numeric(18,6)` | — | Yes | — |
| `status` | `varchar` | — | No | — |
| `approved_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `outstanding_amount` | `numeric(18,2)` | — | No | — |

Constraints: unique `(company, request_reference)`; approved amount cannot exceed requested amount without explicit override; outstanding derives from disbursements minus applied recoveries/reversals.
### AdvanceDisbursement

Physical table: `payroll_advance_disbursement`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `salary_advance_id` | `bigint` | FK | No | `payroll_salary_advance.id` |
| `disbursement_reference` | `varchar` | — | No | — |
| `disbursed_at` | `timestamptz` | — | No | — |
| `amount` | `numeric(18,2)` | — | No | — |
| `currency` | `varchar(3)` | — | No | — |
| `payment_method` | `varchar` | — | No | — |
| `provider_reference` | `varchar` | — | No | — |
| `status` | `varchar` | — | No | — |
| `processed_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `reversal_of_id` | `bigint` | FK/UQ | Yes | `payroll_advance_disbursement.id` |

Constraints: unique `(company, disbursement_reference)`; completed active disbursements cannot exceed approved advance amount.
### SalaryAdvanceRecovery

Physical table: `payroll_salary_advance_recovery`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `salary_advance_id` | `bigint` | FK | No | `payroll_salary_advance.id` |
| `payroll_period_id` | `bigint` | FK | Yes | `payroll_period.id` |
| `recovery_date` | `date` | — | No | — |
| `scheduled_amount` | `numeric(18,2)` | — | No | — |
| `recovered_amount` | `numeric(18,2)` | — | No | — |
| `currency` | `varchar(3)` | — | No | — |
| `method` | `varchar` | — | No | — |
| `external_reference` | `varchar` | — | Yes | — |
| `status` | `varchar` | — | No | — |
| `idempotency_key` | `varchar` | — | No | — |
| `reversal_of_id` | `bigint` | FK/UQ | Yes | `payroll_salary_advance_recovery.id` |

Relations: PayrollLine points to this row when method is payroll_deduction.

Constraints: unique `(company, idempotency_key)`; posted/received recoveries cannot exceed outstanding principal unless explicitly treated as a refundable overpayment.
### EmployeeLoan

Physical table: `payroll_employee_loan`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `employee_id` | `bigint` | FK | No | `employees_employee.id` |
| `loan_reference` | `varchar` | — | No | — |
| `loan_type` | `varchar` | — | No | — |
| `requested_principal` | `numeric(18,2)` | — | No | — |
| `approved_principal` | `numeric(18,2)` | — | No | — |
| `currency` | `varchar(3)` | — | No | — |
| `interest_method` | `varchar` | — | No | — |
| `annual_interest_rate` | `numeric(18,6)` | — | Yes | — |
| `flat_interest_amount` | `numeric(18,2)` | — | Yes | — |
| `term_installments` | `integer` | — | No | — |
| `installment_frequency` | `varchar` | — | No | — |
| `first_due_date` | `date` | — | No | — |
| `grace_periods` | `integer` | — | No | — |
| `maximum_recovery_percent` | `numeric(18,6)` | — | Yes | — |
| `reason` | `text` | — | No | — |
| `terms_snapshot` | `jsonb` | — | No | — |
| `status` | `varchar` | — | No | — |
| `approved_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `approved_at` | `timestamptz` | — | Yes | — |
| `principal_outstanding` | `numeric(18,2)` | — | No | — |
| `interest_outstanding` | `numeric(18,2)` | — | No | — |

Constraints: unique `(company, loan_reference)`; balances reconcile to completed disbursements, repayments, and approved waivers/write-offs.
### LoanDisbursement

Physical table: `payroll_loan_disbursement`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `employee_loan_id` | `bigint` | FK | No | `payroll_employee_loan.id` |
| `disbursement_reference` | `varchar` | — | No | — |
| `disbursed_at` | `timestamptz` | — | No | — |
| `principal_amount` | `numeric(18,2)` | — | No | — |
| `currency` | `varchar(3)` | — | No | — |
| `payment_method` | `varchar` | — | No | — |
| `provider_reference` | `varchar` | — | No | — |
| `status` | `varchar` | — | No | — |
| `processed_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `reversal_of_id` | `bigint` | FK/UQ | Yes | `payroll_loan_disbursement.id` |

Constraints: unique `(company, disbursement_reference)`; completed disbursements cannot exceed approved principal.
### LoanInstallment

Physical table: `payroll_loan_installment`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `employee_loan_id` | `bigint` | FK | No | `payroll_employee_loan.id` |
| `installment_number` | `integer` | — | No | — |
| `due_date` | `date` | — | No | — |
| `payroll_period_id` | `bigint` | FK | Yes | `payroll_period.id` |
| `opening_principal` | `numeric(18,2)` | — | No | — |
| `principal_due` | `numeric(18,2)` | — | No | — |
| `interest_due` | `numeric(18,2)` | — | No | — |
| `fee_due` | `numeric(18,2)` | — | No | — |
| `total_due` | `numeric(18,2)` | — | No | — |
| `paid_principal` | `numeric(18,2)` | — | No | — |
| `paid_interest` | `numeric(18,6)` | — | No | — |
| `paid_fee` | `numeric(18,6)` | — | No | — |
| `status` | `varchar` | — | No | — |
| `deferred_to_id` | `bigint` | FK | Yes | `payroll_loan_installment.id` |
| `calculation_snapshot` | `jsonb` | — | No | — |

Constraints: unique `(employee_loan, installment_number)`; component totals reconcile to total due; paid summaries derive from active allocations.
### LoanRepayment

Physical table: `payroll_loan_repayment`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `employee_loan_id` | `bigint` | FK | No | `payroll_employee_loan.id` |
| `payroll_period_id` | `bigint` | FK | Yes | `payroll_period.id` |
| `repayment_reference` | `varchar` | — | No | — |
| `repayment_date` | `date` | — | No | — |
| `amount` | `numeric(18,2)` | — | No | — |
| `currency` | `varchar(3)` | — | No | — |
| `method` | `varchar` | — | No | — |
| `status` | `varchar` | — | No | — |
| `provider_reference` | `varchar` | — | Yes | — |
| `idempotency_key` | `varchar` | — | No | — |
| `reversal_of_id` | `bigint` | FK/UQ | Yes | `payroll_loan_repayment.id` |

Relations: PayrollLine points to this row when method is payroll_deduction.

Constraints: unique `(company, repayment_reference)` and `(company, idempotency_key)`; allocations cannot exceed the active repayment amount.
### LoanRepaymentAllocation

Physical table: `payroll_loan_repayment_allocation`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `loan_repayment_id` | `bigint` | FK | No | `payroll_loan_repayment.id` |
| `loan_installment_id` | `bigint` | FK | No | `payroll_loan_installment.id` |
| `principal_amount` | `numeric(18,2)` | — | No | — |
| `interest_amount` | `numeric(18,2)` | — | No | — |
| `fee_amount` | `numeric(18,2)` | — | No | — |
| `allocated_at` | `timestamptz` | — | No | — |
| `status` | `varchar` | — | No | — |
| `reversal_of_id` | `bigint` | FK/UQ | Yes | `payroll_loan_repayment_allocation.id` |

Constraints: repayment and installment belong to the same loan/company/currency; active allocations cannot exceed the repayment or remaining installment balances.
### PayrollAdjustment

Physical table: `payroll_adjustment`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `employee_id` | `bigint` | FK | No | `employees_employee.id` |
| `adjustment_reference` | `varchar` | — | No | — |
| `salary_component_id` | `bigint` | FK | Yes | `payroll_salary_component.id` |
| `target_payroll_period_id` | `bigint` | FK | Yes | `payroll_period.id` |
| `adjustment_type` | `varchar` | — | No | — |
| `amount` | `numeric(18,2)` | — | No | — |
| `currency` | `varchar(3)` | — | No | — |
| `service_date` | `date` | — | Yes | — |
| `reason` | `text` | — | No | — |
| `source_document_reference` | `varchar` | — | Yes | — |
| `affects_payroll` | `boolean` | — | No | — |
| `affects_advance_balance` | `boolean` | — | No | — |
| `affects_loan_balance` | `boolean` | — | No | — |
| `salary_advance_id` | `bigint` | FK | Yes | `payroll_salary_advance.id` |
| `employee_loan_id` | `bigint` | FK | Yes | `payroll_employee_loan.id` |
| `status` | `varchar` | — | No | — |
| `approved_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `approved_at` | `timestamptz` | — | Yes | — |
| `posted_at` | `timestamptz` | — | Yes | — |
| `idempotency_key` | `varchar` | — | No | — |
| `reversal_of_id` | `bigint` | FK/UQ | Yes | `payroll_adjustment.id` |

Constraints: unique `(company, adjustment_reference)` and `(company, idempotency_key)`; target/source fields agree with adjustment type; a balance-only waiver must not silently create salary earnings.
### PayrollRemittance

Physical table: `payroll_remittance`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `payroll_period_id` | `bigint` | FK | No | `payroll_period.id` |
| `provider_type` | `varchar` | — | No | — |
| `provider_name` | `varchar` | — | No | — |
| `provider_account_reference` | `varchar` | — | No | — |
| `remittance_reference` | `varchar` | — | No | — |
| `remittance_date` | `date` | — | Yes | — |
| `amount` | `numeric(18,2)` | — | No | — |
| `currency` | `varchar(3)` | — | No | — |
| `payment_method` | `varchar` | — | Yes | — |
| `provider_transaction_reference` | `varchar` | — | Yes | — |
| `status` | `varchar` | — | No | — |
| `approved_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `processed_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `approved_at` | `timestamptz` | — | Yes | — |
| `processed_at` | `timestamptz` | — | Yes | — |
| `idempotency_key` | `varchar` | — | No | — |
| `reversal_of_id` | `bigint` | FK/UQ | Yes | `payroll_remittance.id` |

Constraints: unique `(company, remittance_reference)` and `(company, idempotency_key)`; active allocations cannot exceed a completed remittance.
### PayrollRemittanceAllocation

Physical table: `payroll_remittance_allocation`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `payroll_remittance_id` | `bigint` | FK | No | `payroll_remittance.id` |
| `payroll_line_id` | `bigint` | FK | No | `payroll_line.id` |
| `allocated_amount` | `numeric(18,2)` | — | No | — |
| `allocated_at` | `timestamptz` | — | No | — |
| `status` | `varchar` | — | No | — |
| `reversal_of_id` | `bigint` | FK/UQ | Yes | `payroll_remittance_allocation.id` |

Constraints: remittance and line must have the same company/currency/provider-compatible component; active allocations cannot exceed either available remittance value or line liability.
### PayrollDailyLine_leave_days

Physical table: `payroll_daily_line_leave_days`. Implicit M2M junction.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `payrolldailyline_id` | `bigint` | FK | No | `payroll_daily_line.id` |
| `leaveday_id` | `bigint` | FK | No | `payroll_leave_day.id` |

Unique pair; both rows must belong to the same company. Tenant ownership is inherited from the source parent; this implicit junction has no company_id.

## subscriptions

### Package

Physical table: `subscriptions_package`. Global/platform table; see company field if present.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `code` | `varchar` | UQ | No | — |
| `name` | `varchar` | UQ | No | — |
| `description` | `text` | — | No | — |
| `billing_interval` | `varchar` | — | No | — |
| `price` | `numeric(18,2)` | — | No | — |
| `currency` | `varchar(3)` | — | No | — |
| `included_employee_limit` | `integer` | — | Yes | — |
| `included_user_limit` | `integer` | — | Yes | — |
| `device_limit` | `integer` | — | Yes | — |
| `branch_limit` | `integer` | — | Yes | — |
| `trial_days` | `integer` | — | No | — |
| `is_public` | `boolean` | — | No | — |
| `is_active` | `boolean` | — | No | — |
| `display_order` | `integer` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |

Relations: features are M2M -> Feature through PackageFeature; CompanySubscription references Package.
### PackageFeature

Physical table: `subscriptions_package_feature`. Global/platform table; see company field if present.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `package_id` | `bigint` | FK | No | `subscriptions_package.id` |
| `feature_id` | `bigint` | FK | No | `tenants_feature.id` |
| `is_included` | `boolean` | — | No | — |
| `usage_limit` | `bigint` | — | Yes | — |
| `configuration` | `jsonb` | — | No | — |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |

Constraint: unique `(package, feature)`.
### CompanySubscription

Physical table: `subscriptions_company_subscription`. Direct tenant owner: `company_id`.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | No | `tenants_company.id` |
| `created_at` | `timestamptz` | — | No | — |
| `updated_at` | `timestamptz` | — | No | — |
| `created_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `updated_by_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `package_id` | `bigint` | FK | No | `subscriptions_package.id` |
| `subscription_reference` | `varchar` | — | No | — |
| `starts_at` | `timestamptz` | — | No | — |
| `ends_at` | `timestamptz` | — | No | — |
| `trial_ends_at` | `timestamptz` | — | Yes | — |
| `cancelled_at` | `timestamptz` | — | Yes | — |
| `status` | `varchar` | — | No | — |
| `billing_interval` | `varchar` | — | No | — |
| `price_snapshot` | `numeric(18,2)` | — | No | — |
| `currency` | `varchar(3)` | — | No | — |
| `employee_limit_snapshot` | `integer` | — | Yes | — |
| `user_limit_snapshot` | `integer` | — | Yes | — |
| `device_limit_snapshot` | `integer` | — | Yes | — |
| `branch_limit_snapshot` | `integer` | — | Yes | — |
| `feature_snapshot` | `jsonb` | — | No | — |
| `external_customer_id` | `varchar` | — | Yes | — |
| `external_subscription_id` | `varchar` | — | Yes | — |
| `auto_renew` | `boolean` | — | No | — |
| `cancellation_reason` | `text` | — | No | — |

Constraints: unique `(company, subscription_reference)`; normally no overlapping active/trial subscriptions; entitlements use the subscription snapshot plus explicit CompanyFeature overrides. Capacity counting policy must define whether employees, users, or both are billable—the current recommendation is active employees.

## auditlog

### AuditLog

Physical table: `auditlog_audit_log`. Global/platform table; see company field if present.

| Column | PostgreSQL type | Key | Nullable | References |
|---|---|---|---|---|
| `id` | `bigint` | PK | No | — |
| `company_id` | `bigint` | FK | Yes | `tenants_company.id` |
| `actor_user_id` | `bigint` | FK | Yes | `accounts_user.id` |
| `actor_membership_id` | `bigint` | FK | Yes | `accounts_company_membership.id` |
| `actor_type` | `varchar` | — | No | — |
| `action` | `varchar` | — | No | — |
| `object_app` | `varchar` | — | No | — |
| `object_model` | `varchar` | — | No | — |
| `object_id` | `varchar` | — | No | — |
| `object_public_id` | `varchar` | — | No | — |
| `object_display` | `varchar` | — | No | — |
| `request_id` | `varchar` | — | Yes | — |
| `correlation_id` | `varchar` | — | Yes | — |
| `idempotency_key` | `varchar` | — | Yes | — |
| `ip_address` | `inet` | — | Yes | — |
| `user_agent` | `text` | — | No | — |
| `before_data` | `jsonb` | — | No | — |
| `after_data` | `jsonb` | — | No | — |
| `metadata` | `jsonb` | — | No | — |
| `occurred_at` | `timestamptz` | — | No | — |
| `integrity_hash` | `varchar` | — | Yes | — |
| `previous_hash` | `varchar` | — | Yes | — |
