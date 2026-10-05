# Integrations: the ERP webhook & the audit log

Two ways the outside world hears from the system: the **ERP webhook**, which
sends each person's attendance to the company's own system as it happens, and
the **audit log**, the record of everything done. **API keys:**
`webhook:manage` (the webhook, except changing where it sends); the audit log
is for people only.

## How the webhook works

```
  scan on a terminal ──▶ the day is worked out ──▶ an event is queued
                                                        │
                       the ERP's https address ◀── sent (several at once)
                                                        │
              received ✓ · refused ✗ · no answer: tried again, waiting longer each time
```

1. The company enters the ERP's **https** address and a **secret key** (or
   makes one here and gives it to the ERP's developer), then switches it on.
2. Each person's **check-in** is sent when they arrive and their
   **check-out** when their day is finished (or, if the ERP wants, after every
   scan; breaks too if asked). Days before *send from* are never sent.
3. Every request carries the secret key (and, if set, a signature). The ERP
   answers which events it took.
4. An event that is not received is tried again by itself - 1, 2, 5, 15
   minutes … up to 12 hours - and then given up; *send again* puts it back.

The ERP's developer gets a guide for exactly this company's settings:
`GET /api/v1/webhook/guide` (Markdown or PDF).

## The endpoints

| What | Endpoint |
|---|---|
| Settings | `GET` / `PATCH /api/v1/webhook` |
| A new secret key | `POST /api/v1/webhook/secret` (shown once) |
| Test | `POST /api/v1/webhook/test` (the connection) · `POST …/test-event` (a typed-in check-in) |
| What was sent | `GET /api/v1/webhook/events?status=failed` |
| Send | `POST /api/v1/webhook/send-now` · `POST …/send-again` |
| Debug | `GET` / `POST /api/v1/webhook/debug` (every call and answer, 15 minutes) |
| The developer's guide | `GET /api/v1/webhook/guide?file_type=pdf` |
| Audit log | `GET /api/v1/audit-log` |

### Setting it up

```json
PATCH /api/v1/webhook
{"url": "https://erp.example.com/api/webhook/attendance",
 "secret": "the key the ERP's developer gave you", "is_active": true}

POST /api/v1/webhook/test
→ {"ok": true, "title": "Connected", …}
```

A failed test says what went wrong and what to do (`title`, `detail`, `fix`):
the address cannot be found, the certificate is wrong, the key is refused, …
Then `POST …/test-event` sends a check-in typed in by hand, in the real
format, to try the whole way before a terminal is connected.

### Stricter than the page

- **The secret keys are never shown back.** `has_secret` says one is saved; a
  key made with `POST /webhook/secret` is shown once, in that answer.
- **Where the attendance goes is changed only by a person.** An API key with
  `webhook:manage` reads the settings and events, tests and sends - but cannot
  change the address or the keys.
- The address must be **https** and on the public internet: a private or
  local network address is refused.

## The audit log

`GET /api/v1/audit-log` - who did what, when, from where, and what it was
before and after: every change made in the panels or the API, every login
event, every download. Newest first; a record can never be changed or
removed.

```
GET /api/v1/audit-log?action=leave.&from=2026-10-01&to=2026-10-31
GET /api/v1/audit-log?actor=hr@example.com
GET /api/v1/audit-log?object_type=employees.employee&object_id=41
```

`action` ending in a dot matches every action that starts with it. Only the
company's owner or administrator, logged in, reads it - never an API key.
Passwords, tokens and secret keys are never in it.
