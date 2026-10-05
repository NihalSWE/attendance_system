# Devices: setup

Everything on the panel's **Devices** pages for setting terminals up:
registering a device, what to type on it, whether it is calling in,
the departments it serves, who is which user number on it (enrollments), and
which devices count for attendance. Each endpoint does what its panel page
does — the same checks, messages and audit log lines.

> What travels between a device and the software — scans, users,
> fingerprints and faces, commands, loading one device from another — is the
> next area, **Devices: data flow**.

## Who may do what

Devices are the **owner's or a company administrator's not limited to some
branches** — as on the panel. A device's settings reach the whole terminal,
so HR, branch managers and branch-limited administrators are refused.

**API keys:** `devices:read` / `devices:write`.

## The endpoints

| What | Endpoint |
|---|---|
| Devices | `GET` · `POST /api/v1/devices` · `GET` · `PATCH /api/v1/devices/{id}` · `POST …/{id}/retire` |
| Before registering | `GET /api/v1/devices/serial-check` · `GET /api/v1/device-models` |
| On the terminal | `GET /api/v1/devices/{id}/setup` |
| Is it calling in | `GET /api/v1/devices/connections` · `POST` · `GET /api/v1/devices/{id}/test-connection` |
| Departments | `GET` · `POST /api/v1/devices/{id}/departments` · `POST /api/v1/device-departments/{id}/end` |
| Enrollments | `GET` · `POST /api/v1/device-enrollments` · `GET` · `PATCH /api/v1/device-enrollments/{id}` |
| Which devices count | `GET` · `PATCH /api/v1/devices/attendance-rules` · `POST …/attendance-rules/recheck` |

A device's id is a UUID, e.g. `6f1c9a2b-7e8d-4c3b-9a1f-2d4e5b6c7d8e`.

## Setting up a new terminal

1. `GET /api/v1/devices/serial-check?serial=CQZ7232460045` — one device sends
   to one company only; a serial registered elsewhere is refused.
2. `POST /api/v1/devices` with `branch_id`, `name`, `device_model_id`,
   `serial_number` (exactly as printed) and `timezone`. Leave `comm_key` out
   for a ZKTeco device — it cannot send one, and a key it does not send locks
   it out.
3. `GET /api/v1/devices/{id}/setup` → each setting to type in the terminal's
   own menu (server address, port, HTTPS, …).
4. `POST /api/v1/devices/{id}/test-connection` → `started_at`, `command_id`.
   Ask `GET …/test-connection?since=<started_at>&command_id=<id>` every few
   seconds until `checked_in` (it works) or `gave_up` (then `advice` says what
   to check).

The device's server address, once it is calling in, is changed with the data
flow endpoints — it is checked with the device first, so a typo cannot strand
the terminal.

## Dates and times

Department mappings and enrollments are dated **moments in company time**:
`"2026-01-01"` (midnight) or `"2026-01-01T09:30"`.

## Enrollments

An enrollment says *this employee is user number 41 on this device, from …*.
Being enrolled means the device recognises them; whether their punches
**count** is decided by:

- `attendance_enabled` — the master switch;
- `assigned_device_authorized` — needed when punches count on assigned
  devices only (the company default).

Both are `true` for a new enrollment unless sent `false`. A user number — or a
person — cannot have two overlapping enrollments on one device. A change to
the card or the role is sent to the terminals the person is on. Every change
is audited with what it was before: past punches are judged by it.

## Which devices count

`scope`: `assigned_devices` (default), `department_devices`, `branch_devices`
or `company_devices`. Branches and people can have their own rule. Changing it
does not change punches already stored — re-check a range
(`POST …/attendance-rules/recheck`, at most a year): the punches that now
count rebuild their days; a finalised salary month is left alone.
