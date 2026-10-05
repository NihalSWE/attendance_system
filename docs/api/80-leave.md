# Leave

Leave types, leave policies, balances, recorded leave, and approving what
employees ask for. The same rules as the panel's Leave pages and approval
inbox. **API keys:** `leave:read` / `leave:write`.

## How leave works

```
  leave type ──▶ (policy: days a year, earned yearly or monthly, carry forward)
                               │
                               ▼
                      balance: given · taken · left
                               │
   recorded by HR ─────────────┤◀──── asked for by the employee, then approved
                               ▼
                 leave days ──▶ attendance shows "leave"
                               ──▶ salary: paid, unpaid or part paid
```

1. **Leave types** are the company's: Casual, Sick, Annual … each with its
   days per year (or no limit), and whether it needs a document.
2. **Policies** are optional. Without one, each type's *days per year*
   applies to everyone. With one, its rules decide: days a year, given all at
   once (`yearly`) or a twelfth a month (`monthly`), what carries forward and
   when that expires, half days, hours, going below zero. Rules change with
   **versions**, each from a date; a version that has started never changes
   (add a new one instead), so what was given before stays.
3. **Leave is counted in working days.** Weekly offs and holidays inside the
   dates are not leave. A half day is 0.5; some hours count by the shift.
4. **Every check, every time.** Recording, changing and approving all check
   clashes with other leave, the allowance or policy balance, the shift, and
   finalised salary months.
5. **Nothing is deleted.** Cancelling (all or some days) or changing a leave
   keeps the old days, marked cancelled; attendance is worked out again.

## The endpoints

| What | Endpoint |
|---|---|
| Types | `GET`/`POST /api/v1/leave/types` · `POST …/types/defaults` · `GET`/`PATCH …/types/{id}` · `POST …/types/{id}/status` |
| Policies | `GET`/`POST /api/v1/leave/policies` · `GET`/`PATCH …/policies/{id}` · `POST …/policies/{id}/status` |
| Policy versions | `POST …/policies/{id}/versions` · `PUT`/`DELETE …/policies/{id}/versions/{version_id}` (not started yet) |
| Balances | `GET /api/v1/leave/balances?year=2026&employee_id=41` |
| Leave | `GET`/`POST /api/v1/leave/records` · `GET …/records/{id}` · `POST …/records/{id}/amend` · `POST …/records/{id}/cancel` · `GET …/records/{id}/document` |
| Approval | `GET /api/v1/leave/requests?status=pending` · `GET …/requests/{id}` · `POST …/requests/{id}/decide` |

On someone's profile: *Record leave* is `POST /api/v1/leave/records` with
their `employee_id`; *Leave policy* and *Adjust balance* are
`PUT /api/v1/employees/{id}/leave-policy` and
`POST /api/v1/employees/{id}/leave-adjustments` (phase 3).

### Recording leave

```json
POST /api/v1/leave/records
{"employee_id": 41, "leave_type_id": 1,
 "start_date": "2026-10-12", "end_date": "2026-10-13",
 "pay_type": "paid", "reason": "Family wedding",
 "document": {"filename": "letter.pdf", "content_base64": "JVBERi0xLjQK…"}}
```

- `duration`: `full_day` (default), `half_day` with `half_day_part`
  (`morning` excuses a late arrival, `afternoon` an early leaving), or
  `hourly` with `start_time` / `end_time` - one date for both.
- `pay_type`: `paid`, `unpaid` (deducted from salary) or `partial` with
  `pay_percentage` (1-99, the share kept).
- `document` is optional unless the type needs one: PDF, JPG, PNG or WEBP,
  up to 5 MB, base64 inside the JSON.

**Change** (`…/amend`) takes only what changes. **Cancel** (`…/cancel`) with
no body cancels the whole leave; with `"days": ["2026-10-13"]` and a
`reason`, only those days.

### A policy and its rules

```json
POST /api/v1/leave/policies/2/versions
{"effective_from": "2027-01-01", "note": "Casual leave up to 12",
 "rules": [{"leave_type_id": 1, "days_per_year": "12", "accrual": "monthly",
            "carry_forward_days": "5", "carry_forward_expires_months": 3}]}
```

A version lists every type it covers. The first version of a policy may
start in the past (e.g. 1 January); later ones start today or later.

### Approving requests

Employees ask from their app (the *My account* area, phase 10). Whoever
decides sees each waiting request with the person's allowance:

```json
POST /api/v1/leave/requests/31/decide
{"decision": "approve", "pay_type": "paid"}
{"decision": "reject", "note": "Busy week - please pick another"}
```

Approving needs `pay_type` (`partial` needs `pay_percentage`); rejecting
needs a `note`, which the employee reads.

## Who may do what

Same as the panel:

| | Who |
|---|---|
| Leave types | everyone in the company reads them except Employee and Branch-manager logins; the owner or company administrator changes them |
| Policies & versions | the owner or company administrator |
| Leave list, balances | the owner, company administrator, HR; anyone given *View leave* or *Record leave* in a branch (a branch manager in theirs); a department head for their department |
| Record, change, cancel | the owner, company administrator, HR; anyone given *Record and cancel leave* in a branch - for people placed there. Never your own leave |
| Decide requests | a branch manager or anyone given *Approve leave* - their branches; a department head - their department; the owner or company administrator - requests from branch managers and from branches without one. Never your own |
