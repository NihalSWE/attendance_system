"""The Postman collection, built from the same endpoint declarations as the
documentation site - so it always has every endpoint, with its example body.

How it signs: each signed request carries the four signature headers with
placeholders ({{x_timestamp}}, {{x_nonce}}, {{x_signature}}). The
collection's pre-request script fills them in just before the request goes
out, exactly as docs/api/02-authentication.md describes. Log-in requests save
the tokens into the collection's variables by themselves.

The guide: docs/api/07-postman.md.
"""

import json

from api.core.docs import AREAS, PATH, QUERY
from api.core.registry import endpoints
from api.core.samples import mode_of

SCHEMA = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"

#: Collection variables: (name, starting value, what it is).
VARIABLES = (
    ("email", "", "Your login's email - used by the log-in requests."),
    ("password", "", "Your login's password - used by the log-in requests."),
    ("key_id", "", "Who signs: the session_id (saved by Log in), or an API key id (ak_…)."),
    ("secret", "", "The signing secret (saved by Log in), or the API key's secret."),
    ("access_token", "", "Saved by Log in / Refresh. Leave empty when signing with an API key."),
    ("refresh_token", "", "Saved by Log in / Refresh."),
    ("challenge", "", "Saved by Log in when the two-step code is needed."),
    ("csrf_token", "", "Saved by Get the CSRF token (web requests)."),
    ("company_id", "", "Only for a login with several companies: tick the X-Company header."),
    ("api_key_id", "", "Saved by Create an API key / Rotate."),
    ("api_key_secret", "", "Saved by Create an API key / Rotate (shown once)."),
    ("x_timestamp", "", "Filled in by the signing script - do not edit."),
    ("x_nonce", "", "Filled in by the signing script - do not edit."),
    ("x_signature", "", "Filled in by the signing script - do not edit."),
)

SIGNING_SCRIPT = r"""
// Signs the request (see "Logging in & signing" in the API documentation).
// Runs before every request; does nothing for requests without X-Signature.
(function () {
const CryptoJS = require("crypto-js");
if (!pm.request.headers.has("X-Signature")) { return; }

const keyId = pm.variables.get("key_id") || "";
const secret = pm.variables.get("secret") || "";
if (!keyId || !secret) {
  console.warn("Not signed: run Log in first, or set key_id and secret to an API key's.");
  return;
}
// An API key (ak_...) or an empty token: no Authorization header.
if (keyId.startsWith("ak_") || !pm.variables.get("access_token")) {
  pm.request.headers.remove("Authorization");
}

const method = pm.request.method.toUpperCase();
const path = pm.variables.replaceIn(pm.request.url.getPath());
const query = pm.variables.replaceIn(pm.request.url.getQueryString() || "");
const sortedQuery = query.split("&").filter(p => p).sort().join("&");
const hasBody = pm.request.body && pm.request.body.mode === "raw" && pm.request.body.raw;
const body = hasBody ? pm.variables.replaceIn(pm.request.body.raw) : "";
const ts = Math.floor(Date.now() / 1000).toString();
const nonce = CryptoJS.lib.WordArray.random(16).toString(CryptoJS.enc.Hex);
const bodyHash = CryptoJS.SHA256(body).toString(CryptoJS.enc.Hex);
const canonical = [method, path, sortedQuery, ts, nonce, bodyHash].join("\n");
const signature = CryptoJS.HmacSHA256(canonical, secret).toString(CryptoJS.enc.Hex);

pm.collectionVariables.set("x_timestamp", ts);
pm.collectionVariables.set("x_nonce", nonce);
pm.collectionVariables.set("x_signature", signature);
console.log("Signed. Canonical request:\n" + canonical);
})();
""".strip()

SAVE_TOKENS = r"""
// Saves the tokens for the next requests.
const data = pm.response.json();
if (data.challenge) {
  pm.collectionVariables.set("challenge", data.challenge);
  console.log("Two-step code needed: run 'Log in: the two-step code' with the code.");
}
if (data.access_token) { pm.collectionVariables.set("access_token", data.access_token); }
if (data.refresh_token) { pm.collectionVariables.set("refresh_token", data.refresh_token); }
if (data.session_id && data.signing_secret) {
  pm.collectionVariables.set("key_id", data.session_id);
  pm.collectionVariables.set("secret", data.signing_secret);
}
""".strip()

SAVE_CSRF = r"""
const data = pm.response.json();
if (data.csrf_token) { pm.collectionVariables.set("csrf_token", data.csrf_token); }
""".strip()

