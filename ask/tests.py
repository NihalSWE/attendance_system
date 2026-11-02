"""Ask (2026-11-02): each search with each role, the two client examples end to
end with a fake AI client, and every way it falls back to the name search.

Head Office: Rahim, Clerk (an Employee login) and Manny (its branch manager).
Chittagong: Karim. Ajay Kumar (Head Office) and Ajay Das (Chittagong) are
added for "two Ajays". August 2026 is generated; Monday 10 August has Rahim
and Karim late, and Karim staying until 20:00.
"""

import datetime
import json
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import parse_qs

from django.test import TestCase
from django.urls import reverse

from ai_assistant import claude
from ai_assistant.models import AiCall, AiSettings
from ask import interpret
from attendance.services import recalculate
from common.tenant import use_company
from employees.services import create_employee
from leaves import services as leave_services
from leaves.tests_branch_access import MONDAY
from payroll.models import PayrollRecord
from payroll.tests_overtime import LATER
from payroll.tests_payroll_manager import PayrollManagerCase

UTC = datetime.timezone.utc
DAY = MONDAY.isoformat()
TUESDAY = MONDAY + datetime.timedelta(days=1)


def answer(search, **filters):
    """What Claude sends back: every field, "" or 0 where the question says nothing."""
    data = {"search": search, "status": "", "date_from": "", "date_to": "", "employee": "", "branch": "",
            "department": "", "year": 0, "month": 0, "overtime": "", "group_by": "", "reason": ""}
    data.update(filters)
    return data


class FakeClient:
    """Answers ``beta.messages.create`` with the next queued answer (a dict as
    JSON, a string as it is) and keeps the requests."""

    def __init__(self, answers, requests):
        self.answers, self.requests = answers, requests
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **request):
        self.requests.append(request)
        given = self.answers.pop(0)
        text = given if isinstance(given, str) else json.dumps(given)
        usage = SimpleNamespace(input_tokens=900, output_tokens=60, cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], usage=usage,
                               model=request["model"], stop_reason="end_turn", _request_id="req_ask")


class AskCase(PayrollManagerCase):
    def setUp(self):
        super().setUp()
        with use_company(self.company):
            far_placement = self.far.assignments.select_related("department", "designation").get()
        self.ajay_hq = self.person("Ajay", "Kumar", "E7", self.branch, self.hq_department, self.hq_designation)
        self.ajay_ctg = self.person("Ajay", "Das", "C7", self.unit, far_placement.department,
                                    far_placement.designation)
        # Monday: Rahim in at 9:35 and Karim at 9:40 (late), Karim until 20:00.
        self.punch(MONDAY, 9, 35)
        self.punch(MONDAY, 18, 5)
        self.far_punch(MONDAY, 9, 40)
        self.far_punch(MONDAY, 20)
        recalculate(self.company.pk, start=MONDAY, end=MONDAY, now=LATER)
        self.generate()
        self.settings = AiSettings.all_objects.create(
            company=self.company, ask_enabled=True, ask_consent_received=True,
            ask_consent_date=datetime.date(2026, 11, 1), api_key_encrypted=claude.encrypt("sk-test"))
        self.answers, self.requests = [], []
        claude.CLIENT_FACTORY = lambda key: FakeClient(self.answers, self.requests)
        self.addCleanup(setattr, claude, "CLIENT_FACTORY", None)

    def person(self, first, last, code, branch, department, designation):
        return create_employee(
            company=self.company, first_name=first, last_name=last, employee_code=code, branch=branch,
            department=department, designation=designation,
            effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC), pay_basis="monthly",
            base_rate=Decimal("30000"))["employee"]

    def ask(self, user, question, *answers):
        """Type ``question`` into Ask as ``user``; the AI answers ``answers``."""
        self.answers.extend(answers)
        self.client.force_login(user)
        return self.client.get(reverse("ask:ask"), {"q": question}, follow=True)

    def search(self, user, **params):
        """The page for an understood search, as the address carries it (no AI call)."""
        self.client.force_login(user)
        return self.client.get(reverse("ask:ask"), params)


