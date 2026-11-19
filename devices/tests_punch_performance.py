from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from devices.tests_server_address import ServerAddressTestCase
from devices.models import DeviceMessage, PunchEvent
from employees.models import Employee


class PunchPerformanceTests(ServerAddressTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        now = timezone.now()
        employee = Employee.all_objects.create(
            company=self.company, first_name="First", middle_name="Middle", last_name="Last",
        )
        self.message = DeviceMessage.all_objects.create(
            company=self.company, device=self.device, branch=self.branch,
            message_type=DeviceMessage.MessageType.PUNCH_BATCH, received_at=now,
            raw_payload_text="evidence-must-stay" * 1000,
        )
        self.punches = []
        for index in range(2):
            at = now + timedelta(seconds=index)
            self.punches.append(PunchEvent.all_objects.create(
                company=self.company, device=self.device, branch=self.branch,
                device_message=self.message, employee=employee, device_user_id="100", source_record_index=index,
                punched_at_device_raw=at.isoformat(), punched_at_device=at, punched_at_utc=at,
                received_at=now, raw_record={"evidence": "retained"},
                authorization_snapshot={"evidence": "retained"},
                dedupe_status="confirmed_duplicate" if index else "unique",
                duplicate_of=self.punches[0] if index else None,
            ))

    def test_list_preserves_repeat_labels_order_and_does_not_fetch_payloads(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("devices:punch_list"))
        self.assertContains(response, "Repeat")
        self.assertContains(response, "First Middle Last")
        with self.assertNumQueries(0):
            for punch in response.context["page"].object_list:
                self.assertEqual(punch.employee.full_name, "First Middle Last")
        self.assertEqual([p.pk for p in response.context["page"].object_list],
                         [p.pk for p in reversed(self.punches)])
        selects = [q["sql"] for q in queries if 'FROM "devices_punchevent"' in q["sql"]]
        self.assertTrue(selects)
        for sql in selects:
            self.assertNotIn('"raw_payload_text"', sql)
            self.assertNotIn('"raw_record"', sql)
            self.assertNotIn('"authorization_snapshot"', sql)
        # Details retain complete evidence; no schema/data reduction.
        response = self.client.get(reverse("devices:punch_detail", args=[self.punches[1].pk]))
        self.assertEqual(response.context["punch"].raw_record, {"evidence": "retained"})
        self.assertEqual(response.context["punch"].device_message.raw_payload_text,
                         self.message.raw_payload_text)

    def test_storage_report_is_read_only(self):
        output = StringIO()
        before = list(PunchEvent.all_objects.values())
        with CaptureQueriesContext(connection) as queries:
            call_command("punch_storage_health", stdout=output)
        self.assertEqual(before, list(PunchEvent.all_objects.values()))
        self.assertIn("No punch records deleted", output.getvalue())
        self.assertFalse(any("VACUUM" in q["sql"] for q in queries))
