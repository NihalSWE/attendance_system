"""Phase 4: shifts & calendar (docs/api/40-shifts-and-calendar.md)."""

import datetime
from decimal import Decimal

from api.tests.test_company import CompanyApiTestCase
from common.tenant import use_company
from employees.services import create_employee
from organization.catalogue import adopt_department, adopt_designation
from scheduling.models import Shift

DAY = {"code": "day", "name": "Day shift", "start_time": "09:00", "end_time": "18:00",
       "grace_in_minutes": 10, "minimum_full_day_minutes": 420,
       "minimum_half_day_minutes": 240, "default_break_minutes": 60}


class ShiftApiTestCase(CompanyApiTestCase):
    def make_shift(self, **values):
        response = self.api("POST", "/api/v1/shifts", {**DAY, **values})
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def fields(self, response):
        return response.json()["error"].get("fields", {})


class ShiftTests(ShiftApiTestCase):
    def test_add_change_and_retire(self):
        shift = self.make_shift()
        self.assertEqual((shift["code"], shift["scheduled_minutes"], shift["ends_next_day"],
                          shift["start_time"]), ("DAY", 540, False, "09:00"))
        night = self.make_shift(code="NIGHT", name="Night", start_time="22:00",
                                end_time="06:00", minimum_full_day_minutes=400)
        self.assertTrue(night["ends_next_day"])
        changed = self.api("PATCH", f"/api/v1/shifts/{shift['id']}", {"grace_in_minutes": 15})
        self.assertEqual((changed.json()["grace_in_minutes"], changed.json()["name"]),
                         (15, "Day shift"))
        retired = self.api("POST", f"/api/v1/shifts/{night['id']}/status", {"status": "inactive"})
        self.assertEqual(retired.json()["status"], "inactive")
        active = self.api("GET", "/api/v1/shifts", query="status=active").json()
        self.assertEqual([row["code"] for row in active["results"]], ["DAY"])

    def test_the_panels_rules_apply(self):
        same = self.api("POST", "/api/v1/shifts", {**DAY, "end_time": "09:00"})
        self.assertIn("end_time", self.fields(same))
        too_long = self.api("POST", "/api/v1/shifts", {**DAY, "minimum_full_day_minutes": 600})
        self.assertEqual(too_long.status_code, 422)
        bad_time = self.api("POST", "/api/v1/shifts", {**DAY, "start_time": "9am"})
        self.assertIn("start_time", self.fields(bad_time))
        self.make_shift()
        twice = self.api("POST", "/api/v1/shifts", DAY)
        self.assertEqual(twice.status_code, 422)

    def test_hr_reads_but_does_not_change(self):
        self.make_shift()
        self.person("hr@example.test", role="hr")
        hr = self.logged_in("hr@example.test")
        self.assertEqual(self.api("GET", "/api/v1/shifts", session=hr).json()["count"], 1)
        self.assertEqual(self.api("POST", "/api/v1/shifts", {**DAY, "code": "X"},
                                  session=hr).status_code, 403)

    def test_another_companys_shift_is_not_found(self):
        with use_company(self.other):
            theirs = Shift.objects.create(company=self.other, code="T", name="Theirs",
                                          start_time=datetime.time(9), end_time=datetime.time(17),
                                          scheduled_minutes=480)
        self.assertEqual(self.api("GET", f"/api/v1/shifts/{theirs.pk}").status_code, 404)

    def test_api_keys_need_the_scope(self):
        key = self.key("shifts:read")
        self.assertEqual(self.as_key(key, "GET", "/api/v1/shifts").status_code, 200)
        refused = self.as_key(key, "POST", "/api/v1/shifts", DAY)
        self.assertEqual(self.code_of(refused), "scope_missing")


