# API plan — the whole project, phase by phase

Version 2, 2026-10-04, for Nihal and his senior.
Built **phase by phase**: each phase = its API **+ its documentation + its
tests, together**, pushed and checked before the next phase starts.

**Guiding rule from the senior:** keep it simple enough that anyone on the
team can read it later and understand what happens. Every choice below is
the plain, well-known way of doing it — no clever tricks.

---

## Part 1 — The design in one page

### 1.1 Where the API lives

- One new Django app, **`api`**, inside this project. No second server, no
  new language, no new database.
- One folder per area (`api/v1/employees/`, `api/v1/devices/`, …). Every
  folder looks the same: `serializers.py`, `views.py`, `urls.py`, `tests.py`.
- **The one rule:** an API endpoint never changes data on its own. It checks
  the request, **calls the same service function the website already uses**,
  and returns the answer. So the API and the website always behave the same,
  every business rule already written is respected, and the website itself
  is not touched.

Packages added (all widely used, well documented):
`djangorestframework` (the API), `djangorestframework-simplejwt` (login
tokens), `drf-spectacular` (Swagger / ReDoc documentation).

### 1.2 Login — two ways, both standard

| Who calls the API | How it logs in | In short |
|---|---|---|
| **People** — mobile app, desktop app, React/Vue frontend | **Email + password → token** | Login gives an *access token* (15 min) and a *refresh token* (30 days). Every request sends `Authorization: Bearer <access token>`. Before it runs out, the app swaps the refresh token for a new pair. Logout ends it. |
| **Machines** — ERP, other servers, a desktop sync program, another make of device | **API key, every request signed with HMAC** | The company admin creates a key (ID + secret, secret shown once). Each request carries the key ID, the time and an HMAC-SHA256 signature of the request. The server checks the signature, refuses old or repeated requests, and only allows what the key's scopes allow. |

Why not HMAC for the people apps: a person's app would have to keep a
signing secret, and an app or web page can be unpacked to take it out. A
short-lived token for people and HMAC for machines is the standard,
easy-to-explain split. (If later the senior wants every mobile request signed
too, it can be added on top without changing anything else.)

Protection for everyone:
- HTTPS only; plain HTTP is refused.
- A few wrong passwords lock the login for a while; every login is in the
  audit log.
- **Sessions list:** a person sees each device they are signed in on and can
  sign one out; a company admin can do it for their staff.
- Refresh tokens change on every use; an old one used again ends the session.
- API keys: scopes, optional IP allow-list, expiry, "last used", revoke at once.
- Secrets stored encrypted, shown once, never logged. Rate limits per person
  and per key.

### 1.3 The same conventions on every endpoint

- **Address:** `/api/v1/...` (a breaking change would become `v2`; `v1` keeps
  working).
- **Which company:** someone in several companies sends `X-Company: <id>`; it
  is checked against their memberships every time. An API key belongs to one
  company.
- **JSON**, `snake_case`. Times ISO 8601 with the company's offset
  (`2026-10-04T09:02:13+06:00`), dates `YYYY-MM-DD`.
- **Lists:** `?page=`, `?page_size=` (max 100), `?search=`, `?ordering=` and
  each list's own filters; answer `{"count", "next", "previous", "results"}`.
- **Errors**, always one shape, with the same words the website shows:
  ```json
  {"error": {"code": "validation_error", "message": "Some fields are not valid.",
             "fields": {"email": ["Another company already uses this email."]}}}
  ```
  `401` not logged in · `403` not allowed · `404` not found · `409` conflict
  (e.g. finalised payroll) · `422` invalid · `429` too many requests.
- **Actions** that are not plain create/read/update/delete are named verbs:
  `POST /employees/{id}/end-employment`, `POST /devices/{id}/test-connection`.
- **Files:** upload as multipart; download as a file.

### 1.4 Documentation — written with the code, never after

1. **Reference (generated from the code):** every endpoint has its summary,
   who may use it, parameters, a request example, a response example and its
   errors. Shown as **Swagger** (`/api/docs/`, try requests live) and **ReDoc**
   (`/api/redoc/`, easy reading); the same schema makes a **Postman
   collection**.
