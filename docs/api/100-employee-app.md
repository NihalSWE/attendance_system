# The employee app

Everything an employee does from their own phone - their profile, their
attendance, a missed scan, leave, payslips and LFA - under
`/api/v1/me/…`. These endpoints only ever show **the logged-in person's own
record**: the employee is the one linked to the login, never one named in the
address, so there is no id to guess. API keys cannot use them.

The same rules as the panel's *My account* pages.

## The screens, and what each calls

```
  Log in ──▶ Home ──┬── My attendance ──▶ A day ──▶ Report a missed scan
                    ├── My leave ──▶ Ask for leave
                    ├── My payslips ──▶ A payslip ──▶ PDF
                    ├── LFA ──▶ Claim
                    ├── My profile ──▶ Edit details · Photo · Education
                    └── (branch manager) My branch today · Leave to approve
```

| Screen | Call |
|---|---|
| Log in | `POST /api/v1/auth/login` (an employee needs no two-step login) |
| Home | `GET /api/v1/me` - who, where, today's shift, *now* (in, on a break, left …), this month so far |
| My attendance | `GET /api/v1/me/attendance?year=2026&month=10` |
| A day | `GET /api/v1/me/attendance/2026-10-05` - every scan and how it was read |
| Missed scans | `GET` / `POST /api/v1/me/missed-scans` · `POST …/{id}/withdraw` |
| My leave | `GET /api/v1/me/leave/summary` (types, balances, taken) · `GET /api/v1/me/leave` (requests) |
| Ask for leave | `POST /api/v1/me/leave` · `POST …/{id}/withdraw` · `GET …/{id}/document` |
| Payslips | `GET /api/v1/me/payslips` · `GET …/{id}` · `GET …/{id}/pdf` |
| LFA | `GET /api/v1/me/lfa` (may they, how much) · `POST /api/v1/me/lfa` · `GET …/lfa/claims` · `POST …/claims/{id}/withdraw` |
| Profile | `GET /api/v1/me/profile` · `PATCH /api/v1/me/details` · `GET` / `PUT` / `DELETE /api/v1/me/photo` · `/api/v1/me/education` |
| Password | `POST /api/v1/auth/password/change` |
| Branch manager | `GET /api/v1/me/branch-attendance` · approve leave with `GET /api/v1/leave/requests` and `POST …/{id}/decide` |

## Reporting a missed scan

```json
POST /api/v1/me/missed-scans
{"kind": "check_out", "at": "2026-10-04T18:05",
 "reason": "Left with the visitors through the side gate"}
```

- `kind`: `check_in`, `check_out`, `both` (with `at_out`), or `whole_day`
  (with `work_date`; the shift's times are used).
- Times are company time. The day a scan counts for is worked out from it -
  after midnight on a night shift, give the next date.
- It waits for whoever may fix their attendance; their day changes once it is
  approved.

## Asking for leave

```json
POST /api/v1/me/leave
{"leave_type_id": 1, "start_date": "2026-10-12", "end_date": "2026-10-12",
 "duration": "half_day", "half_day_part": "morning",
 "reason": "Doctor's appointment"}
```

The types come from `GET /me/leave/summary`. A reason is needed. A type that
needs a document takes `"document": {"filename", "content_base64"}` (PDF or a
picture, up to 5 MB). The approver decides the pay; the note they give comes
back as `decision_note`.

## Payslips

Only **finalised** months appear: a draft can still change, so it is the
company's to check first. The PDF is the same as the company prints.

## What they can and cannot change

They change their phone, personal email, address, emergency contact, date of
birth, IDs and the like (`PATCH /me/details`), their photo and their
education. Their name, Employee ID, work email, placement, joining date and
pay are the company's record - shown, not changed.

## A login without an employee record

An administrator's login, say: `GET /me` answers with `employee: null`; the
other `/me/…` endpoints answer `permission_denied`.
