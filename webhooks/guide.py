"""The ERP webhook guide (Nihal, 2026-10-01): written once, shown on the page and
downloaded as PDF or Markdown. In plain words: part 1 for the company (no
technical knowledge needed), part 2 for its developer. It is filled in with
the company's own settings (address, field name, time zone), never its secrets.

A guide is a list of blocks: ("h2", text), ("h3", text), ("p", text),
("note", text), ("list", [items]), ("steps", [items]), ("code", text),
("table", headers, rows).
"""

import io
import json
from xml.sax.saxutils import escape

from webhooks.models import WebhookSettings
from webhooks.services import NO_ANSWER

TITLE = "Attendance webhook - how to connect your system"


def _example(key, zone, company_code, kind, check_out=True):
    event = {
        "event": kind,
        "event_id": "9b2f6c1e-4d0a-4f3b-9a51-2c7e8d1f0a42",
        key: "445909",
        "employee_name": "Rahim Uddin",
        "work_date": "2026-10-01",
        "check_in": "2026-10-01 09:02:13",
    }
    if check_out:
        event["check_out"] = "2026-10-01 18:20:40"
    event["device_ip"] = "27.147.177.102"
    event["timezone"] = zone
    event["company"] = company_code
    return event


#: What Test connection can say, and what to do - the same words as the page.
TEST_MESSAGES = [
    ("Connected", "Nothing - it works. Attendance is sent from now on."),
    ("Wrong secret key", "Copy the secret key again from your developer, then Save and test."),
    ("Nothing found at that address", "Check the address. If it is right, ask your developer to "
                                      "add the test address (part 2, step 5)."),
    ("This server is not allowed in", "Ask your developer to allow our server's address."),
    ("Your system is not ready", "Your developer has not set up the secret key on your system yet."),
    ("Your system had an error", "The problem is on your system; your developer can see it in its logs."),
    ("The address redirects somewhere else", "Use the address it redirects to (often https:// "
                                             "instead of http://)."),
] + [(title, fix) for title, fix in NO_ANSWER.values()]


