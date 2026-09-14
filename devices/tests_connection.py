"""Is the device connected, and the connection test (plan step N8).

A device cannot be pinged; it calls in. So every reading here is built from
``last_seen_at`` and the device's push interval, and a test passes only when a
check-in arrives after it started.
"""

import datetime

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import CompanyMembership, User
from auditlog.models import AuditLog
from common.tenant import use_company
from devices import tests_server_address as base
from devices.models import BiometricDevice, DeviceMessage
from devices.services import connection
from devices.services.commands import queue_command, take_pending_commands
from tenants.models import Company

NOW = datetime.datetime(2026, 9, 14, 10, 0, tzinfo=datetime.timezone.utc)


class ConnectionTestCase(TestCase):
    # One company, one active device ("Main Entrance", serial SN-A, a 10-second
    # push interval) and a company administrator. Borrowed through the module
    # so ServerAddressTestCase is not collected twice.
    setUp_device = base.ServerAddressTestCase.setUp

    def setUp(self):
        self.setUp_device()

    def seen(self, seconds_ago=None, *, device=None, now=NOW, **fields):
        device = device or self.device
        last_seen = None if seconds_ago is None else now - datetime.timedelta(seconds=seconds_ago)
        BiometricDevice.all_objects.filter(pk=device.pk).update(last_seen_at=last_seen, **fields)
        device.refresh_from_db()
        return device

    def state(self, seconds_ago=None, **fields):
        return connection.connection_of(self.seen(seconds_ago, **fields), NOW)


class ReadingTests(ConnectionTestCase):
    def test_never_seen_is_not_connected(self):
        state = self.state(None)
        self.assertEqual((state.key, state.label, state.detail),
                         ("not_connected", "Not connected", "Never checked in"))

    def test_seen_within_a_few_polls_is_connected(self):
        state = self.state(30)
        self.assertEqual((state.key, state.tone), ("connected", "success"))
        self.assertEqual(state.detail, "Checked in 30 s ago")

    def test_a_short_silence_reads_as_last_seen(self):
        state = self.state(5 * 60)
        self.assertEqual((state.key, state.label, state.tone),
                         ("late", "Last seen 5 min ago", "warning"))

    def test_a_long_silence_is_not_connected(self):
        state = self.state(20 * 60)
        self.assertEqual((state.key, state.detail), ("not_connected", "Last seen 20 min ago"))

    def test_the_push_interval_widens_the_window(self):
        """A device set to call every minute is not late after two."""
        self.assertEqual(self.state(5 * 60, settings={"push_interval_seconds": 60}).key,
                         "connected")

    def test_the_window_is_never_tighter_than_two_minutes(self):
        """Real polling drifts from the setting: 10 s configured, 20 s in practice."""
        self.assertEqual(self.state(100, settings={"push_interval_seconds": 5}).key,
                         "connected")

    def test_retired_and_suspended_devices_are_not_judged(self):
        self.assertEqual(self.state(None, status="retired").key, "retired")
        self.assertEqual(self.state(None, status="suspended").key, "suspended")

    def test_a_duration_the_way_a_person_says_it(self):
        for seconds, text in ((3, "just now"), (45, "45 s ago"), (130, "2 min ago"),
                              (7300, "2 h ago"), (86400, "1 day ago"), (3 * 86400, "3 days ago")):
            with self.subTest(seconds=seconds):
                self.assertEqual(connection.ago(seconds), text)


class StoppedDeviceTests(ConnectionTestCase):
    def make(self, name, serial, status, seconds_ago):
        with use_company(self.company):
            device = BiometricDevice.objects.create(
                branch=self.branch, device_model=self.device.device_model,
                name=name, serial_number=serial, timezone="Asia/Dhaka", status=status,
            )
        return self.seen(seconds_ago, device=device)

    def test_only_active_devices_that_went_quiet_are_listed(self):
        self.seen(20 * 60)                                   # active, quiet
        self.make("Back door", "SN-B", "active", 30)         # active, fine
        self.make("New one", "SN-C", "pending", None)        # being set up
        self.make("Old one", "SN-D", "retired", 99999)       # retired
        self.make("Never", "SN-E", "active", None)           # active, never
        stopped = connection.stopped_devices(self.company.pk, now=NOW)
        self.assertEqual(sorted(d.name for d, _ in stopped), ["Main Entrance", "Never"])


