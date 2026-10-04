# API plan — the whole project, phase by phase

Written 2026-10-04 for Nihal and his senior. This is the plan the API is built
to; each phase is built **with its documentation and tests at the same time**,
pushed, and checked before the next one starts.

---

## 1. What is asked

- An API for **everything** the website does, A to Z: every create, read,
  update, delete and action on every page, from creating a company to
  logging in.
- Used by **a mobile app, a separate web frontend (React/Vue), a desktop app
  and the ERP**.
- **Strong security** (the senior suggested HMAC).
- **Documentation written together with each endpoint**, never at the end, so
  nothing is missed. Clear, well organised, easy to follow.
- **Phase by phase**, not all at once.

## 2. The decisions

### 2.1 Django REST Framework, inside this project

The API is a new Django app, `api`, in this same project — not a separate
service. It uses the same models, the same company isolation
(`TenantOwned`, `use_company`), the same permission rules (`can()`, roles,
branch access) and **the same service functions** the website uses
(`create_employee`, `change_placement`, `recalculate`, `start_load`, …).

The rule that keeps the API and the website identical:

> **An API view never writes to a model itself. It validates the request,
> calls the same service the web page calls, and returns the result.**

So a fix in a service fixes both, every business rule (overlapping
placements, finalised payroll, one device per company, …) holds in the API
from day one, and the website is not touched.

Packages: `djangorestframework`, `drf-spectacular` (OpenAPI 3 / Swagger /
ReDoc), `djangorestframework-simplejwt` (tokens). Nothing else to run on the
server — same Gunicorn, same `git pull` and restart.

### 2.2 Security: two kinds of client, two kinds of login

HMAC is an excellent choice — **for the right kind of client.** HMAC signing
proves the caller holds a secret without ever sending it, and the signature
covers the exact request (method, path, body, time), so a request cannot be
altered or replayed. But the secret has to sit **inside the client**:

| Client | Who uses it | Can it keep a secret? | Login |
|---|---|---|---|
| ERP, server-to-server integrations, an unattended desktop sync agent | a machine, on a company's own server | **Yes** — on a server only its owner can read | **HMAC-signed API key** |
| Mobile app, React/Vue frontend, desktop app used by people | a person, on a phone / browser / PC | **No** — anyone can unpack an app or read a web page's code and take the secret out, for everyone | **Email + password → short-lived token** |

A secret built into a mobile app or a web page is not a secret: one person
extracts it and can sign requests as anyone, and it cannot be revoked for one
user without breaking every install. So:

**People (mobile, frontend, desktop app) — JWT tokens**
- `POST /api/v1/auth/login` with email + password → an **access token**
  (valid 15 minutes) and a **refresh token** (30 days).
- Every request: `Authorization: Bearer <access token>`.
- The refresh token **rotates** on every use and the old one is
  **blacklisted**; logout revokes it. A stolen refresh token used twice
  revokes the whole session.
- Each login is a **session** the person (and the company admin) can see and
  end: "Samsung A54, Dhaka, last used 2 min ago — Sign out".
- Login is throttled (wrong passwords lock out for a while), and every login
  is in the audit log.

**Machines (ERP, integrations) — HMAC-signed API keys**
- The company owner/admin creates an **API key** in the software: a key ID
  and a secret (shown once). The key has **scopes** (e.g. read attendance,
  write employees), optional **IP allow-list** and **expiry**, a "last used"
  time, and can be revoked at once.
- Every request carries:
  ```
  X-Api-Key: <key id>
  X-Timestamp: 2026-10-04T10:15:00Z
  X-Nonce: <random, once>
  X-Signature: hex(HMAC-SHA256(secret, canonical request))
  ```
  where the canonical request is `METHOD \n PATH \n QUERY \n TIMESTAMP \n
  NONCE \n SHA256(BODY)`.
- The server refuses a request older than 5 minutes, a nonce seen before
  (replay), a wrong signature, a revoked or expired key, an address outside
  the allow-list, or anything outside the key's scopes.
- The secret is stored **encrypted** (the server must read it to check a
  signature), never shown again, never logged.
- The documentation gives working signing code in **Python, PHP and
  JavaScript**, plus a test endpoint that says exactly what is wrong with a
  signature.

**For everyone:** HTTPS only, per-user / per-key rate limits, and every write
recorded in the audit log with who did it and through which session or key.

### 2.3 Conventions (the same everywhere)

- **Base URL:** `/api/v1/…`. A change that would break a client goes to `v2`;
  `v1` keeps working.
- **Company:** a person with several companies sends `X-Company: <company id>`;
  it is checked against their memberships on every request. An API key
  belongs to one company.
- **JSON**, `snake_case`; times are ISO 8601 with the company's offset
  (`2026-10-04T09:02:13+06:00`); dates `YYYY-MM-DD`.
- **IDs:** the public UUID where a record has one, otherwise its number —
  stated on every endpoint.
- **Lists:** `?page=` and `?page_size=` (at most 100), `?search=`,
  `?ordering=`, and the filters each list documents. Every list answers
  `{"count", "next", "previous", "results"}`.
- **Errors**, always one shape:
  ```json
  {"error": {"code": "validation_error",
             "message": "Some fields are not valid.",
             "fields": {"email": ["Another company already uses this email."]}}}
  ```
  with `401` (not signed in), `403` (not allowed), `404`, `409` (conflict,
  e.g. finalised payroll), `422` (validation), `429` (too many requests).
  The messages are the same ones the website shows.
- **Actions** that are not plain CRUD are verbs on the resource:
  `POST /employees/{id}/end-employment`, `POST /payroll/runs/{id}/finalise`.
- **Files** (photos, documents, imports, exports): multipart upload; downloads
  as a file response or a short-lived link.

