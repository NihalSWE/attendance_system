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
  the API. The main way is an authenticator app (TOTP). The backup, so nobody
  is locked out by a lost phone, is a 6-digit code by email (10 minutes, once,
  5 wrong tries, one email a minute, only its hash stored); the last resort is
  10 recovery codes (stored hashed). Each code works once. Moving the app to a
  new phone needs a current code and a confirm from the new phone. The
  platform owner can reset a locked-out person in the Django admin.
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

- Serve the API over **HTTPS** only.
- Behind nginx set `API_PROXY_COUNT=1` — otherwise every request seems to come
  from nginx's address and the address lockout would lock everyone together.
- Keep `SECRET_KEY` secret and unchanged (see *Signed requests*).
