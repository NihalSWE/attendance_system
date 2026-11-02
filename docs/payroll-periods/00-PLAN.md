# Salary periods, weekly pay, facilities and two attendance items — plan (2026-10-24)

Status (2026-10-24): the senior took the defaults for every decision. **Built:**
item 1 (Today / Yesterday), item 3 (salary month from the settings) and item 4
(weekly pay groups), branch `feature/salary-periods`. **Waiting:** item 2 until
the live database is loaded here; items 5 and 6 later. Each item says what
existed, what we build, worked examples and the decisions, with the default
in **bold**.

**How 3 and 4 were built** (for whoever changes them next):

- `payroll/periods.py` is the one place that turns a salary month into dates
  (`bounds`, `full_days`, `month_of`, `name`); the start day is dated by
  `SalaryCycle.first_month` and changes only from a month with no salary run
  and no bonus/deduction/LFA line (weekly runs do not count).
- `payroll/paygroups.py`: `PayGroup` (name, week start) and `PayGroupMember`
  (a department or one person, from a date; a person's row wins, and a
  person's row with no group keeps them monthly). `Groups.group_id` says who
  is in which group on a day; somebody on a monthly salary is always Monthly
  staff. `generate_payroll(pay_group=, week=)` builds a week's run; the
  monthly run leaves out the days people were weekly. A finalised week locks
  attendance only for that week's people (`attendance.services.weekly_locked_ranges`).
- Screens: Salary settings → **Pay groups** (add a group; put a department or
  person in it from a date); Salary page → **Paid** (Monthly staff or a group)
  and **Week**, with the same Generate → Submit → Approve → Undo buttons.
- Not done yet: the mobile/API salary endpoints still show the Monthly staff's
  month only.

## Order of work

| # | Item | Changes pay? | Size | When |
|---|---|---|---|---|
| 1 | Daily list: **Today** and **Yesterday** buttons | No | 1 hour | first |
| 2 | Attendance not worked out for some linked, authorised people | Fixes it | half to 1 day | after the live database copy is loaded here |
| 3 | Salary month from the settings (e.g. 26th to 25th), with a "preview" label | Yes | 4–5 days | after the answers below |
| 4 | Weekly pay groups (by department or person) | Yes | 1–1.5 weeks more | after 3 |
| 5 | Facilities (mobile bill, lunch, conveyance) paid in or outside salary | Yes | 1–2 days | after 4, or before if more urgent |
| 6 | Final settlement for a leaver (optional) | Yes | 1–2 days | later, if wanted |

---

## 1. Daily list: Today and Yesterday

**Today:** Attendance → Daily list filters by month, by one day ("On date") or
a range. Picking today means opening the date picker.

**Build:** two buttons next to the filters, **Today** and **Yesterday**. Each is
a link to the same page with that day set (`?on=…`); the chosen one shows as
active. Other filters (branch, department, status) stay as they are. Company
time zone (Asia/Dhaka), so "today" is the factory's today.

---

## 2. Attendance not worked out although linked and authorised

**Today:** some people show as linked and authorised on the device, yet no
attendance day appears for them.

**Plan:** load the live database copy here (steps at the end of this file),
then for each person named:

1. Their device link (DeviceEnrollment): dates, device user number, enabled.
2. Their scans (PunchEvent): do they arrive, with which user number, and are
   they matched to the person (authorization status)?
3. Their placement on those days, their department's shift (no shift = no
   days), inactive periods, joining / leaving dates.
4. What attendance did with those days, and why.

Then fix the cause in the code (not by hand in the database), add a test that
fails without the fix, and say what the client must change if the cause is a
setting. **Needed from you:** the Employee IDs or names of two or three people
it happens to, and a date it happened.

---

## 3. Salary month from the settings

**Today:** a salary month is always the calendar month (1–31 Oct). A
`period_start_day` setting exists but nothing uses it.

**Build:**

- **Salary settings → "Salary month starts on day"**: 1 to 28, with a **From**
  date. Kept as dated rows, like shifts, so a change never rewrites the past.
- The Salary page lists **periods**, not calendar months:
  "October 2026 (26 Sep – 25 Oct)". Generate, view and regenerate work as
  today: as often as you like until it is submitted.
- When the period has not ended yet, the page says **"Preview: the period
  ends on 25 Oct; days after today are not counted yet."**

**How a change of start day works** (example: from the 1st to the 26th, from
26 Oct 2026):

| Period | Dates | Note |
|---|---|---|
| September 2026 | 1 Sep – 30 Sep | as before |
| October 2026 | 1 Oct – 25 Oct | short changeover period (25 days) |
| November 2026 | 26 Oct – 25 Nov | the new pattern |
| December 2026 | 26 Nov – 25 Dec | |

No day is paid twice and none is missed: each period starts the day after the
last one ended, always.

**What changes in the calculation** (everything else stays as it is):