def blocks(row, company):
    """The guide for this company: ``row`` is its WebhookSettings (or None)."""
    key = row.employee_key if row else "au_user_id"
    url = row.url if row else "https://your-erp.example.com/api/webhook/attendance"
    ping = (row.ping_url or url.rstrip("/") + "/ping") if row else url.rstrip("/") + "/ping"
    zone = company.timezone or "UTC"
    code = company.code
    every_scan = bool(row and row.mode == WebhookSettings.Mode.EVERY_SCAN)
    batch = row.batch if row else True
    arrived = _example(key, zone, code, "check_in", check_out=False)
    finished = _example(key, zone, code, "check_out")
    return [
        ("note", f"In short: {company.name}'s attendance system sends every employee's "
                 "check-in and check-out to your own system (ERP, payroll or HR software) "
                 "automatically, the moment it happens. You set it up once; after that nobody "
                 "has to export or upload anything."),

        # ------------------------------------------------------------------
        ("h2", "Part 1 - For the company (no technical knowledge needed)"),

        ("h3", "What it does"),
        ("list", [
            "When an employee scans in at the device, their check-in is sent to your system.",
            "When their working day is finished, their check-out is sent." if not every_scan else
            "After every later scan, their latest check-out so far is sent.",
            "Breaks in between are not sent as a check-out, so your system never closes a day "
            "too early." if not every_scan else
            "Your system keeps the first check-in and the latest check-out of the day.",
            "If your system is down, nothing is lost: we keep trying for about 24 hours and you "
            "can send again at any time.",
        ]),

        ("h3", "What you need from your developer"),
        ("p", "Only two things. Ask whoever looks after your ERP or software for:"),
        ("table", ["You need", "What it looks like"], [
            ["1. The webhook address", "A web address starting with https://, for example "
                                       "https://erp.example.com/api/webhook/attendance"],
            ["2. The secret key", "A long password, for example 7f3a9c1e5b... Your system uses "
                                  "it to know the attendance really comes from us."],
        ]),
        ("p", "Your developer finds everything they need in Part 2 of this guide - just send "
              "it to them."),

        ("h3", "How to switch it on"),
        ("steps", [
            "Sign in as the owner or company admin.",
            "Open Organisation → ERP webhook.",
            "Paste the address into \"Your system's webhook address\".",
            "Paste the secret key into \"Secret key\".",
            "Tick \"Send attendance to this address\".",
            "Press \"Save and test connection\".",
            "A green box saying \"Connected\" means it works. You are done.",
        ]),
        ("p", "Leave the \"Advanced\" section closed unless your developer asks you to change "
              "something there."),

        ("h3", "If the test is not green"),
        ("p", "The red box says what is wrong and what to do. The common messages:"),
        ("table", ["The message says", "What to do"], [list(item) for item in TEST_MESSAGES]),

        ("h3", "Checking it later"),
        ("list", [
            "\"What was sent\" on the same page lists every check-in and check-out, and what "
            "your system answered.",
            "\"Will be tried again\" means your system did not take it yet; it is retried "
            "automatically.",
            "\"Given up\" means it was not taken for about 24 hours. Fix the problem, then press "
            "\"Send failed again\".",
            "To stop sending, untick \"Send attendance to this address\" and Save. Nothing is "
            "lost; it continues when you tick it again.",
        ]),

        # ------------------------------------------------------------------
        ("h2", "Part 2 - For the developer"),

        ("h3", "1. In one table"),
        ("table", ["", ""], [
            ["Address (we POST to it)", url],
            ["Test address (we GET it)", ping],
            ["Format", "JSON, UTF-8, Content-Type: application/json"],
            ["Secret key", "Sent both ways on every request: X-Webhook-Secret: <secret> and "
                           "Authorization: Bearer <secret>. Read either one."],
            ["Signature (optional)", "X-Webhook-Signature: sha256=<HMAC-SHA256 of the raw body, "
                                     "keyed with the signing secret>"],
            ["Employee", f"{key} = the employee's Employee ID (the number used on the device)"],
            ["Times", f"\"YYYY-MM-DD HH:MM:SS\" in {zone}"],
            ["Requests", "Up to 500 events in {\"events\": [...]}" if batch
             else "One event per request"],
            ["Your answer", "Any 2xx within 10 seconds. Redirects are not followed."],
        ]),

        ("h3", "2. What we send"),
        ("p", "When an employee comes in (event check_in):"),
        ("code", json.dumps(arrived, indent=2)),
        ("p", "When their day is finished (event check_out - it carries check_in too):"),
        ("code", json.dumps(finished, indent=2)),
        ("p", "Several at once, as one request:" if batch else
              "Each event comes in a request of its own, exactly as above."),
        ("code", json.dumps({"events": [arrived, finished]}, indent=2)) if batch else
        ("p", "If you would like them grouped, ask the company to tick \"Send several in one "
              "request\" under Advanced."),

        ("h3", "3. The fields"),
        ("table", ["Field", "Meaning"], [
            ["event", "check_in - they came in. check_out - their day has a check-out. "
                      "update - a day already sent was corrected; use its values."],
            ["event_id", "Unique per event and the same on every retry - use it to ignore "
                         "a repeat."],
            [key, "The employee's Employee ID. Match your employee on this."],
            ["employee_name", "For your logs only."],
            ["work_date", "The attendance day. A night shift's check-out after midnight "
                          "belongs to the day the shift started."],
            ["check_in", "The first scan of the day."],
            ["check_out", "The last scan out. Not present on a check_in event."],
            ["device_ip", "The public address of the device that took the scan, when known."],
            ["timezone", "The time zone of check_in and check_out."],
            ["company", "The company's code in our system."],
        ]),

        ("h3", "4. What your address must do"),
        ("steps", [
            "Check the secret key (and the signature, if you gave a signing secret). "
            "Wrong: answer 401.",
            "Read the events: a single object, or the list in \"events\".",
            f"Find the employee by {key}. Keep the earliest check_in and the check_out of "
            "each work_date.",
            "Answer 200. The same event may come twice (we retry until we get a 2xx) - "
            "saving it twice must change nothing.",
        ]),
        ("p", "Optional: answer with {\"results\": [...]}, one item per event in the same order. "
              "An item with \"result\": \"skipped\" is not sent again (put the reason in "
              "\"message\"); \"failed\" is retried."),

        ("h3", "5. The test address"),
        ("p", f"Our \"Test connection\" button sends GET {ping} with the same secret key and no "
              "data. Answer 200 when the key is right and 401 when it is not. Save nothing."),

        ("h3", "6. If you do not answer 2xx"),
        ("p", "We try again after 1, 2, 5, 15 and 30 minutes, then 1, 3, 6 and 12 hours - about "
              "a day in all - then give up. The company can send everything again with one "
              "button."),

        ("h3", "7. Try it yourself"),
        ("code", f"# Test address\ncurl -i -H \"X-Webhook-Secret: <secret>\" {ping}\n\n"
                 f"# One check-in\ncurl -i -X POST {url} \\\n"
                 "  -H \"Content-Type: application/json\" \\\n"
                 "  -H \"X-Webhook-Secret: <secret>\" \\\n"
                 f"  -d '{{\"{key}\":\"445909\",\"work_date\":\"2026-10-01\","
                 "\"check_in\":\"2026-10-01 09:02:13\"}'"),

        ("h3", "8. Example receiver (PHP / Laravel)"),
        ("code", "Route::post('/api/webhook/attendance', function (Request $request) {\n"
                 "    abort_unless(hash_equals(config('services.attendance.secret'),\n"
                 "        (string) $request->header('X-Webhook-Secret')), 401);\n"
                 "    $events = $request->input('events', [$request->all()]);\n"
                 "    foreach ($events as $event) {\n"
                 f"        // find the employee by $event['{key}'], keep the earliest\n"
                 "        // check_in and the check_out for $event['work_date']\n"
                 "    }\n"
                 "    return response()->json(['success' => true]);\n"
                 "});"),
        ("h3", "Example receiver (Python)"),
        ("code", "import hmac, json\n\n"
                 "def receive(headers, raw_body, secret):\n"
                 "    if not hmac.compare_digest(headers.get('X-Webhook-Secret', ''), secret):\n"
                 "        return 401\n"
                 "    data = json.loads(raw_body)\n"
                 "    for event in data.get('events', [data]):\n"
                 f"        save(event['{key}'], event['work_date'],\n"
                 "             event.get('check_in'), event.get('check_out'))\n"
                 "    return 200"),

        ("h3", "9. If your system is the IGL ERP"),
        ("p", "It already takes this format. Address: https://<erp>/api/webhook/attendance. "
              "Secret key: ATTENDANCE_WEBHOOK_SECRET from its .env (and "
              "ATTENDANCE_WEBHOOK_SIGNING_SECRET as the signing secret, if set). Keep the "
              "employee field au_user_id and \"When they come in, and when their day is "
              "finished\". If ATTENDANCE_WEBHOOK_IPS is set, add our server's address to it."),
    ]


