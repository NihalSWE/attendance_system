# Device Integration — Handoff

**Owner of this work:** the device developer (separate machine, separate chat).
**Owner of everything else:** Ajay (team lead), working in parallel on company
setup, scheduling, employees, leave and payroll.

This document is the contract between the two streams. Read it before writing
code. Nothing here overrides `docs/DEVICE_ATTENDANCE_POLICY.md`, which remains
authoritative for scope and pairing rules.

---

## 1. Hardware and protocol

| Item | Value |
|---|---|
| Device | **ZKTeco SenseFace 2A** (physically in hand) |
| Mode | **TA Push** (ADMS-style: the *device* initiates outbound HTTP to our server) |
| Direction | Device to server. We never poll the device on port 4370. |
| Why | A previous ZKTeco K40 polling build failed: 4-5 seconds just to connect, periodic downloads interfering with fingerprint use, devices unreachable for hours. Polling is rejected by design, not by preference. |

The office only needs ordinary outbound internet. Do **not** design around a
static public IP or an inbound open port at the client site.

---

## 2. Scope — what you own, and where you stop

### You own

- The entire `devices` app: **10 models**, `DeviceVendor` through `PunchEvent`
  (field contract in `docs/MODEL_FIELD_DICTIONARY.md` sections 22-31).
- The ingestion endpoints and the ZKTeco adapter.
- Employee resolution and device-scope authorization at punch time.
- Retry, offline-backlog drain, deduplication, clock-skew handling.
- A vendor-neutral **simulator** plus repeatable fixtures.
- **The complete device management UI** — every screen an administrator needs to
  register, configure, monitor and troubleshoot devices. See section 2a.
- **Device settings**, both sides: the values stored in our database, and the
  instructions telling an administrator what to type into the physical device.

### You STOP at `PunchEvent`

Do **not** build the `attendance` app (`PunchAllocation`, `AttendanceSession`,
`AttendanceRecord`, corrections, penalties). Pairing punches into sessions needs
shifts, weekly offs, holidays and leave — all of which Ajay is building right
now. That is the integration point, and it is done jointly, later.

Instead you deliver a written **ingestion contract**: exactly what a `PunchEvent`
row is guaranteed to contain, so the attendance engine can be built against it
before your adapter is finished.

---

## 2a. Device management UI — you build this

Backend ingestion with no screens is not a usable feature. An administrator must
be able to do all of this in the browser, without a shell or Django admin:

**Devices**
- List registered devices with status, branch, last seen, last message.
- Register a new device: name, serial number, branch, model, timezone, and the
  authentication credential the device will present.
- Edit a device; retire/decommission one without deleting its punch history.
- Show live health from `DeviceSyncState`: last punch received, consecutive
  errors, estimated backlog, clock offset.

**Device settings (this is the part that was previously under-specified)**
- Our `BiometricDevice` model already carries `external_device_id`, `timezone`,
  `authentication_key_id`, `authentication_secret_hash` and a validated
  `settings` JSONField for vendor-specific configuration. Use them.
- **Surface the exact values an administrator must enter on the SenseFace 2A**:
  the server address/domain, port, path, and the comm/auth key. The admin should
  be able to read them off the screen and type them into the device menu. Do not
  make them guess a URL.
- **Discovering which settings the SenseFace 2A actually exposes is part of your
  job.** Go through the device's own menus (server/ADMS settings, comm key, push
  interval, timezone, verification mode, log capacity, and anything else present),
  document them, and decide which we must store, which we must display as setup
  instructions, and which we can ignore. Report your findings.

**Enrollment**
- Map an employee to a device user id (`DeviceEnrollment`), on dated intervals.
- Toggle `attendance_enabled` (the hard per-enrollment denial) and
  `assigned_device_authorized` (only consulted in assigned_devices mode), with
  the difference between recognition and authorization made obvious in the UI.
- Device/department mapping (`DeviceDepartment`) for department_devices mode.

**Troubleshooting**
- Raw `DeviceMessage` log with processing status and payload, for diagnosing a
  device that is misbehaving.
- `PunchEvent` list filtered by device/employee/date showing
  `authorization_status`, and *why* a punch was excluded.
- An unresolved queue: unknown device user ids, unmatched employees,
  `policy_unresolved` punches awaiting review.

**Design rules — non-negotiable**
- `WARM_PAPER_INK_SPEC.md` is authoritative for colour, typography and tokens.
  Every colour comes from a token; never introduce a hex value.
- `design_reference/` is authoritative for component form and layout.
- Reuse the existing CSS in `base_template/static/base_template/css/`
  (`tokens.css`, `base.css`, `components.css`, `shell.css`) and the existing
  component classes. Do not invent a parallel design system.
- Real DataTables for data tables, real Select2 for database-backed choices,
  styled native selects for fixed choices. Tables need a real result count and
  pagination. Four-space indentation in templates.
