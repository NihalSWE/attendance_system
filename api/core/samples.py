"""Ready-to-copy request examples in every language the plan lists.

Each signed example performs the whole signing (docs/api/02-authentication.md):
timestamp, nonce, sorted query, body hash, canonical request, HMAC-SHA256 -
not just the HTTP call. The same code serves an app session (with its access
token) and an API key (leave the access token empty).

Modes (an endpoint's ``sample_auth``):
- "none": no login (ping, login itself);
- "app": an app session's access token + signature, or an API key's signature;
- "app-refresh": the signature only (refreshing tokens);
- "web": a browser frontend's cookies + CSRF token.

The Python example is run by the tests against the real server, so the
signing shown is proven to work.
"""

import json

#: (key, label) in the order the tabs appear.
LANGUAGES = (
    ("curl", "cURL"),
    ("javascript", "JavaScript"),
    ("python", "Python"),
    ("php", "PHP"),
    ("kotlin", "Kotlin"),
    ("swift", "Swift"),
    ("csharp", "C#"),
    ("dart", "Dart"),
)


def mode_of(doc):
    return doc.sample_auth or ("none" if doc.auth == "public" else "app")


def example_path(doc):
    path = doc.path
    for param in doc.params:
        if param.location == "path" and param.example is not None:
            path = path.replace("{" + param.name + "}", str(param.example))
    return path


def example_query(doc):
    return "&".join(f"{p.name}={p.example}" for p in doc.params
                    if p.location == "query" and p.required and p.example is not None)


def samples(doc, base_url):
    """``[(key, label, code)]`` for this endpoint."""
    base = base_url.rstrip("/")
    method, path, query = doc.method, example_path(doc), example_query(doc)
    has_body = doc.request_example is not None and method in ("POST", "PUT", "PATCH")
    body = json.dumps(doc.request_example, separators=(",", ":"), ensure_ascii=False) if has_body else ""
    mode = mode_of(doc)
    if mode == "web":
        code = _web(base, method, path, query, body)
    elif mode in ("app", "app-refresh"):
        token = "" if mode == "app-refresh" else "at_..."
        code = {key: build(base, method, path, query, body, token)
                for key, build in SIGNED.items()}
    else:
        code = {key: build(base, method, path, query, body) for key, build in PLAIN.items()}
    return [(key, label, code[key]) for key, label in LANGUAGES]


# --- literals in each language ------------------------------------------------------

def _sq(text):      # single-quoted, for JS / Python / PHP / Dart
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _dq(text):      # double-quoted, for C-like languages
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _bash(text):
    return "'" + text.replace("'", "'\\''") + "'"


def _kotlin(text):
    return '"""' + text.replace("$", "${'$'}") + '"""'


def _swift(text):
    return '#"' + text + '"#'


def _csharp(text):
    return '@"' + text.replace('"', '""') + '"'


def _url(base, path, query):
    return base + path + ("?" + query if query else "")


# --- signed (app session or API key) ------------------------------------------------

_WHO = {
    "key": "session_id from login - or an API key id (ak_...)",
    "secret": "signing_secret from login - or the API key's secret",
    "token": "access_token from login - empty for an API key",
}


