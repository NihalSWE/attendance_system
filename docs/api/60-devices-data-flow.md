# Devices: data flow

How data travels between a terminal and the software, and the endpoints to
watch it and steer it. Devices are the owner's or an unrestricted company
administrator's, as on the panel. **API keys:** `devices:read` /
`devices:write`.

## How it works

```
                    every few seconds, on its own
  ┌──────────┐  ─────────────────────────────────────▶  ┌──────────────┐
  │ terminal │   uploads what is new: scans, users,      │   software   │
  │          │   fingerprints, faces, settings           │              │
  │          │  ◀─────────────────────────────────────  │              │
  └──────────┘   collects the commands waiting for it    └──────────────┘
```

1. The terminal **calls the server**, never the other way round (it sits
   behind the office router). Every call is kept as a **message**, exactly as
   it arrived.
2. Scans in a message become **punches**. For each one the server decides
   **whose it is** (the user number's enrollment) and **whether it counts**
   (the enrollment's switches and *which devices count*), then rebuilds that
   person's day of attendance.
3. Anything for the terminal — *send me your users*, *add this person*,
   *change this setting* — waits as a **command** until the terminal next
   calls in and collects it (about twice a second while busy, otherwise every
   push interval). Its answer arrives as another message.

So a command is never instant: queue it, then watch its answer.

## The endpoints — what the devices sent (read)

| What | Endpoint |
|---|---|
| Messages | `GET /api/v1/device-messages` · `GET /api/v1/device-messages/{id}` |
| Punches | `GET /api/v1/punches` · `GET /api/v1/punches/{id}` |
| To sort out | `GET /api/v1/punches/unresolved` · `GET /api/v1/punches/unresolved/counts` |
| A device's users | `GET /api/v1/devices/{id}/users` |
| Saved fingerprints & faces | `GET /api/v1/devices/{id}/templates` |
| A device's settings | `GET /api/v1/devices/{id}/options` |
| Its queue & progress | `GET /api/v1/devices/{id}/commands` · `GET /api/v1/devices/{id}/jobs` |

## Following one scan

1. `GET /api/v1/punches?device_id=…&q=41` — find it; `authorization_status`
   says whether it counts.
2. `authorized` + `processing_status: allocated` → it is in attendance.
3. Anything else says why not:

| authorization_status | Means | Usually fixed by |
|---|---|---|
| `unknown_employee` | no enrollment has this user number | linking the user number to the employee |
| `expired_enrollment` | scanned outside the enrollment's dates | correcting the enrollment's dates |
| `enrollment_disabled` | the enrollment's master switch is off | turning `attendance_enabled` on |
| `unauthorized_device` | not one of their devices (assigned-devices mode) | `assigned_device_authorized`, or another rule |
| `branch_mismatch` / `department_mismatch` | not a device of their branch / department | the rule, or their placement |
| `employee_inactive` | they were made inactive that day | — (blocked on purpose) |
| `policy_unresolved` | the rules could not decide (e.g. no placement) | placing them |

4. After fixing the cause, judge the punches again:
   `POST /api/v1/devices/attendance-rules/recheck` with the dates.

`GET /api/v1/punches/{id}` shows the raw record and the frozen facts the
punch was judged on.

## Users on a device

`GET /api/v1/devices/{id}/users` lists every user number the device reported
(or scanned with), joined to the employee it is linked to. Someone not linked
carries `unlinked.code` with what to do:

| code | Means |
|---|---|
| `ready` | their number is an employee's Employee ID — link them |
| `other_number` | that employee is linked under an old number on this device |
| `other_branch` | that employee works at another branch |
| `no_employee` | no employee has this Employee ID |
| `not_digits` | the device number is not digits — link by hand |

The list is what the device last reported.

Raw message text can hold fingerprint and face templates, so it is shown to
people only; an API key gets `null`. Saved templates themselves are never
returned — only how many there are, and in which format.
