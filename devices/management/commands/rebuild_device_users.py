"""Work each device's user list out again from its stored uploads (2026-10-18).

    python manage.py rebuild_device_users            # every device
    python manage.py rebuild_device_users --device 2
    python manage.py rebuild_device_users --check    # and compare with the old way

Run once after deploying the ``DeviceUser`` table. Without it, each device's
list is worked out the first time someone opens one of its pages (slow once on
a busy device); after that, uploads update it as they arrive. Safe to run
again at any time: it only rebuilds ``DeviceUser`` rows from ``DeviceMessage``,
which it never changes.

``--check`` also works the roster out the old way, from every upload, and says
whether the two agree (slow on a busy device: it is the old page's work).
"""

import time

from django.core.management.base import BaseCommand, CommandError

from common.tenant import use_company
from devices.models import BiometricDevice
from devices.services import device_users
from devices.services.device_roster import build_roster, roster_from_messages

COMPARED = ("pin", "uid", "name", "card_number", "has_password", "group", "tz",
            "privilege_code", "disabled_on_device", "fingerprint_count", "face_count",
            "other_biometric_count", "has_photo", "only_in_scans", "removed_from_device",
            "is_mapped")


class Command(BaseCommand):
    help = "Rebuild each device's user list (DeviceUser) from its stored uploads."

    def add_arguments(self, parser):
        parser.add_argument("--device", type=int, help="Only this device id.")
        parser.add_argument("--check", action="store_true",
                            help="Also compare with the roster worked out the old way.")

    def handle(self, *args, device=None, check=False, **options):
        devices = BiometricDevice.all_objects.select_related("company").order_by("pk")
        if device is not None:
            devices = devices.filter(pk=device)
            if not devices.exists():
                raise CommandError(f"No device with id {device}.")
        problems = 0
        for item in devices:
            started = time.perf_counter()
            device_users.rebuild(item)
            took = time.perf_counter() - started
            listed = sum(1 for row in device_users.rows(item) if row.reported)
            line = f"{item.pk:>4}  {item.name[:40]:<40}  {listed:>5} users  {took:6.1f}s"
            if check:
                with use_company(item.company_id):
                    new = {r["pin"]: tuple(r[k] for k in COMPARED) for r in build_roster(item)}
                    old = {r["pin"]: tuple(r[k] for k in COMPARED)
                           for r in roster_from_messages(item)}
                differ = sorted(pin for pin in new.keys() | old.keys()
                                if new.get(pin) != old.get(pin))
                if differ:
                    problems += 1
                    line += f"  DIFFERENT for {len(differ)}: {', '.join(differ[:10])}"
                else:
                    line += "  same as before"
            self.stdout.write(line)
        if problems:
            raise CommandError(f"{problems} device(s) differ from the old roster.")
        self.stdout.write(self.style.SUCCESS("Done."))