def _signed_curl(base, method, path, query, body, token):
    return f'''#!/usr/bin/env bash
BASE_URL="{base}"
KEY_ID="ses_..."        # {_WHO["key"]}
SECRET="ss_..."         # {_WHO["secret"]}
ACCESS_TOKEN="{token}"   # {_WHO["token"]}
METHOD="{method}"
PATHNAME="{path}"
QUERY="{query}"
BODY={_bash(body)}

TS=$(date +%s)
NONCE=$(openssl rand -hex 16)
SORTED_QUERY=$(printf '%s' "$QUERY" | tr '&' '\\n' | grep -v '^$' | LC_ALL=C sort | paste -sd'&' -)
BODY_HASH=$(printf '%s' "$BODY" | openssl dgst -sha256 | awk '{{print $NF}}')
CANONICAL=$(printf '%s\\n%s\\n%s\\n%s\\n%s\\n%s' "$METHOD" "$PATHNAME" "$SORTED_QUERY" "$TS" "$NONCE" "$BODY_HASH")
SIGNATURE=$(printf '%s' "$CANONICAL" | openssl dgst -sha256 -hmac "$SECRET" | awk '{{print $NF}}')

curl -X "$METHOD" "$BASE_URL$PATHNAME${{QUERY:+?$QUERY}}" \\
  -H "Content-Type: application/json" -H "Accept: application/json" \\
  ${{ACCESS_TOKEN:+-H "Authorization: Bearer $ACCESS_TOKEN"}} \\
  -H "X-Key-Id: $KEY_ID" -H "X-Timestamp: $TS" \\
  -H "X-Nonce: $NONCE" -H "X-Signature: $SIGNATURE" \\
  ${{BODY:+--data-raw "$BODY"}}'''


def _signed_javascript(base, method, path, query, body, token):
    return f'''// Node.js 18+ (an app or server). A browser frontend uses cookies instead.
import crypto from "node:crypto";

const BASE_URL = "{base}";
const KEY_ID = "ses_...";      // {_WHO["key"]}
const SECRET = "ss_...";       // {_WHO["secret"]}
const ACCESS_TOKEN = "{token}";  // {_WHO["token"]}

async function signedRequest(method, path, query = "", body = "") {{
  const ts = Math.floor(Date.now() / 1000).toString();
  const nonce = crypto.randomBytes(16).toString("hex");
  const sortedQuery = query.split("&").filter(Boolean).sort().join("&");
  const bodyHash = crypto.createHash("sha256").update(body, "utf8").digest("hex");
  const canonical = [method, path, sortedQuery, ts, nonce, bodyHash].join("\\n");
  const signature = crypto.createHmac("sha256", SECRET).update(canonical, "utf8").digest("hex");
  const headers = {{
    "Content-Type": "application/json", "Accept": "application/json",
    "X-Key-Id": KEY_ID, "X-Timestamp": ts, "X-Nonce": nonce, "X-Signature": signature,
  }};
  if (ACCESS_TOKEN) headers["Authorization"] = `Bearer ${{ACCESS_TOKEN}}`;
  const url = BASE_URL + path + (query ? `?${{query}}` : "");
  const response = await fetch(url, {{ method, headers, body: body || undefined }});
  const data = await response.json();
  if (!response.ok) throw new Error(`${{data.error.code}}: ${{data.error.message}}`);
  return data;
}}

// Sign exactly the string you send.
const body = {_sq(body)};
console.log(await signedRequest("{method}", "{path}", "{query}", body));'''


def _signed_python(base, method, path, query, body, token):
    return f'''import hashlib
import hmac
import secrets
import time

import requests

BASE_URL = "{base}"
KEY_ID = "ses_..."       # {_WHO["key"]}
SECRET = "ss_..."        # {_WHO["secret"]}
ACCESS_TOKEN = "{token}"  # {_WHO["token"]}


def signed_request(method, path, query="", body=""):
    ts = str(int(time.time()))
    nonce = secrets.token_hex(16)
    sorted_query = "&".join(sorted(part for part in query.split("&") if part))
    body_hash = hashlib.sha256(body.encode()).hexdigest()
    canonical = "\\n".join([method, path, sorted_query, ts, nonce, body_hash])
    signature = hmac.new(SECRET.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    headers = {{"Content-Type": "application/json", "Accept": "application/json",
               "X-Key-Id": KEY_ID, "X-Timestamp": ts, "X-Nonce": nonce,
               "X-Signature": signature}}
    if ACCESS_TOKEN:
        headers["Authorization"] = "Bearer " + ACCESS_TOKEN
    url = BASE_URL + path + ("?" + query if query else "")
    response = requests.request(method, url, headers=headers, data=body.encode() or None,
                                timeout=30)
    data = response.json()
    if not response.ok:
        raise RuntimeError(f"{{data['error']['code']}}: {{data['error']['message']}}")
    return data


# Sign exactly the string you send.
body = {_sq(body)}
print(signed_request("{method}", "{path}", "{query}", body))'''


