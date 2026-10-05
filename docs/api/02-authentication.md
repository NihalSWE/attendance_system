# Logging in & signing

There are three ways in. Pick the one for what you are building:

| You are building | How it logs in | How each request proves who it is |
|---|---|---|
| A **mobile or desktop app** used by people | `POST /api/v1/auth/login` with email + password | the access token **and** a signature |
| A **browser frontend** (React, Vue, …) | `POST /api/v1/auth/web/login` with email + password | secure cookies **and** a CSRF token |
| A **machine** (an ERP, a sync program, a server) | no login — an **API key** the company creates | a signature |

Whoever logs in can do through the API exactly what they can do in their
panel — no more. A login with several companies picks one per request with the
`X-Company: <company id>` header (`GET /api/v1/auth/me` lists them).

The platform's root login cannot use the API (`platform_login_not_allowed`);
it keeps its own panel.

---

## 1. Mobile and desktop apps

### Step 1 — log in

```
POST /api/v1/auth/login
{"email": "rahim@example.com", "password": "…", "client_type": "mobile",
 "device_name": "Rahim's Samsung A54"}
```

The answer:

```json
{
  "two_step_required": false,
  "session_id": "ses_8Kd2…",
  "access_token": "at_Yp3…",
  "access_expires_in": 600,
  "refresh_token": "rt_9Qf…",
  "refresh_expires_at": "2026-11-03T10:15:00+06:00",
  "signing_secret": "ss_Hq7…",
  "two_step_setup_required": false
}
```

Keep all of it in the system's **secure storage** (Android Keystore /
EncryptedSharedPreferences, iOS Keychain, Windows Credential Manager) — never
in plain files or in the code.

| Value | What it is for | Lives |
|---|---|---|
| `session_id` | names this login; sent as `X-Key-Id` | the whole session |
| `access_token` | sent as `Authorization: Bearer …` | 10 minutes |
| `refresh_token` | swapped for a new pair | 30 days, **works once** |
| `signing_secret` | signs every request; **never sent** | the whole session — shown only now |

**If `two_step_required` is true**, the answer has only the challenge and
`methods` — the ways this login can pass:

```json
{"two_step_required": true, "challenge": "ch_…", "challenge_expires_in": 600,
 "methods": ["app", "email", "recovery_code"]}
```

Ask the person for a code and send it, within 10 minutes:

```
POST /api/v1/auth/login/two-step
{"challenge": "ch_…", "code": "123456"}
```

- `app` — the 6-digit code from their authenticator app (the main way).
- `email` — a code by email: for an app user the backup, *"Email me a code
  instead"* (`POST /api/v1/auth/login/two-step/email-code` with
  `{"challenge": "ch_…"}`); for a login set up with email codes it is sent
  already (`email_sent_to`). Send it the same way.
- `recovery_code` — one of the 10 recovery codes.

The answer is the same as above.

### Step 2 — call the API