# --- the three ways it is shown -----------------------------------------------


def markdown(row, company):
    lines = [f"# {TITLE}", "", f"**{company.name}**", ""]
    for block in blocks(row, company):
        kind = block[0]
        if kind == "h2":
            lines += ["---", "", f"## {block[1]}", ""]
        elif kind == "h3":
            lines += [f"### {block[1]}", ""]
        elif kind == "p":
            lines += [block[1], ""]
        elif kind == "note":
            lines += [f"> {block[1]}", ""]
        elif kind == "list":
            lines += [f"- {item}" for item in block[1]] + [""]
        elif kind == "steps":
            lines += [f"{number}. {item}" for number, item in enumerate(block[1], 1)] + [""]
        elif kind == "code":
            lines += ["```", block[1], "```", ""]
        elif kind == "table":
            headers, rows = block[1], block[2]
            lines.append("| " + " | ".join(h or " " for h in headers) + " |")
            lines.append("|" + "---|" * len(headers))
            lines += ["| " + " | ".join(cell.replace("|", "\\|") for cell in r) + " |"
                      for r in rows]
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def pdf(row, company):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (ListFlowable, ListItem, Paragraph, Preformatted,
                                    SimpleDocTemplate, Spacer, Table, TableStyle)

    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=10, leading=14.5)
    small = ParagraphStyle("small", parent=body, fontSize=9, leading=12.5)
    note = ParagraphStyle("note", parent=body, backColor=colors.HexColor("#e8f0f6"),
                          borderColor=colors.HexColor("#c9dbe8"), borderWidth=0.6,
                          borderPadding=8, spaceBefore=4, spaceAfter=10)
    part = ParagraphStyle("part", parent=styles["Heading1"], fontSize=16, spaceBefore=14,
                          textColor=colors.HexColor("#111113"))
    code = ParagraphStyle("code", fontName="Courier", fontSize=7.8, leading=10,
                          backColor=colors.HexColor("#f6f3ee"), borderPadding=6,
                          borderColor=colors.HexColor("#e3ddd2"), borderWidth=0.5)
    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm, title=TITLE,
                            author=company.name)
    story = [Paragraph(escape(TITLE), styles["Title"]),
             Paragraph(escape(company.name), styles["Heading3"]), Spacer(1, 4)]
    width = A4[0] - 36 * mm
    for block in blocks(row, company):
        kind = block[0]
        if kind == "h2":
            story += [Spacer(1, 8), Paragraph(escape(block[1]), part)]
        elif kind == "h3":
            story.append(Paragraph(escape(block[1]), styles["Heading3"]))
        elif kind == "p":
            story.append(Paragraph(escape(block[1]), body))
        elif kind == "note":
            story.append(Paragraph(escape(block[1]), note))
        elif kind in ("list", "steps"):
            story.append(ListFlowable(
                [ListItem(Paragraph(escape(item), body), leftIndent=14) for item in block[1]],
                bulletType="1" if kind == "steps" else "bullet",
                start=1 if kind == "steps" else "-", leftIndent=14))
        elif kind == "code":
            story += [Spacer(1, 3), Preformatted(block[1], code, maxLineLength=96), Spacer(1, 6)]
        elif kind == "table":
            headers, rows = block[1], block[2]
            data = ([[Paragraph(f"<b>{escape(h)}</b>", small) for h in headers]]
                    if any(headers) else [])
            data += [[Paragraph(escape(cell), small) for cell in r] for r in rows]
            table = Table(data, colWidths=[width * 0.34, width * 0.66],
                          repeatRows=1 if any(headers) else 0)
            table.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e3ddd2")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (-1, 0) if any(headers) else (0, -1),
                 colors.HexColor("#f5f1ea")),
                ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ]))
            story += [Spacer(1, 3), table, Spacer(1, 6)]
    doc.build(story)
    return out.getvalue()
