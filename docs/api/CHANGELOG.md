# Changelog

Every endpoint added or changed, newest first.

## 2026-10-04 — Phase 0: foundation & documentation site

- **New:** `GET /api/v1/ping` — is the API up, and the server's time.
- **New:** the documentation site at `/api/docs/`, the error reference, rate
  limits, and the Swagger link (`/api/swagger/`, schema `/api/v1/schema/`).
- **Rules for every endpoint from now on:** one error shape with a reference,
  paged lists (`page`, `page_size` ≤ 100), unknown fields refused, the
  `Idempotency-Key` header for safe retries, rate limits per kind of endpoint,
  `X-Request-Id` on every answer.