def _signed_php(base, method, path, query, body, token):
    return f'''<?php
const BASE_URL = "{base}";
const KEY_ID = "ses_...";       // {_WHO["key"]}
const SECRET = "ss_...";        // {_WHO["secret"]}
const ACCESS_TOKEN = "{token}";   // {_WHO["token"]}

function signedRequest(string $method, string $path, string $query = "", string $body = ""): array {{
    $ts = (string) time();
    $nonce = bin2hex(random_bytes(16));
    $parts = array_values(array_filter(explode("&", $query), fn($p) => $p !== ""));
    sort($parts, SORT_STRING);
    $canonical = implode("\\n", [$method, $path, implode("&", $parts), $ts, $nonce, hash("sha256", $body)]);
    $signature = hash_hmac("sha256", $canonical, SECRET);
    $headers = ["Content-Type: application/json", "Accept: application/json",
                "X-Key-Id: " . KEY_ID, "X-Timestamp: $ts", "X-Nonce: $nonce", "X-Signature: $signature"];
    if (ACCESS_TOKEN !== "") {{ $headers[] = "Authorization: Bearer " . ACCESS_TOKEN; }}
    $ch = curl_init(BASE_URL . $path . ($query !== "" ? "?$query" : ""));
    curl_setopt_array($ch, [CURLOPT_CUSTOMREQUEST => $method, CURLOPT_RETURNTRANSFER => true,
                            CURLOPT_HTTPHEADER => $headers]);
    if ($body !== "") {{ curl_setopt($ch, CURLOPT_POSTFIELDS, $body); }}
    $data = json_decode(curl_exec($ch), true);
    $status = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    if ($status >= 400) {{ throw new Exception($data["error"]["code"] . ": " . $data["error"]["message"]); }}
    return $data;
}}

// Sign exactly the string you send.
$body = {_sq(body)};
print_r(signedRequest("{method}", "{path}", "{query}", $body));'''


def _signed_kotlin(base, method, path, query, body, token):
    return f'''// Android / JVM, with OkHttp
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.security.MessageDigest
import java.security.SecureRandom
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

const val BASE_URL = "{base}"
const val KEY_ID = "ses_..."       // {_WHO["key"]}
const val SECRET = "ss_..."        // {_WHO["secret"]}
const val ACCESS_TOKEN = "{token}"  // {_WHO["token"]}

fun hex(bytes: ByteArray) = bytes.joinToString("") {{ "%02x".format(it) }}

fun signedRequest(method: String, path: String, query: String = "", body: String = ""): String {{
    val ts = (System.currentTimeMillis() / 1000).toString()
    val nonce = hex(ByteArray(16).also {{ SecureRandom().nextBytes(it) }})
    val sortedQuery = query.split("&").filter {{ it.isNotEmpty() }}.sorted().joinToString("&")
    val bodyHash = hex(MessageDigest.getInstance("SHA-256").digest(body.toByteArray(Charsets.UTF_8)))
    val canonical = listOf(method, path, sortedQuery, ts, nonce, bodyHash).joinToString("\\n")
    val mac = Mac.getInstance("HmacSHA256").apply {{
        init(SecretKeySpec(SECRET.toByteArray(Charsets.UTF_8), "HmacSHA256"))
    }}
    val signature = hex(mac.doFinal(canonical.toByteArray(Charsets.UTF_8)))
    val requestBody = if (method in listOf("POST", "PUT", "PATCH"))
        body.toRequestBody("application/json".toMediaType()) else null
    val builder = Request.Builder()
        .url(BASE_URL + path + (if (query.isNotEmpty()) "?$query" else ""))
        .method(method, requestBody)
        .header("Accept", "application/json")
        .header("X-Key-Id", KEY_ID).header("X-Timestamp", ts)
        .header("X-Nonce", nonce).header("X-Signature", signature)
    if (ACCESS_TOKEN.isNotEmpty()) builder.header("Authorization", "Bearer $ACCESS_TOKEN")
    OkHttpClient().newCall(builder.build()).execute().use {{ response ->
        val json = response.body!!.string()
        if (!response.isSuccessful) error(json)
        return json
    }}
}}

// Sign exactly the string you send.
val body = {_kotlin(body)}
println(signedRequest("{method}", "{path}", "{query}", body))'''


