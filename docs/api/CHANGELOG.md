# Changelog

Every endpoint added or changed, newest first.

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