class TestStatusTests(ConnectionTestCase):
    def test_a_check_in_after_the_start_passes(self):
        device = self.seen(0)
        result = connection.test_status(
            device, since=NOW - datetime.timedelta(seconds=15), now=NOW,
        )
        self.assertTrue(result.checked_in)
        self.assertFalse(result.gave_up)

    def test_a_check_in_before_the_start_does_not_count(self):
        device = self.seen(60)
        result = connection.test_status(
            device, since=NOW - datetime.timedelta(seconds=20), now=NOW,
        )
        self.assertFalse(result.checked_in)
        self.assertFalse(result.gave_up)

    def test_after_the_wait_it_gives_advice_but_keeps_listening(self):
        device = self.seen(None)
        result = connection.test_status(
            device, since=NOW - datetime.timedelta(minutes=3), now=NOW,
        )
        self.assertTrue(result.gave_up)
        self.assertFalse(result.checked_in)

    def test_a_test_command_is_followed_to_its_answer(self):
        entry = queue_command(device=self.device, command_key=connection.TEST_COMMAND)
        since = timezone.now() - datetime.timedelta(seconds=1)
        self.assertEqual(connection.command_progress(self.device, entry["id"], since)["stage"],
                         "queued")
        take_pending_commands(self.device)
        self.assertEqual(connection.command_progress(self.device, entry["id"], since)["stage"],
                         "picked_up")
        with use_company(self.company):
            DeviceMessage.objects.create(
                device=self.device, branch=self.branch,
                message_type=DeviceMessage.MessageType.COMMAND_RESULT,
                received_at=timezone.now(), payload_hash="ph-cmd",
                raw_payload_text=f"ID=99&Return=0&CMD=DATA\nID={entry['id']}&Return=0&CMD=DATA\n",
            )
        progress = connection.command_progress(self.device, entry["id"], since)
        self.assertEqual((progress["stage"], progress["ok"]), ("answered", True))

    def test_an_error_answer_says_its_code(self):
        entry = queue_command(device=self.device, command_key=connection.TEST_COMMAND)
        since = timezone.now() - datetime.timedelta(seconds=1)
        take_pending_commands(self.device)
        with use_company(self.company):
            DeviceMessage.objects.create(
                device=self.device, branch=self.branch,
                message_type=DeviceMessage.MessageType.COMMAND_RESULT,
                received_at=timezone.now(), payload_hash="ph-cmd-bad",
                raw_payload_text=f"ID={entry['id']}&Return=-1002&CMD=DATA\n",
            )
        progress = connection.command_progress(self.device, entry["id"], since)
        self.assertFalse(progress["ok"])
        self.assertIn("-1002", progress["text"])


