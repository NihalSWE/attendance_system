"""Ready-to-copy request examples, in every language the plan lists.

Phase 0 has public endpoints only; phase 1 adds the signed versions (each
sample then performs the full login / signing, not just the HTTP call).
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


def example_path(doc):
    path = doc.path
    for param in doc.params:
        if param.location == "path" and param.example is not None:
            path = path.replace("{" + param.name + "}", str(param.example))
    query = [f"{p.name}={p.example}" for p in doc.params
             if p.location == "query" and p.required and p.example is not None]
    return path + ("?" + "&".join(query) if query else "")


def samples(doc, base_url):
    """``[(key, label, code)]`` for this endpoint."""
    url = base_url.rstrip("/") + example_path(doc)
    method = doc.method
    body = json.dumps(doc.request_example, indent=2) if doc.request_example is not None else ""
    one_line = json.dumps(doc.request_example) if doc.request_example is not None else ""
    has_body = bool(body) and method in ("POST", "PUT", "PATCH")
    code = {
        "curl": _curl(method, url, body if has_body else ""),
        "javascript": _javascript(method, url, body if has_body else ""),
        "python": _python(method, url, body if has_body else ""),
        "php": _php(method, url, one_line if has_body else ""),
        "kotlin": _kotlin(method, url, one_line if has_body else ""),
        "swift": _swift(method, url, one_line if has_body else ""),
        "csharp": _csharp(method, url, one_line if has_body else ""),
        "dart": _dart(method, url, one_line if has_body else ""),
    }
    return [(key, label, code[key]) for key, label in LANGUAGES]


def _curl(method, url, body):
    lines = [f'curl -X {method} "{url}" \\', '  -H "Accept: application/json"']
    if body:
        lines[-1] += " \\"
        lines.append('  -H "Content-Type: application/json" \\')
        lines.append("  -d '" + body.replace("'", "'\\''") + "'")
    return "\n".join(lines)


def _javascript(method, url, body):
    options = [f'  method: "{method}",', '  headers: {"Accept": "application/json"'
               + (', "Content-Type": "application/json"' if body else "") + "},"]
    if body:
        options.append("  body: JSON.stringify(" + body.replace("\n", "\n  ") + "),")
    return (f'const response = await fetch("{url}", {{\n' + "\n".join(options) + "\n});\n"
            "const data = await response.json();\n"
            "if (!response.ok) throw new Error(data.error.code + \": \" + data.error.message);\n"
            "console.log(data);")


def _python(method, url, body):
    call = f'response = requests.request("{method}", "{url}",\n' \
           '                            headers={"Accept": "application/json"}'
    if body:
        call += ",\n                            json=" + body.replace("\n", "\n" + " " * 33)
    return ("import requests\n\n" + call + ", timeout=30)\n"
            "data = response.json()\n"
            "if not response.ok:\n"
            "    raise RuntimeError(f\"{data['error']['code']}: {data['error']['message']}\")\n"
            "print(data)")


def _php(method, url, body):
    lines = ["<?php", f'$ch = curl_init("{url}");', "curl_setopt_array($ch, [",
             f'    CURLOPT_CUSTOMREQUEST => "{method}",',
             "    CURLOPT_RETURNTRANSFER => true,",
             '    CURLOPT_HTTPHEADER => ["Accept: application/json"'
             + (', "Content-Type: application/json"' if body else "") + "],"]
    if body:
        lines.append(f"    CURLOPT_POSTFIELDS => '{body}',")
    lines += ["]);", "$data = json_decode(curl_exec($ch), true);",
              "$status = curl_getinfo($ch, CURLINFO_HTTP_CODE);", "curl_close($ch);",
              "if ($status >= 400) { throw new Exception($data['error']['code']); }",
              "print_r($data);"]
    return "\n".join(lines)


def _kotlin(method, url, body):
    request_body = ('"""' + body + '""".toRequestBody("application/json".toMediaType())'
                    if body else "null")
    return ("// OkHttp\nval client = OkHttpClient()\n"
            f"val request = Request.Builder()\n    .url(\"{url}\")\n"
            f"    .method(\"{method}\", {request_body})\n"
            "    .header(\"Accept\", \"application/json\")\n    .build()\n"
            "client.newCall(request).execute().use { response ->\n"
            "    val json = response.body!!.string()\n"
            "    if (!response.isSuccessful) error(json)\n    println(json)\n}")


def _swift(method, url, body):
    lines = [f'var request = URLRequest(url: URL(string: "{url}")!)',
             f'request.httpMethod = "{method}"',
             'request.setValue("application/json", forHTTPHeaderField: "Accept")']
    if body:
        lines += ['request.setValue("application/json", forHTTPHeaderField: "Content-Type")',
                  f'request.httpBody = #"{body}"#.data(using: .utf8)']
    lines += ["let (data, response) = try await URLSession.shared.data(for: request)",
              "guard let http = response as? HTTPURLResponse, http.statusCode < 400 else {",
              "    throw URLError(.badServerResponse)", "}",
              "print(String(data: data, encoding: .utf8)!)"]
    return "\n".join(lines)


def _csharp(method, url, body):
    lines = ["using var client = new HttpClient();",
             f'var request = new HttpRequestMessage(new HttpMethod("{method}"), "{url}");',
             'request.Headers.Add("Accept", "application/json");']
    if body:
        escaped = body.replace('"', '""')
        lines.append(f'request.Content = new StringContent(@"{escaped}", '
                     'System.Text.Encoding.UTF8, "application/json");')
    lines += ["var response = await client.SendAsync(request);",
              "var json = await response.Content.ReadAsStringAsync();",
              "if (!response.IsSuccessStatusCode) throw new Exception(json);",
              "Console.WriteLine(json);"]
    return "\n".join(lines)


def _dart(method, url, body):
    lines = ["import 'package:http/http.dart' as http;", "",
             f"final request = http.Request('{method}', Uri.parse('{url}'));",
             "request.headers['Accept'] = 'application/json';"]
    if body:
        lines += ["request.headers['Content-Type'] = 'application/json';",
                  f"request.body = r'''{body}''';"]
    lines += ["final response = await http.Response.fromStream(await request.send());",
              "if (response.statusCode >= 400) throw Exception(response.body);",
              "print(response.body);"]
    return "\n".join(lines)