- Feature pages live in the `devices` app; shared shell stays in
  `base_template`.

## 2b. Reaching your machine from the device

The device pushes *inbound* to your server, so `localhost` is not reachable from
it. For local development expose your dev server with a tunnel (ngrok or
similar) and configure that hostname on the device. The project already trusts
`*.ngrok-free.dev` in `CSRF_TRUSTED_ORIGINS`; add what you need there, and tell
Ajay if that list must change.

Note that the ingestion endpoint is called by a device, not a browser: it will
need CSRF exemption on that path plus its own device authentication. Do not
weaken CSRF anywhere else.

## 2c. Boundary on device-scope settings

The scope *fields* are split across two owners. Coordinate before building:

| Setting | Model | Owner |
|---|---|---|
| Company default `device_attendance_scope` | `scheduling.CompanyAttendanceSettings` | **Ajay** |
| Branch `device_attendance_scope_override` | `organization.Branch` | **Ajay** |
| Employee `device_attendance_scope_override` | `employees.EmployeeAssignment` | **Ajay** |
| `DeviceEnrollment.attendance_enabled` / `assigned_device_authorized` | `devices` | **You** |
| `DeviceDepartment` mapping | `devices` | **You** |

You *read* the first three when evaluating a punch; Ajay builds their screens as
part of company settings. You build the last two. Confirm with Ajay before
building any screen that edits a field on his models.

---

## 3. Non-negotiable rules

These are recorded project decisions. Breaking one is a correctness bug, not a
style disagreement.

1. **Raw evidence is immutable.** `DeviceMessage` and `PunchEvent` are
   append-only. Never edit or delete a punch to make a calculated result look
   right. Resolution fields may advance through an audited revision; original
   values (`punched_at_device_raw`, `raw_record`) never change.
2. **A retransmission must not multiply attendance effects.** That is idempotent
   *processing*, not deletion.
3. **Closeness in time is NOT proof of duplication.** Two scans seconds apart may
   be genuine. Ambiguous repeats are marked (`dedupe_status`, `duplicate_of`) and
   stay reviewable. Only confirmed duplicates are excluded from downstream
   allocation, and they remain in the table.
4. **Enrollment is not authorization.** Being enrolled means the device
   recognises the person. It does not mean their punch counts.
5. **`attendance_enabled = false` wins in every scope**, including
   `company_devices`. It is a hard per-enrollment denial.
6. **`assigned_device_authorized` is consulted only in `assigned_devices` mode.**
   Wider scopes do not require it. A recognition-only enrollment leaves it false.
7. **Historical authorization, not today's.** An offline punch from last Tuesday
   is judged by the policy in force last Tuesday. If history is missing or
   ambiguous, mark `policy_unresolved` and require review. **Never** silently
   apply today's broader permission.
8. **Use the employee's assignment/home branch at event time**, not the branch of
   the device they happened to use.
9. **Ingestion must be durable before acknowledgement.** A background worker
   finishing later must never be a precondition for accepting device data.
10. **Tenant isolation always.** Every row is company-scoped. Use the existing
    `TenantOwned` base and `TenantManager`. Never weaken them.

Scope precedence (from the policy document): dated **EmployeeAssignment**
override, then assigned **Branch** override, then **CompanyAttendanceSettings**
default. It is a precedence chain, not an intersection of all three.

---

## 4. The offline problem you must solve

**Scenario:** the internet drops for one to two hours. Staff keep punching. The
server receives nothing during that window.

**Expected ADMS behaviour:** the device buffers punches in its own flash and only
advances its internal pointer when the server acknowledges. On reconnect it
resumes from the last acknowledged point and drains the backlog automatically,
with no manual connection required.

**Treat that as an assumption, not a verified fact.** Verifying it on this exact
SenseFace 2A firmware is part of your job:

- Get the acknowledgement/stamp handshake right. A wrong ack means the device
  either re-sends everything forever or silently skips records.
- Measure how many records the device holds before it overwrites the oldest.
- Test it: disconnect the network for an hour while punches occur, reconnect, and
  confirm every punch arrives **exactly once** in `PunchEvent`.
- Confirm normal fingerprint/face use is unaffected while the backlog drains.

Record observed behaviour, sanitised payload samples, timestamp format and
firmware version. If push proves awkward, raise it — do not quietly restore a
polling fallback.

---

## 5. Repository and branch

Private repo: `https://github.com/ajay016/attendance_system.git`
(ask Ajay for a collaborator invite if the clone fails.)

```bash
git clone https://github.com/ajay016/attendance_system.git
cd attendance_system
git checkout -b feature/device-ingestion
git push -u origin feature/device-ingestion
```

**Branch rules**