def _signed_swift(base, method, path, query, body, token):
    return f'''// iOS / macOS, with CryptoKit
import CryptoKit
import Foundation

let baseURL = "{base}"
let keyId = "ses_..."        // {_WHO["key"]}
let secret = "ss_..."        // {_WHO["secret"]}
let accessToken = "{token}"   // {_WHO["token"]}

func hex<S: Sequence>(_ bytes: S) -> String where S.Element == UInt8 {{
    bytes.map {{ String(format: "%02x", $0) }}.joined()
}}

func signedRequest(_ method: String, _ path: String, query: String = "",
                   body: String = "") async throws -> Data {{
    let ts = String(Int(Date().timeIntervalSince1970))
    var random = [UInt8](repeating: 0, count: 16)
    _ = SecRandomCopyBytes(kSecRandomDefault, random.count, &random)
    let nonce = hex(random)
    let sortedQuery = query.split(separator: "&").map(String.init).sorted().joined(separator: "&")
    let bodyHash = hex(SHA256.hash(data: Data(body.utf8)))
    let canonical = [method, path, sortedQuery, ts, nonce, bodyHash].joined(separator: "\\n")
    let signature = hex(HMAC<SHA256>.authenticationCode(
        for: Data(canonical.utf8), using: SymmetricKey(data: Data(secret.utf8))))
    var request = URLRequest(url: URL(string: baseURL + path + (query.isEmpty ? "" : "?" + query))!)
    request.httpMethod = method
    let headers = ["Content-Type": "application/json", "Accept": "application/json",
                   "X-Key-Id": keyId, "X-Timestamp": ts, "X-Nonce": nonce, "X-Signature": signature]
    for (name, value) in headers {{ request.setValue(value, forHTTPHeaderField: name) }}
    if !accessToken.isEmpty {{ request.setValue("Bearer \\(accessToken)", forHTTPHeaderField: "Authorization") }}
    if !body.isEmpty {{ request.httpBody = Data(body.utf8) }}
    let (data, response) = try await URLSession.shared.data(for: request)
    guard let http = response as? HTTPURLResponse, http.statusCode < 400 else {{
        throw URLError(.badServerResponse)
    }}
    return data
}}

// Sign exactly the string you send.
let body = {_swift(body)}
let data = try await signedRequest("{method}", "{path}", query: "{query}", body: body)
print(String(data: data, encoding: .utf8)!)'''


