# A12 part 7 — branch access for Nihal's pages

Written 2026-09-15 by Ajay's session. A12 parts 1–6 are on main: branch
managers and people given access now use the Employees, Leave, Overtime and
Salary pages for **their branches only**. This note says how to do the same for
the attendance pages and the N6 employee page. It is the answer to "should my
pages get permission codes now?" — yes, following this note, in your own branch.

## 1. The rules (Ajay, 2026-09-15)

1. **The company keeps all access in every branch.** Owner and company admin
   see and do everything, exactly as today. A branch without a branch manager
   is handled by the company.
2. **A branch manager controls their branches**: they hold every branch
   permission there automatically, and can hand any of it to others on
   Organisation → Access.
3. **HR keeps what it has today, company-wide** (leave recording, overtime,
   and — with this note — viewing and fixing attendance).
4. **Other company roles** (payroll manager, auditor) keep today's behaviour;
   do not narrow them. Only the Employee and Branch-manager logins
   (`common.middleware.SELF_SERVICE_ROLES`) are branch-limited.
5. **Devices stay with the owner and company admin** (unrestricted), as
   `devices/services/panel_access.py` says: a device's settings reach the
   whole terminal. No device page is opened to branches in A12.

## 2. The tools (already on main)

`access_control/branch_access.py`

| Function | Use |
|---|---|
| `branches_for(user, company_id, code)` | `ALL_BRANCHES` (owner, admin, HR for its codes) or a `set` of branch ids. Empty set = no access. |
| `branches_for_any(user, company_id, *codes)` | Same, for "any of these codes". |
| `can(user, company_id, code, branch_id=None)` | One branch, or "anywhere" when `branch_id` is None. |
| `require(...)` | `can` or `PermissionDenied`. |
| `scope_queryset(qs, user, company_id, code, field="branch")` | Filters `qs` to those branches (no filter for `ALL_BRANCHES`). |

`access_control/page_access.py` — `BRANCH_PAGES = {view name: code or (codes…)}`.
Listing a page there makes `SelfServiceGate` let a branch login in (with the
code in at least one branch), and the sidebar's "Company" section shows its
menu entry. **List a page only when it shows nothing outside the viewer's
branches, and have the service re-check** (a crafted POST must fail too).

`organization/access_services.people(branches)` — employees whose latest
placement is in `branches` (annotated `table_branch_id`); handy for employee
pickers.

`organization/employee_edit_services.get_employee_for_edit(..., code=...)` —
with a code, allows anyone holding that code in the employee's current
branch (owner/admin always). Without a code it is the old owner/admin rule.

Worked examples to copy: `leaves/views._list_scope` (company roles
unchanged, branch logins scoped), `payroll/overtime.overtime_scope`,
`payroll/services.salary_branches`.

## 3. Two new permission codes (add them in your branch)

Add to `BRANCH_PERMISSIONS` in `access_control/branch_access.py`, together with
the pages that use them (so no tick on the Access page does nothing):

```python
("attendance.view", "View attendance", "attendance", "view"),
("attendance.fix", "Fix attendance days", "attendance", "edit"),
```

and add both to `HR_COMPANY_WIDE` (HR already sees and fixes attendance
company-wide through `attendance.correction_services.CORRECTION_ROLES`). No
migration: the `AccessPermission` row is created the first time someone is
given the code. The Access page grid picks the new rows up by itself.

## 4. Page by page

| Page (view name) | `BRANCH_PAGES` code | What to limit |
|---|---|---|
| Daily list `attendance:attendance_list` | `attendance.view` | `scope_queryset(..., "attendance.view")` on the records (field `branch`); Branch dropdown → only those branches; Employee dropdown → `people(branches)`. |
| Calendar `attendance:attendance_calendar` | `attendance.view` | Employee picker → `people(branches)`; a requested employee outside them → 403 (or fall back to the first allowed). |
| Day panel `attendance:attendance_day` | `attendance.view` | 403 unless the record's `branch_id` is in the branches (no record: the employee's placement on that date). `may_correct` → `can(user, company, "attendance.fix", that branch)`. |
| Now `attendance:attendance_now` | `("employees.view", "attendance.view")` | Only return statuses for employees in `people(branches_for_any(user, company, "employees.view", "attendance.view"))`; drop other requested ids. The Employees list already shows "Now" for its rows, so `employees.view` is enough. |
| Days to review `attendance:attendance_review` | `attendance.fix` | `review_queue(company_id, branches)` → filter `branch_id__in` unless `ALL_BRANCHES`. |
| Fix a day `attendance:attendance_day_fix` | `attendance.fix` | `attendance.fix` in the day's branch (record, or placement on that date). `add_scan`, `change_status`, `accept_review` must check it in the service (replace `require_corrector` with a branch-aware check). |
| Withdraw `attendance:attendance_correction_withdraw` | `attendance.fix` | `attendance.fix` in the corrected day's branch; `withdraw()` checks it too. |
| Employee page `organization:employee_detail` | `employees.view` | `get_employee_for_edit(code="employees.view")`. Salary history (`salaries`, `compensation`) only with `can(user, company, "salary.view", branch)`; hide salary events in the audit list otherwise. Own shifts, devices and login are read-only there. |
| End employment `organization:employee_end` | `employees.edit` | `get_employee_for_edit(code="employees.edit")` in `end_employment` too. Refuse ending a branch manager's (or the actor's own) employment for non-company actors — managers' logins are the company's. |
| Devices (every `devices:*` page) | — | Not listed. Unchanged. |
| Dashboard device alert | — | Unchanged (company only). |

Menu entries need nothing: `base_template/navigation.py` already has them, and
the "Company" section shows an entry once its view is in `BRANCH_PAGES`.

## 5. Links on my pages switch on by themselves

Since A12 part 7 these links check `may_open`, so they appear for a branch
login as soon as your view is listed (owner/admin see them as before).

- Employees list: the live "Now" refresh (`attendance:attendance_now`) and the
  name → employee page link (`organization:employee_detail`).
- Overtime day page and payslip: "Open in the attendance calendar" / "Calendar
  view" (`attendance:attendance_calendar`).

## 6. Tests

Use `leaves/tests_branch_access.TwoBranchCase`: Head Office (Rahim, Clerk,
branch manager Manny) and Chittagong (Karim, with its own device and
`far_punch`), plus HR and `grant(code, *branches, to=employee)`. Per page:
branch manager sees own branch only; a person with the code in the other
branch sees that branch only; without the code the gate sends them to
My account (GET) or 403 (POST); owner and HR unchanged; a crafted POST for
another branch is refused by the service. Run the full suite before
reporting (a new code changes the Access page grid).

## 7. Order and hand-back

One branch is fine (for example `feature/n10-branch-attendance`), from fresh
main, pushed only. No migration expected. Report the branch name and anything
you changed in `access_control/`; Ajay's session reviews and merges.
Afterwards Ajay's session will retire **My branches → Branch attendance**
(`me:branch_attendance`, A8) in favour of the scoped Daily list, if Ajay
agrees.