Every request carries the access token **and** a signature (see
[Signing a request](#signing-a-request)):

```
Authorization: Bearer at_Yp3…
X-Key-Id: ses_8Kd2…
X-Timestamp: 1791090900
X-Nonce: 4f1c9a…
X-Signature: 9d2e…
```

Why both? The token says *who*; the signature proves the request came from the
app that logged in and was not changed or replayed on the way. A stolen token
alone is useless without the signing secret.

### Step 3 — stay logged in

Before the access token expires (about every 9 minutes, or when a request
answers `token_expired`), swap the refresh token:

```
POST /api/v1/auth/refresh
{"refresh_token": "rt_9Qf…"}
```

Sign this request too, but send **no** `Authorization` header. The answer is a
new `access_token` **and a new `refresh_token`** — save the new one and forget
the old. The signing secret stays the same.

- A refresh token works **once**. If an old one is ever used again, it may have
  been copied: the whole session ends (`refresh_token_reused`) and the person
  logs in again.
- One exception: sending the same token again **within 60 seconds** counts as a
  retry after a lost answer (a bad connection), not a copy.
- After 30 days without a refresh, or when the session is ended, log in again.

### Step 4 — log out

`POST /api/v1/auth/logout` (signed, with the token). The session ends at once;
forget everything you stored.

### When does a session end without logging out?

- The person signs it out from another device (`/api/v1/auth/sessions`).
- A company owner or administrator signs it out (`/api/v1/company/sessions`).
- The password is changed — here **or in the panels** — or reset.
- An old refresh token is reused.

The next request then answers `session_ended`: send the person to the login
screen.

---

## 2. Browser frontends (React, Vue, …)

A browser cannot keep a secret safe from scripts on the page, so a browser
frontend does **not** sign. It uses cookies the page's JavaScript cannot read
(`HttpOnly`), plus Django's CSRF protection.

Ask the server's administrator to add your frontend's address to
`API_CORS_ORIGINS`, and call the API with `credentials: "include"`.

```js
const BASE = "https://attendance.example.com";

// 1. A CSRF token (also set as the csrftoken cookie).
const { csrf_token } = await (await fetch(BASE + "/api/v1/auth/web/csrf",
                                          { credentials: "include" })).json();

// 2. Log in - the session arrives as cookies.
await fetch(BASE + "/api/v1/auth/web/login", {
  method: "POST", credentials: "include",
  headers: { "Content-Type": "application/json", "X-CSRFToken": csrf_token },
  body: JSON.stringify({ email, password, device_name: "Chrome on the office PC" }),
});

// 3. Every later call: credentials "include"; X-CSRFToken on POST/PUT/PATCH/DELETE.
const me = await (await fetch(BASE + "/api/v1/auth/me", { credentials: "include" })).json();
```

- If the login answers `two_step_required`, send the code to
  `POST /api/v1/auth/web/login/two-step` (same cookies and CSRF header).
- The access cookie lasts 10 minutes: call `POST /api/v1/auth/web/refresh`
  (with the CSRF header) before then, or when a call answers `token_expired`.
- A missing or wrong CSRF token answers `csrf_failed`.
- Log out with `POST /api/v1/auth/logout`; the cookies are removed.

---

## 3. Machines: API keys

A company owner or administrator creates a key (`POST /api/v1/api-keys`) and
gives it only the **scopes** the machine needs (`employees:read`,
`attendance:read`, …), optionally an **IP allow-list** and an **expiry**. The
answer shows the key's `secret` **once**.

The machine signs every request with the key — no `Authorization` header:

```
X-Key-Id: ak_P2v9QmX3tL7c
X-Timestamp: 1791090900
X-Nonce: 4f1c9a…
X-Signature: 9d2e…
```

- A key works for its own company only (`X-Company` is not needed).
- It can never do more than the person who created it — and it stops working
  if that person no longer manages the company.
- **Rotate** (`POST /api/v1/api-keys/{key_id}/rotate`): a new secret; the old
  one keeps working for up to 24 hours so the machine can switch without a gap.
- **Revoke** (`DELETE /api/v1/api-keys/{key_id}`): it stops at once.

Errors you may meet: `invalid_api_key`, `api_key_revoked`, `api_key_expired`,
`scope_missing`, `ip_not_allowed`.

---

## Signing a request

Apps and API keys sign the same way. Every endpoint page's request example
does all of this for you in eight languages — copy it.

**1. Build the canonical request** — six lines joined with `\n` (no newline at
the end):

```
METHOD                      POST
PATH                        /api/v1/auth/logout
SORTED QUERY                page=2&page_size=50   (empty line when none)
TIMESTAMP                   1791090900            (Unix seconds, now)
NONCE                       4f1c9a2b7e8d…         (random, new every request)
SHA-256 OF THE BODY         e3b0c442…             (hex; of the empty string when no body)
```

- **METHOD** in capitals; **PATH** exactly as requested, without the server
  and without `?…`.
- **SORTED QUERY**: the `name=value` pairs as sent, sorted alphabetically,
  joined with `&`.
- **NONCE**: 16–64 characters of `A–Z a–z 0–9 _ -`; never reuse one.
- **BODY**: hash **exactly the bytes you send** — build the JSON string once,
  hash it, send that same string.

**2. Sign it**: `HMAC-SHA256(secret, canonical request)`, as lowercase hex.

**3. Send** `X-Key-Id`, `X-Timestamp`, `X-Nonce`, `X-Signature`.

In Python:

```python
import hashlib, hmac, secrets, time

def sign(secret, method, path, query, body):
    ts = str(int(time.time()))
    nonce = secrets.token_hex(16)
    sorted_query = "&".join(sorted(p for p in query.split("&") if p))
    body_hash = hashlib.sha256(body.encode()).hexdigest()
    canonical = "\n".join([method, path, sorted_query, ts, nonce, body_hash])
    signature = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return {"X-Timestamp": ts, "X-Nonce": nonce, "X-Signature": signature}
```

### The server refuses a request when

| Error | Why | Fix |
|---|---|---|
| `missing_signature_headers` | one of the four headers is missing | send all four |
| `timestamp_out_of_range` | the time is more than 5 minutes off | sync the device clock (`GET /api/v1/ping` gives the server's time) |
| `replay_detected` | this nonce was used before | a new random nonce every request |
| `invalid_signature` | the signature does not match | compare your canonical request with the server's (below) |
| `signature_required` | an app request without a signature | sign every app request |

### Stuck? Test the signature

Send your signed request to `POST /api/v1/auth/signature-test`. It answers,
without doing anything, the canonical request **the server** built, the body
hash it computed, and a list of problems in plain words. Put it next to yours,
line by line — the difference is the bug.

---

## Two-step login

Owners and company administrators **must** use two-step login on the API (any
app: mobile, desktop or a browser frontend); everyone else may. The web panels
are not affected. Until an owner or administrator has set it up, their session
answers `two_step_setup_required` for everything except *Who am I*, the
two-step requests and logging out.

Each person chooses one of two ways:

| Way | How it works |
|---|---|
| **Authenticator app** (`app`) — recommended | an app on the phone (Google Authenticator, Microsoft Authenticator, Authy, …) shows a new 6-digit code every 30 seconds; works offline |
| **Code by email** (`email`) — optional | for someone who does not want an app: each login emails a 6-digit code to the login's email |

**App users always have email as the backup.** When the app cannot be used —
the phone is lost or not at hand, the app was deleted, its account is gone —
*"Email me a code instead"* gets them in. Nothing to set up. And everyone has
**10 recovery codes** as the last resort.

Email codes need a mail account — the company's (*Organisation → Email
settings*) or the server's. `email_backup` (in *Who am I* and in the confirm
answer) says whether this server can send them; without it, email codes
cannot be chosen and the app users' backup is the recovery codes.

**Setting it up — the app:**

1. `POST /api/v1/auth/two-step/setup` with `{"method": "app"}` (or no body) →
   a `secret` and an `otpauth_url`. Show the URL as a QR code; the person scans
   it (or types the secret) in the authenticator app.
2. `POST /api/v1/auth/two-step/confirm` with the 6-digit code from the app →
   it is on, with **10 recovery codes** (shown once).

**Setting it up — email codes only:**

1. `POST /api/v1/auth/two-step/setup` with `{"method": "email"}` → a code is
   emailed (`email_sent_to` says where).
2. `POST /api/v1/auth/two-step/confirm` with that code → it is on, with 10
   recovery codes.

**Logging in:** the login answers `methods`:

- an app user: `["app", "email", "recovery_code"]` — ask for the app's code and
  offer *"Email me a code instead"*
  (`POST /api/v1/auth/login/two-step/email-code` with the challenge);
- an email user: `["email", "recovery_code"]` — their code is emailed already
  (`email_sent_to`); the same endpoint sends another.

Any of the codes goes to `POST /api/v1/auth/login/two-step`.

**Changing the way, or a new phone:**

1. Get a current code: from the app, by email
   (`POST /api/v1/auth/two-step/email-code`) or a recovery code.
2. `POST /api/v1/auth/two-step/setup` with the new `method` and that `code` →
   a new secret to scan (app) or a code by email (email); `replacing: true`.
3. `POST /api/v1/auth/two-step/confirm` with a code of the new way. Until then
   the old way keeps working.

A lost phone: log in with a code by email (or a recovery code), then do the
above.

**Nothing works at all** (no phone, no email getting through, no recovery
codes): the platform owner resets the person's two-step login (Django admin →
*Two steps* → delete). An owner or administrator then sets it up again at
their next login.

**Codes by email, the rules:** 6 digits; work once, for 10 minutes; stop after
5 wrong tries; one email a minute (`rate_limited` with `Retry-After`).

New recovery codes: `POST /api/v1/auth/two-step/recovery-codes` with a code
(the old ones stop). Turn it off (not allowed for owners and administrators):
`POST /api/v1/auth/two-step/disable` with the password and a code.

---

## Passwords

- **Change**: `POST /api/v1/auth/password/change` — every other session of the
  login ends; this one stays.
- **Forgot**: `POST /api/v1/auth/password/forgot` with the email. The answer is
  always the same (so nobody can test which emails exist); if the login exists,
  a reset code (valid 30 minutes) is emailed.
- **Reset**: `POST /api/v1/auth/password/reset` with the code and the new
  password — every session of the login ends.

## Too many wrong passwords

5 wrong passwords in 15 minutes lock the login for 15 minutes; 10 in an hour
lock it for an hour; 20 wrong tries from one address in 15 minutes lock that
address. The answer is `login_locked` with a `Retry-After` header (seconds).
