# Salary

A month's salary from start to finish, payslips, penalties and overtime -
the panel's Salary pages, with the same people allowed to do the same
things. **API keys:** `payroll:read` / `payroll:write`.

## How a month's salary works

```
  attendance · leave · overtime · salary · components · rules · penalties
                               │
                               ▼
   generate ──▶ DRAFT ──submit──▶ WAITING FOR APPROVAL ──approve──▶ FINALISED
     ▲  (again,   │                      │                            │
     │  any time) │ lines by hand        │ send back (with a reason)  │ employees see
     └────────────┘ waive a penalty      ▼                            │ their payslips
                                       DRAFT                          │
                    undo finalise (with a reason) ◀───────────────────┘
```

1. **Generate** works out each person's payslip from the month's attendance
   (brought up to date first), leave, approved overtime, their salary and
   components, the salary rules and the penalty rules. A draft can be
   generated again at any time; people without a salary are skipped and
   named.
2. **Submit**: whoever prepares salary says it is ready. It can no longer be
   regenerated.
3. **Approve**: the owner or company administrator finalises it. Employees
   then see their payslips, and the month's attendance, leave and overtime
   are locked. If a day was fixed after generating, approving is refused
   (`attendance_changed` says so): generate and submit again.
4. **Put right a finalised month** with a **correction** in the first month
   still open - what was paid stays paid. *Undo finalise* exists for a real
   mistake; it hides the payslips from employees until finalised again.

## The endpoints

| What | Endpoint |
|---|---|
| The month | `GET /api/v1/payroll/months/{year}/{month}` |
| Its payslips | `GET /api/v1/payroll/months/{year}/{month}/payslips` |
| Move it on | `POST …/months/{year}/{month}/generate` · `…/submit` · `…/approve` · `…/send-back` · `…/reopen` |
| One payslip | `GET /api/v1/payroll/payslips/{id}` · `GET …/pdf` · `POST …/email` |
| Lines by hand | `POST …/payslips/{id}/adjustments` (draft) · `DELETE /api/v1/payroll/adjustments/{id}` |
| Correction | `POST …/payslips/{id}/corrections` (finalised) |
| Penalties | `POST /api/v1/payroll/penalties/{id}/waive` · `…/unwaive` |
| Overtime | `GET /api/v1/payroll/overtime?year=&month=&show=waiting` · `GET …/overtime/{day_id}` · `POST …/decide` · `POST …/undo` |
| Settings & rules | `GET`/`PATCH /api/v1/payroll/settings` · `POST …/settings/rules` |
| Allowances & deductions | `GET`/`POST /api/v1/payroll/components` · `GET`/`PATCH …/components/{id}` · `POST …/components/{id}/status` |
| A person's | `GET`/`POST /api/v1/employees/{id}/components` · `POST …/components/{row_id}/end` |
| Penalty rules | `GET`/`POST /api/v1/payroll/penalty-rules` · `GET …/{id}` · `POST …/{id}/change` · `POST …/{id}/stop` |
| LFA | `GET`/`PATCH /api/v1/payroll/lfa/settings` · `GET …/lfa/eligibility?employee_id=` · `GET`/`POST …/lfa/claims` · `GET …/claims/{id}` · `POST …/decide` · `…/cancel` · `…/paid` · `GET …/document` |

`send-back` and `reopen` need `{"reason": "…"}`.

### Lines by hand

```json
POST /api/v1/payroll/payslips/501/adjustments
{"type": "earning", "amount": "5000", "reason": "Eid bonus"}
```

`type` is `earning` (a bonus) or `deduction`. The salary is generated again
with the line - the whole month for the owner or administrator, that branch
for a branch login - and the payslip is answered. The same body on
`…/corrections` puts right a finalised month.

### Overtime

A finished day whose overtime ends in a real scan is **paid automatically**.
A day nobody scanned out of **waits** for a decision. Whoever decides can:

```json
POST /api/v1/payroll/overtime/9001/decide
{"decision": "approve", "minutes": 60, "note": "Stock count"}   // fewer than counted
{"decision": "approve", "check_out": "20:15"}                    // nobody scanned out
{"decision": "reject"}
```

`paid_minutes` is what the salary rules pay (a minimum, whole blocks).
Generate a draft month again to include a decision.

### Salary rules

The rules that turn attendance into pay are **dated**: each change applies
from the 1st of a month, and the months before keep the rules they were paid
under.

```json
POST /api/v1/payroll/settings/rules
{"applies_from": "2026-11", "overtime_multiplier": "1.5",
 "minimum_overtime_minutes": 30, "overtime_rounding_minutes": 30}
```

Send only what changes; the rest keeps its value. `GET /payroll/settings`
shows the rules in force, in plain words too (`summary`), and every version.
The currency and pay day are plain settings (`PATCH /payroll/settings`).

### Allowances and deductions

The company's list (house rent 40 % of basic, transport 2,000 a month, a loan
repayment …), then given to people from a date:

```json
POST /api/v1/payroll/components
{"code": "HOUSE_RENT", "name": "House rent", "kind": "earning",
 "method": "percent_of_basic", "default_percent": "40"}

POST /api/v1/employees/41/components
{"component_id": 4, "effective_from": "2026-10-01"}          // its default
{"component_id": 4, "percent": "45", "effective_from": "2027-01-01"}  // a change
```

### Penalty rules

What counts, when, and what it deducts - also from a month:

```json
POST /api/v1/payroll/penalty-rules
{"name": "Late more than 10 minutes", "metric": "late_minutes",
 "operator": "gt", "threshold_minutes": 10,
 "occurrence_mode": "within_period", "required_occurrences": 3,
 "deduction_method": "day_fraction", "deduction_value": "0.5",
 "applies_from": "2026-11"}
```

Every third late day in a month costs half a day's pay. `…/change` saves a
new version from a month; `…/stop` ends it from a month. A month's total
penalties can be capped in the salary rules
(`maximum_period_deduction_percent`).

### LFA (Leave Fare Assistance)

The company sets the rules (`PATCH /payroll/lfa/settings`): how much (fixed,
or months of basic or gross salary, with a cap), after how many months of
service, probation or not, how often, whether leave must be taken with it and
proof attached, a smaller first year, and whether it is paid on the payslip or
separately. Employees claim from their app; here, whoever decides:

1. `GET /payroll/lfa/eligibility?employee_id=41` - may they, how much, and
   which leave it may go with.
2. `POST /payroll/lfa/claims` - enter a claim for someone (e.g. without a
   login).
3. `POST …/claims/{id}/decide` - `approve` (the amount; with salary, the
   `pay_month`) or `reject` (with a `note`).
4. Paid with salary: it becomes *paid* once that month is finalised. Paid
   separately: `POST …/paid` with the date and reference.

## Who may do what

| | Who |
|---|---|
| See the month and payslips | the owner, company administrator, payroll manager; anyone given *View* or *Prepare salary* in a branch (a branch manager in theirs) - those branches' payslips. **HR sees no pay.** |
| Generate, submit, lines, corrections, email | the owner, company administrator, payroll manager; anyone given *Prepare salary* - for that branch |
| Approve, undo finalise, waive a penalty | the owner or company administrator |
| Settings, rules, the list of allowances, penalty rules, LFA settings | the owner or company administrator |
| Give or end someone's allowance; LFA claims | the owner or company administrator; anyone given *Prepare salary* in the person's branch. Never your own LFA claim |
| Send back | the owner or company administrator, or whoever submitted it |
| Overtime | the owner, company administrator, HR; anyone given *View* / *Decide overtime* in a branch; a department head sees (never decides) their department's |
