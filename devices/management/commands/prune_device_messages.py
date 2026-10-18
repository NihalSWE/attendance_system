"""Delete device uploads that are only old copies of the user list (2026-10-18).

    python manage.py prune_device_messages                # what would go (nothing deleted)
    python manage.py prune_device_messages --check        # also prove it, then undo
    python manage.py prune_device_messages --apply        # delete
    python manage.py prune_device_messages --apply --device 2

Deletes for real only with ``--apply``. For each device, inside one
transaction: delete the copies, rebuild the user list from what is left,
compare it with the list before; if anything differs, that device is left
exactly as it was. Punches, check-ins and anything not understood are never
touched (devices/services/message_prune.py).

PostgreSQL reuses the freed space for new rows; the database file itself
shrinks only after ``VACUUM FULL devices_devicemessage`` (it locks the table
while it runs - do it in a quiet hour).
"""

from django.core.management.base import BaseCommand, CommandError

from devices.models import BiometricDevice
from devices.services import message_prune


def _size(count):
    return f"{count / 1024 / 1024:,.1f} MB"


class Command(BaseCommand):
    help = "Delete device uploads that are only old copies of the user list (--apply)."

    def add_arguments(self, parser):
        parser.add_argument("--device", type=int, help="Only this device id.")
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--check", action="store_true",
                          help="Delete, prove the user list is unchanged, then undo.")
        mode.add_argument("--apply", action="store_true", help="Delete for real.")

    def handle(self, *args, device=None, check=False, apply=False, **options):
        devices = BiometricDevice.all_objects.order_by("pk")
        if device is not None:
            devices = devices.filter(pk=device)
            if not devices.exists():
                raise CommandError(f"No device with id {device}.")
        what = "deleted" if apply else "checked, nothing deleted" if check else "would go"
        total_count, total_bytes, refused = 0, 0, []
        for item in devices:
            try:
                if apply or check:
                    result = message_prune.prune(item, apply=apply)
                else:
                    result = message_prune.plan(item)
            except message_prune.PruneMismatch as exc:
                refused.append(str(exc))
                self.stdout.write(self.style.ERROR(f"{item.pk:>4}  {item.name[:40]:<40}  {exc}"))
                continue
            total_count += len(result.deletable)
            total_bytes += result.bytes
            self.stdout.write(
                f"{item.pk:>4}  {item.name[:40]:<40}  {result.uploads:>6} uploads, "
                f"{len(result.deletable):>6} copies ({_size(result.bytes)}) {what}")
        self.stdout.write(f"Total: {total_count} uploads, {_size(total_bytes)} {what}.")
        if refused:
            raise CommandError(f"{len(refused)} device(s) left as they were: the user list "
                               "would have changed.")
        if not (apply or check):
            self.stdout.write("Nothing deleted. --check proves it and undoes; --apply deletes.")