2. **Guide (written by hand, `docs/api/`):** one page per phase in plain
   words — what the area is, the flows step by step, a table of every
   endpoint, copy-paste examples.

**"Done" for every endpoint, in the same commit:** view + serializer ·
permission check · tests · reference annotation with examples · line in the
guide · changelog entry. **A test fails if any endpoint has no
documentation**, so nothing can be skipped or forgotten.

**Tests for every endpoint:** it works · it is refused without the right
permission · another company's data cannot be seen or changed · bad input
gets the right error.

---

## Part 2 — The phases at a glance

| # | Phase | What you can do through the API when it is done | ≈ endpoints |
|---|---|---|---|
| 0 | **Foundation & login** | Log in / out, tokens, sessions, password, API keys with HMAC; the docs site | 20 |
| 1 | **Root (platform)** | Manage companies, their status, features and administrators | 15 |
| 2 | **Company & branches** | Company profile, branches, departments, designations, who may do what | 30 |
| 3 | **Employees** | Everything on the employee pages, from creating to ending employment | 45 |
| 4 | **Shifts & calendar** | Shifts, attendance settings, weekly offs, holidays | 20 |
| 5 | **Devices — setup** | Register, edit, retire devices; connection and test; device rules | 15 |
| 6 | **Devices — data flow (device ⇄ software)** | Everything that comes from the device and everything sent to it, and every process in between | 40 |
| 7 | **Attendance** | Daily list, calendar, day detail, corrections, missed scans, review | 20 |
| 8 | **Leave** | Leave types, policies, balances, records, requests and approvals | 20 |
| 9 | **Salary (payroll)** | Salary runs, settings, components, penalties, payslips, overtime, LFA | 30 |
| 10 | **The employee's own app ("me")** | Everything an employee does for themself — the mobile app's main part | 25 |
| 11 | **Reports & dashboard** | Every report as data and as Excel / PDF; dashboard figures | 15 |
| 12 | **Integrations & finish** | ERP webhook, audit log, final review of all docs, Postman collection | 15 |

About **310 endpoints**. Order: foundation → root → company → branch →
employee → what builds on employees → the employee's own app → reports.
Each phase starts by checking its list against the website's pages, so
nothing is left out.

---

## Part 3 — Each phase in detail

Paths are under `/api/v1`. `{id}` is the record's ID. The lists are complete
to the best of today's knowledge; anything found missing while building is
added to that phase before it is called done.

### Phase 0 — Foundation & login

**Goal:** the base every later phase stands on, and all the ways to log in.

| Group | Endpoints |
|---|---|
| Health | `GET /ping` (is the API up, server time — apps use it to correct their clock) |
| Login | `POST /auth/login` · `POST /auth/refresh` · `POST /auth/logout` |
| Me | `GET /auth/me` (who I am, my companies, my role in each) |
| Sessions | `GET /auth/sessions` · `DELETE /auth/sessions/{id}` (sign one device out) · `POST /auth/sessions/sign-out-others` |
| Password | `POST /auth/password/change` · `POST /auth/password/forgot` · `POST /auth/password/reset` |
| API keys (company admin) | `GET /api-keys` · `POST /api-keys` (secret shown once) · `GET /api-keys/{id}` · `PATCH /api-keys/{id}` (name, scopes, IP list, expiry) · `POST /api-keys/{id}/rotate` · `DELETE /api-keys/{id}` (revoke) |
| HMAC check | `POST /auth/hmac-test` (says exactly what is wrong with a signature) |

**Also built:** the `api` app and its folder pattern, the error format,
paging, rate limits, the company header, the "every endpoint documented"
test, Swagger and ReDoc.

**Guide pages:** `01-getting-started.md` (first request in 5 minutes) ·
`02-authentication.md` (tokens step by step; HMAC signing with ready code in
Python, PHP and JavaScript) · `03-conventions.md` (IDs, times, lists,
company header) · `04-errors.md` · `05-how-the-api-is-built.md` (for the
team: the folder pattern, how to add an endpoint).

