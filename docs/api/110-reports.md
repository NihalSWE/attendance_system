# Reports & dashboard

Every report of the panel's Reports menu, as data or as a download, and the
dashboard. **API keys:** `reports:read`.

## How reports work

A report is built from what the system already records - the attendance
days, leave, overtime decisions and the scans themselves - by the same code as
the panel's report page. So a report never disagrees with the Daily list, the
Leave list or the Overtime page, and the API, the page and the Excel/PDF
download always hold the same rows.

Who sees a report is decided by **the page it reads from**: attendance
reports follow the Daily list, the Leave report the Leave list, the Overtime
report the Overtime page. A branch manager sees their branches, a department
head their department, the company every branch - no new permission to hand
out.

## The endpoints

| What | Endpoint |
|---|---|
| The reports you may open | `GET /api/v1/reports` |
| Run one | `GET /api/v1/reports/{slug}?…` |
| Download it | `GET /api/v1/reports/{slug}/export?file_type=xlsx` (or `pdf`) |
| Dashboard | `GET /api/v1/dashboard` |

## The reports

| slug | Report | Period | Its own filters | What it shows |
|---|---|---|---|---|
| `daily-attendance` | Daily Attendance Report | `on` | - | Everyone's day: status, in and out, hours worked, late and overtime. |
| `weekly-attendance` | Weekly Attendance Report | `week_start` | - | A week at a glance: one line per person, one letter per day, and totals. |
| `monthly-attendance` | Monthly Attendance Report | `year`, `month` | - | The month's register: one line per person, one letter per day, and totals. |
| `custom-attendance` | Customize Attendance Report | `from`, `to` | `view`, `status` | Any dates: totals per person, or every day - for one status if you like. |
| `leave` | Leave Report | `from`, `to` | `leave_status`, `leave_type` | Leave taken or asked for in a period, by person and leave type. |
| `daily-absent` | Daily Absent Report | `on` | - | Who was absent on a day. |
| `monthly-absent` | Monthly Absent Report | `year`, `month` | - | Absent days in a month, per person, and which days. |
| `daily-late` | Daily Late Report | `on` | - | Who came in late on a day, and by how much. |
| `monthly-late` | Monthly Late Report | `year`, `month` | - | Late days and minutes in a month, per person. |
| `working-hours` | Working Hour Report | `from`, `to` | - | Hours worked against shift hours, per person, over any dates. |
| `short-hours` | Less than Full Working Hour Report | `from`, `to` | - | Days someone came in but worked less than their shift. |
| `overtime` | Overtime Report | `from`, `to` | - | Overtime worked, and how much of it was approved. |
| `entry-logs` | Entry Logs Report | `from`, `to` | - | Every scan the terminals sent, as they sent it. |

Every report also takes `branch_id`, `department_id` and `employee_id`. A
filter outside what you may see is refused. Left out, the period is today,
this week (weeks start on Saturday), this month, or this month so far. A
range covers at most 92 days (the Leave report 366, Entry logs 31): a longer
one is shortened and `warnings` says so.

## Reading a report

```json
GET /api/v1/reports/daily-late?on=2026-10-05
{
  "title": "Daily Late Report", "period": "Monday, 05 October 2026",
  "filters": ["Period: Monday, 05 October 2026", "Branch: All branches"],
  "columns": [{"label": "Employee", "numeric": false, "kind": ""},
              {"label": "Late by", "numeric": true, "kind": "duration"}],
  "summary": [{"label": "Late", "value": "1"}],
  "count": 1, "next": null, "previous": null,
  "results": [["Rahim Uddin", "0:12"]]
}
```

Each row is a list with **one value per column**, in the columns' order -
draw the table from `columns`, page through `results` (`page`, `page_size`).
`kind` says how the panel draws a cell: `status` as a coloured badge, `code`
as a day's letter on the weekly and monthly grids (`legend` explains them),
`number` and `duration` faint when zero.

## Downloads

`…/export` answers the file, with the company, period, filters and totals at
the top - the panel's download. Excel holds at most 10,000 rows, PDF 1,500;
more is refused with a message: narrow the filters. Every download is kept in
the audit log.

## The dashboard

`GET /api/v1/dashboard` - the panel's front page: headcount (active,
probation, resigned), branches and departments, the newest people, **devices
that stopped calling in** (for whoever may manage devices) and **people still
in two hours after their shift ended** (for whoever may fix attendance). For
the owner or an unrestricted company administrator, as on the panel.