def _signed_csharp(base, method, path, query, body, token):
    return f'''// .NET 6+ (desktop app or server)
using System.Security.Cryptography;
using System.Text;

const string BaseUrl = "{base}";
const string KeyId = "ses_...";       // {_WHO["key"]}
const string Secret = "ss_...";       // {_WHO["secret"]}
const string AccessToken = "{token}";  // {_WHO["token"]}

static string Hex(byte[] bytes) => Convert.ToHexString(bytes).ToLowerInvariant();

async Task<string> SignedRequest(string method, string path, string query = "", string body = "")
{{
    var ts = DateTimeOffset.UtcNow.ToUnixTimeSeconds().ToString();
    var nonce = Hex(RandomNumberGenerator.GetBytes(16));
    var sortedQuery = string.Join("&", query.Split('&', StringSplitOptions.RemoveEmptyEntries)
        .OrderBy(p => p, StringComparer.Ordinal));
    var bodyHash = Hex(SHA256.HashData(Encoding.UTF8.GetBytes(body)));
    var canonical = string.Join("\\n", method, path, sortedQuery, ts, nonce, bodyHash);
    var signature = Hex(HMACSHA256.HashData(Encoding.UTF8.GetBytes(Secret),
                                            Encoding.UTF8.GetBytes(canonical)));
    using var client = new HttpClient();
    var request = new HttpRequestMessage(new HttpMethod(method),
        BaseUrl + path + (query == "" ? "" : "?" + query));
    request.Headers.Add("Accept", "application/json");
    request.Headers.Add("X-Key-Id", KeyId);
    request.Headers.Add("X-Timestamp", ts);
    request.Headers.Add("X-Nonce", nonce);
    request.Headers.Add("X-Signature", signature);
    if (AccessToken != "") request.Headers.Add("Authorization", "Bearer " + AccessToken);
    if (body != "") request.Content = new StringContent(body, Encoding.UTF8, "application/json");
    var response = await client.SendAsync(request);
    var json = await response.Content.ReadAsStringAsync();
    if (!response.IsSuccessStatusCode) throw new Exception(json);
    return json;
}}

// Sign exactly the string you send.
var body = {_csharp(body)};
Console.WriteLine(await SignedRequest("{method}", "{path}", "{query}", body));'''


def _signed_dart(base, method, path, query, body, token):
    return f'''// Flutter / Dart, with package:http and package:crypto
import 'dart:convert';
import 'dart:math';

import 'package:crypto/crypto.dart';
import 'package:http/http.dart' as http;

const baseUrl = '{base}';
const keyId = 'ses_...';       // {_WHO["key"]}
const secret = 'ss_...';       // {_WHO["secret"]}
const accessToken = '{token}';  // {_WHO["token"]}

Future<String> signedRequest(String method, String path,
    {{String query = '', String body = ''}}) async {{
  final ts = (DateTime.now().millisecondsSinceEpoch ~/ 1000).toString();
  final random = Random.secure();
  final nonce = List.generate(16, (_) => random.nextInt(256).toRadixString(16).padLeft(2, '0')).join();
  final parts = query.split('&').where((p) => p.isNotEmpty).toList()..sort();
  final bodyHash = sha256.convert(utf8.encode(body)).toString();
  final canonical = [method, path, parts.join('&'), ts, nonce, bodyHash].join('\\n');
  final signature = Hmac(sha256, utf8.encode(secret)).convert(utf8.encode(canonical)).toString();
  final request = http.Request(method, Uri.parse(baseUrl + path + (query.isEmpty ? '' : '?$query')));
  request.headers.addAll({{
    'Content-Type': 'application/json', 'Accept': 'application/json',
    'X-Key-Id': keyId, 'X-Timestamp': ts, 'X-Nonce': nonce, 'X-Signature': signature,
  }});
  if (accessToken.isNotEmpty) request.headers['Authorization'] = 'Bearer $accessToken';
  if (body.isNotEmpty) request.body = body;
  final response = await http.Response.fromStream(await request.send());
  if (response.statusCode >= 400) throw Exception(response.body);
  return response.body;
}}

// Sign exactly the string you send.
final body = {_sq(body).replace("$", "\\$")};
print(await signedRequest('{method}', '{path}', query: '{query}', body: body));'''


SIGNED = {
    "curl": _signed_curl, "javascript": _signed_javascript, "python": _signed_python,
    "php": _signed_php, "kotlin": _signed_kotlin, "swift": _signed_swift,
    "csharp": _signed_csharp, "dart": _signed_dart,
}


# --- no login --------------------------------------------------------------------

