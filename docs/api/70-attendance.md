# Attendance

Who came, when, and for how long: the daily list, one person's month and
day, the days that need a person, fixing a day, and missed scans. Everything
is limited to the branches you may see (or fix), exactly as on the panel.
**API keys:** `attendance:read` / `attendance:write`.

## How a day is worked out

Attendance is never typed in: it is **worked out from the scans**, and worked
out again whenever something changes (a new scan, a fix, a new shift).

```
  scans from every device ──▶ one stream, in time order
                               │  repeats a few seconds apart are dropped
                               ▼
                         IN, OUT, IN, OUT …   (alternating; the terminal's own
                               │               IN/OUT keys are not trusted)
                               ▼
     first IN = check-in · OUTs in the middle = breaks · last OUT = check-out
                               │
                               ▼
         measured against the shift ──▶ worked, late, early out, overtime
                               │
                               ▼
                      status: present · half_day · absent …
```

1. **Open or closed.** A day stays *open* until the person's next shift
   starts, or 24 hours after this one started. While open, a last OUT is a
   **break** (out at lunch is not gone home) — unless it is at or after the
   shift's end, when it is the check-out.
2. **No check-out.** A day that closes on an IN gets the shift's end as its
   check-out **by rule** and goes to **review** (`GET /attendance/review`).
3. **Minutes are measured against the shift.** *Worked* is the time in the
   office between the shift's start and end (plus a paid break); time after
   the end is *overtime*; arriving early counts for nothing. The real
   check-in time is always shown.
4. **Late** = minutes after the shift start, **less the grace minutes**
   (arriving 09:25 on a 09:00 shift with 10 minutes' grace is 15 late).
   **Early out** counts only beyond the out-grace.
5. **Status.** Enough worked minutes for a full day: `present`; enough for a
   half: `half_day`; otherwise `absent`. A day with leave, a holiday or a
   weekly off says so (`leave`, `holiday`, `weekly_off`); `incomplete` means
   no check-out yet; `inactive` means they did not work here that day.
6. **Fixes are inputs, not edits.** A correction (an added scan, a changed
   status) is something the calculation reads, so it survives every later
   recalculation. Withdraw it and the day is worked out without it.
7. **Finalised months are locked.** Once a salary month is finalised, its days
   cannot change: `locked` is `true` and fixes are refused.

Each read brings the days asked for up to date first, so the answer is never
stale.

## The endpoints

| What | Endpoint |
|---|---|
| Daily list | `GET /api/v1/attendance` · `GET /api/v1/attendance/late` |
| Download it | `GET /api/v1/attendance/export?file_type=xlsx` (or `pdf`) |
| Who is in now | `GET /api/v1/attendance/now?employee_ids=41,42` |
| One person's month | `GET /api/v1/attendance/calendar?employee_id=41&year=2026&month=10` · as a PDF: `GET /api/v1/attendance/calendar/export?…` |
| One day | `GET /api/v1/attendance/days/{employee_id}/{date}` |
| Days to review | `GET /api/v1/attendance/review` |
| Fix a day | `POST …/days/{employee_id}/{date}/add-scan` · `…/change-status` · `…/accept-review` · `…/excuse-late` |
| Undo a fix | `POST /api/v1/attendance/corrections/{id}/withdraw` |
| Missed scans | `GET /api/v1/missed-scans?status=pending` · `POST /api/v1/missed-scans/{id}/decide` |
| Enter missing attendance | `POST /api/v1/employees/{id}/missing-attendance` |

### Filtering the daily list

`year` + `month` (this month by default), or `on=2026-10-05`, or
`from=…&to=…` (at most 366 days); then `branch_id`, `employee_id`, `status`.
The download takes the same filters, and refuses more than 10,000 rows
(Excel) or 1,500 (PDF) — narrow the filter.

### Fixing a day

Every fix needs a **reason**, which is kept with it, and answers the whole
day again (scans, corrections, `may_fix`, `locked`).

| Fix | Body | When |
|---|---|---|
| `add-scan` | `{"at": "2026-10-05T18:02", "reason": "…"}` | a scan the device did not record (company time; a night shift's after-midnight scan takes the next date) |
| `change-status` | `{"status": "present" \| "half_day" \| "absent", "reason": "…"}` | not for an open day, a leave day, or a locked month |
| `accept-review` | `{"reason": "…"}` | the day to review is right as it is |
| `excuse-late` | `{"reason": "…"}` | a late day of the last two months; the day then counts no late minutes |

Who may fix: the owner, company administrator, HR, or anyone given **Fix
attendance** in that day's branch (`may_fix` says so). Nobody fixes their own
day.

### Missed scans

An employee reports their own missed scan from their app (the *My account*
area, phase 10). Here, whoever may fix their attendance decides it:

```json
POST /api/v1/missed-scans/15/decide
{"decision": "approve"}                              // the scan is added
{"decision": "reject", "note": "No guard saw you"}   // a note is needed
```

HR, a branch manager or a department head can also **enter** missing
attendance for someone (`kind`: `check_in`, `check_out`, `both`, or
`whole_day` with `work_date`). It waits for someone else's approval; the owner
or company administrator may send `"approve_now": true` to approve it at once.

## Who may do what

Same as the panel:

- **See** attendance: owner, company administrator, HR, auditor; a branch
  manager or employee only with *View attendance* in a branch (or as a
  department head for their department).
- **Fix** and **decide**: owner, company administrator, HR; others only with
  *Fix attendance* in a branch.
- An employee login without a grant gets `permission_denied`.
