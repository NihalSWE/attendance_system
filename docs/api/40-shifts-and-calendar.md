# Shifts & calendar

Everything on the panel's **Schedule** pages: the attendance settings,
shifts, which shift each department (or one person) works, the weekly off
days and the holidays. Each endpoint does what its panel page does — the same
checks, messages and audit log lines.

## Who may do what

| | Owner, company admin | HR, payroll | Branch manager, employee login |
|---|---|---|---|
| See the schedule, shifts, weekly offs, holidays | ✔ | ✔ | — |
| Change them | ✔ | — | — |

**API keys:** `shifts:read` to read, `shifts:write` to change.

## The endpoints

| What | Endpoint |
|---|---|
| Overview | `GET /api/v1/schedule` |
| Settings | `GET` · `PATCH /api/v1/attendance-settings` |
| Shifts | `GET` · `POST /api/v1/shifts` · `GET` · `PATCH /api/v1/shifts/{id}` · `POST …/{id}/status` |
| Department shifts | `GET` · `POST /api/v1/department-shifts` |
| One person's shift | `GET` · `POST /api/v1/employees/{id}/shifts` · `POST …/shifts/{assignment_id}/end` |
| Weekly offs | `GET` · `POST /api/v1/weekly-offs` · `POST …/{id}/start` · `POST …/{id}/end` |
| Holidays | `GET` · `POST /api/v1/holidays` · `POST /api/v1/holidays/year` · `GET` · `PATCH /api/v1/holidays/{id}` · `POST …/{id}/cancel` |

## Who works which shift

```
an employee's own shift (if any)  →  wins
their department's shift          →  otherwise (shift_mode department_shifts)
the company shift                 →  otherwise, or everyone (company_single_shift)
```

`GET /api/v1/schedule` says `ready: true` when everyone working has a shift —
attendance can then be worked out.

## Shifts

Times are `HH:MM`, 24-hour. An end before the start (`22:00` → `06:00`) is a
night shift that ends the next day (`ends_next_day`); the length
(`scheduled_minutes`) follows from the times.

```json
{"code": "DAY", "name": "Day shift", "start_time": "09:00", "end_time": "18:00",
 "grace_in_minutes": 10, "minimum_full_day_minutes": 420,
 "minimum_half_day_minutes": 240, "default_break_minutes": 60}
```

Shifts are never deleted: retire one with its status endpoint, and past
attendance keeps the shift it was measured against. A change applies from
now on; recalculate a month to apply it to past days.

## Dated changes

- A **department's shift** applies from `from_date`; earlier days keep theirs.
- **One person's shift**: without `last_day` it is theirs until changed; with
  one it is temporary and they return to what they had. End it early with
  `…/end`.
- **Weekly offs**: `weekdays` are names (`"friday"`, `"saturday"`, …), for every
  branch or one (`branch_id`). Stopping one keeps it for the days before.
- **Holidays**: one at a time, or many at once with
  `POST /api/v1/holidays/year` (`days`: each with `date` and `name`; all or
  nothing). Cancelling keeps it listed as cancelled.
