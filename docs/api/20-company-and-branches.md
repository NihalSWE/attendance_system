# Company & branches

Everything on the panel's **Organisation** menu: the company profile and
logo, the email settings, branches, departments, designations and who has
which access.

Each endpoint does exactly what its panel page does — the same checks, the
same messages, the same audit log lines. A company can use the panel, the API
or both.

## Who may do what

| | Owner, company admin | HR, payroll | Branch manager | Employee login |
|---|---|---|---|---|
| Company profile, logo, email settings | ✔ | — | — | — |
| See branches, departments, designations | ✔ | ✔ | — | — |
| Add or change them | ✔ | — | — | — |
| Access (give permissions by branch) | ✔ every branch | — | ✔ their branches | only if given *Give access* |

Someone limited to some branches (in their login's settings) sees and changes
only those branches, and what is in them. A record of another company — or
one you may not see — answers `not_found`.

**API keys** (for an ERP): `company:read` to read, `company:write` to change
the profile, branches, departments and designations. The email settings and
access are for people only — a key is refused there.

## The endpoints

| What | Endpoint |
|---|---|
| The company | `GET /api/v1/company` · `PATCH /api/v1/company` |
| The logo | `PUT /api/v1/company/logo` · `DELETE /api/v1/company/logo` |
| Email settings | `GET /api/v1/company/mail-settings` · `PATCH …/mail-settings` · `POST …/mail-settings/test` |
| Branches | `GET /api/v1/branches` · `POST /api/v1/branches` · `GET /api/v1/branches/{id}` · `PATCH /api/v1/branches/{id}` · `POST /api/v1/branches/{id}/status` |
| Departments | `GET /api/v1/departments` · `POST /api/v1/departments` · `GET /api/v1/departments/{id}` · `PATCH /api/v1/departments/{id}` · `POST /api/v1/departments/{id}/status` · `POST /api/v1/departments/copy` |
| Designations | `GET /api/v1/designations` · `POST /api/v1/designations` · `GET /api/v1/designations/{id}` · `PATCH /api/v1/designations/{id}` · `POST /api/v1/designations/{id}/status` |
| Access | `GET /api/v1/access/permissions` · `GET /api/v1/access/people` · `GET /api/v1/access/people/{employee_id}` · `PUT /api/v1/access/people/{employee_id}` |

## How the structure fits together

```
Company
└── Branch (Head Office, Chattogram, …)          one is the default
    └── Department (Software, HR, …)             code unique in the branch; may have a head
        └── Designation (Software Engineer, …)   code unique in the department; may report to another
```

- **Nothing is deleted.** Branches, departments and designations are set
  *inactive* with their status endpoint; their history stays.
- A **department's branch** and a **designation's department** are fixed once
  created (changing them would silently move people). Add a new one instead.
- A **new branch** gets the company's existing departments and designations
  copied in (`departments_copied` in the answer), as on the panel.
  `POST /api/v1/departments/copy` copies one branch's into another at any
  time; departments the target already has are left alone.
- The **default branch** cannot be retired; making another branch the default
  moves it.
- A department or designation **in use** (people placed in it) cannot be made
  inactive — move the people first.

## Changing a record

`PATCH` changes only the fields you send; everything else stays:

```
PATCH /api/v1/branches/3
{"phone": "01711000009"}
```

Choice lists (for a form in your app) come from the list endpoints with a
filter: `GET /api/v1/departments?branch_id=3&status=active`,
`GET /api/v1/designations?department_id=8&status=active`.

## The logo

Files travel inside the JSON, base64-encoded, so the request is signed like
any other:

```
PUT /api/v1/company/logo
{"filename": "logo.png", "content_base64": "iVBORw0KGgo…"}
```

PNG, JPG, WEBP or SVG, at most 2 MB. `DELETE /api/v1/company/logo` removes it.

## Email settings

The company's own SMTP account, used for payslips and two-step codes by
email. The password is write-only: `has_password` says one is saved, and
leaving `password` out keeps it. Save, then `POST …/mail-settings/test`; the
result is in `last_test`.

## Access

The panel's grid — permissions down, branches across:

1. `GET /api/v1/access/people/{employee_id}` → every cell with `granted` and
   `editable` (you may change it: you may give access there and hold that
   permission there).
2. `PUT` the same address with **everything the person should have**:

```json
{"grants": [{"permission": "employees.view", "branch_id": 1},
            {"permission": "leave.view", "branch_id": 1}],
 "reason": "Covers HR at Head Office"}
```

Editable cells that are not in the list are taken away; cells you may not
change stay as they are, whatever is sent. The answer lists what was `added`
and `removed`. You cannot change your own access, nor an owner's or company
administrator's (they have everything already).