---

### Phase 1 — Root (platform)

**Goal:** the platform owner manages companies.

| Group | Endpoints |
|---|---|
| Companies | `GET /platform/companies` · `POST /platform/companies` (with its administrator) · `GET /platform/companies/{id}` · `PATCH /platform/companies/{id}` |
| Status | `POST /platform/companies/{id}/status` (trial / active / suspended, with reason) |
| Features | `GET /platform/features` · `POST /platform/companies/{id}/features` (switch a feature on / off) |
| Members | `GET /platform/companies/{id}/members` · `POST /platform/companies/{id}/administrators` · `PATCH /platform/companies/{id}/members/{id}` (role, status) |
| Overview | `GET /platform/dashboard` |

**Guide:** `10-platform.md`.

---

### Phase 2 — Company & branches

**Goal:** a company sets itself up.

| Group | Endpoints |
|---|---|
| Company profile | `GET /company` · `PATCH /company` · `PUT /company/logo` · `GET/PATCH /company/mail-settings` |
| Branches | `GET /branches` · `POST /branches` · `GET /branches/{id}` · `PATCH /branches/{id}` · `POST /branches/{id}/status` |
| Departments | `GET /departments` · `POST /departments` (adopt from the catalogue) · `POST /departments/{id}/copy` (to another branch) · `PATCH /departments/{id}` · `POST /departments/{id}/status` |
| Designations | `GET /designations` · `POST /designations` · `PATCH /designations/{id}` · `POST /designations/{id}/status` |
| Access | `GET /access` (who may do what) · `GET/PUT /access/designations/{id}` · `GET/PUT /access/departments/{id}` · `GET/PUT /access/people/{id}` (personal overrides) |
| Lookups | `GET /branches/{id}/departments` · `GET /departments/{id}/designations` (for dropdowns) |

**Guide:** `20-company-and-branches.md`.

---

### Phase 3 — Employees

**Goal:** everything on the Employees pages and the employee profile.

| Group | Endpoints |
|---|---|
| List & create | `GET /employees` (search, filters: branch, department, status, device) · `POST /employees` |
| Import | `POST /employees/import` (upload, get a preview) · `POST /employees/import/{id}/confirm` · `GET /employees/import/demo-file` |
| Profile | `GET /employees/{id}` (everything the profile shows) · `GET /employees/{id}/history` |
| Edit | `PATCH /employees/{id}` (details) · `PATCH /employees/{id}/personal` · `POST /employees/{id}/placement` · `POST /employees/{id}/salary` (both with "the latest save wins") · `PUT /employees/{id}/line-manager` |
| Photo | `GET /employees/{id}/photo` · `PUT /employees/{id}/photo` |
| Education | `GET /employees/{id}/education` · `POST …` · `PATCH …/{row}` · `DELETE …/{row}` |
| Documents | `GET /employees/{id}/documents` · `POST …` · `GET …/{doc}` (download) · `DELETE …/{doc}` |
| Status | `POST /employees/{id}/end-employment` · `POST /employees/{id}/inactive` (period) · `POST /employees/{id}/active` |
| Leave on profile | `PUT /employees/{id}/leave-policy` · `POST /employees/{id}/leave-adjustments` |
| Settings | `PATCH /employees/{id}/late-rules` · `PATCH /employees/{id}/overtime` · `PATCH /employees/{id}/report-visibility` · `PATCH /employees/{id}/devices/{enrollment}` (device permissions) |
| Login for an employee | `POST /employees/{id}/login` (give) · `POST …/login/password` · `POST …/login/disable` · `POST …/login/enable` · `PATCH …/login/role` |

**Guide:** `30-employees.md`.

---

### Phase 4 — Shifts & calendar