| Rule today (calendar month) | With a salary period |
|---|---|
| Monthly salary ÷ days in the month (or ÷ 30, or ÷ workdays: the company's setting) | ÷ days in the **period** (or ÷ 30, or ÷ workdays) |
| Joined or left mid-month: paid for employed days | paid for employed days **in the period** |
| Allowances and deductions per month | per period |
| Penalty rules "in a month" | "in a period" |
| A finalised month locks its days | a finalised period locks its days (already works by dates) |
| Leave balances by calendar year | unchanged |
| Attendance pages and monthly attendance reports | unchanged: calendar months |

**Worked example:** Rahim, 30,000 a month, company divides by calendar days.

| Period | Days | Absent | Pay |
|---|---|---|---|
| October 2026 (1–25 Oct, changeover) | 25 | 0 | 30,000 × 25 ÷ 31 = **24,193.55** |
| November 2026 (26 Oct – 25 Nov) | 31 | 1 | 30,000 − 30,000 ÷ 31 = **29,032.26** |

**Decisions:**

1. A period is named after the month it **ends** in (26 Sep – 25 Oct =
   "**October**")? **Default: yes.**
2. The short changeover period pays basic × its days ÷ **the days of its
   calendar month** (25 ÷ 31 above)? **Default: yes.**
3. Start days 29, 30 and 31 are not offered (February would break them).
   **Default: 1–28 only.**

---

## 4. Weekly pay groups

**Today:** every salary run is a month, for everybody. A person can be paid
monthly, daily or hourly, but always once a month.

**Build: pay groups.** A pay group is people paid on the same schedule.

| Pay group | Paid | Period |
|---|---|---|
| Monthly staff (every company starts with this, everyone in it) | Monthly | from item 3's start day |
| Weekly workers (added by the company) | Weekly | **Saturday to Friday** |

- **Who is in a group:** by **department** (Production → Weekly workers), with
  a **per-person override** (one supervisor in Production stays monthly).
  Dated, like a placement: a move takes effect from the start of a period.
- **Each group has its own runs:** Salary page → choose the group → its
  periods. Weekly workers: one run a week; same draft → submit → approve →
  finalise; regenerate as often as needed until submitted.
- **Nothing changes for a company that adds no group.**

**Worked example:** Karim, Weekly workers, daily rate 600, week Sat 17 Oct –
Fri 23 Oct (Friday is the weekly off).

| Day | Attendance | Pay |
|---|---|---|
| Sat–Wed (5 days) | present | 5 × 600 = 3,000 |
| Thu | half day | 300 (half day pays 50%) |
| Fri | weekly off | 0 (daily pay: days off unpaid unless the company's setting pays them) |
| | 2 h approved overtime | 600 ÷ 8 × 2 × 2 = 300 |
| **Week's pay** | | **3,600** |

**Decisions:**

1. Weekly groups pay by **daily rate × payable days** (the existing "daily"
   pay basis), or hourly. A person on a monthly salary cannot be put in a
   weekly group (refused, with the reason). **Default: yes.** *Built as:* a
   monthly salary keeps the person in Monthly staff even when their department
   is weekly, so a department can be moved without checking each person.
2. The week runs **Saturday to Friday**. **Default: yes.**
3. Overtime, leave and penalties are counted in the week they happen.
   **Default: yes.**
4. Allowances and deductions (and facilities, item 5) for weekly workers: paid
   in the **last week that ends in the month** (one payment a month), not split
   per week. **Default: yes.**
5. A week that crosses the end of a month belongs to the week (it is not cut).
   **Default: yes.**

---

## 5. Facilities: mobile bill, lunch, conveyance — in or outside salary

**Today:** "Salary components" exist (Salary → Allowances and deductions):
each is an allowance or a deduction, a fixed amount or a % of basic, given to
a person with dates, and paid **inside** the salary (gross and net).

**Build:** one more choice on each allowance: **How it is paid**:

- **In the salary**: as today, in gross and net, on the payslip.
- **Outside the salary**: worked out the same way and listed for the month in
  a separate **Facilities** sheet (and its own Excel / PDF), **not** added to
  gross or net. The payslip shows it under "Paid separately" for information.

Including or leaving out a facility for a person stays what it is today: give
or stop that component on their profile, with dates.

**Example:** Selina: basic 25,000; lunch 2,000 **in salary**; mobile bill
1,000 and conveyance 1,500 **outside**. Payslip: gross 27,000; "Paid
separately: mobile 1,000, conveyance 1,500". The Facilities sheet lists 2,500
for her to be paid in cash or by bank.

**Decisions:**

1. Facilities outside the salary are cut for absence or unpaid leave like the
   salary, or always paid in full? **Default: paid in full** (a fixed
   facility), unless set as "% of basic".
2. They need their own approval, or go with the period's salary approval?
   **Default: with the salary approval.**

---

## 6. Final settlement for a leaver (optional)

A person leaves on 12 October and must be paid now, before the period is
generated. Build: on End employment, **Pay final settlement**: a one-person
run for 26 Sep – 12 Oct inside the same October period (so nothing overlaps),
approved like any other. Later, when October is generated, they are already
paid and are left out.

---

## Getting the live database for item 2

On the server, as root, in `/opt/attendance`:

    source venv/bin/activate
    python manage.py dump_database --gzip --output /tmp/attendance_live.jsonl.gz
    chmod 644 /tmp/attendance_live.jsonl.gz

(The file must end in **.jsonl.gz**: `.json.gz` is refused.) On the PC:

    scp <ssh user>@<server address>:/tmp/attendance_live.jsonl.gz D:/attendance_device/db_dumps/

Then delete it on the server (`rm /tmp/attendance_live.jsonl.gz`) and load it
on the PC:

    venv/Scripts/python.exe manage.py load_database db_dumps/attendance_live.jsonl.gz
