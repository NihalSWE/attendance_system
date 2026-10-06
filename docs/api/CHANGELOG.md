# Changelog

Every endpoint added or changed, newest first.

## 2026-10-06 — Department filter; attendance read from what is saved

- **New:** `department_id` on `GET /api/v1/attendance`, `GET /attendance/late`
  and `GET /attendance/export` - the department they were placed in that day.
- **Faster:** attendance is read from what is saved; only what time alone has
  changed is rebuilt, at most every 5 minutes (changes still show at once).

## 2026-10-05 — The last two panel downloads

Found by checking every panel page and action against the API:

- **New:** `GET /api/v1/employees/export` - the Employees list as Excel or
  PDF, with the list's filters; pay only where you may see it, and none for
  an API key without `payroll:read`.
- **New:** `GET /api/v1/attendance/calendar/export` - one person's month as a
  PDF calendar.

## 2026-10-05 — Phase 12: integrations and the finish

- **New:** the ERP webhook - settings, a new secret key (shown once), test
  the connection, send a test attendance, what was sent, send now, send
  again, debug messages, the developer's guide.
- **New:** `GET /api/v1/audit-log` - the company's audit records (owner or
  administrator, logged in).
- **New guides:** *Integrations*, and *Build a client in 10 minutes* (its code
  is run by the tests against a live server).
- **Security:** refused signatures and replays are written to the
  `api.security` server log; HSTS and the https redirect can be switched on in
  `.env`; the whole API is swept by the tests (see *Security*).

## 2026-10-05 — Phase 11: reports and the dashboard

- **New:** `GET /api/v1/reports` - the reports you may open; run any of the
  13 (`GET /reports/{slug}`), or download it as Excel or PDF (`…/export`).
- **New:** `GET /api/v1/dashboard` - headcount, newest people, devices not
  calling in, people still in after their shift.

## 2026-10-05 — Phase 10: My account (the employee app)

- **New:** `/api/v1/me` - home; profile, details, photo, education.
- **New:** my attendance - a month, a day; report and withdraw a missed scan.
- **New:** my leave - the year (types, balances, taken), requests, ask,
  withdraw, the document.
- **New:** my payslips (finalised months) and their PDF; my LFA - may I,
  claim, claims, withdraw, the proof.
- **New:** a branch manager's day - their people and how each day went.

## 2026-10-05 — Phase 9, part b: salary - settings, components, penalty rules, LFA

- **New:** salary settings (currency, pay day) and dated salary rules.
- **New:** allowances and deductions - the company's list; give one to a
  person from a date, end it.
- **New:** penalty rules - list, one, add, change from a month, stop.
- **New:** LFA - settings, eligibility, claims (list, one, enter for someone,
  decide, cancel, mark paid, the proof).

## 2026-10-05 — Phase 9, part a: salary - months, payslips, overtime

- **New:** a month's salary - where it stands, its payslips; generate,
  submit, approve, send back, undo finalise.
- **New:** payslips - one in full, the PDF, email it; bonus and deduction
  lines on a draft; corrections for a finalised month.
- **New:** waive a penalty, undo a waiver.
- **New:** overtime - a month's days, one day, approve (all or some) or
  reject, undo.

## 2026-10-05 — Phase 8: leave

- **New:** leave types - list, add, change, turn on or off, add the defaults.
- **New:** leave policies - list, add, change, turn on or off; versions of
  the rules (add; change or remove one not started yet).
- **New:** balances - each person's given, taken and left per type and year.
- **New:** leave - list (a month or a range), one, record (also from the
  profile; with a document), change, cancel all or some days, the document.
- **New:** approval - the requests to decide, one, approve or reject.

## 2026-10-05 — Phase 7: attendance

- **New:** the daily list and late entries (`GET /attendance`,
  `GET /attendance/late`), with the panel's filters; download as Excel or
  PDF (`GET /attendance/export`).
- **New:** who is in now; one person's month (`/attendance/calendar`); one
  day in full, with every scan and the fixes made.
- **New:** the days to review, and fixing a day - add a scan, change the
  status, accept it as it is, approve a late arrival; withdraw a fix.
- **New:** missed scans - list, approve or reject; enter missing attendance
  for someone (`POST /employees/{id}/missing-attendance`).

## 2026-10-05 — Phase 6, part b: devices - telling them

