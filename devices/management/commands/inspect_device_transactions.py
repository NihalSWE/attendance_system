import json

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from devices.models import BiometricDevice
from devices.services.transaction_inventory import inspect


class Command(BaseCommand):
    help = "Read-only report of an already captured transaction query. Sends no device command."

    def add_arguments(self, parser):
        parser.add_argument("--device", required=True, type=int)
        parser.add_argument("--command", required=True, type=int)
        parser.add_argument("--days", required=True, type=int)

    def handle(self, *args, device, command, days, **options):
        try:
            with transaction.atomic():
                if connection.vendor == "postgresql":
                    with connection.cursor() as cursor:
                        cursor.execute("SET TRANSACTION READ ONLY")
                reader = BiometricDevice.all_objects.get(pk=device)
                report = inspect(reader, command, days)
        except (BiometricDevice.DoesNotExist, ValueError, KeyError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(report, indent=2))
