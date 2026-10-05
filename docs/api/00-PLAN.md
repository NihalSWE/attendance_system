# API plan — the whole project, phase by phase

**Version 4 — 2026-10-04.** Agreed with Nihal and his senior:
- an API for **everything** the company, branch and employee panels do, A to
  Z, including everything between the software and the devices;
- **no API for the root (platform)**: the root is the main owner, is never
  created through the software, and keeps its own panel;
- **panels and API side by side**: a company can work in the panels, through
  the API, or both — the same data and the same rules either way;
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

### 1.0 Panels and API, side by side

The software has three panels, and the API mirrors them:

| Panel (today) | Who | The API for the same people |
|---|---|---|
| **Company panel** | owner, company admin | everything they can do in the panel |
| **Branch panel** | branch manager, line manager | the same, limited to their branches / team, exactly as in the panel |
| **Employee panel** ("me") | every employee with a login | their own profile, attendance, missed scans, leave, payslips |

Nothing moves from the panels to the API: each company chooses to use the
panels, the API, or both. A person has the **same permissions** in both —
the API uses the panels' own permission checks. The root (platform) has no
API; it keeps its panel. Each endpoint's documentation says which panel roles
may use it.

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
| *(no JWT package)* | tokens are opaque random values, only their SHA-256 stored (built in phase 1): a session ends at once, nothing stays valid until it expires |
| `drf-spectacular` | OpenAPI 3 schema (feeds our docs site) and the Swagger link |
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
X-Timestamp: 1791100800            (Unix seconds)
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
- **Tokens:** opaque random values (only their SHA-256 is stored); access
  token **10 minutes**; refresh token **30 days**, **rotated on every use**.
  **Reuse detection:** if an old refresh token is ever presented again, that
  whole session is ended at once (it means someone copied it) — except within
  60 seconds, a retry after a lost answer.
- **Session secret** (mobile/desktop): issued **once, at login**, kept for the
  session's life (a refresh is itself signed with it, so it is never sent
  again), stored by the app in the system's secure storage (Android
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
  unless it says so (only `ping`, `login`, password reset and the documentation
  site are public).
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

### 4.1 Our own documentation site

A custom documentation site at **`/api/docs/`**, in the style the senior chose
(like nomadly.cloud/api/docs). **Swagger UI stays as an extra link**
(`/api/swagger/`), plus the raw OpenAPI schema (`/api/v1/schema/`) and a
Postman collection.

**Layout:** a menu on the left grouped by area (Start here · Company &
branches · Employees · Shifts · Devices · Attendance · Leave · Salary · My
account · Reports · Reference), the page in the middle, previous / next at
the bottom.

**Every endpoint has its own page with:**

1. **Title and one-line summary**, the **method + path** with a copy button,
   and **who may use it** (company admin / branch manager / employee / API
   key scope).
