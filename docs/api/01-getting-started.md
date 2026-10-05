# Getting started

The Attendance Management API gives apps, frontends and other systems the
same functions as the **company, branch and employee panels** — the same data
and the same rules. Use the panels, the API, or both.

> **Status:** phase 2 (company & branches). Logging in, sessions, two-step
> login, passwords, API keys, the company profile, branches, departments,
> designations and access work; the panels' other functions follow phase by
> phase, as set out in the plan (`docs/api/00-PLAN.md` in the project).

## The address

Every endpoint is under one versioned address:

```
https://<your server>/api/v1/
```

A change that would break existing clients gets a new version (`/api/v2/`);
`v1` keeps working as it is.

## Your first request

`ping` needs no login. It tells you the API is up and gives the server's time:

```
curl https://<your server>/api/v1/ping
```

```json
{
  "status": "ok",
  "version": "v1",
  "server_time": "2026-10-04T04:15:00Z"
}
```

If you get this answer, the address is right.

## Who logs in, and how

| You are building | You log in with |
|---|---|
| A mobile or desktop app used by people | email + password → tokens, and every request signed |
| A web frontend (React, Vue, …) | email + password → secure cookies |
| A server-to-server integration (an ERP, a sync program) | an API key the company creates, every request signed |

Step by step, with code: [Logging in & signing](authentication/).

A person can do through the API exactly what they can do in their panel —
no more. A company owner or administrator works with the whole company; a
branch manager with their branches; an employee with their own records.

## Reading an endpoint's page

Each endpoint in the menu has its own page with:

- **what it does** and **who may use it**;
- **request parameters** — what you send. Nested fields open with the **+**
  button; *Expand all* opens everything;
- a **request example** in cURL, JavaScript, Python, PHP, Kotlin, Swift, C#
  and Dart — pick your language once and every page shows it;
- **response fields** and a **response example**;
- **every error it can return**, with what it means and how to fix it.

The [Error reference](/api/docs/errors/) lists every error in one place, and
[Conventions](/api/docs/conventions/) explains the rules every endpoint
follows (IDs, dates, lists, the company header).

## When something goes wrong

Every error has the same shape:

```json
{"error": {"code": "validation_error",
           "message": "Some fields are not valid.",
           "fields": {"name": ["This field is required."]},
           "reference": "5f0c2a9e1b7d"}}
```

Look the `code` up in the [Error reference](/api/docs/errors/). If you need
help, send the `reference` — it identifies exactly your request in the server
log (it is also in the `X-Request-Id` header of every answer).

## Also available

- [Swagger](/api/swagger/) — the OpenAPI description, to try requests or
  generate a client.
- The raw OpenAPI schema at `/api/v1/schema/` (import it into Postman).
