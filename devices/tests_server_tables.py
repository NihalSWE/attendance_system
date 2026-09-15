"""The device lists on the shared server-side table (plan step N9).

Devices, enrollments, messages, punches and the unresolved queue page, search
and sort in SQL through base_template/tables.py. A device's user roster is not
a database table — it is rebuilt from the uploads the device sent — so it pages,
searches and sorts on the server over the whole roster, with the same contract.
"""

import datetime

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import CompanyMembership, User
from common.tenant import use_company
from devices import tests_server_address as base
from devices.models import BiometricDevice, DeviceEnrollment, DeviceMessage, PunchEvent
from employees.models import Employee
from tenants.models import Company

LISTS = {
    "devices:device_list": 7,
    "devices:enrollment_list": 7,
    "devices:message_list": 6,
    "devices:punch_list": 7,
    "devices:unresolved_queue": 6,
}


class DeviceTablesTestCase(TestCase):
    # One company with an administrator and an active device, "Main Entrance".
    # Borrowed through the module so ServerAddressTestCase is not collected twice.
    setUp_device = base.ServerAddressTestCase.setUp

    def setUp(self):
        self.setUp_device()
        self.client.force_login(self.admin)
        now = timezone.now()
        with use_company(self.company):
            self.message = DeviceMessage.objects.create(
                device=self.device, branch=self.branch,
                message_type=DeviceMessage.MessageType.PUNCH_BATCH,
                received_at=now, raw_payload_text="batch", payload_hash="ph-batch",
                record_count=30,
            )
            employees = Employee.objects.bulk_create([
                Employee(company=self.company, first_name=f"Person {i:02}") for i in range(12)
            ])
            for index, employee in enumerate(employees):
                DeviceEnrollment.objects.create(
                    device=self.device, employee=employee, device_user_id=str(100 + index),
                    effective_from=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
                )
            PunchEvent.objects.bulk_create([
                PunchEvent(
                    company=self.company, device_message=self.message, device=self.device,
                    branch=self.branch, device_user_id=str(900 + i), source_record_index=i,
                    punched_at_device_raw="2026-09-01 09:00:00",
                    punched_at_device=now - datetime.timedelta(minutes=i),
                    punched_at_utc=now - datetime.timedelta(minutes=i), received_at=now,
                    raw_record={},
                    authorization_status=PunchEvent.AuthorizationStatus.UNKNOWN_EMPLOYEE,
                )
                for i in range(30)
            ])

    def draw(self, name, args=None, **params):
        response = self.client.get(reverse(name, args=args), {
            "table": "1", "draw": "5", "start": "0", "length": "10", **params,
        })
        self.assertEqual(response.status_code, 200, name)
        self.assertEqual(response["Content-Type"], "application/json")
        data = response.json()
        self.assertEqual(data["draw"], 5)
        self.assertLessEqual(len(data["data"]), 10)
        return data


