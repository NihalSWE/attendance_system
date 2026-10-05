# Security

How the API keeps the companies' data safe — for the team running the
server and for anyone reviewing it. How to log in is in
[Logging in & signing](../authentication/).

## Who can do what

- **Deny by default.** An endpoint without a declared permission refuses
  everyone (`DenyAll`); the documentation test fails if one is missing.
- **The same rules as the panels.** Endpoints call the same services the
  panels use; a person can do through the API exactly what their panel lets
  them, in the companies they are an active member of.
- **One company per request.** Every request works inside one company (the
  key's company, or `X-Company` for a person); other companies' records are
  never visible — the same tenant guard as the panels.
- **No platform (root) access.** The root login is refused by the API.

## Logins and tokens

- **Passwords** are stored with **Argon2** (old hashes are upgraded at the
  next login). Wrong passwords lock the login and the address for a while
  (`login_locked` with `Retry-After`).
- **Two-step login** is required for owners and company administrators on
  the API. Each person chooses an authenticator app (TOTP, recommended) or
  codes by email only; app users always have a code by email as the backup,
  so a lost phone locks nobody out. Email codes: 10 minutes, once, 5 wrong
  tries, one email a minute, only their hash stored. The last resort is 10
  recovery codes (stored hashed). Each code works once. Changing the way or
  the phone needs a current code and a confirm of the new way. An email code
  is only as safe as the mailbox - the app is recommended. The platform owner
  can reset a locked-out person in the Django admin.
- **Tokens are random values, not JWTs.** The server keeps only their
  SHA-256, so a stolen database does not give working tokens, and a session
  can be ended **at once** (nothing stays valid until it expires).
- **Short access, rotating refresh.** Access tokens live 10 minutes. Refresh
  tokens work once; reusing an old one ends the whole session.
- **A password change ends sessions** — here or in the panels (each session
  remembers a fingerprint of the password it logged in with).
- People see and end their own sessions; owners and administrators see and end
  their staff's.

## Signed requests

Apps and API keys sign every request with **HMAC-SHA256** over the method,
path, sorted query, a timestamp, a nonce and the SHA-256 of the body.

- The secret is never sent after it is first shown; a copied request cannot be
  changed (the signature would break).
- A request older or newer than **5 minutes** is refused; each **nonce works
  once** (the server remembers them) — a copied request cannot be replayed.
- Secrets are stored **encrypted** (a key derived from the server's
  `SECRET_KEY`), because the server must read them to check signatures.
  Changing `SECRET_KEY` makes every API key and app session stop working.

## Browser frontends

- The session lives in **HttpOnly, Secure, SameSite=Strict** cookies: scripts
  on the page cannot read them.
- Every request that changes data needs the **CSRF token**.
- Only addresses in `API_CORS_ORIGINS` may call the API from a browser.

## API keys

- **Scopes**: a key does only what it was given.
- **IP allow-list** and **expiry**, optional.
- **Never more than its creator**: the key acts as the person who created it,
  and stops if they are no longer the company's owner or administrator.
- **Rotate** with a short grace period (≤ 24 hours); **revoke** at once.

## Limits and records

- **Rate limits** per kind of endpoint (logins have their own, stricter
  limit) — see [Rate limits](../rate-limits/).
- **Audit log**: logins, sessions ended, password and two-step changes, and
  every API-key change are written to the company's audit log, marked as
  coming from the API.
- Every answer has an `X-Request-Id`; an error's `reference` is the same
  value — quote it when reporting a problem.
- Passwords, tokens and secrets are never written to logs.

## For the server administrator

Settings (in `.env`):

| Setting | What it does | Default |
|---|---|---|
| `API_PROXY_COUNT` | how many proxies (e.g. nginx) stand in front; the client's address is read from `X-Forwarded-For` only that deep | `0` |
| `API_CORS_ORIGINS` | the browser frontends allowed to call the API, comma-separated | empty |
| `API_COOKIE_SECURE` | send the session cookies over HTTPS only | on (off when `DEBUG`) |
| `API_COOKIE_SAMESITE` | `Strict`, or `None` for a frontend on another site | `Strict` |
| `API_PASSWORD_RESET_URL` | your frontend's reset page, with `{token}` where the code goes, e.g. `https://app.example.com/reset?token={token}` | empty: the bare code is emailed |
| `API_ACCESS_MINUTES` / `API_REFRESH_DAYS` | token lifetimes | 10 / 30 |
| `SECURE_HSTS_SECONDS` | browsers use https only, for this long (`31536000` = a year) | `0` (off) |
| `SECURE_SSL_REDIRECT` | send http to https | off |
| `SECURE_PROXY_SSL` | behind nginx: trust its `X-Forwarded-Proto` to know a request was https | off |

- Serve the API over **HTTPS** only.
- Behind nginx set `API_PROXY_COUNT=1` — otherwise every request seems to come
  from nginx's address and the address lockout would lock everyone together.
- Keep `SECRET_KEY` secret and unchanged (see *Signed requests*).

## The security review (2026-10-05)

The whole API was checked against the plan's security part (00-PLAN.md,
Part 2) when the last endpoints were added. What holds, and how it is kept so:

| Rule | How it is kept | Proved by |
|---|---|---|
| Deny by default | Every endpoint names who may use it; anything else is refused | the documentation test (a view without a rule fails) and the **sweep**: all 269 non-public endpoints answer 401 without a login |
| The panels' own rules | Each endpoint passes its panel page's gate, then the panel's own service | each area's tests, and the sweep: an Employee login without grants is refused by all 237 company endpoints |
| Company isolation | Every request acts for one company; another company's records answer 404 | each area's tests, and the sweep's cross-company check |
| No server errors on bad ids | Clean 404/403/422 answers | the sweep: all 115 reads with made-up ids |
| Signatures, replays, old timestamps | HMAC-SHA256, one-use nonces, a 5-minute window | the authentication tests |
| Refused signatures and replays logged | `api.security` server log: who, from where, on what - never a secret | `SecurityLogTests` |
| Tokens and secrets at rest | Tokens stored as SHA-256 only; secrets encrypted | the authentication tests |
| API keys | Scopes, company, IP allow-list, expiry, rotation with a grace period, revoke | the key tests; keys cannot use people-only endpoints (My account, the audit log, changing the webhook) |
| Input | Unknown fields refused; the panels' own forms and file checks; 4 MB bodies; pages at most 100 | each area's tests |
| Secrets never shown again | API key and webhook secrets shown once; `GET /webhook` only says one is saved | the key and integration tests |
| HTTPS, HSTS | Settings above, for the live server | - (a deployment setting) |
| A client that works | The guide's own code logs in, signs, refreshes, logs out | `ClientGuideTests`, against a live server |