class SettingsAndAssignmentTests(ShiftApiTestCase):
    def setUp(self):
        super().setUp()
        self.shift = self.make_shift()
        with use_company(self.company):
            self.department = adopt_department(self.hq, "SW", "Software")
            designation = adopt_designation(self.department, "DEV", "Developer")
        self.rahim = create_employee(
            company=self.company, first_name="Rahim", employee_code="1", branch=self.hq,
            department=self.department, designation=designation,
            effective_from=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
            pay_basis="monthly", base_rate=Decimal("20000"))["employee"]

    def test_settings(self):
        refused = self.api("PATCH", "/api/v1/attendance-settings",
                           {"shift_mode": "company_single_shift", "company_shift_id": None})
        self.assertIn("company_shift_id", self.fields(refused))
        saved = self.api("PATCH", "/api/v1/attendance-settings", {
            "shift_mode": "company_single_shift", "company_shift_id": self.shift["id"],
            "punch_pairing_strategy": "first_last"})
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertEqual((saved.json()["company_shift"]["id"],
                          saved.json()["punch_pairing_strategy"]),
                         (self.shift["id"], "first_last"))
        # Not sent: kept as they were (on), not read as an unticked box.
        self.assertEqual((saved.json()["late_made_up_after_shift"],
                          saved.json()["came_in_is_half_day"]), (True, True))
        off = self.api("PATCH", "/api/v1/attendance-settings", {"came_in_is_half_day": False})
        self.assertEqual(off.status_code, 200, off.content)
        self.assertEqual((off.json()["late_made_up_after_shift"],
                          off.json()["came_in_is_half_day"]), (True, False))
        overview = self.api("GET", "/api/v1/schedule").json()
        self.assertTrue(overview["ready"])

    def test_department_shift(self):
        set_ = self.api("POST", "/api/v1/department-shifts", {
            "department_id": self.department.pk, "shift_id": self.shift["id"],
            "from_date": "2026-01-01"})
        self.assertEqual(set_.status_code, 201, set_.content)
        rows = self.api("GET", "/api/v1/department-shifts", query="on=2026-02-01").json()
        row = next(r for r in rows["results"] if r["department"]["id"] == self.department.pk)
        self.assertEqual(row["shift"]["id"], self.shift["id"])
        before = self.api("GET", "/api/v1/department-shifts", query="on=2025-12-01").json()
        row = next(r for r in before["results"] if r["department"]["id"] == self.department.pk)
        self.assertIsNone(row["shift"])

    def test_an_employees_own_shift(self):
        night = self.make_shift(code="NIGHT", name="Night", start_time="22:00", end_time="06:00",
                                minimum_full_day_minutes=400)
        path = f"/api/v1/employees/{self.rahim.pk}/shifts"
        given = self.api("POST", path, {"shift_id": night["id"], "first_day": "2026-11-01",
                                        "last_day": "2026-11-30", "reason": "Night desk"})
        self.assertEqual(given.status_code, 201, given.content)
        self.assertEqual((given.json()["kind"], given.json()["last_day"]),
                         ("temporary", "2026-11-30"))
        ended = self.api("POST", f"{path}/{given.json()['id']}/end", {"last_day": "2026-11-15"})
        self.assertEqual(ended.status_code, 200, ended.content)
        self.assertEqual(ended.json()["last_day"], "2026-11-15")
        backwards = self.api("POST", path, {"shift_id": night["id"], "first_day": "2026-12-10",
                                            "last_day": "2026-12-01"})
        self.assertEqual(backwards.status_code, 422)


class CalendarTests(ShiftApiTestCase):
    def test_weekly_offs(self):
        added = self.api("POST", "/api/v1/weekly-offs",
                         {"weekdays": ["friday", "saturday"], "from_date": "2026-01-01"})
        self.assertEqual(added.status_code, 201, added.content)
        self.assertEqual([r["weekday"] for r in added.json()["results"]], ["friday", "saturday"])
        again = self.api("POST", "/api/v1/weekly-offs",
                         {"weekdays": ["friday"], "from_date": "2026-02-01"})
        self.assertIn("weekdays", self.fields(again))
        friday = added.json()["results"][0]["id"]
        moved = self.api("POST", f"/api/v1/weekly-offs/{friday}/start", {"from_date": "2025-12-01"})
        self.assertEqual(moved.json()["from_date"], "2025-12-01")
        stopped = self.api("POST", f"/api/v1/weekly-offs/{friday}/end",
                           {"stops_from": "2027-01-01"})
        self.assertEqual(stopped.json()["stops_from"], "2027-01-01")
        bad = self.api("POST", "/api/v1/weekly-offs", {"weekdays": ["funday"],
                                                       "from_date": "2026-01-01"})
        self.assertEqual(bad.status_code, 422)

    def test_holidays(self):
        made = self.api("POST", "/api/v1/holidays", {"date": "2026-12-16", "name": "Victory Day"})
        self.assertEqual(made.status_code, 201, made.content)
        twice = self.api("POST", "/api/v1/holidays", {"date": "2026-12-16", "name": "Again"})
        self.assertEqual(twice.status_code, 422)
        many = self.api("POST", "/api/v1/holidays/year", {"days": [
            {"date": "2026-02-21", "name": "Language Martyrs' Day"},
            {"date": "2026-03-26", "name": "Independence Day"}]})
        self.assertEqual(many.status_code, 201, many.content)
        self.assertEqual(many.json()["count"], 2)
        clash = self.api("POST", "/api/v1/holidays/year", {"days": [
            {"date": "2026-12-16", "name": "Clash"}]})
        self.assertEqual(clash.status_code, 422)
        renamed = self.api("PATCH", f"/api/v1/holidays/{made.json()['id']}",
                           {"name": "Victory Day (observed)"})
        self.assertEqual((renamed.json()["name"], renamed.json()["date"]),
                         ("Victory Day (observed)", "2026-12-16"))
        cancelled = self.api("POST", f"/api/v1/holidays/{made.json()['id']}/cancel")
        self.assertEqual(cancelled.json()["status"], "cancelled")
        listed = self.api("GET", "/api/v1/holidays", query="year=2026&status=active").json()
        self.assertEqual(listed["count"], 2)
