"""Holiday year calendar: many holidays at once, each with its own name."""

from datetime import date

from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from auditlog.models import AuditLog
from common.tenant import use_company
from scheduling import services
from scheduling.models import Holiday
from scheduling.tests_screens import CalendarBase

URL = "scheduling:holiday_year"


class AddHolidaysServiceTests(CalendarBase):
    def add(self, days, branch=None, is_paid=True, actor=None):
        return services.add_holidays(
            actor=actor or self.admin, company_id=self.company.pk,
            values={"days": days, "branch": branch, "is_paid": is_paid},
        )

    def test_adds_every_date_with_its_own_name(self):
        added = self.add([
            (date(2026, 3, 26), "Independence Day"),
            (date(2026, 12, 16), "Victory Day"),
        ])
        self.assertEqual(len(added), 2)
        with use_company(self.company):
            self.assertEqual(
                list(Holiday.objects.order_by("holiday_date").values_list("name", flat=True)),
                ["Independence Day", "Victory Day"],
            )
        self.assertEqual(
            AuditLog.objects.filter(company=self.company, action="holiday.created").count(), 2
        )

    def test_one_clash_adds_nothing_and_names_it(self):
        self.add([(date(2026, 12, 16), "Victory Day")])
        with self.assertRaises(ValidationError) as caught:
            self.add([
                (date(2026, 3, 26), "Independence Day"),
                (date(2026, 12, 16), "Victory Day again"),
            ])
        self.assertIn("16 Dec 2026 (Victory Day)", str(caught.exception))
        with use_company(self.company):
            self.assertEqual(Holiday.objects.count(), 1)

    def test_every_date_needs_a_name(self):
        with self.assertRaises(ValidationError) as caught:
            self.add([(date(2026, 3, 26), "Independence Day"), (date(2026, 4, 14), " ")])
        self.assertIn("14 Apr 2026", str(caught.exception))

    def test_nothing_selected_is_refused(self):
        with self.assertRaises(ValidationError):
            self.add([])

    def test_a_branch_holiday_does_not_clash_with_a_company_one(self):
        self.add([(date(2026, 5, 1), "May Day")])
        self.add([(date(2026, 5, 1), "Branch day")], branch=self.hq)
        with use_company(self.company):
            self.assertEqual(Holiday.objects.filter(holiday_date=date(2026, 5, 1)).count(), 2)

    def test_hr_cannot_add_holidays(self):
        with self.assertRaises(PermissionDenied):
            self.add([(date(2026, 3, 26), "Independence Day")], actor=self.hr)


class HolidayYearPageTests(CalendarBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def test_the_year_shows_twelve_months_of_real_days(self):
        response = self.client.get(reverse(URL), {"year": 2026})
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertEqual(body.count('class="yc-month"'), 12)
        self.assertEqual(body.count('class="yc-day__input"'), 365)
        self.assertNotIn('type="date"', body)

    def test_a_leap_year_has_366_days(self):
        response = self.client.get(reverse(URL), {"year": 2028})
        self.assertEqual(response.content.decode().count('class="yc-day__input"'), 366)

    def test_existing_holidays_and_weekly_offs_are_marked(self):
        services.create_holiday(
            actor=self.admin, company_id=self.company.pk,
            values={"holiday_date": date(2026, 12, 16), "name": "Victory Day",
                    "branch": None, "is_paid": True, "description": ""},
        )
        services.add_weekly_offs(
            actor=self.admin, company_id=self.company.pk,
            values={"weekdays": [4], "branch": None, "is_paid": True,
                    "effective_from": date(2026, 1, 1)},
        )
        body = self.client.get(reverse(URL), {"year": 2026}).content.decode()
        self.assertIn("Victory Day", body)
        # A company-wide holiday cannot be selected again.
        self.assertRegex(body, r'value="2026-12-16"[^>]*disabled')
        self.assertNotRegex(body, r'value="2026-12-15"[^>]*disabled')
        # 52 Fridays in 2026 are weekly offs.
        self.assertEqual(body.count("yc-day yc-day--off"), 52)

    def test_saving_adds_every_row_and_returns_to_the_same_year(self):
        response = self.client.post(reverse(URL), {
            "year": "2026", "save": "1", "is_paid": "on",
            "date": ["2026-03-26", "2026-04-14", "2026-04-15"],
            "name": ["Independence Day", "Bengali New Year", "Bengali New Year"],
        })
        self.assertRedirects(response, f"{reverse(URL)}?year=2026")
        with use_company(self.company):
            self.assertEqual(Holiday.objects.filter(is_paid=True).count(), 3)

    def test_a_clash_keeps_every_selected_row_on_the_page(self):
        services.create_holiday(
            actor=self.admin, company_id=self.company.pk,
            values={"holiday_date": date(2026, 12, 16), "name": "Victory Day",
                    "branch": None, "is_paid": True, "description": ""},
        )
        response = self.client.post(reverse(URL), {
            "year": "2026", "save": "1", "is_paid": "on",
            "date": ["2026-03-26", "2026-12-16"],
            "name": ["Independence Day", "Again"],
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Already a holiday for all branches")
        self.assertContains(response, 'value="Independence Day"')
        self.assertContains(response, 'value="Again"')

    def test_changing_year_keeps_the_selection_without_saving(self):
        response = self.client.post(reverse(URL), {
            "year": "2026", "go_year": "2027",
            "date": ["2026-12-16"], "name": ["Victory Day"],
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["year"], 2027)
        self.assertContains(response, 'value="Victory Day"')
        with use_company(self.company):
            self.assertFalse(Holiday.objects.exists())

    def test_selected_days_are_ticked_when_the_page_comes_back(self):
        response = self.client.post(reverse(URL), {
            "year": "2026", "go_year": "2026",
            "date": ["2026-03-26"], "name": ["Independence Day"],
        })
        body = response.content.decode()
        self.assertRegex(body, r'value="2026-03-26"[^>]*checked')
        self.assertNotRegex(body, r'value="2026-03-27"[^>]*checked')

    def test_a_date_that_cannot_be_read_is_an_error_not_a_crash(self):
        response = self.client.post(reverse(URL), {
            "year": "2026", "save": "1", "date": ["2026-02-30"], "name": ["Nope"],
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "could not be read")

    def test_hr_cannot_open_the_calendar(self):
        self.client.force_login(self.hr)
        self.assertEqual(self.client.get(reverse(URL)).status_code, 403)

    def test_the_holiday_list_links_to_the_calendar(self):
        response = self.client.get(reverse("scheduling:holiday_list"))
        self.assertContains(response, reverse(URL))
