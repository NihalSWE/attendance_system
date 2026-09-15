"""Changing a device's server address from the software.

The thing under test is not really "does the setting save". It is: can the
software be trusted with a write that, done wrong, puts the hardware out of
reach of every screen we have? So most of what is asserted here is about
restraint — what does *not* happen when the address is bad, and whether the
page tells the truth about whether the terminal was touched.

The device side is the simulator's job (see ``devices/simulator/``); the device
requests here are made with the test client against the real ``/iclock/``
endpoints, so the host a request arrives on is genuinely the thing being read.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import CompanyMembership
from auditlog.models import AuditLog
from common.tenant import use_company
from devices.models import (
    BiometricDevice,
    DeviceModel,
    DeviceServerAddressChange,
    DeviceVendor,
)
from devices.services import server_address
from devices.services.commands import pending_summary, take_pending_commands
from organization.models import Branch
from tenants.models import Company

User = get_user_model()

OLD_HOST = "old.example.test"
NEW_HOST = "new.example.test"

ALLOWED = ["testserver", OLD_HOST, NEW_HOST, "192.168.1.20"]


def _reference_model():
    vendor = DeviceVendor.objects.get_or_create(
        code="zkteco",
        defaults={"name": "ZKTeco", "adapter_key": "zkteco_adms_push"},
    )[0]
    return DeviceModel.objects.get_or_create(
        vendor=vendor,
        model_code="senseface-2a",
        defaults={
            "name": "SenseFace 2A",
            "protocol": DeviceModel.Protocol.ADMS_PUSH,
        },
    )[0]


class ServerAddressTestCase(TestCase):
    """One company, one active device already proven at ``OLD_HOST``."""

    def setUp(self):
        model = _reference_model()
        self.company = Company.objects.create(code="A", slug="a", name="Company A")
        self.admin = User.objects.create_user(
            email="admin@example.test", password="pw-12345678"
        )
        CompanyMembership.all_objects.create(
            company=self.company, user=self.admin, role="company_admin",
            status="active",
        )
        with use_company(self.company):
            self.branch = Branch.objects.create(code="HQ", name="HQ", is_default=True)
            self.device = BiometricDevice.objects.create(
                branch=self.branch,
                device_model=model,
                name="Main Entrance",
                serial_number="SN-A",
                timezone="Asia/Dhaka",
                status=BiometricDevice.Status.ACTIVE,
                settings={"push_interval_seconds": 10},
                server_scheme="https",
                server_host=OLD_HOST,
                server_port=443,
            )
        self.client = Client()

    # -- helpers ----------------------------------------------------------

    def _ok_probe(self):
        """A probe that behaves exactly like the real endpoint answering."""

        def fetch(url):
            token = url.split("token=")[1]
            return 200, server_address.probe_body(token)

        return fetch

    def _failing_probe(self, exc=None, status=200, body="hello"):
        def fetch(url):
            if exc is not None:
                raise exc
            return status, body

        return fetch

    def _request_change(self, host=NEW_HOST, fetch=None):
        with use_company(self.company):
            return server_address.request_change(
                device=self.device,
                actor=self.admin,
                raw_address=f"https://{host}",
                fetch=fetch if fetch is not None else self._ok_probe(),
            )

    def _device_request(self, host, path="/iclock/getrequest", scheme="https", **extra):
        """A device check-in arriving at ``host``.

        X-Forwarded-Proto because that is the real shape: a tunnel or load
        balancer terminates TLS and forwards plain HTTP, so the scheme the
        device actually dialled only survives in that header. Getting this
        wrong is precisely the bug that would read an https device as http and
        never confirm.
        """
        extra.setdefault("HTTP_X_FORWARDED_PROTO", scheme)
        return self.client.get(
            path, {"SN": self.device.serial_number}, HTTP_HOST=host, **extra
        )


# ---------------------------------------------------------------- parsing


class AddressParsingTests(ServerAddressTestCase):
    def test_a_bare_hostname_defaults_to_https_on_443(self):
        address = server_address.parse_address("attendance.example.com")
        self.assertEqual(address.scheme, "https")
        self.assertEqual(address.port, 443)
        self.assertEqual(address.text, "https://attendance.example.com")

    def test_an_explicit_port_is_kept_and_shown(self):
        address = server_address.parse_address("http://192.168.1.20:8000")
        self.assertEqual(
            (address.scheme, address.host, address.port),
            ("http", "192.168.1.20", 8000),
        )
        self.assertEqual(address.text, "http://192.168.1.20:8000")

    def test_a_lan_address_is_allowed(self):
        """The normal shape of this product: device and server on one LAN."""
        address = server_address.parse_address("192.168.1.20:8000")
        self.assertEqual(address.host, "192.168.1.20")

    def test_link_local_is_refused(self):
        """169.254.169.254 is cloud instance metadata, never a device server."""
        with self.assertRaises(server_address.ServerAddressError):
            server_address.parse_address("http://169.254.169.254")

    def test_a_path_is_refused_because_the_device_builds_it(self):
        with self.assertRaises(server_address.ServerAddressError) as ctx:
            server_address.parse_address("https://host.example/iclock/cdata")
        self.assertIn("device builds", str(ctx.exception))

    def test_other_schemes_are_refused(self):
        for raw in ("ftp://host.example", "file:///etc/passwd", "gopher://x"):
            with self.subTest(raw=raw):
                with self.assertRaises(server_address.ServerAddressError):
                    server_address.parse_address(raw)

    def test_credentials_in_the_address_are_refused(self):
        with self.assertRaises(server_address.ServerAddressError):
            server_address.parse_address("https://user:pw@host.example")

    def test_blank_and_whitespace_are_refused(self):
        for raw in ("", "   ", "host name.example"):
            with self.subTest(raw=raw):
                with self.assertRaises(server_address.ServerAddressError):
                    server_address.parse_address(raw)

    def test_matching_compares_scheme_host_and_port(self):
        a = server_address.parse_address("https://Host.Example")
        self.assertTrue(a.matches(server_address.parse_address("https://host.example")))
        self.assertFalse(a.matches(server_address.parse_address("http://host.example")))
        self.assertFalse(
            a.matches(server_address.parse_address("https://host.example:8443"))
        )


# ------------------------------------------------------- step 1: the probe


@override_settings(ALLOWED_HOSTS=ALLOWED)
class ProbeTests(ServerAddressTestCase):
    def test_a_failed_probe_never_touches_the_device(self):
        import urllib.error

        attempt = self._request_change(
            fetch=self._failing_probe(
                exc=urllib.error.URLError("no route to host")
            )
        )
        self.assertIn("Could not connect", attempt.failure_reason)
        self.assertEqual(attempt.status, DeviceServerAddressChange.Status.UNREACHABLE)
        self.assertEqual(pending_summary(self.device), [])
        self.assertIsNone(attempt.command_queued_at)
        self.assertFalse(attempt.was_sent_to_device)
        # The clean revert: the saved address is exactly what it was.
        self.device.refresh_from_db()
        self.assertEqual(self.device.server_host, OLD_HOST)

    def test_a_server_that_is_not_us_fails_the_check(self):
        """Reachable is not enough — it has to be *this* software.

        A device pointed at somebody else's working server is just as lost as
        one pointed at nothing, so a 200 with the wrong body must not pass.
        """
        attempt = self._request_change(
            fetch=self._failing_probe(status=200, body="<html>Welcome to nginx</html>")
        )
        self.assertEqual(attempt.status, DeviceServerAddressChange.Status.UNREACHABLE)
        self.assertIn("not this attendance server", attempt.failure_reason)
        self.assertEqual(pending_summary(self.device), [])

    def test_a_400_is_explained_as_an_allowed_hosts_problem(self):
        """The failure mode that would otherwise silently lose a device."""
        import urllib.error

        attempt = self._request_change(
            fetch=self._failing_probe(
                exc=urllib.error.HTTPError(
                    "http://x", 400, "Bad Request", {}, None
                )
            )
        )
        self.assertEqual(attempt.status, DeviceServerAddressChange.Status.UNREACHABLE)
        self.assertIn("ALLOWED_HOSTS", attempt.failure_reason)

    def test_a_check_in_during_the_probe_confirms_nothing(self):
        """The device polls every few seconds, so it lands inside step 1.

        At that point it has been told nothing, so a request arriving at the
        target address is not evidence of anything — it is simply where the
        device already was. Acting on it saved an address the device was never
        asked to move to, and flipped the attempt out of ``checking`` while the
        probe was still in flight, so the probe endpoint stopped recognising
        its own token. Both were seen on real hardware.
        """
        seen = {}

        def fetch(url):
            # A device check-in lands mid-probe, at the address being tested.
            self._device_request(NEW_HOST)
            seen["attempt"] = DeviceServerAddressChange.all_objects.get()
            token = url.split("token=")[1]
            response = self.client.get(
                reverse("devices:iclock_address_check"),
                {"token": token},
                HTTP_HOST=NEW_HOST,
            )
            return response.status_code, response.content.decode()

        attempt = self._request_change(fetch=fetch)
        # Still checking while the probe ran, so the token still resolved.
        self.assertEqual(
            seen["attempt"].status, DeviceServerAddressChange.Status.CHECKING
        )
        self.assertEqual(attempt.status, DeviceServerAddressChange.Status.QUEUED)
        # And nothing was saved off the back of that check-in.
        self.device.refresh_from_db()
        self.assertEqual(self.device.server_host, OLD_HOST)

    def test_the_probe_endpoint_answers_only_a_live_token(self):
        with use_company(self.company):
            attempt = DeviceServerAddressChange.all_objects.create(
                company=self.company, device=self.device, created_by=self.admin,
                new_scheme="https", new_host=NEW_HOST, new_port=443,
                probe_token="live-token", probe_started_at=timezone.now(),
            )
        url = reverse("devices:iclock_address_check")

        good = self.client.get(url, {"token": "live-token"})
        self.assertEqual(good.status_code, 200)
        self.assertEqual(
            good.content.decode().strip(),
            server_address.probe_body("live-token").strip(),
        )

        self.assertEqual(self.client.get(url, {"token": "other"}).status_code, 404)
        self.assertEqual(self.client.get(url).status_code, 404)

        # Spent once the attempt leaves the checking state.
        attempt.status = DeviceServerAddressChange.Status.QUEUED
        attempt.save(update_fields=["status"])
        self.assertEqual(
            self.client.get(url, {"token": "live-token"}).status_code, 404
        )

    def test_an_unexpected_error_still_fails_closed(self):
        """Anything the probe did not anticipate must not reach the device."""
        attempt = self._request_change(
            fetch=self._failing_probe(exc=ValueError("something odd"))
        )
        self.assertEqual(attempt.status, DeviceServerAddressChange.Status.UNREACHABLE)
        self.assertEqual(pending_summary(self.device), [])

    def test_the_token_is_cleared_whatever_the_outcome(self):
        for fetch in (self._ok_probe(), self._failing_probe(exc=OSError("x"))):
            with self.subTest(fetch=fetch):
                DeviceServerAddressChange.all_objects.all().delete()
                attempt = self._request_change(fetch=fetch)
                self.assertEqual(attempt.probe_token, "")

    def test_the_real_endpoint_satisfies_the_real_probe(self):
        """End to end through Django, not a stubbed fetch.

        This is the test that would catch the endpoint and the checker
        drifting apart — the two halves are written in different modules and
        only ever meet over the wire.
        """
        captured = {}

        def fetch(url):
            captured["url"] = url
            token = url.split("token=")[1]
            response = self.client.get(
                reverse("devices:iclock_address_check"),
                {"token": token},
                HTTP_HOST=NEW_HOST,
            )
            return response.status_code, response.content.decode()

        attempt = self._request_change(fetch=fetch)
        self.assertEqual(attempt.status, DeviceServerAddressChange.Status.QUEUED)
        self.assertIn("/iclock/serveraddress-check", captured["url"])


# --------------------------------------------------- steps 2-4: the machine


@override_settings(ALLOWED_HOSTS=ALLOWED)
class ChangeFlowTests(ServerAddressTestCase):
    def test_a_passing_check_queues_the_command_but_saves_nothing(self):
        attempt = self._request_change()
        self.assertEqual(attempt.status, DeviceServerAddressChange.Status.QUEUED)

        # Two commands, one option each: this firmware swallows a
        # tab-separated pair as the value of the first option and leaves the
        # second untouched. Port first, so the device is never handed a new
        # host paired with a stale port.
        pending = pending_summary(self.device)
        self.assertEqual(
            [entry["body"] for entry in pending],
            [
                f"SET OPTION {server_address.SERVER_PORT_OPTION}=443",
                f"SET OPTION {server_address.SERVER_ADDRESS_OPTION}={NEW_HOST}",
            ],
        )
        self.assertEqual(pending[1]["key"], "set_server_address")
        self.assertEqual(attempt.command_id, pending[1]["id"])
        self.assertEqual(attempt.port_command_id, pending[0]["id"])

        # Still the old address: the device has not moved yet.
        self.device.refresh_from_db()
        self.assertEqual(self.device.server_host, OLD_HOST)

    def test_handing_the_command_over_advances_to_delivered(self):
        attempt = self._request_change()
        take_pending_commands(self.device)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, DeviceServerAddressChange.Status.DELIVERED)
        self.assertIsNotNone(attempt.command_delivered_at)

    def test_a_zero_return_acknowledges_but_does_not_confirm(self):
        """The device says OK before it tries the address. That is not proof."""
        attempt = self._request_change()
        take_pending_commands(self.device)
        server_address.note_command_result(
            device=self.device, command_id=attempt.command_id, return_code="0"
        )
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, DeviceServerAddressChange.Status.ACKNOWLEDGED)
        self.device.refresh_from_db()
        self.assertEqual(self.device.server_host, OLD_HOST)

    def test_a_request_arriving_at_the_new_address_confirms_and_saves(self):
        attempt = self._request_change()
        take_pending_commands(self.device)

        response = self._device_request(NEW_HOST)
        self.assertEqual(response.status_code, 200)

        attempt.refresh_from_db()
        self.assertEqual(attempt.status, DeviceServerAddressChange.Status.CONFIRMED)
        self.assertIsNotNone(attempt.seen_at_new_address_at)
        self.device.refresh_from_db()
        self.assertEqual(self.device.server_host, NEW_HOST)
        self.assertEqual(self.device.server_scheme, "https")
        self.assertEqual(self.device.server_port, 443)

    def test_a_request_at_the_old_address_is_recorded_not_confirmed(self):
        attempt = self._request_change()
        take_pending_commands(self.device)
        self._device_request(OLD_HOST)

        attempt.refresh_from_db()
        self.assertIsNotNone(attempt.seen_at_old_address_at)
        self.assertIsNone(attempt.seen_at_new_address_at)
        self.assertEqual(attempt.status, DeviceServerAddressChange.Status.DELIVERED)

    def test_a_device_rejection_ends_the_attempt_immediately(self):
        """No reason to make anyone wait out the deadline for a known answer."""
        attempt = self._request_change()
        take_pending_commands(self.device)
        server_address.note_command_result(
            device=self.device, command_id=attempt.command_id, return_code="-629"
        )
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, DeviceServerAddressChange.Status.NOT_APPLIED)
        self.assertIn("-629", attempt.failure_reason)
        self.device.refresh_from_db()
        self.assertEqual(self.device.server_host, OLD_HOST)

    def test_neither_command_ever_carries_two_options(self):
        """The measured firmware trap, kept as a rule the code cannot drift past.

        ``SET OPTION IclockSvrIP=host\tIclockSvrPort=443`` answers Return=0
        on a SenseFace 2A and then reads back with the whole string as the
        host and the port unchanged. One option per command, always.
        """
        self._request_change()
        for entry in pending_summary(self.device):
            self.assertNotIn("\t", entry["body"])
            self.assertEqual(entry["body"].count("="), 1)

    def test_the_port_command_is_withdrawn_if_the_host_one_cannot_be_queued(self):
        """Half an address is worse than none: the device would be lost."""
        from devices.services import commands

        original = commands._queue_raw
        calls = {"n": 0}

        def only_the_first(**kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return original(**kwargs)
            return None, "Too many commands are already queued for this device."

        try:
            commands._queue_raw = only_the_first
            attempt = self._request_change()
        finally:
            commands._queue_raw = original

        self.assertEqual(attempt.status, DeviceServerAddressChange.Status.UNREACHABLE)
        self.assertEqual(pending_summary(self.device), [])

    def test_a_command_result_for_another_command_is_ignored(self):
        attempt = self._request_change()
        take_pending_commands(self.device)
        server_address.note_command_result(
            device=self.device, command_id=(attempt.command_id or 0) + 99,
            return_code="-1",
        )
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, DeviceServerAddressChange.Status.DELIVERED)

    def test_the_devicecmd_endpoint_feeds_the_result_through(self):
        """The wire path, not the service call: ID=n&Return=c off the device."""
        attempt = self._request_change()
        take_pending_commands(self.device)
        self.client.post(
            "/iclock/devicecmd",
            data=f"ID={attempt.command_id}&Return=0&CMD=SET OPTION",
            content_type="text/plain",
            QUERY_STRING=f"SN={self.device.serial_number}",
            HTTP_HOST=OLD_HOST,
            HTTP_X_FORWARDED_PROTO="https",
        )
        attempt.refresh_from_db()
        self.assertEqual(attempt.command_return_code, "0")
        self.assertIsNotNone(attempt.command_acknowledged_at)

    def test_only_one_change_may_run_at_a_time(self):
        self._request_change()
        with self.assertRaises(server_address.ServerAddressError) as ctx:
            self._request_change(host="third.example.test")
        self.assertIn("already in progress", str(ctx.exception))

    def test_a_new_change_is_allowed_once_the_last_one_finished(self):
        self._request_change()
        take_pending_commands(self.device)
        self._device_request(NEW_HOST)
        # The confirmation was written by the view stack, so re-read before
        # asking the service what this device's current address is.
        self.device.refresh_from_db()
        attempt = self._request_change(host=OLD_HOST)
        self.assertEqual(attempt.status, DeviceServerAddressChange.Status.QUEUED)

    def test_changing_to_the_address_already_saved_is_refused(self):
        with self.assertRaises(server_address.ServerAddressError) as ctx:
            self._request_change(host=OLD_HOST)
        self.assertIn("already uses", str(ctx.exception))

    def test_a_retired_device_is_refused(self):
        BiometricDevice.all_objects.filter(pk=self.device.pk).update(
            status=BiometricDevice.Status.RETIRED
        )
        self.device.refresh_from_db()
        with self.assertRaises(server_address.ServerAddressError):
            self._request_change()

    def test_the_probe_is_rate_capped_per_device(self):
        for index in range(server_address.MAX_PROBES_PER_HOUR):
            DeviceServerAddressChange.all_objects.create(
                company=self.company, device=self.device,
                new_scheme="https", new_host=f"h{index}.example.test", new_port=443,
                status=DeviceServerAddressChange.Status.UNREACHABLE,
                probe_started_at=timezone.now(),
            )
        with self.assertRaises(server_address.ServerAddressError) as ctx:
            self._request_change()
        self.assertIn("Too many address checks", str(ctx.exception))


# ------------------------------------------------------------ step 5: time


@override_settings(ALLOWED_HOSTS=ALLOWED)
class TimeoutTests(ServerAddressTestCase):
    def _expire(self, attempt):
        """Wind the deadline back and judge it, from a freshly loaded row.

        The reload matters: the device requests above ran through the view
        stack and updated their own instance, so an in-memory attempt from
        before them is missing exactly the field the deadline turns on. In
        production the status endpoint always loads the row fresh.
        """
        attempt.refresh_from_db()
        attempt.deadline_at = timezone.now() - timedelta(seconds=1)
        attempt.save(update_fields=["deadline_at"])
        return server_address.refresh(attempt)

    def test_the_deadline_is_derived_from_the_push_interval(self):
        BiometricDevice.all_objects.filter(pk=self.device.pk).update(
            settings={"push_interval_seconds": 120}
        )
        self.device.refresh_from_db()
        attempt = self._request_change()
        window = attempt.deadline_at - attempt.command_queued_at
        self.assertEqual(
            window,
            timedelta(seconds=120 * server_address.DEADLINE_INTERVALS),
        )

    def test_a_short_interval_still_gets_the_floor(self):
        attempt = self._request_change()
        window = attempt.deadline_at - attempt.command_queued_at
        self.assertEqual(
            window, timedelta(seconds=server_address.MIN_DEADLINE_SECONDS)
        )

    def test_still_on_the_old_address_at_the_deadline_is_not_applied(self):
        attempt = self._request_change()
        take_pending_commands(self.device)
        self._device_request(OLD_HOST)

        attempt = self._expire(attempt)
        self.assertEqual(attempt.status, DeviceServerAddressChange.Status.NOT_APPLIED)
        self.assertIn("never applied the change", attempt.failure_reason)
        self.device.refresh_from_db()
        self.assertEqual(self.device.server_host, OLD_HOST)

    def test_silence_at_the_deadline_means_the_device_is_lost(self):
        attempt = self._request_change()
        take_pending_commands(self.device)

        attempt = self._expire(attempt)
        self.assertEqual(attempt.status, DeviceServerAddressChange.Status.LOST)
        self.assertIn("only the terminal can undo", attempt.failure_reason)
        # The old address is still saved: it is the value someone has to type
        # back in, and losing it would leave them guessing.
        self.device.refresh_from_db()
        self.assertEqual(self.device.server_host, OLD_HOST)

    def test_a_lost_device_gets_the_exact_value_to_type_back(self):
        attempt = self._request_change()
        take_pending_commands(self.device)
        attempt = self._expire(attempt)
        self.assertEqual(attempt.status, DeviceServerAddressChange.Status.LOST)

        recovery = server_address.recovery_instructions(attempt)
        self.assertEqual(recovery["address"], OLD_HOST)
        self.assertEqual(recovery["port"], "443")
        self.assertIn("Cloud Server Settings", recovery["menu_path"])
        self.assertEqual(recovery["domain_name_setting"], "On")

    def test_recovery_says_domain_name_off_for_an_ip(self):
        BiometricDevice.all_objects.filter(pk=self.device.pk).update(
            server_scheme="http", server_host="192.168.1.20", server_port=8000
        )
        self.device.refresh_from_db()
        attempt = self._request_change()
        take_pending_commands(self.device)
        attempt = self._expire(attempt)
        recovery = server_address.recovery_instructions(attempt)
        self.assertEqual(recovery["address"], "192.168.1.20")
        self.assertEqual(recovery["port"], "8000")
        self.assertEqual(recovery["domain_name_setting"], "Off")

    def test_the_deadline_does_not_fire_early(self):
        attempt = self._request_change()
        self.assertEqual(
            server_address.refresh(attempt).status,
            DeviceServerAddressChange.Status.QUEUED,
        )

    def test_a_finished_attempt_is_never_re_judged(self):
        attempt = self._request_change()
        take_pending_commands(self.device)
        self._device_request(NEW_HOST)
        attempt.refresh_from_db()
        attempt = self._expire(attempt)
        self.assertEqual(attempt.status, DeviceServerAddressChange.Status.CONFIRMED)


# ------------------------------------------------------------- the screens


@override_settings(ALLOWED_HOSTS=ALLOWED)
class ServerAddressScreenTests(ServerAddressTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def _edit_post(self, **overrides):
        data = {
            "name": self.device.name,
            "serial_number": self.device.serial_number,
            "branch": self.branch.pk,
            "device_model": self.device.device_model_id,
            "external_device_id": "",
            "timezone": "Asia/Dhaka",
            "installed_at": "",
            "status": BiometricDevice.Status.ACTIVE,
            "comm_key": "",
            "push_interval_seconds": 10,
            "error_delay_seconds": 30,
            "realtime": "on",
            "server_address": f"https://{OLD_HOST}",
        }
        data.update(overrides)
        return data

    def test_the_edit_form_offers_the_saved_address(self):
        response = self.client.get(
            reverse("devices:device_edit", args=[self.device.public_id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["form"].fields["server_address"].initial,
            f"https://{OLD_HOST}",
        )

    def test_registering_a_device_has_no_server_address_field(self):
        """There is nothing to repoint yet; the value is typed at the terminal."""
        response = self.client.get(reverse("devices:device_register"))
        self.assertNotIn("server_address", response.context["form"].fields)

    def test_changing_the_address_without_confirming_is_refused(self):
        response = self.client.post(
            reverse("devices:device_edit", args=[self.device.public_id]),
            self._edit_post(server_address=f"https://{NEW_HOST}"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("server_address_confirmed", response.context["form"].errors)
        self.assertEqual(DeviceServerAddressChange.all_objects.count(), 0)

    def test_saving_the_form_without_touching_the_address_changes_nothing(self):
        response = self.client.post(
            reverse("devices:device_edit", args=[self.device.public_id]),
            self._edit_post(name="Renamed"),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(DeviceServerAddressChange.all_objects.count(), 0)
        self.assertEqual(pending_summary(self.device), [])

    def test_an_invalid_address_is_a_field_error_not_a_server_error(self):
        response = self.client.post(
            reverse("devices:device_edit", args=[self.device.public_id]),
            self._edit_post(
                server_address="https://host.example/iclock/cdata",
                server_address_confirmed="on",
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("server_address", response.context["form"].errors)

    def test_a_confirmed_change_starts_the_flow(self):
        def fetch(url):
            token = url.split("token=")[1]
            return 200, server_address.probe_body(token)

        original = server_address._run_probe
        try:
            server_address._run_probe = lambda address, token, fetch=None: (True, "")
            response = self.client.post(
                reverse("devices:device_edit", args=[self.device.public_id]),
                self._edit_post(
                    server_address=f"https://{NEW_HOST}",
                    server_address_confirmed="on",
                ),
                follow=True,
            )
        finally:
            server_address._run_probe = original

        self.assertEqual(response.status_code, 200)
        attempt = DeviceServerAddressChange.all_objects.get()
        self.assertEqual(attempt.status, DeviceServerAddressChange.Status.QUEUED)
        self.assertEqual(attempt.created_by, self.admin)
        self.assertContains(response, NEW_HOST)

    def test_the_status_endpoint_reports_the_live_state(self):
        attempt = self._request_change()
        response = self.client.get(
            reverse(
                "devices:device_server_address_status", args=[self.device.public_id]
            )
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["active"])
        self.assertEqual(payload["status"], "queued")
        self.assertEqual(payload["new_address"], f"https://{NEW_HOST}")
        self.assertEqual(payload["saved_address"], f"https://{OLD_HOST}")
        self.assertTrue(payload["sent_to_device"])
        self.assertFalse(payload["finished"])

    def test_reading_the_status_is_what_fires_the_timeout(self):
        """No background worker: the deadline is judged when someone looks."""
        attempt = self._request_change()
        take_pending_commands(self.device)
        DeviceServerAddressChange.all_objects.filter(pk=attempt.pk).update(
            deadline_at=timezone.now() - timedelta(seconds=1)
        )
        payload = self.client.get(
            reverse(
                "devices:device_server_address_status", args=[self.device.public_id]
            )
        ).json()
        self.assertEqual(payload["status"], "lost")
        self.assertTrue(payload["finished"])
        self.assertEqual(payload["recovery"]["address"], OLD_HOST)

    def test_the_two_timeout_failures_read_differently(self):
        attempt = self._request_change()
        take_pending_commands(self.device)
        self._device_request(OLD_HOST)
        DeviceServerAddressChange.all_objects.filter(pk=attempt.pk).update(
            deadline_at=timezone.now() - timedelta(seconds=1)
        )
        payload = self.client.get(
            reverse(
                "devices:device_server_address_status", args=[self.device.public_id]
            )
        ).json()
        self.assertEqual(payload["status"], "not_applied")
        # No terminal instructions: nothing on the device changed.
        self.assertIsNone(payload["recovery"])
        self.assertIn("still on the old address", payload["message"])

    def test_the_detail_page_shows_the_panel_and_the_history(self):
        attempt = self._request_change()
        take_pending_commands(self.device)
        self._device_request(NEW_HOST)
        response = self.client.get(
            reverse("devices:device_detail", args=[self.device.public_id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Server address")
        self.assertContains(response, "Connected at the new address")
        self.assertContains(response, self.admin.email)

    def test_the_edit_form_is_closed_while_a_change_runs(self):
        self._request_change()
        response = self.client.get(
            reverse("devices:device_edit", args=[self.device.public_id])
        )
        self.assertTrue(response.context["form"].fields["server_address"].disabled)

    def test_a_change_can_be_cancelled_only_before_it_is_sent(self):
        with use_company(self.company):
            attempt = DeviceServerAddressChange.all_objects.create(
                company=self.company, device=self.device, created_by=self.admin,
                new_scheme="https", new_host=NEW_HOST, new_port=443,
                status=DeviceServerAddressChange.Status.CHECKING,
                probe_token="t", probe_started_at=timezone.now(),
            )
        self.client.post(
            reverse(
                "devices:device_server_address_cancel", args=[self.device.public_id]
            )
        )
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, DeviceServerAddressChange.Status.CANCELLED)

    def test_a_sent_change_cannot_be_cancelled(self):
        attempt = self._request_change()
        with self.assertRaises(server_address.ServerAddressError):
            server_address.cancel(attempt=attempt, actor=self.admin)


# --------------------------------------------------------- the status panel


@override_settings(ALLOWED_HOSTS=ALLOWED)
class StatusPanelTests(ServerAddressTestCase):
    """What the page tells the poller about whether to keep watching.

    A finished change must render as finished and *stay* there. Getting this
    wrong turned a failed address change into a page that reloaded itself
    forever: the panel rendered a terminal state, the script could not tell it
    apart from a running one, polled once, saw "finished", reloaded, and did
    the same thing on every load.
    """

    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def _panel(self):
        response = self.client.get(
            reverse("devices:device_detail", args=[self.device.public_id])
        )
        body = response.content.decode()
        return body.split("data-address-panel", 1)[1].split(">", 1)[0]

    def test_a_running_change_tells_the_page_to_watch(self):
        self._request_change()
        self.assertIn('data-address-active="1"', self._panel())

    def test_a_failed_check_tells_the_page_to_stop(self):
        """The exact state that used to loop: nothing was even sent."""
        attempt = self._request_change(
            fetch=self._failing_probe(status=404, body="not found")
        )
        self.assertEqual(attempt.status, DeviceServerAddressChange.Status.UNREACHABLE)
        panel = self._panel()
        self.assertIn('data-address-active="0"', panel)
        self.assertIn('data-address-status="unreachable"', panel)

    def test_every_finished_state_tells_the_page_to_stop(self):
        """Not just the two that happened to be styled as success or danger.

        The old guard looked at the alert's colour class and only recognised
        success and danger, so the warning-coloured outcomes kept polling.
        """
        finished = (
            DeviceServerAddressChange.Status.UNREACHABLE,
            DeviceServerAddressChange.Status.CONFIRMED,
            DeviceServerAddressChange.Status.NOT_APPLIED,
            DeviceServerAddressChange.Status.LOST,
            DeviceServerAddressChange.Status.CANCELLED,
        )
        for status in finished:
            with self.subTest(status=status):
                DeviceServerAddressChange.all_objects.all().delete()
                DeviceServerAddressChange.all_objects.create(
                    company=self.company, device=self.device, created_by=self.admin,
                    previous_scheme="https", previous_host=OLD_HOST,
                    previous_port=443,
                    new_scheme="https", new_host=NEW_HOST, new_port=443,
                    status=status,
                )
                self.assertIn('data-address-active="0"', self._panel())

    def test_a_device_with_no_change_at_all_tells_the_page_to_stop(self):
        self.assertIn('data-address-active="0"', self._panel())


# ------------------------------------------------------ the communication key


@override_settings(ALLOWED_HOSTS=ALLOWED)
class CommKeyOnEditTests(ServerAddressTestCase):
    """Editing a device must not invent a key the device was never told.

    This locked a live terminal out. The device had been pushing without a
    comm key for weeks, which the ingestion path allows when none is stored.
    Somebody opened the edit form to change the server address, saved it, and
    the form generated a key because none existed — so every push afterwards
    was refused with 401 and the device simply went quiet.
    """

    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        BiometricDevice.all_objects.filter(pk=self.device.pk).update(
            authentication_secret_hash="", authentication_key_id=""
        )
        self.device.refresh_from_db()

    def _edit(self, **overrides):
        data = {
            "name": self.device.name,
            "serial_number": self.device.serial_number,
            "branch": self.branch.pk,
            "device_model": self.device.device_model_id,
            "external_device_id": "",
            "timezone": "Asia/Dhaka",
            "installed_at": "",
            "status": BiometricDevice.Status.ACTIVE,
            "comm_key": "",
            "push_interval_seconds": 10,
            "error_delay_seconds": 30,
            "realtime": "on",
            "server_address": f"https://{OLD_HOST}",
        }
        data.update(overrides)
        return self.client.post(
            reverse("devices:device_edit", args=[self.device.public_id]),
            data, follow=True,
        )

    def test_editing_a_keyless_device_leaves_it_keyless(self):
        self._edit(name="Renamed")
        self.device.refresh_from_db()
        self.assertEqual(self.device.authentication_secret_hash, "")

    def test_a_keyless_device_still_pushes_after_an_edit(self):
        """The end-to-end version: the device must still be let in."""
        self._edit(name="Renamed")
        response = self.client.get(
            "/iclock/getrequest", {"SN": self.device.serial_number},
            HTTP_HOST=OLD_HOST, HTTP_X_FORWARDED_PROTO="https",
        )
        self.assertEqual(response.status_code, 200)

    def test_typing_a_key_on_the_edit_form_still_sets_one(self):
        """Deliberate is still deliberate."""
        self._edit(comm_key="abc123def456")
        self.device.refresh_from_db()
        self.assertNotEqual(self.device.authentication_secret_hash, "")

    def test_removing_a_key_lets_a_locked_out_device_in(self):
        """The SenseFace 3A, 2026-09-14: registered with an invented key, every
        push refused with 401 because the device cannot send one."""
        self._edit(comm_key="abc123def456")
        locked = self.client.get(
            "/iclock/getrequest", {"SN": self.device.serial_number},
            HTTP_HOST=OLD_HOST, HTTP_X_FORWARDED_PROTO="https",
        )
        self.assertEqual(locked.status_code, 401)

        page = self.client.get(reverse("devices:device_edit", args=[self.device.public_id]))
        self.assertContains(page, "Remove the communication key")
        self._edit(remove_comm_key="on")
        self.device.refresh_from_db()
        self.assertEqual(
            (self.device.authentication_secret_hash, self.device.authentication_key_id), ("", "")
        )
        let_in = self.client.get(
            "/iclock/getrequest", {"SN": self.device.serial_number},
            HTTP_HOST=OLD_HOST, HTTP_X_FORWARDED_PROTO="https",
        )
        self.assertEqual(let_in.status_code, 200)

    def test_a_keyless_device_is_not_offered_the_removal(self):
        page = self.client.get(reverse("devices:device_edit", args=[self.device.public_id]))
        self.assertNotContains(page, "Remove the communication key")

    def test_registering_a_device_issues_no_key_unless_one_is_typed(self):
        response = self.client.post(reverse("devices:device_register"), {
            "name": "Back Door",
            "serial_number": "SN-NEW-1",
            "branch": self.branch.pk,
            "device_model": self.device.device_model_id,
            "external_device_id": "",
            "timezone": "Asia/Dhaka",
            "installed_at": "",
            "status": BiometricDevice.Status.PENDING,
            "comm_key": "",
            "push_interval_seconds": 10,
            "error_delay_seconds": 30,
            "realtime": "on",
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        created = BiometricDevice.all_objects.get(serial_number="SN-NEW-1")
        # A ZKTeco push device cannot send a key; one it was never given
        # would lock it out from its first request.
        self.assertEqual(created.authentication_secret_hash, "")
        self.assertNotContains(response, "Communication key:")


# ------------------------------------------------- permissions and auditing


@override_settings(ALLOWED_HOSTS=ALLOWED)
class ServerAddressAuthorizationTests(ServerAddressTestCase):
    def setUp(self):
        super().setUp()
        self.outsider = User.objects.create_user(
            email="outsider@example.test", password="pw-12345678"
        )
        self.other_company = Company.objects.create(
            code="B", slug="b", name="Company B"
        )
        CompanyMembership.all_objects.create(
            company=self.other_company, user=self.outsider,
            role="company_admin", status="active",
        )
        self.viewer = User.objects.create_user(
            email="viewer@example.test", password="pw-12345678"
        )
        CompanyMembership.all_objects.create(
            company=self.company, user=self.viewer, role="employee", status="active",
        )

    def test_the_service_refuses_a_user_from_another_company(self):
        """Checked in the service, not only on the view."""
        with use_company(self.company):
            with self.assertRaises(PermissionDenied):
                server_address.request_change(
                    device=self.device, actor=self.outsider,
                    raw_address=f"https://{NEW_HOST}", fetch=self._ok_probe(),
                )
        self.assertEqual(DeviceServerAddressChange.all_objects.count(), 0)

    def test_the_service_refuses_an_employee_of_this_company(self):
        with use_company(self.company):
            with self.assertRaises(PermissionDenied):
                server_address.request_change(
                    device=self.device, actor=self.viewer,
                    raw_address=f"https://{NEW_HOST}", fetch=self._ok_probe(),
                )

    def test_a_branch_restricted_administrator_is_refused(self):
        """A device sits in one branch, but its settings reach the terminal.

        Only one current administrator is allowed per company, so this narrows
        the existing one rather than adding a second.
        """
        membership = CompanyMembership.all_objects.get(
            company=self.company, user=self.admin
        )
        with use_company(self.company):
            membership.allowed_branches.add(self.branch)
            with self.assertRaises(PermissionDenied):
                server_address.request_change(
                    device=self.device, actor=self.admin,
                    raw_address=f"https://{NEW_HOST}", fetch=self._ok_probe(),
                )

    def test_the_status_endpoint_is_closed_to_other_companies(self):
        self.client.force_login(self.outsider)
        response = self.client.get(
            reverse(
                "devices:device_server_address_status", args=[self.device.public_id]
            )
        )
        self.assertIn(response.status_code, (403, 404))

    def test_the_status_endpoint_needs_a_login(self):
        response = self.client.get(
            reverse(
                "devices:device_server_address_status", args=[self.device.public_id]
            )
        )
        self.assertEqual(response.status_code, 302)

    def test_the_request_is_audited(self):
        self.client.force_login(self.admin)
        original = server_address._run_probe
        try:
            server_address._run_probe = lambda address, token, fetch=None: (True, "")
            self.client.post(
                reverse("devices:device_edit", args=[self.device.public_id]),
                {
                    "name": self.device.name,
                    "serial_number": self.device.serial_number,
                    "branch": self.branch.pk,
                    "device_model": self.device.device_model_id,
                    "external_device_id": "",
                    "timezone": "Asia/Dhaka",
                    "installed_at": "",
                    "status": BiometricDevice.Status.ACTIVE,
                    "comm_key": "",
                    "push_interval_seconds": 10,
                    "error_delay_seconds": 30,
                    "realtime": "on",
                    "server_address": f"https://{NEW_HOST}",
                    "server_address_confirmed": "on",
                },
            )
        finally:
            server_address._run_probe = original

        entry = AuditLog.objects.get(action="device.server_address.requested")
        self.assertEqual(entry.actor_user, self.admin)
        self.assertEqual(entry.after_data["address"], f"https://{NEW_HOST}")
        self.assertEqual(entry.before_data["address"], f"https://{OLD_HOST}")

    def test_every_outcome_is_audited_even_without_a_request(self):
        attempt = self._request_change()
        take_pending_commands(self.device)
        self._device_request(NEW_HOST)
        entry = AuditLog.objects.get(action="device.server_address.confirmed")
        self.assertEqual(entry.actor_type, "device")
        self.assertEqual(entry.after_data["requested_by"], self.admin.email)

    def test_a_timeout_outcome_is_audited_as_the_system(self):
        attempt = self._request_change()
        take_pending_commands(self.device)
        attempt.deadline_at = timezone.now() - timedelta(seconds=1)
        attempt.save(update_fields=["deadline_at"])
        server_address.refresh(attempt)
        entry = AuditLog.objects.get(action="device.server_address.lost")
        self.assertEqual(entry.actor_type, "system")


# ------------------------------------------------------------- the guardrail


class WritableOptionsGuardTests(ServerAddressTestCase):
    def test_the_server_address_is_still_not_a_writable_option(self):
        """The guarded flow must be the only way in.

        ``queue_set_option`` writes straight to the device on the next poll,
        with no proof that the address reaches anything. Adding the server
        address to that allowlist would bypass step 1 entirely, which is the
        whole safety property.
        """
        from devices.services.commands import WRITABLE_OPTIONS

        for key, (option_name, _validator, _help) in WRITABLE_OPTIONS.items():
            self.assertNotIn(
                option_name,
                (
                    server_address.SERVER_ADDRESS_OPTION,
                    server_address.SERVER_PORT_OPTION,
                ),
                f"{key} would let the server address be set without a check",
            )

    def test_set_option_refuses_the_address_keys_by_name(self):
        from devices.services.commands import queue_set_option

        for key in ("server_address", "IclockSvrIP", "IclockSvrPort"):
            entry, error = queue_set_option(
                device=self.device, option_key=key, value="evil.example"
            )
            self.assertIsNone(entry, key)
            self.assertTrue(error, key)