class ExampleTests(AskCase):
    def test_late_today_end_to_end(self):
        page = self.ask(self.admin, "find all the employees who were late today",
                        answer("attendance_days", status="late", date_from=DAY, date_to=DAY))
        self.assertContains(page, "Late on 10 Aug 2026: 2 people.")
        self.assertContains(page, "Rahim")
        self.assertContains(page, "Karim")
        self.assertNotContains(page, "Clerk")
        for chip in ("Attendance days", "Late", "10 Aug 2026", "All branches"):
            self.assertContains(page, chip)
        # Links to the real pages: the day on the calendar, and the Late page.
        self.assertContains(page, f"{reverse('attendance:attendance_calendar')}?employee={self.employee.pk}&amp;date={DAY}")
        self.assertContains(page, reverse("attendance:attendance_late"))
        # One AI call, logged as "ask", with the question and no employee name in it.
        [call] = AiCall.all_objects.filter(company=self.company)
        self.assertEqual((call.kind, call.model, call.error), ("ask", "claude-haiku-5-5", ""))
        [request] = self.requests
        content = request["messages"][0]["content"]
        self.assertIn("find all the employees who were late today", content)
        self.assertIn("Branches: Chittagong; Head Office", content)
        self.assertNotIn("Rahim", content + request["system"][0]["text"])
        self.assertEqual(request["output_config"]["effort"], "low")

    def test_reloading_the_answer_does_not_call_the_ai_again(self):
        page = self.ask(self.admin, "late today", answer("attendance_days", status="late", date_from=DAY))
        url = page.redirect_chain[-1][0]
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(len(self.requests), 1)

    def test_salary_for_ajay_asks_which_then_shows_the_payslip(self):
        page = self.ask(self.admin, "how much salary generated for Ajay this month",
                        answer("salary_of", employee="Ajay", year=2026, month=8))
        self.assertContains(page, "2 people match “Ajay”. Which one?")
        self.assertContains(page, "Ajay Kumar")
        self.assertContains(page, "Ajay Das")
        choice = next(query for label, query in page.context["result"].choices if "Kumar" in label)
        page = self.client.get(reverse("ask:ask") + "?" + choice)
        record = PayrollRecord.all_objects.get(employee=self.ajay_hq)
        self.assertContains(page, f"Ajay Kumar, salary for August 2026: net {record.currency} {record.net_pay:,.2f}")
        self.assertContains(page, reverse("payroll:payslip", args=[record.pk]))
        self.assertEqual(len(self.requests), 1)

    def test_bangla_question_and_a_month_not_generated(self):
        page = self.ask(self.admin, "রহিমের নভেম্বরের বেতন কত",
                        answer("salary_of", employee="Rahim", year=2026, month=11))
        self.assertContains(page, "Salary for November 2026 has not been generated yet.")

    def test_no_one_by_that_name(self):
        page = self.ask(self.admin, "salary of Zubair in August",
                        answer("salary_of", employee="Zubair", year=2026, month=8))
        self.assertContains(page, "No one called “Zubair” among the people you can see.")


