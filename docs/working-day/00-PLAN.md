# Make a day a working day — plan (2026-10-17)

Status: **small version built 2026-10-17** (see `docs/PHASE_STATUS.md`,
"Working days on a weekly off"). The senior chose the defaults: normal rules
on the worked date, overtime only after the shift at the normal rate, past
dates allowed unless posted, one employee working a day off later. Dropped
from this plan for now: the linked swap day (add an ordinary holiday) and
adding the day to leave automatically (people on leave are named instead).
The rest of this file is the original, larger plan.

## 1. The problem

A company sometimes works on its weekly off — "this Friday we work, next
Thursday is off instead". Today the software has no way to say so:

- A Friday covered by the weekly-off rule stays **Weekly off** even when people
  scan (`attendance/services.py`, the `HOLIDAY, WEEKLY_OFF` branch).
- Everybody is paid for the day as a day off; people who did not come lose
  nothing.
- People who came get **every minute in the office as day-off overtime**
  (approved automatically when they scanned out), paid at the policy's
  day-off rate (2× by default).
- A hand correction cannot help: corrections only apply to working days.
- The table `scheduling.HolidayWorkAssignment` ("work on a holiday or weekly
  off", per employee) exists but nothing reads it — no screen, no engine, no
  payroll.

The only workaround is to stop the Friday weekly-off rule from that Friday,
add it again from the next day, and have the server run
`manage.py recalculate_attendance` — because adding/stopping weekly offs and
adding/cancelling holidays do **not** rebuild days already worked out.

## 2. What we will build

**A "working day" on the calendar**: one date, company-wide or for one branch,
that is a normal working day even though the weekly-off rule says otherwise.
Optionally with a **swap day**: another date given off instead (stored as an
ordinary paid Holiday, linked to the working day).

On that date everything behaves exactly like any other working day: the
person's shift, late / early / half day / absent, overtime only after the
shift at the normal rate, leave counts it, the live board expects people.

### Examples for the senior

Policy used in the examples (the default; each company's own policy may
differ): monthly salary ÷ 30 for one day, absence deducted by day, half day
pays 50%, overtime and day-off overtime both 2×. Shift 09:00–17:00, 8 paid
hours. **Rahim** earns 30,000/month → one day = 1,000, one hour = 125.
**Karim** is a daily worker at 800/day ("pay days off to daily staff" off).
Weekly off is Friday. The company decides to work **Friday 16 Oct** and gives
**Thursday 22 Oct** off instead.

| Who / what on Fri 16 Oct | Today (stays weekly off) | After (working day) |
|---|---|---|
| Rahim comes 09:00–17:00 | 30,000 + **8 h day-off overtime = 2,000** | 30,000, present, no overtime |
| Rahim comes 09:00–19:00 | 30,000 + 10 h day-off overtime = 2,500 | 30,000 + 2 h overtime = 500 |
| Rahim comes 09:40–17:00 | 30,000 + 2,000 (no late) | 30,000, **late 40 min** (late penalty rules apply) |
| Rahim does not come | 30,000 (paid day off) | **absent: −1,000** |
| Rahim comes 09:00–12:30 | 30,000 + 3.5 h day-off overtime = 875 | half day: **−500** |
| Karim comes 09:00–17:00 | 8 h day-off overtime = 1,600 (the day itself not paid) | **800** (a normal paid day) |
| Karim does not come | 0 | 0 (absent) |

| Who / what on Thu 22 Oct (swap day) | Today (normal working day) | After (paid holiday) |
|---|---|---|
| Rahim does not come | absent: −1,000 | 30,000, holiday, no cut |
| Rahim comes 09:00–17:00 | 30,000, present | 30,000 + 8 h day-off overtime = 2,000 |

So with a swap day, a monthly employee who works the Friday and rests the
Thursday ends the month with exactly the same pay as a normal month.

### Things the change must handle (not just the calendar)

1. **Approved leave around the date.** Leave only books working days, so a
   leave from Thu 15 to Sat 17 Oct booked Thu and Sat and skipped Fri. Once Fri
   is a working day, that person has no leave and no scans on Fri → **absent,
   −1,000**. Proposal: when saving, list everyone with leave around the date
   and **add the day to their leave** (uses one more day of balance), with the
   list shown in the confirmation modal. The reverse for a swap day: a leave
   day booked on the new holiday is **given back** to the balance.
   (Question 3.)
2. **Overtime already decided on that date.** A decision is kept per
   employee and date and wins over the rule. If an admin approved 480 day-off
   minutes, that would stay on the new working day. Proposal: decisions on a
   changed date are **cleared** (with an audit row) and listed, so overtime is
   worked out again under the new rule.
3. **Posted salary.** Dates inside a posted payroll month are refused ("reopen
   the month first"), the same as every other change.
4. **Rebuilding the days.** Saving (and cancelling) marks every employee-day
   on the changed dates as due (`AttendanceDue`); the background rebuilds them
   within a minute, and any page that shows them rebuilds first. No server
   command needed, any month that is not posted. In the same slice, **adding /
   stopping a weekly off and adding / cancelling a holiday** start doing the
   same (today they silently leave old days as they were).
5. **Cancelling** a working day puts the weekly off back, cancels its swap
   holiday, rebuilds both dates; leave added in step 1 is not removed
   automatically (it is listed for HR to change).
6. **Clashes.** The date must be a weekly off for that scope (a holiday is
   cancelled instead — existing screen); one active working day per date and
   scope; the swap day must be a working day and not already a holiday.

## 3. How (for the developers)

| Part | What |
|---|---|
| Table | `scheduling.WorkingDay` (`TenantOwned`, `ActorTracked`): `branch` (null = company-wide), `work_date`, `reason`, `swap_holiday` → `Holiday` (null), `status` active/cancelled, `cancelled_at/by`. Unique active per (company, branch, date). |
| Calendar | `WorkCalendar` loads active working days; `day()` order: **holiday → working day → weekly off → working**. Every user of the calendar (attendance, leave, live board, profile, payroll's shift check) gets it for free. |
| Services | `scheduling.services.add_working_day / cancel_working_day` (`require_structure_manager`, `_writable`, `create_validated`, `@transaction.atomic`, `record_company_event`), posted-month refusal, leave additions/returns via the leave services, overtime decisions cleared, days marked due. Holiday and weekly-off services start marking their dates due. |
| Screens | Scheduling → Holidays calendar: a weekly-off date gets **"Make it a working day"** (modal: scope, reason, optional swap date, the leave/overtime list to confirm). A **Working days** list (server-side DataTable: count, search, branch / year / status filters; cancel by modal). Calendar, Daily list and day page show "Working day (moved from weekly off): reason". |
| API | `/api/v1/shifts/working-days` list / create, `/{id}`, `/{id}/cancel`, same permissions as holidays; `docs/api/CHANGELOG.md`. |
| Tests | Calendar order; scans on the date → present/late/absent/half day; overtime normal rate; swap holiday paid; leave around the date extended / swap leave day returned; overtime decision cleared; posted month refused; branch scope only touches that branch; cancel restores; weekly-off/holiday changes now rebuild; every form POSTed; API. |
| Docs | `docs/PHASE_STATUS.md`, this file. |

Estimate: about **3–4 working days** (table + calendar + services + rebuild 1.5,
screens 1, API 0.5, leave/overtime handling and tests 1).

**Not in this slice:** one employee working a day off (the unused
`HolidayWorkAssignment`, e.g. a guard on Friday as a normal day, or overtime,
or a compensatory day off). Later, if the senior wants it (Question 6).

## 4. Questions for the senior

1. On a working Friday, are the normal rules right — late, leaving early,
   half day, and **absent with a salary cut** for people who do not come?
2. Overtime that day: **normal rate** (after the shift only), not the day-off
   rate?
3. People already on approved leave around the date: **add the day to their
   leave** (uses one more day of balance)? Or give it without using balance,
   or leave them absent?
4. The swap day is an ordinary **paid holiday** — someone who works it gets
   day-off overtime?
5. Allowed for **past dates** (e.g. last Friday) as long as that month's
   salary is not posted?
6. Do we also need **one employee** working a day off (guards, maintenance)
   now, or later?
