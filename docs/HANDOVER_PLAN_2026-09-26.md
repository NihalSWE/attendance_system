# Ajay's handover (2026-09-26): what was asked, what is done, what is left

Nihal works it in phases, one at a time: each is tested (full suite), merged
into main and pushed before the next starts.

## A. What Ajay asked for

| # | Ask | Ajay's notes |
|---|---|---|
| 1 | Employee profile page | Build on the employee page (N6). Photo (never used), personal info, attendance, shift, everything Edit employee has; for admin, manager, branch manager, anyone with access. Modals, never confirm()/alert(). |
| 2 | Manual attendance, pending until approved | HR/managers enter it for someone; name the cases (missing check-in, check-out, both, whole day); an approved day must not come back in Days to review. |
| 3 | Full leave | First attachments, amendment, partial cancellation. Policies/versions and the accrual ledger: a plan to Ajay before building. |
| 4 | Every employee operation on the profile | Leave, resign/end, "remove from attendance report", the rest. Reuse end_employment. |
| 5 | Monthly attendance / absent / late summary per employee | On the profile, from the report builders. |
| 6 | Modern, easy UI | Modals only; every list a server-side DataTable with count and filters. |
| + | Small items | Auditor stripped · import NAME ONLY in the docstring · SECRET_KEY note in DEPLOYMENT.md · mail host check at connect time |

## B. Done (on main)

| Ask | What | Commit |
|---|---|---|
| Small items | Auditor opens no company page (joins the self-service roles at the gate) · NAME ONLY documented · SECRET_KEY rotation note · the mail host check already runs on every connection (explained) | 763c3ad |
| 1, 4, 5 | Profile: photo (private, checked), personal information, tabs, monthly summaries from the report builders, actions (Edit, Personal info, Record leave, Calendar, Remove from reports, End employment); import renames reach the terminals | 64420a5 |
| 2 | Named cases; entered for someone (profile, or Missed scans → Enter missing attendance); maker-checker; approving accepts what the rebuilt day flags, so it stays out of Days to review | 0718911 |
| 6 | Applied in all of the above | — |

## C. Left, in phases

| Phase | What |
|---|---|
| 1 | ✅ Done 2026-09-27: leave - cancel some days, change an approved leave, attendance refreshed at once, list and report show current values |
| 2 | ✅ Done 2026-09-27: leave documents - one per leave (a column, no new table); record, request, change; "Needs a document" on leave types; private download |
| 3 | ~~A plan for policies/ledger~~ - **dropped**: Ajay decided (2026-09-27) not to build policies/versions or the accrual ledger; the days-per-year allowance stays the balance |
| 4 | ~~The prompt for Ajay~~ - not needed: Ajay is out; the handover is complete |

## Open questions for Ajay

- HR holds no "view employees", so cannot open profiles. Should it?
- "Delete an employee": End employment is used (history kept) rather than deleting. Right?

## How the work goes on

Nihal said (2026-09-26): nothing is implemented until he says start; then one
phase at a time, each finished, pushed and reported before the next.

## Ajay's leave depth (2026-09-27)

Build: attachments, amendment, partial cancellation. Do not build: policies
with versions, the accrual / carry-forward ledger. Hourly, partly paid and
morning/afternoon only if one falls out naturally - not chased. The
days-per-year allowance per leave type stays the balance. A new table or a
dated-version model means stop and ask Ajay.