class AttendanceAccessTests(AskCase):
    LATE = {"s": "attendance_days", "status": "late", "from": DAY, "to": DAY}

    def test_admin_hr_and_payroll_manager_see_every_branch(self):
        # (One owner-or-admin per company: the company administrator stands for both.)
        for user in (self.admin, self.hr, self.payroll):
            page = self.search(user, **self.LATE)
            self.assertContains(page, "Late on 10 Aug 2026: 2 people.", msg_prefix=user.email)

    def test_a_branch_manager_sees_their_branch_only(self):
        page = self.search(self.manager, **self.LATE)
        self.assertContains(page, "Late on 10 Aug 2026: 1 person.")
        self.assertContains(page, "Rahim")
        self.assertNotContains(page, "Karim")
        self.assertContains(page, "Your branches")

    def test_an_employee_login_opens_nothing_until_given_access(self):
        page = self.search(self.clerk_user, **self.LATE)
        self.assertRedirects(page, reverse("me:home"), fetch_redirect_response=False)
        self.grant("attendance.view", self.branch)
        page = self.search(self.clerk_user, **self.LATE)
        self.assertContains(page, "Rahim")
        self.assertNotContains(page, "Karim")
        # With attendance but no salary, salary is refused in our code.
        page = self.search(self.clerk_user, s="salary_totals", year=2026, month=8)
        self.assertContains(page, "Your login does not show salaries.")

    def test_counts_most_first(self):
        page = self.search(self.admin, s="attendance_counts", status="absent", **{"from": DAY, "to": DAY})
        self.assertContains(page, "Absent on 10 Aug 2026")
        page = self.search(self.admin, s="attendance_counts", status="late", **{"from": DAY, "to": DAY})
        self.assertContains(page, "Late on 10 Aug 2026: 2 people; most")

    def test_in_the_office_now(self):
        for user in (self.admin, self.hr, self.manager):
            page = self.search(user, s="present_now")
            self.assertContains(page, "In the office now", msg_prefix=user.email)

    def test_a_person_outside_your_branches_is_not_found(self):
        page = self.search(self.manager, s="attendance_days", **{"from": DAY}, emp_id=self.far.pk)
        self.assertContains(page, "That person is not among the people you can see.")
        page = self.search(self.manager, s="attendance_days", **{"from": DAY}, emp="Karim")
        self.assertContains(page, "No one called “Karim” among the people you can see.")


class SalaryAccessTests(AskCase):
    TOTALS = {"s": "salary_totals", "year": 2026, "month": 8}

    def test_hr_and_the_auditor_get_no_salary(self):
        for user in (self.hr, self.auditor):
            for params in (self.TOTALS, {"s": "salary_of", "emp": "Rahim", "year": 2026, "month": 8}):
                self.client.force_login(user)
                page = self.client.get(reverse("ask:ask"), params)
                if user == self.auditor:   # a branch login without salary: the page does not open
                    self.assertEqual(page.status_code, 302)
                    continue
                self.assertContains(page, "Your login does not show salaries.")
                self.assertNotContains(page, "30,000")

    def test_totals_match_the_salary_page(self):
        net = sum(record.net_pay for record in PayrollRecord.all_objects.filter(payroll_run__company=self.company))
        for user in (self.admin, self.payroll):
            page = self.search(user, **self.TOTALS)
            self.assertContains(page, "5 people", msg_prefix=user.email)
            self.assertContains(page, f"{net:,.2f}", msg_prefix=user.email)

    def test_a_branch_manager_sees_their_branch_s_salary(self):
        page = self.search(self.manager, **self.TOTALS)
        hq = PayrollRecord.all_objects.filter(employee__in=[self.employee, self.clerk, self.ajay_hq])
        self.assertContains(page, "3 people")
        self.assertContains(page, f"{sum(r.net_pay for r in hq):,.2f}")
        page = self.search(self.manager, s="salary_of", emp="Karim", year=2026, month=8)
        self.assertContains(page, "No one called “Karim”")
        page = self.search(self.manager, s="salary_of", emp="Ajay", year=2026, month=8)
        self.assertContains(page, "Ajay Kumar, salary for August 2026")   # only one Ajay in their branch

    def test_by_branch(self):
        page = self.search(self.admin, by="branch", **self.TOTALS)
        self.assertContains(page, "Head Office")
        self.assertContains(page, "Chittagong")


