"""The managers' daily or weekly report (timers: daily 6 pm, weekly Saturday 10 am).

    python manage.py ai_reports daily|weekly [--company ID] [--date YYYY-MM-DD] [--dry-run]

For every company with the AI assistant on and recipients who get that
report. The figures come from the ERP; the AI writes the opening paragraph
(left out if the AI is off or fails). Exits with status 1 if a report could
not be sent, so a timer's failure shows in the journal.
"""

from datetime import date

from django.core.management.base import BaseCommand, CommandError

from ai_assistant import summaries
from ai_assistant.models import AiReportRun, AiSettings
from inventory.access import inventory_on
from inventory.doc_common import local_today


class Command(BaseCommand):
    help = "Email the AI assistant's daily or weekly sales and stock report to the managers."

    def add_arguments(self, parser):
        parser.add_argument("kind", choices=AiReportRun.Kind.values)
        parser.add_argument("--company", type=int)
        parser.add_argument("--date", help="The report's last day (YYYY-MM-DD); default today.")
        parser.add_argument("--dry-run", action="store_true", help="Build it and say who would get it; send nothing.")

    def handle(self, *args, **options):
        rows = AiSettings.all_objects.filter(enabled=True, consent_received=True).select_related("company")
        if options.get("company"):
            rows = rows.filter(company_id=options["company"])
        failed = 0
        for own in rows.order_by("company_id"):
            company = own.company
            if not inventory_on(company.pk):
                continue
            today = date.fromisoformat(options["date"]) if options.get("date") else local_today(company)
            run = summaries.send_report(company, options["kind"], today, dry_run=options["dry_run"])
            if run is None:
                self.stdout.write(f"{company.code}: nobody gets the {options['kind']} report.")
            elif run.status == "failed":
                failed += 1
                self.stderr.write(f"{company.code}: not sent - {run.error}")
            else:
                self.stdout.write(f"{company.code}: {run.status} to {run.sent_to.count('@')} address(es).")
        if failed:
            raise CommandError(f"{failed} report(s) could not be sent.")
