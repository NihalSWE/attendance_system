# PunchEvent ingestion contract

**Audience:** whoever builds the `attendance` app (`PunchAllocation`,
`AttendanceSession`, `AttendanceRecord`).
**Purpose:** state exactly what a `PunchEvent` row is guaranteed to contain, so
the attendance engine can be built and tested before the device adapter is
finished against real hardware.

This is the integration boundary. The devices app stops at `PunchEvent`; it
never creates allocations, sessions or records.

Status: the guarantees below hold for rows produced by the implemented
ingestion path and are covered by tests. They have not yet been exercised
against the physical ZKTeco SenseFace 2A — see "What is not yet proven".

---

## 1. Which rows the attendance engine should read

Read only rows that satisfy **both**:

```python
PunchEvent.objects.filter(
    authorization_status=PunchEvent.AuthorizationStatus.AUTHORIZED,
    dedupe_status=PunchEvent.DedupeStatus.UNIQUE,
)
```

Everything else is deliberately retained and must not be silently consumed:

| Excluded because | Where to see it |
|---|---|
| `authorization_status` is anything but `authorized` | the punch list, filtered |
| `dedupe_status` is `confirmed_duplicate` | it is a retransmission of another row |
| `dedupe_status` is `probable_duplicate` | needs a human decision first |

`processing_status` is the devices app's own view of the same thing:
`pending` means "authorized, unambiguous, waiting for the attendance engine".
`excluded`, `needs_review` and `failed` mean it is not an attendance input yet.
**The attendance engine owns `processing_status` from `pending` onward** — set
it to `allocated` once a row has been consumed, so reprocessing is detectable.

## 2. Guarantees on every row

1. **A row is never deleted and never rewritten.** `punched_at_device_raw` and
   `raw_record` are exactly what the device sent. Resolution fields
   (`employee`, `device_enrollment`, `authorization_status`,
   `authorization_snapshot`, `dedupe_status`, `duplicate_of`) may advance
   through an audited revision; the source values do not change.
2. **`company_id` is always set** and is derived from the device's trusted
   registration, never from anything in the payload. Every query must be
   tenant-scoped; use the default manager.
3. **`punched_at_utc` is a timezone-aware UTC instant** and is the field to
   sort and compare on. `punched_at_device` is the same moment in the device's
   local zone; `device_timezone` and `utc_offset_minutes` record how it was
   interpreted.
4. **`device_id` and `branch_id` are snapshots at receipt.** `branch` is *the
   device's* branch, which is **not** necessarily the employee's. For anything
   employment-related — which branch/department a day belongs to, whose payroll
   it hits — use the employee's `EmployeeAssignment` effective at
   `punched_at_utc`, not this field.
5. **`employee_id` is the permanent `Employee.id`**, resolved through the
   `DeviceEnrollment` covering `punched_at_utc`. It is never the reusable
   `employee_code`, and it is null on unresolved rows.
6. **`source_record_index` is unique within a `device_message`**, so a row is
   always traceable to its exact position in the original upload.
7. **`reported_direction` is informational only.** It is the device's own
   IN/OUT flag and is not trustworthy. Pairing must follow the combined
   employee/shift sequence, not this field, and not a per-device counter.

## 3. Idempotency: what the engine can rely on

- A device re-sending an identical batch stores **nothing new**: the message is
  rejected on `(device, idempotency_key)`.
- The same punch arriving in a *different* batch **is** stored, and is marked
  `confirmed_duplicate` with `duplicate_of` pointing at the original. Filtering
  on `dedupe_status=unique` is therefore sufficient to avoid double counting.
- Two scans close together are **not** automatically duplicates. They are
  marked `probable_duplicate` and left for review; the engine must not treat
  proximity as proof and must not consume them until resolved.

Consequence: replaying the same fixture twice produces no duplicate attendance
effect, provided the engine filters as in section 1.

## 4. Ordering and late arrival

- Sort by `punched_at_utc`, then `id` for a deterministic tie-break. Do **not**
  sort by `received_at`: a punch buffered during an outage arrives hours after
  it happened, and network arrival order is meaningless.
- **Rows can arrive for a day you have already processed.** A device that was
  offline drains its backlog on reconnect, so a punch for last Tuesday can be
  created today. The engine needs a revision path for an already-calculated
  day; it cannot assume a day is final once processed.
- `received_at` is when the server durably stored it, and is the right field
  for "what is new since I last ran".

## 5. Authorization: reading the decision

`authorization_status` is the verdict. `authorization_snapshot` is a JSON
record of *why*, frozen at evaluation time. Useful keys:

| Key | Meaning |
|---|---|
| `effective_scope` | the device scope that applied at the punch instant |
| `winning_policy_level` | `employee_assignment`, `branch` or `company_default` |
| `employee_branch_id` | the employee's assignment branch at that instant |
| `attendance_enabled` | the enrollment master switch as it stood then |
| `assigned_device_authorized` | the explicit grant as it stood then |
| `decision_reason` | a human-readable sentence, shown in the UI |

The snapshot explains an evaluation that already happened. It is **not** a
source of policy truth for a new decision, and it is not a live relationship.

`policy_unresolved` means the historical policy could not be established — a
policy row changed after the punch with no audit record covering it. Those rows
require review and must never be treated as authorized.

## 6. What the devices app does *not* do

- No pairing of punches into sessions, and no IN/OUT interpretation.
- No shift, weekly-off, holiday or leave awareness.
- No work-date assignment. A night shift spanning midnight is the attendance
  engine's problem; the devices app only records instants.
- No attendance records, corrections or penalties.

## 7. What is not yet proven

- Every guarantee above is verified with the simulator and unit tests. **No
  physical SenseFace 2A evidence exists yet.** The wire format, the
  acknowledgement string and the backlog-drain behaviour are taken from the
  published ZKTeco protocol and remain assumptions until tested on the real
  firmware.
- Historical reconstruction depends on audited changes recording every field
  they changed in `before_data`. **Screens that edit
  `CompanyAttendanceSettings.device_attendance_scope`,
  `Branch.device_attendance_scope_override` or
  `EmployeeAssignment.device_attendance_scope_override` must write an audit
  entry with both before and after values.** Without that, punches that arrive
  after such an edit become `policy_unresolved` rather than being judged
  wrongly — safe, but noisy.

## 8. Building against this before hardware exists

Register a device in the browser, enroll an employee, then:

```bash
python manage.py simulate_device --serial <serial> --scenario single_day
```

Scenarios: `single_day`, `replay`, `resend_new_stamp`, `rapid_repeat`,
`offline_backlog`, `unknown_user`, `malformed`. Each is deterministic for a
given `--date`, so a run can be repeated and compared.
