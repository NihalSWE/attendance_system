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

## Who may do what

| | Who |
|---|---|
| See the month and payslips | the owner, company administrator, payroll manager; anyone given *View* or *Prepare salary* in a branch (a branch manager in theirs) - those branches' payslips. **HR sees no pay.** |
| Generate, submit, lines, corrections, email | the owner, company administrator, payroll manager; anyone given *Prepare salary* - for that branch |
| Approve, undo finalise, waive a penalty | the owner or company administrator |
| Send back | the owner or company administrator, or whoever submitted it |
| Overtime | the owner, company administrator, HR; anyone given *View* / *Decide overtime* in a branch; a department head sees (never decides) their department's |