class LeaveAndOvertimeAccessTests(AskCase):
    def setUp(self):
        super().setUp()
        # Tuesday: Monday's overtime stays (a leave day carries none).
        self.record_leave(self.employee, on=TUESDAY)
        self.record_leave(self.far, on=TUESDAY)

    def test_leave_on_a_day(self):
        page = self.search(self.hr, s="leave_on", **{"from": TUESDAY.isoformat()})
        self.assertContains(page, "On leave on 11 Aug 2026: 2 people.")
        page = self.search(self.manager, s="leave_on", **{"from": TUESDAY.isoformat()})
        self.assertContains(page, "On leave on 11 Aug 2026: 1 person.")
        self.assertNotContains(page, "Karim")

    def test_leave_balance(self):
        leave_services.create_leave_type(actor=self.admin, company_id=self.company.pk, values={
            "code": "ANN", "name": "Annual", "days_per_year": Decimal("10")})
        page = self.search(self.admin, s="leave_balance", emp="Rahim", year=2026)
        self.assertContains(page, "Rahim, leave left in 2026: Annual 10.")

    def test_overtime(self):
        page = self.search(self.admin, s="overtime", **{"from": "2026-08-01", "to": "2026-08-31"})
        self.assertContains(page, "Karim")
        page = self.search(self.manager, s="overtime", **{"from": "2026-08-01", "to": "2026-08-31"})
        self.assertNotContains(page, "Karim")
        # The payroll manager decides no overtime and sees none (as on the page).
        page = self.search(self.payroll, s="overtime", **{"from": "2026-08-01", "to": "2026-08-31"})
        self.assertContains(page, "Your login does not show overtime.")


class FallBackTests(AskCase):
    def assert_name_search(self, page, words):
        self.assertEqual(page.redirect_chain[-1][0], f"{reverse('employee_list')}?q={words}")
        self.assertContains(page, "Showing the employee search instead.")

    def test_ask_off(self):
        AiSettings.all_objects.filter(pk=self.settings.pk).update(ask_enabled=False)
        page = self.ask(self.admin, "who was late today")
        self.assert_name_search(page, "who+was+late+today")
        self.assertContains(page, "Ask is off for this company.")
        self.assertEqual(self.requests, [])

    def test_no_key_and_no_consent(self):
        AiSettings.all_objects.filter(pk=self.settings.pk).update(api_key_encrypted="")
        self.assertContains(self.ask(self.admin, "who was late today"), "No AI key is saved")
        AiSettings.all_objects.filter(pk=self.settings.pk).update(ask_consent_received=False)
        self.assertContains(self.ask(self.admin, "who was late today"), "consent")

    def test_daily_limit_reached(self):
        AiCall.all_objects.create(company=self.company, kind="ask", model="claude-haiku-5-5", cost=Decimal("6"))
        page = self.ask(self.admin, "who was late today")
        self.assertContains(page, "spending limit")
        self.assertEqual(self.requests, [])

    def test_unknown_and_bad_json(self):
        page = self.ask(self.admin, "what is the weather", answer("unknown", reason="Not about HR data."))
        self.assertContains(page, "Ask could not answer that: Not about HR data.")
        page = self.ask(self.admin, "who was late today", "not json at all")
        self.assertContains(page, "Ask could not read that question")
        self.assertEqual(AiCall.all_objects.filter(error__icontains="not valid JSON").count(), 1)

    def test_a_name_goes_straight_to_the_name_search(self):
        page = self.ask(self.admin, "Rahim Uddin Ahmed", answer("unknown", reason="name"))
        self.assertEqual(page.redirect_chain[-1][0], f"{reverse('employee_list')}?q=Rahim+Uddin+Ahmed")
        self.assertNotContains(page, "Showing the employee search instead.")

    def test_a_branch_login_without_the_name_search_sees_the_reason_here(self):
        self.grant("attendance.view", self.branch)
        AiSettings.all_objects.filter(pk=self.settings.pk).update(ask_enabled=False)
        page = self.ask(self.clerk_user, "who was late today")
        self.assertEqual(page.redirect_chain, [])
        self.assertContains(page, "Ask is off for this company.")

    def test_an_unknown_branch_name_is_said_and_dropped(self):
        page = self.ask(self.admin, "late today in Dhaka",
                        answer("attendance_days", status="late", date_from=DAY, branch="Dhaka"))
        self.assertContains(page, "No branch is called “Dhaka”")
        self.assertContains(page, "Late on 10 Aug 2026: 2 people.")


