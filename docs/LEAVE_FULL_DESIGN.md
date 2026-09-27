# Full leave: design (Phase E, 2026-09-27)

Ajay: leave policies with versions, an accrual and carry-forward ledger,
balances, entitlements, hourly leave, partly paid leave, morning/afternoon
half days. What is built stays. "Plan it before you write it, and if something
in the design looks like it will change how existing leave days or finalised
months are read, say so before building."

## What exists and stays the source of truth

- `LeaveDay` - one row per working day on leave - is what attendance and
  salary read: `balance_units` (1 or 0.5), `leave_minutes`,
  `approved_pay_percentage` (100 or 0). **It stays the only record of leave
  taken.** Balances count it; nothing copies it.
- `LeaveType.days_per_year` - the simple yearly allowance - keeps working for
  everyone who has no leave policy.
- Finalised months: attendance never rewrites a day inside a posted payroll
  run, and leave cannot be recorded, changed or cancelled there. Unchanged.

## 1. Day shapes: morning / afternoon, hourly, partly paid

On `LeaveRequestSegment` (fields already there, unused until now):

| | full day | half day | hourly |
|---|---|---|---|
| `duration_type` | full_day | half_day | hourly |
| `half_day_part` | "" | morning / afternoon | "" |
| `start_time`, `end_time` | - | - | the hours taken |
| `LeaveDay.balance_units` | 1 | 0.5 | minutes ÷ shift minutes (2 decimals) |
| `LeaveDay.leave_minutes` | shift | shift ÷ 2 | the hours |
| covered interval | shift | its first or second half | the hours |

Pay: `pay_type` gains **partial** with a percentage (1-99), stored in
`approved_pay_percentage`. The database rule "paid = 100, unpaid = 0" is
widened to also allow "partial = 1..99".

How a day reads (one rule, written with the paid share `p` = percentage/100
and the leave's day fraction `f` = `balance_units`):

- Full-day leave: payable `p` (as now: 1 or 0).
- Part-day leave (half or hourly) and they came in: present, payable
  `1 - f × (1 - p)`. No scans: leave, payable `f × p`.
- Late / early: a morning half or hours at the start of the shift excuse the
  late arrival up to the leave's length; an afternoon half or hours at the end
  excuse the early leaving; hours in the middle excuse neither. A half day with
  **no part** (every existing one) excuses both, as now.
- Hourly-paid salary counts paid leave minutes × `p`.

**Existing leave days read exactly as before:** for `p` = 1 and 0 and `f` = 0.5
the formulas give today's numbers (worked half-day: 1 / 0.5; not worked:
0.5 / 0), and existing half days have no part. Tested against the current
behaviour.

## 2. Leave policies, with versions

- **Leave policy** (company): code, name; one may be the **company default**,
  which applies to everyone not given another.
- **Version**: effective from a date; a policy's rules from that date until the
  next version. A version starting in the past cannot be edited - a change is a
  new version from a date. That is what keeps earlier balances reproducible.
- **Rule** (version × leave type): days per year (the entitlement); accrual
  **yearly** (all at the start of the year, prorated by months for someone
  who joins or gets the policy later in it) or **monthly** (1/12 at each month
  start); carry-forward cap in days (none by default); carried days expire
  after N months (never, by default); half days allowed; hours allowed; may go
  below zero.
- **Employee's policy**: dated rows (from / until) on the profile's General
  information ("Leave policy") and a modal to change it.
- A leave type with no rule in someone's policy falls back to its
  `days_per_year`, as now.

## 3. The ledger and balances

`LeaveLedgerEntry`: employee, leave type, leave year (calendar year, as the
allowance is now), date, kind, units (+/-), note, who.

- **accrual** - posted automatically (idempotent: one per employee, type and
  month or year).
- **carry forward** - on 1 January: what was left of last year, up to the cap.
- **expiry** - when carried days expire: what of them was not used by then.
- **adjustment** - by hand, with a reason (owner, admin, HR, or whoever records
  leave in their branch).

Nothing is ever taken from the ledger for leave: **taken = the live `LeaveDay`
units of that type in the year.**

Balance for a year: accrued + carried - expired ± adjustments - taken.
Entries are posted when a balance is read (profile, the employee's own page,
the balances page, a leave being recorded or requested) and by
`manage.py post_leave_accruals` for a nightly run; posting is repeatable.

Enforcement: for someone with a policy, recording, requesting or approving
leave checks the balance available on the leave's dates (accrual up to that
date counts), and whether half days / hours are allowed; for someone without,
the current `days_per_year` check. Salary and attendance never read the ledger.

## What changes, said before building

1. **No existing leave day, attendance day or salary figure changes.** Salary
   and attendance do not read policies or the ledger; the day-shape formulas
   reduce to today's for every existing row; finalised months are untouched.
2. **Whether new leave is accepted can change** for someone once they have a
   policy: the check becomes their balance instead of the type's days per
   year. Nobody has a policy until one is created, so nothing changes on the
   day this ships.
3. **A policy started in the past posts accruals for the past** (from its
   start, or joining), and leave already taken in those years counts against
   them. Balances can therefore start negative for someone who took more than
   the policy gives; they are shown as such, and only new leave is refused.
4. Changing someone's policy mid-year: monthly accrual follows the new policy
   from the next month; a yearly entitlement is given once a year, by the
   policy in force when it first fell due.

## Build order

- E1 Day shapes (morning/afternoon, hourly, partly paid) - forms, services,
  attendance, salary.
- E2 Policies, versions, rules, the employee's policy.
- E3 Ledger, accruals, carry-forward, expiry, adjustments, balances,
  enforcement.
- E4 Balances on the profile, on the employee's own leave page and on a
  Leave balances page; the leave pages show the new shapes.