def _plain_curl(base, method, path, query, body):
    lines = [f'curl -X {method} "{_url(base, path, query)}" \\', '  -H "Accept: application/json"']
    if body:
        lines[-1] += " \\"
        lines += ['  -H "Content-Type: application/json" \\', f"  --data-raw {_bash(body)}"]
    return "\n".join(lines)


def _plain_javascript(base, method, path, query, body):
    lines = [f'const response = await fetch("{_url(base, path, query)}", {{',
             f'  method: "{method}",',
             '  headers: {"Accept": "application/json"'
             + (', "Content-Type": "application/json"' if body else "") + "},"]
    if body:
        lines.append(f"  body: {_sq(body)},")
    lines += ["});", "const data = await response.json();",
              'if (!response.ok) throw new Error(`${data.error.code}: ${data.error.message}`);',
              "console.log(data);"]
    return "\n".join(lines)


def _plain_python(base, method, path, query, body):
    data = f",\n                            data={_sq(body)}.encode()" if body else ""
    content = ', "Content-Type": "application/json"' if body else ""
    return ("import requests\n\n"
            f'response = requests.request("{method}", "{_url(base, path, query)}",\n'
            f'                            headers={{"Accept": "application/json"{content}}}'
            f"{data}, timeout=30)\n"
            "data = response.json()\n"
            "if not response.ok:\n"
            "    raise RuntimeError(f\"{data['error']['code']}: {data['error']['message']}\")\n"
            "print(data)")


def _plain_php(base, method, path, query, body):
    lines = ["<?php", f'$ch = curl_init("{_url(base, path, query)}");',
             "curl_setopt_array($ch, [", f'    CURLOPT_CUSTOMREQUEST => "{method}",',
             "    CURLOPT_RETURNTRANSFER => true,",
             '    CURLOPT_HTTPHEADER => ["Accept: application/json"'
             + (', "Content-Type: application/json"' if body else "") + "],"]
    if body:
        lines.append(f"    CURLOPT_POSTFIELDS => {_sq(body)},")
    lines += ["]);", "$data = json_decode(curl_exec($ch), true);",
              "$status = curl_getinfo($ch, CURLINFO_HTTP_CODE);", "curl_close($ch);",
              'if ($status >= 400) { throw new Exception($data["error"]["code"]); }',
              "print_r($data);"]
    return "\n".join(lines)


def _plain_kotlin(base, method, path, query, body):
    request_body = (f'{_kotlin(body)}.toRequestBody("application/json".toMediaType())'
                    if method in ("POST", "PUT", "PATCH") else "null")
    return ("// OkHttp\nval request = Request.Builder()\n"
            f'    .url("{_url(base, path, query)}")\n'
            f'    .method("{method}", {request_body})\n'
            '    .header("Accept", "application/json")\n    .build()\n'
            "OkHttpClient().newCall(request).execute().use { response ->\n"
            "    val json = response.body!!.string()\n"
            "    if (!response.isSuccessful) error(json)\n    println(json)\n}")


def _plain_swift(base, method, path, query, body):
    lines = [f'var request = URLRequest(url: URL(string: "{_url(base, path, query)}")!)',
             f'request.httpMethod = "{method}"',
             'request.setValue("application/json", forHTTPHeaderField: "Accept")']
    if body:
        lines += ['request.setValue("application/json", forHTTPHeaderField: "Content-Type")',
                  f"request.httpBody = Data({_swift(body)}.utf8)"]
    lines += ["let (data, response) = try await URLSession.shared.data(for: request)",
              "guard let http = response as? HTTPURLResponse, http.statusCode < 400 else {",
              "    throw URLError(.badServerResponse)", "}",
              "print(String(data: data, encoding: .utf8)!)"]
    return "\n".join(lines)


