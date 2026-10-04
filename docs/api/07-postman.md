# Testing with Postman

A ready Postman collection has **every endpoint**, with its example body, and
**signs each request for you** — no code needed. It is built from the same
declarations as this documentation, so it is always complete: download it
again after an update to get the new endpoints.

## 1. Import the collection

1. Download it: **[attendance-api.postman_collection.json](../postman.json)**
   (also in the menu: *Reference → Postman collection*).
2. In Postman: **Import** → drop the file in → **Import**.

A collection *Attendance Management API v1* appears, with one folder per part
of the API (*Logging in*, …).

> **Testing a server on your own computer** (`http://localhost:8000`,
> `http://127.0.0.1:8000`)? Postman **in the web browser** cannot reach it.
> It answers *"Cloud agent error: cannot send request"* or *"localhost request
> not supported"*, and the request never arrives at the server. Use the
> **Postman desktop app**, or install the **Postman Desktop Agent** and choose
> it in the agent selector (bottom right of the Postman web page). A server on
> the internet (e.g. `https://attendance.example.com`) works from any of them.

## 2. Set your login

Click the collection's name → the **Variables** tab:

| Variable | Set it to |
|---|---|
| `base_url` | the server, e.g. `http://127.0.0.1:8000` or `https://attendance.example.com` — no `/` at the end. Already filled with the server you downloaded from. |
| `email` | the login's email |
| `password` | the login's password |

Press **Save** (Ctrl+S). Leave the other variables empty — the requests fill
them in.

## 3. Log in

Open **Logging in → Log in (apps)** and press **Send**.

The answer's tokens are saved into the variables by themselves (`key_id`,
`secret`, `access_token`, `refresh_token`). From now on **every request is
signed automatically** — open any request and press Send.

**If the answer says `"two_step_required": true`** (owners and company
administrators): open **Log in: the two-step code (apps)**, put the 6-digit
code from the authenticator app in the body's `"code"`, and Send. (The
`challenge` was saved for you.)

**If the answer says `"two_step_setup_required": true`**: first run
**Set up two-step login**, add the `otpauth_url`/`secret` to an authenticator
app, then **Confirm two-step login** with a code. Until then only two-step
setup and logout work.

## 4. After 10 minutes

The access token expires after 10 minutes (`token_expired`). Run
**Refresh the tokens (apps)** — the new tokens are saved — and carry on.
After 30 days, or after logging out, log in again.

## How the signing works in Postman

Signed requests have these headers (see each request's **Headers** tab):

```
Authorization: Bearer {{access_token}}
X-Key-Id:      {{key_id}}
X-Timestamp:   {{x_timestamp}}
X-Nonce:       {{x_nonce}}
X-Signature:   {{x_signature}}
```

The collection's **pre-request script** (collection → *Scripts* →
*Pre-request*) fills in the last three just before each request is sent. It
prints the canonical request it signed in the **Postman Console** (bottom
left, *Console*) — handy to compare with your own code.

**Edit the body freely**: the script signs the body exactly as it is sent.

## Testing an API key

1. Logged in as the owner or administrator, run **Create an API key** (edit
   the body: name, scopes). Its `api_key_id` and `api_key_secret` are saved.
2. In the collection's Variables, set `key_id` to the `api_key_id` value,
   `secret` to the `api_key_secret` value, and **empty `access_token`**. Save.
3. Requests are now signed with the key (the script leaves out the
   Authorization header by itself).

To go back to your login, run **Log in (apps)** again.

## A login with several companies

Set `company_id` in the Variables, and in the request's Headers **tick**
`X-Company` (it is there, unticked, on every request).

## Browser (web) endpoints

Postman keeps cookies like a browser, so the web flow works too:

1. **Get the CSRF token (web)** — the token is saved as `csrf_token`.
2. **Log in (web)** — the session cookies are kept by Postman.
3. Web requests send `X-CSRFToken: {{csrf_token}}` by themselves.

Most people test with the apps flow (step 3 above); the web flow is for
checking a browser frontend's calls.

## Something is refused?

| Answer | What to do |
|---|---|
| no answer, *"Cloud agent error"* / *"localhost request not supported"* | Postman on the web cannot reach your computer — use the desktop app or the Desktop Agent (see step 1) |
| `not_authenticated` / `signature_required` | run **Log in (apps)** first; check `key_id` and `secret` are filled |
| `token_expired` | run **Refresh the tokens (apps)** |
| `invalid_token` / `session_ended` | log in again |
| `invalid_signature` | open the Postman Console and run **Test a signature** — it lists what is wrong |
| `timestamp_out_of_range` | the computer's clock is off: set it to update automatically |
| `two_step_setup_required` | see step 3 |
| `csrf_failed` (web) | run **Get the CSRF token (web)** again |

Every error's meaning and fix: the [Error reference](../errors/).