### 2.4 Documentation, built with the code

Two layers, both updated in the same commit as the endpoint:

1. **Reference — generated from the code (OpenAPI 3).** Every endpoint is
   annotated with its summary, permissions, parameters, request and response
   examples and errors. Served as **Swagger UI** (`/api/docs/`, try it live),
   **ReDoc** (`/api/redoc/`, easy reading) and the raw schema
   (`/api/v1/schema/`) — which also makes a **Postman collection**.
2. **Guide — written by hand, in `docs/api/`.** One page per phase, in plain
   words: what the area is, the flows step by step (e.g. "register a device
   and load employees onto it"), a table of every endpoint, and copy-paste
   examples (curl, and Python/JS where it helps).

**A phase is not done until every endpoint in it has, in the same commit:**
the view and serializer · the permission check · tests (works; refused without
permission; another company's data is invisible; validation errors) · its
OpenAPI annotation with examples · its line in the guide · a changelog entry.
A test fails the build if any endpoint is missing from the schema or has no
description — so nothing can be left out of the documentation.

## 3. The phases

Order: foundation first, then **root → company → branch → employee**, then
the areas that build on employees, then the employee's own app, then reports
and integrations. Each phase is its own branch, built, documented, tested
(full suite), pushed, and reported to you before the next starts.

| Phase | Area | What it covers (every page and action in it) | ≈ endpoints |
|---|---|---|---|
| **0** | **Foundation & login** | API app, versioning, error format, paging, rate limits, company header. **Auth:** login, refresh, logout, me, my companies, my sessions (list / end), change password, forgot / reset password. **API keys:** create, list, revoke, rotate, scopes, allow-list; HMAC check; signature test endpoint. Docs: Getting started, Authentication (with signing code), Conventions, Errors, Paging. Swagger + ReDoc. | 20 |
| **1** | **Root (platform)** | Companies: list, create with administrator, detail, edit, status (trial / active / suspended), features on/off, members (add administrator, change role/status). Platform dashboard. | 15 |
| **2** | **Company & branches** | Company profile and logo, mail settings. Branches: list, create, edit, status. Departments (adoption: list, create, copy, edit, status). Designations: list, create, edit, status. Access: who may do what (designation / department permissions, per-person overrides). | 30 |
| **3** | **Employees** | List / search / filter, create, CSV import (upload, preview, confirm, demo file), profile, edit details / personal / placement / salary (with the "latest save wins" rule), line manager, photo, education, documents, end employment, inactive / active periods, leave policy and adjustments, late and overtime settings, report visibility, device permissions, logins (give, password, disable, enable, role). | 45 |
| **4** | **Shifts & calendar** | Overview, attendance settings (incl. "first and last scan"), shifts (create, edit, status), department shifts, employee shifts, weekly offs (create, start, end), holidays (list, create, year, edit, cancel). | 20 |
| **5** | **Devices** | Devices: list, register, serial check, edit, retire, departments. Connection status and test. Device users: roster, refresh, add / delete one, read back, map automatically, replace old links, copy to another device, add as employees, remove, **load employees (background job + progress)**. Employees ↔ devices: map, bulk map, send. Fingerprints / faces: save, trial. Commands and options, server address change. Enrollments, messages, punches, unresolved queue, attendance rules and recheck. (The device's own `/iclock/` push protocol stays as it is.) | 45 |
| **6** | **Attendance** | Daily list, late entries, "who is in now", calendar month, one day in detail, days to review, fix a day (corrections), missed scans (staff: list, enter, decide; withdraw), manual attendance, Excel / PDF exports. | 20 |
| **7** | **Leave** | Leave types (create, defaults, edit, status), policies and versions, balances, leave records (record, cancel, amend), requests and approvals, documents. | 20 |
| **8** | **Salary (payroll)** | Periods and runs (generate, submit, finalise, send back, reopen), salary settings, components (company and per employee), penalty rules (create, change, stop, waive), payslips (view, email, adjust, correct), overtime decisions, LFA (settings, claims, paid). | 30 |
| **9** | **The employee's own app ("me")** | My profile, details, photo, education; my attendance and a day; report / withdraw a missed scan; my leave (request, withdraw, document); leave inbox for approvers; branch attendance for branch managers; my payslips; my LFA; change password. Built last of the main areas because it uses all of them — and it is what the mobile app needs most. | 25 |
| **10** | **Reports & dashboard** | Every report (daily, weekly, monthly, late, overtime, …) as JSON plus Excel / PDF export, dashboard figures. | 15 |
| **11** | **Integrations & finish** | ERP webhook (settings, test, send a test, events, send again, debug messages, guide), audit log (read), final pass over the whole guide, the Postman collection, a "build a client in 10 minutes" walkthrough. | 15 |

About **300 endpoints** in all. The counts are estimates; each phase starts
by listing its exact endpoints from the web routes, so nothing is missed.

## 4. How each phase is run

1. **List** every page and action in the area (from the web routes and
   services) — that list becomes the guide page's table of contents.
2. **Build** endpoint by endpoint: serializer → view calling the service →
   permission → tests → OpenAPI annotation → guide entry. One endpoint is
   finished before the next starts.
3. **Check:** the area's tests, the "every endpoint documented" test, a look
   through Swagger, then the full suite.
4. **Push** to Ajay's main, with the guide page, and a short report: what was
   added, how to try it, anything to decide.

The website is never changed by an API phase (except where a service needs a
small, shared fix — then both use it, and both are tested).

## 5. What the server needs

- Phase 0 adds three Python packages (`pip install -r requirements.txt` on the
  server once), and a few small tables (API keys, sessions, used nonces).
  After that each phase is the usual `git pull`, `migrate`, restart.
- HTTPS is already in place on the live server; the API refuses plain HTTP
  there.