def _plain_csharp(base, method, path, query, body):
    lines = ["using var client = new HttpClient();",
             f'var request = new HttpRequestMessage(new HttpMethod("{method}"), '
             f'"{_url(base, path, query)}");',
             'request.Headers.Add("Accept", "application/json");']
    if body:
        lines.append(f"request.Content = new StringContent({_csharp(body)}, Encoding.UTF8, "
                     '"application/json");')
    lines += ["var response = await client.SendAsync(request);",
              "var json = await response.Content.ReadAsStringAsync();",
              "if (!response.IsSuccessStatusCode) throw new Exception(json);",
              "Console.WriteLine(json);"]
    return "\n".join(lines)


def _plain_dart(base, method, path, query, body):
    lines = ["import 'package:http/http.dart' as http;", "",
             f"final request = http.Request('{method}', Uri.parse('{_url(base, path, query)}'));",
             "request.headers['Accept'] = 'application/json';"]
    if body:
        lines += ["request.headers['Content-Type'] = 'application/json';",
                  f"request.body = {_sq(body).replace('$', chr(92) + '$')};"]
    lines += ["final response = await http.Response.fromStream(await request.send());",
              "if (response.statusCode >= 400) throw Exception(response.body);",
              "print(response.body);"]
    return "\n".join(lines)


PLAIN = {
    "curl": _plain_curl, "javascript": _plain_javascript, "python": _plain_python,
    "php": _plain_php, "kotlin": _plain_kotlin, "swift": _plain_swift,
    "csharp": _plain_csharp, "dart": _plain_dart,
}


# --- browser frontend (cookies + CSRF) -------------------------------------------------

def _web(base, method, path, query, body):
    url = _url(base, path, query)
    javascript = f'''// In the browser frontend. Your frontend's address must be in the server's
// API_CORS_ORIGINS. The session lives in HttpOnly cookies the page cannot read.
const BASE_URL = "{base}";

async function csrfToken() {{
  const r = await fetch(BASE_URL + "/api/v1/auth/web/csrf", {{ credentials: "include" }});
  return (await r.json()).csrf_token;
}}

const response = await fetch("{url}", {{
  method: "{method}",
  credentials: "include",
  headers: {{
    "Accept": "application/json", "Content-Type": "application/json",
    "X-CSRFToken": await csrfToken(),
  }},{f"{chr(10)}  body: {_sq(body)}," if body else ""}
}});
const data = await response.json();
if (!response.ok) throw new Error(`${{data.error.code}}: ${{data.error.message}}`);
console.log(data);'''
    curl = f'''# The cookie flow, to try it from a terminal (a browser does this by itself).
BASE_URL="{base}"
CSRF=$(curl -s -c cookies.txt "$BASE_URL/api/v1/auth/web/csrf" | python -c "import sys,json;print(json.load(sys.stdin)['csrf_token'])")
curl -X {method} "{url}" -b cookies.txt -c cookies.txt \\
  -H "Accept: application/json" -H "Content-Type: application/json" \\
  -H "X-CSRFToken: $CSRF" -H "Origin: $BASE_URL" -H "Referer: $BASE_URL/"{f" {chr(92)}{chr(10)}  --data-raw {_bash(body)}" if body else ""}'''
    python = f'''# The cookie flow from Python (a browser does this by itself).
import requests

BASE_URL = "{base}"
web = requests.Session()
csrf = web.get(BASE_URL + "/api/v1/auth/web/csrf", timeout=30).json()["csrf_token"]
response = web.request("{method}", "{url}", timeout=30,
                       headers={{"Accept": "application/json", "Content-Type": "application/json",
                                "X-CSRFToken": csrf, "Origin": BASE_URL, "Referer": BASE_URL + "/"}}{f",{chr(10)}                       data={_sq(body)}.encode()" if body else ""})
print(response.json())'''
    note = ("// Cookie login is for browser frontends - see the JavaScript tab.\n"
            "// An app logs in with POST /api/v1/auth/login and signs its requests.")
    return {"curl": curl, "javascript": javascript, "python": python,
            "php": note, "kotlin": note, "swift": note, "csharp": note, "dart": note}
