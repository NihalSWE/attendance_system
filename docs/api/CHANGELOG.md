# Changelog

Every endpoint added or changed, newest first.

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
