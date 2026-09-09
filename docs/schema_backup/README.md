# Schema backup — before root-owned department/designation catalogue

Snapshot taken on branch `department_designation_relation_companywise`, from
`feature/company-org-setup` at commit a5f59f9, immediately before the change
that moved `Department` and `Designation` to root ownership and introduced the
per-company link tables `CompanyDepartment` and `CompanyDesignation`.

## What the schema looked like at this point

* `organization.Department` was **tenant-owned** — every company created its own
  rows, unique per branch by code and by name.
* `organization.Designation` was **tenant-owned**, belonged to a Department, and
  carried a self-referencing `parent` plus a derived `hierarchy_level`.
* Access ceilings were enforced by walking the `Designation.parent` chain in
  `access_control.DesignationPermission.clean()`.

## What changed after this snapshot

* `Department` and `Designation` became **root-owned global catalogues**. No
  company can write to them.
* `CompanyDepartment` and `CompanyDesignation` link a company (and branch) to a
  catalogue row. Everything company-specific — employees, shifts, permissions,
  scopes — points at the link row, never at the catalogue row.
* The designation hierarchy (`parent`, `hierarchy_level`) was removed. The
  access ceiling now comes from the department via
  `access_control.DepartmentPermission`, and each `CompanyDepartment` has a
  `head` who administers permissions inside it.

The files in this folder are a frozen copy. The live schema documents in
`docs/` describe the current design.
