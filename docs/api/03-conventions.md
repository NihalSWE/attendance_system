# Conventions

The rules every endpoint follows, so you learn them once.

## Address and version

- Base address: `https://<your server>/api/v1/`.
- Paths have no trailing slash: `/api/v1/ping`, `/api/v1/employees/{id}`.
- A breaking change makes a new version (`/api/v2/`). Adding a new field to an
  answer or a new optional parameter is not breaking — **ignore fields you do
  not know**, so your client keeps working when we add one.

## Requests

- Send JSON with `Content-Type: application/json` (uploads: `multipart/form-data`,
  stated on the endpoint).
- **Unknown fields are refused** (`unknown_field`) — a typo in a field name is
  reported, never silently ignored.
- Names are `snake_case`: `first_name`, `work_date`.

## Dates and times

- Dates: `YYYY-MM-DD`, e.g. `2026-10-04`.
- Times of day: `HH:MM` (24-hour), e.g. `09:00`.
- Moments: ISO 8601 with the offset, e.g. `2026-10-04T09:02:13+06:00`. Moments
  you send may use any offset (or `Z` for UTC); the server stores the exact
  moment.
- Attendance days follow the company's time zone, as in the panels.

## IDs

Each endpoint says which ID it uses: a record's public UUID where it has one
(`"5d1e…"`), otherwise its number. IDs come from the list endpoint of that
kind of record. A record of another company is never shown — asking for one
answers `404 not_found`, the same as a record that does not exist.

## Which company

A login that belongs to one company needs nothing more. A login in **several
companies** sends the company on every request:

```
X-Company: 12
```

The company must be one the login is an active member of. An API key always
belongs to exactly one company. (Arrives with logging in, phase 1.)

## Lists

Every list is paged:

| Parameter | Meaning |
|---|---|
| `page` | page number, from 1 |
| `page_size` | results per page: 25 unless given, 100 at most |
| `search` | free text, where the list says so |
| `ordering` | a field name to sort by; `-` in front for descending, e.g. `-created_at` |

Plus each list's own filters, listed on its page. Every list answers:

```json
{"count": 132,
 "next": "https://<your server>/api/v1/employees?page=2",
 "previous": null,
 "results": [ ... ]}
```

## Actions

Creating, reading, changing and deleting use `POST`, `GET`, `PATCH`/`PUT` and
`DELETE`. Anything else is a named action on the record, always a `POST`:

```
POST /api/v1/employees/{id}/end-employment
POST /api/v1/devices/{id}/load
```

## Retrying safely: `Idempotency-Key`

On a poor connection you may not know whether a request arrived. Send a
unique value (a random UUID) in the `Idempotency-Key` header with any `POST`,
`PUT`, `PATCH` or `DELETE`:

```
Idempotency-Key: 6f1c9a2e-0b7d-4e55-9a3a-2f6d0c1b8e44
```

If you send the same request again with the same key within **24 hours**, you
get the first answer back (with the header `Idempotent-Replay: true`) and
nothing is done twice. Use a new key for every new action; reusing a key for a
different request is refused (`idempotency_key_reused`).

## Every answer

- `X-Request-Id`: a reference for this request. Errors carry the same value as
  `reference`. Send it when you ask for help.
- Errors: always the shape in the [Error reference](/api/docs/errors/).
- Too many requests: `429 rate_limited` with `Retry-After` — see
  [Rate limits](/api/docs/rate-limits/).

## Security basics

- **HTTPS only** on the live server.
- Never put passwords, tokens or secrets in a URL — they belong in headers or
  the body.
- Store secrets in the system's secure storage (Android Keystore, iOS
  Keychain, Windows Credential Manager), never in plain files or source code.