2. **What it does** — a few plain bullets — and its **rate limit**.
3. **Description** — the rules in plain words (e.g. "an earlier date replaces
   the history from that date; never reaches into a finalised payroll").
4. **Request parameters — you send:** a table of *name · required · in
   (path / query / header / body) · type · description*. **Nested objects and
   lists open with a ⊕ plus icon** to show the fields inside, level by level,
   with **Expand all / Collapse all**. Allowed values, formats, limits and
   defaults are written on each field.
5. **Request example:** the full request, ready to copy, in **cURL, JavaScript,
   Python, PHP, Kotlin, Swift, C# and Dart** — each one doing the complete
   login / signing, not just the HTTP call — plus the example JSON body.
6. **Response fields — you receive:** the same nested ⊕ tree for the
   response.
7. **Response example:** real-looking example data for the success answer.
8. **Errors this endpoint can return:** each with its **code**, **HTTP
   status**, **title**, **what it means**, **how to fix it** and an **example
   response** — and a link to the full error reference.

**Reference pages:** Getting started · Logging in & request signing (every
client type, step by step) · Conventions (IDs, times, lists, company header)
· **Error reference — every error message of the whole API on one page**,
grouped, each with its meaning, fix and example · Rate limits · Webhooks ·
Security · Changelog.

### 4.2 One source, so the site can never drift from the code

The pages are **generated from the code**, not written separately:
- descriptions, examples, rate limits, roles and the errors an endpoint
  returns are declared **on the endpoint** itself;
- every field's explanation is written **on the field** (in the serializer),
  so the nested ⊕ trees are always exactly what the endpoint accepts and
  returns;
- every error lives in **one error catalogue** (`api/core/errors.py`: code,
  status, title, meaning, fix, example) — the error reference page and each
  endpoint's error list both come from it, and the API answers with exactly
  those codes.

The plain-language **guide pages** for each area (`docs/api/`, the flows step
by step) are written by hand alongside.

### 4.3 "Done" — checked by tests

An endpoint is done only when, in the same commit, it has: view + serializer
· permission · tests (incl. security tests) · summary, description, "what it
does", example request and response · an explanation on **every** field ·
its error list · its guide line · its changelog line. **A test fails the build
if any endpoint, any field or any error is missing its documentation**, so
nothing can be forgotten.

## Part 5 — The phases at a glance

| # | Phase | When it is done, the API can… | ≈ endpoints |
|---|---|---|---|
| 0 | **Foundation & documentation site** | answer `ping`; errors, paging, versioning, company header, rate limits; the custom docs site (menu, endpoint pages, nested ⊕ fields, multi-language examples, error reference) and its tests; Swagger link | 3 |
| 1 | **Security & login** | log people in (tokens, signing, cookies, two-step), sessions, passwords; API keys with HMAC | 28 |
| 2 | **Company & branches** | company profile, branches, departments, designations, access rules | 30 |
| 3 | **Employees** | everything on the employee pages, creation to end of employment | 45 |
| 4 | **Shifts & calendar** | shifts, attendance settings, weekly offs, holidays | 20 |
| 5 | **Devices — setup** | register, edit, retire devices; connection and test; device rules | 15 |
| 6 | **Devices — data flow (device ⇄ software)** | everything the device sends, everything sent to it, every process between; punch push for other devices | 40 |
| 7 | **Attendance** | daily list, calendar, day detail, corrections, missed scans, review, exports | 20 |
| 8 | **Leave** | types, policies, balances, records, requests, approvals | 20 |
| 9 | **Salary (payroll)** | runs, settings, components, penalties, payslips, overtime, LFA | 30 |
| 10 | **Employee panel ("me")** | everything an employee / approver does for themself | 25 |
| 11 | **Reports & dashboard** | every report as data and Excel / PDF; dashboard figures | 15 |
| 12 | **Integrations & finish** | ERP webhook, audit log; final review of all docs; Postman collection | 15 |

About **305 endpoints**. Order: foundation → security → company → branch →
employee → what builds on employees → the employee panel → reports →
integrations. (No root/platform phase: the root keeps its own panel.)

---

## Part 6 — Each phase in detail

Paths are under `/api/v1`.

### Phase 0 — Foundation & documentation site

**Goal:** the base every phase stands on. No login yet (only `ping` is open).

- The `api` app, `api/core/` and the per-area folder pattern.
- Versioned routing `/api/v1/`; the error format (with reference IDs); paging;
  ordering and search helpers; the `X-Company` resolver (wired to auth in
  phase 1); `Idempotency-Key` support; rate-limit framework; strict JSON
  parsing and body-size limits; secure headers and CORS configuration.
- **The documentation site (Part 4):** the layout and menu, the endpoint page
  (method/path with copy, what it does, rate limit, description, request
  parameters, nested ⊕ field trees with Expand / Collapse all, request
  examples in cURL / JavaScript / Python / PHP / Kotlin / Swift / C# / Dart,
  response fields and example, the endpoint's errors), the **error catalogue
  and error reference page**, rate limits page, previous / next. Built once;
  every later endpoint page is generated by it.
- OpenAPI schema, **Swagger link**; the **documentation tests** (every
  endpoint, field and error documented; every endpoint declares its
  permission).
- Endpoints: `GET /ping` · `GET /schema` · the docs site `/api/docs/` ·
  Swagger `/api/swagger/`.
- Guide: `01-getting-started.md` · `03-conventions.md` · `04-errors.md` ·
  `05-how-the-api-is-built.md` (for the team: the pattern, how to add an
  endpoint step by step) · `CHANGELOG.md`.

### Phase 1 — Security & login

**Goal:** every way to log in, and everything around it (Part 2).

| Group | Endpoints |
|---|---|
| Login | `POST /auth/login` · `POST /auth/login/two-step` (the code, when required) · `POST /auth/refresh` · `POST /auth/logout` |
| Browser frontend | `GET /auth/web/csrf` · `POST /auth/web/login` · `POST /auth/web/login/two-step` · `POST /auth/web/refresh` (cookie mode; log out with `POST /auth/logout`) |
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
Part 2, for the senior and for auditors) · `07-postman.md` (testing with the
Postman collection, `/api/docs/postman.json`, which signs every request).

**Status: DONE 2026-10-04.** Added the same day, at the senior's request:
two-step codes **by email** as the second way (the authenticator app stays the
main one) — email-only setup for those who do not want an app, and an email
code at login for app users without their phone.

### Phase 2 — Company & branches

| Group | Endpoints |
|---|---|
| Company | `GET /company` · `PATCH /company` · `PUT/DELETE /company/logo` · `GET/PATCH /company/mail-settings` · `POST /company/mail-settings/test` |
| Branches | `GET /branches` · `POST /branches` · `GET /branches/{id}` · `PATCH /branches/{id}` · `POST /branches/{id}/status` |
| Departments | `GET /departments` · `POST /departments` · `GET /departments/{id}` · `PATCH /departments/{id}` · `POST /departments/{id}/status` · `POST /departments/copy` (branch → branch, as the panel) |
| Designations | `GET /designations` · `POST /designations` · `GET /designations/{id}` · `PATCH /designations/{id}` · `POST /designations/{id}/status` |
| Access | `GET /access/permissions` · `GET /access/people` · `GET/PUT /access/people/{employee_id}` (the panel's per-person, per-branch grid - access by designation or department no longer exists in the panel) |
| Lookups | filters on the lists: `GET /departments?branch_id=` · `GET /designations?department_id=` |

Guide: `20-company-and-branches.md`.

**Status: DONE 2026-10-05.** Each endpoint uses its panel page's gate
(`PanelRule`, the panel's `SelfServiceGate` rule), form (`api/core/forms.py`)
and service. Found and fixed on the way: the panel's "Remove the logo" crashed
on save.

### Phase 3 — Employees

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

**Status: DONE 2026-10-05**, in two parts. Part 1: list, choices, create,
profile, history, details, personal, placement, salary, line manager, end
employment, inactive, active. Part 2: photo, education, documents, login,
leave policy and adjustments, overtime, report visibility, set as line
manager, device permissions, import (check, then confirm - stateless, the same
file twice) and the demo file. Recording leave and late approval from the
profile go with Leave (phase 8) and Attendance (phase 7). The late rules are
the company's (Shifts, phase 4).

### Phase 4 — Shifts & calendar

| Group | Endpoints |
|---|---|
| Overview & settings | `GET /schedule` · `GET/PATCH /attendance-settings` |
| Shifts | `GET/POST /shifts` · `GET/PATCH /shifts/{id}` · `POST /shifts/{id}/status` |
| Department shifts | `GET/POST /department-shifts` |
| Employee shifts | `GET/POST /employees/{id}/shifts` · `POST /employees/{id}/shifts/{id}/end` |
| Weekly offs | `GET/POST /weekly-offs` · `POST /weekly-offs/{id}/start` · `POST /weekly-offs/{id}/end` |
| Holidays | `GET/POST /holidays` · `POST /holidays/year` · `PATCH /holidays/{id}` · `POST /holidays/{id}/cancel` |

Guide: `40-shifts-and-calendar.md`.

**Status: DONE 2026-10-05.** Also `GET /shifts/{id}`, `GET /holidays/{id}`,
`GET /attendance-settings`; weekly offs take weekday names.

### Phase 5 — Devices: setup

| Group | Endpoints |
|---|---|
| Devices | `GET/POST /devices` · `GET /devices/serial-check` · `GET/PATCH /devices/{id}` · `POST /devices/{id}/retire` · `GET /device-models` |
| Setup | `GET /devices/{id}/setup` (what to type on the terminal) |
| Connection | `GET /devices/connections` · `GET /devices/{id}/connection` · `POST /devices/{id}/test-connection` · `GET /devices/{id}/test-connection/{test}` |
| Departments | `GET/POST /devices/{id}/departments` · `POST /devices/{id}/departments/{id}/end` |
| Rules | `GET/PATCH /devices/attendance-rules` |

Guide: `50-devices-setup.md`.

### Phase 6 — Devices: data flow (device ⇄ software)

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

### Phase 7 — Attendance

| Group | Endpoints |
|---|---|
| Lists | `GET /attendance` · `GET /attendance/late` · `GET /attendance/now` |
| Calendar & day | `GET /attendance/calendar` · `GET /attendance/days/{employee}/{date}` |
| Review & fix | `GET /attendance/review` · `POST /attendance/days/{employee}/{date}/fix` · `POST /attendance/corrections/{id}/withdraw` |
| Missed scans (staff) | `GET/POST /missed-scans` · `POST /missed-scans/{id}/decide` |
| Exports | `GET /attendance/export` (xlsx / pdf) |

Guide: `70-attendance.md` (incl. how a day is worked out).

### Phase 8 — Leave

| Group | Endpoints |
|---|---|
| Types | `GET/POST /leave/types` · `POST /leave/types/defaults` · `PATCH /leave/types/{id}` · `POST /leave/types/{id}/status` |
| Policies | `GET/POST /leave/policies` · `GET/PATCH /leave/policies/{id}` · `POST /leave/policies/{id}/status` · `POST /leave/policies/{id}/versions` · `PATCH/DELETE /leave/policies/{id}/versions/{v}` |
| Balances | `GET /leave/balances` |
| Records | `GET/POST /leave/records` · `POST /leave/records/{id}/cancel` · `POST /leave/records/{id}/amend` · `GET /leave/records/{id}/document` |
| Approvals | `GET /leave/requests` · `POST /leave/requests/{id}/decide` |

Guide: `80-leave.md`.

### Phase 9 — Salary (payroll)

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

### Phase 10 — Employee panel ("me")

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

### Phase 11 — Reports & dashboard

| Group | Endpoints |
|---|---|
| Reports | `GET /reports` · `GET /reports/{name}` · `GET /reports/{name}/export` — every report |
| Dashboard | `GET /dashboard` |

Guide: `110-reports.md`.

### Phase 12 — Integrations & finish

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