class DeviceListTests(DeviceTablesTestCase):
    def test_every_list_answers_draws_and_sorts_every_orderable_column(self):
        for name, columns in LISTS.items():
            with self.subTest(list=name):
                data = self.draw(name)
                self.assertGreater(data["recordsTotal"], 0)
                self.assertEqual(len(data["data"][0]) - 2, columns)
                for column in range(columns):
                    self.draw(name, **{"order[0][column]": column, "order[0][dir]": "desc"})
                empty = self.draw(name, **{"search[value]": "NeverMatchesAnything"})
                self.assertEqual((empty["recordsFiltered"], empty["data"]), (0, []))

    def test_punch_search_and_count_cover_every_page(self):
        data = self.draw("devices:punch_list", **{"search[value]": "905", "start": "0"})
        self.assertEqual((data["recordsTotal"], data["recordsFiltered"]), (30, 1))
        self.assertIn("905", data["data"][0]["1"])
        data = self.draw("devices:punch_list", start="20")
        self.assertEqual(len(data["data"]), 10)

    def test_the_page_filters_still_narrow_the_set_first(self):
        data = self.draw("devices:punch_list", authorization="authorized")
        self.assertEqual(data["recordsTotal"], 0)
        data = self.draw("devices:unresolved_queue", reason="unknown_employee")
        self.assertEqual(data["recordsTotal"], 30)

    def test_enrollments_search_by_employee_name(self):
        data = self.draw("devices:enrollment_list", **{"search[value]": "Person 07"})
        self.assertEqual(data["recordsFiltered"], 1)
        self.assertEqual(data["recordsTotal"], 12)

    def test_another_companys_rows_are_never_counted(self):
        other = Company.objects.create(code="B", slug="b", name="Company B")
        outsider = User.objects.create_user(email="admin@b.test", password="pw-12345678")
        CompanyMembership.all_objects.create(
            company=other, user=outsider, role="company_admin", status="active",
        )
        self.client.force_login(outsider)
        for name in LISTS:
            with self.subTest(list=name):
                data = self.draw(name)
                self.assertEqual(data["recordsTotal"], 0)
                self.assertNotIn("Main Entrance", str(data))

    def test_html_pages_have_the_counted_pager_and_no_second_datatables_script(self):
        for name in LISTS:
            with self.subTest(list=name):
                response = self.client.get(reverse(name), {"per_page": "10"})
                self.assertContains(response, "data-server-table")
                self.assertContains(response, "dataTables.min.js", count=1)
                self.assertNotContains(response, "data-enhance")
                self.assertNotContains(response, 'class="sr-only">\n                                    Actions')
        punches = self.client.get(reverse("devices:punch_list"), {"per_page": "10"})
        self.assertContains(punches, 'aria-label="Result pages"')

    def test_the_headers_stay_when_a_search_finds_nothing(self):
        response = self.client.get(reverse("devices:punch_list"), {"table_q": "NeverMatchesAnything"})
        self.assertContains(response, "data-server-table")
        self.assertContains(response, "Punched (device time)")


class DeviceUsersTableTests(DeviceTablesTestCase):
    def setUp(self):
        super().setUp()
        rows = "\n".join(
            f"user uid={i}\tpin={i}\tname=Worker {i:02}\tprivilege=0\tcardno=0\tpassword=\tdisable=0"
            for i in range(1, 13)
        )
        with use_company(self.company):
            DeviceMessage.objects.create(
                device=self.device, branch=self.branch,
                message_type=DeviceMessage.MessageType.ENROLLMENT_RESULT,
                received_at=timezone.now(), raw_payload_text=rows, payload_hash="ph-users",
            )
        self.args = [self.device.public_id]

    def test_the_whole_roster_is_counted_and_one_page_is_sent(self):
        data = self.draw("devices:device_users", self.args)
        self.assertEqual((data["recordsTotal"], data["recordsFiltered"]), (12, 12))
        self.assertEqual(len(data["data"]), 10)
        self.assertEqual(len(data["data"][0]) - 2, 11)

    def test_search_covers_the_rows_not_on_screen(self):
        data = self.draw("devices:device_users", self.args, **{"search[value]": "Worker 12"})
        self.assertEqual(data["recordsFiltered"], 1)
        self.assertIn("Worker 12", data["data"][0]["1"])

    def test_user_ids_sort_as_numbers(self):
        rows = self.draw("devices:device_users", self.args,
                         **{"order[0][column]": 0, "order[0][dir]": "desc"})["data"]
        self.assertIn("12", rows[0]["0"])
        self.assertIn("11", rows[1]["0"])

    def test_every_orderable_column_sorts_and_paging_past_the_end_is_empty(self):
        for column in range(11):
            with self.subTest(column=column):
                self.draw("devices:device_users", self.args,
                          **{"order[0][column]": column, "order[0][dir]": "asc"})
        data = self.draw("devices:device_users", self.args, start="1000", length="-1")
        self.assertEqual(data["data"], [])

    def test_the_html_page_pages_the_roster(self):
        response = self.client.get(reverse("devices:device_users", args=self.args),
                                   {"per_page": "10"})
        self.assertContains(response, 'aria-label="Result pages"')
        self.assertContains(response, "12 users")
