# API plan — the whole project, phase by phase

**Version 3 — 2026-10-04.** Agreed with Nihal and his senior:
- an API for **everything** the website does, A to Z, including everything
  between the software and the devices;
- used by a **mobile app, a desktop app, a React/Vue frontend and the ERP**;
- **strong security** — clear and understandable, but never weakened to be
  simpler;
- **documentation built together with every endpoint**, never at the end;
- built **phase by phase**; each phase is finished (code + docs + tests),
  pushed and reported before the next starts;
- the website and the device connection keep working exactly as now.

This file is the plan of record. Each phase below lists exactly what it
delivers; anything found missing while building is added to that phase before
it is called done.

---

## Part 1 — How the API is built

### 1.1 One app, one pattern

- A new Django app, **`api`**, inside this project. Same server, same database,
  same deployment — no second service.
- One folder per area: `api/v1/<area>/` with the same four files every time:
  `serializers.py` (what goes in and out), `views.py` (the endpoints),
  `urls.py`, `tests.py`. Shared pieces live in `api/core/` (authentication,
  permissions, errors, paging, documentation helpers).
- **The rule that keeps everything correct:** an endpoint never changes data
  by itself. It authenticates, checks permission, validates the input, then
  **calls the same service function the website uses** (`create_employee`,
  `change_placement`, `recalculate`, `start_load`, …) and returns the result.
  Every business rule that already exists holds in the API automatically,
  and the website is not changed.

### 1.2 Packages (all mainstream and maintained)

| Package | Why |
|---|---|
| `djangorestframework` | the API framework |
| `djangorestframework-simplejwt` | access / refresh tokens, rotation, blacklist |
| `drf-spectacular` | OpenAPI 3 schema, Swagger UI, ReDoc |
| `django-cors-headers` | which web frontends may call the API |
| `argon2-cffi` | Argon2 password hashing (stronger than the default) |
| `pyotp` | two-step login (authenticator app codes) |

---

## Part 2 — Security

### 2.1 Who calls the API, and how each one proves who it is

| Client | Proves itself with | Every request carries |
|---|---|---|
| **Mobile app, desktop app** (people) | login → **access token** + **refresh token** + **this session's signing secret** | the access token **and** an HMAC signature made with the session secret |
| **React/Vue web frontend** (people, in a browser) | login → tokens in **Secure, HttpOnly, SameSite cookies** (page scripts cannot read them) + **CSRF token** | the cookies (sent by the browser) and the CSRF header |
| **ERP, other servers, sync programs, other makes of device** (machines) | an **API key** the company admin creates (key ID + secret) | the key ID and an HMAC signature made with the key's secret |

**One signing method for everything that is not a browser.** Apps sign with
their session secret, machines with their key secret — the same format, the
same code, the same checks. Two things to learn, not five.

**Why the browser is different:** a web page cannot keep a secret — any script
that gets onto the page could read it. HttpOnly cookies are the one place a
browser keeps a credential that page scripts cannot touch; CSRF tokens stop
other sites from using them.

### 2.2 The signature (HMAC-SHA256)

Each signed request carries four headers:

```
X-Key-Id:    <API key ID, or the session ID for an app>
X-Timestamp: 2026-10-04T10:15:00Z
X-Nonce:     <random value, never used twice>
X-Signature: hex( HMAC-SHA256( secret, canonical request ) )
```

The canonical request is these lines joined by a newline:

```
METHOD            e.g. POST
PATH              e.g. /api/v1/employees
QUERY             the query string, parameters sorted
TIMESTAMP         the X-Timestamp value
NONCE             the X-Nonce value
SHA256(BODY)      hex digest of the exact body bytes (empty body: digest of "")
```

The server refuses the request (401, with a clear reason in the error code)
when: the timestamp is more than **5 minutes** off · the nonce was seen
before (**replay**) · the signature does not match (**tampered**) · the key or
session is unknown, revoked or expired · the address is outside the key's
allow-list. Comparison is constant-time. `GET /api/v1/ping` returns the
server's time so a device with a wrong clock can correct itself. The guide
ships working signing code in **Python, PHP, JavaScript (Node), Kotlin
(Android), Swift (iOS) and C# (desktop)**, and `POST /auth/signature-test`
says exactly which part of a signature is wrong.

