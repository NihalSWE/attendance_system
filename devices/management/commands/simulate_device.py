"""Send a repeatable device scenario at a running server.

    python manage.py simulate_device --serial SF2A-001 --scenario replay

The device must already be registered through the UI: the simulator only sends
punches, it never creates the device. That keeps the cold-start rule honest —
if registering a device required a management command, the UI would be
incomplete (TEAM_LEAD_PLAYBOOK.md safeguard 6).

Output separates what was sent from what the server stored, and states plainly
that this is simulator evidence.
"""

from datetime import date, datetime

from django.core.management.base import BaseCommand, CommandError

from devices.models import BiometricDevice, DeviceMessage, PunchEvent
from devices.simulator import SimulatedDevice, scenarios


class Command(BaseCommand):
    help = "Send a repeatable simulated device scenario to the ingestion endpoint."

    def add_arguments(self, parser):
        parser.add_argument("--serial", required=True, help="Registered device serial.")
        parser.add_argument(
            "--scenario",
            default="single_day",
            choices=scenarios.scenario_names(),
            help="Which repeatable scenario to send.",
        )
        parser.add_argument(
            "--base-url",
            default="http://127.0.0.1:8000",
            help="Server base URL (use the tunnel URL to test the real path).",
        )
        parser.add_argument("--comm-key", default="", help="Device secret, if set.")
        parser.add_argument("--key-id", default="", help="Key id, if set.")
        parser.add_argument(
            "--date",
            default=None,
            help="Anchor date (YYYY-MM-DD) so a run is byte-identical when repeated.",
        )
        parser.add_argument(
            "--device-user-id",
            default="1",
            help="Device user number the scenario punches as.",
        )

    def handle(self, *args, **options):
        serial = options["serial"]
        anchor = self._anchor(options["date"])

        device = BiometricDevice.all_objects.filter(serial_number=serial).first()
        if device is None:
            raise CommandError(
                f"No device registered with serial {serial!r}. Register it in the "
                "browser first; the simulator does not create devices."
            )

        kwargs = {}
        if options["scenario"] != "unknown_user":
            kwargs["device_user_id"] = options["device_user_id"]
        scenario = scenarios.build(
            options["scenario"], anchor=anchor, serial=serial, **kwargs
        )

        before = self._counts(device)

        simulated = SimulatedDevice(
            base_url=options["base_url"],
            serial=serial,
            comm_key=options["comm_key"],
            key_id=options["key_id"],
        )

        self.stdout.write(self.style.MIGRATE_HEADING(f"Scenario: {scenario.name}"))
        self.stdout.write(f"  {scenario.description}")
        self.stdout.write(f"  Anchor date: {anchor.isoformat()}  Device: {serial}")
        self.stdout.write("")

        self.stdout.write(self.style.MIGRATE_HEADING("Sent"))
        total_rows = 0
        for exchange in simulated.run_scenario(scenario):
            total_rows += exchange.sent_rows
            marker = "ok " if exchange.status == 200 else "ERR"
            self.stdout.write(
                f"  [{marker}] {exchange.label}: {exchange.sent_rows} row(s) "
                f"-> HTTP {exchange.status} {exchange.response_body!r}"
            )

        after = self._counts(device)
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Stored by the server"))
        self.stdout.write(f"  rows sent:              {total_rows}")
        self.stdout.write(
            f"  new DeviceMessages:     {after['messages'] - before['messages']}"
        )
        self.stdout.write(
            f"  new PunchEvents:        {after['punches'] - before['punches']}"
        )
        for label, key in (
            ("unique", "unique"),
            ("probable duplicates", "probable"),
            ("confirmed duplicates", "confirmed"),
        ):
            self.stdout.write(f"    {label:<21} {after[key] - before[key]}")

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Expected"))
        self.stdout.write(f"  {scenario.expectation}")
        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING(
                "This is simulator evidence only. It does not demonstrate that the "
                "physical SenseFace 2A behaves this way."
            )
        )

    def _anchor(self, value):
        if not value:
            return date.today()
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            raise CommandError(f"--date must be YYYY-MM-DD, got {value!r}") from None

    def _counts(self, device):
        punches = PunchEvent.all_objects.filter(device=device)
        return {
            "messages": DeviceMessage.all_objects.filter(device=device).count(),
            "punches": punches.count(),
            "unique": punches.filter(
                dedupe_status=PunchEvent.DedupeStatus.UNIQUE
            ).count(),
            "probable": punches.filter(
                dedupe_status=PunchEvent.DedupeStatus.PROBABLE_DUPLICATE
            ).count(),
            "confirmed": punches.filter(
                dedupe_status=PunchEvent.DedupeStatus.CONFIRMED_DUPLICATE
            ).count(),
        }