SAVE_KEY = r"""
// The secret is shown only now: saved as api_key_id / api_key_secret.
const data = pm.response.json();
if (data.secret) {
  pm.collectionVariables.set("api_key_id", data.id);
  pm.collectionVariables.set("api_key_secret", data.secret);
}
""".strip()

AFTER = {
    "auth-login": SAVE_TOKENS, "auth-login-two-step": SAVE_TOKENS, "auth-refresh": SAVE_TOKENS,
    "auth-web-csrf": SAVE_CSRF, "api-keys-create": SAVE_KEY, "api-keys-rotate": SAVE_KEY,
}
LOGIN_IDS = ("auth-login", "auth-web-login")
#: Body fields filled from the collection's variables.
FROM_VARIABLES = ("refresh_token", "challenge")
#: Path parameters filled from the collection's variables.
PATH_VARIABLES = {"key_id": "{{api_key_id}}"}


def _body(doc):
    example = doc.request_example
    if not isinstance(example, dict) or doc.method not in ("POST", "PUT", "PATCH"):
        return None
    body = dict(example)
    for name in FROM_VARIABLES:
        if name in body:
            body[name] = "{{%s}}" % name
    if doc.id in LOGIN_IDS:
        body["email"], body["password"] = "{{email}}", "{{password}}"
    return json.dumps(body, indent=2, ensure_ascii=False)


def _headers(mode):
    headers = [{"key": "Accept", "value": "application/json"},
               {"key": "Content-Type", "value": "application/json"}]
    if mode in ("app", "app-refresh"):
        if mode == "app":
            headers.append({"key": "Authorization", "value": "Bearer {{access_token}}"})
        headers += [{"key": "X-Key-Id", "value": "{{key_id}}"},
                    {"key": "X-Timestamp", "value": "{{x_timestamp}}"},
                    {"key": "X-Nonce", "value": "{{x_nonce}}"},
                    {"key": "X-Signature", "value": "{{x_signature}}"}]
    if mode == "web":
        headers += [{"key": "X-CSRFToken", "value": "{{csrf_token}}"},
                    {"key": "Referer", "value": "{{base_url}}/"}]
    if mode != "none":
        headers.append({"key": "X-Company", "value": "{{company_id}}", "disabled": True,
                        "description": "Tick it when the login has several companies."})
    return headers


def _url(doc):
    path = doc.path
    variables = []
    for param in doc.params:
        if param.location == PATH:
            path = path.replace("{%s}" % param.name, ":" + param.name)
            value = PATH_VARIABLES.get(param.name, "" if param.example is None else str(param.example))
            variables.append({"key": param.name, "value": value, "description": param.description})
    query = [{"key": p.name, "value": "" if p.example is None else str(p.example),
              "description": p.description, "disabled": not p.required}
             for p in doc.params if p.location == QUERY]
    url = {"raw": "{{base_url}}" + path, "host": ["{{base_url}}"],
           "path": [part for part in path.split("/") if part]}
    if query:
        url["query"] = query
    if variables:
        url["variable"] = variables
    return url


def _item(doc, docs_url):
    request = {"method": doc.method, "header": _headers(mode_of(doc)), "url": _url(doc),
               "description": f"{doc.summary}\n\nFull documentation: {docs_url}"}
    body = _body(doc)
    if body is not None:
        request["body"] = {"mode": "raw", "raw": body, "options": {"raw": {"language": "json"}}}
    item = {"name": doc.title, "request": request}
    if doc.id in AFTER:
        item["event"] = [{"listen": "test",
                          "script": {"type": "text/javascript", "exec": AFTER[doc.id].splitlines()}}]
    return item


def collection(base_url, docs_url_of):
    """The whole collection. ``docs_url_of(doc)`` gives an endpoint's page."""
    docs = endpoints()
    folders = []
    for area, title in AREAS:
        items = [_item(doc, docs_url_of(doc)) for doc in docs if doc.area == area]
        if items:
            folders.append({"name": title, "item": items})
    return {
        "info": {
            "name": "Attendance Management API v1",
            "schema": SCHEMA,
            "description": ("Every endpoint of the API. Set email and password in the "
                            "collection's Variables, run Logging in > Log in (apps), then any "
                            "request: it is signed for you. Guide: "
                            f"{base_url}/api/docs/postman/"),
        },
        "event": [{"listen": "prerequest",
                   "script": {"type": "text/javascript", "exec": SIGNING_SCRIPT.splitlines()}}],
        "variable": [{"key": "base_url", "value": base_url,
                      "description": "The server, without a slash at the end."}]
                    + [{"key": name, "value": value, "description": words}
                       for name, value, words in VARIABLES],
        "item": folders,
    }