| Group | Endpoints |
|---|---|
| Overview & settings | `GET /schedule` · `GET/PATCH /attendance-settings` (incl. "Check-in and check-out": alternate or first-and-last) |
| Shifts | `GET /shifts` · `POST /shifts` · `GET /shifts/{id}` · `PATCH /shifts/{id}` · `POST /shifts/{id}/status` |
| Department shifts | `GET /department-shifts` · `POST /department-shifts` (a department's shift from a date) |
| Employee shifts | `GET /employees/{id}/shifts` · `POST /employees/{id}/shifts` · `POST /employees/{id}/shifts/{id}/end` |
| Weekly offs | `GET /weekly-offs` · `POST /weekly-offs` · `POST /weekly-offs/{id}/start` · `POST /weekly-offs/{id}/end` |
| Holidays | `GET /holidays` (by year) · `POST /holidays` · `POST /holidays/year` (a whole year at once) · `PATCH /holidays/{id}` · `POST /holidays/{id}/cancel` |

**Guide:** `40-shifts-and-calendar.md`.

---

### Phase 5 — Devices: setup

**Goal:** register and look after devices.

| Group | Endpoints |
|---|---|
| Devices | `GET /devices` · `POST /devices` (register) · `GET /devices/serial-check?serial=` · `GET /devices/{id}` · `PATCH /devices/{id}` · `POST /devices/{id}/retire` |
| Setup | `GET /devices/{id}/setup` (what to type on the terminal: server address, port, serial) · `GET /device-models` |
| Connection | `GET /devices/connections` (all devices: connected, late, not connected) · `GET /devices/{id}/connection` · `POST /devices/{id}/test-connection` · `GET /devices/{id}/test-connection/{test}` (result) |
| Departments | `GET /devices/{id}/departments` · `POST /devices/{id}/departments` · `POST /devices/{id}/departments/{id}/end` |
| Rules | `GET/PATCH /devices/attendance-rules` (which devices count for attendance) |

**Guide:** `50-devices-setup.md`.

---

### Phase 6 — Devices: data flow (device ⇄ software)

**Goal:** *A to Z between the software and the devices* — see everything a
device sends, send it anything it can do, and follow every process in
between.

**How it works today (explained in the guide with a diagram):** a ZKTeco
terminal calls the server every few seconds on its own (`/iclock/…`, the
protocol built into the device). With each call it **uploads** what is new
(scans, users, fingerprints, faces, settings) and **collects** commands
waiting for it. The server stores every upload as a *message*, turns scans
into *punches*, decides whose they are and whether they count, and rebuilds
attendance. The device's own `/iclock/` protocol cannot change — the device
decides it — so the API is the window onto all of it and the way to drive it.

| Direction | Group | Endpoints |
|---|---|---|
| **Device → software** | Messages (every upload, raw) | `GET /devices/{id}/messages` · `GET /device-messages/{id}` (raw text, parsed result, processing status) |
| | Punches (scans) | `GET /punches` (filters: device, employee, date, status) · `GET /punches/{id}` (who, counted or not, and why) |
| | Unresolved scans | `GET /punches/unresolved` (scans nobody is mapped to) · `POST /punches/recheck` (judge excluded scans again from a date) |
| | Users on the device | `GET /devices/{id}/users` (roster: ID, name, card, role, finger/face counts, linked employee, removed on the terminal, why not linked) |
| | Fingerprints & faces | `GET /devices/{id}/templates` (what is saved, by format) · `POST /devices/{id}/templates/save` |
| | Device settings | `GET /devices/{id}/options` (what the device reported about itself) |
| **Software → device** | Ask the device | `POST /devices/{id}/users/refresh` (send the user list) · `POST /devices/{id}/users/{pin}/read-back` · `POST /devices/{id}/commands` (the safe commands: refresh users / templates / settings, fetch attendance history) |
| | Write users | `POST /devices/{id}/users` (add / update one) · `DELETE /devices/{id}/users/{pin}` · `POST /devices/{id}/users/remove` (several) · `POST /devices/{id}/users/copy` (to another device) |
| | Load a device | `POST /devices/{id}/load` (all employees of the branch, in the background) · `GET /devices/{id}/load` (preparing X of Y, who could not go) |
| | Settings & address | `POST /devices/{id}/options` (allowed settings) · `POST /devices/{id}/server-address` · `GET /devices/{id}/server-address` (progress) · `POST /devices/{id}/server-address/cancel` |
| | The queue | `GET /devices/{id}/commands` (waiting, sent, answered, refused — with the device's answer) · `DELETE /devices/{id}/commands/{id}` (withdraw one not yet sent) · `GET /devices/{id}/jobs` (progress: done / waiting / refused) |
| **People ⇄ device users** | Linking | `GET /enrollments` · `POST /enrollments` (map) · `PATCH /enrollments/{id}` · `POST /devices/{id}/users/map-automatically` · `POST /devices/{id}/users/replace-old-links` · `POST /devices/{id}/users/import` (add device users as employees) · `POST /employees/{id}/map` · `POST /employees/bulk-map` · `POST /employees/send-to-devices` |
| **Other makes of device** | Push scans in | `POST /ingest/punches` (HMAC key with the `punches:write` scope) — a device or program that is not a ZKTeco terminal sends its scans here, and they go through exactly the same processing as a terminal's |

**Guide:** `60-devices-data-flow.md` — the flow drawn end to end (device →
message → punch → whose → counts? → attendance → ERP webhook, and software →
queue → device → answer), what each status means, and how to follow one scan
from the terminal to the attendance report.

---

### Phase 7 — Attendance

| Group | Endpoints |
|---|---|
| Lists | `GET /attendance` (daily list: filters date range, branch, employee, status) · `GET /attendance/late` · `GET /attendance/now` (who is in, on a break, left) |
| Calendar & day | `GET /attendance/calendar?employee=&month=` · `GET /attendance/days/{employee}/{date}` (every scan with its label, totals, worked (paid)) |
| Review | `GET /attendance/review` (days to review) |
| Fix a day | `POST /attendance/days/{employee}/{date}/fix` · `POST /attendance/corrections/{id}/withdraw` |
| Missed scans (staff) | `GET /missed-scans` · `POST /missed-scans` (enter one for someone) · `POST /missed-scans/{id}/decide` (approve / reject) |
| Exports | `GET /attendance/export?format=xlsx|pdf` |

**Guide:** `70-attendance.md` (includes how a day is worked out: check-in,
check-out, breaks, the two "check-in and check-out" settings, when a day
closes).

---

### Phase 8 — Leave

| Group | Endpoints |
|---|---|
| Types | `GET /leave/types` · `POST /leave/types` · `POST /leave/types/defaults` · `PATCH /leave/types/{id}` · `POST /leave/types/{id}/status` |
| Policies | `GET /leave/policies` · `POST /leave/policies` · `GET /leave/policies/{id}` · `PATCH /leave/policies/{id}` · `POST /leave/policies/{id}/status` · `POST /leave/policies/{id}/versions` · `PATCH /leave/policies/{id}/versions/{v}` · `DELETE /leave/policies/{id}/versions/{v}` |
| Balances | `GET /leave/balances` |
| Records | `GET /leave/records` · `POST /leave/records` (record leave for someone) · `POST /leave/records/{id}/cancel` · `POST /leave/records/{id}/amend` · `GET /leave/records/{id}/document` |
| Approvals | `GET /leave/requests` (to approve) · `POST /leave/requests/{id}/decide` |

**Guide:** `80-leave.md`.

---

### Phase 9 — Salary (payroll)

| Group | Endpoints |
|---|---|
| Runs | `GET /payroll/runs` · `POST /payroll/runs` (generate a month) · `GET /payroll/runs/{id}` · `POST …/submit` · `POST …/finalise` · `POST …/send-back` · `POST …/reopen` |
| Settings | `GET/PATCH /payroll/settings` |
| Components | `GET /payroll/components` · `POST …` · `PATCH …/{id}` · `POST …/{id}/status` · `GET/POST /employees/{id}/components` · `POST /employees/{id}/components/{id}/end` |
| Penalties | `GET /payroll/penalty-rules` · `POST …` · `POST …/{id}/change` · `POST …/{id}/stop` · `POST /payroll/penalties/{id}/waive` · `POST …/unwaive` |
| Payslips | `GET /payroll/payslips/{id}` · `GET …/{id}/pdf` · `POST …/{id}/email` · `POST …/{id}/adjustments` · `POST …/{id}/corrections` · `DELETE …/adjustments/{id}` |
| Overtime | `GET /payroll/overtime` · `POST /payroll/overtime/{id}/decide` · `POST /payroll/overtime/{id}/undo` |
| LFA | `GET/PATCH /payroll/lfa/settings` · `GET /payroll/lfa/claims` · `POST …` · `GET …/{id}` · `POST …/{id}/cancel` · `POST …/{id}/paid` · `GET …/{id}/document` |

**Guide:** `90-salary.md`.

---

### Phase 10 — The employee's own app ("me")

**Goal:** what an employee, line manager or branch manager does for
themself. Built after the areas it uses — and the main part of the mobile app.

| Group | Endpoints |
|---|---|
| Profile | `GET /me/profile` · `PATCH /me/details` · `PUT /me/photo` · `GET/POST/PATCH/DELETE /me/education` |
| Attendance | `GET /me/attendance?month=` · `GET /me/attendance/{date}` |
| Missed scans | `GET /me/missed-scans` · `POST /me/missed-scans` · `POST /me/missed-scans/{id}/withdraw` |
| Leave | `GET /me/leave` (balances and requests) · `POST /me/leave` · `POST /me/leave/{id}/withdraw` · `GET /me/leave/{id}/document` |
| Approver | `GET /me/leave-inbox` · `POST /me/leave-inbox/{id}/decide` · `GET /me/branch-attendance` |
| Salary | `GET /me/payslips` · `GET /me/payslips/{id}` · `GET /me/payslips/{id}/pdf` · `GET/POST /me/lfa` · `POST /me/lfa/{id}/withdraw` |
| Account | `POST /me/password` |

**Guide:** `100-employee-app.md` (a "build the mobile app screens" walk-through).

---

### Phase 11 — Reports & dashboard

| Group | Endpoints |
|---|---|
| Reports | `GET /reports` (the list) · `GET /reports/{name}` (data, same filters as the page) · `GET /reports/{name}/export?format=xlsx|pdf` — for every report: daily, weekly, monthly, late, overtime, times out, leave, salary, … |
| Dashboard | `GET /dashboard` (the figures on the home page) |

**Guide:** `110-reports.md`.

---

### Phase 12 — Integrations & finish

| Group | Endpoints |
|---|---|
| ERP webhook | `GET/PATCH /webhook` · `POST /webhook/secret` (create a key) · `POST /webhook/test` · `POST /webhook/test-event` · `GET /webhook/events` · `POST /webhook/send-now` · `POST /webhook/send-again` · `POST /webhook/debug` (on / off) · `GET /webhook/debug` · `GET /webhook/guide?format=pdf|md` |
| Audit log | `GET /audit-log` (who did what, when, through which session or key) |

**Finish:** read every guide page again, top to bottom; check Swagger
against every page of the website; publish the Postman collection; write
`120-build-a-client.md` (a working client in 10 minutes).

**Guide:** `120-integrations.md`.

---

## Part 4 — How every phase is run

1. **List** — take the website's pages and actions for the area and write the
   exact endpoint list (it becomes the guide page's table).
2. **Build, one endpoint at a time** — serializer → view calling the service →
   permission → tests → documentation → guide line. An endpoint is finished
   before the next one starts.
3. **Check** — the area's tests, the "every endpoint documented" test, a walk
   through Swagger, then the full test suite.
4. **Push** — to Ajay's main, with the guide page and a short report: what
   was added, how to try it, anything to decide.
5. **Next phase only after that.**

## Part 5 — What the server needs

- **Phase 0 only:** three new Python packages (`pip install -r
  requirements.txt` once) and a few small tables (API keys, sessions, used
  request IDs).
- **Every phase after:** the usual `git pull`, `python manage.py migrate`,
  restart.
- The website and the device connection keep working exactly as now
  throughout.