### 2.3 Logging in and staying logged in (people)

- **Passwords:** Argon2 hashing (old hashes upgrade on next login), the
  project's existing password rules.
- **Login:** `POST /auth/login` (email, password, device name, client type).
- **Two-step login (authenticator app, TOTP):** **required** for the platform
  owner, company owners and company admins; any user may switch it on.
  10 single-use **recovery codes** for a lost phone.
- **Tokens:** access token **10 minutes**; refresh token **30 days**, **rotated
  on every use** — the old one is blacklisted. **Reuse detection:** if an old
  refresh token is ever presented again, that whole session is ended at once
  (it means someone copied it).
- **Session secret** (mobile/desktop): issued at login, **renewed with every
  refresh**, stored by the app in the system's secure storage (Android
  Keystore, iOS Keychain, Windows Credential Manager); deleted on logout.
- **Sessions:** every login is a session (device name, client type, IP, last
  used). A person sees theirs and can sign any of them out; a company admin can
  sign out their staff. **Changing the password, disabling a login or ending
  employment signs out every session** of that person.
- **Lockout:** repeated wrong passwords or codes lock the login for a growing
  time (5 failures → 15 minutes, …); throttled per account and per IP.
- **Password reset:** a single-use link by email, valid 30 minutes; using it
  signs out every session.

### 2.4 API keys (machines)

- Created by a company owner/admin (with two-step login): name, **scopes**
  (exactly what the key may do — e.g. `attendance:read`, `employees:write`,
  `punches:write`), optional **IP allow-list**, optional **expiry**.
- The secret is shown **once**; stored **encrypted** (the server must read it
  to check signatures), never logged, never shown again.
- **Rotate** (new secret, old one still accepted for a grace period you set,
  up to 24 h) and **revoke** (stops at once). "Last used" time and address
  shown.
- A key belongs to one company and can never see another.

### 2.5 What each caller may do (authorisation)

- **Deny by default:** every endpoint states who may use it; nothing is open
  unless it says so (only `ping`, `login`, password reset and the docs login
  page are public).
- **The website's own permission rules**, unchanged: the role (owner, company
  admin, manager, employee), branch access (`can()`), the special permissions
  (`employees.edit`, `salary.prepare`, …). The API uses the very same checks.