class PageTests(ConnectionTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        self.detail = reverse("devices:device_detail", args=[self.device.public_id])
        self.test_url = reverse("devices:device_connection_test", args=[self.device.public_id])

    def test_the_device_page_has_the_connection_panel(self):
        self.seen(0, now=timezone.now())
        response = self.client.get(self.detail)
        self.assertContains(response, 'id="connection"')
        self.assertContains(response, "Connected")
        self.assertContains(response, "Test connection")

    def test_starting_a_test_carries_its_start_time(self):
        response = self.client.post(self.test_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("?test=", response["Location"])
        self.assertNotIn("+", response["Location"])  # "+" would read back as a space
        self.assertTrue(response["Location"].endswith("#connection"))
        page = self.client.get(response["Location"].split("#")[0])
        self.assertContains(page, "Waiting for the device to check in")

    def test_a_test_with_a_command_queues_a_harmless_one(self):
        from devices.services.commands import pending_summary

        response = self.client.post(self.test_url, {"with_command": "1"})
        [entry] = pending_summary(self.device)
        self.assertEqual(entry["key"], "query_options")
        self.assertIn(f"&command={entry['id']}", response["Location"])
        self.assertTrue(AuditLog.objects.filter(action="device.command_queued").exists())

    def test_the_test_status_json_says_what_to_check_when_nothing_arrives(self):
        self.seen(None)
        since = (timezone.now() - datetime.timedelta(minutes=5)).isoformat()
        data = self.client.get(self.test_url, {"since": since}).json()
        self.assertTrue(data["gave_up"])
        titles = [item["title"] for item in data["advice"]]
        self.assertIn("The serial number", titles)
        self.assertTrue(any("SN-A" in item["text"] for item in data["advice"]))

    def test_the_test_status_json_passes_on_a_check_in(self):
        since = (timezone.now() - datetime.timedelta(seconds=30)).isoformat()
        self.seen(0, now=timezone.now())
        data = self.client.get(self.test_url, {"since": since}).json()
        self.assertTrue(data["checked_in"])
        self.assertEqual(data["advice"], [])

    @override_settings(ALLOWED_HOSTS=base.ALLOWED)
    def test_a_real_check_in_passes_the_test(self):
        """End to end: the device's own poll is what passes it."""
        since = (timezone.now() - datetime.timedelta(seconds=1)).isoformat()
        response = self.client.get(
            "/iclock/getrequest", {"SN": self.device.serial_number},
            HTTP_HOST=base.OLD_HOST, HTTP_X_FORWARDED_PROTO="https",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.client.get(self.test_url, {"since": since}).json()["checked_in"])

    def test_registering_and_editing_start_a_test(self):
        payload = {
            "name": "Back Door", "serial_number": "SN-NEW-1", "branch": self.branch.pk,
            "device_model": self.device.device_model_id, "external_device_id": "",
            "timezone": "Asia/Dhaka", "installed_at": "", "status": "pending",
            "comm_key": "", "push_interval_seconds": 10, "error_delay_seconds": 30,
            "realtime": "on",
        }
        response = self.client.post(reverse("devices:device_register"), payload)
        self.assertIn("?test=", response["Location"])
        created = BiometricDevice.all_objects.get(serial_number="SN-NEW-1")
        response = self.client.post(
            reverse("devices:device_edit", args=[created.public_id]),
            {**payload, "name": "Back Door 2", "server_address": ""},
        )
        self.assertIn("?test=", response.get("Location", ""))

    def test_a_retired_device_is_not_tested(self):
        self.seen(None, status="retired")
        self.client.post(self.test_url)
        self.assertNotContains(self.client.get(self.detail), "Test connection")

    def test_the_badges_json_answers_only_for_this_company(self):
        other = Company.objects.create(code="B", slug="b", name="Company B")
        with use_company(other):
            from organization.models import Branch

            branch = Branch.objects.create(code="HQ", name="HQ", is_default=True)
            foreign = BiometricDevice.objects.create(
                branch=branch, device_model=self.device.device_model,
                name="Theirs", serial_number="SN-X", timezone="Asia/Dhaka",
            )
        data = self.client.get(reverse("devices:device_connections"), {
            "devices": f"{self.device.public_id},{foreign.public_id},not-a-uuid",
        }).json()
        self.assertEqual(list(data["devices"]), [str(self.device.public_id)])

    def test_the_list_shows_badges_and_the_stopped_device_alert(self):
        self.seen(3 * 3600, now=timezone.now())
        response = self.client.get(reverse("devices:device_list"))
        self.assertContains(response, "Not connected")
        self.assertContains(response, "1 device has stopped checking in.")
        self.assertContains(response, f'data-connection-for="{self.device.public_id}"')

    def test_the_dashboard_warns_the_people_who_manage_devices(self):
        self.seen(3 * 3600, now=timezone.now())
        self.assertContains(self.client.get(reverse("dashboard")),
                            "has stopped checking in")
        hr = User.objects.create_user(email="hr@example.test", password="pw-12345678")
        CompanyMembership.all_objects.create(
            company=self.company, user=hr, role="hr", status="active",
        )
        self.client.force_login(hr)
        response = self.client.get(reverse("dashboard"), follow=True)
        # HR may be refused the dashboard altogether; either way, no alert.
        self.assertNotContains(response, "has stopped checking in",
                               status_code=response.status_code)

    def test_nobody_signed_out_reaches_the_pages(self):
        self.client.logout()
        for url in (reverse("devices:device_register"), self.test_url,
                    reverse("devices:device_connections")):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn("login", response["Location"])