- **New:** ask a device for its users, fingerprints and faces, settings or
  scans (`POST …/commands`); one user - send again, remove, ask about; many
  users - remove, copy to another device; link by Employee ID, replace old
  links, add device users as employees; load a new device; save templates;
  change a setting.
- **New:** the server address - status, change (checked first), cancel.
- **New:** from the Employees list - map one, bulk map a branch, send to
  devices (branch managers too, for their branch).
- **New:** `POST /api/v1/ingest/punches` - other makes of device push their
  scans (API key with `punches:write`); same pipeline as the terminals.

## 2026-10-05 — Phase 6, part a: devices - what they sent

- **New:** device messages (list, one - raw text for people only), punches
  (list with filters, one with its raw record and frozen judgement), punches
  to sort out (list, counts).
- **New:** for one device - its users (joined to employees, with why someone
  is not linked), saved fingerprints and faces (counts and formats, never the
  templates), its settings, its command queue and its work in progress.
- **Guide:** [Devices: data flow](../guides/devices-data-flow/) - how a scan
  travels, every status explained.

## 2026-10-05 — Phase 5: devices - setup

- **New:** devices - list, register, one, change, retire; `serial-check`,
  `device-models`; `…/setup` (what to type on the terminal).
- **New:** connections (a live board) and the connection test (start, then
  ask how it went).
- **New:** department mappings (list, add, end), enrollments (list, add, one,
  change), and which devices count (`attendance-rules`: get, change, recheck).
- **Who:** the owner or an unrestricted company administrator, as on the
  panel. **Keys:** `devices:read` / `devices:write`.
- **Guide:** [Devices: setup](../guides/devices-setup/).

## 2026-10-05 — Phase 4: shifts & calendar

- **New:** `GET /api/v1/schedule` (is everyone covered by a shift) and the
  attendance settings (`GET`/`PATCH /api/v1/attendance-settings`).
- **New:** shifts (list, add, one, change, status), department shifts (list on
  a day, set from a date), one employee's own shift (list, give, end).
- **New:** weekly offs (list, add by weekday names, change start, stop) and
  holidays (list by year, add one, add many, one, change, cancel).
- **Keys:** `shifts:read` / `shifts:write`.
- **Guide:** [Shifts & calendar](../guides/shifts-and-calendar/).

## 2026-10-05 — Phase 3, part 2: the rest of the profile, and import

- **New:** the photo (`GET`/`PUT`/`DELETE …/{id}/photo`), education
  (`…/education`, `…/education/{row_id}`), documents (`…/documents`,
  `…/documents/{document_id}`) - files as base64 in JSON, served privately.
- **New:** their login - `GET`, `POST` (give), `PATCH` (access) `…/{id}/login`,
  `…/login/password`, `…/login/disable`, `…/login/enable`.
- **New:** `…/leave-policy` and `…/leave-adjustments` (keys: `leave:write`),
  `…/overtime`, `…/report-visibility`, `…/reports` (set as line manager),
  `…/devices` and `…/devices/{enrollment_id}` (keys: `devices:write`).
- **New:** `POST /api/v1/employees/import` (check, then `confirm`) and
  `GET /api/v1/employees/import/demo-file`.

## 2026-10-05 — Two-step login: email codes stay as an option

At the senior's word: a person chooses the authenticator app (recommended)
or **email codes only** (`{"method": "email"}` on setup); app users keep the
email backup at login. *Who am I* and confirm show `method` again; the login
answer has `email_sent_to` for an email user (their code is sent already).

## 2026-10-05 — Phase 3, part 1: employees

- **New:** `GET /api/v1/employees` (the Employees list: filters, paging, pay
  only where you may see it), `GET /api/v1/employees/choices` and
  `POST /api/v1/employees` (Create employee).
- **New:** `GET /api/v1/employees/{id}` (the profile) and `…/history`.
- **New:** changes — details (`PATCH /api/v1/employees/{id}`), `…/personal`,
  `…/placement` and `…/salary` (dated: later date keeps history, same or
  earlier replaces), `…/line-manager`.
- **New:** `…/end-employment`, `…/inactive`, `…/active`.
- **Keys:** `employees:read` / `employees:write`; pay needs `payroll:read`,
  changing it `payroll:write`.
- **Guide:** [Employees](../guides/employees/).

## 2026-10-05 — Two-step login: the app first, email as the backup