class ReadingTests(TestCase):
    TODAY = datetime.date(2026, 11, 2)

    def read(self, **filters):
        return interpret.read(answer(**filters), "q", self.TODAY, branches=[(1, "Head Office")],
                              departments=[(4, "Software"), (5, "Software")])

    def test_defaults_and_order(self):
        found = self.read(search="attendance_days", status="late").ask
        self.assertEqual(found.period, (self.TODAY, self.TODAY))
        found = self.read(search="overtime", date_from="2026-10-31", date_to="2026-10-01").ask
        self.assertEqual(found.period, (datetime.date(2026, 10, 1), datetime.date(2026, 10, 31)))
        found = self.read(search="salary_of", employee="Ajay").ask
        self.assertEqual((found.year, found.month, found.date_from), (2026, 11, None))
        found = self.read(search="attendance_counts", date_from="2024-01-01", date_to="2026-11-02").ask
        self.assertEqual((found.date_to - found.date_from).days + 1, interpret.MAX_SPAN_DAYS)

    def test_only_known_values_pass(self):
        self.assertIsNone(self.read(search="drop_tables", reason="x").ask)
        found = self.read(search="attendance_days", status="sleeping", month=13, branch="head office",
                          department="SOFTWARE").ask
        self.assertEqual((found.status, found.month, found.branch_id, found.department), ("", None, 1, "Software"))

    def test_the_address_round_trip(self):
        found = self.read(search="leave_on", date_from="2026-11-02", employee="Rahim").ask
        again = interpret.Ask.from_query({k: v[0] for k, v in parse_qs(found.query()).items()}, self.TODAY)
        self.assertEqual(again, found)
        tampered = interpret.Ask.from_query({"s": "salary_of", "month": "99", "emp_id": "x"}, self.TODAY)
        self.assertEqual((tampered.month, tampered.employee_id), (11, None))


class SettingsAndShellTests(AskCase):
    def test_settings_by_the_owner_or_admin_only(self):
        self.client.force_login(self.hr)
        self.assertEqual(self.client.get(reverse("ask:settings")).status_code, 403)
        self.client.force_login(self.admin)
        page = self.client.get(reverse("ask:settings"))
        self.assertContains(page, "salaries and attendance are not")
        response = self.client.post(reverse("ask:settings"), {
            "ask_enabled": "on", "ask_consent_received": "", "ask_model": "claude-sonnet-5-5",
            "daily_cost_cap": "5.00"})
        self.assertContains(response, "Record the client&#x27;s written consent first.")
        response = self.client.post(reverse("ask:settings"), {
            "ask_enabled": "on", "ask_consent_received": "on", "ask_consent_date": "2026-11-01",
            "ask_model": "claude-sonnet-5-5", "daily_cost_cap": "5.00"})
        self.assertRedirects(response, reverse("ask:settings"))
        self.assertEqual(AiSettings.all_objects.get(company=self.company).ask_model, "claude-sonnet-5-5")

    def test_the_company_s_model_choice_is_used(self):
        AiSettings.all_objects.filter(pk=self.settings.pk).update(ask_model="claude-sonnet-5-5")
        self.ask(self.admin, "late today", answer("attendance_days", status="late", date_from=DAY))
        self.assertEqual(self.requests[0]["model"], "claude-sonnet-5-5")

    def test_the_top_bar(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("employee_list"))
        self.assertContains(page, f'data-ask-url="{reverse("ask:ask")}"')
        self.assertContains(page, reverse("ask:settings"))     # the menu entry
        self.client.force_login(self.manager)
        page = self.client.get(reverse("me:home"))
        self.assertContains(page, "data-ask-only")
        self.client.force_login(self.clerk_user)
        page = self.client.get(reverse("me:home"))
        self.assertNotContains(page, "data-ask-only")

    def test_the_order_assistant_stays_off_while_ask_is_on(self):
        with self.assertRaises(claude.AiUnavailable):
            claude.check_available(claude.settings_for(self.company.pk))
        claude.check_available(claude.settings_for(self.company.pk), AiCall.Kind.ASK)

