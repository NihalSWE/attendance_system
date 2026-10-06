"""Bring today's and yesterday's attendance up to date for every company, in the
background, so the attendance screens only read what is saved (2026-10-06).

Devices checking in already do this once a minute for their company. This is
for a scheduler, so a company whose devices are offline is kept up to date
too - e.g. every 5 minutes:

    python manage.py settle_attendance

Repeatable and cheap: a day already built in the last minute is left alone.
"""

from django.core.management.base import BaseCommand

from attendance.services import settle_recent
from tenants.models import Company


class Command(BaseCommand):
    help = "Build today's and yesterday's attendance for every active company, if due."

    def handle(self, *args, **options):
        for company in Company.objects.filter(
                status__in=(Company.Status.ACTIVE, Company.Status.TRIAL)).order_by("pk"):
            try:
                result = settle_recent(company.pk)
            except Exception as exc:  # noqa: BLE001 - one company must not stop the rest
                self.stderr.write(f"{company.name}: {exc}")
                continue
            done = "up to date" if result.get("unchanged") else f"{result.get('days', 0)} day(s) built"
            self.stdout.write(f"{company.name}: {done}.")
