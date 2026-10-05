# Employees

Everything the panel's **Employees** list, **Create employee**, the employee
profile and **Edit employee** do. Each endpoint does exactly what its panel
page does — the same checks, the same messages, the same audit log lines.

Recording leave and approving a late arrival from the profile come with the
Leave and Attendance areas.

## Who may do what

| | Owner, company admin | HR | Branch manager | Given by hand (Access) |
|---|---|---|---|---|
| See employees | everyone | everyone | their branch | *View employees* in a branch |
| See pay | ✔ | only where given *View salaries* | their branch | *View salaries* |
| Add and edit people | ✔ | ✔ (no pay unless given) | their branch | *Create and edit employees* |
| Change pay | ✔ | only where given | their branch | *Prepare salary* + *View salaries* |
| End employment | ✔ | ✔ | their branch, Employee logins only, not themselves | *Create and edit employees* |

A department head sees and edits the people in the department they head.
Someone outside what you may see answers `permission_denied`; another
company's employee, `not_found`.

**API keys:** `employees:read` / `employees:write`. Pay is shown to a key only
with `payroll:read` too, and changing pay needs `payroll:write`.

## The endpoints

| What | Endpoint |
|---|---|
| The list | `GET /api/v1/employees` — filters `q`, `status`, `branch_id`, `setup` · download: `GET /api/v1/employees/export?file_type=xlsx` (or `pdf`; pay only where you may see it) |
| Add | `GET /api/v1/employees/choices` · `POST /api/v1/employees` |
| One person | `GET /api/v1/employees/{id}` · `GET /api/v1/employees/{id}/history` |
| Change | `PATCH /api/v1/employees/{id}` (name, work email, phone, joining date) · `PATCH …/{id}/personal` · `POST …/{id}/placement` · `POST …/{id}/salary` · `PUT …/{id}/line-manager` |
| Status | `POST …/{id}/end-employment` · `POST …/{id}/inactive` · `POST …/{id}/active` |
| Photo | `GET` · `PUT` · `DELETE …/{id}/photo` |
| Education | `GET` · `POST …/{id}/education` · `PATCH` · `DELETE …/{id}/education/{row_id}` |
| Documents | `GET` · `POST …/{id}/documents` · `GET` · `DELETE …/{id}/documents/{document_id}` |
| Their login | `GET` · `POST` (give) · `PATCH` (access) `…/{id}/login` · `POST …/login/password` · `POST …/login/disable` · `POST …/login/enable` |
| Leave | `PUT …/{id}/leave-policy` · `POST …/{id}/leave-adjustments` |
| Settings | `PUT …/{id}/overtime` · `PUT …/{id}/report-visibility` · `PUT …/{id}/reports` (set as line manager) |
| Devices | `GET …/{id}/devices` · `PATCH …/{id}/devices/{enrollment_id}` |
| Import | `POST /api/v1/employees/import` · `GET /api/v1/employees/import/demo-file` |

## Adding someone

1. `GET /api/v1/employees/choices` → the branches you may add to, and `pay`:
   `required` (you set pay), `optional` (in some branches) or `none` (whoever
   prepares that branch's salary sets it).
2. `GET /api/v1/employees/choices?branch_id=3` → that branch's departments;
   `&department_id=8` → that department's designations.
3. `POST /api/v1/employees`:

```json
{"first_name": "Rahim", "last_name": "Uddin", "employee_code": "E-0041",
 "branch_id": 3, "department_id": 8, "designation_id": 31,
 "start_date": "2024-02-01", "pay_basis": "monthly", "base_rate": "45000.00"}
```

The Employee ID may be reused only once the previous holder's placement has
ended. The answer is the new person's profile.

## Dated changes: placement and pay

Placement and pay are history that attendance and salary read, so each
change has a **from date**:

- a date **after** the current one starts → the old one is kept as history;
- the current one's date, or an earlier one → it is replaced from that date
  (the latest save wins).

```
POST /api/v1/employees/41/salary
{"pay_basis": "monthly", "base_rate": "50000.00", "from_date": "2026-11-01",
 "reason": "Yearly raise"}
```

Regenerate the month's salary afterwards to apply a pay change.

## Ending employment and inactive periods

- `POST …/end-employment` with `last_day` (today or earlier), `status`
  (`resigned`, `terminated`, `retired`) and `reason`. By default their login
  is disabled and they are removed from the devices; `devices_by_hand` names
  any terminal to clear yourself.
- `POST …/inactive` with `start_date`, optional `end_date` and `reason`: their
  scans on those days are blocked and not paid; status `suspended`.
  `POST …/active` makes them active from today.

## Files: the photo and documents

Files travel inside the JSON as base64, like the company logo:

```
PUT /api/v1/employees/41/photo
{"filename": "rahim.jpg", "content_base64": "/9j/4AAQSkZJRgABAQ…"}
```

- Photo: PNG, JPG or WEBP, up to 3 MB. Documents: PDF, JPG, PNG or WEBP, up to
  5 MB. Each is opened to check it really is that.
- `GET …/photo` and `GET …/documents/{document_id}` answer the file itself, only
  to someone who may see the person — never a public address.

## Their login

- `POST …/{id}/login` gives a login: `email` (it must not have one already),
  `password`, `password_confirm`, `role` (`employee`, `manager` with
  `branch_ids`, or `hr`).
- A branch login (a branch manager, or someone given *Manage logins*) gives
  **Employee** logins only, for people in their branches; making someone a
  branch manager or HR stays with the owner and company administrator.
- A new password (`…/login/password`) is refused for someone who also belongs
  to another company — only they change theirs.

## Importing a file

A `.csv` or Excel file with the headings **Employee ID** and **Name**
(`GET …/import/demo-file`, `?file_type=xlsx` for Excel):

1. `POST /api/v1/employees/import` with the file (and `branch_id`) →
   **checks only**: rows read, new, already here, and every bad row with why.
2. Fix the file until nothing is bad, then send it again with
   `"confirm": true` → they are imported into the branch's *Unassigned*
   department with no salary; set those afterwards.