- **Company isolation:** every request is tied to exactly one company (the
  key's, or the `X-Company` a person chose and belongs to). Records of another
  company are invisible — as `404`, never `403`, so their existence is not
  revealed. Tested on every endpoint.
- **API keys** are limited by their scopes *and* by the company.

### 2.6 Protecting the server and the data

- **HTTPS only**, HSTS, secure headers (no sniffing, no framing).
- **CORS:** only the frontend addresses the company lists may call the API
  from a browser.
- **Input:** strict serializers (unknown fields refused), size limits on bodies
  and uploads, file type and size checks (the project's existing ones),
  page size capped at 100.
- **Rate limits:** per IP, per person, per key; stricter on login, password
  reset and two-step codes. `429` with `Retry-After`.
- **Secrets at rest:** session secrets and key secrets encrypted with the
  project's existing encryption key; tokens never stored in plain text
  (only their IDs).
- **Logs:** tokens, secrets, passwords and signatures are never written to a
  log or the audit trail.
- **Errors:** never show internal details (stack traces, SQL) — a code, a
  message and a reference ID for the server log.

### 2.7 Audit and monitoring

- Every write records **who** (person, session or API key), **what**, **when**
  and **from where** (IP), through the existing audit log.
- Logins, failed logins, lockouts, two-step changes, sessions ended, keys
  created / rotated / revoked — all recorded.
- Repeated signature failures or replays from one key or address are logged
  as security events.

### 2.8 The device connection

The ZKTeco terminals' own protocol (`/iclock/…`) is fixed by the device
firmware and cannot sign requests, so it stays as it is, protected as now:
the device must be registered, its serial must belong to exactly one company,
retired or suspended devices are refused. The API adds **no** new way in for
terminals; other makes of device push scans through `POST /ingest/punches`
with a signed API key (`punches:write`).

### 2.9 Security tests (written with each endpoint)

For every endpoint: works for the right caller · refused without login ·
refused without permission · another company's records are invisible ·
invalid input gives the right error. For the security layer itself: expired
token, reused refresh token (whole session ends), wrong / tampered signature,
replayed nonce, old timestamp, revoked key, key outside its scopes, IP outside
the allow-list, lockout after failures, two-step required for admins, password
change signs out everywhere.

---

## Part 3 — Conventions (the same on every endpoint)

- **Address:** `/api/v1/...`. A change that would break a client goes to
  `v2`; `v1` keeps working.
- **Company:** a person in several companies sends `X-Company: <company id>`.
- **JSON**, `snake_case`; times ISO 8601 with the company's offset
  (`2026-10-04T09:02:13+06:00`); dates `YYYY-MM-DD`.
- **IDs:** a record's public UUID where it has one, otherwise its number —
  stated on each endpoint.
- **Lists:** `?page=`, `?page_size=` (≤ 100), `?search=`, `?ordering=`, plus
  each list's filters → `{"count", "next", "previous", "results"}`.
- **Errors** — always this shape, with the website's own words:
  ```json
  {"error": {"code": "validation_error",
             "message": "Some fields are not valid.",
             "fields": {"email": ["Another company already uses this email."]},
             "reference": "c1f4…"}}
  ```
  `400` bad request · `401` not authenticated (with the reason) · `403` not
  allowed · `404` not found · `409` conflict (e.g. finalised payroll) ·
  `422` invalid · `429` too many requests.
- **Actions** beyond create/read/update/delete are verbs on the record:
  `POST /employees/{id}/end-employment`, `POST /devices/{id}/load`.
- **Safe retries:** create and action endpoints accept an `Idempotency-Key`
  header — the same key within 24 h returns the first result instead of
  doing it twice (a mobile app on a bad connection can retry safely).
- **Files:** multipart upload; download as a file.

---

## Part 4 — Documentation, built with the code

1. **Reference, generated from the code (OpenAPI 3):** summary, who may use
   it, scopes, parameters, request and response examples and every error, for
   every endpoint. Shown as **Swagger UI** (`/api/docs/`, try it live) and
   **ReDoc** (`/api/redoc/`); the schema at `/api/v1/schema/` also produces a
   **Postman collection**. The docs pages require a login on the live server.
2. **Guide, written by hand (`docs/api/`):** one page per phase, in plain
   words — what the area is, its flows step by step, a table of every
   endpoint, copy-paste examples, and the errors you will meet.
3. **Changelog (`docs/api/CHANGELOG.md`):** every endpoint added or changed,
   by date and phase.

**"Done" for an endpoint, in one commit:** view + serializer · permission ·
tests (incl. security tests) · OpenAPI annotation with examples · guide line ·
changelog line. **A test fails the build if any endpoint has no
documentation or no permission declared**, so nothing can be skipped.

---

## Part 5 — The phases at a glance

| # | Phase | When it is done, the API can… | ≈ endpoints |
|---|---|---|---|
| 0 | **Foundation** | answer `ping`; errors, paging, versioning, company header, rate limits, docs site and the documentation checks in place | 3 |
| 1 | **Security & login** | log people in (tokens, signing, cookies, two-step), sessions, passwords; API keys with HMAC | 28 |
| 2 | **Root (platform)** | manage companies, status, features, administrators | 15 |
| 3 | **Company & branches** | company profile, branches, departments, designations, access rules | 30 |
| 4 | **Employees** | everything on the employee pages, creation to end of employment | 45 |
| 5 | **Shifts & calendar** | shifts, attendance settings, weekly offs, holidays | 20 |
| 6 | **Devices — setup** | register, edit, retire devices; connection and test; device rules | 15 |
| 7 | **Devices — data flow (device ⇄ software)** | everything the device sends, everything sent to it, every process between; punch push for other devices | 40 |
| 8 | **Attendance** | daily list, calendar, day detail, corrections, missed scans, review, exports | 20 |
| 9 | **Leave** | types, policies, balances, records, requests, approvals | 20 |
| 10 | **Salary (payroll)** | runs, settings, components, penalties, payslips, overtime, LFA | 30 |
| 11 | **The employee's own app ("me")** | everything an employee / approver does for themself | 25 |
| 12 | **Reports & dashboard** | every report as data and Excel / PDF; dashboard figures | 15 |
| 13 | **Integrations & finish** | ERP webhook, audit log; final review of all docs; Postman collection | 15 |

About **320 endpoints**. Order: foundation → security → root → company →
branch → employee → what builds on employees → the employee's own app →
reports → integrations.

---

## Part 6 — Each phase in detail

Paths are under `/api/v1`.

### Phase 0 — Foundation

**Goal:** the base every phase stands on. No login yet (only `ping` is open).

- The `api` app, `api/core/` and the per-area folder pattern.
- Versioned routing `/api/v1/`; the error format (with reference IDs); paging;
  ordering and search helpers; the `X-Company` resolver (wired to auth in
  phase 1); `Idempotency-Key` support; rate-limit framework; strict JSON
  parsing and body-size limits; secure headers and CORS configuration.
- OpenAPI schema, Swagger UI, ReDoc; the **"every endpoint documented and has a
  permission" test**.
- Endpoints: `GET /ping` · `GET /schema` · `GET /docs` (+ ReDoc).
- Guide: `01-getting-started.md` · `03-conventions.md` · `04-errors.md` ·
  `05-how-the-api-is-built.md` (for the team: the pattern, how to add an
  endpoint step by step) · `CHANGELOG.md`.

### Phase 1 — Security & login

**Goal:** every way to log in, and everything around it (Part 2).

| Group | Endpoints |
|---|---|
| Login | `POST /auth/login` · `POST /auth/login/two-step` (the code, when required) · `POST /auth/refresh` · `POST /auth/logout` |
| Browser frontend | `POST /auth/web/login` · `POST /auth/web/refresh` · `POST /auth/web/logout` · `GET /auth/web/csrf` (cookie mode) |
| Me | `GET /auth/me` (who I am, my companies and role in each, two-step status) |
| Sessions | `GET /auth/sessions` · `DELETE /auth/sessions/{id}` · `POST /auth/sessions/sign-out-others` · `GET /company/sessions` · `DELETE /company/sessions/{id}` (admin, for staff) |
| Password | `POST /auth/password/change` · `POST /auth/password/forgot` · `POST /auth/password/reset` |
| Two-step | `POST /auth/two-step/setup` (secret + QR link) · `POST /auth/two-step/confirm` · `POST /auth/two-step/disable` · `POST /auth/two-step/recovery-codes` (new set) |
| API keys | `GET /api-keys` · `POST /api-keys` · `GET /api-keys/{id}` · `PATCH /api-keys/{id}` · `POST /api-keys/{id}/rotate` · `DELETE /api-keys/{id}` · `GET /api-keys/scopes` |
| Signature help | `POST /auth/signature-test` |

New tables: API keys, sessions (with encrypted signing secret), used nonces,
two-step secrets and recovery codes, login attempts. Settings: Argon2 first in
the password hashers.

Guide: `02-authentication.md` (each client type step by step; signing code in
Python, PHP, JavaScript, Kotlin, Swift, C#) · `06-security.md` (the whole of
Part 2, for the senior and for auditors).

### Phase 2 — Root (platform)

| Group | Endpoints |
|---|---|
| Companies | `GET /platform/companies` · `POST /platform/companies` (with administrator) · `GET /platform/companies/{id}` · `PATCH /platform/companies/{id}` |
| Status | `POST /platform/companies/{id}/status` |
| Features | `GET /platform/features` · `POST /platform/companies/{id}/features` |
| Members | `GET /platform/companies/{id}/members` · `POST /platform/companies/{id}/administrators` · `PATCH /platform/companies/{id}/members/{id}` |
| Overview | `GET /platform/dashboard` |

Guide: `10-platform.md`.

### Phase 3 — Company & branches

| Group | Endpoints |
|---|---|
| Company | `GET /company` · `PATCH /company` · `PUT /company/logo` · `GET/PATCH /company/mail-settings` |
| Branches | `GET /branches` · `POST /branches` · `GET /branches/{id}` · `PATCH /branches/{id}` · `POST /branches/{id}/status` |
| Departments | `GET /departments` · `POST /departments` · `POST /departments/{id}/copy` · `PATCH /departments/{id}` · `POST /departments/{id}/status` |
| Designations | `GET /designations` · `POST /designations` · `PATCH /designations/{id}` · `POST /designations/{id}/status` |
| Access | `GET /access` · `GET/PUT /access/designations/{id}` · `GET/PUT /access/departments/{id}` · `GET/PUT /access/people/{id}` |
| Lookups | `GET /branches/{id}/departments` · `GET /departments/{id}/designations` |

Guide: `20-company-and-branches.md`.

### Phase 4 — Employees

| Group | Endpoints |
|---|---|
| List & create | `GET /employees` · `POST /employees` |
| Import | `POST /employees/import` (preview) · `POST /employees/import/{id}/confirm` · `GET /employees/import/demo-file` |
| Profile | `GET /employees/{id}` · `GET /employees/{id}/history` |
| Edit | `PATCH /employees/{id}` · `PATCH /employees/{id}/personal` · `POST /employees/{id}/placement` · `POST /employees/{id}/salary` · `PUT /employees/{id}/line-manager` |
| Photo | `GET /employees/{id}/photo` · `PUT /employees/{id}/photo` |
| Education | `GET/POST /employees/{id}/education` · `PATCH/DELETE /employees/{id}/education/{row}` |
| Documents | `GET/POST /employees/{id}/documents` · `GET/DELETE /employees/{id}/documents/{doc}` |
| Status | `POST /employees/{id}/end-employment` · `POST /employees/{id}/inactive` · `POST /employees/{id}/active` |
| Leave on profile | `PUT /employees/{id}/leave-policy` · `POST /employees/{id}/leave-adjustments` |
| Settings | `PATCH /employees/{id}/late-rules` · `PATCH /employees/{id}/overtime` · `PATCH /employees/{id}/report-visibility` · `PATCH /employees/{id}/devices/{enrollment}` |
| Their login | `POST /employees/{id}/login` · `POST …/login/password` · `POST …/login/disable` · `POST …/login/enable` · `PATCH …/login/role` |

Guide: `30-employees.md`.

### Phase 5 — Shifts & calendar

| Group | Endpoints |
|---|---|
| Overview & settings | `GET /schedule` · `GET/PATCH /attendance-settings` |
| Shifts | `GET/POST /shifts` · `GET/PATCH /shifts/{id}` · `POST /shifts/{id}/status` |
| Department shifts | `GET/POST /department-shifts` |
| Employee shifts | `GET/POST /employees/{id}/shifts` · `POST /employees/{id}/shifts/{id}/end` |
| Weekly offs | `GET/POST /weekly-offs` · `POST /weekly-offs/{id}/start` · `POST /weekly-offs/{id}/end` |
| Holidays | `GET/POST /holidays` · `POST /holidays/year` · `PATCH /holidays/{id}` · `POST /holidays/{id}/cancel` |

Guide: `40-shifts-and-calendar.md`.

### Phase 6 — Devices: setup

| Group | Endpoints |
|---|---|
| Devices | `GET/POST /devices` · `GET /devices/serial-check` · `GET/PATCH /devices/{id}` · `POST /devices/{id}/retire` · `GET /device-models` |
| Setup | `GET /devices/{id}/setup` (what to type on the terminal) |
| Connection | `GET /devices/connections` · `GET /devices/{id}/connection` · `POST /devices/{id}/test-connection` · `GET /devices/{id}/test-connection/{test}` |
| Departments | `GET/POST /devices/{id}/departments` · `POST /devices/{id}/departments/{id}/end` |
| Rules | `GET/PATCH /devices/attendance-rules` |

Guide: `50-devices-setup.md`.

### Phase 7 — Devices: data flow (device ⇄ software)

How it works (explained in the guide with a diagram): the terminal calls the
server every few seconds on its own; each call **uploads** what is new (scans,
users, fingerprints, faces, settings) and **collects** commands waiting for
it. Uploads are stored as *messages*; scans become *punches*; the server
decides whose they are and whether they count, and rebuilds attendance; then
the ERP webhook sends check-ins and check-outs on.

| Direction | Group | Endpoints |
|---|---|---|
| Device → software | Messages | `GET /devices/{id}/messages` · `GET /device-messages/{id}` |
| | Punches | `GET /punches` · `GET /punches/{id}` · `GET /punches/unresolved` · `POST /punches/recheck` |
| | Users on the device | `GET /devices/{id}/users` |
| | Fingerprints & faces | `GET /devices/{id}/templates` · `POST /devices/{id}/templates/save` |
| | Device settings | `GET /devices/{id}/options` |
| Software → device | Ask | `POST /devices/{id}/users/refresh` · `POST /devices/{id}/users/{pin}/read-back` · `POST /devices/{id}/commands` (the safe commands) |
| | Users | `POST /devices/{id}/users` · `DELETE /devices/{id}/users/{pin}` · `POST /devices/{id}/users/remove` · `POST /devices/{id}/users/copy` |
| | Load a device | `POST /devices/{id}/load` · `GET /devices/{id}/load` |
| | Settings & address | `POST /devices/{id}/options` · `POST/GET /devices/{id}/server-address` · `POST /devices/{id}/server-address/cancel` |
| | Queue | `GET /devices/{id}/commands` · `DELETE /devices/{id}/commands/{id}` · `GET /devices/{id}/jobs` |
| People ⇄ device users | Linking | `GET/POST /enrollments` · `PATCH /enrollments/{id}` · `POST /devices/{id}/users/map-automatically` · `POST /devices/{id}/users/replace-old-links` · `POST /devices/{id}/users/import` · `POST /employees/{id}/map` · `POST /employees/bulk-map` · `POST /employees/send-to-devices` |
| Other devices | Push scans | `POST /ingest/punches` (signed API key, `punches:write`) |

Guide: `60-devices-data-flow.md` — the flow end to end, every status
explained, and how to follow one scan from the terminal to the report and the
ERP.

### Phase 8 — Attendance

| Group | Endpoints |
|---|---|
| Lists | `GET /attendance` · `GET /attendance/late` · `GET /attendance/now` |
| Calendar & day | `GET /attendance/calendar` · `GET /attendance/days/{employee}/{date}` |
| Review & fix | `GET /attendance/review` · `POST /attendance/days/{employee}/{date}/fix` · `POST /attendance/corrections/{id}/withdraw` |
| Missed scans (staff) | `GET/POST /missed-scans` · `POST /missed-scans/{id}/decide` |
| Exports | `GET /attendance/export` (xlsx / pdf) |

Guide: `70-attendance.md` (incl. how a day is worked out).

### Phase 9 — Leave

| Group | Endpoints |
|---|---|
| Types | `GET/POST /leave/types` · `POST /leave/types/defaults` · `PATCH /leave/types/{id}` · `POST /leave/types/{id}/status` |
| Policies | `GET/POST /leave/policies` · `GET/PATCH /leave/policies/{id}` · `POST /leave/policies/{id}/status` · `POST /leave/policies/{id}/versions` · `PATCH/DELETE /leave/policies/{id}/versions/{v}` |
| Balances | `GET /leave/balances` |
| Records | `GET/POST /leave/records` · `POST /leave/records/{id}/cancel` · `POST /leave/records/{id}/amend` · `GET /leave/records/{id}/document` |
| Approvals | `GET /leave/requests` · `POST /leave/requests/{id}/decide` |

Guide: `80-leave.md`.

### Phase 10 — Salary (payroll)

| Group | Endpoints |
|---|---|
| Runs | `GET/POST /payroll/runs` · `GET /payroll/runs/{id}` · `POST …/submit` · `…/finalise` · `…/send-back` · `…/reopen` |
| Settings | `GET/PATCH /payroll/settings` |
| Components | `GET/POST /payroll/components` · `PATCH /payroll/components/{id}` · `POST …/status` · `GET/POST /employees/{id}/components` · `POST /employees/{id}/components/{id}/end` |
| Penalties | `GET/POST /payroll/penalty-rules` · `POST …/{id}/change` · `POST …/{id}/stop` · `POST /payroll/penalties/{id}/waive` · `…/unwaive` |
| Payslips | `GET /payroll/payslips/{id}` · `GET …/pdf` · `POST …/email` · `POST …/adjustments` · `POST …/corrections` · `DELETE …/adjustments/{id}` |
| Overtime | `GET /payroll/overtime` · `POST /payroll/overtime/{id}/decide` · `POST …/undo` |
| LFA | `GET/PATCH /payroll/lfa/settings` · `GET/POST /payroll/lfa/claims` · `GET …/{id}` · `POST …/cancel` · `POST …/paid` · `GET …/document` |

Guide: `90-salary.md`.

### Phase 11 — The employee's own app ("me")

| Group | Endpoints |
|---|---|
| Profile | `GET /me/profile` · `PATCH /me/details` · `PUT /me/photo` · `GET/POST/PATCH/DELETE /me/education` |
| Attendance | `GET /me/attendance` · `GET /me/attendance/{date}` |
| Missed scans | `GET/POST /me/missed-scans` · `POST /me/missed-scans/{id}/withdraw` |
| Leave | `GET/POST /me/leave` · `POST /me/leave/{id}/withdraw` · `GET /me/leave/{id}/document` |
| Approver | `GET /me/leave-inbox` · `POST /me/leave-inbox/{id}/decide` · `GET /me/branch-attendance` |
| Salary | `GET /me/payslips` · `GET /me/payslips/{id}` · `GET …/pdf` · `GET/POST /me/lfa` · `POST /me/lfa/{id}/withdraw` |
| Account | `POST /me/password` |

Guide: `100-employee-app.md` (building the mobile app's screens).

### Phase 12 — Reports & dashboard

| Group | Endpoints |
|---|---|
| Reports | `GET /reports` · `GET /reports/{name}` · `GET /reports/{name}/export` — every report |
| Dashboard | `GET /dashboard` |

Guide: `110-reports.md`.

### Phase 13 — Integrations & finish

| Group | Endpoints |
|---|---|
| ERP webhook | `GET/PATCH /webhook` · `POST /webhook/secret` · `POST /webhook/test` · `POST /webhook/test-event` · `GET /webhook/events` · `POST /webhook/send-now` · `POST /webhook/send-again` · `GET/POST /webhook/debug` · `GET /webhook/guide` |
| Audit log | `GET /audit-log` |

Finish: read every guide page again; check Swagger against every website page;
publish the Postman collection; `120-build-a-client.md` (a working client in
10 minutes); a security review of the whole API against Part 2.

Guide: `120-integrations.md`.

---

## Part 7 — How every phase is run

1. **List** the area's pages and actions → the exact endpoint list (the guide
   page's table).
2. **Build one endpoint at a time:** serializer → view calling the service →
   permission → tests (incl. security tests) → OpenAPI annotation → guide line
   → changelog line.
3. **Check:** the area's tests, the documentation test, a walk through
   Swagger, then the **full test suite**.
4. **Push** to Ajay's main with the guide page; report what was added, how to
   try it, anything to decide.
5. **Only then** the next phase.

## Part 8 — What the server needs

- **Phase 0/1:** the new packages (`pip install -r requirements.txt` once),
  a few new tables (`migrate`), and two settings in `.env`: the allowed
  frontend addresses (CORS) and, if wanted, token lifetimes.
- **Every phase after:** `git pull`, `python manage.py migrate`, restart.
- The website and the devices keep working exactly as now throughout.
