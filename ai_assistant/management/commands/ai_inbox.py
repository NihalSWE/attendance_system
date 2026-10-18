"""Read the order inboxes and turn new orders into drafts (a timer, every 2 minutes).

    python manage.py ai_inbox [--company ID]

For every company with the AI assistant on: unread emails of its order inbox
are saved, then every message still waiting is read by the AI and drafted
(at most ``PER_RUN`` a company, oldest first). A message that waits because
the day's spending limit was reached is tried again on the next run.
"""

from django.core.management.base import BaseCommand

from ai_assistant import inbox, intake
from ai_assistant.models import AiSettings, InboundMessage
from inventory.access import inventory_on

PER_RUN = 20


class Command(BaseCommand):
    help = "Read the AI order inboxes and draft sales orders from new messages."

    def add_arguments(self, parser):
        parser.add_argument("--company", type=int, help="Only this company id.")

    def handle(self, *args, **options):
        rows = AiSettings.all_objects.filter(enabled=True, consent_received=True).select_related("company")
        if options.get("company"):
            rows = rows.filter(company_id=options["company"])
        for own in rows.order_by("company_id"):
            if not (own.ready and inventory_on(own.company_id)):
                continue
            new = inbox.fetch(own)
            if own.last_inbox_error:
                self.stderr.write(f"{own.company.code}: {own.last_inbox_error}")
            waiting = InboundMessage.all_objects.filter(
                company_id=own.company_id,
                status__in=[InboundMessage.Status.NEW, InboundMessage.Status.WAITING]).order_by("received_at", "pk")
            done = [intake.process(message).status for message in waiting[:PER_RUN]]
            self.stdout.write(f"{own.company.code}: {len(new)} new email(s), {len(done)} read, "
                              f"{done.count(InboundMessage.Status.NEEDS_REVIEW)} to review.")
