# Build a client in 10 minutes

A complete, working client in about 70 lines of Python: it logs in (with the
two-step code when asked), signs every request, swaps the tokens before they
run out, and reads a few things. The same steps work in any language - the
request examples on every endpoint's page show the signing in eight.

You need Python 3.9+ and nothing else: it uses only the standard library.
(The project's test suite runs this very code against a live server, so it
stays right.)

```python
"""A minimal client for the Attendance API (docs/api/120-build-a-client.md)."""

import getpass
import hashlib
import hmac
import json
import secrets
import time
import urllib.error
import urllib.request

BASE = "https://attendance.example.com"     # your server


class Client:
    def __init__(self, email, password, device="My script"):
        answer = self._send("POST", "/api/v1/auth/login", {
            "email": email, "password": password,
            "client_type": "desktop", "device_name": device}, signed=False)
        if answer.get("two_step_required"):
            code = input(f"Two-step code ({', '.join(answer['methods'])}): ")
            answer = self._send("POST", "/api/v1/auth/login/two-step",
                                {"challenge": answer["challenge"], "code": code}, signed=False)
        self._keep(answer)
        self.signing_secret = answer["signing_secret"]   # shown once: keep it safe
        self.session_id = answer["session_id"]

    def _keep(self, answer):
        self.access_token = answer["access_token"]
        self.refresh_token = answer["refresh_token"]
        self.access_until = time.time() + answer["access_expires_in"] - 60

    def _headers(self, method, path, query, body):
        ts, nonce = str(int(time.time())), secrets.token_hex(16)
        sorted_query = "&".join(sorted(p for p in query.split("&") if p))
        canonical = "\n".join([method, path, sorted_query, ts, nonce,
                               hashlib.sha256(body.encode()).hexdigest()])
        signature = hmac.new(self.signing_secret.encode(), canonical.encode(),
                             hashlib.sha256).hexdigest()
        return {"X-Key-Id": self.session_id, "X-Timestamp": ts, "X-Nonce": nonce,
                "X-Signature": signature}

    def _send(self, method, path, data=None, query="", signed=True, token=True):
        body = json.dumps(data) if data is not None else ""
        headers = {"Content-Type": "application/json"}
        if signed:
            headers.update(self._headers(method, path, query, body))
            if token:
                headers["Authorization"] = f"Bearer {self.access_token}"
        url = BASE + path + (f"?{query}" if query else "")
        request = urllib.request.Request(url, data=body.encode() if body else None,
                                         headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as reply:
                content = reply.read()
        except urllib.error.HTTPError as failed:
            error = json.loads(failed.read())["error"]
            raise RuntimeError(f"{error['code']}: {error['message']} "
                               f"(reference {error.get('reference')})") from None
        return json.loads(content) if content else {}

    def call(self, method, path, data=None, query=""):
        if time.time() > self.access_until:            # about every 9 minutes
            self._keep(self._send("POST", "/api/v1/auth/refresh",
                                  {"refresh_token": self.refresh_token}, token=False))
        return self._send(method, path, data, query)


if __name__ == "__main__":
    api = Client(input("Email: "), getpass.getpass())
    me = api.call("GET", "/api/v1/auth/me")
    print("Logged in as", me["name"], "-", [c["name"] for c in me["companies"]])
    today = api.call("GET", "/api/v1/attendance", query="on=" + time.strftime("%Y-%m-%d"))
    for day in today["results"]:
        print(day["employee"]["name"], day["status"], day["check_in"], day["check_out"])
    api.call("POST", "/api/v1/auth/logout")
```

Run it, give an owner's or HR's email and password, and it prints today's
attendance.

## What each part does

1. **Log in** (`/auth/login`) - not signed yet: there is no secret before
   logging in. An owner or company administrator is asked for a two-step code.
2. **Keep** the session id, the tokens and the **signing secret** - the secret
   is shown only at login. A real app keeps them in the system's secure
   storage (Keychain, Keystore, Credential Manager).
3. **Sign** every request: six lines - method, path, sorted query, time,
   nonce, SHA-256 of the body - HMAC-SHA256 with the secret. Hash exactly the
   bytes you send.
4. **Refresh** before the access token's 10 minutes are up; save the new
   refresh token each time - an old one used again ends the session.
5. **Errors** always come as `{"error": {"code", "message", "reference"}}`:
   act on the `code`, show the `message`, quote the `reference` when asking
   for help.

## Next steps

- In several companies? Send `X-Company: <id>` (from `GET /auth/me`).
- A machine (the ERP) uses an **API key** instead of a login: the same
  signing, with the key's id and secret, and no `Authorization` header.
- A browser frontend uses cookies, not signatures: see *Logging in & signing*.
- Stuck on a signature? `POST /api/v1/auth/signature-test` shows the request
  the server built - compare it with yours line by line.
- Try any endpoint without code: the Postman collection
  (`/api/docs/postman.json`) signs requests for you.
