"""The morning stock alert (Phase 7): reorder and expiry, by email.

    python manage.py stock_alerts [--company ID] [--date YYYY-MM-DD] [--dry-run]

For every company with Store & Inventory and alert addresses (Inventory
settings): what is at or below its reorder point and what is expired or
expiring, in one email. Nothing to say, no email. Exits with status 1 when
an email could not be sent, so a systemd timer's failure shows in the journal.
"""

from datetime import date

from django.core.management.base import BaseCommand, CommandError

from inventory import alerts
from inventory.access import inventory_on
from inventory.doc_common import local_today
from inventory.models import InventorySettings
from tenants.models import Company


class Command(BaseCommand):
    help = "Email each company's reorder and expiry alert to the addresses in Inventory settings."

    def add_arguments(self, parser):
        parser.add_argument("--company", type=int, help="Only this company id.")
        parser.add_argument("--date", help="As if today were this day (YYYY-MM-DD).")
        parser.add_argument("--dry-run", action="store_true", help="Say what would be sent; send nothing.")

    def handle(self, *args, **options):
        wanted = InventorySettings.all_objects.exclude(alert_emails="").values_list("company_id", flat=True)
        companies = Company.objects.filter(pk__in=wanted).order_by("pk")
        if options.get("company"):
            companies = companies.filter(pk=options["company"])
        failed = 0
        for company in companies:
            if not inventory_on(company.pk):
                continue
            today = date.fromisoformat(options["date"]) if options.get("date") else local_today(company)
            try:
                to, _found, subject = alerts.send_alert(company, today, dry_run=options["dry_run"])
            except Exception as error:  # one company's mail trouble must not stop the others
                failed += 1
                self.stderr.write(f"{company.code}: not sent - {error}")
                continue
            if to:
                verb = "would send" if options["dry_run"] else "sent"
                self.stdout.write(f"{company.code}: {verb} \"{subject}\" to {len(to)} address(es).")
            else:
                self.stdout.write(f"{company.code}: nothing to report.")
        if failed:
            raise CommandError(f"{failed} company alert(s) could not be sent.")
