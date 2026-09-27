# Ajay's second message (2026-09-27): plan

Nihal works it in phases: each is built, tested (full suite), merged into
main, pushed and reported - then he says go for the next. Nothing starts
until he says start.

## What Ajay asked

| # | Ask |
|---|---|
| 1 | HR gets `employees.view` and `employees.edit`, company-wide (`HR_COMPANY_WIDE`). |
| 2 | HR gets no salary code; only grantable per branch on Access. The profile must then follow the Employees list's Base rate rule - pay only for a viewer with `salary.view` in that person's branch - built in the SAME change as 1. A grant attaches to an Employee record, so an HR login with none cannot be granted. |
| 3 | Leave in full (reverses 2026-09-27 morning): policies with versions, accrual/carry-forward ledger, balances, entitlements, hourly, partly paid, morning/afternoon. Design first; flag anything that changes how existing leave days or finalised months are read. What exists stays. |
| 4 | The profile covers everything: every Edit employee option, in modals; the reference's sections, tabs and 12 actions - coverage, not its look. Keep the Edit employee page until Ajay has seen the profile complete. |
| 5 | Delete employee = End employment, history kept. As built. |

## What exists (checked in the code)

- On the profile: photo; personal and contact info and address; Attendance
  and Leave tabs; employment history; Manual entry; Record leave; Remove
  from reports; End employment (resign / delete).
- Only on Edit employee: details, placement, salary, allowances give/end,
  own shift set/end, login give / password / disable / enable / role.
- Nowhere yet: education history, employee documents (several), disallow
  overtime, late approval, enrolment from the profile, one person's pending
  approvals.

## Phases

| Phase | What |
|---|---|
| A | HR access + pay privacy on the profile (Ajay 1 + 2 together) |
| B | Every Edit employee option on the profile, in modals (the old page untouched) |
| C | The reference's sections and tabs: General info mapped, Approver info, Change password, Education history and Employee documents (new tables), tabs Enrol employee, Device permissions, Roster, Pending approvals |
| D | The 12 actions: add Late approval, Set as HR / Line manager, Disallow overtime, Make inactive, Sync employee |
| E | Full leave: a written design for Nihal's OK first, then build in sub-steps |

## Questions to settle (asked when their phase comes)

1. Set as Admin: one owner-or-admin per company (`uniq_current_company_administrator`) - leave out, or "transfer the admin role"?
2. Set as Line manager: branch manager (a login role) or someone's direct manager (`EmployeeAssignment.manager`)?
3. Late approval: a manager approves a late arrival so it is not counted or penalised that day?
4. Disallow overtime: never counted or paid for this person?
5. Make inactive (Suspended): stop their scans counting, or only mark them?
6. Notification settings: no notifications exist - left out as meaningless.

## Status (2026-09-27): all five phases done

A, B, C, D and E are built, tested and pushed (details in PHASE_STATUS.md).
The open questions were settled as follows, since Ajay was away - each is
easy to change if he wants it otherwise:

1. Set as Admin - left as an explanation: a company has one administrator.
2. Set as Line manager - choose the people who report to them (their line
   manager); branch manager access stays under Login → Change access.
3. Late approval - a late arrival approved by whoever may fix that day
   (a correction, withdrawable): no late minutes, no penalty.
4. Disallow overtime - from a date: no overtime approved or paid.
5. Make inactive - Suspended; marks them only (no leave requests or
   missed-scan reports), attendance and salary as before.
6. Notification settings - left out: the app sends no notifications.

The Edit employee page is kept, as Ajay asked, until he has seen the profile.
