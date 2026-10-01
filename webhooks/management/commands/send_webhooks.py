"""Send what the ERP webhook has waiting, for every company that has one on
(2026-10-01). Finished days are worked out first, so their check-outs go too.
Devices checking in already do this once a minute; this is for a scheduler,
e.g. every 5 minutes. Repeatable.

    python manage.py send_webhooks
"""

import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from attendance.services import refresh
from tenants.models import Company
from webhooks import services
from webhooks.models import WebhookSettings


class Command(BaseCommand):
    help = "Send waiting ERP webhook events for every company that has one switched on."

    def handle(self, *args, **options):
        for row in WebhookSettings.all_objects.filter(is_active=True).order_by("company_id"):
            company = Company.objects.get(pk=row.company_id)
            today = timezone.now().astimezone(services.company_zone(company)).date()
            refresh(company.pk, start=today - datetime.timedelta(days=1), end=today)
            received = services.deliver_due(company.pk)
            self.stdout.write(f"{company.name}: {received} event(s) received.")