- Never commit to `main`. Work on `feature/device-ingestion`.
- Open a Pull Request into `main` when a slice is complete. Ajay reviews it.
- Before opening a PR, pull in anything already merged:
  `git checkout main && git pull origin main`, then
  `git checkout feature/device-ingestion && git merge main`.
- Keep branches short-lived: one slice per PR, not one giant long-running branch.

---

## 6. Local setup

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in **your own** PostgreSQL credentials.
You get the schema from migrations; you never receive Ajay's data.

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

`createsuperuser` prompts for an **email**, not a username — the project uses a
custom `accounts.User` with email login.

`.env` is git-ignored. Never commit it.

Optional demo data, development only and guarded by `DEBUG`:

```bash
python manage.py seed_demo
```

---

## 7. Collision rules (both streams work in parallel)

| Area | Rule |
|---|---|
| Migrations | Add **only** `devices/migrations/*`. Never touch `accounts`, `tenants`, `organization`, `employees`, `scheduling`, `access_control` or `auditlog` migrations. |
| `config/settings.py` | Append-only. `'devices'` is already in `INSTALLED_APPS`. Nothing else without asking. |
| `config/urls.py` | Add one `include()` for your ingestion routes. Nothing else. |
| `common/*` | **Ask first.** `common/models.py`, `common/tenant.py` and `common/db.py` are shared foundations owned by Ajay. |
| `base_template/*` | Shared UI and design tokens, owned by Ajay. Reuse existing CSS classes; never introduce a new colour. |
| Database | Your own local PostgreSQL. Never point at Ajay's database. |

Your dependencies (`organization.Branch`, `employees.Employee`) already exist and
are migrated. You should not need to change them.

---

## 8. Required reading, in order

1. `docs/DEVICE_ATTENDANCE_POLICY.md` — **authoritative** for scope, pairing,
   historical authorization, and the worked examples you must verify.
2. `docs/MODEL_FIELD_DICTIONARY.md` sections 22-31 — agreed field contract for
   all 10 device models.
3. `docs/PROJECT_HANDOFF.md` — business context and why push replaced polling.
4. `docs/TEAM_LEAD_PLAYBOOK.md` — slice safeguards (cold-start test, no dead
   controls, read and write ship together). These apply to you too.
5. `docs/IMPLEMENTATION_ROADMAP.md` — the P3 and D1 sections.
6. `WARM_PAPER_INK_SPEC.md` and `design_reference/` — only if you build any UI.

---

## 9. Suggested order of work

1. Models for the `devices` app (all 10), with the constraints and indexes named
   in the dictionary. Migration in `devices/migrations/` only.
2. Ingestion endpoint plus durable `DeviceMessage` capture, raw payload preserved.
3. `PunchEvent` extraction with idempotency keys and dedupe status.
4. Employee resolution through dated `DeviceEnrollment`.
5. Device-scope authorization writing `authorization_status` and
   `authorization_snapshot`.
6. Simulator and fixtures covering the policy document's scenarios.
7. Device management UI: device list/register/edit, enrollment, device settings
   with the on-device setup instructions, sync health, raw message log and the
   unresolved queue (section 2a).
8. **Then** the real SenseFace 2A: walk its settings menus and document them,
   capture payloads, verify the ack contract, run the offline/backlog test.
9. Publish the `PunchEvent` contract for the attendance engine.

Steps 1-7 need no hardware at all. Step 7 can and should be built against the
simulator, so the screens are ready before the device is wired in.

---

## 10. Acceptance gate

A slice is done only when every one of these holds:

- Replaying the same fixture twice produces **no duplicate attendance effect**.
- An unauthorized-device punch is **preserved** and marked, never dropped.
- `attendance_enabled = false` excludes the punch under **every** scope.
- A punch from an offline period is judged by **historical** policy, or marked
  `policy_unresolved` for review.
- Cross-tenant isolation holds: no company can see another company's devices or
  punches.
- Tests pass on PostgreSQL, `manage.py check` is clean, and
  `makemigrations --check --dry-run` reports no drift.
- Simulator evidence and real-device evidence are reported **separately**. A
  passing simulator is never presented as proof that the SenseFace 2A works.
- An administrator can register a device, enroll an employee on it, change its
  settings and see its sync health **entirely in the browser** — no shell, no
  Django admin. Cold-start rule from the playbook applies.
- No dead controls: every button either works or is disabled with a stated
  reason.
- The screen tells the administrator exactly what to enter on the physical
  device (server address, port, path, comm key) rather than assuming they know.
- Screens use the existing design tokens and components, with no new colours,
  and are checked at 1440, 768 and 375 px.

---

## 11. Reporting back

At each checkpoint report: files and functions changed, workflows implemented,
checks actually run with their output, known limitations, any new
`.env.example` variables, and the exact next task.

Do not claim a phase is complete because its tables exist.