At the senior's decision: the authenticator app is the main way; a code by
email is the backup for everyone, so a lost phone never locks anyone out.

- **Removed:** setting two-step login up with email codes only
  (`method` on `POST /api/v1/auth/two-step/setup`). Anyone set up that way
  sets up the app at their next login.
- **Changed:** `POST /api/v1/auth/two-step/setup` with a current `code` moves
  the app to a new phone (`replacing: true`); the old phone works until the
  new one is confirmed. Confirm answers `email_backup`; *Who am I* shows
  `two_step.email_backup` instead of `two_step.method`. The login answer no
  longer emails a code by itself (no `email_sent_to`): the person asks for it.
- **New:** the platform owner can reset a locked-out person's two-step login
  (Django admin).

## 2026-10-05 — Phase 2: company & branches

- **New:** the company — `GET`/`PATCH /api/v1/company`, the logo
  (`PUT`/`DELETE /api/v1/company/logo`, the file as base64 in JSON) and the
  email settings (`GET`/`PATCH /api/v1/company/mail-settings`,
  `POST …/mail-settings/test`).
- **New:** branches, departments and designations — list (search, status and
  parent filters, paged), create, one, change, status; copy a branch's
  departments into another (`POST /api/v1/departments/copy`).
- **New:** access — `GET /api/v1/access/permissions`, `GET /api/v1/access/people`,
  `GET`/`PUT /api/v1/access/people/{employee_id}` (the panel's grid).
- **Rules:** each endpoint applies its panel page's gate, form and service — the
  same checks and messages. API keys: `company:read` / `company:write`; email
  settings and access are for people only.
- **Guide:** [Company & branches](../guides/company-and-branches/).

## 2026-10-04 — Two-step login by email

- **New:** codes by email as the second way of two-step login, beside the
  authenticator app (still the main way). `POST /api/v1/auth/two-step/setup`
  takes `{"method": "app" | "email"}`; a login set up with email gets its code
  emailed at each login.
- **New:** `POST /api/v1/auth/login/two-step/email-code` — at login, a code by
  email instead of the app. `POST /api/v1/auth/two-step/email-code` — the same
  when logged in (confirm, change the way, turn off, recovery codes).
- **Changed:** the login's two-step answer also has `methods` (and
  `email_sent_to` when a code was emailed); the challenge now lasts
  10 minutes; *Who am I* shows `two_step.method`; confirm answers `method`.
- **New errors:** `email_not_available`, `email_not_sent`.

## 2026-10-04 — Phase 1: security & login

- **New — apps:** `POST /api/v1/auth/login`, `/login/two-step`, `/refresh`,
  `/logout`. Opaque tokens (access 10 minutes, refresh 30 days, rotated once
  per use) and every request signed with HMAC-SHA256.
- **New — browser frontends:** `GET /api/v1/auth/web/csrf`,
  `POST /api/v1/auth/web/login`, `/web/login/two-step`, `/web/refresh` —
  HttpOnly cookies plus CSRF.
- **New — the login:** `GET /api/v1/auth/me`; my sessions (list, end one, sign
  out everywhere else); password change, forgot, reset; two-step login (setup,
  confirm, disable, recovery codes) — required for owners and administrators.
- **New — company:** staff sessions (`/api/v1/company/sessions`) and API keys
  (`/api/v1/api-keys`: create, change, rotate with grace, revoke, scopes).
- **New:** `POST /api/v1/auth/signature-test` — checks a signature step by step.
- **Guides:** [Logging in & signing](../authentication/),
  [Security](../security/) and [Testing with Postman](../postman/). Request
  examples now sign in all eight languages.
- **New:** the [Postman collection](../postman.json) — every endpoint, signed
  for you; log-in requests save the tokens by themselves.
- **Server:** passwords stored with Argon2; changing a password (here or in the
  panels) ends that login's API sessions.

## 2026-10-04 — Phase 0: foundation & documentation site

- **New:** `GET /api/v1/ping` — is the API up, and the server's time.
- **New:** the documentation site at `/api/docs/`, the error reference, rate
  limits, and the Swagger link (`/api/swagger/`, schema `/api/v1/schema/`).
- **Rules for every endpoint from now on:** one error shape with a reference,
  paged lists (`page`, `page_size` ≤ 100), unknown fields refused, the
  `Idempotency-Key` header for safe retries, rate limits per kind of endpoint,
  `X-Request-Id` on every answer.
